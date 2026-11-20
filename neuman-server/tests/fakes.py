"""In-memory implementations of every port.

The layering exists so this file is possible: the decision layer declares
Protocols, and these satisfy them without a database, a socket, or a model.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from neuman_server.decision.harness.ports import (
    Chunk,
    DispatchOutcome,
    ModelTarget,
    Reservation,
    StoredImage,
    Swept,
)
from neuman_server.decision.ops.sessions import BranchSummary, EventWindow

__all__ = [
    "FakeAccounts",
    "FakeAudit",
    "FakeCatalog",
    "FakeDirectory",
    "FakeEvents",
    "FakeExecution",
    "FakeGateway",
    "FakeGenerations",
    "FakeGrants",
    "FakeImages",
    "FakePasswords",
    "FakeRegistry",
    "FakeSessions",
    "FakeUsage",
    "manifest",
]


# -- identity ---------------------------------------------------------------


class FakeDirectory:
    """Implements IdentityDirectory.

    Given an `accounts` store it answers through it, so a disabled account loses
    its groups the way the real store does. The plain `groups` map is for tests
    that only exercise the policy layer.
    """

    def __init__(
        self,
        groups: dict[str, set[str]] | None = None,
        store: FakeAccounts | None = None,
    ) -> None:
        self.groups = groups or {}
        self.store = store

    async def groups_of(self, name: str) -> frozenset[str]:
        if self.store is not None:
            return await self.store.groups_of(name)
        return frozenset(self.groups.get(name, set()))


def _scattered(needle: str, text: str) -> bool:
    """`needle`'s letters in order within `text`, as the real store's `LIKE` asks."""

    rest = iter(text)
    return all(letter in rest for letter in needle)


@dataclass
class FakeAccountRecord:
    id: str
    name: str
    password_hash: str | None
    must_change_password: bool = False
    disabled_at: datetime | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    palette: str = "dark"
    collapse_reasoning: bool = False
    active_minutes: int = 30
    instructions: str = ""
    default_model: str | None = None
    default_reasoning: str | None = None

    @property
    def disabled(self) -> bool:
        return self.disabled_at is not None


class FakeAccounts:
    """Implements AccountRepository and the group/preference calls routes use."""

    def __init__(self) -> None:
        self.rows: dict[str, FakeAccountRecord] = {}
        # group name -> member names
        self.groups: dict[str, set[str]] = {"neuman-admin": set()}
        self._ids = itertools.count(1)

    async def by_name(self, name: str) -> FakeAccountRecord | None:
        return self.rows.get(name.lower())

    async def names_for(self, account_ids: Sequence[str]) -> dict[str, str]:
        return {row.id: row.name for row in self.rows.values() if row.id in account_ids}

    async def by_id(self, account_id: str) -> FakeAccountRecord | None:
        for row in self.rows.values():
            if row.id == account_id:
                return row
        return None

    async def create(
        self, *, name: str, password_hash: str, must_change_password: bool = True
    ) -> FakeAccountRecord | None:
        key = name.lower()
        if key in self.rows:
            return None
        now = datetime.now(UTC)
        row = FakeAccountRecord(
            id=f"acct{next(self._ids)}",
            name=key,
            password_hash=password_hash,
            must_change_password=must_change_password,
            first_seen=now,
            last_seen=now,
        )
        self.rows[key] = row
        return row

    async def set_password(
        self, *, name: str, password_hash: str, must_change_password: bool
    ) -> FakeAccountRecord | None:
        row = await self.by_name(name)
        if row is None:
            return None
        row.password_hash = password_hash
        row.must_change_password = must_change_password
        row.disabled_at = None
        return row

    async def set_defaults(
        self, *, name: str, model: str | None, reasoning: str | None
    ) -> FakeAccountRecord | None:
        row = await self.by_name(name)
        if row is None:
            return None
        row.default_model = model
        row.default_reasoning = reasoning
        return row

    async def set_disabled(self, *, name: str, disabled: bool) -> FakeAccountRecord | None:
        row = await self.by_name(name)
        if row is None:
            return None
        row.disabled_at = datetime.now(UTC) if disabled else None
        return row

    async def set_must_change_password(
        self, *, name: str, required: bool
    ) -> FakeAccountRecord | None:
        row = await self.by_name(name)
        if row is None:
            return None
        row.must_change_password = required
        return row

    def groups_by_account(self) -> dict[str, set[str]]:
        """The shape `FakeDirectory` wants: name -> groups."""

        result: dict[str, set[str]] = {}
        for group, members in self.groups.items():
            for name in members:
                result.setdefault(name, set()).add(group)
        return result

    async def page(
        self, *, query: str, limit: int, offset: int
    ) -> tuple[int, list[FakeAccountRecord]]:
        """The real store's search and slice, over this fake's rows.

        Group names are searched too, because the roster's search box says it
        searches groups and a fake that only matched names would let a route
        test pass with a search narrower than the one it advertises.
        """

        rows = sorted(self.rows.values(), key=lambda row: row.name)
        if query:
            needle = "".join(query.lower().split())
            rows = [
                row
                for row in rows
                if _scattered(needle, row.name.lower())
                or any(_scattered(needle, g.lower()) for g in self._groups_of_row(row))
            ]

            def rank(row: FakeAccountRecord) -> int:
                name = row.name.lower()
                if name.startswith(needle):
                    return 0
                if needle in name:
                    return 1
                return 2 if _scattered(needle, name) else 3

            rows.sort(key=rank)
        return len(rows), rows[offset : offset + limit]

    def _groups_of_row(self, row: FakeAccountRecord) -> list[str]:
        return [group for group, members in self.groups.items() if row.name in members]

    async def groups_for(self, account_ids: Sequence[str]) -> dict[str, frozenset[str]]:
        by_id = {row.id: row for row in self.rows.values()}
        return {
            account_id: frozenset(self._groups_of_row(by_id[account_id]))
            for account_id in account_ids
            if account_id in by_id
        }

    async def groups_of(self, name: str) -> frozenset[str]:
        row = await self.by_name(name)
        if row is None or row.disabled:
            return frozenset()
        return frozenset(group for group, members in self.groups.items() if row.name in members)

    async def has_admin(self) -> bool:
        return any(
            name in self.rows and not self.rows[name].disabled
            for name in self.groups.get("neuman-admin", set())
        )

    async def list_all(self) -> list[FakeAccountRecord]:
        # Kept in step with the real store, which orders by `first_seen`. A fake
        # that returned an empty list for a method it did not implement is how a
        # route test passes against nothing — the shape this suite has been
        # bitten by twice.
        return sorted(
            self.rows.values(),
            key=lambda row: row.first_seen or datetime.min.replace(tzinfo=UTC),
        )

    async def ensure_group(self, name: str) -> str:
        self.groups.setdefault(name, set())
        return name

    async def list_groups(self) -> list[str]:
        return sorted(self.groups)

    async def group_exists(self, name: str) -> bool:
        return name in self.groups

    async def delete_group(self, name: str) -> bool:
        return self.groups.pop(name, None) is not None

    async def add_member(self, *, group: str, name: str) -> bool:
        row = await self.by_name(name)
        if row is None:
            return False
        self.groups.setdefault(group, set()).add(row.name)
        return True

    async def remove_member(self, *, group: str, name: str) -> bool:
        row = await self.by_name(name)
        if row is None or row.name not in self.groups.get(group, set()):
            return False
        self.groups[group].discard(row.name)
        return True

    async def members(self, group: str) -> list[FakeAccountRecord]:
        return [
            row
            for name in sorted(self.groups.get(group, set()))
            if (row := await self.by_name(name)) is not None
        ]

    # Mirrors the real store's partial-update semantics, including the parts a
    # looser fake would paper over: `None` means "not mentioned" rather than
    # "set to null", an unknown palette raises rather than being stored, and a
    # missing account returns None instead of being created.
    PALETTES = (
        "dark",
        "light",
        "nord",
        "everforest-dark",
        "everforest-light",
        "gruvbox-dark",
        "gruvbox-light",
    )

    # The same ceiling the real store holds, for the reason it holds it: the
    # route reports the bound when it refuses.
    ACTIVE_MINUTES_MAX = 1440

    async def set_interface(
        self,
        *,
        name: str,
        palette: str | None = None,
        collapse_reasoning: bool | None = None,
        active_minutes: int | None = None,
    ) -> FakeAccountRecord | None:
        if palette is not None and palette not in self.PALETTES:
            raise ValueError(f"unknown palette: {palette!r}")
        if active_minutes is not None and not 0 <= active_minutes <= self.ACTIVE_MINUTES_MAX:
            raise ValueError(f"active minutes outside 0..{self.ACTIVE_MINUTES_MAX}")
        row = self.rows.get(name)
        if row is None:
            return None
        if palette is not None:
            row.palette = palette
        if collapse_reasoning is not None:
            row.collapse_reasoning = collapse_reasoning
        if active_minutes is not None:
            row.active_minutes = active_minutes
        return row

    # The ceiling, held here for the same reason the real store holds it: the
    # route reports the number when it refuses, and a fake with a different one
    # would let a test pass against a limit production does not have.
    MAX_INSTRUCTIONS = 2000

    async def set_instructions(
        self, *, name: str, instructions: str
    ) -> FakeAccountRecord | None:
        if len(instructions) > self.MAX_INSTRUCTIONS:
            raise ValueError(f"instructions exceed {self.MAX_INSTRUCTIONS} characters")
        row = self.rows.get(name)
        if row is None:
            return None
        row.instructions = instructions
        return row


@dataclass
class FakeSessionRecord:
    account_id: str
    csrf_token: str
    expires_at: datetime
    last_seen_at: datetime


class FakeSessions:
    """Implements SessionRepository."""

    def __init__(self) -> None:
        self.rows: dict[bytes, FakeSessionRecord] = {}

    async def create(
        self, *, token_hash: bytes, account_id: str, csrf_token: str, expires_at: datetime
    ) -> FakeSessionRecord:
        row = FakeSessionRecord(
            account_id=account_id,
            csrf_token=csrf_token,
            expires_at=expires_at,
            last_seen_at=datetime.now(UTC),
        )
        self.rows[token_hash] = row
        return row

    async def by_token_hash(self, token_hash: bytes) -> FakeSessionRecord | None:
        return self.rows.get(token_hash)

    async def touch(self, token_hash: bytes, *, expires_at: datetime) -> None:
        row = self.rows.get(token_hash)
        if row is not None:
            row.expires_at = expires_at
            row.last_seen_at = datetime.now(UTC)

    async def delete(self, token_hash: bytes) -> bool:
        return self.rows.pop(token_hash, None) is not None

    async def delete_for_account(self, account_id: str) -> int:
        doomed = [key for key, row in self.rows.items() if row.account_id == account_id]
        for key in doomed:
            del self.rows[key]
        return len(doomed)

    async def purge_expired(self, *, now: datetime) -> int:
        doomed = [key for key, row in self.rows.items() if row.expires_at <= now]
        for key in doomed:
            del self.rows[key]
        return len(doomed)

    async def list_for_account(self, account_id: str) -> Sequence[FakeSessionRecord]:
        return [row for row in self.rows.values() if row.account_id == account_id]


class FakePasswords:
    """Implements PasswordHasher without doing the expensive hash.

    The real Argon2 parameters are exercised by their own test; every other
    test wants the identity flow, not 64 MiB of memory hardness per login.
    """

    PREFIX = "fake:"

    async def hash(self, password: str) -> str:
        return f"{self.PREFIX}{password}"

    async def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"{self.PREFIX}{password}"

    async def verify_dummy(self, password: str) -> None:
        return None

    def needs_rehash(self, password_hash: str) -> bool:
        return False


# -- policy -----------------------------------------------------------------


class FakeGrants:
    """Implements GrantRepository."""

    def __init__(self, grants: set[tuple[str, str]] | None = None) -> None:
        self.grants = grants or set()

    async def grants_for_groups(self, groups: frozenset[str]) -> list[tuple[str, str]]:
        return sorted((group, role) for group, role in self.grants if group in groups)

    async def list_grants(self) -> list[tuple[str, str]]:
        return sorted(self.grants)

    async def rename_role(self, old: str, new: str) -> int:
        moved = {(group, role) for group, role in self.grants if role == old}
        self.grants.difference_update(moved)
        self.grants.update((group, new) for group, _ in moved)
        return len(moved)

    async def add_grant(self, group: str, role: str) -> bool:
        before = len(self.grants)
        self.grants.add((group, role))
        return len(self.grants) != before

    async def remove_grant(self, group: str, role: str) -> bool:
        if (group, role) in self.grants:
            self.grants.discard((group, role))
            return True
        return False


class FakeGenerations:
    """Implements GenerationRepository."""

    def __init__(self) -> None:
        self.state: dict[str, tuple[int, str]] = {}

    async def current(self, account_id: str) -> int:
        return self.state.get(account_id, (0, ""))[0]

    async def bump_if_changed(self, account_id: str, policy_hash: str) -> int:
        generation, recorded = self.state.get(account_id, (0, ""))
        if recorded != policy_hash:
            generation += 1
            self.state[account_id] = (generation, policy_hash)
        return generation


@dataclass(frozen=True, slots=True)
class FakeManifest:
    """Implements ToolManifest."""

    name: str
    kind: str = "client"
    description: str = "a tool"
    needs: frozenset[str] = frozenset()
    mutating: bool = False
    critical: bool = False
    # Mirrors the real manifest. Presentation only — `/api/tools` omits an
    # ungranted diagnostic — so nothing here depends on it except the route.
    diagnostic: bool = False
    summary: str | None = None
    wait_ms: int = 5000
    lifetime_ms: int = 5000
    daemon: bool = False
    returns_image: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def detaches(self) -> bool:
        return self.lifetime_ms > self.wait_ms


def manifest(name: str, **kwargs: Any) -> FakeManifest:
    if "needs" in kwargs:
        kwargs["needs"] = frozenset(kwargs["needs"])
    return FakeManifest(name=name, **kwargs)


class FakeRegistry:
    """Implements ToolRegistry."""

    def __init__(
        self,
        manifests: Sequence[FakeManifest] = (),
        withheld: dict[str, str] | None = None,
        catalog: Any = None,
    ) -> None:
        self.tools = {item.name: item for item in manifests}
        # Tool name -> the package holding it, for tools shipped but switched off.
        self.packages = dict(withheld or {})
        # The full catalog, for the Tools page. The surface hands it the shipped
        # `tools/` tree: a page tested against a fixture catalog would prove only
        # that the fixture agrees with the page.
        self.catalog = catalog

    def get(self, name: str) -> FakeManifest | None:
        return self.tools.get(name)

    def names(self) -> Sequence[str]:
        return sorted(self.tools)

    def __len__(self) -> int:
        return len(self.tools)

    def withheld(self, name: str) -> str | None:
        return self.packages.get(name) if name not in self.tools else None


# -- audit ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeAuditRow:
    seq: int
    ts: datetime
    actor: str | None
    action: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class FakeAudit:
    """Implements AuditRepository."""

    def __init__(self) -> None:
        self.rows: list[FakeAuditRow] = []

    async def append(
        self,
        *,
        actor: str | None,
        action: str,
        payload: dict[str, Any],
        prev_hash: str,
        digest: str,
        document: str,
    ) -> FakeAuditRow:
        row = FakeAuditRow(
            seq=len(self.rows) + 1,
            ts=datetime.now(UTC),
            actor=actor,
            action=action,
            payload=payload,
            prev_hash=prev_hash,
            hash=digest,
        )
        self.rows.append(row)
        return row

    async def head(self) -> FakeAuditRow | None:
        return self.rows[-1] if self.rows else None

    async def read(self, *, after: int = 0, limit: int = 1000) -> Sequence[FakeAuditRow]:
        return [row for row in self.rows if row.seq > after][:limit]

    async def search(
        self,
        query: str,
        *,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> Sequence[FakeAuditRow]:
        found = self.rows
        if actor:
            found = [row for row in found if row.actor == actor]
        if action:
            found = [row for row in found if row.action == action]
        return found[:limit]

    async def recent_of(self, prefixes: Sequence[str], limit: int) -> Sequence[FakeAuditRow]:
        found = [row for row in self.rows if row.action.startswith(tuple(prefixes))]
        return list(reversed(found))[:limit]

    async def reindex(self) -> int:
        return len(self.rows)

    async def purge_before(self, cutoff: datetime) -> int:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.ts >= cutoff]
        return before - len(self.rows)

    @property
    def actions(self) -> list[str]:
        return [row.action for row in self.rows]


# -- harness ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeEvent:
    seq: int
    ts: datetime
    kind: str
    message_id: str | None
    payload: dict[str, Any]


class FakeImages:
    """Implements ImageRepository, in memory and content-addressed.

    Also models the reference graph (`session_image_refs`) closely enough to
    exercise `sweep`: `refs` is the edge set and `forget` is the cascade a
    session purge performs on it.
    """

    def __init__(self) -> None:
        self.rows: dict[str, StoredImage] = {}
        self.refs: set[tuple[str, str]] = set()

    async def put(self, *, session_id: str, media_type: str, data: bytes) -> StoredImage:
        sha256 = hashlib.sha256(data).hexdigest()
        # Same `DO NOTHING` behaviour as the real store: identical bytes are one
        # row, and the first media type wins.
        self.rows.setdefault(
            sha256, StoredImage(sha256=sha256, media_type=media_type, data=data)
        )
        self.refs.add((session_id, sha256))
        return StoredImage(sha256=sha256, media_type=media_type, data=data)

    async def get(self, sha256: str) -> StoredImage | None:
        return self.rows.get(sha256)

    def forget(self, session_id: str) -> None:
        """The cascade: drop one session's reference edges."""

        self.refs = {(s, sha) for (s, sha) in self.refs if s != session_id}

    async def sweep(self) -> Swept:
        referenced = {sha for _, sha in self.refs}
        orphans = [sha for sha in self.rows if sha not in referenced]
        freed = sum(len(self.rows[sha].data) for sha in orphans)
        for sha in orphans:
            del self.rows[sha]
        return Swept(images=len(orphans), bytes=freed)


