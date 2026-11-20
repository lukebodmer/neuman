"""Change the scene in the user's Blender: run Python in it, or load, save and export.

The mutating half of the Blender tools. `exec` runs model-written Python inside
Blender as the user, which is `python`'s blast radius plus the open scene, so
the tool is mutating and every call asks under `safe`. Looking is
`blender_scene` and `blender_view`, which do not (D59, D100).
"""

from __future__ import annotations

import sys

from blender_link import arguments, run

ACTIONS = ("exec", "new", "open", "save", "export", "render")


def main(argv: list[str]) -> int:
    args = arguments("blender", argv)
    if args is None:
        return 2
    action = args.get("action")
    if action not in ACTIONS:
        print(
            f"blender: unknown action {action!r}; expected one of {', '.join(ACTIONS)}",
            file=sys.stderr,
        )
        return 2
    request = {"op": action}
    for key in ("code", "path", "selected_only"):
        if key in args:
            request[key] = args[key]
    return run("blender", request)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
