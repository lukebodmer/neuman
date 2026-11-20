"""What the GPU says, from the one unit with device access (D30).

`neuman-server` runs with `PrivateDevices=yes`, so it never asks the device.
A oneshot unit runs `nvidia-smi` on a timer and writes `/run/neuman/gpu/state.json`;
this reads it. Which models are loaded is the runtime host's to say
(`adapters/runtime.py`), not this file's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation import get_logger

__all__ = ["GpuReporter"]

_log = get_logger("neuman.server.adapters.gpu")


@dataclass(frozen=True, slots=True)
class GpuReporter:
    """Reads the GPU sample."""

    sample_path: Path

    def gpu(self) -> dict[str, Any]:
        """The most recent GPU sample.

        `available: false` covers both "this machine has no NVIDIA GPU" and "the
        sampler has not run yet" — the second resolves itself within the timer's
        period, and distinguishing them would mean the panel reporting on its own
        plumbing.
        """

        absent = {
            "available": False,
            "devices": [],
            "processes": [],
            # Distinct from the sampler's own reason: this one means the sampler has
            # not written anything the server can read, which on a fresh boot is a
            # few seconds and otherwise is a failed unit.
            "unavailable_because": "no GPU sample yet",
        }

        try:
            parsed = json.loads(self.sample_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return absent
        except OSError as error:
            _log.warning("could not read the GPU sample", fields={"error": str(error)})
            return {**absent, "unavailable_because": f"cannot read the GPU sample: {error}"}

        if not isinstance(parsed, dict):
            return absent
        return parsed