class FakeEvents:
    """Implements EventRepository."""

    def __init__(self) -> None:
        self.rows: dict[str, list[FakeEvent]] = {}
        self.redactions: list[dict[str, Any]] = []
        # Every session whose whole log was fetched. A windowed read must not
        # appear here: reading all of it to show fifty rows is the cost this
        # records, and a fake that did not record it let the route get slow in
        # silence.
        self.whole_log_reads: list[str] = []

    async def append(
        self,
        *,
        branch_id: str,
        kind: str,
        payload: dict[str, Any],
        message_id: str | None = None,
        redactions: list[dict[str, Any]] | None = None,
    ) -> FakeEvent:
        events = self.rows.setdefault(branch_id, [])
        # Kept, unlike the real `EventRow`, which does not read the column back.
        # A fake that silently dropped the argument made every claim about what
        # was recorded as altered untestable — the shape of defect the README
        # warns about, where the only thing exercising the code is a fake that
        # agrees with it.
        self.redactions.append({"branch_id": branch_id, "kind": kind, "redactions": redactions})
        event = FakeEvent(
            seq=len(events) + 1,
            ts=datetime.now(UTC),
            kind=kind,
            message_id=message_id,
            payload=payload,
        )
        events.append(event)
        return event

    async def copy_prefix(
        self,
        *,
        session_id: str,
        source_branch_id: str,
        target_branch_id: str,
        through_seq: int,
        at_seq: int | None,
        side: str | None,
    ) -> int:
        """The same filter and renumbering the SQL does, over the fake rows."""

        target = self.rows.setdefault(target_branch_id, [])
        target.append(
            FakeEvent(
                seq=1,
                ts=datetime.now(UTC),
                kind="harness_event",
                message_id=None,
                payload={
                    "type": "fork",
                    "from_branch": source_branch_id,
                    "from_seq": through_seq,
                    "at_seq": at_seq,
                    "side": side,
                    "events": 0,
                },
            )
        )
        copied = 0
        for event in list(self.rows.get(source_branch_id, [])):
            if event.seq > through_seq:
                continue
            keep = event.kind in ("user_message", "response_final", "tool_result") or (
                event.kind == "harness_event"
                and (event.payload or {}).get("type") in ("turn_complete", "compaction")
            )
            if not keep:
                continue
            copied += 1
            target.append(
                FakeEvent(
                    seq=len(target) + 1,
                    ts=event.ts,
                    kind=event.kind,
                    message_id=event.message_id,
                    payload=(
                        {k: v for k, v in event.payload.items() if k != "usage"}
                        if event.kind == "response_final"
                        else event.payload
                    ),
                )
            )
        target[0].payload["events"] = copied
        return copied

    def redactions_for(self, kind: str) -> list[dict[str, Any]] | None:
        """The pointers recorded with the last appended event of ``kind``."""

        for call in reversed(self.redactions):
            if call["kind"] == kind:
                return call["redactions"]
        return None

    async def tokens_for_sessions(self, session_ids: Sequence[str]) -> dict[str, int]:
        """The same sum the store's SQL does, over the rows this fake holds.

        Summed from `response_final` payloads rather than kept as a settable
        total, so a test that wants a session to have spent something records
        the usage a provider would have reported and the route reads it.
        """

        totals: dict[str, int] = {}
        for session_id in session_ids:
            total = 0
            found = False
            for event in self.rows.get(session_id, []):
                if event.kind != "response_final":
                    continue
                usage = (event.payload or {}).get("usage") or {}
                value = usage.get("total_tokens")
                if isinstance(value, int) and not isinstance(value, bool):
                    total += value
                    found = True
            if found:
                totals[session_id] = total
        return totals

    def _matching(self, session_id: str, kinds: Sequence[str] | None) -> list[FakeEvent]:
        """The rows matching a kind filter, without recording a read.

        `window` and `summary` build on this rather than on `read`, because
        `read` is what the counter watches: a route that asks for a window must
        not look like one that asked for everything just because the fake
        implements the window by filtering the same list.
        """

        rows = self.rows.get(session_id, [])
        if kinds is None:
            return list(rows)
        return [event for event in rows if event.kind in kinds]

    async def read(
        self, branch_id: str, *, kinds: Sequence[str] | None = None
    ) -> Sequence[FakeEvent]:
        self.whole_log_reads.append(branch_id)
        return self._matching(branch_id, kinds)

    async def last_of_kind(self, branch_id: str, kind: str) -> FakeEvent | None:
        for event in reversed(self.rows.get(branch_id, [])):
            if event.kind == kind:
                return event
        return None

    async def window(
        self,
        branch_id: str,
        *,
        kinds: Sequence[str] | None = None,
        last: int | None = None,
        after: int = 0,
        limit: int = 50,
    ) -> EventWindow:
        """The same window the SQL resolves, so a route reads the same either way."""

        selected = self._matching(branch_id, kinds)
        if last is not None:
            page = list(selected[-max(1, last) :])
        else:
            page = [event for event in selected if event.seq > after][: min(max(1, limit), 200)]
        return EventWindow(
            rows=page,
            total=len(selected),
            last_seq=selected[-1].seq if selected else None,
        )

    async def summary(self, branch_id: str) -> BranchSummary:
        rows = self.rows.get(branch_id, [])
        counts: dict[str, int] = {}
        for event in rows:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return BranchSummary(
            total=len(rows),
            first_ts=rows[0].ts if rows else None,
            last_ts=rows[-1].ts if rows else None,
            counts=counts,
        )

    async def at_seq(self, branch_id: str, seq: int) -> FakeEvent | None:
        for event in self.rows.get(branch_id, []):
            if event.seq == seq:
                return event
        return None

    def kinds(self, session_id: str) -> list[str]:
        return [event.kind for event in self.rows.get(session_id, [])]


