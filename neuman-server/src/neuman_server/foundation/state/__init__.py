"""File-backed state, deliberately not in the database (docs/decisions.md D18).

The policy overlay must survive a wiped database because it is the
runtime-authoring surface. The presets ship with the server, and provider keys
are files so that no database row, backup or audit entry ever holds one (D77).
"""

from .overlay import (
    OverlayError,
    delete_overlay,
    list_overlays,
    read_overlay,
    write_overlay,
)
from .presets import PresetError, load_presets
from .secrets import SecretError, key_path, sweep_secrets, write_secret

__all__ = [
    "OverlayError",
    "PresetError",
    "SecretError",
    "delete_overlay",
    "key_path",
    "list_overlays",
    "load_presets",
    "read_overlay",
    "sweep_secrets",
    "write_overlay",
    "write_secret",
]
