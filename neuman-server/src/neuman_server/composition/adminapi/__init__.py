"""The admin surface: Unix socket, peer credentials.

A thin adapter over the same core operations the HTTP surface uses (docs/decisions.md D9).
Admin *authority* is checked inside those operations — `require_admin`,
so the HTTP and socket surfaces cannot drift apart on who may do what.

That sentence used to end "authorization is checked inside those operations,
not here", which was true of every route that mutates and false of every route
that only reads: they called operations taking no actor, so they answered
anyone who reached them (N27). `guard.py` now refuses any `/admin` request
without an identified peer, before routing. The two checks are different
claims — "there is a peer" and "the peer is an admin" — and the appliance
makes both.
"""

from __future__ import annotations

from typing import Any

from .accounts import admin_account_routes
from .audit import admin_audit_routes
from .catalog import admin_catalog_routes
from .gateway import admin_gateway_routes
from .machines import machine_routes
from .overview import admin_overview_routes
from .policy import admin_policy_routes
from .sessions import admin_session_routes
from .settings import admin_settings_routes
from .system import admin_system_routes
from .tools import admin_tool_routes

__all__ = ["admin_routes"]


def admin_routes() -> list[Any]:
    return [
        *admin_account_routes(),
        *admin_policy_routes(),
        *admin_audit_routes(),
        *admin_session_routes(),
        *admin_settings_routes(),
        *admin_system_routes(),
        *admin_tool_routes(),
        *admin_gateway_routes(),
        *admin_catalog_routes(),
        *admin_overview_routes(),
        *machine_routes(),
    ]