class FakeCatalog:
    """The model catalog, resolving a local name to a provider string.

    Returns a target for any name by default, with `provider_model` visibly
    different from the local name — so a test asserting on what the provider
    received cannot pass by accident when the translation is skipped. That was
    N48: the harness sent the local name and nothing noticed.
    """

    def __init__(self, targets: dict[str, ModelTarget] | None = None) -> None:
        self.targets = targets
        self.asked: list[str] = []

    async def describe(self, names: Any) -> dict[str, dict[str, Any]]:
        """What the panel reads. Costs differ per model on purpose.

        A fake returning the same numbers for every model would let a test pass while
        the route reported one model's price for another.
        """

        out: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(names):
            target = (self.targets or {}).get(name)
            if self.targets is not None and target is None:
                # A granted name with no catalog row: the panel must say "not
                # registered" rather than offer it.
                continue
            endpoint = (target.endpoint if target else None) or "https://cloud.test/v1"
            out[name] = {
                "endpoint": endpoint,
                "provider_model": f"provider/{name}",
                "vision": bool(target.vision) if target else False,
                "input_cost_per_m": 1.0 + index,
                "output_cost_per_m": 2.0 + index,
                "enabled": True,
            }
        return out

    async def resolve(self, name: str) -> ModelTarget | None:
        self.asked.append(name)
        if self.targets is not None:
            return self.targets.get(name)
        return ModelTarget(name=name, provider_model=f"provider/{name}")


