"""Dumb shared services: config, logging, the database, file-backed state.

No business logic and no opinion about what anything means. Imports nothing
else inside `neuman_server` (docs/decisions.md D7).
"""

from .config import ConfigError, Settings
from .logging import bind, configure_logging, get_logger, log_context

__all__ = [
    "ConfigError",
    "Settings",
    "bind",
    "configure_logging",
    "get_logger",
    "log_context",
]
