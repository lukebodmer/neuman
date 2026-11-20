"""The runtime host and the fetcher (D78, D79).

The plan tests read the real presets: what the host refuses to start is the
point, and a fixture preset would prove only the fixture. The host and the
fetcher run for real against stand-ins — `llama-server` as a small Python
program that answers the endpoints the host uses, and a local HTTP server in place of the Hub — so what is tested is the process
management, the control socket and the bytes on disk, without a GPU.
"""

from __future__ import annotations

import ast
import hashlib
import http.client
import http.server
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from neuman_server.runtime.fetch import Fetcher
from neuman_server.runtime.host import Host, HostSettings
from neuman_server.runtime.plan import PlanError, load_local_presets, parse_plan

PRESETS = load_local_presets()
MODELS = Path("/models")

QWEN = {
    "name": "qwen-here",
    "preset": "qwen3_8-27b",
    "context_size": 65536,
    "gpu_layers": 99,
    "speculation": "draft-mtp",
    "residency": "on-demand",
    "idle_minutes": 15,
}


# -- the plan -------------------------------------------------------------------


def test_the_router_file_is_built_from_the_preset_and_typed_values():
    plan = parse_plan({"chat": [QWEN]}, PRESETS, MODELS)
    ini = plan.router_ini()

    assert "[qwen-here]" in ini
    assert "model = /models/Qwen3.8-27B-UD-Q4_K_XL.gguf" in ini
    assert "mmproj = /models/mmproj-F16.gguf" in ini
    assert "ctx-size = 65536" in ini
    assert "spec-type = draft-mtp" in ini
    assert "sleep-idle-seconds = 900" in ini
    # The flag the model needs on any hardware, from the preset.
    assert "jinja = true" in ini
    # One slot, so the window is the window.
    assert "parallel = 1" in ini
    # llama.cpp's own cache type is not restated.
    assert "cache-type" not in ini


def test_a_split_model_is_started_from_its_first_shard():
    """llama.cpp opens the other shards beside it by their numbered names."""

    item = {**QWEN, "name": "glm-here", "preset": "glm-5_3-flash"}
    item.pop("speculation")
    ini = parse_plan({"chat": [item]}, PRESETS, MODELS).router_ini()

    assert "model = /models/GLM-5.3-Flash-UD-Q4_K_XL-00001-of-00006.gguf" in ini
    assert "mmproj = /models/GLM-5.3-Flash-mmproj-F16.gguf" in ini


def test_a_compact_cache_is_one_type_for_both_k_and_v():
    """Differing types have no flash-attention kernel and run on the CPU (N100)."""

    ini = parse_plan({"chat": [{**QWEN, "kv_cache": "q8_0"}]}, PRESETS, MODELS).router_ini()

    assert "cache-type-k = q8_0" in ini
    assert "cache-type-v = q8_0" in ini


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # Not a shipped model.
        ({"chat": [{**QWEN, "preset": "someone-evil"}]}, "no shipped preset"),
        # A hosted-only model has nothing to run here.
        ({"chat": [{**QWEN, "preset": "kimi-k3_1-pro"}]}, "no shipped preset"),
        # A field the host does not take is refused, not ignored: it is a flag
        # somebody hoped would reach the command line.
        ({"chat": [{**QWEN, "extra_flags": ["--model", "/etc/shadow"]}]}, "does not take"),
        ({"chat": [{**QWEN, "model": "/etc/shadow"}]}, "does not take"),
        # A name that would break out of its INI section.
        ({"chat": [{**QWEN, "name": "x]\nmodel = /etc/shadow\n[y"}]}, "not a catalog name"),
        ({"chat": [{**QWEN, "context_size": "65536; rm -rf /"}]}, "context_size"),
        ({"chat": [{**QWEN, "gpu_layers": -1}]}, "gpu_layers"),
        ({"chat": [{**QWEN, "speculation": "draft-anything"}]}, "speculation"),
        # A type this build has no GPU kernel for.
        ({"chat": [{**QWEN, "kv_cache": "q5_1"}]}, "kv_cache"),
        ({"chat": [QWEN, QWEN]}, "named twice"),
        ({"chat": [], "elsewhere": 1}, "unknown fields"),
    ],
)
def test_a_document_asking_for_anything_else_is_refused(document, named):
    with pytest.raises(PlanError, match=named):
        parse_plan(document, PRESETS, MODELS)


