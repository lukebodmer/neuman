"""Usage administration.

Neuman owns the keys and the accounting (docs/decisions.md D20), so this reads
our own tables rather than proxying a bundled service's admin API.

`/admin/models` reports what the catalog resolves to. It is written through the
baseline (`settings.py`) and the preset routes (`catalog.py`), not here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ...adapters.persistence import AccountStore, GrantStore, UsageStore
from ...decision.policy.ops import preview_policy
from .deps import connection, services_of

if TYPE_CHECKING:  # pragma: no cover
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["admin_gateway_routes"]


def admin_gateway_routes() -> list[Any]:
    from starlette.routing import Route

    return [
        Route("/admin/models", _list_models, methods=["GET"]),
        Route("/admin/usage", _all_usage, methods=["GET"]),
        Route("/admin/usage/{account}", _usage, methods=["GET"]),
    ]


async def _list_models(request: Request) -> Response:
    """What the appliance resolved from the declared catalog.

    Read-only. It stays because an entry that resolves wrongly looks exactly
    like one that works until a turn spends tokens on it, and this is the one
    surface that reports where each name actually sends — the same reason
    `vision` and `context_tokens` are reported rather than left to be inferred.

    Unscoped, unlike the per-user panel: an operator asking what the appliance
    can reach is entitled to the whole answer, where a user is shown only what
    policy grants them.

    `api_key_file` is reported as a path and never read. Confirming which file
    a provider was pointed at is the point; its contents are not.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    baseline = services.model_catalog().baseline
    providers = baseline.get("providers") or {}
    models = baseline.get("models") or {}
    # A preset names its maker through its logo. A model added by hand has no preset,
    # and is drawn without a brand.
    presets = services.presets["models"]

    return JSONResponse(
        {
            "providers": [
                {
                    "name": name,
                    "url": entry.get("url"),
                    "api_key_file": entry.get("api_key_file"),
                }
                for name, entry in sorted(providers.items())
            ],
            "models": [
                {
                    "name": name,
                    "provider": entry.get("provider"),
                    "provider_model": entry.get("provider_model"),
                    "logo": (presets.get(entry.get("preset")) or {}).get("logo"),
                    # Resolved rather than left to the reader to join. The
                    # question this surface answers is where a model
                    # actually sends, and a provider name is one lookup
                    # short of that.
                    "endpoint": (providers.get(entry.get("provider")) or {}).get("url"),
                    "vision": bool(entry.get("vision", False)),
                    "context_tokens": int(entry.get("context_tokens", 0) or 0),
                    "max_tokens": int(entry.get("max_tokens", 0) or 0),
                    "input_cost_per_m": float(entry.get("input_cost_per_m", 0) or 0),
                    "output_cost_per_m": float(entry.get("output_cost_per_m", 0) or 0),
                }
                for name, entry in sorted(models.items())
            ],
        }
    )


async def _all_usage(request: Request) -> Response:
    """Today's tokens for every account, in one read.

    The panel draws this as a table and asks once. The per-account route behind
    it was reachable only by typing a name into a box, which made an account
    that spent nothing invisible rather than zero — and the operator's question
    is "who is spending today", which is a roster, not a lookup.

    Each figure carries the cap it is measured against, composed from policy
    rather than stored with the count, for the same reason `/api/usage` does: a
    limit can change under a running day, and a percentage against a stale one
    is worse than none. `None` means uncapped.
    """

    from starlette.responses import JSONResponse

    services = services_of(request)
    # One day for the query and the label: computing it twice could cross
    # midnight between the rows read and the date they are reported under.
    today = datetime.now(UTC).date()
    account = (request.query_params.get("account") or "").strip()

    try:
        days = int(request.query_params.get("days", "14"))
    except ValueError:
        return JSONResponse(
            {"error": "invalid_request", "detail": "days must be an integer"},
            status_code=400,
        )
    # A quarter of bars is unreadable in a card and unbounded work for the
    # store; a fortnight is what the roster and its detail both draw.
    days = max(1, min(days, 90))
    since = today - timedelta(days=days - 1)

    async with connection(request) as conn:
        store = UsageStore(conn)
        rows = await store.all_on(today)
        if account:
            # One account's page: the detail view asks for its own history, and
            # the roster on a large appliance would otherwise send every other
            # account's days to draw one chart.
            rows = [row for row in rows if row[0] == account]
        # Keyed by account and then by day, so the zero-fill below is a lookup
        # rather than a scan per account.
        recorded: dict[str, dict[date, tuple[int, int]]] = {}
        for name, day, settled, reserved in await store.history(since):
            recorded.setdefault(name, {})[day] = (settled, reserved)
        # Over all days rather than the window: "last call" says when, and an
        # empty fortnight is not the same fact as no call ever.
        last_active = await store.last_active()

        accounts = []
        for name, settled, reserved in rows:
            policy = await preview_policy(
                name,
                source=services.policy_source(),
                directory=services.directory,
                grants=GrantStore(conn),
            )
            stored = recorded.get(name, {})
            last_call = last_active.get(name)
            accounts.append(
                {
                    "name": name,
                    "settled": settled,
                    "reserved": reserved,
                    "tokens_daily": policy.tokens_daily,
                    "last_call": last_call.isoformat() if last_call else None,
                    "history": [
                        {
                            "day": day.isoformat(),
                            "settled": stored.get(day, (0, 0))[0],
                            "reserved": stored.get(day, (0, 0))[1],
                        }
                        for day in (since + timedelta(days=offset) for offset in range(days))
                    ],
                }
            )

    # Nearest its cap first: an account about to stop working is what the
    # operator opened this to see. Uncapped accounts follow by spend, which is
    # the only ordering they have.
    accounts.sort(
        key=lambda account: (
            0 if account["tokens_daily"] else 1,
            (
                -account["settled"] / account["tokens_daily"]
                if account["tokens_daily"]
                else -account["settled"]
            ),
            account["name"],
        )
    )

    return JSONResponse({"day": today.isoformat(), "days": days, "accounts": accounts})


async def _usage(request: Request) -> Response:
    from starlette.responses import JSONResponse

    account_name = request.path_params["account"]

    async with connection(request) as conn:
        account = await AccountStore(conn).by_name(account_name)
        if account is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        usage = await UsageStore(conn).usage_for(account.id)

    return JSONResponse({"account": account_name, **usage})
