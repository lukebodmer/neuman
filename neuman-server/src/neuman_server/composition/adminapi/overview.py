"""The overview: what needs the operator, what is happening, what it costs.

The first tab of the administration page used to report the build, the uptime
and the schema version — what an operator needs when filing a bug, which is the
rarest reason to open the page. It now answers the two questions they open it
with: does anything need me, and what is going on. The identity line is still
here, as a footer.

One route, so the tab is one round trip, and every number in it is read from
something that already exists: the catalog and the presets, the runtime host
and the fetcher, the GPU sample, the usage ledger and the responses, the audit
chain, the server's event log, and the live state of this process. Nothing here
is stored for the overview's sake.

**Needs attention** is a list of things that are wrong *now* and that an
operator can fix, each naming the tab that fixes it. A check belongs here only
if both are true: a warning nobody can act on is noise, and one about the past
belongs in Events.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, AuditStore
from ...adapters.persistence.events import ServerEventStore
from ...adapters.persistence.harness import UsageStore
from ...adapters.weights import weights_state
from ...decision.harness.presets import local_facts
from ...foundation.db import latest_version, schema_version
from ..waiting import until_newer_sample
from .deps import connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_overview_routes"]

# Seen within this long counts as active now. The session touch moves
# `last_seen` on every request, so a person reading a reply is still here.
_ACTIVE = timedelta(minutes=15)
# A backup older than this is worth a line. A week is the longest an appliance
# whose database is its whole configuration should go without one.
_BACKUP_STALE = timedelta(days=7)
# What a backup holds, said wherever one is asked for. "A backup" names an act,
# not the thing at risk; these are the three parts of the appliance's own state
# (`decision/ops/backup.py`), and the provider keys are deliberately not of them.
_BACKUP_HOLDS = "the database, the audit log and the policy overlay"
# The trend's length, today included.
_DAYS = 7
# How long the usage figures are reused between refreshes of an open overview.
# Measured on a million events, the reply scan took ~73 ms; once a minute is
# negligible where once per GPU change while the tab is open would not be.
_USAGE_FOR_S = 60.0
# Configuration changes, by audit action prefix. Not tool calls, logins, or a
# person's own preferences: those are what Audit is for, and here they would
# bury the one change an operator is looking for.
_CHANGES = (
    "settings.",
    "policy.role_",
    "policy.overlay_",
    "ops.",
    "weights.",
    "identity.account_",
    "identity.group_",
    "identity.admin_granted",
    "identity.bootstrap_admin",
    "identity.password_reset",
)


def admin_overview_routes() -> list[Any]:
    from starlette.routing import Route

    return [Route("/admin/overview", _overview, methods=["GET"])]


async def _overview(request: Request) -> Response:
    from starlette.responses import JSONResponse

    services = services_of(request)

    # `?after=<sampled_at>`, as the model panel does: held until the GPU sample
    # is newer, so an open overview follows a load without polling.
    after = request.query_params.get("after")
    if after:
        try:
            await until_newer_sample(services.gpu, float(after))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    now = datetime.now(UTC)
    today = now.date()
    async with connection(request) as conn:
        accounts = await AccountStore(conn).list_all()
        cached = services.overview_usage
        if (
            cached is not None
            and time.monotonic() - cached[0] < _USAGE_FOR_S
            and cached[1][0] == today
        ):
            _day, history, by_model = cached[1]
        else:
            usage = UsageStore(conn)
            history = await usage.history(today - timedelta(days=_DAYS - 1))
            by_model = await usage.by_model_since(
                datetime(today.year, today.month, today.day, tzinfo=UTC)
            )
            services.overview_usage = (time.monotonic(), (today, history, by_model))
        events = await ServerEventStore(conn).count_since(
            now - timedelta(days=1), ("error", "warning")
        )
        audit = AuditStore(conn)
        changes = await audit.recent_of(_CHANGES, 8)
        backups = await audit.search("", action="ops.backup", limit=1)
        applied = await schema_version(conn)

    runtime = await services.runtime.status() if services.runtime is not None else None
    fetching = await services.fetch.status() if services.fetch is not None else None
    gpu = services.gpu.gpu() if services.gpu is not None else {}

    return JSONResponse(
        {
            "attention": _attention(
                services,
                runtime=runtime,
                fetching=fetching,
                accounts=accounts,
                events=events,
                last_backup=backups[0].ts if backups else None,
                schema=(applied, latest_version()),
                now=now,
            ),
            "now": _now(services, accounts, runtime, gpu, now),
            "today": _today(services, history, by_model, today),
            "recent": [
                {
                    "at": row.ts.isoformat(),
                    "actor": row.actor,
                    "summary": _summary(row, services.presets),
                }
                for row in changes
            ],
            "about": {
                "build": services.settings.build_id,
                "uptime_s": (
                    time.monotonic() - services.started_mono if services.started_mono else None
                ),
                "bind": services.settings.bind,
                "port": services.settings.port,
                "tls": services.settings.tls_enabled,
                "schema": {"applied": applied, "expected": latest_version()},
            },
        }
    )


# -- needs attention --------------------------------------------------------------


def _attention(
    services: Any,
    *,
    runtime: dict[str, Any] | None,
    fetching: dict[str, Any] | None,
    accounts: Any,
    events: dict[str, int],
    last_backup: datetime | None,
    schema: tuple[int, int],
    now: datetime,
) -> list[dict[str, Any]]:
    """Every problem an operator can fix now, worst first, each with its tab.

    Worst first by construction: the checks below that report errors come
    before those that report warnings, and a new check goes in with its kind.
    """

    items: list[dict[str, Any]] = []

    def add(level: str, text: str, tab: str | None) -> None:
        items.append({"level": level, "text": text, "tab": tab})

    applied, expected = schema
    if applied != expected:
        add(
            "error",
            f"The database is at schema {applied} and this build expects {expected}: a "
            "migration did not apply, or an older backup was restored.",
            None,
        )

    baseline = services.baseline
    presets = services.presets
    models = baseline.get("models") or {}
    providers = baseline.get("providers") or {}

    # Providers whose key cannot be read: every model on them fails its first
    # turn with a 401, after the user has typed.
    for name, provider in sorted(providers.items()):
        if provider.get("preset") is None:
            continue
        key = provider.get("api_key_file")
        if not key:
            add("error", f"Provider {name} has no key.", "providers")
        elif not Path(key).is_file():
            add("error", f"Provider {name}'s key file is missing.", "providers")

    # Models on this machine whose weights are not there to load.
    local_uses = [
        (name, model.get("preset")) for name, model in models.items() if "local" in model
    ]
    for name, preset_id in sorted(local_uses, key=lambda item: str(item[0])):
        facts = local_facts(presets, preset_id) if isinstance(preset_id, str) else None
        if facts is None:
            continue
        state = weights_state(services.settings.models_dir, facts["weights"]["files"])["state"]
        if state != "verified":
            add("error", f"{name}'s weights are {state}; it cannot load.", "models")

    if runtime is None and local_uses:
        add(
            "error",
            "The runtime host is not answering; no model on this machine can run.",
            "models",
        )
    elif runtime is not None:
        if runtime.get("problem"):
            add(
                "error",
                f"The runtime host refused its configuration: {runtime['problem']}",
                "models",
            )
        for model in runtime.get("chat") or []:
            if model.get("error"):
                add("warn", f"{model['name']} could not load: {model['error']}", "models")

    for preset_id, job in sorted((fetching or {}).items()):
        if job.get("state") == "failed":
            label = (presets.get("models") or {}).get(preset_id, {}).get("label", preset_id)
            add("warn", f"Downloading {label} failed: {job.get('error')}", "models")

    # Grants and the catalog disagreeing: a model nobody may choose, or a role
    # offering one that does not exist.
    granted: set[str] = set((baseline.get("defaults") or {}).get("models") or [])
    for role in (baseline.get("roles") or {}).values():
        granted |= set(role.get("models") or [])
    ungranted = sorted(set(models) - granted)
    if ungranted:
        add(
            "warn",
            f"{_names(ungranted)} {'is' if len(ungranted) == 1 else 'are'} granted to no role, "
            "so nobody can choose "
            f"{'it' if len(ungranted) == 1 else 'them'}.",
            "roles",
        )
    missing = sorted(granted - set(models))
    if missing:
        add(
            "warn",
            f"Roles grant {_names(missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} not in the catalog.",
            "roles",
        )

    pending = sorted(
        row.name
        for row in accounts
        if getattr(row, "must_change_password", False)
        and getattr(row, "disabled_at", None) is None
    )
    if pending:
        add("warn", f"{_names(pending)} must still change their password.", "accounts")

    if events.get("error"):
        count = events["error"]
        add("warn", f"{count} error{'s' if count != 1 else ''} in the last day.", "events")

    if last_backup is None:
        add(
            "warn",
            f"No backup has been made. `neuman-admin backup <directory>` copies "
            f"{_BACKUP_HOLDS}.",
            None,
        )
    elif now - last_backup > _BACKUP_STALE:
        add(
            "warn",
            f"The last backup was {(now - last_backup).days} days ago. "
            f"`neuman-admin backup <directory>` copies {_BACKUP_HOLDS}.",
            None,
        )

    return items


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


# -- now -----------------------------------------------------------------------------


def _now(
    services: Any,
    accounts: Any,
    runtime: dict[str, Any] | None,
    gpu: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    active = sorted(
        row.name
        for row in accounts
        if row.last_seen is not None
        and now - row.last_seen <= _ACTIVE
        and getattr(row, "disabled_at", None) is None
    )
    turns = [
        {"branch_id": branch_id, "account": running.account}
        for branch_id, running in sorted(services.turns.items())
    ]
    # An approval is someone blocked on a person, the one thing in "now" an
    # operator may be the answer to.
    awaiting = []
    for branch_id, inbox in sorted(services.inboxes.items()):
        calls = inbox.pending()
        if not calls:
            continue
        running = services.turns.get(branch_id)
        awaiting.append(
            {
                "branch_id": branch_id,
                "account": running.account if running is not None else None,
                "calls": len(calls),
            }
        )
    loaded = [m["name"] for m in (runtime or {}).get("chat") or [] if m.get("loaded")]
    device = (gpu.get("devices") or [None])[0] if gpu.get("available") else None
    return {
        "active": active,
        "clients": services.execution.connected_users(),
        "turns": turns,
        "awaiting": awaiting,
        "loaded": loaded,
        "gpu": (
            {
                "name": device.get("name"),
                "used_mb": device.get("memory_used_mb"),
                "total_mb": device.get("memory_total_mb"),
                "sampled_at": gpu.get("sampled_at"),
            }
            if device
            else None
        ),
    }


# -- today ---------------------------------------------------------------------------


def _today(
    services: Any,
    history: list[tuple[str, Any, int, int]],
    by_model: list[dict[str, Any]],
    today: Any,
) -> dict[str, Any]:
    """Tokens and spend today, and tokens for the week, both from stored records.

    Tokens and dollars are both shown because neither is the whole story: a
    model on this machine costs nothing per token, so a dollar figure alone
    would make the local share disappear, and a token figure alone would hide
    which tokens were billed.
    """

    days = [today - timedelta(days=offset) for offset in range(_DAYS - 1, -1, -1)]
    per_day = dict.fromkeys(days, 0)
    per_account: dict[str, int] = {}
    for name, day, settled, _reserved in history:
        if day in per_day:
            per_day[day] += settled
        if day == today and settled:
            per_account[name] = per_account.get(name, 0) + settled

    models = services.baseline.get("models") or {}
    usd = sum(row["usd"] for row in by_model if row["usd"] is not None)
    priced = any(row["usd"] is not None for row in by_model)
    return {
        "tokens": per_day[today],
        "usd": round(usd, 4) if priced else None,
        "usd_estimated": any(row["estimated"] for row in by_model),
        "unpriced_tokens": sum(row["unpriced_tokens"] for row in by_model),
        "days": [{"day": day.isoformat(), "tokens": per_day[day]} for day in days],
        "accounts": [
            {"name": name, "tokens": tokens}
            for name, tokens in sorted(per_account.items(), key=lambda item: -item[1])[:5]
        ],
        "models": [
            {
                "name": row["model"],
                "tokens": row["tokens"],
                "usd": round(row["usd"], 4) if row["usd"] is not None else None,
                "local": "local" in (models.get(row["model"]) or {}),
            }
            for row in by_model[:5]
        ],
    }


# -- recently changed ----------------------------------------------------------------

_PHRASES = {
    "settings.provider_added": "added provider {name}",
    "settings.provider_key_set": "set the key for {name}",
    "settings.model_added": "added model {name}",
    "settings.local_model_added": "added {name} on this machine",
    "settings.baseline_set": "edited the configuration",
    "settings.role_renamed": "renamed role {from} to {to}",
    "settings.prompt_set": "changed the prompt",
    "settings.home_dirs_set": "changed the home folders",
    "settings.home_dirs_reset": "reset the home folders to the default",
    "policy.role_granted": "granted {role} to {group}",
    "policy.role_revoked": "revoked {role} from {group}",
    "policy.overlay_set": "changed {account}'s exception",
    "policy.overlay_cleared": "cleared {account}'s exception",
    "ops.backup": "made a backup",
    "ops.restore": "restored a backup",
    "weights.fetch_requested": "downloaded {preset}",
    "weights.deleted": "deleted {preset}'s weights",
    "identity.account_created": "created account {account}",
    "identity.account_disabled": "disabled account {account}",
    "identity.group_created": "created group {group}",
    "identity.group_member_added": "added {account} to {group}",
    "identity.group_member_removed": "removed {account} from {group}",
    "identity.admin_granted": "made {account} an administrator",
    "identity.bootstrap_admin": "became the first administrator",
    "identity.password_reset": "reset {account}'s password",
}


def _summary(row: Any, presets: dict[str, Any]) -> str:
    """One audit entry as a phrase, falling back to its action name.

    A preset is named by its label, as the Models tab shows it, rather than by
    the id the payload records.
    """

    payload = dict(row.payload) if isinstance(row.payload, dict) else {}
    labels = {
        key: value.get("label", key) for key, value in (presets.get("models") or {}).items()
    }
    if "preset" in payload:
        payload["preset"] = labels.get(payload["preset"], payload["preset"])
    phrase = _PHRASES.get(row.action)
    if phrase is None:
        return row.action
    try:
        return phrase.format_map({key: str(value) for key, value in payload.items()})
    except KeyError:
        # A payload without the field the phrase names: say what kind of change
        # it was rather than nothing.
        return phrase.split(" {", 1)[0]
