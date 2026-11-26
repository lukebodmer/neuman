"""A running app with every port faked, for testing the transport surfaces.

`fakes.py` implements the *ports the decision layer declares*. This file is a
different thing: it stands up the real Starlette app — real routes, real
middleware, real dependency plumbing — and replaces only what would otherwise
need a database, a connected client, or a model.

Why it exists: `composition/` is the layer the untrusted network actually
reaches, and it is the one layer with no tests. CSRF validation, Origin
checking, cookie flags, and the one-turn-per-session rule all live there and
nowhere else, so a fake that stops short of the app proves nothing about them.

The substitution is deliberately shallow. The persistence stores are thin SQL
wrappers, so faking them at the module where composition imports them leaves
every routing, auth, and concurrency decision running for real — which is the
part under test. What these tests do *not* cover, and must not be read as
covering, is the SQL itself: that needs a real Postgres and belongs with the VM
tests.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fakes import (
    FakeAccountRecord,
    FakeAccounts,
    FakeAudit,
    FakeDirectory,
    FakeEvents,
    FakeExecution,
    FakeFetch,
    FakeGateway,
    FakeGrants,
    FakeImages,
    FakeMachines,
    FakePasswords,
    FakeRegistry,
    FakeRuntime,
    FakeSessions,
    FakeUsage,
    manifest,
)

from neuman_server.adapters.clients import RPC_VERSION
from neuman_server.adapters.gpu import GpuReporter
from neuman_server.adapters.persistence import TaskRow, TaskRunRow
from neuman_server.adapters.persistence.machines import CLIENT, MachineRow
from neuman_server.adapters.registry import load_registry
from neuman_server.composition.app import Services
from neuman_server.decision.identity import tokens
from neuman_server.foundation import Settings
from neuman_server.foundation.state import load_presets

__all__ = ["Surface", "build_surface"]

ORIGIN = "https://neuman.test"
BASE_URL = ORIGIN

# The password every seeded account authenticates with.
PASSWORD = "correct horse battery staple"


# The catalog every surface test resolves against.
#
# Declared rather than patched: models live in the baseline now, so a test that
# stubbed a store would be driving a path the appliance does not have. Spliced
# into inline baselines with `**CATALOG` so a test writing its own policy does
# not also have to restate a provider.
#
# `provider_model` differs visibly from the local name on purpose. A fake that
# echoed the name back would let a test pass while the translation was skipped,
# which is exactly how N48 survived: the harness sent the local name, the
# provider answered "deepseek is not a valid model ID", and nothing caught it.
CATALOG = {
    "providers": {"test-provider": {"url": "https://cloud.test/v1", "api_key_file": None}},
    "models": {
        "m": {
            "provider": "test-provider",
            "provider_model": "provider/m",
            "context_tokens": 100000,
        }
    },
}


# -- agent sessions ---------------------------------------------------------


@dataclass
class FakeAgentSessionRow:
    id: str
    account_id: str
    title: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None
    removed_at: datetime | None = None
    # Mirrors the real row. A fake missing a field the production code reads is
    # the N38/N48 drift, and it fails as an AttributeError twelve tests deep
    # rather than where the field was added.
    # When the owner last had it in front of them. The watermark a reply is
    # compared against, and the clock that keeps a session in the working set.
    read_at: datetime | None = None
    # The newest reply's timestamp, standing in for the `response_final` the
    # real working-set query finds in the event log. A test sets it to make a
    # session unread; no route sets or reads it directly.
    replied_at: datetime | None = None
    # The listing's derived columns. The settings live on the branch now; the
    # session keeps only what the rail row shows and what names the conversation.
    last_activity: datetime | None = None
    pinned_at: datetime | None = None
    branch_count: int = 1
    open_branch_id: str | None = None
    # The root branch, for a test that needs to open a socket or read a log.
    branch_id: str | None = None
    # Derived by `active_for_account`, like the real query's EXISTS.
    unread: bool = False
    # Set when the session is a task run's agent step: the task's, never chat's.
    task_run_id: str | None = None


@dataclass
class FakeBranchRow:
    id: str
    session_id: str
    forked_from: str | None = None
    forked_from_seq: int | None = None
    inherited_event_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Every branch names a machine (D112). One is enough for a fake: tests that
    # care which label reaches the dispatch read it back from `FakeExecution`.
    machine: str = "ernest"
    working_dir: str | None = None
    attachments: tuple[str, ...] = ()
    tool_selection: tuple[str, ...] | None = None
    model: str | None = None
    reasoning: str | None = None
    approval: str | None = None
    first_message: str | None = None


class FakeBranches:
    """Stands in for BranchStore, over the dict the session fake owns."""

    def __init__(self, sessions: FakeAgentSessions) -> None:
        self.sessions = sessions

    def transaction(self):
        """An async no-op: the fake has no statements to roll back."""

        class _Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Transaction()

    @property
    def rows(self) -> dict[str, FakeBranchRow]:
        return self.sessions.branches

    async def get(self, branch_id: str):
        return self.rows.get(branch_id)

    def _scoped(self, branch_id: str, account_id: str):
        row = self.rows.get(branch_id)
        if row is None:
            return None
        session = self.sessions.rows.get(row.session_id)
        if session is None or session.account_id != account_id:
            return None
        return row

    async def owned_by(self, branch_id: str, account_id: str):
        return self._scoped(branch_id, account_id)

    async def visible_to(self, branch_id: str, account_id: str):
        row = self._scoped(branch_id, account_id)
        if row is None:
            return None
        session = self.sessions.rows.get(row.session_id)
        if session is None or session.removed_at is not None or session.task_run_id:
            return None
        return row

    async def resolve_prefix(self, prefix: str):
        if not prefix or len(prefix) > 32 or not all(c in "0123456789abcdef" for c in prefix):
            return []
        return [row for row in self.rows.values() if row.id.startswith(prefix)][:10]

    async def list_for_session(self, session_id: str):
        rows = [row for row in self.rows.values() if row.session_id == session_id]
        rows.sort(key=lambda row: (row.created_at, row.id))
        return rows

    async def open_for_session(self, session_id: str):
        rows = await self.list_for_session(session_id)
        return rows[-1] if rows else None

    async def create(self, *, session_id: str, **fields: Any):
        row = FakeBranchRow(id=uuid.uuid4().hex, session_id=session_id, **fields)
        self.rows[row.id] = row
        return row

    async def _set(self, branch_id: str, **fields: Any) -> bool:
        row = self.rows.get(branch_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    async def set_inherited(self, branch_id: str, inherited: int) -> bool:
        return await self._set(branch_id, inherited_event_count=inherited)

    async def set_machine(self, branch_id: str, machine: str) -> bool:
        return await self._set(branch_id, machine=machine)

    async def set_working_dir(self, branch_id: str, working_dir: str | None) -> bool:
        return await self._set(branch_id, working_dir=working_dir)

    async def set_attachments(self, branch_id: str, paths: Sequence[str]) -> bool:
        return await self._set(branch_id, attachments=tuple(paths))

    async def attached_paths(self, account_id: str) -> list[str]:
        paths: set[str] = set()
        for row in self.rows.values():
            session = self.sessions.rows.get(row.session_id)
            if session is None or session.account_id != account_id:
                continue
            if session.removed_at is not None:
                continue
            paths.update(row.attachments)
        return sorted(paths)

    async def set_tool_selection(self, branch_id: str, tools: Sequence[str] | None) -> bool:
        return await self._set(
            branch_id, tool_selection=None if tools is None else tuple(tools)
        )

    async def set_model_reasoning(
        self, branch_id: str, *, model: str | None, reasoning: str | None
    ) -> bool:
        return await self._set(branch_id, model=model, reasoning=reasoning)

    async def set_approval(self, branch_id: str, approval: str | None) -> bool:
        return await self._set(branch_id, approval=approval)


class FakeAgentSessions:
    """Stands in for AgentSessionStore.

    `owned_by` returns None for a session this account does not own — the same
    shape as the real query, where ownership is a scope rather than a check, so
    a route that forgets to compare account ids still cannot leak one.
    """

    def __init__(self) -> None:
        self.rows: dict[str, FakeAgentSessionRow] = {}
        # One transcript per session, keyed by branch id. The real store has a
        # table for these; the fake keeps the same separation so a route that
        # confuses a session with a branch fails here rather than in production.
        self.branches: dict[str, FakeBranchRow] = {}
        # What was *said* in each session, keyed by id: the text the real search
        # index holds. Seeded by a test rather than derived from appended
        # events, because these tests fake the event store too — so there is no
        # log here to derive it from.
        self.said: dict[str, str] = {}

    def seed(self, *, account_id: str, session_id: str | None = None) -> FakeAgentSessionRow:
        row = FakeAgentSessionRow(id=session_id or uuid.uuid4().hex, account_id=account_id)
        self.rows[row.id] = row
        # The root branch shares the session's id in the fake, because the
        # socket is opened for a branch and most tests are written in terms of
        # a session. Production gives the two different ids.
        branch = FakeBranchRow(id=row.id, session_id=row.id)
        self.branches[branch.id] = branch
        row.open_branch_id = branch.id
        row.branch_id = branch.id
        return row

    async def create(
        self,
        *,
        account_id: str,
        title: str | None = None,
        machine: str = "ernest",
        task_run_id: str | None = None,
    ):
        row = FakeAgentSessionRow(
            id=uuid.uuid4().hex, account_id=account_id, title=title, task_run_id=task_run_id
        )
        self.rows[row.id] = row
        branch = FakeBranchRow(id=uuid.uuid4().hex, session_id=row.id, machine=machine)
        self.branches[branch.id] = branch
        row.open_branch_id = branch.id
        row.branch_id = branch.id
        return row, branch

    async def get(self, session_id: str):
        return self.rows.get(session_id)

    async def owned_by(self, session_id: str, account_id: str):
        row = self.rows.get(session_id)
        return row if row is not None and row.account_id == account_id else None

    async def resolve_prefix(self, prefix: str):
        """Every match, newest first, the way the real `LIKE` query returns them.

        All matches rather than the first, because an ambiguous prefix has to
        stay ambiguous here: the route is expected to refuse it, and a fake that
        collapsed two sessions into one would make that refusal untestable.
        """

        if not prefix or len(prefix) > 32 or not all(c in "0123456789abcdef" for c in prefix):
            return []
        hits = [row for row in self.rows.values() if row.id.startswith(prefix)]
        hits.sort(key=lambda row: row.created_at, reverse=True)
        return hits[:10]

    async def visible_to(self, session_id: str, account_id: str):
        """Ownership *and* not removed, exactly as the real query scopes it.

        A fake that ignored `removed_at` here would make the routes using it
        indistinguishable from the ones using `owned_by`, which is the whole
        distinction being tested.
        """

        row = await self.owned_by(session_id, account_id)
        if row is None or row.removed_at is not None or row.task_run_id:
            return None
        return row

    async def mark_read(self, session_id: str, account_id: str) -> bool:
        """The same scope as the real `UPDATE`: this account's row, removed or not.

        Removal ends the viewing, not the record, so marking a removed session
        read is a successful no-op — which is what the real statement's `WHERE`
        does too.
        """

        row = await self.owned_by(session_id, account_id)
        if row is None:
            return False
        row.read_at = datetime.now(UTC)
        return True

    async def active_for_account(self, account_id: str, *, cutoff: datetime):
        """Modelled on the real predicate: `read_at` or activity inside the window.

        `unread` is derived from the newest reply a test seeded (`replied_at`)
        rather than from a flag, because deriving it is what the real query
        does: a fake with a settable `unread` would let the route pass with the
        watermark logic gone. The reply's timestamp stands in for the
        `response_final` row the real query finds in the event log.
        """

        rows = []
        for row in self.rows.values():
            if row.account_id != account_id or row.removed_at is not None or row.task_run_id:
                continue
            if row.last_activity is None:
                row.last_activity = row.created_at
            activity = max(row.last_activity, row.read_at or row.created_at)
            if activity < cutoff:
                continue
            row.unread = row.replied_at is not None and (
                row.read_at is None or row.replied_at > row.read_at
            )
            rows.append(row)
        rows.sort(key=lambda row: (row.last_activity, row.id), reverse=True)
        return rows

    async def counts_by_account(self, account_ids: Sequence[str]) -> dict[str, int]:
        """Visible sessions per account, the scope `list_for_account` uses.

        Removed rows are excluded for the same reason the list excludes them:
        a roster that counted a session its owner had removed would disagree
        with the list the owner sees.
        """

        wanted = set(account_ids)
        counts: dict[str, int] = {}
        for row in self.rows.values():
            if row.account_id in wanted and row.removed_at is None and not row.task_run_id:
                counts[row.account_id] = counts.get(row.account_id, 0) + 1
        return counts

    async def list_for_account(self, account_id: str, *, sort: str, limit: int = 50):
        """Scoped, ordered and limited the way the real statement is.

        The order and the limit are modelled rather than ignored because the
        interesting behaviour is their *interaction* with the removal filter:
        excluding removed rows before `LIMIT` is what lets a removal reveal the
        session behind the cap, and a fake that returned everything would make
        that indistinguishable from filtering afterwards.

        `sort` is required, like the real store's: a fake that defaulted would
        hide a caller that never said which order it wanted, which is the one
        thing the real signature is shaped to prevent.
        """

        if sort not in ("used", "created"):
            raise ValueError(f"unknown session sort: {sort!r}")
        mine = [
            row
            for row in self.rows.values()
            if row.account_id == account_id and row.removed_at is None and not row.task_run_id
        ]
        # The listing's derived column, computed the way the SQL computes it: a
        # session with no events was last active when it was created. Filled
        # here rather than in `seed` so a test can state a different answer.
        for row in mine:
            if row.last_activity is None:
                row.last_activity = row.created_at
        # Pinned first in both orders, as `_order_by` puts them; then the chosen
        # key, with `created_at` breaking ties the way the SQL's last keys do.
        mine.sort(
            key=lambda row: (
                row.pinned_at is not None,
                (row.last_activity or row.created_at) if sort == "used" else row.created_at,
                row.created_at,
            ),
            reverse=True,
        )
        return mine[:limit]

    async def search_for_account(
        self, account_id: str, query: str, *, sort: str, limit: int = 50
    ):
        """Full-text-ish matching over `said`, which a test seeds per session.

        Postgres does the real matching; this models the *contract* the route
        depends on — scoped to the account, removed rows excluded, ordered the
        way the list is, each hit carrying a snippet with `<<`/`>>` around the
        term. Deliberately word-prefix rather than substring, because that is
        what `tsquery` does: a fake that matched mid-word would let a test pass
        for a query the real index returns nothing for.
        """

        import re

        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        hits = []
        for row in await self.list_for_account(account_id, sort=sort, limit=limit):
            body = self.said.get(row.id, "")
            words = re.split(r"(\W+)", body)
            matched = terms and all(any(w.lower().startswith(t) for w in words) for t in terms)
            title_hit = query.lower() in (row.title or "").lower()
            if not matched and not title_hit:
                continue
            snippet = ""
            if matched:
                snippet = "".join(
                    f"<<{w}>>"
                    if any(w.lower().startswith(t) for t in terms) and w.strip()
                    else w
                    for w in words
                )
            hits.append((row, snippet))
        return hits

    async def reindex_search(self) -> int:
        return len(self.said)

    async def list_all(self, *, limit: int = 50):
        """Unscoped, like the real statement, and still excluding removed rows.

        Modelled rather than returning `self.rows.values()` because the removal
        filter is the one thing that distinguishes this from "everything", and a
        fake that resurrected a removed session would make the admin view look
        like a bug to whoever tidied up.
        """

        live = [row for row in self.rows.values() if row.removed_at is None]
        live.sort(key=lambda row: row.created_at, reverse=True)
        return live[:limit]

    async def title_if_unnamed(self, session_id: str, account_id: str, title: str) -> bool:
        """The real store does this in one statement, and so must this.

        A read-then-write here would let a test pass while the production query
        raced — the point of the `title IS NULL` condition is that it is *in* the
        UPDATE, and a fake that checks then assigns is a different mechanism
        wearing the same name.
        """

        row = await self.owned_by(session_id, account_id)
        if row is None or row.title is not None:
            return False
        row.title = title
        return True

    async def set_title(self, session_id: str, account_id: str, title: str) -> bool:
        row = await self.owned_by(session_id, account_id)
        if row is None:
            return False
        row.title = title
        return True

    async def close(self, session_id: str, account_id: str) -> bool:
        row = await self.owned_by(session_id, account_id)
        if row is None:
            return False
        row.closed_at = datetime.now(UTC)
        return True

    async def mark_removed(self, session_id: str, account_id: str) -> bool:
        row = await self.owned_by(session_id, account_id)
        # `removed_at is None` mirrors the real statement's guard: a second
        # removal returns False without moving the timestamp, which is what the
        # route relies on to answer a repeated click with 200 rather than 404.
        if row is None or row.removed_at is not None:
            return False
        row.removed_at = datetime.now(UTC)
        return True

    async def set_pinned(self, session_id: str, account_id: str, pinned: bool) -> bool:
        """Pin or unpin, with the real statements' guards.

        Each direction only writes when the row is in the other state, so a
        repeated pin keeps the moment it was first pinned — the property the
        timestamp exists to make true, and one a fake that assigned
        unconditionally would quietly stop testing.
        """

        row = await self.owned_by(session_id, account_id)
        if row is None:
            return False
        if pinned:
            if row.pinned_at is not None:
                return False
            row.pinned_at = datetime.now(UTC)
            return True
        if row.pinned_at is None:
            return False
        row.pinned_at = None
        return True


# -- database ---------------------------------------------------------------


class FakeDatabase:
    """A pool that hands out a connection nothing actually uses.

    The stores are patched out, so the connection only has to exist and satisfy
    the health check's `SELECT 1`.
    """

    def __init__(self) -> None:
        self.healthy = True

    @asynccontextmanager
    async def connection(self):
        if not self.healthy:
            raise RuntimeError("database unavailable")
        yield _FakeConnection()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeSettings:
    """Stands in for SettingsStore: the `settings` rows, held per surface.

    `replace_if` compares, as the real statement does. A fake that always
    reported success would leave every compare-and-set test proving nothing
    about the 409.
    """

    values: dict[str, Any]

    def __init__(self, conn: Any = None) -> None:
        self._conn = conn

    async def get(self, key: str) -> Any | None:
        return copy.deepcopy(self.values.get(key))

    async def set(self, key: str, value: Any) -> None:
        self.values[key] = copy.deepcopy(value)

    async def replace_if(self, key: str, *, expected: Any, value: Any) -> bool:
        if key not in self.values or self.values[key] != expected:
            return False
        self.values[key] = copy.deepcopy(value)
        return True


class _FakeConnection:
    """Answers the two things lifespan asks before the stores take over.

    The migration runner reads `schema_migrations` to decide what is pending, so
    this reports every bundled migration as already applied — the alternative is
    stubbing `transaction()` and letting the migration runner "apply" DDL into a
    void,
    which would make a broken migration look fine. Whether the SQL is correct is
    a question for a real Postgres, and these tests do not pretend to answer it.
    """

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        # What a write reports: one row touched. A compare-and-set against this
        # fake always succeeds, which is honest — there is one row and nothing
        # else writes it here. The refusal is tested where the operations live.
        self.rowcount = 1

    @asynccontextmanager
    async def transaction(self):
        # The rename needs the baseline write and the grant move in one
        # transaction. The fake reports every migration as already applied, so
        # the migration runner never reaches this and cannot "apply" DDL into a
        # void; the stores patched into the surface do not use it.
        yield self

    async def execute(self, query: str = "", params: Any = None, *args: Any, **kwargs: Any):
        from neuman_server.foundation.db.migrate import migrations

        text = " ".join(query.split()).lower()
        if "max(version)" in text:
            applied = migrations()
            top = applied[-1].version if applied else 0
            self._rows = [{"version": top}]
        elif "select version, checksum from schema_migrations" in text:
            self._rows = [{"version": m.version, "checksum": m.checksum} for m in migrations()]
        else:
            self._rows = []
        return self

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


# -- the surface ------------------------------------------------------------


class FakeTaskStore:
    """The task rows, in memory. The SQL is the appliance VM test's job."""

    def __init__(self) -> None:
        self.rows: dict[str, TaskRow] = {}

    async def create(
        self,
        *,
        account_id: str,
        name: str,
        workspace: str,
        run_root: str,
        machine: str,
        inputs: Sequence[dict[str, Any]],
        steps: Sequence[dict[str, Any]],
        schedule: dict[str, Any],
        outputs: Sequence[str],
        enabled: bool,
        next_run_at: datetime | None,
    ) -> TaskRow:
        now = datetime.now(UTC)
        row = TaskRow(
            id=uuid.uuid4().hex,
            account_id=account_id,
            name=name,
            workspace=workspace,
            run_root=run_root,
            machine=machine,
            inputs=[dict(item) for item in inputs],
            steps=[dict(item) for item in steps],
            schedule=dict(schedule),
            outputs=list(outputs),
            enabled=enabled,
            created_at=now,
            updated_at=now,
            next_run_at=next_run_at,
            last_run_at=None,
        )
        self.rows[row.id] = row
        return row

    async def get(self, task_id: str) -> TaskRow | None:
        return self.rows.get(task_id)

    async def get_for_account(self, task_id: str, account_id: str) -> TaskRow | None:
        row = self.rows.get(task_id)
        return row if row is not None and row.account_id == account_id else None

    async def list_for_account(self, account_id: str) -> Sequence[TaskRow]:
        return sorted(
            (row for row in self.rows.values() if row.account_id == account_id),
            key=lambda row: row.created_at,
            reverse=True,
        )

    async def update(
        self,
        task_id: str,
        *,
        name: str,
        workspace: str,
        run_root: str,
        machine: str,
        inputs: Sequence[dict[str, Any]],
        steps: Sequence[dict[str, Any]],
        schedule: dict[str, Any],
        outputs: Sequence[str],
        enabled: bool,
        next_run_at: datetime | None,
    ) -> TaskRow | None:
        row = self.rows.get(task_id)
        if row is None:
            return None
        updated = replace(
            row,
            name=name,
            workspace=workspace,
            run_root=run_root,
            machine=machine,
            inputs=[dict(item) for item in inputs],
            steps=[dict(item) for item in steps],
            schedule=dict(schedule),
            outputs=list(outputs),
            enabled=enabled,
            next_run_at=next_run_at,
            updated_at=datetime.now(UTC),
        )
        self.rows[task_id] = updated
        return updated

    async def set_enabled(
        self, task_id: str, *, enabled: bool, next_run_at: datetime | None
    ) -> TaskRow | None:
        row = self.rows.get(task_id)
        if row is None:
            return None
        updated = replace(
            row,
            enabled=enabled,
            next_run_at=next_run_at,
            updated_at=datetime.now(UTC),
        )
        self.rows[task_id] = updated
        return updated

    async def delete(self, task_id: str, account_id: str) -> bool:
        row = self.rows.get(task_id)
        if row is None or row.account_id != account_id:
            return False
        del self.rows[task_id]
        return True

    async def due(self, now: datetime, *, limit: int = 10) -> Sequence[TaskRow]:
        rows = [
            row
            for row in self.rows.values()
            if row.enabled and row.next_run_at is not None and row.next_run_at <= now
        ]
        return sorted(rows, key=lambda row: row.next_run_at)[:limit]

    async def advance(
        self, task_id: str, *, next_run_at: datetime | None, last_run_at: datetime
    ) -> None:
        row = self.rows.get(task_id)
        if row is not None:
            self.rows[task_id] = replace(
                row,
                next_run_at=next_run_at,
                last_run_at=last_run_at,
                updated_at=datetime.now(UTC),
            )