def test_the_runtime_imports_only_the_standard_library():
    """These run in units of their own; a dependency the server has and they lack
    is a failure only the appliance would find (N47)."""

    source = Path(__file__).resolve().parents[1] / "src" / "neuman_server" / "runtime"
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for path in source.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in allowed, f"{path.name} imports {name}"


# -- the host, against stand-in programs ---------------------------------------

_ROUTER = r"""
import json, os, sys, http.server, socketserver
args = sys.argv[1:]
# Where the router would search for cached models, for the test to read.
json.dump({k: os.environ.get(k) for k in ("HOME", "LLAMA_CACHE", "HF_HOME", "XDG_CACHE_HOME")},
          open(os.path.join(os.path.dirname(args[args.index("--models-preset") + 1]), "router-env.json"), "w"))
port = int(args[args.index("--port") + 1])
ini = open(args[args.index("--models-preset") + 1]).read()
names = [line[1:-1] for line in ini.splitlines() if line.startswith("[")]
loaded = set()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self.reply(200, {"status": "ok"})
        elif self.path == "/models": self.reply(200, {"data": [{"id": n, "status": {"value": "loaded" if n in loaded else "unloaded"}} for n in names]})
        else: self.reply(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        name = body.get("model")
        if name not in names: return self.reply(400, {"error": "not found"})
        if self.path == "/models/load":
            print(f"spawning server instance with name={name} on port 4242", flush=True)
            if name.startswith("oom"):
                print("[4242] E ggml_backend_cuda_buffer_type_alloc_buffer: cudaMalloc failed: out of memory", flush=True)
                return self.reply(500, {"error": "failed to load"})
            loaded.add(name); self.reply(200, {"success": True})
        elif self.path == "/models/unload":
            if name not in loaded: return self.reply(400, {"error": "not loaded"})
            loaded.discard(name); self.reply(200, {"success": True})
        else: self.reply(404, {})
socketserver.TCPServer.allow_reuse_address = True
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""

# Records each time it is run, standing in for the GPU sampler.
_SAMPLER = r"""
import sys, time
open(sys.argv[1], "a").write(f"{time.monotonic()}\n")
"""


def _program(directory: Path, name: str, source: str) -> str:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Control:
    """HTTP over the host's Unix socket, as the server's adapter speaks it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def call(self, method: str, url: str, body: bytes | None = None, headers=None):
        connection = http.client.HTTPConnection("control", timeout=30)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.connect(str(self.path))
        connection.request(method, url, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("content-type")

    def json(self, method: str, url: str, value=None):
        status, body, _ = self.call(
            method, url, json.dumps(value).encode() if value is not None else None
        )
        return status, json.loads(body)


@pytest.fixture
def host(tmp_path):
    # Short: a Unix socket path is limited to 107 bytes, and pytest's
    # temporary directories are long.
    import tempfile

    run = Path(tempfile.mkdtemp(prefix="nr-"))
    bins = tmp_path / "bin"
    bins.mkdir()
    settings = HostSettings(
        llama_server=_program(bins, "llama-server", _ROUTER),
        models_dir=tmp_path / "models",
        config_path=tmp_path / "runtime.json",
        run_dir=run,
        chat_port=_free_port(),
        poll_s=0.1,
        sample_command=(_program(bins, "sample", _SAMPLER), str(tmp_path / "samples")),
    )
    running = Host(settings, PRESETS)
    thread = threading.Thread(target=running.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not settings.socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    yield running, _Control(settings.socket_path)
    running.stop()
    thread.join(timeout=20)
    running.close()


def _tell(running: Host, document) -> None:
    running.settings.config_path.write_text(json.dumps(document), encoding="utf-8")
    running.reload()


def test_the_host_runs_what_it_is_told_and_loads_by_name(host):
    running, control = host
    _tell(running, {"chat": [QWEN]})

    status, body = control.json("GET", "/status")
    assert status == 200
    assert body["chat"] == [
        {
            "name": "qwen-here",
            "preset": "qwen3_8-27b",
            "residency": "on-demand",
            "state": "unloaded",
            "loaded": False,
            "error": None,
        }
    ]

    assert control.json("POST", "/load", {"name": "qwen-here"})[0] == 200
    assert control.json("GET", "/status")[1]["chat"][0]["loaded"] is True
    assert control.json("POST", "/unload", {"name": "qwen-here"})[0] == 200
    # A second unload is the state asked for, not an error.
    assert control.json("POST", "/unload", {"name": "qwen-here"})[0] == 200

    # A name it was not told to run is not a name at all.
    assert control.json("POST", "/load", {"name": "../etc"})[0] == 404


def test_the_router_finds_no_cache_to_offer(host):
    """With a real home, the router also offers every cached model, with a download
    argument (measured on the workstation, D78). Every place it looks is an empty directory."""

    running, _ = host
    _tell(running, {"chat": [QWEN]})
    seen = json.loads((running.settings.run_dir / "router-env.json").read_text())
    empty = running.settings.run_dir / "empty"
    assert seen == {key: str(empty) for key in seen}, seen
    assert list(empty.iterdir()) == []


def test_a_refused_document_leaves_what_was_running(host):
    running, control = host
    _tell(running, {"chat": [QWEN]})
    _tell(running, {"chat": [{**QWEN, "extra_flags": ["--x"]}]})

    body = control.json("GET", "/status")[1]
    assert "does not take" in body["problem"]
    assert [model["name"] for model in body["chat"]] == ["qwen-here"]


# -- the fetcher, against a local stand-in for the Hub --------------------------


@pytest.fixture
def hub(tmp_path):
    """Serves `/<repo>/resolve/<revision>/<name>` from a directory, with ranges."""

    root = tmp_path / "hub"
    root.mkdir()
    requests: list[dict[str, str | None]] = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            name = self.path.rsplit("/", 1)[-1]
            data = (root / name).read_bytes()
            ranged = self.headers.get("range")
            requests.append({"path": self.path, "range": ranged})
            if ranged:
                start = int(ranged.split("=")[1].rstrip("-"))
                data = data[start:]
                self.send_response(206)
            else:
                self.send_response(200)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}", requests
    server.shutdown()


def _preset(blobs: dict[str, bytes]) -> dict[str, dict]:
    return {
        "tiny": {
            "local": {
                "facts": {
                    "weights": {
                        "repo": "org/tiny",
                        "revision": "0" * 40,
                        "files": [
                            {
                                "name": name,
                                "size": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "role": "model" if index == 0 else "mmproj",
                            }
                            for index, (name, data) in enumerate(blobs.items())
                        ],
                    },
                }
            }
        }
    }


def _wait(fetcher: Fetcher, preset: str = "tiny") -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = fetcher.status().get(preset)
        if job and job["state"] not in ("fetching", "verifying"):
            return job
        time.sleep(0.02)
    raise TimeoutError(fetcher.status())


def test_weights_are_downloaded_verified_and_recorded(tmp_path, hub):
    root, url, requests = hub
    blobs = {"tiny.gguf": b"weights" * 1000, "mmproj.gguf": b"projector"}
    for name, data in blobs.items():
        (root / name).write_bytes(data)
    models = tmp_path / "models"
    models.mkdir()
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    assert fetcher.start("tiny")[0] == 202
    assert _wait(fetcher)["state"] == "done"

    assert (models / "tiny.gguf").read_bytes() == blobs["tiny.gguf"]
    assert not (models / "tiny.gguf.part").exists()
    assert (models / ".verified" / "tiny.gguf.json").exists()
    # From the pinned commit, never a branch.
    assert requests[0]["path"] == f"/org/tiny/resolve/{'0' * 40}/tiny.gguf"

    from neuman_server.adapters.weights import weights_state

    files = _preset(blobs)["tiny"]["local"]["facts"]["weights"]["files"]
    assert weights_state(models, files)["state"] == "verified"


def test_a_file_under_a_directory_is_fetched_from_there_and_saved_by_name(tmp_path, hub):
    """A quantisation published in a directory of its own still lands flat in
    `modelsDir`, where llama.cpp looks for a split model's other shards."""

    root, url, requests = hub
    blobs = {"tiny-00001-of-00001.gguf": b"weights" * 100}
    (root / "tiny-00001-of-00001.gguf").write_bytes(blobs["tiny-00001-of-00001.gguf"])
    models = tmp_path / "models"
    models.mkdir()
    preset = _preset(blobs)
    preset["tiny"]["local"]["facts"]["weights"]["files"][0]["path"] = (
        "Q4/tiny-00001-of-00001.gguf"
    )
    fetcher = Fetcher(models, tmp_path / "run", url, preset)

    fetcher.start("tiny")
    assert _wait(fetcher)["state"] == "done"
    assert requests[0]["path"] == f"/org/tiny/resolve/{'0' * 40}/Q4/tiny-00001-of-00001.gguf"
    assert (models / "tiny-00001-of-00001.gguf").read_bytes() == blobs[
        "tiny-00001-of-00001.gguf"
    ]


def test_an_interrupted_download_resumes_from_where_it_stopped(tmp_path, hub):
    root, url, requests = hub
    blobs = {"tiny.gguf": bytes(range(256)) * 64}
    (root / "tiny.gguf").write_bytes(blobs["tiny.gguf"])
    models = tmp_path / "models"
    models.mkdir()
    (models / "tiny.gguf.part").write_bytes(blobs["tiny.gguf"][:5000])
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    fetcher.start("tiny")
    assert _wait(fetcher)["state"] == "done"
    assert requests[0]["range"] == "bytes=5000-"
    assert (models / "tiny.gguf").read_bytes() == blobs["tiny.gguf"]


def test_bytes_that_are_not_the_pinned_ones_are_never_renamed_into_place(tmp_path, hub):
    root, url, _ = hub
    blobs = {"tiny.gguf": b"the real weights"}
    (root / "tiny.gguf").write_bytes(b"something else!!")  # same length, other bytes
    models = tmp_path / "models"
    models.mkdir()
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    fetcher.start("tiny")
    job = _wait(fetcher)
    assert job["state"] == "failed"
    assert "pinned hash" in job["error"]
    assert not (models / "tiny.gguf").exists()
    assert not (models / "tiny.gguf.part").exists()


def test_a_file_fetched_by_hand_is_adopted_without_downloading(tmp_path, hub):
    """How the 3090's 16 GB arrives: hashed in place, not fetched again."""

    _, url, requests = hub
    blobs = {"tiny.gguf": b"fetched by hand"}
    models = tmp_path / "models"
    models.mkdir()
    (models / "tiny.gguf").write_bytes(blobs["tiny.gguf"])
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    fetcher.start("tiny")
    assert _wait(fetcher)["state"] == "done"
    assert requests == []
    assert (models / ".verified" / "tiny.gguf.json").exists()


def test_somebody_elses_file_under_a_pinned_name_is_refused_not_overwritten(tmp_path, hub):
    _, url, requests = hub
    blobs = {"tiny.gguf": b"the real weights"}
    models = tmp_path / "models"
    models.mkdir()
    (models / "tiny.gguf").write_bytes(b"something else!!")
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    fetcher.start("tiny")
    job = _wait(fetcher)
    assert job["state"] == "failed"
    assert "another hash" in job["error"]
    assert (models / "tiny.gguf").read_bytes() == b"something else!!"
    assert requests == []


def test_a_fetch_names_a_preset_and_nothing_else(tmp_path):
    fetcher = Fetcher(tmp_path, tmp_path / "run", "http://127.0.0.1:9", {})
    assert fetcher.start("someone/evil-GGUF")[0] == 404
    assert fetcher.status() == {}


def test_a_download_that_would_fill_the_disk_is_refused_before_it_starts(
    tmp_path, hub, monkeypatch
):
    """The database shares the disk; a download that took the last of it would be
    discovered at 97% and leave the appliance unable to write."""

    import shutil

    _, url, requests = hub
    blobs = {"tiny.gguf": b"x" * 4096}
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(1 << 40, 1 << 40, 512 << 20),
    )
    fetcher = Fetcher(models, tmp_path / "run", url, _preset(blobs))

    fetcher.start("tiny")
    job = _wait(fetcher)
    assert job["state"] == "failed"
    assert "512 MiB is free" in job["error"] and "leaves 1.0 GiB" in job["error"], job
    assert requests == []


