"""App factory, lifespan, startup ordering, health.

This layer wires everything and decides nothing. A request arrives, identity is
resolved, a core operation is called, the response is serialized.

Startup order matters and is explicit:

1. Logging, so every later failure is structured.
2. Config, validated — a bad value must fail here, not on first use.
3. The database pool, waiting for readiness so an unreachable Postgres is a
   startup error with a clear message.
4. Migrations, before anything reads a table.
5. The tool registry, which fails closed: no registry, no dispatch.
6. Background jobs last, so nothing runs against a half-built app.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..adapters.clients import ClientHub
from ..adapters.directory import DatabaseDirectory
from ..adapters.gateway import ModelClient
from ..adapters.gpu import GpuReporter
from ..adapters.passwords import Argon2Passwords
from ..adapters.persistence.settings import SettingsStore
from ..adapters.registry import InstalledRegistry, RegistryError, load_registry
from ..adapters.runtime import FetchClient, RuntimeClient
from ..decision.harness.catalog import ModelCatalog
from ..decision.harness.context import ContextConfig, ContextManager
from ..decision.harness.inbox import Inbox, PendingApproval
from ..decision.harness.presets import preset_problems, refresh_facts
from ..decision.harness.prompt import DEFAULT_NAME
from ..decision.identity.authz import AdminPolicy
from ..decision.identity.throttle import LoginThrottle
from ..decision.policy.source import PolicySource
from ..foundation import Settings, configure_logging, get_logger
from ..foundation.db import Database, migrate
from ..foundation.state import PresetError, load_presets
from .feeds import BranchFeeds
from .runtime import publish_runtime

if TYPE_CHECKING:  # pragma: no cover
    from starlette.applications import Starlette

__all__ = ["Services", "create_app", "lifespan"]

_log = get_logger("neuman.server")

# What a fresh database starts with: no policy and no catalog. `providers` and
# `models` are optional in the schema and absent reads as empty.
EMPTY_BASELINE: dict[str, Any] = {"schema_version": 2, "defaults": {}, "roles": {}, "users": []}


@dataclass(frozen=True, slots=True)
class RunningTurn:
    """A turn running in one branch.

    `task` is the registry's identity as well as the coroutine: `end_turn`
    removes an entry only when it is still this task, so a turn that finishes
    just as another starts cannot take the new one's place. The socket that
    started the turn keeps the same reference to cancel it when that socket
    goes (D63).

    `account_id` and `session_id` are carried so a turn can be found by the
    account it belongs to without a query, and `inbox` is *this turn's* mailbox
    rather than whatever `inbox_for` would return now: removing or closing a
    session drops the registry's entry while the turn keeps running, so a fresh
    lookup answers "nothing pending" about a turn that is blocked (D63).
    """

    account: str
    account_id: str
    session_id: str
    inbox: Inbox
    task: asyncio.Task[Any]


@dataclass
class Services:
    """Everything the surfaces need, wired once.

    Held on the app rather than imported as module globals so tests can build a
    Services with fakes and never touch a socket.
    """

    settings: Settings
    database: Database
    # The tools present now: built in, and those of every package the live
    # baseline enables (D101). The full catalog is `registry.catalog`.
    registry: InstalledRegistry
    directory: DatabaseDirectory
    passwords: Argon2Passwords
    execution: ClientHub
    gateway: ModelClient
    # The GPU sample from the only unit with device access, for the model panel.
    gpu: Any = None
    # The runtime host and the fetcher (D78, D79): what is loaded, and whether a
    # preset's weights are being downloaded.
    runtime: Any = None
    fetch: Any = None
    # The operator's system-prompt text and the policy/model baseline. Both are
    # loaded from the database at startup and replaced in memory when an admin
    # changes them. A string/dict
    # rather than a path: the harness builds a prompt per turn and must not do
    # file IO to do it, and a change has to take effect on the next turn rather
    # than at the next restart (D68).
    operator_prompt: str = ""
    # What the appliance calls itself, in the prompt and in the chat window's
    # brand. A setting rather than a constant because a business running this
    # for its own people is not "Neuman"; it is voice and identity, and it
    # reaches nothing — the identity sentence sits above the reach list and no
    # dispatch reads it.
    agent_name: str = DEFAULT_NAME
    baseline: dict[str, Any] = field(default_factory=dict)
    # The providers and models Neuman ships (D76): what the administration page
    # offers as tiles, and what an entry added from one is held to. Part of the
    # release rather than of the database, so it is read once and never written.
    presets: dict[str, Any] = field(default_factory=lambda: {"providers": {}, "models": {}})
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    admin_policy: AdminPolicy = field(default_factory=AdminPolicy)
    context: ContextManager = field(default_factory=ContextManager)
    # One inbox per live branch (docs/decisions.md D12). The WebSocket posts into
    # it; the harness drains it. Keyed by branch because two branches of one
    # session may have turns running at once — that is what a fork is for.
    inboxes: dict[str, Inbox] = field(default_factory=dict)
    # The one registry of running turns: branch id -> the turn running in it.
    #
    # The branch, not the connection, is what admits one turn at a time. This
    # is what `turn_in_flight` consults before a socket starts a turn: a second
    # socket on the same branch would project the first turn's assistant
    # message before that turn has written its `tool` replies, and the provider
    # refuses the half-answered history (N179). The overview reads the same
    # map, so "is anyone waiting on a turn" has one answer.
    turns: dict[str, RunningTurn] = field(default_factory=dict)
    # The live frames of every branch that has a socket on it (D113). One
    # registry per branch, beside `turns` and with the same lifetime rule: in
    # memory, one process, and nothing here that is not also in the log. A turn
    # publishes through it so a second socket — another window, or another
    # computer holding the same account — sees the step it is on.
    feeds: BranchFeeds = field(default_factory=BranchFeeds)
    # Task runs under way: run id -> the asyncio task walking its steps. Held
    # so the event loop keeps a reference to it, and so a test can await a run
    # to its end; an agent step's own turn is in `turns` like any other.
    task_runners: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    # The overview's usage figures and when they were read (monotonic). Today's
    # spend moves slowly and costs a scan of the replies to compute, while an
    # open overview refreshes on every GPU change; a minute's reuse keeps the
    # live part live and the expensive part rare.
    overview_usage: tuple[float, Any] | None = None
    # When this process began serving, as two clocks for one fact: the wall clock
    # is what the overview shows as a date, and the monotonic one is what the
    # uptime is measured from, because an NTP step moves the first and must not
    # move the second. `None`/`0.0` means the lifespan has not run, which is the
    # ordinary state of a `Services` a test built by hand.
    started_at: datetime | None = None
    started_mono: float = 0.0

    def policy_source(self) -> PolicySource:
        """A fresh source over the current runtime baseline.

        Deliberately not cached: policy is re-read per dispatch so revocation is
        immediate (docs/security-model.md). The baseline dict is replaced, not
        mutated, by an admin write; a turn already holding the old dict keeps
        it for that step, and the next step sees the new one.
        """

        return PolicySource(
            baseline=self.baseline,
            overlay_dir=str(self.settings.overlay_dir),
        )

    def model_catalog(self) -> ModelCatalog:
        """A fresh catalog over the current runtime baseline.

        Not cached, for the same reason `policy_source` is not: an admin can
        change a provider or a model and the next turn must use it without a
        restart.
        """

        return ModelCatalog(
            baseline=self.baseline, local_endpoint=self.settings.runtime_chat_url
        )

    def running_turn(self, branch_id: str) -> RunningTurn | None:
        """The turn running in `branch_id`, or None.

        A finished task is not running even if its done callback has not been
        called yet: `call_soon` puts the removal behind whatever the loop is
        doing, and a message arriving in that window must not be refused for a
        turn that is already over.
        """

        turn = self.turns.get(branch_id)
        if turn is not None and turn.task.done():
            self.turns.pop(branch_id, None)
            return None
        return turn

    def begin_turn(
        self,
        branch_id: str,
        *,
        account: str,
        account_id: str,
        session_id: str,
        inbox: Inbox,
        task: asyncio.Task[Any],
    ) -> None:
        """Register one turn as the branch's running turn."""

        self.turns[branch_id] = RunningTurn(
            account=account,
            account_id=account_id,
            session_id=session_id,
            inbox=inbox,
            task=task,
        )
        task.add_done_callback(lambda _done: self.end_turn(branch_id, task))

    def live_turns(self, account_id: str) -> list[dict[str, Any]]:
        """What this account has running, wherever it was started.

        The rail's live read (D63). It answers from the two in-memory registries
        and touches no database: `turns` says what is running, and the turn's
        own inbox says whether it is blocked on a person. `inboxes` is
        deliberately not scanned — it holds one entry per branch ever opened and
        nothing removes an idle one, so a scan would grow with use rather than
        with work.

        A finished task is pruned here for the same reason `running_turn`
        prunes: `call_soon` puts the done callback behind whatever the loop is
        doing, and a row claiming a turn that is already over is worse than a
        row arriving a moment late.
        """

        live: list[dict[str, Any]] = []
        for branch_id, turn in sorted(self.turns.items()):
            if turn.task.done():
                self.turns.pop(branch_id, None)
                continue
            if turn.account_id != account_id:
                continue
            pending = turn.inbox.pending()
            live.append(
                {
                    "session_id": turn.session_id,
                    "branch_id": branch_id,
                    # Blocked on a human outranks merely running: it is the one
                    # state a reader can do something about (D63).
                    "state": "approval" if pending else "running",
                    "tool": pending[0].tool if pending else None,
                }
            )
        return live

    def pending_approvals(self, branch_id: str) -> list[PendingApproval]:
        """The calls this branch is blocked on, with what their cards need.

        From the *turn's own* inbox when a turn is registered, and only from
        the registry otherwise. Removing or closing a session drops the
        registry's entry while its turn keeps running, so `inbox_for` can hand
        back a fresh, empty inbox for a turn that is in fact blocked — a card
        that says "nothing pending" about a waiting turn is worse than no card
        (D63).
        """

        turn = self.running_turn(branch_id)
        if turn is not None:
            return turn.inbox.pending()
        inbox = self.inboxes.get(branch_id)
        return inbox.pending() if inbox is not None else []

    def end_turn(self, branch_id: str, task: asyncio.Task[Any]) -> None:
        """Remove `task`'s entry, and only `task`'s entry."""

        current = self.turns.get(branch_id)
        if current is not None and current.task is task:
            self.turns.pop(branch_id, None)

    def inbox_for(self, branch_id: str) -> Inbox:
        inbox = self.inboxes.get(branch_id)
        if inbox is None:
            inbox = Inbox(branch_id)
            self.inboxes[branch_id] = inbox
        return inbox

    def drop_inbox(self, branch_id: str) -> None:
        self.inboxes.pop(branch_id, None)

    def drop_inboxes(self, branch_ids: Sequence[str]) -> None:
        """Drop every branch's inbox when its session is removed or closed.

        The branch's live feed goes with it. A removed session's transcript is
        not reachable anywhere, and a buffer of frames from it would be the one
        copy left in memory — the sockets on it are being refused by their next
        read anyway.
        """

        for branch_id in branch_ids:
            self.drop_inbox(branch_id)
            self.feeds.drop(branch_id)


