"""Answering the working-directory chooser: what is in a directory, and what it is.

Two routes, one argument. `/api/browse` lists a directory so the chooser can
descend; `/api/environment` reports whether that directory has a realised
development environment, which is what colours the chooser's own control. They
share this file because they share the reasoning below — the same reach check,
the same dispatch outside the harness, the same "the human clicked, the model
did not".

**Listing.** A chooser needs to descend. Policy grants a *subtree* — `/home/alice/projects`
reaches `/home/alice/projects/neuman/neuman-client/web` and every other depth beneath
it — but `/api/policy` returns only the granted roots, so a menu built from it
can offer exactly one level. This route is how the client sees the rest of the
tree it already has reach over.

**Why this is not an HTTP-level `os.scandir`.** The server runs as `neuman`; the
files belong to the acting user, and a home directory is `0700`. A listing done
here would fail on every path in a normal policy, and making it succeed would
mean loosening the home directory so a service account could read it — trading a
real protection for a file picker. So the listing is a dispatch to the acting
user's client, which runs as the user and sees what the user sees.

**Why it does not go through the harness.** The model's path — gate, approval,
audit, inbox — is right for an action the *model* chose. Here the human clicked a
folder in their own file picker. Routing that through the approval flow would pop
a card per click for a person browsing their own directories, which trains people
to approve without reading; and with `auto_accept = "never"` it would make the
picker unusable.

`within_reach` is checked here so a directory outside policy's grant comes back
as `403 outside_policy` rather than an opaque failure from a tool.

**The tool is named by the route, never by the caller** — in both. A route that
let a request choose which tool to dispatch outside the approval flow would be a
way to run `shell` without an approval card, which is the one thing this file
must not become.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import GrantStore
from ...adapters.persistence.machines import MACHINE_LABEL
from ...decision.policy.ops import preview_policy
from ...decision.policy.paths import expand_paths, within_reach
from .deps import (
    Unauthorized,
    connection,
    machine_facts,
    require_principal,
    services_of,
)

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["browse_routes"]

# The tools these routes dispatch, one each. Named here rather than taken from
# the request (see the module docstring).
_TOOL = "fs_list"
_ENV_TOOL = "env_probe"

# Enough to navigate; not enough to serve as a bulk read of a data directory.
_LIMIT = 500


def browse_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/api/browse", _browse, methods=["GET"]),
        Route("/api/environment", _environment, methods=["GET"]),
    ]


async def _browse(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    path = request.query_params.get("path")
    if not path or not path.startswith("/"):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    # The directory chooser wants directories; the attachment picker wants both.
    # One route, because the difference is a listing argument and not a different
    # question — and because a second route would re-read the same policy and
    # eventually disagree about reach.
    files = request.query_params.get("files") == "1"

    # Which computer to list, and it is required. The picker names the branch's
    # machine, because a directory named on one computer is a different
    # directory on another — the whole reason the machine exists (D112). It used
    # to fall back to "the most recently connected client", which is how this
    # picker came to offer the laptop's folders to a session on the DGX (N186).
    machine = request.query_params.get("machine")
    if not machine or not MACHINE_LABEL.fullmatch(machine):
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": "listing must name a machine: add ?machine=<label>",
            },
            status_code=400,
        )

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        facts = await machine_facts(conn, services, principal, machine)

        # Re-composed per request, not cached, so a revoked grant stops working
        # here at the same moment it stops working everywhere else.
        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

    if not within_reach(
        path,
        policy,
        home=facts.home,
    ):
        return JSONResponse(
            {
                "error": "outside_policy",
                "detail": f"{path} is not inside any path this account may read.",
            },
            status_code=403,
        )

    manifest = services.registry.get(_TOOL)
    if manifest is None:
        # The appliance is installed without the listing tool. Worth saying
        # plainly: the chooser degrades to the granted roots, and a generic 500
        # would send someone looking for a bug in the client.
        return JSONResponse({"error": "tool_unavailable", "tool": _TOOL}, status_code=501)

    outcome = await services.execution.dispatch(
        user=principal.name,
        dispatch_id=uuid.uuid4().hex,
        machine=machine,
        tool=_TOOL,
        # `summarise: False` explicitly. Past its threshold `fs_list` groups
        # entries by name shape and prints `d<TAB>FILE#<TAB>x 2036<TAB>(..)`,
        # which is the right answer for a model reading a data directory and
        # the wrong one here: `_parse` would read `FILE#` as an entry, and the
        # chooser would offer a directory that does not exist. This surface
        # navigates, so it always wants real names.
        args={
            "path": path,
            "dirs_only": not files,
            "limit": _LIMIT,
            "summarise": False,
        },
        wait_ms=min(manifest.wait_ms, services.settings.tool_wait_ms),
        # The listing tool declares `path`, so the client checks it before
        # running. An empty surface would refuse the picker outright; the
        # account's own expanded read paths are what the route already checked
        # `within_reach` against.
        surfaces={
            "read": expand_paths(
                policy.files_read,
                home=facts.home,
            ),
            "write": [],
        },
    )

    # `ok` rather than the exit code: a timeout or a cancellation exits non-zero
    # for reasons that have nothing to do with the directory.
    if not outcome.ok:
        # The tool's answer, or a path that is not a directory. Either way the
        # client shows the message rather than an empty folder, because an empty
        # listing and an unreadable one look identical otherwise.
        return JSONResponse(
            {"error": "list_failed", "detail": (outcome.stderr or "").strip()[:500]},
            status_code=422,
        )

    return JSONResponse({"path": path, "entries": _parse(outcome.stdout)})


# Every state the resolver can report, and nothing else. The route translates
# the tool's JSON into its own answer rather than forwarding it, so a probe that
# learns a new reason cannot start a UI state nobody designed — an unknown name
# arrives here as `env_unreadable`, which is the honest "cannot say".
#
# Kept as a set rather than trusting the string because this crosses a trust
# boundary like any other tool output: the probe runs on the user's machine, and
# a reason is a name the browser switches on.
_ENV_REASONS = frozenset(
    {
        "ok",
        "no_environment",
        "env_unrealised",
        "env_stale",
        "env_collected",
        "env_untrusted",
        "env_unreadable",
        "no_such_directory",
    }
)


async def _environment(request: Request) -> Response:
    """Report the development-environment state of a directory.

    Why a dispatch and not a read here: the `.direnv` cache lives in the acting
    user's home, which is `0700`, and this process is `neuman`. The same reason
    `/api/browse` dispatches — and the reason a server-side `resolve()` would
    quietly answer about the wrong machine whenever the client is on another
    host.

    Why not through the harness: the human set a working directory. Nothing was
    proposed by a model, so there is no verdict to seek, and a card per
    directory change would be the chooser's problem all over again.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)

    path = request.query_params.get("path")
    if not path or not path.startswith("/"):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    # The same machine the chooser is listing, so the environment it reports is
    # the one the turn will actually run in (D112).
    machine = request.query_params.get("machine")
    if not machine or not MACHINE_LABEL.fullmatch(machine):
        return JSONResponse(
            {
                "error": "invalid_request",
                "detail": "the probe must name a machine: add ?machine=<label>",
            },
            status_code=400,
        )

    async with connection(request) as conn:
        try:
            principal = await require_principal(request, conn)
        except Unauthorized as exc:
            return JSONResponse({"error": exc.reason}, status_code=exc.status)

        facts = await machine_facts(conn, services, principal, machine)

        policy = await preview_policy(
            principal.name,
            source=services.policy_source(),
            directory=services.directory,
            grants=GrantStore(conn),
        )

    home = facts.home

    if not within_reach(path, policy, home=home):
        return JSONResponse(
            {
                "error": "outside_policy",
                "detail": f"{path} is not inside any path this account may read.",
            },
            status_code=403,
        )

    manifest = services.registry.get(_ENV_TOOL)
    if manifest is None:
        # The appliance is installed without the probe. The chooser then draws
        # no environment state at all, which is the right degradation: a missing
        # answer is not the same as "no environment here", and a 500 would send
        # someone hunting a bug in the client.
        return JSONResponse({"error": "tool_unavailable", "tool": _ENV_TOOL}, status_code=501)

    outcome = await services.execution.dispatch(
        user=principal.name,
        dispatch_id=uuid.uuid4().hex,
        machine=machine,
        tool=_ENV_TOOL,
        args={"cwd": path},
        wait_ms=min(manifest.wait_ms, services.settings.tool_wait_ms),
        # The probe declares `cwd` as a path, so the client checks it before
        # running. The account's own expanded read paths are what this route
        # already checked `within_reach` against.
        surfaces={
            "read": expand_paths(policy.files_read, home=home),
            "write": [],
        },
    )

    if not outcome.ok:
        # A refusal is not a failure for this tool — every environment state
        # exits zero — so a non-ok outcome is the dispatch itself failing: a
        # timeout, a cancellation, no client connected.
        return JSONResponse(
            {"error": "probe_failed", "detail": (outcome.stderr or "").strip()[:500]},
            status_code=422,
        )

    return JSONResponse(_parse_environment(outcome.stdout, path))