def test_deleting_removes_the_pinned_files_and_nothing_else(tmp_path):
    blobs = {"tiny.gguf": b"weights", "mmproj.gguf": b"projector"}
    models = tmp_path / "models"
    (models / ".verified").mkdir(parents=True)
    for name, data in blobs.items():
        (models / name).write_bytes(data)
        (models / ".verified" / f"{name}.json").write_text("{}")
    (models / "someone-elses.gguf").write_bytes(b"keep")
    fetcher = Fetcher(models, tmp_path / "run", "http://127.0.0.1:9", _preset(blobs))

    status, body = fetcher.delete("tiny")
    assert status == 200
    assert body["freed_bytes"] == len(b"weights") + len(b"projector") + 4
    assert sorted(p.name for p in models.iterdir()) == [".verified", "someone-elses.gguf"]
    assert fetcher.delete("someone/evil")[0] == 404


def test_every_shipped_local_preset_is_a_document_the_host_accepts():
    """What the server writes and what the host reads, joined over the real presets.

    The two halves are checked separately elsewhere, and once disagreed: the
    server wrote a setting the host, rightly, refused — refusing the whole
    document. Found on hardware (N170).
    """

    from neuman_server.composition.runtime import runtime_document
    from neuman_server.decision.harness.presets import build_local_entry
    from neuman_server.foundation.state import load_presets

    shipped = load_presets()
    for preset_id, preset in shipped["models"].items():
        if "local" not in preset:
            continue
        baseline = {"models": {"m": build_local_entry(shipped, preset_id, "m")}}
        plan = parse_plan(runtime_document(baseline), PRESETS, MODELS)
        assert plan.chat, preset_id


