"""`neuman-runtime`: the one unit with the GPU (D78).

It replaces a unit, a socket, a proxy and an idle timer per model with one
process that reads what to run from the server's document (`plan.py`) and runs
it: `llama-server` in router mode on loopback, one INI section per model. The
router loads a model on the first request naming it (the request waits; it does
not fail), keeps `--models-max` resident, and sleeps a child after its idle
period — measured on the workstation before this was written. It is started with an empty
cache and home directory, because with the default environment it also offers
every model in `~/.cache/huggingface` with a download argument, which is not a
set anyone chose.

The control surface is HTTP on a Unix socket only the `neuman` group reaches:
`GET /status`, `POST /load` and `/unload`. The server is the only client.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import signal
import socketserver
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .plan import Plan, PlanError, load_local_presets, parse_plan

__all__ = ["Host", "HostSettings", "main"]

# The most the control socket will read in one request. Every request is a model
# name in a small JSON object; anything near this is not the server talking.
_MAX_BODY = 64 * 1024


def _log(message: str, **fields: Any) -> None:
    # One JSON line to stderr, which the journal keeps: the server's log record
    # (D10) is not available here without its dependencies, and this is the
    # shape it would have.
    print(json.dumps({"message": message, **fields}), file=sys.stderr, flush=True)


@dataclass(frozen=True, slots=True)
class HostSettings:
    llama_server: str
    models_dir: Path
    config_path: Path
    run_dir: Path
    chat_port: int = 8081
    max_loaded: int = 1
    poll_s: float = 2.0
    # The sampler the timer runs, run here as well at the moments GPU memory
    # changes. Empty on a machine without one.
    sample_command: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str]) -> HostSettings:
        return cls(
            llama_server=env["NEUMAN_LLAMA_SERVER"],
            models_dir=Path(env.get("NEUMAN_MODELS_DIR", "/var/lib/neuman/models")),
            config_path=Path(
                env.get("NEUMAN_RUNTIME_CONFIG", "/var/lib/neuman/state/runtime.json")
            ),
            run_dir=Path(env.get("NEUMAN_RUNTIME_DIR", "/run/neuman/runtime")),
            chat_port=int(env.get("NEUMAN_RUNTIME_CHAT_PORT", "8081")),
            max_loaded=int(env.get("NEUMAN_RUNTIME_MAX_LOADED", "1")),
            sample_command=tuple(shlex.split(env.get("NEUMAN_GPU_SAMPLE_COMMAND", ""))),
        )

    @property
    def socket_path(self) -> Path:
        return self.run_dir / "control.sock"


class Host:
    """Runs what the plan says and answers the control socket."""

    def __init__(
        self, settings: HostSettings, presets: dict[str, dict[str, Any]] | None = None
    ):
        self.settings = settings
        self.presets = presets if presets is not None else load_local_presets()
        self.plan = Plan()
        self.problem: str | None = None
        self._config_stamp: tuple[int, int] | None = None
        self._lock = threading.RLock()
        self._router: subprocess.Popen[bytes] | None = None
        # Why a chat model last failed to load, read from the router's output:
        # the router itself answers only "failed to load".
        self._chat_errors: dict[str, str] = {}
        # Set when GPU memory has just changed; one thread runs the sampler, so a
        # burst of changes is one sample rather than several.
        self._sample_wanted = threading.Event()
        self._stopping = threading.Event()
        self._empty = settings.run_dir / "empty"

    # -- the plan ---------------------------------------------------------------

    def reload(self) -> None:
        """Apply the server's document if it changed since the last look."""

        try:
            stat = self.settings.config_path.stat()
        except FileNotFoundError:
            stamp = None
        else:
            stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._config_stamp:
            return
        self._config_stamp = stamp

        if stamp is None:
            # No document yet means nothing chosen, which is what a fresh
            # appliance is: run nothing.
            self.apply(Plan())
            return
        try:
            document = json.loads(self.settings.config_path.read_text(encoding="utf-8"))
            plan = parse_plan(document, self.presets, self.settings.models_dir)
        except (OSError, json.JSONDecodeError, PlanError) as exc:
            # Refused whole; what was running keeps running. The server reads
            # this back on /status, so the panel can say why.
            self.problem = str(exc)
            _log("runtime document refused", error=str(exc))
            return
        self.problem = None
        self.apply(plan)

    def apply(self, plan: Plan) -> None:
        with self._lock:
            previous, self.plan = self.plan, plan
            if plan.chat != previous.chat:
                self._stop(self._router)
                self._router = None
                if plan.chat:
                    self._start_router()
        _log("runtime plan applied", chat=[model.name for model in plan.chat])

    # -- chat: the router -------------------------------------------------------

    def _start_router(self) -> None:
        ini = self.settings.run_dir / "router.ini"
        ini.write_text(self.plan.router_ini(), encoding="utf-8")
        self._empty.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            # Only the INI's models. With a real home or cache the router also
            # offers whatever it finds cached there (measured, D78).
            "HOME": str(self._empty),
            "LLAMA_CACHE": str(self._empty),
            "HF_HOME": str(self._empty),
            "XDG_CACHE_HOME": str(self._empty),
        }
        self._router = subprocess.Popen(
            [
                self.settings.llama_server,
                "--models-preset",
                str(ini),
                "--models-max",
                str(self.settings.max_loaded),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.settings.chat_port),
            ],
            env=env,
            cwd=self._empty,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        threading.Thread(target=self._watch_router, args=(self._router,), daemon=True).start()
        if not self._wait_http(self.settings.chat_port, "/health", 30.0, self._router):
            _log("the chat router did not come up")
            return
        for model in self.plan.chat:
            if model.residency == "always":
                self._router_post("/models/load", {"model": model.name})

    # The router names each model's child by its port: `spawning server
    # instance with name=X on port P`, then every line the child prints is
    # prefixed `[P]`.
    _SPAWN = re.compile(r"spawning server instance with name=(\S+) on port (\d+)")
    _CHILD = re.compile(r"^\[(\d+)\]")
    # The router's lines for the moments a model's memory changes: a child
    # starting, finishing its load, sleeping, waking, stopping or exiting.
    _MEMORY = re.compile(
        r"spawning server instance|model loaded|sleeping state|stopping model instance"
        r"|exited with status"
    )

    def _sample_soon(self) -> None:
        """Ask for a GPU sample now, so the panel's bar follows a load or unload.

        The timer still samples, slowly, for changes outside Neuman; Neuman's
        own changes are sampled when they happen rather than by sampling often.
        """

        if self.settings.sample_command:
            self._sample_wanted.set()

    def _sampler(self) -> None:
        while not self._stopping.is_set():
            if not self._sample_wanted.wait(timeout=1.0):
                continue
            self._sample_wanted.clear()
            try:
                subprocess.run(list(self.settings.sample_command), timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                _log("the GPU sample did not run", error=str(exc))

    def _watch_router(self, process: subprocess.Popen[bytes]) -> None:
        """Pass the router's output to the journal, noting a load that ran out of memory."""

        ports: dict[str, str] = {}
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            print(line, file=sys.stderr, flush=True)
            if self._MEMORY.search(line):
                self._sample_soon()
            spawned = self._SPAWN.search(line)
            if spawned:
                ports[spawned.group(2)] = spawned.group(1)
                self._chat_errors.pop(spawned.group(1), None)
                continue
            child = self._CHILD.match(line)
            if child and "out of memory" in line.lower():
                name = ports.get(child.group(1))
                if name:
                    self._chat_errors[name] = _OUT_OF_MEMORY

    def _router_models(self) -> dict[str, str]:
        """Each chat model's router status (`loaded`, `unloaded`, `loading`, ...)."""

        try:
            status, body = _request("127.0.0.1", self.settings.chat_port, "GET", "/models")
        except OSError:
            return {}
        if status != 200:
            return {}
        out: dict[str, str] = {}
        for item in json.loads(body).get("data") or []:
            value = (item.get("status") or {}).get("value")
            out[str(item.get("id"))] = str(value) if value else "unknown"
        return out

    def _router_post(self, path: str, body: dict[str, Any]) -> tuple[int, bytes]:
        try:
            return _request(
                "127.0.0.1", self.settings.chat_port, "POST", path, json.dumps(body).encode()
            )
        except OSError as exc:
            return 502, json.dumps(
                {"error": f"the chat router is not answering: {exc}"}
            ).encode()

    # -- load, unload, status ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        routed = self._router_models() if self._router is not None else {}
        return {
            "problem": self.problem,
            "chat": [
                {
                    "name": model.name,
                    "preset": model.preset,
                    "residency": model.residency,
                    "state": routed.get(model.name, "stopped"),
                    "loaded": routed.get(model.name) == "loaded",
                    "error": self._chat_errors.get(model.name),
                }
                for model in self.plan.chat
            ],
        }

    def load(self, name: str) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if any(model.name == name for model in self.plan.chat):
                status, body = self._router_post("/models/load", {"model": name})
                return (200, {"state": "loading"}) if status == 200 else (502, _json(body))
        return 404, {"error": "not_found"}

    def unload(self, name: str) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if any(model.name == name for model in self.plan.chat):
                status, body = self._router_post("/models/unload", {"model": name})
                # The router refuses to unload what is not loaded; for the
                # panel that is already the state it asked for.
                return (
                    (200, {"state": "unloaded"}) if status in (200, 400) else (502, _json(body))
                )
        return 404, {"error": "not_found"}

    # -- the loop ---------------------------------------------------------------

    def tick(self) -> None:
        """One pass of the housekeeping loop: config, then a crashed router."""

        self.reload()
        with self._lock:
            if self.plan.chat and self._router is not None and self._router.poll() is not None:
                _log("the chat router exited; restarting", code=self._router.returncode)
                self._start_router()

    def serve(self) -> None:
        self.settings.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.socket_path
        path.unlink(missing_ok=True)
        server = _ControlServer(str(path), _handler_for(self))
        # 0660: the server reaches it through the group, nothing else does. Set
        # after bind, which honours the umask.
        os.chmod(path, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=self._sampler, daemon=True).start()
        _log("neuman-runtime listening", socket=str(path))
        try:
            self.tick()
            while not self._stopping.wait(self.settings.poll_s):
                self.tick()
        finally:
            server.shutdown()
            self.close()

    def stop(self) -> None:
        self._stopping.set()

    def close(self) -> None:
        with self._lock:
            self._stop(self._router)
            self._router = None

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _stop(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _wait_http(
        self, port: int, path: str, limit: float, process: subprocess.Popen[bytes] | None = None
    ) -> bool:
        """Whether `path` answers within `limit`; False at once if `process` exits."""

        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                return False
            try:
                if _request("127.0.0.1", port, "GET", path, timeout=2.0)[0] == 200:
                    return True
            except OSError:
                pass
            time.sleep(0.2)
        return False


_OUT_OF_MEMORY = "the GPU ran out of memory; unload another model in the model panel first"


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        sent = {"content-type": "application/json"} if body else {}
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _json(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except ValueError:
        return {"error": body.decode("utf-8", "replace")[:300]}
    return parsed if isinstance(parsed, dict) else {"error": str(parsed)[:300]}


class _ControlServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def _handler_for(host: Host) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def address_string(self) -> str:  # a Unix peer has no address
            return "control"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json_reply(self, status: int, value: Any) -> None:
            self._reply(status, json.dumps(value).encode(), "application/json")

        def _body(self) -> bytes | None:
            length = int(self.headers.get("content-length") or 0)
            if length > _MAX_BODY:
                self._json_reply(413, {"error": "too large"})
                return None
            return self.rfile.read(length)

        def do_GET(self) -> None:
            if self.path == "/status":
                self._json_reply(200, host.status())
            else:
                self._json_reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            body = self._body()
            if body is None:
                return
            try:
                request = json.loads(body or b"{}")
            except ValueError:
                self._json_reply(400, {"error": "the body is not JSON"})
                return
            if not isinstance(request, dict):
                self._json_reply(400, {"error": "the body is not an object"})
                return
            if self.path in ("/load", "/unload"):
                act = host.load if self.path == "/load" else host.unload
                status, value = act(str(request.get("name")))
                # The panel should see the change now rather than at the timer's
                # next tick.
                host._sample_soon()
                self._json_reply(status, value)
            else:
                self._json_reply(404, {"error": "not_found"})

    return Handler


def main() -> int:
    host = Host(HostSettings.from_env(dict(os.environ)))
    signal.signal(signal.SIGTERM, lambda *_: host.stop())
    signal.signal(signal.SIGINT, lambda *_: host.stop())
    host.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