def _parse_environment(stdout: str, path: str) -> dict[str, Any]:
    """Turn the probe's JSON into this route's answer.

    Unparseable output is `env_unreadable` rather than a 500. The caller is a
    status light: it has a state for "cannot say" and no state for "the request
    errored", and a directory whose probe came back malformed is exactly the
    case where saying nothing confidently is correct.
    """

    try:
        report = json.loads(stdout)
    except (ValueError, TypeError):
        report = None

    if not isinstance(report, dict):
        return {"path": path, "reason": "env_unreadable"}

    reason = report.get("reason")
    if not isinstance(reason, str) or reason not in _ENV_REASONS:
        return {"path": path, "reason": "env_unreadable"}

    answer: dict[str, Any] = {"path": path, "reason": reason}

    # Only on the resolved path, and only these two. The rc path is the cache's
    # location inside the project and says nothing a user acts on; the profile
    # and the interpreter are what make "which environment is this" answerable
    # in the tooltip and in the prompt.
    if reason == "ok":
        for field in ("profile", "interpreter"):
            value = report.get(field)
            if isinstance(value, str) and value:
                answer[field] = value

    return answer


def _parse(stdout: str) -> list[dict[str, str]]:
    """Turn `fs_list`'s lines into entries.

    The tool prints `d<TAB>name`. Its sentinel lines — `(empty)`, `(stopped at
    N entries)`, and the summary header — are not entries and carry no tab,
    which is what distinguishes them; a name containing a tab is not
    representable on this channel and is skipped rather than guessed at.

    A *grouped* line carries two tabs (`d<TAB>FILE#<TAB>x 2036<TAB>(..)`) and is
    skipped for the same reason: `FILE#` is a shape, not a name, and offering
    it would send the chooser to a directory that does not exist. The caller
    asks for unsummarised output, so this should never arrive — it is rejected
    structurally anyway, because the cost of being wrong is a navigation
    surface that lies.
    """

    entries: list[dict[str, str]] = []
    for line in (stdout or "").splitlines():
        kind, tab, name = line.partition("\t")
        if not tab or kind not in ("d", "f") or not name or "\t" in name:
            continue
        entries.append({"kind": kind, "name": name})
    return entries
