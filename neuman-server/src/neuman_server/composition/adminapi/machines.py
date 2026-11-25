"""Machines on the appliance: the named places dispatches run (D112).

Two surfaces, one object, and they answer different questions.

`GET /admin/machines` is the operator's view: every machine on the appliance,
whose it is, whether it is connected now, what release it runs and what an
operator has written down about what is installed there. For a lab the useful
row is "who is on the dgx", which spans accounts by definition — a machine is
scoped to an account because *reach* is, so one physical box appears once per
person and the label is what groups them.

`notes` is the only writable field. Everything else on the row is reported by a
client at its handshake, so editing it here would be an operator asserting a
fact about somebody else's computer; what is installed is the thing the
appliance cannot learn and the model needs.

Notes are told to the model, so they are a prompt surface and are treated as
one: bounded, audited, and never a grant. A note saying "CUDA 12" does not put a
GPU in the tool's reach; it stops the model discovering the absence one refused
turn at a time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AuditStore, MachineStore
from ...adapters.persistence.machines import MACHINE_LABEL
from ...decision.audit import chain
from ...decision.identity.authz import Denied, require_admin
from .deps import admin_actor, connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["machine_routes"]

# The same ceiling the column carries. Repeated because the route must refuse a
# document it would only fail to store: a 500 from a CHECK constraint is not a
# sentence anyone can act on.
MAX_NOTES = 2000


def machine_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/machines", _list_machines, methods=["GET"]),
        Route("/admin/machines/{account}/{label}", _set_notes, methods=["PATCH"]),
    ]


async def _list_machines(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        rows = await MachineStore(conn).list_all()

    # The live set, read once, so "connected" and the release come from the same
    # moment. The stored row is the last hello and may be days old; a page that
    # merged them per row would show a machine as online with yesterday's
    # release.
    live = {
        (entry["account"], entry["machine"]): entry
        for entry in services.execution.connected_clients()
    }
    return JSONResponse(
        {
            "machines": [
                {
                    "account": row.account_id,
                    "label": row.label,
                    "kind": row.kind,
                    "notes": row.notes,
                    "last_seen_at": (
                        row.last_seen_at.isoformat() if row.last_seen_at is not None else None
                    ),
                    # Live values win where the machine is connected now.
                    "release": (live.get((row.account_id, row.label)) or {}).get(
                        "release", row.release
                    ),
                    "rpc": (live.get((row.account_id, row.label)) or {}).get("rpc", row.rpc),
                    "connected": (row.account_id, row.label) in live,
                }
                for row in rows
            ]
        }
    )


async def _set_notes(request: Request) -> Response:
    from starlette.responses import JSONResponse

    account = request.path_params["account"]
    label = request.path_params["label"]
    if not MACHINE_LABEL.fullmatch(label):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("notes"), str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    notes = body["notes"].strip()
    if len(notes) > MAX_NOTES:
        return JSONResponse(
            {
                "error": "notes_too_long",
                "detail": f"notes are at most {MAX_NOTES} characters",
            },
            status_code=400,
        )

    services = services_of(request)
    async with connection(request) as conn:
        actor = admin_actor(request)
        try:
            await require_admin(
                actor, directory=services.directory, policy=services.admin_policy
            )
        except Denied as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        store = MachineStore(conn)
        row = await store.get(account, label)
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        await store.set_notes(account, label, notes)
        await chain.append(
            AuditStore(conn),
            actor=actor,
            action="settings.machine_notes",
            # The length, not the text: a note describes a machine's toolchain,
            # and the chain is read by people looking for who changed what —
            # quoting 2 KB into every entry makes that harder, not easier. The
            # text itself is in the row, and the row's change is what this
            # records.
            payload={"account": account, "machine": label, "characters": len(notes)},
            mirror=services.settings.audit_mirror_path,
        )

    return JSONResponse({"account": account, "label": label, "notes": notes})
