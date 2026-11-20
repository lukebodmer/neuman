"""Read the presets the server ships (D76).

One JSON file per provider and per model under `neuman_server/presets/`, keyed by
the file's stem. They are part of the release, not of the deployment, so a file
that is unreadable or violates `presets.schema.json` is a build defect and stops
the server rather than leaving the administration page short of a tile.

Shape only. What a preset *means* for a baseline entry — which fields are facts,
how a route merges over its model — is the decision layer's
(`decision/harness/presets.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["PRESETS_DIR", "PresetError", "load_presets"]

# Beside the package, because the presets are versioned with the code that
# enforces them: a preset and the check that holds an entry to it cannot be
# releases apart.
PRESETS_DIR = Path(__file__).resolve().parents[2] / "presets"


class PresetError(RuntimeError):
    """A shipped preset cannot be trusted."""


def load_presets(directory: str | Path = PRESETS_DIR) -> dict[str, Any]:
    """Assemble and validate the preset document."""

    base = Path(directory)
    document: dict[str, Any] = {"providers": {}, "models": {}}
    for kind in ("providers", "models"):
        for file in sorted((base / kind).glob("*.json")):
            try:
                document[kind][file.stem] = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PresetError(f"{file}: cannot read preset: {exc}") from exc

    from jsonschema import ValidationError
    from neuman_contracts import presets as contract

    try:
        contract.validate(document)
    except ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path)
        raise PresetError(f"presets violate their schema at {where}: {exc.message}") from exc
    return document
