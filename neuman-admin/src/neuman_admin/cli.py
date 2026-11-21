"""`neuman-admin` — the admin's interface to a running appliance.

A thin argparse front end over `client.py`. Every command maps to one admin
route: this decides nothing, and it must not, because the checks that matter
live in the decision core where both surfaces share them (D9). A CLI that
validated policy itself would be a second implementation of the rules, and two
implementations of an authorization rule is how one of them ends up wrong.

**Output.** Human-readable by default, `--json` for the raw response. The
default formatting exists because an admin reading an audit chain or a
policy preview in raw JSON is being asked to do the machine's job; the flag
exists because anything a person can read, a script eventually parses, and
scraping the pretty form is worse than asking for the real one.

**Exit codes**, so a script can branch without parsing anything:

* `0` — the server answered and the answer was success.
* `1` — the server refused (bad request, denied, not found). It said why.
* `2` — usage error, from argparse.
* `69` — `EX_UNAVAILABLE`: the socket is not there. Usually the server is not
  running, or this account is not in the `neuman` group.

Conflating the last with a refusal would be the expensive mistake: an admin
told "forbidden" checks their authority, and an admin told "unreachable"
checks the service. They are different afternoons.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from .client import DEFAULT_SOCKET, AdminClient, AdminError, Unreachable
from .format import render

__all__ = ["main"]

EX_FAILURE = 1
EX_UNAVAILABLE = 69  # sysexits.h


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neuman-admin",
        description="Operate a running Neuman appliance over its admin socket.",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_SOCKET,
        help=f"admin socket path (default: {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the server's response verbatim",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # -- policy -------------------------------------------------------------
    policy = sub.add_parser("policy", help="inspect and author policy").add_subparsers(
        dest="subcommand", required=True
    )

    show = policy.add_parser(
        "show",
        help="the effective policy for an account, and where each part came from",
    )
    show.add_argument("account")
    show.set_defaults(handler=_policy_show)

    overlay_set = policy.add_parser(
        "set", help="apply a policy overlay from a JSON file (- for stdin)"
    )
    overlay_set.add_argument("account")
    overlay_set.add_argument("fragment", help="path to a JSON fragment, or -")
    overlay_set.set_defaults(handler=_policy_set)

    overlay_clear = policy.add_parser("clear", help="remove an account's overlay")
    overlay_clear.add_argument("account")
    overlay_clear.set_defaults(handler=_policy_clear)

    # -- roles --------------------------------------------------------------
    roles = sub.add_parser("role", help="policy roles").add_subparsers(
        dest="subcommand", required=True
    )

    rename = roles.add_parser("rename", help="rename a role everywhere")
    rename.add_argument("name")
    rename.add_argument("to")
    rename.set_defaults(handler=_role_rename)

    # -- grants -------------------------------------------------------------
    grants = sub.add_parser("grant", help="bind groups to roles").add_subparsers(
        dest="subcommand", required=True
    )

    grants_list = grants.add_parser("list", help="every group→role binding")
    grants_list.set_defaults(handler=_grant_list)

    grant_add = grants.add_parser("add", help="bind a group to a role")
    grant_add.add_argument("group")
    grant_add.add_argument("role")
    grant_add.set_defaults(handler=_grant_add)

    grant_remove = grants.add_parser("remove", help="unbind a group from a role")
    grant_remove.add_argument("group")
    grant_remove.add_argument("role")
    grant_remove.set_defaults(handler=_grant_remove)

    # -- audit --------------------------------------------------------------
    audit = sub.add_parser("audit", help="the governance record").add_subparsers(
        dest="subcommand", required=True
    )

    search = audit.add_parser("search", help="search the audit chain")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--actor")
    search.add_argument("--action")
    search.add_argument("--limit", type=int, default=50)
    search.set_defaults(handler=_audit_search)

    verify = audit.add_parser("verify", help="re-check the hash chain; reports the first break")
    verify.set_defaults(handler=_audit_verify)

    reindex = audit.add_parser("reindex", help="rebuild the search index")
    reindex.set_defaults(handler=_audit_reindex)

    # -- sessions -----------------------------------------------------------
    #
    # The admin view of a conversation. The `session_query` tool covers a user
    # reading their *own*; this is the same operation
    # (`decision/ops/sessions.py`) reached with an admin's scope, so paging and
    # clipping are not described twice.
    sessions = sub.add_parser("session", help="read any account's sessions").add_subparsers(
        dest="subcommand", required=True
    )

    sessions_list = sessions.add_parser("list", help="sessions, newest first")
    sessions_list.add_argument("--account", help="only this account's")
    sessions_list.set_defaults(handler=_session_list)

    sessions_branches = sessions.add_parser(
        "branches", help="the branches of one session, oldest first"
    )
    sessions_branches.add_argument("session")
    sessions_branches.set_defaults(handler=_session_branches)

    sessions_kinds = sessions.add_parser(
        "kinds", help="event-kind counts and time span, reading no payloads"
    )
    sessions_kinds.add_argument("session")
    sessions_kinds.add_argument("--branch", help="default: the most recently active")
    sessions_kinds.set_defaults(handler=_session_kinds)

    sessions_events = sessions.add_parser("events", help="a window of the event log")
    sessions_events.add_argument("session")
    sessions_events.add_argument("--branch", help="default: the most recently active")
    sessions_events.add_argument("--kind", help="comma-separated kinds to keep")
    sessions_events.add_argument("--last", type=int, help="the final N events")
    sessions_events.add_argument("--after", type=int, help="events after this seq")
    sessions_events.add_argument("--limit", type=int, default=50)
    sessions_events.set_defaults(handler=_session_events)

    sessions_event = sessions.add_parser("event", help="one event, payload whole")
    sessions_event.add_argument("session")
    sessions_event.add_argument("seq", type=int)
    sessions_event.add_argument("--branch", help="default: the most recently active")
    sessions_event.set_defaults(handler=_session_event)

    sessions_purge = sessions.add_parser(
        "purge", help="delete sessions with no activity for a duration, with their images"
    )
    sessions_purge.add_argument(
        "--older-than",
        required=True,
        metavar="DURATION",
        help="e.g. 5d, 12h, 30m — measured from each session's last activity",
    )
    sessions_purge.add_argument("--account", help="only this account's")
    sessions_purge.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    sessions_purge.set_defaults(handler=_session_purge)

    # -- bootstrap ----------------------------------------------------------
    bootstrap = sub.add_parser(
        "bootstrap", help="create the first administrator (local socket only)"
    )
    bootstrap.add_argument("--name", help="the account name (default: this account)")
    bootstrap.add_argument("--password-file", help="read the password from this file")
    bootstrap.add_argument(
        "--force", action="store_true", help="reset an existing administrator's password"
    )
    bootstrap.set_defaults(handler=_bootstrap)

    # -- accounts -----------------------------------------------------------
    accounts = sub.add_parser("account", help="accounts on this appliance").add_subparsers(
        dest="subcommand", required=True
    )

    accounts_list = accounts.add_parser("list", help="every account on the appliance")
    accounts_list.set_defaults(handler=_account_list)

    account_create = accounts.add_parser("create", help="create an account")
    account_create.add_argument("account")
    account_create.add_argument("--admin", action="store_true", help="also make an operator")
    account_create.add_argument(
        "--role",
        action="append",
        default=[],
        help="assign a role; repeat for more than one",
    )
    account_create.add_argument(
        "--group",
        action="append",
        default=[],
        help="add the account to a group; repeat for more than one",
    )
    account_create.add_argument("--password-file")
    account_create.add_argument(
        "--no-change", action="store_true", help="do not require a password change"
    )
    account_create.set_defaults(handler=_account_create)

    account_passwd = accounts.add_parser("passwd", help="set a temporary password")
    account_passwd.add_argument("account")
    account_passwd.add_argument("--password-file")
    account_passwd.add_argument(
        "--no-change", action="store_true", help="do not require a password change"
    )
    account_passwd.set_defaults(handler=_account_passwd)

    account_disable = accounts.add_parser("disable", help="revoke an account and its sessions")
    account_disable.add_argument("account")
    account_disable.set_defaults(handler=_account_disable)

    account_enable = accounts.add_parser("enable", help="allow an account to sign in again")
    account_enable.add_argument("account")
    account_enable.set_defaults(handler=_account_enable)

    account_admin = accounts.add_parser("admin", help="give an account operator authority")
    account_admin.add_argument("account")
    account_admin.set_defaults(handler=_account_admin)

    account_unadmin = accounts.add_parser("unadmin", help="remove operator authority")
    account_unadmin.add_argument("account")
    account_unadmin.set_defaults(handler=_account_unadmin)

    # -- groups -------------------------------------------------------------
    groups = sub.add_parser("group", help="policy groups").add_subparsers(
        dest="subcommand", required=True
    )
    group_list = groups.add_parser("list", help="every group and its members")
    group_list.set_defaults(handler=_group_list)
    group_create = groups.add_parser("create", help="create a group")
    group_create.add_argument("group")
    group_create.set_defaults(handler=_group_create)
    group_delete = groups.add_parser("delete", help="delete a group and its grants")
    group_delete.add_argument("group")
    group_delete.set_defaults(handler=_group_delete)
    group_add = groups.add_parser("add", help="add an account to a group")
    group_add.add_argument("group")
    group_add.add_argument("account")
    group_add.set_defaults(handler=_group_add)
    group_remove = groups.add_parser("remove", help="remove an account from a group")
    group_remove.add_argument("group")
    group_remove.add_argument("account")
    group_remove.set_defaults(handler=_group_remove)

    # -- runtime settings ---------------------------------------------------
    baseline = sub.add_parser("baseline", help="the policy and model baseline").add_subparsers(
        dest="subcommand", required=True
    )
    baseline_show = baseline.add_parser("show", help="print the current baseline")
    baseline_show.set_defaults(handler=_baseline_show)
    baseline_set = baseline.add_parser(
        "set", help="replace the baseline from JSON (- for stdin)"
    )
    baseline_set.add_argument("source")
    baseline_set.set_defaults(handler=_baseline_set)

    prompt = sub.add_parser("prompt", help="the operator's prompt text").add_subparsers(
        dest="subcommand", required=True
    )
    prompt_show = prompt.add_parser("show", help="print the operator prompt")
    prompt_show.set_defaults(handler=_prompt_show)
    prompt_set = prompt.add_parser("set", help="replace the prompt from a file (- for stdin)")
    prompt_set.add_argument("source")
    prompt_set.set_defaults(handler=_prompt_set)

    events = sub.add_parser("events", help="recent warnings and errors")
    events.add_argument("--level", choices=["warning", "error"])
    events.add_argument("--limit", type=int, default=100)
    events.set_defaults(handler=_events)

    # -- models -------------------------------------------------------------
    # Read-only, and that is the whole surface. The catalog lives in the
    # baseline document in Postgres (D68) and is written two ways: the
    # administration page, whose preset routes hold an entry to its facts and
    # file a provider's key (D76, D77), and `baseline set` for a whole
    # document. A `model set` here would be a third spelling of the same write.
    # `list` stays because confirming what the appliance actually resolved is
    # worth a command: an entry that resolves wrongly looks exactly like one
    # that works until a turn spends tokens on it.
    models = sub.add_parser("model", help="the model catalog").add_subparsers(
        dest="subcommand", required=True
    )

    models_list = models.add_parser("list", help="declared models")
    models_list.set_defaults(handler=_model_list)

    # -- status, usage, backup ----------------------------------------------
    status = sub.add_parser("status", help="what the appliance reports about itself")
    status.set_defaults(handler=_status)

    usage = sub.add_parser("usage", help="today's token usage for an account")
    usage.add_argument("account")
    usage.set_defaults(handler=_usage)

    backup = sub.add_parser("backup", help="dump the database to a destination path")
    backup.add_argument("destination")
    backup.set_defaults(handler=_backup)

    restore = sub.add_parser("restore", help="load a backup, replacing the current state")
    restore.add_argument("source", help="a directory written by `neuman-admin backup`")
    restore.set_defaults(handler=_restore)

    return parser


# -- handlers ---------------------------------------------------------------
#
# Each is one request. They are separate functions rather than a table because
# a few need to read a file or reshape arguments, and a table with exceptions
# is harder to read than the thing it replaced.


def _policy_show(client: AdminClient, args: Any) -> Any:
    return client.request("GET", f"/admin/policy/{args.account}")


def _policy_set(client: AdminClient, args: Any) -> Any:
    fragment = _read_json(args.fragment)
    return client.request("PUT", f"/admin/policy/{args.account}/overlay", body=fragment)


def _policy_clear(client: AdminClient, args: Any) -> Any:
    return client.request("DELETE", f"/admin/policy/{args.account}/overlay")


def _role_rename(client: AdminClient, args: Any) -> Any:
    return client.request("POST", f"/admin/roles/{args.name}/rename", body={"to": args.to})


def _grant_list(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/grants")


def _grant_add(client: AdminClient, args: Any) -> Any:
    return client.request(
        "POST", "/admin/grants", body={"group": args.group, "role": args.role}
    )


def _grant_remove(client: AdminClient, args: Any) -> Any:
    return client.request(
        "DELETE", "/admin/grants", body={"group": args.group, "role": args.role}
    )


def _audit_search(client: AdminClient, args: Any) -> Any:
    params = {"q": args.query, "limit": str(args.limit)}
    if args.actor:
        params["actor"] = args.actor
    if args.action:
        params["action"] = args.action
    return client.request("GET", "/admin/audit", params=params)


def _audit_verify(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("POST", "/admin/audit/verify")


def _audit_reindex(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("POST", "/admin/audit/reindex")


def _session_list(client: AdminClient, args: Any) -> Any:
    params = {"account": args.account} if args.account else None
    return client.request("GET", "/admin/sessions", params=params)


def _session_branches(client: AdminClient, args: Any) -> Any:
    return client.request("GET", f"/admin/sessions/{args.session}", params={"view": "branches"})


def _session_kinds(client: AdminClient, args: Any) -> Any:
    params = {"view": "kinds"}
    if args.branch:
        params["branch"] = args.branch
    return client.request("GET", f"/admin/sessions/{args.session}", params=params)


def _session_events(client: AdminClient, args: Any) -> Any:
    params: dict[str, str] = {"view": "events", "limit": str(args.limit)}
    if args.branch:
        params["branch"] = args.branch
    if args.kind:
        params["kind"] = args.kind
    if args.last is not None:
        params["last"] = str(args.last)
    if args.after is not None:
        params["after"] = str(args.after)
    return client.request("GET", f"/admin/sessions/{args.session}", params=params)


def _session_event(client: AdminClient, args: Any) -> Any:
    return client.request(
        "GET",
        f"/admin/sessions/{args.session}",
        params={
            "view": "event",
            "seq": str(args.seq),
            **({"branch": args.branch} if args.branch else {}),
        },
    )


_DURATION_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def _parse_duration(text: str) -> int:
    """`5d`, `12h`, `30m`, `45s` into seconds, and nothing else.

    A unit is required. `--older-than 5` asks "older than five *what*", and the
    difference between the readings is a year of transcripts, so this refuses
    rather than choosing. Nix's `--delete-older-than` takes the same shapes.
    """

    text = text.strip().lower()
    unit = _DURATION_UNITS.get(text[-1:])
    if unit is None or len(text) < 2:
        raise SystemExit(
            f"neuman-admin: --older-than must be a duration like 5d, 12h, 30m or "
            f"45s (got {text!r})"
        )
    try:
        value = int(text[:-1])
    except ValueError:
        raise SystemExit(
            f"neuman-admin: --older-than must be a whole number of days, hours, "
            f"minutes or seconds (got {text!r})"
        ) from None
    if value <= 0:
        raise SystemExit("neuman-admin: --older-than must be positive; 0 is not a duration")
    return value * unit


def _print_purge_plan(plan: Any) -> None:
    sessions = plan["sessions"]
    print(f"{len(sessions)} session(s) with no activity in the window:")
    for row in sessions:
        title = (row.get("title") or "").strip() or "(untitled)"
        print(
            f"  {row['id'][:12]}  {row['account']}  "
            f"{(row.get('last_activity') or '')[:19]}  {title[:60]}"
        )
    print(f"  {plan.get('events', 0):,} event row(s) go with them, and their images.")


def _session_purge(client: AdminClient, args: Any) -> Any:
    """List what a purge would remove, confirm, then remove exactly that.

    Two requests, deliberately: the plan names the sessions and the apply takes
    the ids it named, so what an operator approved is what is deleted even if a
    session closes (or the hourly retention pass runs) in between. A single
    request could not bind the approval to a set.
    """

    seconds = _parse_duration(args.older_than)
    plan = client.request(
        "POST",
        "/admin/sessions/purge/plan",
        body={"older_than_seconds": seconds, "account": args.account},
    )
    sessions = plan.get("sessions") or []
    if not sessions:
        return {"purged": 0, "events": 0, "images": 0, "bytes": 0}

    # `--json` is for scripts, and a script that did not pass `--yes` gets the
    # plan and nothing happens. It is the machine-readable preview the
    # confirmation prompt is for a human.
    if args.as_json and not args.yes:
        return plan

    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit(
                "neuman-admin: refusing to purge without a terminal; pass --yes "
                "to confirm non-interactively"
            )
        _print_purge_plan(plan)
        answer = input(f"Purge {len(sessions)} session(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise SystemExit("neuman-admin: aborted")
    elif not args.as_json:
        _print_purge_plan(plan)

    return client.request(
        "POST",
        "/admin/sessions/purge/apply",
        body={"session_ids": [row["id"] for row in sessions]},
    )


def _password(source: str | None, *, confirm: bool) -> str:
    """A password from a file or the terminal, never from argv."""

    if source:
        return Path(source).read_text(encoding="utf-8").rstrip("\n")
    value = getpass.getpass("password: ")
    if confirm and getpass.getpass("repeat: ") != value:
        raise SystemExit("neuman-admin: passwords do not match")
    if len(value) < 12:
        raise SystemExit("neuman-admin: password must be at least 12 characters")
    return value


def _bootstrap(client: AdminClient, args: Any) -> Any:
    body: dict[str, Any] = {"password": _password(args.password_file, confirm=True)}
    if args.name:
        body["name"] = args.name
    if args.force:
        body["force"] = True
    return client.request("POST", "/admin/bootstrap", body=body)


def _account_list(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/accounts")


def _account_create(client: AdminClient, args: Any) -> Any:
    return client.request(
        "POST",
        "/admin/accounts",
        body={
            "name": args.account,
            "password": _password(args.password_file, confirm=True),
            "admin": args.admin,
            "must_change_password": not args.no_change,
            "roles": args.role,
            "groups": args.group,
        },
    )


def _account_passwd(client: AdminClient, args: Any) -> Any:
    return client.request(
        "POST",
        f"/admin/accounts/{args.account}/password",
        body={
            "password": _password(args.password_file, confirm=True),
            "must_change_password": not args.no_change,
        },
    )


def _account_disabled(client: AdminClient, args: Any, disabled: bool) -> Any:
    return client.request(
        "PATCH", f"/admin/accounts/{args.account}", body={"disabled": disabled}
    )


def _account_disable(client: AdminClient, args: Any) -> Any:
    return _account_disabled(client, args, True)


def _account_enable(client: AdminClient, args: Any) -> Any:
    return _account_disabled(client, args, False)


def _account_admin(client: AdminClient, args: Any) -> Any:
    return client.request("PATCH", f"/admin/accounts/{args.account}", body={"admin": True})


def _account_unadmin(client: AdminClient, args: Any) -> Any:
    return client.request("PATCH", f"/admin/accounts/{args.account}", body={"admin": False})


def _group_list(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/groups")


def _group_create(client: AdminClient, args: Any) -> Any:
    return client.request("POST", "/admin/groups", body={"name": args.group})


def _group_delete(client: AdminClient, args: Any) -> Any:
    return client.request("DELETE", f"/admin/groups/{args.group}")


def _group_add(client: AdminClient, args: Any) -> Any:
    return client.request(
        "POST", f"/admin/groups/{args.group}/members", body={"account": args.account}
    )


def _group_remove(client: AdminClient, args: Any) -> Any:
    return client.request("DELETE", f"/admin/groups/{args.group}/members/{args.account}")


def _baseline_show(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/baseline")


def _baseline_set(client: AdminClient, args: Any) -> Any:
    document = _read_json(args.source)
    # `--json baseline show` prints the response, the document under `baseline`
    # with the built-in values beside it, and the two are the export/import pair,
    # so what one writes the other reads. Unambiguous: a baseline document has no
    # `baseline` key of its own.
    if isinstance(document, dict) and "baseline" in document:
        document = document["baseline"]
    return client.request("PUT", "/admin/baseline", body={"baseline": document})


def _prompt_show(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/prompt")


def _prompt_set(client: AdminClient, args: Any) -> Any:
    text = sys.stdin.read() if args.source == "-" else Path(args.source).read_text()
    return client.request("PUT", "/admin/prompt", body={"text": text})


def _events(client: AdminClient, args: Any) -> Any:
    params = {"limit": str(args.limit)}
    if args.level:
        params["level"] = args.level
    return client.request("GET", "/admin/events", params=params)


def _model_list(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/models")


def _status(client: AdminClient, args: Any) -> Any:
    del args
    return client.request("GET", "/admin/status")


def _usage(client: AdminClient, args: Any) -> Any:
    return client.request("GET", f"/admin/usage/{args.account}")


def _backup(client: AdminClient, args: Any) -> Any:
    # `read_timeout=None`: the route runs `pg_dump` inside the request, and a
    # dump of a real database outlives any deadline worth setting (N166).
    return client.request(
        "POST",
        "/admin/backup",
        body={"destination": args.destination},
        read_timeout=None,
    )


def _restore(client: AdminClient, args: Any) -> Any:
    # Same as `_backup`, for `pg_restore`.
    return client.request(
        "POST",
        "/admin/restore",
        body={"source": args.source},
        read_timeout=None,
    )


def _read_json(source: str) -> Any:
    """A JSON fragment from a file or stdin.

    Parsed here rather than posted blind so a typo is a local error naming the
    line, instead of a 400 from the far side of a socket.
    """

    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # `from None`: the admin wants the line number, not a traceback
        # through the JSON decoder.
        raise SystemExit(f"neuman-admin: {source}: invalid JSON: {exc}") from None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    client = AdminClient(args.socket)

    try:
        payload = args.handler(client, args)
    except Unreachable as exc:
        print(f"neuman-admin: {exc}", file=sys.stderr)
        print(
            "neuman-admin: is neuman-server running, and is this account in "
            "the 'neuman' group?",
            file=sys.stderr,
        )
        return EX_UNAVAILABLE
    except AdminError as exc:
        print(f"neuman-admin: {exc}", file=sys.stderr)
        return EX_FAILURE

    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(args.command, getattr(args, "subcommand", None), payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