def build_services(settings: Settings) -> Services:
    """Construct every adapter from settings. No I/O beyond reading the registry."""

    try:
        catalog = load_registry(settings.registry_dir)
    except RegistryError as exc:
        # Fail closed and loudly: an appliance with no valid tool set should not
        # come up pretending it can work (docs/decisions.md D5).
        raise RuntimeError(f"tool registry unusable: {exc}") from exc

    # The same rule for the presets: they ship with this code, so one that does
    # not load is a broken release rather than a missing tile.
    presets = _shipped_presets()

    database = Database(
        settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
    )

    services = Services(
        settings=settings,
        database=database,
        # Reads `services.baseline` on every call, so the view follows each
        # admin write: the name is bound below, before anything can call it.
        registry=catalog.installed(lambda: (services.baseline or {}).get("packages") or ()),
        directory=DatabaseDirectory(database),
        passwords=Argon2Passwords(),
        execution=ClientHub(),
        # No base URL and no key: both are per provider, resolved from the
        # declared catalog and passed per call. A key held here was a key sent
        # to every provider, so a second one received the first one's token.
        gateway=ModelClient(timeout_s=settings.gateway_timeout_s),
        context=ContextManager(
            config=ContextConfig(),
        ),
        # Built here, not in the route. A route that constructed its own client
        # per request would be the N38 shape in reverse: the setting exists, the
        # adapter exists, and whether they ever meet depends on a line nobody
        # tests.
        gpu=GpuReporter(sample_path=settings.gpu_sample_path),
        runtime=RuntimeClient(socket=settings.runtime_socket),
        fetch=FetchClient(socket=settings.fetch_socket),
        presets=presets,
    )
    return services


