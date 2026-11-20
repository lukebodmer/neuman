"""Sample GPU state into a file the server can read.

This unit exists because of one line in `neuman-server.service`:
`PrivateDevices=yes`. The server cannot see `/dev/nvidia*`, deliberately — it is
the process the untrusted network talks to, and giving it device access so it can
draw a status bar would trade the appliance's central hardening for a
convenience.

So the device access lives here, in a unit that does nothing else: no database, no
network, no ability to start or stop anything. It runs `nvidia-smi`, writes JSON,
and exits. A file is the entire interface between the two, which means the worst a
compromise of this unit yields is a lie about how full the GPU is.

Written atomically — temp file then rename — because the server may read at any
moment and a half-written JSON file is a parse error rather than stale data.

`nvidia-smi` absent is not an error. An appliance with no NVIDIA GPU is an
ordinary appliance; it gets a report saying so rather than a failed unit that an
operator has to investigate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

GPU_FIELDS = ("index", "name", "memory.used", "memory.total", "utilization.gpu")


def _run(args: list[str]) -> tuple[str | None, str]:
    """Output, and *why* there is none.

    The reason matters as much as the result. `available: false` used to mean four
    different things — no driver, a version mismatch, a timeout, or a sampler that had
    never run — and an operator looking at a panel that says "no GPU detected" on a
    machine with a 3090 in it has no way to tell which. So the failure is carried
    forward and reported (N79).
    """

    try:
        done = subprocess.run(args, capture_output=True, timeout=15)
    except FileNotFoundError:
        return None, "no `nvidia-smi` on PATH; `services.neuman.gpuTools` is unset"
    except OSError as error:
        return None, f"could not run nvidia-smi: {error}"
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi did not answer within 15s"

    if done.returncode != 0:
        # Both streams, because `nvidia-smi` prints the version mismatch to
        # **stdout** — checked — and reading only stderr reported the useless
        # `exit 18` for the single most confusing failure here: a tool that exists,
        # runs, and finds nothing.
        detail = (done.stderr or b"").decode("utf-8", "replace").strip() or (
            done.stdout or b""
        ).decode("utf-8", "replace").strip()
        first = detail.splitlines()[0] if detail else f"exit {done.returncode}"
        return None, first

    return done.stdout.decode("utf-8", "replace"), ""


def _devices(smi: str) -> tuple[list[dict[str, object]], str]:
    out, why = _run(
        [
            smi,
            f"--query-gpu={','.join(GPU_FIELDS)}",
            "--format=csv,noheader,nounits",
        ]
    )
    if out is None:
        return [], why

    devices: list[dict[str, object]] = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(GPU_FIELDS):
            continue
        try:
            devices.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_used_mb": int(parts[2]),
                    "memory_total_mb": int(parts[3]),
                    "utilization_pct": int(parts[4]),
                }
            )
        except ValueError:
            # A field that is not a number — `[N/A]` on some drivers — makes that
            # one device unreportable rather than the whole sample.
            continue
    return devices, ""


def _processes(smi: str) -> list[dict[str, object]]:
    """Which processes hold VRAM, so the panel can attribute it.

    Names only, never command lines: a command line can contain a path or an
    argument that says more about the machine than a status panel should, and the
    process *name* is enough to tell a model runtime from a desktop compositor.
    """

    out, _why = _run(
        [
            smi,
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    if out is None:
        return []

    processes: list[dict[str, object]] = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            processes.append(
                {
                    "pid": int(parts[0]),
                    "name": os.path.basename(parts[1]),
                    "memory_mb": int(parts[2]),
                }
            )
        except ValueError:
            continue
    return processes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gpu-sample.py <output.json>", file=sys.stderr)
        return 2

    target = argv[1]
    smi = os.environ.get("NEUMAN_NVIDIA_SMI", "nvidia-smi")

    devices, why = _devices(smi)
    report = {
        "sampled_at": time.time(),
        # Distinguishes "no GPU on this machine" from "the sampler has not run
        # yet", which the server otherwise cannot tell apart from a missing file.
        "available": bool(devices),
        # And *why* there is none, when there is none. A panel saying "no GPU
        # detected" on a machine with a 3090 in it is worse than one saying
        # "Driver/library version mismatch", which is a sentence an operator can act
        # on.
        "unavailable_because": "" if devices else why,
        "devices": devices,
        "processes": _processes(smi) if devices else [],
    }

    # Temp-then-rename: the server may read at any instant, and `os.replace` is
    # atomic on the same filesystem, so a reader sees either the old sample or the
    # new one and never a truncated file.
    temporary = f"{target}.new"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