class FakeGateway:
    """Implements ModelGateway. Replays scripted chunk sequences."""

    def __init__(
        self,
        scripts: list[list[Chunk]] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.scripts = scripts or []
        # Raised instead of yielding a chunk. The path is the same one a
        # connection that dies mid-stream takes, and the message is the point:
        # transport exceptions often have none, so a caller reporting only
        # `str(exc)` has nothing to report (N162).
        self.raises = raises
        self.requests: list[dict[str, Any]] = []
        self.endpoints: list[str | None] = []
        self.api_key_files: list[str | None] = []

    async def stream(
        self,
        request: dict[str, Any],
        *,
        endpoint: str | None = None,
        api_key_file: str | None = None,
    ) -> AsyncIterator[Chunk]:
        # Recorded, so a test can assert the catalog's per-model endpoint
        # actually reached the gateway rather than being accepted and dropped.
        self.endpoints.append(endpoint)
        # And the credential beside it. A fake that accepted the argument
        # without recording it would pass whether or not the key travelled with
        # the endpoint, which is the bug this parameter exists to prevent.
        self.api_key_files.append(api_key_file)
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        script = self.scripts.pop(0) if self.scripts else [Chunk("finish", {"reason": "stop"})]
        for chunk in script:
            yield chunk


def effectful(dispatches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The dispatches that could actually change something.

    `fs_write` and `fs_edit` are dispatched twice for one write: once with
    `preview: true` to render a diff for the approval frame, then once for real
    after a verdict. So "did a mutating tool run?" is a question about the second
    kind only, and asserting on the raw list would make every approval test pass
    or fail on the presence of a dispatch that cannot write.

    Keyed on the `preview` argument. It used to key on the profile's
    `read_only`, which was what made a preview safe; there is no profile now,
    and the argument is the only signal left — which is the weakening the
    approval docstring records.
    """

    return [d for d in dispatches if not d["args"].get("preview")]


class FakeExecution:
    """Implements ToolExecution."""

    def __init__(self, outcome: DispatchOutcome | None = None) -> None:
        self.outcome = outcome or DispatchOutcome(exit_code=0, stdout="ok")
        self.dispatches: list[dict[str, Any]] = []
        self.cancellations: list[str] = []
        # What the port reports as the connected client's home. None is "no
        # client connected", which is what most tests want; a test about `$HOME`
        # sets it to stand in for a client somewhere (N145).
        self.home_value: str | None = None
        self.drops_value: str | None = None
        # What the connected client said it can run, or None for "no client
        # connected" — which is what most tests want. A test that needs the
        # harness to actually probe an environment sets this, because a tool the
        # client never advertised is one the core must not dispatch.
        self.available_value: frozenset[str] | None = None
        # Per-tool overrides, for a turn where two different tools are
        # dispatched and only one of them is what the test is about: the
        # environment probe runs before the model's own call, and one shared
        # `outcome` would make the probe replay the tool's stdout.
        self.outcomes: dict[str, DispatchOutcome] = {}
        # The connections `/client` registered, so a test can assert on the
        # handshake without standing up a hub behind the app.
        self.connections: list[Any] = []
        # Who the overview reports as connected now.
        self.connected_names: list[str] = []
        # Sessions whose jobs were ended, for the task step that must not
        # leave a build running behind it.
        self.ended_sessions: list[str] = []

    async def dispatch(
        self,
        *,
        user: str,
        dispatch_id: str,
        tool: str,
        args: dict[str, Any],
        wait_ms: int,
        lifetime_ms: int | None = None,
        working_dir: str | None = None,
        surfaces: dict[str, list[str]] | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> DispatchOutcome:
        self.dispatches.append(
            {
                "user": user,
                "dispatch_id": dispatch_id,
                "tool": tool,
                "args": args,
                "wait_ms": wait_ms,
                "lifetime_ms": lifetime_ms or wait_ms,
                "session_id": session_id,
                "branch_id": branch_id,
                "surfaces": surfaces,
                # Recorded so a test can assert the dispatch was told where it
                # is. A fake that dropped the field would let the harness stop
                # sending it and every test still pass — the N38/N48 shape.
                "working_dir": working_dir,
            }
        )
        return self.outcomes.get(tool, self.outcome)

    async def cancel(self, *, user: str, dispatch_id: str) -> None:
        self.cancellations.append(dispatch_id)

    async def end_session_jobs(self, *, user: str, session_id: str) -> list[str]:
        self.ended_sessions.append(session_id)
        return []

    def connected_users(self) -> list[str]:
        return sorted(self.connected_names)

    def available(self, user: str) -> frozenset[str] | None:
        """What `available_value` says; None — no client — by default.

        None leaves the advertisement unrestricted, which is what most tests
        want. It also means the harness declines to probe for a development
        environment, because dispatching a tool no client advertised is the
        round trip the handshake exists to avoid.
        """

        return self.available_value

    def home(self, user: str) -> str | None:
        """What `home_value` says, which a test sets to stand in for a client."""

        return self.home_value

    def drops(self, user: str) -> str | None:
        """What `drops_value` says, set beside `home_value` for a client."""

        return self.drops_value

    def connected(self, user: str) -> bool:
        """Whether a dispatch for `user` has anywhere to go.

        The scheduler asks this before starting a run. `home_value` is the
        second half of the same fact in the real hub: a connection with no home
        cannot expand `$HOME`, and a task with no client has neither.
        """

        return user in self.connected_names and self.home_value is not None

    async def register(self, connection: Any) -> None:
        """Keep the connection the route built, as the hub would."""

        self.connections.append(connection)

    def unregister(self, connection: Any) -> None:
        if connection in self.connections:
            self.connections.remove(connection)


class FakeUsage:
    """Implements UsageRepository."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.settled: list[int] = []
        self.released = 0
        self._counter = itertools.count(1)
        # What `/api/usage` reads. Settable rather than derived from `settled`
        # above: that list is what the *harness* recorded this run, and the
        # route reports the day's stored total — a test wanting a figure to
        # render should state it rather than having to drive turns to produce it.
        self.today = {"settled": 0, "reserved": 0}

        # What `/admin/usage` reads. Settable rather than derived from `today`
        # above, because the admin table is a roster: a test states who spent
        # what, including the accounts that spent nothing.
        self.everyone: list[tuple[str, int, int]] = []
        # What the roster's Today column reads: account id -> (settled,
        # reserved). Keyed differently from `everyone` because the roster is
        # keyed by id and the usage tab by name, and a test should state the
        # figure in the shape the route it exercises actually reads.
        self.today_by_id: dict[str, tuple[int, int]] = {}
        # What the trend reads: (name, day, settled, reserved), exactly the rows
        # the store's history query returns. Days a test leaves out are the
        # zero-fill the route is supposed to do.
        self.records: list[tuple[str, date, int, int]] = []
        # When each account last moved a row, reported over all days rather than
        # the trend window. Settable apart from `records` because the case that
        # matters is an account whose last call is older than the window.
        self.last_call: dict[str, datetime] = {}
        # What the overview's spend by model reads, in the store's own shape.
        self.by_model: list[dict[str, Any]] = []

    async def reserve(
        self, *, account_id: str, tokens_daily: int | None, requested: int
    ) -> Reservation | None:
        if not self.allow:
            return None
        return Reservation(
            id=f"res{next(self._counter)}", account_id=account_id, tokens=requested
        )

    async def settle(self, reservation: Reservation, used: int) -> None:
        self.settled.append(used)

    async def release(self, reservation: Reservation) -> None:
        self.released += 1

    async def sweep_stale(self, *, older_than_s: float) -> int:
        return 0

    async def usage_for(self, account_id: str) -> dict[str, int]:
        return dict(self.today)

    async def all_on(self, day: date) -> list[tuple[str, int, int]]:
        return list(self.everyone)

    async def today_for(
        self, account_ids: Sequence[str], day: date
    ) -> dict[str, tuple[int, int]]:
        return {
            account_id: self.today_by_id[account_id]
            for account_id in account_ids
            if account_id in self.today_by_id
        }

    async def history(self, since: date) -> list[tuple[str, date, int, int]]:
        return [row for row in self.records if row[1] >= since]

    async def last_active(self) -> dict[str, datetime]:
        return dict(self.last_call)

    async def by_model_since(self, moment: datetime) -> list[dict[str, Any]]:
        return list(self.by_model)


class FakeRuntime:
    """The runtime host's control socket, as `adapters/runtime.RuntimeClient` asks it.

    `state` is what `status()` returns, None for a host that does not answer.
    Every load and unload is recorded as `(verb, name)`, because what the route
    hands the host — a name, never a unit or a path — is what the tests are
    about.
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state if state is not None else {"problem": None, "chat": []}
        self.calls: list[tuple[str, str]] = []
        self.answer: tuple[int, dict[str, Any]] | None = None

    async def status(self) -> dict[str, Any] | None:
        return self.state

    async def load(self, name: str) -> tuple[int, dict[str, Any]]:
        self.calls.append(("load", name))
        return self.answer or (200, {"state": "loading"})

    async def unload(self, name: str) -> tuple[int, dict[str, Any]]:
        self.calls.append(("unload", name))
        return self.answer or (200, {"state": "unloaded"})


class FakeFetch:
    """The fetcher's control socket: records what it was asked, reports `jobs`."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    async def status(self) -> dict[str, Any] | None:
        return self.jobs

    async def fetch(self, preset: str) -> tuple[int, dict[str, Any]]:
        self.calls.append(("fetch", preset))
        self.jobs[preset] = {"state": "fetching", "done_bytes": 0, "total_bytes": 1}
        return 202, self.jobs[preset]

    async def cancel(self, preset: str) -> tuple[int, dict[str, Any]]:
        self.calls.append(("cancel", preset))
        return 200, {"state": "cancelled"}

    async def delete(self, preset: str) -> tuple[int, dict[str, Any]]:
        self.calls.append(("delete", preset))
        return 200, {"freed_bytes": 1}