class FakeTaskRunStore:
    """Runs and the queue, in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, TaskRunRow] = {}

    async def create(
        self,
        *,
        task_id: str,
        account_id: str,
        trigger: str,
        due_at: datetime,
        inputs: dict[str, str],
        steps: Sequence[dict[str, Any]],
        status: str = "due",
        error: str | None = None,
    ) -> TaskRunRow:
        row = TaskRunRow(
            id=uuid.uuid4().hex,
            task_id=task_id,
            account_id=account_id,
            trigger=trigger,
            status=status,
            inputs=dict(inputs),
            run_dir=None,
            steps=[dict(item) for item in steps],
            outputs_present=None,
            due_at=due_at,
            started_at=None,
            finished_at=None,
            error=error,
            cost_usd=None,
            tokens=None,
            created_at=datetime.now(UTC),
        )
        self.rows[row.id] = row
        return row

    async def get(self, run_id: str) -> TaskRunRow | None:
        return self.rows.get(run_id)

    async def list_for_task(self, task_id: str, *, limit: int = 20) -> Sequence[TaskRunRow]:
        rows = sorted(
            (row for row in self.rows.values() if row.task_id == task_id),
            key=lambda row: row.created_at,
            reverse=True,
        )
        return rows[:limit]

    async def since_for_account(
        self, account_id: str, moment: datetime
    ) -> Sequence[TaskRunRow]:
        rows = (
            row
            for row in self.rows.values()
            if row.account_id == account_id and row.created_at >= moment
        )
        return sorted(rows, key=lambda row: row.created_at, reverse=True)

    async def latest_for_task(self, task_id: str) -> TaskRunRow | None:
        rows = await self.list_for_task(task_id, limit=1)
        return rows[0] if rows else None

    async def pending(self, now: datetime, *, limit: int = 10) -> Sequence[TaskRunRow]:
        rows = [
            row
            for row in self.rows.values()
            if row.status in ("due", "waiting") and row.due_at is not None and row.due_at <= now
        ]
        return sorted(rows, key=lambda row: row.due_at)[:limit]

    async def claim(self, run_id: str, *, run_dir: str) -> TaskRunRow | None:
        row = self.rows.get(run_id)
        if row is None or row.status not in ("due", "waiting"):
            return None
        updated = replace(
            row, status="running", started_at=datetime.now(UTC), run_dir=run_dir, error=None
        )
        self.rows[run_id] = updated
        return updated

    async def set_steps(self, run_id: str, steps: Sequence[dict[str, Any]]) -> None:
        row = self.rows.get(run_id)
        if row is not None:
            self.rows[run_id] = replace(row, steps=[dict(item) for item in steps])

    async def wait(self, run_id: str, *, error: str | None = None) -> None:
        row = self.rows.get(run_id)
        if row is not None:
            self.rows[run_id] = replace(row, status="waiting", error=error)

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        steps: Sequence[dict[str, Any]] | None = None,
        outputs_present: Sequence[str] | None = None,
        cost_usd: float | None = None,
        tokens: int | None = None,
    ) -> None:
        row = self.rows.get(run_id)
        if row is not None:
            self.rows[run_id] = replace(
                row,
                status=status,
                finished_at=datetime.now(UTC),
                error=error,
                steps=[dict(item) for item in steps] if steps is not None else row.steps,
                outputs_present=list(outputs_present) if outputs_present is not None else None,
                cost_usd=cost_usd,
                tokens=tokens,
            )

    async def cancel_pending(self, task_id: str, *, reason: str) -> int:
        count = 0
        for run_id, row in list(self.rows.items()):
            if row.task_id == task_id and row.status in ("due", "waiting"):
                self.rows[run_id] = replace(
                    row,
                    status="cancelled",
                    finished_at=datetime.now(UTC),
                    error=reason,
                )
                count += 1
        return count

    async def reconcile_running(self, *, error: str) -> int:
        count = 0
        for run_id, row in list(self.rows.items()):
            if row.status == "running":
                self.rows[run_id] = replace(
                    row,
                    status="failed",
                    finished_at=datetime.now(UTC),
                    error=error,
                )
                count += 1
        return count


@dataclass
class Surface:
    """An app plus the fakes behind it, so a test can assert on both."""

    app: Any
    services: Services
    accounts: FakeAccounts
    sessions: FakeSessions
    agent_sessions: FakeAgentSessions
    events: FakeEvents
    images: FakeImages
    machines: FakeMachines
    audit: FakeAudit
    grants: FakeGrants
    usage: FakeUsage
    directory: FakeDirectory
    gateway: FakeGateway
    settings: Any
    tasks: FakeTaskStore
    task_runs: FakeTaskRunStore

    def store_baseline(self) -> None:
        """Make the stored row match `services.baseline` after a test edited it.

        The two are one fact in production: every write stores the row and then
        swaps the dict. A test that edits only the dict has built a server whose
        memory disagrees with its database, and the next compare-and-set
        correctly refuses to write over a row it did not read.
        """

        self.settings.values["baseline"] = copy.deepcopy(self.services.baseline)

    def login(self, name: str = "alice") -> tuple[str, str]:
        """Mint a session directly, returning ``(cookie_token, csrf_token)``.

        Deliberately bypasses `/api/login`: most tests are about what an
        *authenticated* request may do, and going through the login route would
        couple them to password verification they are not testing. The login
        route has its own tests.
        """

        record = self.accounts.rows.get(name)
        assert record is not None, f"no seeded account {name!r}"
        token = tokens.new_secret()
        csrf = tokens.new_secret()
        self.sessions.rows[tokens.hash_token(token)] = type(
            "Row",
            (),
            {
                "account_id": record.id,
                "csrf_token": csrf,
                "expires_at": tokens.expiry_from(),
                "last_seen_at": datetime.now(UTC),
            },
        )()
        return token, csrf

    def client(self, **kwargs: Any):
        from starlette.testclient import TestClient

        # An https base URL, or the Secure cookie is silently never sent
        # .
        return TestClient(self.app, base_url=BASE_URL, **kwargs)


def _store_importers() -> list[str]:
    """Every module under `composition` that might import a store by name."""

    import pkgutil

    import neuman_server.composition as package

    return [
        info.name
        for info in pkgutil.walk_packages(package.__path__, prefix="neuman_server.composition.")
    ]


def build_surface(
    monkeypatch,
    tmp_path,
    *,
    baseline: dict[str, Any] | None = None,
    runtime: Any = None,
) -> Surface:
    """Stand up the real app with every outside dependency faked.

    `baseline` overrides the default grant for a test whose subject *is* the
    policy — the default reaches `/srv/eng`, which expands to itself, so a test of
    home-directory expansion built on it would pass whatever the code did.
    """

    resolved = ("alice", "bob", "carol")

    # `carol` holds admin authority via `neuman-admin`; alice and bob do not. Three
    # accounts rather than two because "alice succeeds and an anonymous request
    # fails" is equally consistent with the operator check doing nothing — the case
    # that matters is an *authenticated* account without authority (N32).
    account_store = FakeAccounts()
    account_store.groups["eng"] = set(resolved)
    account_store.groups["neuman-admin"] = {"carol"}
    directory = FakeDirectory(store=account_store)
    session_store = FakeSessions()
    agent_sessions = FakeAgentSessions()
    branch_store = FakeBranches(agent_sessions)
    task_store = FakeTaskStore()
    task_run_store = FakeTaskRunStore()
    events = FakeEvents()
    images = FakeImages()
    machines = FakeMachines()
    audit = FakeAudit()
    grants = FakeGrants()
    usage = FakeUsage()
    gateway = FakeGateway([])
    settings_store = type("SettingsStore", (FakeSettings,), {"values": {}})

    for name in resolved:
        account_store.rows[name] = FakeAccountRecord(
            id=f"acct-{name}",
            name=name,
            # The fake hasher's prefix, so `/api/login` works with PASSWORD.
            password_hash=f"{FakePasswords.PREFIX}{PASSWORD}",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
        )
        # A machine each account has connected from. A session names one and the
        # route refuses a label that has never connected (D112), so a surface
        # with no machines would refuse every `POST /api/sessions` — which is
        # the check working, and something every test would have to arrange.
        machines.rows[(f"acct-{name}", "ernest")] = MachineRow(
            account_id=f"acct-{name}",
            label="ernest",
            kind=CLIENT,
            notes="",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            home="/home/alice",
            drops="/home/alice/.local/state/neuman/drops",
            tools=(),
            release=None,
            rpc=RPC_VERSION,
        )

    # Patch the stores where composition imports them. Each module does
    # `from ...adapters.persistence import XStore`, so the name must be replaced
    # in the importing module's namespace, not in the persistence package.
    #
    # Discovered rather than listed. This was a hand-kept list of module names
    # and it drifted twice: `/admin/sessions/{id}` resolved against the real
    # store and a fake connection until it was added, and `/client` was never
    # added at all, so the handshake could not resolve a session and every
    # refusal it makes was untestable. A store bound against a fake connection
    # returns nothing rather than failing, so the symptom is a test that passes
    # against an empty answer.
    replacements = {
        "AccountStore": lambda conn: account_store,
        "SessionStore": lambda conn: session_store,
        "AgentSessionStore": lambda conn: agent_sessions,
        "BranchStore": lambda conn: branch_store,
        "EventStore": lambda conn: events,
        "ImageStore": lambda conn: images,
        "MachineStore": lambda conn: machines,
        "AuditStore": lambda conn: audit,
        "GrantStore": lambda conn: grants,
        "UsageStore": lambda conn: usage,
        "TaskStore": lambda conn: task_store,
        "TaskRunStore": lambda conn: task_run_store,
        # A class rather than a factory, so a test can patch one of its methods
        # the way it would patch the real store's.
        "SettingsStore": settings_store,
    }
    for target in _store_importers():
        module = __import__(target, fromlist=["_"])
        for attribute, factory in replacements.items():
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, factory, raising=False)

    # A baseline that actually grants something. Without one the composed policy
    # is empty, every tool is denied at the gate, and a test that meant to
    # exercise approval would silently exercise denial instead — which is how a
    # test passes while proving the opposite of its name.
    #
    # The document is the database row the server reads at startup, which is the
    # only place a baseline comes from (D68).
    if baseline is None:
        baseline = {
            "schema_version": 2,
            "defaults": {
                "tools": ["fs_read", "fs_write"],
                "files_read": ["/srv/eng"],
                "files_write": ["/srv/eng/work"],
                "auto_accept": "safe",
                # Granted here so a turn can select it. The catalog below is
                # what the name resolves *to*; this is who may pick it, and the
                # two are deliberately separate.
                "models": ["m"],
            },
            "roles": {},
            "users": [{"name": name} for name in resolved],
            **CATALOG,
        }
    settings_store.values["baseline"] = copy.deepcopy(baseline)

    settings = Settings(
        allowed_origins=(ORIGIN,),
        state_dir=tmp_path / "state",
        audit_mirror_path=tmp_path / "audit.jsonl",
        runtime_config=tmp_path / "state" / "runtime.json",
        gpu_sample_path=tmp_path / "no-gpu.json",
        # Empty: a test of a preset's weights writes the files it means.
        models_dir=tmp_path / "models",
    )

    services = Services(
        settings=settings,
        database=FakeDatabase(),
        registry=FakeRegistry(
            [
                manifest("fs_read", needs={"read"}, mutating=False, critical=False),
                manifest("fs_write", needs={"write"}, mutating=True, critical=False),
                # What `/api/browse` dispatches. Present here even though the
                # baseline does not grant it: the chooser is a human navigating
                # their own directories, not a tool the model chose to call, so
                # the route deliberately does not consult the gate.
                manifest("fs_list", needs={"read"}, mutating=False, critical=False),
                # What `/api/environment` dispatches, and what the harness asks
                # before building a prompt. Ungranted on purpose and in the real
                # catalog too: the model has `python(action="describe")` for this
                # question, and the two callers here cannot make a tool call.
                manifest("env_probe", needs={"read"}, mutating=False, critical=False),
                # A diagnostic, ungranted like every real one: `/api/tools`
                # leaves it out of the chooser rather than listing it struck
                # through, and a fake without one makes that untestable.
                manifest("hog", needs={"read"}, diagnostic=True),
            ],
            catalog=load_registry(Path(__file__).resolve().parents[2] / "tools"),
        ),
        directory=directory,
        # Every seeded account has the same password; a test that wants a
        # failure supplies a different one.
        passwords=FakePasswords(),
        execution=FakeExecution(),
        gateway=gateway,
        # The real reporter against a test path, not a fake: it is one file read.
        gpu=GpuReporter(sample_path=tmp_path / "no-gpu.json"),
        runtime=runtime if runtime is not None else FakeRuntime(),
        fetch=FakeFetch(),
        # The shipped files, not a fixture: a test of the preset routes against a
        # copy of the presets would prove only that the copy agrees with itself.
        presets=load_presets(),
    )

    # The background jobs are periodic loops over the database; they are not
    # what these tests are about, and leaving them running would have every test
    # racing a retention sweep against a fake connection. `jobs/` gets its own
    # tests when there is a real database to run them against.
    import neuman_server.composition.jobs as jobs_module

    async def _no_jobs(_services: Any) -> list[Any]:
        return []

    async def _no_stop(_tasks: Any) -> None:
        return None

    monkeypatch.setattr(jobs_module, "start_jobs", _no_jobs, raising=False)
    monkeypatch.setattr(jobs_module, "stop_jobs", _no_stop, raising=False)

    from neuman_server.composition.app import create_app

    app = create_app(settings=settings, services=services)

    return Surface(
        app=app,
        services=services,
        accounts=account_store,
        sessions=session_store,
        agent_sessions=agent_sessions,
        events=events,
        images=images,
        machines=machines,
        audit=audit,
        grants=grants,
        usage=usage,
        directory=directory,
        gateway=gateway,
        settings=settings_store,
        tasks=task_store,
        task_runs=task_run_store,
    )