def _shipped_presets() -> dict[str, Any]:
    """The presets beside the package, refused whole if any is wrong."""

    try:
        presets = load_presets()
    except PresetError as exc:
        raise RuntimeError(f"presets unusable: {exc}") from exc
    problems = preset_problems(presets)
    if problems:
        raise RuntimeError("presets inconsistent: " + "; ".join(problems))
    return presets


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    """Open the pool, migrate, start jobs; reverse on the way out."""

    services: Services = app.state.services
    settings = services.settings

    # Before anything else. An appliance that spends a minute migrating has
    # been up for that minute, and the overview should say so rather than start
    # its clock when the first request happens to arrive.
    services.started_at = datetime.now(UTC)
    services.started_mono = time.monotonic()

    settings.ensure_dirs()
    await services.database.open()

    async with services.database.connection() as conn:
        version = await migrate(conn)
    _log.info("schema ready", fields={"version": version})

    await load_runtime(services)

    # Before any client can connect: a job's end is recorded from here on.
    from .client_jobs import record_job_end

    services.execution.on_job_settled = record_job_end(services)

    from .jobs import start_jobs, stop_jobs

    jobs = await start_jobs(services)
    _log.info(
        "server ready",
        fields={"tools": len(services.registry), "bind": settings.bind, "port": settings.port},
    )

    try:
        yield
    finally:
        await stop_jobs(jobs)
        await services.database.close()


