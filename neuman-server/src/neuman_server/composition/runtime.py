"""Tell the runtime host what to run (D78).

The host reads one document: the local chat models, each with its typed
settings. It is derived from the baseline and nothing else, so it is rewritten
whenever the baseline is stored and at startup, and the host re-reads it when it
changes. A file rather than a request because the host may restart on its own,
and a file is still there when it does.

The host trusts none of it (`runtime/plan.py`): every name must be a shipped
preset that runs on this machine and every number is bounded there too. This
side's job is only to say what the operator chose.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..foundation.logging import get_logger

__all__ = ["publish_runtime", "runtime_document"]

_log = get_logger("neuman.server.runtime")


def runtime_document(baseline: dict[str, Any]) -> dict[str, Any]:
    chat = []
    for name, model in sorted((baseline.get("models") or {}).items()):
        local = model.get("local")
        if not isinstance(local, dict):
            continue
        chat.append(
            {
                "name": name,
                "preset": model.get("preset"),
                "context_size": model.get("context_tokens"),
                **local,
            }
        )
    return {"chat": chat}


def publish_runtime(services: Any) -> None:
    """Write the document for `services.baseline`, if it changed.

    Never fails the caller: the baseline is already stored, and a host that
    cannot be told is a panel that says so, not a write to roll back.
    """

    path = services.settings.runtime_config
    text = json.dumps(runtime_document(services.baseline), indent=1, sort_keys=True) + "\n"
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        _log.warning(
            "could not write the runtime document",
            fields={"path": str(path), "error": str(exc)},
        )
