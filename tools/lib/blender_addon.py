"""The half of the Blender package's host that runs inside Blender.

`neuman-blender-host` starts Blender with this file as its `--python` script. It
binds a Unix socket and answers the `blender`, `blender_scene` and `blender_view` tools
over it, one newline-terminated JSON request and one JSON reply per connection.
It imports nothing of Neuman's, because it runs on Blender's own interpreter.

**A socket with a mode, not a port.** Whoever reaches this can run Python as the
user, exactly as CDP lets whoever reaches it drive the browser — so it gets the
browse link's treatment: `0600` in the user's `0700` runtime directory, mode set
by the umask at `bind` so there is no window where it exists wider (N164's
neighbour in `bridge.py`). The published Blender integrations listen on loopback
TCP with no authentication, which is every process on the machine.

**Everything runs on Blender's main thread.** `bpy` is not thread-safe: a call
from the socket thread corrupts state or crashes Blender. The socket thread only
queues a request and waits; a timer drains the queue between redraws, so the
person keeps using the window while the agent edits the same scene and each sees
the other's changes as they land.

**Each change is one undo step.** A request that can change the scene ends with
an undo push named after it, so the person undoes the agent's edit with Ctrl-Z
like any other, rather than having no way back but a saved file.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import math
import os
import queue
import socket
import tempfile
import threading
import traceback

import bmesh
import bpy
import mathutils

# The package host's socket, which the client chose and names to the host and to
# the tools alike (D101).
SOCKET_ENV = "NEUMAN_HOST_SOCKET"

# A request is code or a path; a megabyte is far beyond either.
MAX_REQUEST_BYTES = 1024 * 1024

# What one `exec` may print. The tool's own `max_output_bytes` is the hard
# limit; cutting here, with a note, is what keeps a loop that prints every
# vertex from arriving as a truncated stream the model mistakes for the whole.
MAX_EXEC_OUTPUT = 64 * 1024

# The viewport's virtual sensor width in millimetres, from which Blender derives
# the 3D view's field of view for a given lens. Used to frame a view so the
# scene fills it.
VIEW_SENSOR_MM = 72.0

# Rotations that look down each axis, as Blender's own numpad views do.
AXIS_VIEWS = {
    "front": (90, 0, 0),
    "back": (90, 0, 180),
    "right": (90, 0, 90),
    "left": (90, 0, -90),
    "top": (0, 0, 0),
    "bottom": (180, 0, 0),
    "iso": (60, 0, 45),
}

# Object types with no surface to frame or to count. A light's bound box is a
# point, and including a sun two hundred metres away frames the sun.
UNFRAMED = {"LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE", "EMPTY"}

_requests: queue.Queue = queue.Queue()


# -- the socket ----------------------------------------------------------------


class _Pending:
    def __init__(self, request: dict) -> None:
        self.request = request
        self.reply: dict | None = None
        self.done = threading.Event()


def _read_request(connection: socket.socket) -> dict:
    data = b""
    while b"\n" not in data:
        chunk = connection.recv(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
    request = json.loads(data.split(b"\n", 1)[0] or b"null")
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    return request


def _serve_connection(connection: socket.socket) -> None:
    with connection:
        try:
            pending = _Pending(_read_request(connection))
        except (OSError, ValueError) as error:
            reply = {"ok": False, "error": f"bad request: {error}"}
        else:
            _requests.put(pending)
            # No timeout: the tool's own wait bounds the call, and a request
            # already running on the main thread cannot be abandoned halfway.
            pending.done.wait()
            reply = pending.reply or {"ok": False, "error": "no reply"}
        with contextlib.suppress(OSError):
            connection.sendall(json.dumps(reply).encode() + b"\n")


def _listen(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o177)
    try:
        server.bind(path)
    finally:
        os.umask(previous)
    os.chmod(path, 0o600)
    server.listen(8)
    while True:
        connection, _ = server.accept()
        threading.Thread(target=_serve_connection, args=(connection,), daemon=True).start()


def _drain() -> float:
    """Run every queued request on the main thread; Blender calls this again."""

    while True:
        try:
            pending = _requests.get_nowait()
        except queue.Empty:
            return 0.05
        try:
            pending.reply = _handle(pending.request)
        except Exception as error:  # noqa: BLE001 - the reply is the report; Blender lives on
            pending.reply = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "trace": traceback.format_exc(limit=6),
            }
        pending.done.set()


# -- context ---------------------------------------------------------------------


def _view3d():
    """The largest 3D viewport in any window: (window, area, region, space)."""

    best = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            size = area.width * area.height
            if best is None or size > best[0]:
                best = (size, window, area, region, area.spaces.active)
    if best is None:
        return None
    return best[1:]


def _override():
    """A context where operators that need a 3D view find one."""

    found = _view3d()
    if found is None:
        window = bpy.context.window_manager.windows[0]
        return bpy.context.temp_override(window=window)
    window, area, region, _space = found
    return bpy.context.temp_override(window=window, area=area, region=region)


def _undo_push(message: str) -> None:
    with _override(), contextlib.suppress(RuntimeError):
        bpy.ops.ed.undo_push(message=f"Neuman: {message}")


# -- requests --------------------------------------------------------------------


def _handle(request: dict) -> dict:
    op = request.get("op")
    handler = _HANDLERS.get(op)
    if handler is None:
        return {"ok": False, "error": f"unknown op {op!r}"}
    return handler(request)


def _exec(request: dict) -> dict:
    code = request.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "`code` is required"}
    out = io.StringIO()
    namespace = {"bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "__name__": "__neuman__"}
    failed = False
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), _override():
        try:
            exec(compile(code, "<neuman>", "exec"), namespace)
        except Exception:  # noqa: BLE001 - the model's code, whose error is the answer
            failed = True
            traceback.print_exc()
    # Pushed even after an error: the code may have changed half the scene
    # before raising, and that half must be undoable too.
    _undo_push("exec")
    text = out.getvalue()
    if len(text) > MAX_EXEC_OUTPUT:
        text = text[:MAX_EXEC_OUTPUT] + f"\n[output cut at {MAX_EXEC_OUTPUT} bytes]"
    return {"ok": not failed, "text": text, "error": "the code raised" if failed else None}


def _new(_request: dict) -> dict:
    with _override():
        bpy.ops.wm.read_homefile(use_empty=True)
    return {"ok": True, "text": "started an empty scene"}


def _open(request: dict) -> dict:
    path = _path(request)
    with _override():
        # `use_scripts=False` whatever the preferences say: a .blend can carry a
        # script that runs on load, and a file the model was told to open is
        # exactly the one nobody vetted.
        bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    return {"ok": True, "text": f"opened {path}"}


def _save(request: dict) -> dict:
    path = request.get("path") or bpy.data.filepath
    if not path:
        return {"ok": False, "error": "this scene has never been saved; give a `path`"}
    path = os.path.abspath(os.path.expanduser(path))
    with _override():
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
    return {"ok": True, "text": f"saved {path}"}


def _export(request: dict) -> dict:
    path = _path(request)
    selected = bool(request.get("selected_only"))
    extension = os.path.splitext(path)[1].lower()
    with _override():
        if extension in (".glb", ".gltf"):
            bpy.ops.export_scene.gltf(
                filepath=path,
                export_format="GLB" if extension == ".glb" else "GLTF_SEPARATE",
                use_selection=selected,
            )
        elif extension == ".obj":
            bpy.ops.wm.obj_export(filepath=path, export_selected_objects=selected)
        elif extension == ".stl":
            bpy.ops.wm.stl_export(filepath=path, export_selected_objects=selected)
        elif extension == ".ply":
            bpy.ops.wm.ply_export(filepath=path, export_selected_objects=selected)
        elif extension == ".fbx":
            bpy.ops.export_scene.fbx(filepath=path, use_selection=selected)
        elif extension in (".usd", ".usda", ".usdc", ".usdz"):
            bpy.ops.wm.usd_export(filepath=path, selected_objects_only=selected)
        else:
            return {
                "ok": False,
                "error": f"no exporter for {extension or 'a file with no extension'}; "
                "use .glb, .gltf, .obj, .stl, .ply, .fbx or .usd",
            }
    return {"ok": True, "text": f"exported {path}"}


def _render(request: dict) -> dict:
    path = _path(request)
    scene = bpy.context.scene
    if scene.camera is None:
        return {"ok": False, "error": "the scene has no camera to render from"}
    previous = scene.render.filepath
    scene.render.filepath = path
    try:
        with _override():
            bpy.ops.render.render(write_still=True)
    finally:
        scene.render.filepath = previous
    return {"ok": True, "text": f"rendered {path} with {scene.render.engine}"}


def _path(request: dict) -> str:
    path = request.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("`path` is required")
    return os.path.abspath(os.path.expanduser(path))


# -- describing the scene ------------------------------------------------------


def _vec(values, digits: int = 3) -> str:
    return "(" + ", ".join(f"{round(v, digits):g}" for v in values) + ")"


def _deg(euler) -> str:
    return "(" + ", ".join(f"{round(math.degrees(v), 1):g}" for v in euler) + ")"


def _object_line(obj) -> str:
    parts = [
        f"{obj.name}  {obj.type}",
        f"loc {_vec(obj.location)}",
        f"rot {_deg(obj.rotation_euler)}°",
        f"scale {_vec(obj.scale)}",
    ]
    if obj.type not in UNFRAMED:
        parts.append(f"dims {_vec(obj.dimensions)}")
    if obj.type == "MESH" and obj.data:
        parts.append(f"verts {len(obj.data.vertices)} faces {len(obj.data.polygons)}")
    materials = [slot.material.name for slot in obj.material_slots if slot.material]
    if materials:
        parts.append("mats [" + ", ".join(materials) + "]")
    if obj.modifiers:
        parts.append("mods [" + ", ".join(m.name for m in obj.modifiers) + "]")
    if obj.parent:
        parts.append(f"parent {obj.parent.name}")
    if obj.hide_get() or obj.hide_viewport:
        parts.append("hidden")
    return "  ".join(parts)


def _scene(request: dict) -> dict:
    name = request.get("object")
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            return {"ok": False, "error": f"no object named {name!r}"}
        return {"ok": True, "text": _object_detail(obj)}

    limit = int(request.get("limit") or 200)
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    lines = [
        f'scene "{scene.name}"  file {bpy.data.filepath or "(unsaved)"}'
        f"{'  (modified)' if bpy.data.is_dirty else ''}",
        f"units {scene.unit_settings.system} scale {scene.unit_settings.scale_length:g}"
        f"  frame {scene.frame_current}  engine {scene.render.engine}"
        f"  camera {scene.camera.name if scene.camera else '-'}",
    ]
    active = view_layer.objects.active
    selected = [obj.name for obj in view_layer.objects if obj.select_get()]
    lines.append(
        f"active {active.name if active else '-'}  selected [{', '.join(selected[:20])}"
        f"{', …' if len(selected) > 20 else ''}]"
    )
    objects = list(scene.objects)
    lines.append(f"objects ({len(objects)}):")
    lines.extend(_object_line(obj) for obj in objects[:limit])
    if len(objects) > limit:
        lines.append(
            f"[{len(objects) - limit} more; pass a larger `limit` or name an `object`]"
        )
    materials = [m.name for m in bpy.data.materials if m.users]
    if materials:
        lines.append("materials [" + ", ".join(materials) + "]")
    return {"ok": True, "text": "\n".join(lines)}


def _simple_properties(struct) -> list[str]:
    """`name = value` for a struct's plain settings, for modifiers and lights.

    Generic rather than a table per modifier type, because Blender has dozens
    and every one is described by its own RNA.
    """

    out = []
    for prop in struct.bl_rna.properties:
        key = prop.identifier
        if key in ("rna_type", "name", "type") or key.startswith("show_"):
            continue
        if prop.type in ("POINTER", "COLLECTION"):
            if prop.type == "POINTER":
                value = getattr(struct, key, None)
                if value is not None and hasattr(value, "name"):
                    out.append(f"{key} = {value.name}")
            continue
        try:
            value = getattr(struct, key)
        except AttributeError:
            continue
        if hasattr(value, "__len__") and not isinstance(value, str):
            value = _vec(value)
        out.append(f"{key} = {value}")
    return out


def _material_line(material) -> str:
    if material.use_nodes and material.node_tree:
        bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            base = bsdf.inputs["Base Color"].default_value
            return (
                f"{material.name}: principled base {_vec(base)} metallic "
                f"{bsdf.inputs['Metallic'].default_value:g} roughness "
                f"{bsdf.inputs['Roughness'].default_value:g}"
            )
        kinds = sorted({n.type for n in material.node_tree.nodes})
        return f"{material.name}: nodes [{', '.join(kinds)}]"
    return f"{material.name}: diffuse {_vec(material.diffuse_color)}"


def _object_detail(obj) -> str:
    lines = [_object_line(obj)]
    world = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
    if obj.type not in UNFRAMED:
        low = [min(v[i] for v in world) for i in range(3)]
        high = [max(v[i] for v in world) for i in range(3)]
        lines.append(f"world bounds {_vec(low)} to {_vec(high)}")
    lines.append("collections [" + ", ".join(c.name for c in obj.users_collection) + "]")
    if obj.children:
        lines.append("children [" + ", ".join(c.name for c in obj.children) + "]")
    if obj.type == "MESH" and obj.data:
        mesh = obj.data
        smooth = sum(1 for p in mesh.polygons if p.use_smooth)
        lines.append(
            f"mesh {mesh.name}: verts {len(mesh.vertices)} edges {len(mesh.edges)} "
            f"faces {len(mesh.polygons)} smooth faces {smooth} "
            f"uv maps [{', '.join(uv.name for uv in mesh.uv_layers)}]"
        )
    elif obj.type in ("LIGHT", "CAMERA") and obj.data:
        lines.append(f"{obj.type.lower()} {obj.data.name}:")
        lines.extend(f"  {line}" for line in _simple_properties(obj.data)[:40])
    for modifier in obj.modifiers:
        lines.append(f"modifier {modifier.name} ({modifier.type}):")
        lines.extend(f"  {line}" for line in _simple_properties(modifier))
    for slot in obj.material_slots:
        if slot.material:
            lines.append("material " + _material_line(slot.material))
    custom = [key for key in obj.keys() if not key.startswith("_")]
    if custom:
        lines.append("custom " + ", ".join(f"{key} = {obj[key]!r}" for key in custom))
    return "\n".join(lines)


# -- looking at it -------------------------------------------------------------


def _framed(name: str | None):
    """The objects a view should fit: one by name, or everything visible."""

    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError(f"no object named {name!r}")
        return [obj]
    return [
        obj
        for obj in bpy.context.view_layer.objects
        if obj.type not in UNFRAMED and obj.visible_get()
    ]


def _bounds(objects):
    points = [
        obj.matrix_world @ mathutils.Vector(corner)
        for obj in objects
        for corner in obj.bound_box
    ]
    if not points:
        return mathutils.Vector((0, 0, 0)), 1.0
    low = mathutils.Vector([min(p[i] for p in points) for i in range(3)])
    high = mathutils.Vector([max(p[i] for p in points) for i in range(3)])
    centre = (low + high) / 2
    return centre, max((high - low).length / 2, 0.01)


def _view(request: dict) -> dict:
    """A picture of the scene, from the person's view or a named one.

    Drawn by the viewport renderer (`render.opengl`) rather than captured from
    the screen: it draws offscreen, so a named view is set, drawn and put back
    inside one main-thread call, and the person's viewport never visibly moves.
    A screen capture would need a redraw between the move and the capture,
    which is exactly the flicker this avoids. The person's overlays come with
    it — the grid, the selection outline — which is what lets the model read
    scale and see what is selected.
    """

    found = _view3d()
    if found is None:
        return {"ok": False, "error": "Blender has no 3D viewport open to look through"}
    window, area, region, space = found
    r3d = space.region_3d
    scene = bpy.context.scene
    render = scene.render

    view = request.get("view") or "iso"
    shading = request.get("shading") or "solid"
    size = int(request.get("size") or 1024)
    # Capped so a PNG of it stays well inside the tool's `max_output_bytes` once
    # base64 has inflated it by a third.
    size = max(256, min(size, 1536))

    saved_view = (
        r3d.view_location.copy(),
        r3d.view_rotation.copy(),
        r3d.view_distance,
        r3d.view_perspective,
    )
    saved_shading = space.shading.type
    saved_render = (render.resolution_x, render.resolution_y, render.resolution_percentage)
    path = tempfile.mkstemp(prefix="neuman-view-", suffix=".png")[1]
    try:
        if view in AXIS_VIEWS:
            centre, radius = _bounds(_framed(request.get("object")))
            rotation = mathutils.Euler([math.radians(a) for a in AXIS_VIEWS[view]], "XYZ")
            r3d.view_rotation = rotation.to_quaternion()
            r3d.view_location = centre
            half_fov = math.atan((VIEW_SENSOR_MM / 2) / space.lens)
            r3d.view_distance = radius / math.sin(half_fov) * 1.05
            r3d.view_perspective = "PERSP" if view == "iso" else "ORTHO"
        elif view == "camera":
            if scene.camera is None:
                return {"ok": False, "error": "the scene has no camera; use another `view`"}
            r3d.view_perspective = "CAMERA"
        elif view != "current":
            return {"ok": False, "error": f"unknown view {view!r}"}

        if shading in ("solid", "material", "rendered"):
            space.shading.type = shading.upper()
        else:
            return {"ok": False, "error": f"unknown shading {shading!r}"}

        aspect = region.width / max(region.height, 1) if view == "current" else 4 / 3
        if view == "camera":
            aspect = saved_render[0] / max(saved_render[1], 1)
        width, height = (
            (size, round(size / aspect)) if aspect >= 1 else (round(size * aspect), size)
        )
        render.resolution_x, render.resolution_y, render.resolution_percentage = (
            width,
            height,
            100,
        )

        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.render.opengl(view_context=True)
        bpy.data.images["Render Result"].save_render(path)
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        (r3d.view_location, r3d.view_rotation, r3d.view_distance, r3d.view_perspective) = (
            saved_view
        )
        space.shading.type = saved_shading
        render.resolution_x, render.resolution_y, render.resolution_percentage = saved_render
        with contextlib.suppress(OSError):
            os.unlink(path)
    return {
        "ok": True,
        "image": {"media_type": "image/png", "data": base64.b64encode(data).decode()},
    }


_HANDLERS = {
    "exec": _exec,
    "new": _new,
    "open": _open,
    "save": _save,
    "export": _export,
    "render": _render,
    "scene": _scene,
    "view": _view,
}


def main() -> None:
    path = os.environ.get(SOCKET_ENV)
    if not path:
        raise SystemExit(f"neuman-blender-host: {SOCKET_ENV} is not set")
    threading.Thread(target=_listen, args=(path,), daemon=True).start()
    # Persistent, so opening another file does not silently unregister the one
    # thing that answers the tools.
    bpy.app.timers.register(_drain, first_interval=0.1, persistent=True)
    print(f"neuman-blender-host: answering on {path}")


main()