def create_app(settings: Settings | None = None, services: Services | None = None) -> Starlette:
    """Build the ASGI app.

    ``services`` is injectable so tests can supply fakes without a database or a
    connected client.
    """

    from starlette.applications import Starlette
    from starlette.routing import Route

    from .adminapi import admin_routes
    from .http import http_routes
    from .http.middleware import middleware_stack

    resolved = settings or Settings.from_env()
    configure_logging(resolved.log_level)
    wired = services or build_services(resolved)

    routes: list[Any] = [
        Route("/healthz", _healthz, methods=["GET"]),
        Route("/versions", _versions, methods=["GET"]),
        *http_routes(),
        *admin_routes(),
    ]

    app = Starlette(
        routes=routes,
        middleware=middleware_stack(resolved),
        lifespan=lifespan,
    )
    app.state.services = wired
    return app


async def load_runtime(services: Services) -> None:
    """Load the mutable settings, creating them empty on a fresh database.

    The database is the only source (D68). A fresh appliance has no roles, no
    providers and no models, and the administration page is where each is
    made; there is no file to seed from, so nothing outside the database can
    disagree with it.
    """

    async with services.database.connection() as conn:
        store = SettingsStore(conn)

        baseline = await store.get("baseline")
        if baseline is None:
            baseline = copy.deepcopy(EMPTY_BASELINE)
            await store.set("baseline", baseline)
            _log.info("baseline created empty")

        prompt = await store.get("prompt")
        if prompt is None:
            prompt = ""
            await store.set("prompt", prompt)

        # The default is written rather than left absent, so the administration
        # page reads back the name in force instead of an empty box that means
        # "unset" for a field that is never unset.
        name = await store.get("name")
        if not isinstance(name, str) or not name.strip():
            name = DEFAULT_NAME
            await store.set("name", name)

    baseline = baseline if isinstance(baseline, dict) else {}
    if services.presets["models"] or services.presets["providers"]:
        # Facts belong to the release (D76). An entry added from a preset gets
        # the facts this release ships, so a corrected window reaches it with the
        # upgrade rather than when someone thinks to re-add the model.
        refreshed, changes = refresh_facts(baseline, services.presets)
        if changes:
            async with services.database.connection() as conn:
                await SettingsStore(conn).set("baseline", refreshed)
            for change in changes:
                _log.info("catalog fact refreshed", fields={"change": change})
            baseline = refreshed

    services.baseline = baseline
    services.operator_prompt = prompt if isinstance(prompt, str) else ""
    services.agent_name = name.strip()
    # Said once at startup as well as on every write: the host may have started
    # first, or the document may predate a release that changed its shape.
    publish_runtime(services)

    if services.baseline:
        # A conflict already in the baseline is not repaired by composition:
        # `compose` would pick the alphabetically last role and the operator
        # would never see why. Every write refuses one, so finding one here
        # means the row was edited by hand; fail at startup with the account
        # named rather than serving a silent choice.
        from ..adapters.persistence import AccountStore, GrantStore
        from ..decision.policy.source import baseline_role_conflicts, conflict_detail

        async with services.database.connection() as conn:
            conflicts = await baseline_role_conflicts(
                PolicySource(
                    baseline=services.baseline,
                    overlay_dir=str(services.settings.overlay_dir),
                ),
                accounts=AccountStore(conn),
                directory=services.directory,
                grants=GrantStore(conn),
            )
        if conflicts:
            detail = "; ".join(
                f"{name}: {conflict_detail(found)}" for name, found in conflicts.items()
            )
            raise RuntimeError(f"baseline has conflicting roles: {detail}")


