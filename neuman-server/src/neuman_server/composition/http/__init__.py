"""The session surface: REST + WebSocket over TLS, session cookies."""

from __future__ import annotations

from typing import Any

from .admin import admin_http_routes
from .auth import auth_routes
from .browse import browse_routes
from .client import client_routes
from .models import model_routes
from .policy import policy_routes
from .sessions import session_routes
from .tasks import task_routes
from .ws import ws_routes

__all__ = ["http_routes"]


def http_routes() -> list[Any]:
    return [
        *auth_routes(),
        *admin_http_routes(),
        *session_routes(),
        *task_routes(),
        *policy_routes(),
        *model_routes(),
        *browse_routes(),
        *ws_routes(),
        *client_routes(),
    ]
