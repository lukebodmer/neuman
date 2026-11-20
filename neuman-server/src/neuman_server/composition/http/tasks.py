"""The task surface: author, list, run, stop, and read what a run did.

Everything here is the caller's own tasks. A task is not a principal and has no
cross-account read: the route resolves the principal and passes its id into the
service, which scopes every store query by it. An admin surface, if one is
added, should be a second route over the same service rather than a flag on
this one (D9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jsonschema import ValidationError

from ...decision.tasks import TaskValidationError
from ..tasks import (
    TaskBusy,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    run_now,
    step_transcript,
    stop_run,
    update_task,
)
from .deps import Unauthorized, connection, require_csrf, require_principal, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["task_routes"]

# Fields the server owns. A draft that names one is refused rather than
# silently ignored: a client that thought it could set `id` should learn
# otherwise, not have its value dropped.
_OWNED = ("id", "account", "created_at", "updated_at", "next_run_at", "last_run_at")


def task_routes() -> list:
    from starlette.routing import Route

    return [
        Route("/api/tasks", _list, methods=["GET"]),
        Route("/api/tasks", _create, methods=["POST"]),
        Route("/api/tasks/{task_id}", _get, methods=["GET"]),
        Route("/api/tasks/{task_id}", _patch, methods=["PATCH"]),
        Route("/api/tasks/{task_id}", _delete, methods=["DELETE"]),
        Route("/api/tasks/{task_id}/run", _run, methods=["POST"]),
        Route("/api/tasks/{task_id}/stop", _stop, methods=["POST"]),
        Route(
            "/api/tasks/{task_id}/runs/{run_id}/steps/{index:int}",
            _step,
            methods=["GET"],
        ),
    ]


async def _body(request: Request) -> dict | None:
    try:
        value = await request.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _list(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        payload = await list_tasks(
            services, account_id=principal.account_id, account=principal.name
        )
    return JSONResponse(payload)


async def _create(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        body = await _body(request)
        if body is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        owned = [name for name in _OWNED if name in body]
        if owned:
            return JSONResponse(
                {"error": "invalid_request", "detail": f"server-owned: {owned}"},
                status_code=400,
            )

    try:
        payload = await create_task(
            services, account_id=principal.account_id, account=principal.name, body=body
        )
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_request", "detail": exc.message}, status_code=400
        )
    except TaskValidationError as exc:
        return JSONResponse(
            {"error": "task_not_allowed", "problems": list(exc.problems)},
            status_code=400,
        )
    return JSONResponse(payload, status_code=201)


async def _get(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    task_id = request.path_params["task_id"]
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        payload = await get_task(
            services,
            account_id=principal.account_id,
            account=principal.name,
            task_id=task_id,
        )
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(payload)


async def _patch(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)
    task_id = request.path_params["task_id"]
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        body = await _body(request)
        if body is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        owned = [name for name in _OWNED if name in body]
        if owned:
            return JSONResponse(
                {"error": "invalid_request", "detail": f"server-owned: {owned}"},
                status_code=400,
            )

    try:
        payload = await update_task(
            services,
            account_id=principal.account_id,
            account=principal.name,
            task_id=task_id,
            body=body,
        )
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_request", "detail": exc.message}, status_code=400
        )
    except TaskValidationError as exc:
        return JSONResponse(
            {"error": "task_not_allowed", "problems": list(exc.problems)},
            status_code=400,
        )
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(payload)


async def _delete(request: Request) -> Response:
    from starlette.responses import JSONResponse
    from starlette.responses import Response as PlainResponse

    services = services_of(request)
    task_id = request.path_params["task_id"]
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        removed = await delete_task(services, account_id=principal.account_id, task_id=task_id)
    if not removed:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return PlainResponse(status_code=204)


async def _run(request: Request) -> Response:
    """Run now. The body is optional: `{"inputs": {name: value}}`."""

    from starlette.responses import JSONResponse

    services = services_of(request)
    task_id = request.path_params["task_id"]
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

    inputs: dict[str, str] = {}
    if await request.body():
        body = await _body(request)
        given = body.get("inputs", {}) if body is not None else None
        if not isinstance(given, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in given.items()
        ):
            return JSONResponse(
                {"error": "invalid_request", "detail": "inputs must map names to strings"},
                status_code=400,
            )
        inputs = given

    try:
        payload = await run_now(
            services,
            account_id=principal.account_id,
            account=principal.name,
            task_id=task_id,
            inputs=inputs,
        )
    except TaskBusy as exc:
        return JSONResponse({"error": "task_busy", "detail": str(exc)}, status_code=409)
    except TaskValidationError as exc:
        return JSONResponse(
            {"error": "invalid_inputs", "problems": list(exc.problems)}, status_code=400
        )
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    # 202: the run is created, but its turn may still be waiting for a client.
    return JSONResponse({"run": payload}, status_code=202)


async def _stop(request: Request) -> Response:
    """Stop the task's queued or running run. 409 when there is none."""

    from starlette.responses import JSONResponse

    services = services_of(request)
    task_id = request.path_params["task_id"]
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
            require_csrf(request, principal)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

    payload = await stop_run(
        services, account_id=principal.account_id, account=principal.name, task_id=task_id
    )
    if payload is None:
        return JSONResponse({"error": "not_running"}, status_code=409)
    return JSONResponse({"run": payload})


async def _step(request: Request) -> Response:
    """One agent step's transcript, read through its run (D99).

    A GET the task view repeats while the step runs; it records nothing, for the
    reason reading your own session records nothing.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

    payload = await step_transcript(
        services,
        account_id=principal.account_id,
        task_id=request.path_params["task_id"],
        run_id=request.path_params["run_id"],
        index=request.path_params["index"],
    )
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(payload)