async def _versions(request: Any) -> Any:
    """What this appliance is, for a client that cannot get a session yet.

    Unauthenticated for the same reason `/healthz` is: the question is asked by
    `neuman-client doctor` on a machine whose session may be exactly what is
    broken, and the answer is what turns "it doesn't work" into one sentence and
    one command (D111).

    It names the product, not the deployment: no bind, no TLS, no schema, no
    counts. The build id is deliberately absent because it may be a store path,
    which says which code a turn ran under and nothing a person can install.
    """

    from starlette.responses import JSONResponse

    from .. import __version__
    from ..adapters.clients import (
        MIN_CLIENT_RELEASE,
        MIN_CLIENT_RPC_VERSION,
        SUPPORTED_RPC_VERSIONS,
    )

    return JSONResponse(
        {
            "product": "neuman",
            "release": __version__,
            "rpc_versions": sorted(SUPPORTED_RPC_VERSIONS),
            "minimum_client": {
                "release": MIN_CLIENT_RELEASE,
                "rpc": MIN_CLIENT_RPC_VERSION,
            },
        },
        headers={"Cache-Control": "no-store"},
    )


async def _healthz(request: Any) -> Any:
    """Liveness plus the two facts an operator actually wants."""

    from starlette.responses import JSONResponse

    services: Services = request.app.state.services
    healthy = True
    try:
        async with services.database.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - health must report, never raise
        healthy = False

    return JSONResponse(
        {"ok": healthy, "tools": len(services.registry)},
        status_code=200 if healthy else 503,
    )
