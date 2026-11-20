"""`neuman-fetch`: puts a preset's pinned weights in `modelsDir` (D79).

The one unit with network egress and write access to the weights, and no GPU.
A request names a preset and nothing else; the repository, the commit, the file
names, sizes and hashes are the shipped preset's. So the most a caller can do is
make this download a file Neuman already ships a hash for.

Each file is downloaded to `<name>.part`, resumed with a range request when a
partial one is there, hashed as it arrives, and renamed into place only when
its SHA-256 is the pinned one. A file already in place is hashed rather than
downloaded again: that is how weights fetched by hand before this existed are
adopted, at the cost of one read. Either way a verified file gets a record in
`modelsDir/.verified/`, which is what the administration page reads to call it
verified without hashing 16 GB on every look (`adapters/weights.py`).

Control is HTTP on a Unix socket: `POST /fetch`, `POST /cancel` and
`POST /delete` with `{"preset": id}`, and `GET /status`. Deleting removes the
preset's pinned files and nothing else: the names come from the preset, so the
most a caller can delete is weights Neuman would fetch again.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .host import _ControlServer, _log
from .plan import load_local_presets

__all__ = ["Fetcher", "main", "verified_record"]

_CHUNK = 1 << 20
# Room left beside a download, so a full disk is refused up front rather than
# discovered at 97% — and so a download never takes the last of a disk the
# database is on.
_HEADROOM = 1 << 30


def verified_record(models_dir: Path, name: str) -> Path:
    return models_dir / ".verified" / f"{name}.json"


@dataclass
class _Job:
    total: int
    done: int = 0
    state: str = "fetching"
    error: str | None = None
    file: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "done_bytes": self.done,
            "total_bytes": self.total,
            "file": self.file,
            "error": self.error,
        }


class Fetcher:
    def __init__(
        self,
        models_dir: Path,
        run_dir: Path,
        base_url: str = "https://huggingface.co",
        presets: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.models_dir = models_dir
        self.run_dir = run_dir
        self.base_url = base_url.rstrip("/")
        self.presets = presets if presets is not None else load_local_presets()
        self.jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def start(self, preset_id: str) -> tuple[int, dict[str, Any]]:
        preset = self.presets.get(preset_id)
        if preset is None:
            return 404, {"error": f"no shipped preset `{preset_id}` runs on this machine"}
        weights = preset["local"]["facts"]["weights"]
        with self._lock:
            running = self.jobs.get(preset_id)
            if running is not None and running.state in ("fetching", "verifying"):
                return 200, running.view()
            job = _Job(total=sum(file["size"] for file in weights["files"]))
            self.jobs[preset_id] = job
        threading.Thread(target=self._run, args=(job, weights), daemon=True).start()
        return 202, job.view()

    def cancel(self, preset_id: str) -> tuple[int, dict[str, Any]]:
        job = self.jobs.get(preset_id)
        if job is None:
            return 404, {"error": "not_found"}
        job.cancel.set()
        return 200, job.view()

    def status(self) -> dict[str, Any]:
        return {preset_id: job.view() for preset_id, job in self.jobs.items()}

    def delete(self, preset_id: str) -> tuple[int, dict[str, Any]]:
        preset = self.presets.get(preset_id)
        if preset is None:
            return 404, {"error": f"no shipped preset `{preset_id}` runs on this machine"}
        running = self.jobs.get(preset_id)
        if running is not None and running.state in ("fetching", "verifying"):
            return 409, {"error": "a fetch of these weights is running; cancel it first"}
        freed = 0
        for file in preset["local"]["facts"]["weights"]["files"]:
            final = self.models_dir / file["name"]
            for path in (
                final,
                final.with_name(file["name"] + ".part"),
                verified_record(self.models_dir, file["name"]),
            ):
                freed += _size(path)
                path.unlink(missing_ok=True)
        self.jobs.pop(preset_id, None)
        _log("weights deleted", preset=preset_id, freed=freed)
        return 200, {"freed_bytes": freed}

    # -- the work ---------------------------------------------------------------

    def _run(self, job: _Job, weights: dict[str, Any]) -> None:
        try:
            needed = sum(
                file["size"] - _size(self.models_dir / (file["name"] + ".part"))
                for file in weights["files"]
                if not (self.models_dir / file["name"]).exists()
            )
            free = shutil.disk_usage(self.models_dir).free
            if needed + _HEADROOM > free:
                raise _Refused(
                    f"{_size_text(needed)} is needed and {_size_text(free)} is free, and a "
                    f"download leaves {_size_text(_HEADROOM)} for the rest of the appliance"
                )
            for file in weights["files"]:
                job.file = file["name"]
                self._one(job, weights, file)
            job.state = "done"
            job.file = None
        except _Cancelled:
            job.state = "cancelled"
        except (_Refused, OSError, urllib.error.URLError) as exc:
            job.state = "failed"
            job.error = str(exc.reason if isinstance(exc, urllib.error.URLError) else exc)
            _log("fetch failed", file=job.file, error=job.error)

    def _one(self, job: _Job, weights: dict[str, Any], file: dict[str, Any]) -> None:
        name, size, want = file["name"], file["size"], file["sha256"]
        final = self.models_dir / name
        if _record_matches(self.models_dir, final, file):
            job.done += size
            return

        digest = hashlib.sha256()
        if final.exists():
            # Fetched by hand, or by an earlier release: adopt it if it is the
            # pinned file, and refuse rather than overwrite if it is not — it is
            # somebody's download, and deleting it is theirs to decide.
            job.state = "verifying"
            if final.stat().st_size != size:
                raise _Refused(
                    f"{name} is in modelsDir with another size; move it aside to fetch"
                )
            self._hash_into(digest, final, job)
            if digest.hexdigest() != want:
                raise _Refused(
                    f"{name} is in modelsDir with another hash; move it aside to fetch"
                )
            self._record(final, file)
            job.state = "fetching"
            return

        part = final.with_name(name + ".part")
        have = _size(part)
        if have > size:
            part.unlink()
            have = 0
        if have:
            self._hash_into(digest, part, job)
        where = file.get("path", name)
        url = f"{self.base_url}/{weights['repo']}/resolve/{weights['revision']}/{where}"
        request = urllib.request.Request(url, headers={"user-agent": "neuman-fetch"})
        if have:
            request.add_header("range", f"bytes={have}-")
        with urllib.request.urlopen(request, timeout=60) as response:
            if have and response.status != 206:
                # The server ignored the range: start again rather than append
                # a second copy of the beginning.
                have = 0
                digest = hashlib.sha256()
                job.done -= _size(part)
            with part.open("ab" if have else "wb") as out:
                while chunk := response.read(_CHUNK):
                    if job.cancel.is_set():
                        raise _Cancelled
                    out.write(chunk)
                    digest.update(chunk)
                    job.done += len(chunk)

        if part.stat().st_size != size or digest.hexdigest() != want:
            part.unlink(missing_ok=True)
            raise _Refused(
                f"{name} did not match its pinned hash; the partial file was removed"
            )
        os.replace(part, final)
        self._record(final, file)

    def _hash_into(self, digest: Any, path: Path, job: _Job) -> None:
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK):
                if job.cancel.is_set():
                    raise _Cancelled
                digest.update(chunk)
                job.done += len(chunk)

    def _record(self, final: Path, file: dict[str, Any]) -> None:
        record = verified_record(self.models_dir, file["name"])
        record.parent.mkdir(exist_ok=True)
        stat = final.stat()
        record.write_text(
            json.dumps(
                {"sha256": file["sha256"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            ),
            encoding="utf-8",
        )

    # -- serving ----------------------------------------------------------------

    def serve(self, stopping: threading.Event) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "control.sock"
        path.unlink(missing_ok=True)
        server = _ControlServer(str(path), _handler_for(self))
        os.chmod(path, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _log("neuman-fetch listening", socket=str(path))
        stopping.wait()
        for job in self.jobs.values():
            job.cancel.set()
        server.shutdown()


class _Refused(Exception):
    pass


class _Cancelled(Exception):
    pass


def _size_text(count: int) -> str:
    return f"{count / 2**30:.1f} GiB" if count >= 2**30 else f"{count / 2**20:.0f} MiB"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _record_matches(models_dir: Path, final: Path, file: dict[str, Any]) -> bool:
    try:
        record = json.loads(
            verified_record(models_dir, file["name"]).read_text(encoding="utf-8")
        )
        stat = final.stat()
    except (OSError, ValueError):
        return False
    return (
        record.get("sha256") == file["sha256"]
        and record.get("size") == stat.st_size == file["size"]
        and record.get("mtime_ns") == stat.st_mtime_ns
    )


def _handler_for(fetcher: Fetcher) -> type:
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def address_string(self) -> str:
            return "control"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, status: int, value: Any) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/status":
                self._reply(200, fetcher.status())
            else:
                self._reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = min(int(self.headers.get("content-length") or 0), 4096)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
                preset = str(request["preset"])
            except (ValueError, KeyError, TypeError):
                self._reply(400, {"error": 'the body must be {"preset": id}'})
                return
            if self.path == "/fetch":
                self._reply(*fetcher.start(preset))
            elif self.path == "/cancel":
                self._reply(*fetcher.cancel(preset))
            elif self.path == "/delete":
                self._reply(*fetcher.delete(preset))
            else:
                self._reply(404, {"error": "not_found"})

    return Handler


def main() -> int:
    env = os.environ
    fetcher = Fetcher(
        models_dir=Path(env.get("NEUMAN_MODELS_DIR", "/var/lib/neuman/models")),
        run_dir=Path(env.get("NEUMAN_FETCH_DIR", "/run/neuman/fetch")),
        base_url=env.get("NEUMAN_FETCH_BASE_URL", "https://huggingface.co"),
    )
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    fetcher.serve(stopping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
