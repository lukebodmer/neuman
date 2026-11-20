"""The agent loop and its state."""

from .context import ContextConfig, ContextManager
from .inbox import Inbox, InboxMessage
from .loop import Actor, Harness, HarnessConfig, TurnResult
from .projection import PROJECTION_KINDS, project, resolve_images

__all__ = [
    "PROJECTION_KINDS",
    "Actor",
    "ContextConfig",
    "ContextManager",
    "Harness",
    "HarnessConfig",
    "Inbox",
    "InboxMessage",
    "TurnResult",
    "project",
    "resolve_images",
]
