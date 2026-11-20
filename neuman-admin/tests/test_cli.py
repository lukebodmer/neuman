"""The admin CLI: argument shapes, exit codes, and output.

The test that matters most here is `test_every_command_targets_a_real_route`,
which checks this CLI's requests against the server's actual router. A CLI is
a second description of an API, and two descriptions drift — a renamed path
would otherwise be found by an admin, at the moment they needed the
command, with a 404 and no hint about which side was wrong.

Everything else is a fake: these tests are about the CLI, and a CLI that only
works against a live appliance cannot be tested at all.
"""

from __future__ import annotations

import json

import pytest

from neuman_admin.cli import EX_FAILURE, EX_UNAVAILABLE, build_parser, main
from neuman_admin.client import TIMEOUT_S, AdminError, Unreachable
from neuman_admin.format import render


class Recorder:
    """Stands in for AdminClient, capturing the request instead of sending it."""

    def __init__(self, response=None, raises=None):
        self.response = response if response is not None else {}
        self.raises = raises
        self.calls = []
        # Kept beside `calls` rather than inside them: the existing assertions
        # compare a whole call dict, and a deadline is a property of how the
        # request was sent, not of what was asked for.
        self.timeouts = []

    def request(self, method, path, *, body=None, params=None, read_timeout=TIMEOUT_S):
        self.calls.append({"method": method, "path": path, "body": body, "params": params})
        self.timeouts.append(read_timeout)
        if self.raises is not None:
            raise self.raises
        return self.response


@pytest.fixture
def run(monkeypatch):
    """Run the CLI against a Recorder; return (exit_code, recorder, stdout)."""

    def go(argv, response=None, raises=None, capsys=None):
        recorder = Recorder(response, raises)
        monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: recorder)
        code = main(argv)
        return code, recorder

    return go


# -- the wire ---------------------------------------------------------------


def all_commands(fragment: str = "-") -> list[list[str]]:
    """Every leaf command, with plausible arguments.

    Written out rather than derived, because argparse cannot tell us what a
    positional argument should contain — but the *set* is checked against the
    parser below, so a new command that is not listed here fails the suite
    rather than going untested.
    """

    return [
        ["policy", "show", "alice"],
        ["policy", "set", "alice", fragment],
        ["policy", "clear", "alice"],
        ["role", "rename", "alice", "full-access"],
        ["grant", "list"],
        ["grant", "add", "eng", "engineer"],
        ["grant", "remove", "eng", "engineer"],
        ["audit", "search", "hello"],
        ["audit", "verify"],
        ["audit", "reindex"],
        ["session", "list"],
        ["session", "branches", "abc123"],
        ["session", "kinds", "abc123"],
        ["session", "events", "abc123"],
        ["session", "event", "abc123", "7"],
        ["session", "purge", "--older-than", "5d"],
        ["bootstrap", "--name", "alice", "--password-file", fragment],
        ["account", "list"],
        ["account", "create", "alice", "--password-file", fragment],
        ["account", "passwd", "alice", "--password-file", fragment],
        ["account", "disable", "alice"],
        ["account", "enable", "alice"],
        ["account", "admin", "alice"],
        ["account", "unadmin", "alice"],
        ["group", "list"],
        ["group", "create", "eng"],
        ["group", "delete", "eng"],
        ["group", "add", "eng", "alice"],
        ["group", "remove", "eng", "alice"],
        ["baseline", "show"],
        ["baseline", "set", fragment],
        ["prompt", "show"],
        ["prompt", "set", fragment],
        ["events"],
        ["model", "list"],
        ["status"],
        ["usage", "alice"],
        ["backup", "/var/backups/neuman.sql"],
        ["restore", "/var/backups/neuman-2026-09-21"],
    ]