def test_a_chat_model_that_ran_out_of_memory_says_so_in_status(host):
    """The router answers only "failed to load"; its child's output says why."""

    running, control = host
    _tell(running, {"chat": [{**QWEN, "name": "oom-qwen"}]})
    control.json("POST", "/load", {"name": "oom-qwen"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        chat = control.json("GET", "/status")[1]["chat"][0]
        if chat["error"]:
            break
        time.sleep(0.05)
    assert chat["loaded"] is False
    assert "ran out of memory" in chat["error"], chat


def test_a_load_is_sampled_when_it_happens_not_at_the_next_tick(host, tmp_path):
    """The panel's bar follows the runtime's own changes: each load, unload and
    router line that moves memory runs the sampler, and a burst runs it once."""

    running, control = host
    _tell(running, {"chat": [QWEN]})
    samples = tmp_path / "samples"

    def count():
        return len(samples.read_text().splitlines()) if samples.exists() else 0

    def settle(previous):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and count() == previous:
            time.sleep(0.05)
        time.sleep(0.3)
        return count()

    before = count()
    control.json("POST", "/load", {"name": "qwen-here"})
    after_chat = settle(before)
    assert after_chat > before, "loading a chat model did not sample the GPU"

    control.json("POST", "/unload", {"name": "qwen-here"})
    assert settle(after_chat) > after_chat, "unloading a chat model did not sample the GPU"