class Scripted:
    """An AdminClient that answers each request from a list, in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, *, body=None, params=None):
        self.calls.append({"method": method, "path": path, "body": body, "params": params})
        return self.responses.pop(0)


def _plan(*ids):
    return {
        "sessions": [
            {
                "id": session_id,
                "account": "alice",
                "title": f"session {session_id}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_activity": "2026-01-02T00:00:00+00:00",
            }
            for session_id in ids
        ],
        "count": len(ids),
        "events": 7 * len(ids),
    }


@pytest.mark.parametrize("wrapped", [True, False])
def test_baseline_set_reads_what_baseline_show_writes(run, tmp_path, wrapped):
    """Export and import are a pair: `--json baseline show > f; baseline set f`."""

    document = {"schema_version": 2, "defaults": {}, "roles": {}, "users": []}
    exported = tmp_path / "baseline.json"
    exported.write_text(
        json.dumps(
            {"baseline": document, "built_in": {"network": "none"}} if wrapped else document
        ),
        encoding="utf-8",
    )

    code, recorder = run(["baseline", "set", str(exported)])

    assert code == 0
    assert recorder.calls[0]["body"] == {"baseline": document}, recorder.calls


def test_account_create_carries_roles_and_groups(run, tmp_path):
    """Onboarding is one request: the account, its roles and its groups."""

    password = tmp_path / "password"
    password.write_text("correct horse battery", encoding="utf-8")

    code, recorder = run(
        [
            "account",
            "create",
            "alice",
            "--role",
            "reader",
            "--role",
            "engineer",
            "--group",
            "eng",
            "--password-file",
            str(password),
        ]
    )

    assert code == 0
    assert recorder.calls == [
        {
            "method": "POST",
            "path": "/admin/accounts",
            "body": {
                "name": "alice",
                "password": "correct horse battery",
                "admin": False,
                "must_change_password": True,
                "roles": ["reader", "engineer"],
                "groups": ["eng"],
            },
            "params": None,
        }
    ]


def test_purge_applies_exactly_the_ids_the_plan_named(monkeypatch):
    """Two calls: the plan, then the ids it listed — never a re-evaluated cutoff.

    Binding to the ids is what makes "you approved this" true even if a session
    closes, or the hourly retention pass runs, between the listing and the yes.
    """

    scripted = Scripted([_plan("aaaa1111", "bbbb2222"), {"purged": 2, "events": 14}])
    monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: scripted)

    assert main(["session", "purge", "--older-than", "5d", "--yes"]) == 0
    assert [call["path"] for call in scripted.calls] == [
        "/admin/sessions/purge/plan",
        "/admin/sessions/purge/apply",
    ]
    assert scripted.calls[0]["body"] == {"older_than_seconds": 432000, "account": None}
    assert scripted.calls[1]["body"] == {"session_ids": ["aaaa1111", "bbbb2222"]}


def test_purge_asks_and_an_n_deletes_nothing(monkeypatch):
    """The prompt is the last guard; declining sends no apply at all."""

    import types

    scripted = Scripted([_plan("aaaa1111")])
    monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: scripted)
    monkeypatch.setattr(
        "neuman_admin.cli.sys.stdin", types.SimpleNamespace(isatty=lambda: True)
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    with pytest.raises(SystemExit):
        main(["session", "purge", "--older-than", "5d"])
    assert [call["path"] for call in scripted.calls] == ["/admin/sessions/purge/plan"]


def test_purge_confirms_then_applies(monkeypatch):
    import types

    scripted = Scripted([_plan("aaaa1111"), {"purged": 1, "events": 7}])
    monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: scripted)
    monkeypatch.setattr(
        "neuman_admin.cli.sys.stdin", types.SimpleNamespace(isatty=lambda: True)
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert main(["session", "purge", "--older-than", "5d"]) == 0
    assert len(scripted.calls) == 2


def test_purge_without_a_terminal_refuses_to_act(monkeypatch):
    """A piped `neuman-admin session purge` must not delete on an unread EOF."""

    import types

    scripted = Scripted([_plan("aaaa1111")])
    monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: scripted)
    monkeypatch.setattr(
        "neuman_admin.cli.sys.stdin", types.SimpleNamespace(isatty=lambda: False)
    )

    with pytest.raises(SystemExit):
        main(["session", "purge", "--older-than", "5d"])
    assert len(scripted.calls) == 1


def test_purge_json_without_yes_is_a_preview(monkeypatch, capsys):
    """`--json` is for scripts, and a script without `--yes` purges nothing."""

    scripted = Scripted([_plan("aaaa1111")])
    monkeypatch.setattr("neuman_admin.cli.AdminClient", lambda _path: scripted)

    assert main(["--json", "session", "purge", "--older-than", "5d"]) == 0
    assert len(scripted.calls) == 1
    assert "aaaa1111" in capsys.readouterr().out


def test_every_command_targets_a_real_route(run, tmp_path):
    """The CLI and the server must agree on every path.

    Checked against the server's own router rather than a copy of it, so a
    renamed route breaks this test instead of breaking an admin.
    """

    pytest.importorskip("neuman_server", reason="server not on path")
    from neuman_server.composition.adminapi import admin_routes

    known = {(m, r.path) for r in admin_routes() for m in (r.methods or {"GET"})}

    # `policy set` reads its fragment from disk before sending anything.
    fragment = tmp_path / "overlay.json"
    fragment.write_text("{}")

    for argv in all_commands(str(fragment)):
        _code, recorder = run(argv, response={})
        assert len(recorder.calls) == 1, argv
        call = recorder.calls[0]

        # Substitute back to the templated form the router knows.
        path = call["path"]
        for value in ("alice", "big"):
            path = path.replace(f"/{value}", "/{name}")
        path = path.replace("/usage/{name}", "/usage/{account}")
        path = path.replace("/admin/groups/eng", "/admin/groups/{group}")
        path = path.replace("/sessions/abc123", "/sessions/{session_id}")

        assert (call["method"], path) in known, (
            f"{argv} sends {call['method']} {call['path']}, which is not a route "
            f"the server serves"
        )


def _declared(parser) -> dict:
    """Every top-level command, mapped to its subcommands or None if it has none."""

    import argparse

    found = {}
    for action in parser._subparsers._group_actions:
        for name, sub in action.choices.items():
            # Only a subparser action is a command level. An option with
            # `choices` (e.g. `events --level`) is not one, and treating it as
            # one made `events` look like a group with no leaf of its own.
            nested = [a for a in sub._actions if isinstance(a, argparse._SubParsersAction)]
            found[name] = list(nested[0].choices) if nested else None
    return found


def test_the_command_list_covers_every_subcommand():
    """Guards the list above: a new command must be exercised, not just added.

    Without this, `all_commands` silently stops being "every command" the
    first time somebody adds one.
    """

    parser = build_parser()

    flat = {name for name, sub in _declared(parser).items() if sub is None}
    tested = {(argv[0],) if argv[0] in flat else (argv[0], argv[1]) for argv in all_commands()}

    declared = set()
    for name, children in _declared(parser).items():
        if children is None:
            declared.add((name,))
        else:
            declared.update((name, child) for child in children)

    missing = declared - tested
    assert not missing, f"commands with no test: {sorted(missing)}"


def test_reads_do_not_send_a_body(run):
    for argv in (["policy", "show", "alice"], ["grant", "list"], ["status"]):
        _code, recorder = run(argv, response={})
        assert recorder.calls[0]["body"] is None, argv


def test_audit_search_passes_its_filters(run):
    _code, recorder = run(
        [
            "audit",
            "search",
            "login",
            "--actor",
            "root",
            "--action",
            "policy.set",
            "--limit",
            "5",
        ],
        response={"hits": []},
    )
    params = recorder.calls[0]["params"]
    assert params == {"q": "login", "limit": "5", "actor": "root", "action": "policy.set"}


def test_a_policy_fragment_is_read_from_a_file(run, tmp_path, monkeypatch):
    fragment = {"tools": ["fs_read"]}
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(fragment))

    _code, recorder = run(["policy", "set", "alice", str(path)], response={})
    assert recorder.calls[0]["body"] == fragment


def test_malformed_json_fails_locally_without_a_request(run, tmp_path):
    """A typo should name the line, not come back as a 400 from a socket."""

    path = tmp_path / "bad.json"
    path.write_text("{not json")

    with pytest.raises(SystemExit) as caught:
        run(["policy", "set", "alice", str(path)])
    assert "invalid JSON" in str(caught.value)


# -- exit codes -------------------------------------------------------------


def test_an_unreachable_socket_is_not_a_refusal(run, capsys):
    """69 and 1 mean different afternoons: check the service, or your authority."""

    code, _recorder = run(["status"], raises=Unreachable("no socket"))
    assert code == EX_UNAVAILABLE

    err = capsys.readouterr().err
    assert "neuman' group" in err, "the likely cause must be named"


def test_a_refusal_exits_one_and_repeats_the_servers_reason(run, capsys):
    code, _recorder = run(
        ["policy", "clear", "alice"],
        raises=AdminError(403, {"error": "forbidden", "detail": "not an admin"}),
    )
    assert code == EX_FAILURE
    assert "not an admin" in capsys.readouterr().err


def test_success_exits_zero(run):
    code, _recorder = run(["status"], response={"ok": True})
    assert code == 0


def test_json_output_is_the_servers_payload(run, capsys):
    payload = {"account": "alice", "policy": {"tools": ["fs_read"]}}
    run(["--json", "policy", "show", "alice"], response=payload)
    assert json.loads(capsys.readouterr().out) == payload


# -- formatting -------------------------------------------------------------


def test_an_unknown_shape_prints_as_json_rather_than_guessing():
    """Dropping a field silently is worse than printing raw JSON."""

    payload = {"surprising": ["a", "b"]}
    assert json.loads(render("policy", "show", payload)) == payload


def test_a_broken_audit_chain_cannot_read_as_success():
    """The one result that must never be mistaken for a pass."""

    out = render("audit", "verify", {"ok": False, "broken_at": 41})
    assert "BROKEN" in out
    assert "41" in out


def test_an_intact_chain_says_so():
    out = render("audit", "verify", {"ok": True, "checked": 100})
    assert "intact" in out
    assert "BROKEN" not in out


def test_an_empty_table_is_not_a_blank_line():
    assert render("grant", "list", {"grants": []}) == "(none)"


def test_grants_render_as_a_table():
    out = render("grant", "list", {"grants": [{"group": "eng", "role": "engineer"}]})
    assert "GROUP" in out and "eng" in out and "engineer" in out


def test_policy_show_names_where_each_part_came_from():
    out = render(
        "policy",
        "show",
        {
            "account": "alice",
            "policy": {"tools": ["fs_read"], "auto_accept": "safe"},
            "reach": ["/srv/eng"],
            "provenance": {"tools": "role:engineer"},
        },
    )
    assert "alice" in out
    assert "fs_read" in out
    assert "/srv/eng" in out
    # "Why can alice do this" is answered here or nowhere.
    assert "role:engineer" in out


# -- reachability -----------------------------------------------------------


def test_an_unreadable_socket_directory_is_unreachable_not_a_crash(tmp_path):
    """The ordinary case for an account outside the `neuman` group.

    The admin directory is 0750, so `Path.exists()` raises PermissionError
    rather than returning False — and an unhandled traceback is the wrong
    answer to "you may not reach this". Found in `vm-appliance`, where the
    non-admin got a traceback and exit 1 instead of a refusal and 69.
    """

    import os

    from neuman_admin.client import AdminClient

    closed = tmp_path / "closed"
    closed.mkdir(mode=0o700)
    sock = closed / "admin.sock"
    os.chmod(closed, 0o000)
    try:
        client = AdminClient(sock)
        with pytest.raises(Unreachable):
            client.request("GET", "/admin/status")
    finally:
        # Restore so tmp_path cleanup can remove it.
        os.chmod(closed, 0o700)


def test_a_missing_socket_is_unreachable(tmp_path):
    from neuman_admin.client import AdminClient

    client = AdminClient(tmp_path / "absent.sock")
    with pytest.raises(Unreachable):
        client.request("GET", "/admin/status")


def test_a_restore_leads_with_the_chain_result():
    """The field that can be bad must not be the one a reader scrolls past.

    A restore reports "it worked" and "the record is not intact" in the same
    object, and the second is the one that matters.
    """

    out = render(
        "restore",
        None,
        {
            "source": "/backup",
            "database_loaded": True,
            "overlay_files": ["alice.json"],
            "chain_ok": False,
            "chain_checked": 9,
            "chain_first_bad_seq": 4,
        },
    )
    assert out.splitlines()[0].startswith("CHAIN BROKEN"), out
    assert "4" in out


def test_a_clean_restore_says_the_chain_is_intact():
    out = render(
        "restore",
        None,
        {
            "source": "/backup",
            "database_loaded": True,
            "overlay_files": [],
            "chain_ok": True,
            "chain_checked": 9,
            "chain_first_bad_seq": None,
        },
    )
    assert "intact" in out
    assert "BROKEN" not in out
    assert "(none)" in out, "an empty overlay list must say so"


# -- vision ------------------------------------------------------------------


def test_the_cli_cannot_write_the_model_catalog():
    """`model set` and `model remove` are gone, and must not come back quietly.

    The catalog is written through the administration page, whose preset
    routes hold an entry to its facts and file a provider's key, or whole with
    `baseline set`. A CLI write that did neither would be a third way to the
    same row, and the one that skipped both checks.

    `list` stays: an entry that resolves wrongly looks exactly like one that
    works, and this is the surface that tells them apart.
    """

    parser = build_parser()
    models = _declared(parser)["model"]

    assert models == ["list"], models


def test_the_model_table_shows_which_models_can_see(run, capsys):
    """Listing must reveal it, or a failed registration looks like a success.

    This is what made the deployment confusing: `model list` returned every other
    column, so there was no way to tell a model registered with vision from one
    without it — the server knew and the admin could not find out.
    """

    run(
        ["model", "list"],
        response={
            "models": [
                {
                    "name": "seer",
                    "endpoint": "https://example.test",
                    "provider_model": "provider/seer",
                    "vision": True,
                },
                {
                    "name": "reader",
                    "endpoint": "https://example.test",
                    "provider_model": "provider/reader",
                    "vision": False,
                },
            ]
        },
    )

    out = capsys.readouterr().out
    assert "VISION" in out, out
    seer = next(line for line in out.splitlines() if "seer" in line)
    reader = next(line for line in out.splitlines() if "reader" in line)
    assert "yes" in seer, seer
    assert "yes" not in reader, reader


def test_the_dump_commands_wait_for_the_dump(run):
    """`pg_dump`/`pg_restore` run inside the request, so they get no deadline.

    30 seconds is right for a local socket that either answers or does not, and
    wrong for a route whose work is a whole database: the command reported
    "cannot reach ... timed out" while the dump carried on server-side, and the
    natural retry raced the first attempt into one file (N166). The connect
    timeout stays, which is what actually distinguishes a missing server.
    """

    for argv in (
        ["backup", "/var/backups/neuman"],
        ["restore", "/var/backups/neuman-2026-09-21"],
    ):
        _code, recorder = run(argv)
        assert recorder.timeouts[-1] is None, argv

    # The other half: an ordinary command keeps the short deadline, so the
    # exemption above cannot leak into the commands that need it.
    _code, recorder = run(["account", "list"])
    assert recorder.timeouts[-1] == TIMEOUT_S
