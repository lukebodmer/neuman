"""The shipped tool set, loaded and gated as the appliance will load and gate it.

`tests/test_registry.py` proves the *loader* is correct against synthetic
manifests. This file proves the *manifests we ship* are correct: it loads the
real `tools/` directory through the real loader and asserts the properties that
make the tool set safe rather than merely well-formed.

Worth stating why this is a separate file and not more cases in the loader's
tests: these fail when someone edits a manifest, not when someone edits the
loader, and the fix is in `tools/` rather than in `src/`. Keeping them apart
means the failure names the thing that broke.

The registry is also the one contract with two installations (docs/decisions.md
D3): the server's registry and the client's own manifest set, which the
client's suite loads from the same `tools/` tree. These tests pin the
vocabulary both agree on — names, needs, limits — because a manifest that
parses for one and not the other is a dispatch that fails on the far side.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neuman_server.adapters.registry import load_registry
from neuman_server.decision.harness.loop import Harness
from neuman_server.decision.harness.prompt import tool_schemas
from neuman_server.decision.policy import gate
from neuman_server.decision.policy.compose import EffectivePolicy

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"

# Handlers registered in composition/http/ws.py::_server_native_handlers.
# Named here rather than imported because importing composition from a decision
# test would invert the layering the suite exists to protect.
REGISTERED_SERVER_NATIVE = frozenset({"session_query"})


@pytest.fixture(scope="module")
def registry():
    return load_registry(TOOLS_DIR)


@pytest.fixture(scope="module")
def names(registry):
    return tuple(registry.names())


def policy(**overrides) -> EffectivePolicy:
    base = {"auto_accept": "safe"}
    base.update(overrides)
    return EffectivePolicy(**base)


def test_the_shipped_registry_loads(registry):
    """The appliance refuses to start on an unloadable registry, so this is a boot test."""

    assert len(registry) >= 1


def test_every_manifest_matches_its_directory(registry):
    """The client resolves tools by directory; a mismatch means the two disagree (D3)."""

    for name in registry.names():
        assert (TOOLS_DIR / name / "manifest.toml").is_file()


# The shipped manifests that hide from the tool chooser, named one at a time.
#
# The previous version of this test asserted the set was *empty*, and said why
# that mattered: a shipped manifest growing the flag is a decision, not a
# manifest edit. The decision has since been made once — `env_probe` is
# dispatched by a route and by the harness, and an ungranted row for it invites
# an operator to grant a capability that is not one (D33) — so the rule is now
# an allowlist rather than a prohibition. Adding a name here is still the
# decision; the test exists so it cannot be made by accident.
SHIPPED_DIAGNOSTICS = {"env_probe"}


def test_only_the_named_tools_hide_from_the_chooser(registry):
    """`diagnostic` is presentation, and the shipped set that uses it is closed.

    The four that originally shipped — `escapee`, `hog`, `noisy`, `ops_probe` —
    proved sandbox mechanisms that are gone, and they went with them. What is
    left is a tool a *person* never picks because something else dispatches it.

    A tool reaching this list by accident is the failure worth catching: the
    flag hides a row, so a manifest that grows one quietly disappears from the
    chooser and nothing on screen says it exists.
    """

    declared = {name for name in registry.names() if registry.get(name).diagnostic}
    assert declared == SHIPPED_DIAGNOSTICS


def test_a_hidden_tool_is_granted_to_nobody(registry):
    """Hiding a row and granting a capability must not meet.

    `diagnostic` is presentation only — the gate ignores it — so a tool that is
    both granted and hidden would be a live capability with no control over it
    and nothing on screen saying it is there. `/api/tools` guards the display
    side by listing a *granted* diagnostic anyway; this guards the other end, by
    holding the shipped set to tools no baseline names.
    """

    for name in SHIPPED_DIAGNOSTICS:
        decision = gate.check(name, policy(), registry)
        assert not decision.allowed, (
            f"{name} is hidden from the chooser and granted by the default "
            "policy; one of those is wrong"
        )
        assert decision.reason == gate.NOT_GRANTED, decision


def test_every_shipped_tool_has_a_summary(registry):
    """The tool chooser shows one line per tool, and this is that line (D50).

    `summary` is optional in the schema so the loader stays tolerant, which
    means nothing else would catch a new tool shipping without one — it would
    silently fall back to its `description`, and a description is a paragraph
    written for the model. That fallback exists for robustness, not as the
    normal case, so the shipped set is held to the stricter rule here.
    """

    for name in registry.names():
        summary = registry.get(name).summary
        assert summary, f"{name}: no summary; the chooser would show its whole description"
        # A sentence, not prose. The schema caps the length; this catches the
        # other way a summary stops being a label.
        assert summary.count(".") <= 1, f"{name}: summary is more than one sentence"


def test_client_tools_declare_exec_and_server_native_do_not(registry):
    """`kind` must be true of where the tool actually executes (D13)."""

    for name in registry.names():
        manifest = registry.get(name)
        if manifest.kind == "client" and manifest.daemon:
            # Answered by the daemon itself (D98): there is no program.
            assert manifest.exec is None, f"{name}: daemon tool declares exec"
        elif manifest.kind == "client":
            assert manifest.exec, f"{name}: client tool without exec"
        else:
            assert manifest.exec is None, f"{name}: {manifest.kind} declares exec"


def test_server_native_tools_all_have_handlers(registry):
    """A server-native manifest with no handler fails closed at dispatch (D5).

    Both directions matter: an unhandled manifest is a tool the model is offered
    and cannot use, and an unreferenced handler is dead code that looks live.
    """

    declared = {n for n in registry.names() if registry.get(n).kind == "server-native"}
    assert declared - REGISTERED_SERVER_NATIVE == set(), "declared server-native, no handler"
    assert REGISTERED_SERVER_NATIVE - declared == set(), "handler with no manifest"


def test_server_native_tools_need_no_reach(registry):
    """They never leave the core, so requiring reach would deny them for no reason."""

    for name in registry.names():
        manifest = registry.get(name)
        if manifest.kind == "server-native":
            assert manifest.needs == frozenset(), f"{name}: server-native declares needs"


def test_critical_asks_in_every_mode(registry):
    """A critical tool asks under `never`, `safe` *and* `yolo`.

    This used to assert `critical implies mutating`, on the grounds that
    `_needs_consent` read `mutating` under `safe` and `critical` under `yolo`,
    so a critical-but-not-mutating tool would skip approval on the *stricter*
    setting. That reasoning was right about the hazard and wrong about the code:
    `safe` returns `mutating or critical`, so there is no gap — and the old
    assertion forbade a legitimate shape, a read grave enough to always ask.

    No shipped tool is critical now, so this uses a manifest shaped like one:
    iterating the shipped set would assert nothing at all.
    """

    from fakes import manifest

    from neuman_server.decision.harness.loop import Harness

    harness = Harness.__new__(Harness)
    grave = manifest("grave", critical=True)
    for mode in ("never", "safe", "yolo"):
        asked = Harness._needs_consent(harness, policy(auto_accept=mode), grave)
        assert asked, f"critical but not asked about under {mode}"


def test_read_only_tools_are_not_mutating(registry):
    """A tool that only asks for read reach cannot be changing anything."""

    for name in registry.names():
        manifest = registry.get(name)
        if manifest.needs == frozenset({"read"}):
            assert not manifest.mutating, f"{name}: needs only read but claims to mutate"


def test_mutating_tools_require_write_reach(registry):
    """Anything that changes a file must declare the reach that lets it.

    Otherwise the gate would admit it for a read-only user and the denial would
    come from the kernel instead of from policy — a confusing failure, and one
    that arrives after the audit event says the dispatch was allowed.
    """

    for name in registry.names():
        manifest = registry.get(name)
        if manifest.mutating and manifest.kind != "server-native":
            assert "write" in manifest.needs, f"{name}: mutating without write reach"


def test_a_user_with_no_reach_is_offered_only_server_native(registry, names):
    """Reach is derived from having paths, not from a separate grant.

    `session_query` survives a policy with nothing granted because it is
    server-native: it touches no filesystem, so requiring reach would deny it
    for no reason. Every other tool needs a path or a host.
    """

    offered = sorted(s["function"]["name"] for s in tool_schemas(policy(tools=names), registry))
    assert offered == ["session_query"]


def test_advertisement_equals_the_gate(registry, names):
    """Advertising a tool the gate would deny teaches the model to propose what it cannot have."""

    reader = policy(tools=names, files_read=("/srv/eng",))
    offered = {s["function"]["name"] for s in tool_schemas(reader, registry)}
    allowed = {n for n in names if gate.check(n, reader, registry).allowed}
    # Narrower, never wider: a reader can start nothing that becomes a job, so
    # the job tools are withheld from the advertisement while the gate would
    # still admit them (D98).
    assert offered == allowed - {"job_output", "job_kill"}


def test_the_job_tools_are_offered_only_beside_a_tool_that_can_become_a_job(registry, names):
    """Two schemas on every request are context; with nothing to follow they buy nothing."""

    writer = policy(tools=names, files_read=("/srv/eng",), files_write=("/srv/eng",))
    offered = {s["function"]["name"] for s in tool_schemas(writer, registry)}
    assert {"shell", "job_output", "job_kill"} <= offered

    no_shell = {
        s["function"]["name"] for s in tool_schemas(writer, registry, ["fs_read", "job_output"])
    }
    assert no_shell == {"fs_read"}


def test_a_client_that_lacks_a_tool_is_not_advertised_it(registry, names):
    """The other half of the handshake: the core asks what the machine can run.

    The client says which tools it has when it connects, and the advertisement
    is intersected with that. Otherwise the model is told about a tool the
    machine in front of it has no program for, and spends a turn discovering
    it.
    """

    reader = policy(tools=names, files_read=("/srv/eng",))
    offered = {
        schema["function"]["name"]
        for schema in tool_schemas(reader, registry, available=frozenset({"fs_read"}))
    }
    assert offered == {"fs_read"}


def test_network_tools_need_the_network_grant(registry, names):
    """No grant, no tool: the gate is the only thing `needs = ["network"]` does."""

    withheld = policy(tools=names, network="none")
    for name in names:
        if "network" in registry.get(name).needs:
            assert not gate.check(name, withheld, registry).allowed

    # Read and write as well, because `git` needs all three: what is asserted
    # here is the network half, and a tool refused for the other half would make
    # this pass for the wrong reason.
    granted = policy(
        tools=names,
        files_read=("/srv/eng",),
        files_write=("/srv/eng",),
        network="any",
    )
    for name in names:
        if "network" in registry.get(name).needs:
            assert gate.check(name, granted, registry).allowed


def test_yolo_still_stops_at_anything_critical():
    """ "Yolo" is about convenience, not consent to something grave (D23).

    No shipped tool is critical — `neuman_admin` was the one, and it went with
    the admin socket; `shell` and `python` are the operator's `yolo` to make. A
    manifest shaped like one is what this asserts against, because iterating an
    empty set asserts nothing.
    """

    from fakes import manifest

    harness = Harness.__new__(Harness)
    yolo = policy(auto_accept="yolo")
    assert harness._needs_consent(yolo, manifest("grave", critical=True))
    assert not harness._needs_consent(yolo, manifest("plain"))


def test_never_asks_for_everything(registry, names):
    """`auto_accept = "never"` must not have exceptions."""

    harness = Harness(
        gateway=None,
        execution=None,
        registry=registry,
        audit_repository=None,
        event_repository=None,
        usage=None,
        images=None,
        policy_for=None,
    )
    strict = policy(tools=names, auto_accept="never")
    for name in names:
        assert harness._needs_consent(strict, registry.get(name))


def test_waits_and_lifetimes_stay_under_the_contract_ceiling(registry):
    """The manifest schema caps both at an hour; the lifetime is never the shorter."""

    for name in registry.names():
        manifest = registry.get(name)
        assert 1 <= manifest.wait_ms <= manifest.lifetime_ms <= 3_600_000, name


def test_only_the_tools_whose_work_can_run_long_become_jobs(registry):
    """D98: a lifetime past the wait is a decision, per tool, not a default.

    A command, a script and a clone can do real work for longer than anyone
    should wait on it. A read, a search, a write, a fetch or a page should not
    outlive the call that asked for it — nobody will look at the result — and
    the job tools themselves must never become jobs.
    """

    detaching = {name for name in registry.names() if registry.get(name).detaches}
    assert detaching == {"git", "python", "shell"}


def test_the_job_tools_are_answered_by_the_daemon(registry):
    for name in ("job_output", "job_kill"):
        manifest = registry.get(name)
        assert manifest is not None, name
        assert manifest.kind == "client" and manifest.daemon and manifest.exec is None
        # Following and stopping your own job prompts nobody.
        assert not manifest.mutating and not manifest.critical
        assert manifest.needs == frozenset()


# -- the binaries, not the manifests ------------------------------------------


def _run_tool(name: str, args: dict, env: dict | None = None) -> tuple[int, str, str]:
    """Run a shipped tool script the way the client does: one JSON argument.

    `PYTHONPATH` carries `lib/` because the installed wrapper puts it on
    `sys.path` before running the module; without it a tool that imports the
    planner or the CDP client would fail here and pass in production.
    """

    import json
    import os
    import subprocess
    import sys

    environment = dict(os.environ, PYTHONPATH=str(TOOLS_DIR / "lib"), **(env or {}))
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "bin" / f"{name}.py"), json.dumps(args)],
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.returncode, result.stdout, result.stderr


def test_reading_a_binary_file_says_what_it_is(tmp_path):
    """ "not valid UTF-8" is true and useless.

    A model told only that a file is not text has no way to find out what it is:
    `shell` runs with the client's PATH, which on a minimal machine may hold no
    `file` and no `ls`. Asked "can you see this image", it burned two turns and
    still could not say whether the attachment was an image.
    """

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    code, _out, err = _run_tool("fs_read", {"path": str(png)})

    assert code == 1
    assert "PNG image" in err, err
    assert str(png) in err


def test_binary_identification_covers_the_formats_people_attach(tmp_path):
    cases = {
        "a.png": (b"\x89PNG\r\n\x1a\n", "PNG image"),
        "b.jpg": (b"\xff\xd8\xff\xe0", "JPEG image"),
        "c.pdf": (b"%PDF-1.7\n", "PDF document"),
        "d.zip": (b"PK\x03\x04", "ZIP archive"),
        "e.gz": (b"\x1f\x8b\x08", "gzip archive"),
        "f.webp": (b"RIFF\x00\x00\x00\x00WEBP", "WebP image"),
        "g.bin": (b"\x00\x01\x02\x03", "binary data"),
    }

    for name, (magic, expected) in cases.items():
        path = tmp_path / name
        path.write_bytes(magic + b"\xff" * 32)
        code, _out, err = _run_tool("fs_read", {"path": str(path)})
        assert code == 1, (name, err)
        assert expected in err, (name, err)


def test_a_text_file_is_still_read_normally(tmp_path):
    """The identification must not fire on files that decode fine."""

    path = tmp_path / "notes.md"
    path.write_text("hello\nworld\n", encoding="utf-8")

    code, out, err = _run_tool("fs_read", {"path": str(path)})

    assert code == 0, err
    assert "hello" in out
    assert "binary" not in err.lower()
    # A whole-file read is not a page, so it carries no continuation notice.
    assert "use offset=" not in out


def test_a_limited_read_says_where_to_continue(tmp_path):
    """A fragment that does not announce itself reads like the whole file.

    The tool bounds its own page and says where it stopped, so "is there more"
    is a fact rather than a guess — and the page is delivered inline rather than
    as a preview of a scratch file the reader would have to open.
    """

    path = tmp_path / "notes.txt"
    path.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n", encoding="utf-8")

    code, out, err = _run_tool("fs_read", {"path": str(path), "limit": 4})

    assert code == 0, err
    assert "line 4" in out
    assert "line 5" not in out
    assert "[showing lines 1-4 of 10; use offset=5 to continue]" in out

    # And the next page completes without a notice, so "keep going" terminates.
    code, out, err = _run_tool("fs_read", {"path": str(path), "offset": 5})
    assert code == 0, err
    assert "line 5" in out
    assert "line 10" in out
    assert "use offset=" not in out


def test_a_byte_limited_page_stops_on_a_line_boundary(tmp_path):
    """Never a partial line: the reader cannot tell it from the real thing."""

    path = tmp_path / "wide.txt"
    # 15001 bytes per line against the tool's 32768-byte page budget: 2 lines
    # fit, the third would cross it. Real sizes, because the tool runs in its own
    # process and a patched constant in this one would not reach it.
    path.write_text(("a" * 15_000 + "\n") * 20, encoding="utf-8")

    code, out, err = _run_tool("fs_read", {"path": str(path)})

    assert code == 0, err
    assert out.startswith(("a" * 15_000 + "\n") * 2)
    assert ("a" * 15_000 + "\n") * 3 not in out
    assert "[showing lines 1-2 of 20; use offset=3 to continue]" in out


def test_an_offset_past_the_end_names_the_count(tmp_path):
    """An empty string would hide the very number that resolves the mistake."""

    path = tmp_path / "short.txt"
    path.write_text("only\n", encoding="utf-8")

    code, _out, err = _run_tool("fs_read", {"path": str(path), "offset": 9})

    assert code == 1
    assert "offset 9 is beyond end of file (1 lines)" in err


def test_an_empty_file_says_so(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    code, out, _err = _run_tool("fs_read", {"path": str(path)})

    assert code == 0
    assert "empty" in out


def test_a_line_over_the_read_budget_shows_its_head_and_names_the_way_in(tmp_path):
    """One line larger than a whole page cannot be paged by line.

    A minified page or a document spilled to a single line is what the first
    unattended sweep had to read, and the tool used to refuse it while naming
    `fs_search` and `shell` — tools that run's envelope did not grant, so the
    answer was a dead end (N181). The head is shown, marked with the line's
    real size, and `fs_search` is named for the rest.
    """

    path = tmp_path / "oneline.txt"
    body = "x" * 230_000 + " needle\n"
    path.write_text(body, encoding="utf-8")

    code, out, err = _run_tool("fs_read", {"path": str(path)})

    assert code == 0, err
    assert out.startswith("x" * 10_000)
    assert f"[line 1 is {len(body.encode()):,} bytes, longer than one page" in out
    assert "fs_search finds text anywhere in it" in out
    # The window is the head and says the rest was not shown, so a quote from
    # it cannot be mistaken for the whole line.
    assert "needle" not in out
    assert len(out.encode()) <= 32_768


def test_searching_a_single_file_finds_matches(tmp_path):
    """A file is a valid target, not a directory that yielded nothing.

    `os.walk` on a file yields nothing, so this used to answer `(no matches)`
    with a success exit for a file that plainly contained the pattern.
    """

    path = tmp_path / "module.py"
    path.write_text("def manage():\n    force_compaction = True\n", encoding="utf-8")

    code, out, err = _run_tool("fs_search", {"path": str(path), "pattern": "force_compaction"})

    assert code == 0, err
    assert f"{path}:2:" in out
    assert "no matches" not in out


def test_searching_a_missing_path_is_an_error_not_an_empty_result(tmp_path):
    """ "No matches" is a false statement about a file that is not there."""

    missing = tmp_path / "nope.py"

    code, out, err = _run_tool("fs_search", {"path": str(missing), "pattern": "anything"})

    assert code == 1
    assert "no such file or directory" in err
    assert "no matches" not in out


def test_search_returns_newest_modified_files_first(tmp_path):
    """The manifest promises mtime order; directory order buried that."""

    import os

    old = tmp_path / "old.py"
    new = tmp_path / "new.py"
    for name in (old, new):
        name.write_text("needle\n", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    code, out, err = _run_tool("fs_search", {"path": str(tmp_path), "pattern": "needle"})

    assert code == 0, err
    assert out.index("new.py") < out.index("old.py"), out


def test_a_single_file_target_still_honours_the_glob(tmp_path):
    """A caller who asks for `*.py` and names a `.md` gets an empty result,
    not a match from a file the glob excludes."""

    path = tmp_path / "notes.md"
    path.write_text("needle\n", encoding="utf-8")

    code, out, err = _run_tool(
        "fs_search", {"path": str(path), "pattern": "needle", "glob": "*.py"}
    )

    assert code == 0, err
    assert "no matches" in out


def test_a_path_glob_scopes_the_search(tmp_path):
    """`src/**/*.py` must match on the relative path, not the basename alone.

    Matching only the filename made it silently return `(no matches)` for a
    tree that plainly contained the pattern — the same false negative a
    single-file `path` produced before it was accepted.
    """

    (tmp_path / "src" / "tools").mkdir(parents=True)
    (tmp_path / "src" / "tools" / "read.ts").write_text("needle\n", encoding="utf-8")
    (tmp_path / "other.ts").write_text("needle\n", encoding="utf-8")

    code, out, err = _run_tool(
        "fs_search", {"path": str(tmp_path), "pattern": "needle", "glob": "src/**/*.ts"}
    )

    assert code == 0, err
    assert "read.ts" in out
    assert "other.ts" not in out, out


def test_a_search_that_fills_the_output_budget_says_so(tmp_path):
    """A byte-bounded result must not read as a complete one."""

    path = tmp_path / "lines.txt"
    # Each hit prints a bounded window (~640 bytes), so it takes many of them to
    # reach the tool's 32768-byte budget: 120 do, 120 do not all fit.
    path.write_text(("x" * 4000 + " needle\n") * 120, encoding="utf-8")

    code, out, err = _run_tool(
        "fs_search", {"path": str(path), "pattern": "needle", "limit": 200}
    )

    assert code == 0, err
    assert "output budget" in out
    assert out.count("needle") < 120


def test_a_hit_inside_a_minified_line_shows_the_hit_not_the_line(tmp_path):
    """A 130 KB line printed whole is truncated before the match.

    The client stops forwarding at the manifest's cap, so an entry that starts at
    byte 0 of a minified page ends long before a hit near its end — the search
    says it found something and shows no sign of it (N181). The window is cut
    around the match, marked at the ends that were cut.
    """

    path = tmp_path / "page.html"
    path.write_text("x" * 130_000 + "needle" + "y" * 30_000 + "\n", encoding="utf-8")

    code, out, err = _run_tool(
        "fs_search", {"path": str(path), "pattern": "needle", "limit": 5}
    )

    assert code == 0, err
    [line] = out.splitlines()
    assert "needle" in line
    assert line.startswith(f"{path}:1:…")
    assert line.endswith("…")
    assert len(line) < 1_000


def test_listing_a_directory_reports_kinds(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    code, out, err = _run_tool("fs_list", {"path": str(tmp_path)})

    assert code == 0, err
    assert "d\tsub" in out
    assert "f\tfile.txt" in out


def test_a_large_directory_is_grouped_rather_than_enumerated(tmp_path):
    """Cheap to run is not the same as cheap to read (N82).

    A DICOM export of 4,080 files listed its first thousand names — `._FILE1`,
    `._FILE10`, `._FILE100` — and filled a 16k context, ending the session with
    `exceed_context_size_error`. Those names differ only in a serial number, so
    they carried about as much information as one line saying how many there
    were.

    The sizes here bracket the real failure without creating four thousand
    files: enough to cross the threshold, with two shapes and one singleton so
    grouping, counting and the single-name path are all exercised.

    It sizes itself from `SUMMARY_THRESHOLD`, so verifying it by *raising* that
    constant does not disable grouping — it asks for a directory of that many
    files. To check this test still fails when grouping regresses, disable the
    summary branch in `fs_list.py` and leave the constant alone.
    """

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "neuman_fs_list", TOOLS_DIR / "bin" / "fs_list.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["neuman_fs_list"] = module
    spec.loader.exec_module(module)

    count = module.SUMMARY_THRESHOLD + 50
    for i in range(count):
        (tmp_path / f"FILE{i}").write_text("x")
        (tmp_path / f"._FILE{i}").write_text("x")
    (tmp_path / "README.md").write_text("x")

    code, out, err = _run_tool("fs_list", {"path": str(tmp_path)})
    assert code == 0, err

    # Asserted on the line set rather than with `in out`. When this fails the
    # output is the ungrouped listing — five hundred names — and pytest builds
    # a character-level diff of it against the needle, which burns minutes of
    # CPU before reporting. A failing test has to be cheap to fail.
    # Size first, reported as a count rather than by showing the listing: when
    # grouping regresses this is five hundred names, and the number is the
    # useful signal.
    assert len(out) < 1024, (
        f"listing is {len(out)} bytes over {len(out.splitlines())} lines; "
        "a directory past the threshold was not grouped"
    )

    # The line set, not `in out`: a substring check against a multi-kilobyte
    # listing makes pytest render the whole thing into the failure.
    lines = set(out.splitlines())
    assert any(line.startswith(f"({count * 2 + 1} entries") for line in lines), (
        "the true total must be reported"
    )
    # The range is the numeric extremes, not the lexicographic ones. A plain
    # sort puts `FILE99` above `FILE249` and would report this group of 250 as
    # `FILE0 .. FILE99`, understating it by more than half — which is how the
    # bug was found, by asserting the range this test expected.
    assert f"f\tFILE#\tx {count}\t(FILE0 .. FILE{count - 1})" in lines
    assert f"f\t._FILE#\tx {count}\t(._FILE0 .. ._FILE{count - 1})" in lines
    # A name with no digits has no shape to share, so it stays itself rather
    # than becoming a group of one.
    assert "f\tREADME.md" in lines
    assert "f\tFILE7" not in lines, "individual generated names must not be listed"


def test_a_small_directory_is_still_listed_by_name(tmp_path):
    """The threshold must not change the answer for a directory people navigate.

    A listing of twenty source files is worth enumerating; grouping it would
    hide the thing the caller asked for.
    """

    for name in ("alpha.py", "beta.py", "gamma.py"):
        (tmp_path / name).write_text("x")

    code, out, err = _run_tool("fs_list", {"path": str(tmp_path)})
    assert code == 0, err
    assert out.splitlines() == ["f\talpha.py", "f\tbeta.py", "f\tgamma.py"]


def test_summarise_can_be_forced_either_way(tmp_path):
    """The threshold is a default, not a judgement about what a caller needs.

    The directory chooser depends on the `false` direction: it navigates, so it
    always wants real names however large the directory is.
    """

    for i in range(5):
        (tmp_path / f"FILE{i}").write_text("x")

    code, grouped, err = _run_tool("fs_list", {"path": str(tmp_path), "summarise": True})
    assert code == 0, err
    assert "FILE#\tx 5" in grouped

    code, named, err = _run_tool("fs_list", {"path": str(tmp_path), "summarise": False})
    assert code == 0, err
    assert named.splitlines() == [f"f\tFILE{i}" for i in range(5)]


def test_looking_at_an_image_returns_its_media_type_and_bytes(tmp_path):
    import base64

    png = tmp_path / "shot.png"
    body = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    png.write_bytes(body)

    code, out, err = _run_tool("fs_image", {"path": str(png)})

    assert code == 0, err
    media, _, data = out.strip().partition(" ")
    assert media == "image/png"
    # Round-trips: the provider gets the file, not an approximation of it.
    assert base64.b64decode(data) == body


def test_the_media_type_comes_from_the_bytes_not_the_extension(tmp_path):
    """A `.png` holding a JPEG must be labelled JPEG.

    The provider validates the data: URI against its declared type, so trusting
    the filename turns a mislabelled file into an opaque provider error.
    """

    lying = tmp_path / "actually-jpeg.png"
    lying.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)

    code, out, err = _run_tool("fs_image", {"path": str(lying)})

    assert code == 0, err
    assert out.startswith("image/jpeg "), out


def test_a_non_image_is_refused_by_fs_image(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("hello", encoding="utf-8")

    code, _out, err = _run_tool("fs_image", {"path": str(path)})

    assert code == 1
    assert "not a recognised image format" in err


def test_an_oversized_image_is_refused_before_it_is_read(tmp_path):
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024))

    code, _out, err = _run_tool("fs_image", {"path": str(big)})

    assert code == 1
    assert "too large" in err
    assert "limit" in err


# -- browse -------------------------------------------------------------------


def test_the_browser_is_still_gated_by_policy(registry):
    """It adds no reach beyond the user's own.

    The browser runs as the user and drives a Chromium on their own machine, so
    the gate is what limits it: a user whose policy grants no filesystem reach
    is not offered it. Declaring no needs would have made it reachable by anyone
    granted the name.
    """

    manifest = registry.get("browse")
    assert manifest is not None
    assert manifest.needs, "browse declares no needs, so no profile can fail to back it"
    assert manifest.mutating, (
        "browse runs model-chosen JavaScript in a page that may hold a session; "
        "declaring it non-mutating would skip approval under the default policy"
    )


def test_the_browser_does_not_claim_network_reach(registry):
    """`needs = ["network"]` would be a lie here.

    The browser uses the user's own network exactly as it would if they had
    opened it themselves. Declaring network would require the account to hold
    network reach for a tool that does not need the core to have any.
    """

    assert "network" not in registry.get("browse").needs


def test_the_cdp_client_needs_no_third_party_package():
    """The tools run on a bare interpreter, and this keeps them there.

    A `websockets` import here would be a dependency declared for the server and
    missing for the tools — N47 exactly, failing only at runtime. The client is
    hand-written for that reason, so an import creeping in would quietly undo it.
    """

    import ast
    import sys
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent / "tools" / "lib" / "browse_cdp.py"
    ).read_text()

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert "websockets" not in imported, "browse_cdp imports websockets; it must not"
    stdlib = set(sys.stdlib_module_names) | {"typing"}
    outside = sorted(imported - stdlib)
    assert not outside, f"browse_cdp imports non-stdlib modules: {outside}"


def test_units_only_write_where_the_mode_bits_allow():
    """`ReadWritePaths` lifts systemd's protection, not the filesystem's.

    The GPU sampler was given `ReadWritePaths = [ "/run/neuman" ]` and failed at
    every activation with `Permission denied` on `/run/neuman/gpu.json.new`, because
    that directory is `0755 root root` — deliberately, since the admin socket and the
    socket-activation units live there, and a directory `neuman` can write to is one
    where it could replace a socket another unit listens on. A directive cannot
    grant what the mode bits deny (N73).

    So a unit running as `neuman` that writes under `/run/neuman` must write into a
    subdirectory `tmpfiles` created for it. Asserted by pairing the two: every
    `ReadWritePaths` entry under `/run/neuman` needs a matching neuman-owned tmpfiles
    rule.
    """

    import re
    from pathlib import Path

    module_dir = Path(__file__).parent.parent.parent / "nix" / "module"
    sources = "\n".join(p.read_text() for p in module_dir.glob("*.nix"))

    # Directories tmpfiles creates as neuman-writable.
    writable = set(re.findall(r'"d (/run/neuman/[a-z-]+) 0\d{3} neuman neuman', sources))
    assert writable, "no neuman-owned runtime directories found; test would be vacuous"

    # Every path a unit claims write access to under /run/neuman.
    claimed = set(re.findall(r'ReadWritePaths = \[ "(/run/neuman[a-z/-]*)" \]', sources))
    assert claimed, "no ReadWritePaths under /run/neuman found; test would be vacuous"

    for path in claimed:
        # `/run/neuman` itself is root-owned on purpose; only the server has it, and
        # only because it creates its own socket directory beneath it.
        if path == "/run/neuman":
            continue
        assert path in writable, (
            f"{path} is claimed by a unit but no tmpfiles rule makes it "
            "neuman-writable; ReadWritePaths cannot override mode bits"
        )


def test_the_gpu_sampler_uses_the_configured_driver():
    """A second evaluation of `nvidia_x11` is not the driver the kernel is running.

    Pinning `pkgs.linuxPackages.nvidia_x11.bin` gave 595.99.02 while the running
    kernel module was 595.45.04, so `nvidia-smi` answered `Failed to initialize NVML:
    Driver/library version mismatch` and the sampler reported `available: false` on a
    machine with an RTX 3090 in it (N79).

    Asserted on the `default =` expression alone. Two earlier versions of this test
    passed against the reintroduced bug: the rejected package name appears in the
    option's own documentation and in `defaultText`, so every search over the file or
    the option block matched the prose explaining the fix. Third time hitting that
    (cf. N69, N72), and the lesson is the same each time — match the code, and prove
    it by reintroducing the bug.
    """

    import re
    from pathlib import Path

    module = (
        Path(__file__).parent.parent.parent / "nix" / "module" / "runtime.nix"
    ).read_text()

    assert "options.services.neuman.gpuTools" in module, "the gpuTools option is gone"

    # Only the default expression: from `default =` to the line that ends it.
    default = re.search(r"\n    default =\n(.*?);\n", module, re.S)
    assert default, "gpuTools has no multi-line default; this test would pass vacuously"
    expression = default.group(1)

    assert "config.hardware.nvidia.package" in expression, expression
    assert "nvidia_x11" not in expression, (
        f"the default pins its own nvidia package, which will drift from the running "
        f"driver: {expression.strip()}"
    )


def test_the_sampler_reports_why_it_found_nothing():
    """`available: false` meant four different things.

    No driver, a version mismatch, a timeout, or a sampler that never ran — and an
    operator looking at "no GPU detected" on a machine with a GPU cannot tell which.
    The reason is carried forward.
    """

    from pathlib import Path

    sampler = (
        Path(__file__).parent.parent.parent / "nix" / "module" / "gpu-sample.py"
    ).read_text()

    assert "unavailable_because" in sampler

    # Both streams, checked inside the failure branch only. `done.stdout` also appears
    # in the *success* path — `return done.stdout.decode(...)` — so searching the whole
    # file passed against a version that read only stderr. Same trap as the driver
    # test above.
    import re

    branch = re.search(r"if done\.returncode != 0:(.*?)return None, first", sampler, re.S)
    assert branch, "the failure branch is gone; this test would pass vacuously"
    body = branch.group(1)
    assert "done.stderr" in body and "done.stdout" in body, (
        "the failure reason does not read stdout; nvidia-smi prints the version "
        f"mismatch there, so this reports a useless `exit 18`: {body.strip()[:200]}"
    )


def _resolver():
    """Import the resolver from `tools/lib`, as the installed tool imports it."""

    import importlib.util
    import sys

    path = TOOLS_DIR / "lib" / "direnv_env.py"
    spec = importlib.util.spec_from_file_location("direnv_env", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because `@dataclass` resolves its string
    # annotations through `sys.modules[cls.__module__]`, which is None for a
    # module loaded by spec alone.
    sys.modules["direnv_env"] = module
    spec.loader.exec_module(module)
    return module


def _realised(tmp_path, rc_body: str, *, store_target: str | None = None):
    """A plausible nix-direnv cache: a GC-root symlink and its rc file.

    The symlink is what distinguishes nix's own work from a file someone
    committed to a repository, so a fixture that omitted it would be testing a
    shape the resolver is supposed to refuse.

    `store_target` defaults to a store path that genuinely exists, because the
    resolver also refuses a *collected* environment — a fabricated target made
    every fixture here fail that check instead of the one it was written for.
    """

    import sys

    if store_target is None:
        # This interpreter's own store path: real, and resident by definition.
        store_target = str(Path(sys.executable).resolve().parent.parent)

    cache = tmp_path / ".direnv"
    cache.mkdir()
    profile = cache / "flake-profile-abc123"
    profile.symlink_to(store_target)
    profile.with_suffix(".rc").write_text(rc_body)
    return tmp_path


def test_a_realised_environment_resolves_to_its_interpreter(tmp_path):
    """The ordinary case: PATH comes from the cache, and python is found on it.

    The interpreter is resolved by scanning the environment's PATH rather than
    by letting the shell look `python3` up, because the resolver is what decides
    which Python a directory means (D33) and because naming it in the transcript
    is what makes a later `ModuleNotFoundError` diagnosable.
    """

    import sys

    resolver = _resolver()

    # A real interpreter under a real store path, found by asking this one
    # where it lives. Fabricating a directory and relaxing `NIX_STORE` to
    # accept it would have made the fixture agree with itself while testing
    # neither the store check nor the PATH scan — the shape of self-agreeing
    # fixture that hid two of this tree's worst bugs.
    interpreter = Path(sys.executable).resolve()
    assert str(interpreter).startswith(resolver.NIX_STORE), (
        "this test needs a store-resident interpreter to scan for"
    )
    bin_dir = interpreter.parent

    project = _realised(tmp_path, f"PATH='{bin_dir}'\nPYTHONPATH='{bin_dir}'\n")
    environment = resolver.resolve(str(project))

    # Whichever of python3/python the store path offers first, resolved to an
    # executable file in the directory the cache named.
    assert environment.interpreter is not None
    assert Path(environment.interpreter).parent == bin_dir
    assert environment.env["PYTHONPATH"] == str(bin_dir)


def test_an_unrealised_environment_is_refused_by_name(tmp_path):
    """A project that declares an environment and has not built it.

    Falling back to a bare interpreter here would be the wrong kindness: the
    `flake.nix` says packages were intended, so a script running without them
    fails as a `ModuleNotFoundError` deep in its output, which reads as a
    broken tool rather than an environment nobody built. The name is what lets
    the model say "ask the user to enter the directory".

    A directory declaring *nothing* is a different answer — `no_environment`,
    which the tool falls back on rather than reporting (N85).
    """

    resolver = _resolver()
    (tmp_path / ".envrc").write_text("use flake\n")

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(tmp_path))

    assert caught.value.reason == "env_unrealised"


def test_a_substitution_in_the_cache_is_never_evaluated(tmp_path):
    """The attack this module exists to prevent.

    `.direnv` lives inside the working directory, so its contents are
    attacker-influenced for any repository Neuman has been pointed at. Sourcing
    the rc would turn "read this project" into "run this project's code", which
    inverts D23: injected text may propose work, never widen reach.

    Single-quoted values cannot interpolate, so this *should* be unreachable —
    which is why the resolver treats it as a violated invariant and refuses
    rather than filtering. Verified by removing the check, at which point the
    value is carried through as a literal string: harmless only because nothing
    downstream runs it through a shell, which is a property no future caller
    should have to preserve.
    """

    resolver = _resolver()

    # `SOURCE_DATE_EPOCH` deliberately, not a path-like variable. A
    # substitution in `PATH` or `PYTHONPATH` is caught by the store-path filter
    # instead, which made a first version of this test pass with the
    # substitution guard deleted — it asserted the right reason via the wrong
    # check and so proved nothing. This variable is carried verbatim, so the
    # guard is the only thing standing between the cache and the environment.
    assert "SOURCE_DATE_EPOCH" in resolver.CARRIED
    assert "SOURCE_DATE_EPOCH" not in resolver.PATH_LIKE

    project = _realised(
        tmp_path,
        "PATH='/nix/store/a/bin'\nSOURCE_DATE_EPOCH='$(touch /tmp/pwned)'\n",
    )

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(project))

    assert caught.value.reason == "env_untrusted"
    assert not Path("/tmp/pwned").exists(), "the substitution was evaluated"


def test_a_path_outside_the_store_is_refused(tmp_path):
    """The store check is what makes "this widens nothing" structural.

    A `.direnv` cache is text inside the directory being read, so an entry
    outside `/nix/store` is a repository naming its own program — refusing
    rather than dropping the entry is what keeps “reading a repository” from
    being “running code from it” (D33).
    """

    resolver = _resolver()
    project = _realised(tmp_path, "PATH='/nix/store/a/bin:/usr/local/bin'\n")

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(project))

    assert caught.value.reason == "env_untrusted"
    assert "/usr/local/bin" in caught.value.detail


def test_a_cache_without_a_nix_profile_is_refused(tmp_path):
    """An rc file alone proves nothing.

    Without this, a `.direnv/flake-profile-x.rc` committed to a repository
    would be read as an environment — the file is trivial to fabricate, where a
    symlink resolving into the store is evidence that a real `nix` produced the
    paths it names.
    """

    resolver = _resolver()
    cache = tmp_path / ".direnv"
    cache.mkdir()
    (cache / "flake-profile-abc.rc").write_text("PATH='/nix/store/a/bin'\n")

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(tmp_path))

    assert caught.value.reason == "env_unrealised"


def test_a_profile_pointing_outside_the_store_is_refused(tmp_path):
    """A symlink is only evidence if it resolves where nix would put it."""

    resolver = _resolver()
    decoy = tmp_path / "not-the-store"
    decoy.mkdir()
    project = _realised(tmp_path, "PATH='/nix/store/a/bin'\n", store_target=str(decoy))

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(project))

    assert caught.value.reason == "env_untrusted"


def test_an_environment_older_than_its_flake_is_refused(tmp_path):
    """Staleness is the failure that would otherwise be silent and expensive.

    The script runs, imports resolve against the previous package set, and the
    symptom is a missing module rather than the stale cache it is. Named so the
    model reports a cause instead of retrying.
    """

    import os

    resolver = _resolver()
    project = _realised(tmp_path, "PATH='/nix/store/a/bin'\n")

    flake = project / "flake.nix"
    flake.write_text("{}\n")
    rc = project / ".direnv" / "flake-profile-abc123.rc"
    # Explicit times rather than sleeping: the property is an ordering, and a
    # test that waits for the clock is a test that is slow and still flaky.
    os.utime(rc, (1_000_000, 1_000_000))
    os.utime(flake, (2_000_000, 2_000_000))

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(project))

    assert caught.value.reason == "env_stale"


def test_the_shell_hook_is_never_carried(tmp_path):
    """`shellHook` is code, and carrying it would reintroduce execution.

    The allowlist is what keeps this out. A blocklist would have to anticipate
    every code-valued variable nix might add, and would be wrong the first time
    one was named something else.
    """

    resolver = _resolver()
    project = _realised(
        tmp_path,
        "PATH='/nix/store/a/bin'\nshellHook='rm -rf /'\n",
    )

    environment = resolver.resolve(str(project))
    assert "shellHook" not in environment.env


def test_the_python_tool_ships_with_a_resolver_and_a_distinct_exec(registry):
    """The tool's exec name must not be `python`.

    `exec` is resolved by joining the tool directory with the name, so any name
    works — but this package may end up on a PATH, where a file called `python`
    would shadow the interpreter for everything that searches it. The tool that
    exists to make "which interpreter does this directory mean" answerable does
    not get to ship a file named after the interpreter.
    """

    manifest = registry.get("python")
    assert manifest.kind == "client", "an interpreter needs no exception kind"
    assert manifest.exec != "python", (
        "an exec named `python` would shadow the interpreter for anything "
        "searching the tool directory"
    )
    assert (TOOLS_DIR / "lib" / "direnv_env.py").exists()


def test_a_directory_declaring_no_environment_is_not_a_failure(tmp_path):
    """Most directories are not projects, and refusing those was the real mistake.

    The first version refused any directory without a `.direnv`, answering "ask
    the user to run `direnv allow`" — advice about a `.envrc` that does not
    exist and never will. The common case is a folder of data a script is
    pointed at, where the work needs nothing but the standard library (N85).

    `no_environment` is a distinct reason so the caller can fall back rather
    than report a failure; `env_unrealised` is kept for a project that declares
    an environment it has not built.
    """

    resolver = _resolver()

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(tmp_path))

    assert caught.value.reason == "no_environment"


def test_a_declared_but_unbuilt_environment_is_still_refused(tmp_path):
    """The distinction the fallback rests on.

    A directory holding a `flake.nix` means somebody intended packages here, so
    running against a bare interpreter would silently lack them — a
    `ModuleNotFoundError` that looks like a broken tool rather than an unbuilt
    environment. That case keeps its refusal.
    """

    resolver = _resolver()
    (tmp_path / "flake.nix").write_text("{}\n")

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(tmp_path))

    assert caught.value.reason == "env_unrealised"


def test_every_declaration_marks_a_directory_as_a_project(tmp_path):
    """Any of the four files means an environment was intended.

    `.envrc` is direnv's, the three `.nix` names are what `use flake` and
    `use nix` read. Checked individually because a loop over the tuple would
    pass if the tuple were empty.
    """

    resolver = _resolver()

    for name in (".envrc", "flake.nix", "shell.nix", "default.nix"):
        directory = tmp_path / name.replace(".", "_")
        directory.mkdir()
        (directory / name).write_text("{}\n")

        with pytest.raises(resolver.EnvError) as caught:
            resolver.resolve(str(directory))
        assert caught.value.reason == "env_unrealised", (
            f"{name} should mark the directory as a project"
        )


def test_the_tool_falls_back_in_a_plain_directory(tmp_path):
    """End to end: the majority case must produce output, not a refusal.

    Asserted through the tool rather than the resolver, because the fallback is
    the tool's decision — the resolver only reports that nothing is declared.
    """

    import json
    import os
    import subprocess
    import sys

    (tmp_path / "data.json").write_text('{"a": 1}')

    result = subprocess.run(
        [
            sys.executable,
            str(TOOLS_DIR / "bin" / "python.py"),
            json.dumps(
                {
                    "action": "run",
                    "cwd": str(tmp_path),
                    "code": "import json; print(json.load(open('data.json'))['a'])",
                }
            ),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(TOOLS_DIR / "lib"),
            "HOME": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1", result.stdout
    assert "env_unrealised" not in result.stderr


def test_a_collected_environment_is_refused_by_name(tmp_path):
    """A GC root is a root only while something holds it.

    This is the ordinary state of any project not entered since the last
    `nix-collect-garbage`: the rc file still lists seventy store paths and none
    of them exists. Found by running the tool against a real cache rather than
    a fixture, which is also why it is its own reason — the first version
    reported it as "declares no Python on its PATH", which pointed at the
    project's flake instead of at the collected store.
    """

    resolver = _resolver()
    project = _realised(
        tmp_path,
        "PATH='/nix/store/a/bin'\n",
        store_target="/nix/store/does-not-exist-env",
    )

    with pytest.raises(resolver.EnvError) as caught:
        resolver.resolve(str(project))

    assert caught.value.reason == "env_collected"


def test_every_resolver_refusal_carries_advice():
    """A named reason the tool has no sentence for is a dead end for the model.

    The resolver names the cause; the tool says what to do about it. Those are
    two files, so a reason added to one and not the other would surface as a
    bare refusal — which is the shape of drift N38/N48 describe, caught here by
    requiring the two sets to agree.
    """

    import importlib.util
    import sys

    path = TOOLS_DIR / "bin" / "python.py"
    spec = importlib.util.spec_from_file_location("neuman_python_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["neuman_python_tool"] = module
    spec.loader.exec_module(module)

    resolver = _resolver()
    source = (TOOLS_DIR / "lib" / "direnv_env.py").read_text()

    # The reasons the resolver can actually raise, read from its own source so
    # a new one is picked up without this test being told about it.
    import re

    raised = set(re.findall(r'EnvError\(\s*"([a-z_]+)"', source))
    assert raised, "no refusals found; the regex no longer matches the source"

    missing = raised - set(module.ADVICE)
    assert not missing, f"refusals with no advice for the model: {sorted(missing)}"
    assert resolver  # the resolver imports cleanly alongside the tool


def test_describe_lists_the_packages_the_interpreter_can_import(tmp_path):
    """The question `describe` exists to answer is "will my import work".

    Asked of the interpreter rather than derived from the cache, so the list
    and the imports cannot disagree. Run against this interpreter's own
    environment, which is a real one with real distributions — a fixture
    asserting against a fabricated `site-packages` would be the self-agreeing
    kind that hid two of this tree's worst bugs.
    """

    import json
    import os
    import subprocess
    import sys

    interpreter = Path(sys.executable).resolve()
    project = _realised(tmp_path, f"PATH='{interpreter.parent}'\n")

    result = subprocess.run(
        [
            sys.executable,
            str(TOOLS_DIR / "bin" / "python.py"),
            json.dumps({"action": "describe", "cwd": str(project)}),
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(TOOLS_DIR / "lib"),
            "HOME": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "packages" in result.stdout
    # pytest is running this, so it is installed by definition — a name the
    # environment genuinely has, rather than one the test hopes for.
    assert "pytest" in result.stdout


def test_describe_truncates_an_environment_larger_than_its_cap(tmp_path):
    """An inventory a model cannot afford to read is one it will stop asking for.

    The session that prompted this tool died on `exceed_context_size_error`
    after a directory listing filled its context, so a `describe` that dumps a
    scientific stack unbounded would reproduce the failure it exists to
    prevent.

    The cap has to be exercised against an environment that *exceeds* it. A
    first version of this test ran against the suite's own interpreter, which
    has twenty-seven distributions against a cap of sixty — so it passed with
    the cap raised to a hundred thousand and proved nothing. The fake
    interpreter here reports more packages than the cap on purpose.
    """

    import importlib.util
    import sys

    if str(TOOLS_DIR / "lib") not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR / "lib"))

    spec = importlib.util.spec_from_file_location(
        "neuman_python_cap", TOOLS_DIR / "bin" / "python.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["neuman_python_cap"] = module
    spec.loader.exec_module(module)

    oversized = module.MAX_PACKAGES * 3

    # An "interpreter" that prints an inventory far larger than the cap. It
    # stands in for a scientific stack without this test having to build one.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python3"
    fake.write_text(
        f'#!/bin/sh\ni=0; while [ $i -lt {oversized} ]; do echo "pkg$i 1.0"; i=$((i+1)); done\n'
    )
    fake.chmod(0o755)

    resolver = _resolver()
    environment = resolver.Environment(
        rc_path=str(tmp_path / "flake-profile-abc123.rc"),
        profile="/nix/store/irrelevant",
        env={"PATH": str(bin_dir)},
    )

    import contextlib
    import io

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = module._describe(environment, str(fake))
    output = captured.getvalue()

    assert code == 0
    listed = [line for line in output.splitlines() if line.startswith("  pkg")]
    assert len(listed) == module.MAX_PACKAGES, (
        f"printed {len(listed)} package lines against a cap of {module.MAX_PACKAGES}"
    )

    # The true count is reported even though the list is cut, so a truncated
    # inventory never reads as a complete one.
    assert f"({oversized})" in output
    assert f"{oversized - module.MAX_PACKAGES} more" in output

    # The real budget is a model's context, not the manifest's 1 MiB cap.
    assert len(output) < 8192, (
        f"describe produced {len(output)} bytes; too expensive to call routinely"
    )


def test_describe_survives_an_interpreter_that_cannot_be_asked(tmp_path):
    """A missing inventory is a missing extra, not a broken environment.

    The interpreter and the import path are the answer `describe` is for, and
    they are printed before the inventory is attempted. Reporting the whole
    call as failed because the listing did not work would turn a usable
    environment into one the model believes is unusable.
    """

    import subprocess
    import sys

    # An "interpreter" that is executable and is not Python, so the inventory
    # subprocess exits non-zero while everything above it still resolves.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python3"
    fake.write_text("#!/bin/sh\nexit 9\n")
    fake.chmod(0o755)

    resolver = _resolver()
    project = _realised(tmp_path, f"PATH='{bin_dir}'\n")

    # The store check would refuse this PATH, so the test targets the branch
    # directly rather than smuggling a non-store path past a check it is not
    # about. `describe` is called with an already-resolved environment.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "neuman_python_degraded", TOOLS_DIR / "bin" / "python.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["neuman_python_degraded"] = module
    spec.loader.exec_module(module)

    environment = resolver.Environment(
        rc_path=str(project / ".direnv" / "flake-profile-abc123.rc"),
        profile="/nix/store/irrelevant",
        env={"PATH": str(bin_dir)},
    )

    code = module._describe(environment, str(fake))
    assert code == 0, "a failed inventory must not fail the describe"
    assert subprocess  # the branch under test shells out


# -- blender ------------------------------------------------------------------

BLENDER_TOOLS = ("blender", "blender_scene", "blender_view")


def test_the_blender_tools_are_a_package_that_needs_blender(registry):
    """Not built in: an appliance has them only once an operator enables them (D101).

    And Neuman never installs Blender — the package names it and where to get
    it, and the client refuses a call on a machine without it.
    """

    package = registry.package("blender")
    assert package is not None
    assert set(package.tools) == set(BLENDER_TOOLS)
    assert all(registry.package_of(name) == "blender" for name in BLENDER_TOOLS)
    assert package.requires["program"] == "blender"
    assert package.requires["url"].startswith("https://")
    assert package.host == {"exec": "neuman-blender-host"}
    for manifest in (registry.get(name) for name in BLENDER_TOOLS):
        assert manifest.exec != package.host["exec"], "no dispatch may name the host"


def test_only_the_blender_tools_are_in_a_package(registry):
    """Everything else ships built in; a package is a choice an operator makes."""

    packaged = {name for name in registry.names() if registry.package_of(name)}
    assert packaged == set(BLENDER_TOOLS)


def test_only_the_blender_tool_that_changes_the_scene_asks(registry):
    """Looking is free under `safe`; changing is not (D59, D100).

    Building a model is a loop of change, then look. If looking prompted, every
    iteration would cost the person two approvals, and the loop the tools exist
    for would be one nobody runs. If changing did not, `exec` — arbitrary Python
    as the user — would run unasked under the default policy.
    """

    change = registry.get("blender")
    assert change.mutating and "write" in change.needs
    for name in ("blender_scene", "blender_view"):
        manifest = registry.get(name)
        assert not manifest.mutating, f"{name} only looks, and must not prompt under safe"
        assert manifest.needs == frozenset({"read"}), name


def test_the_viewport_picture_is_lifted_as_an_image(registry):
    """`blender_view` declares the image shape, so the harness lifts it (D28).

    The harness once recognised `fs_image` by name; a second picture-producing
    tool without the flag would hand a model megabytes of base64 as text.
    """

    view = registry.get("blender_view")
    assert view.returns_image
    assert registry.get("fs_image").returns_image
    others = [
        name
        for name in registry.names()
        if registry.get(name).returns_image and name not in ("fs_image", "blender_view")
    ]
    assert not others, f"tools claiming to return an image: {others}"


def test_no_blender_tool_execs_a_file_called_blender(registry):
    """A file called `blender` in the tool directory would shadow Blender on a PATH."""

    for name in BLENDER_TOOLS:
        assert registry.get(name).exec != "blender", name


def test_the_blender_link_needs_no_third_party_package():
    """The tools run on a bare interpreter (N47); the socket client stays stdlib."""

    import ast
    import sys

    source = (TOOLS_DIR / "lib" / "blender_link.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    outside = sorted(imported - set(sys.stdlib_module_names))
    assert not outside, f"blender_link imports non-stdlib modules: {outside}"


@pytest.mark.parametrize("name", BLENDER_TOOLS)
def test_a_blender_tool_run_outside_the_client_says_so(name, monkeypatch):
    """The socket is the client's to name; without it there is nothing to dial.

    Whether Blender is installed is the client's question, answered before the
    tool runs (`missing_program`, D101); this is the tool's own last word.
    """

    monkeypatch.delenv("NEUMAN_HOST_SOCKET", raising=False)
    args = {"action": "exec", "code": "pass"} if name == "blender" else {}
    code, _out, err = _run_tool(name, args)
    assert code == 1
    assert "Neuman client" in err, err


def _live_blender(tmp_path):
    """A real Blender running the real add-on, in a virtual display.

    Skipped where either is missing, which is every sandboxed build: this is the
    test that proves the tools reach Blender at all, and it runs on a machine
    that has one. A fake socket here would prove only that the fake agrees with
    the client (README, "a fixture that constructs the thing under test").
    """

    import os
    import shutil
    import subprocess
    import sys
    import time

    blender = shutil.which("blender")
    xvfb = shutil.which("Xvfb")
    if not blender or not xvfb:
        pytest.skip("needs blender and Xvfb on PATH")
    # The host and its add-on side by side, as `tools/default.nix` installs them:
    # the host finds the add-on beside itself.
    installed = tmp_path / "lib"
    installed.mkdir()
    shutil.copy(TOOLS_DIR / "bin" / "blender_host.py", installed)
    shutil.copy(TOOLS_DIR / "lib" / "blender_addon.py", installed)
    socket_path = tmp_path / "blender.sock"
    display = f":{90 + os.getpid() % 500}"
    screen = subprocess.Popen(
        [xvfb, display, "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    environment = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    environment.update(
        DISPLAY=display,
        NEUMAN_HOST_SOCKET=str(socket_path),
        NEUMAN_HOST_PROGRAM=blender,
    )
    time.sleep(1)
    process = subprocess.Popen(
        [sys.executable, str(installed / "blender_host.py")],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 90
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.2)

    def stop():
        process.terminate()
        screen.terminate()
        process.wait(timeout=10)
        screen.wait(timeout=10)

    if not socket_path.exists():
        stop()
        pytest.fail("Blender never opened its socket")
    return {"NEUMAN_HOST_SOCKET": str(socket_path)}, stop


def test_the_blender_tools_build_look_at_and_undo_a_model(tmp_path):
    """The loop the tools exist for, against a real Blender.

    Build with `blender`, read it back with `blender_scene`, see it with
    `blender_view` — and undo the change with one step, which is the promise
    the add-on makes to the person working beside the model.
    """

    import base64
    import stat

    env, stop = _live_blender(tmp_path)
    try:
        mode = (tmp_path / "blender.sock").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600, "the socket runs Python as the user"

        code, out, err = _run_tool(
            "blender",
            {
                "action": "exec",
                "code": (
                    "o = bpy.data.objects.new('Plinth', bpy.data.meshes.new('Plinth'))\n"
                    "bpy.context.scene.collection.objects.link(o)\n"
                    "print('built', o.name)"
                ),
            },
            env,
        )
        assert code == 0, err
        assert "built Plinth" in out

        code, out, err = _run_tool("blender_scene", {}, env)
        assert code == 0, err
        assert "Plinth  MESH" in out

        code, out, err = _run_tool("blender_view", {"view": "front", "size": 256}, env)
        assert code == 0, err
        media, _, data = out.strip().partition(" ")
        assert media == "image/png"
        assert base64.b64decode(data).startswith(b"\x89PNG")

        code, out, err = _run_tool(
            "blender",
            {
                "action": "exec",
                "code": (
                    "o = bpy.data.objects.new('Column', bpy.data.meshes.new('Column'))\n"
                    "bpy.context.scene.collection.objects.link(o)\n"
                    "1/0"
                ),
            },
            env,
        )
        assert code == 1
        assert "ZeroDivisionError" in out

        # One undo takes back the second call and only the second, even though
        # it raised half way: each call is its own step. Without the add-on's
        # push, a data-API change makes no step at all and the undo would land
        # before both.
        code, out, err = _run_tool(
            "blender",
            {
                "action": "exec",
                "code": "bpy.ops.ed.undo()\nprint(sorted(o.name for o in bpy.data.objects))",
            },
            env,
        )
        assert code == 0, err
        assert "Plinth" in out and "Column" not in out, out

        code, _out, err = _run_tool(
            "blender", {"action": "export", "path": str(tmp_path / "model.glb")}, env
        )
        assert code == 0, err
        assert (tmp_path / "model.glb").read_bytes()[:4] == b"glTF"
    finally:
        stop()


# -- fs_edit, and the write path both edit tools share -----------------------


def _edit(tmp_path, body: str, **args):
    target = tmp_path / "f.py"
    target.write_text(body)
    return target, _run_tool("fs_edit", {"path": str(target), **args})


def test_a_new_file_gets_its_missing_parents(tmp_path):
    """A task's run directory is made by writing its record into it (D97)."""

    target = tmp_path / "runs" / "2026-11-10-abc" / "neuman-run.json"
    code, _, err = _run_tool("fs_write", {"path": str(target), "content": "{}\n"})

    assert code == 0, err
    assert target.read_text() == "{}\n"


def test_a_preview_of_a_new_file_makes_no_directory(tmp_path):
    target = tmp_path / "runs" / "today" / "x.json"
    code, _, err = _run_tool(
        "fs_write", {"path": str(target), "content": "{}", "preview": True}
    )

    assert code == 0, err
    assert not (tmp_path / "runs").exists(), "a preview made a directory"


def test_a_preview_reports_the_diff_and_writes_nothing(tmp_path):
    """The property the approval frame depends on.

    A human approves the diff this prints. If the preview wrote, the approval
    would be asked after the fact; if it computed the diff some other way, the
    approval would describe a different write (N35, N38).
    """

    target, (code, out, err) = _edit(
        tmp_path,
        "a = 1\nb = 2\n",
        preview=True,
        edits=[{"old_text": "b = 2", "new_text": "b = 3"}],
    )

    assert code == 0, err
    assert target.read_text() == "a = 1\nb = 2\n", "a preview wrote to the file"
    assert "-b = 2" in out and "+b = 3" in out


def test_a_preview_and_the_write_describe_the_same_change(tmp_path):
    """One module decides, so the two cannot disagree.

    Asserted by comparing the diffs rather than by trusting that both call the
    same function: a shared helper that two callers use differently is still two
    behaviours.
    """

    import json

    edits = [{"old_text": "b = 2", "new_text": "b = 3"}]
    target, (_, preview_out, _) = _edit(tmp_path, "a = 1\nb = 2\n", preview=True, edits=edits)
    _, write_out, _ = _run_tool("fs_edit", {"path": str(target), "edits": edits})

    def diff_of(stdout: str) -> str:
        return json.loads(stdout.strip().splitlines()[-1])["neuman_diff"]["diff"]

    assert diff_of(preview_out) == diff_of(write_out)
    assert target.read_text() == "a = 1\nb = 3\n"


def test_an_edit_that_does_not_match_refuses_and_names_the_closest_line(tmp_path):
    """A refusal a model can act on, rather than one it has to guess at."""

    target, (code, _, err) = _edit(
        tmp_path,
        "value = 1\n",
        edits=[{"old_text": "value = 2", "new_text": "value = 3"}],
    )

    assert code == 1
    assert "does not appear" in err
    assert "value = 1" in err, "the refusal did not name the closest line"
    assert target.read_text() == "value = 1\n"


def test_an_ambiguous_edit_is_refused_rather_than_guessed(tmp_path):
    """Two candidate sites means the tool does not know which was meant."""

    target, (code, _, err) = _edit(
        tmp_path,
        "x = 1\ny = 1\n",
        edits=[{"old_text": " = 1", "new_text": " = 2"}],
    )

    assert code == 1
    assert "appears 2 times" in err
    assert target.read_text() == "x = 1\ny = 1\n"


def test_overlapping_edits_are_refused(tmp_path):
    """Order-dependent results are worse than a refusal."""

    target, (code, _, err) = _edit(
        tmp_path,
        "x = 1\ny = 1\nz = 3\n",
        edits=[
            {"old_text": "x = 1\ny = 1", "new_text": "a"},
            {"old_text": "y = 1\nz = 3", "new_text": "b"},
        ],
    )

    assert code == 1
    assert "overlap" in err
    assert target.read_text() == "x = 1\ny = 1\nz = 3\n"


def test_one_failing_edit_leaves_the_file_untouched(tmp_path):
    """All or nothing. A half-applied edit is a state nobody asked for."""

    target, (code, _, _) = _edit(
        tmp_path,
        "a = 1\nb = 2\n",
        edits=[
            {"old_text": "a = 1", "new_text": "a = 9"},
            {"old_text": "nope", "new_text": "x"},
        ],
    )

    assert code == 1
    assert target.read_text() == "a = 1\nb = 2\n", "a refused batch applied part of itself"


def test_a_write_succeeds_when_rename_and_unlink_are_denied(tmp_path, monkeypatch):
    """N96: the fallback that had never once run.

    An atomic `os.replace` needs the removal right on the source, which a
    directory can withhold; then it is denied and the tool must fall back to
    writing in place. The old handler cleaned up its temp file *before* testing
    for the permission error, and that cleanup is denied for the same reason, so
    it raised and the fallback below it never executed.

    Denying exactly `os.replace` and `os.unlink` is what reproduces it. An
    unwritable directory does not: `mkstemp` fails there, taking an early return
    that never reaches the bug — which is why no existing test caught this.
    """

    import importlib
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(edit_plan.os, "replace", denied)
    monkeypatch.setattr(edit_plan.os, "unlink", denied)

    target = tmp_path / "f.txt"
    target.write_text("orig\n")

    code = edit_plan.run(
        "fs_write",
        ["fs_write", f'{{"path": "{target}", "content": "new\\n"}}'],
        whole_file=True,
    )

    assert code == 0, "the write failed where it must fall back"
    assert target.read_text() == "new\n"


def test_a_diff_carries_the_line_numbers_a_client_would_otherwise_have_to_derive(tmp_path):
    """`rows` is why the approval frame can show a gutter.

    A unified patch keeps its line numbers in hunk headers only, so a renderer
    wanting "which line is this" has to walk two counters through the body.
    Doing it once server-side means every client agrees about the numbering.
    """

    import importlib
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    target = tmp_path / "f.py"
    target.write_text("a = 1\nb = 2\nc = 3\n")
    intent = edit_plan.plan(str(target), edits=[{"old_text": "b = 2", "new_text": "b = 22"}])
    rows = intent.structured()["rows"]

    removed = [r for r in rows if r["kind"] == "remove"]
    added = [r for r in rows if r["kind"] == "add"]
    assert [r["text"] for r in removed] == ["b = 2"]
    assert [r["text"] for r in added] == ["b = 22"]

    # The changed line is line 2 on both sides, and each row numbers only the
    # file it exists in.
    assert removed[0]["old"] == 2 and removed[0]["new"] is None
    assert added[0]["new"] == 2 and added[0]["old"] is None

    # Context keeps both, so a reader can follow either column.
    context = [r for r in rows if r["kind"] == "context"]
    assert context[0]["old"] == 1 and context[0]["new"] == 1
    assert all(r["old"] == r["new"] for r in context)


def test_an_edit_does_not_change_an_existing_file_s_permissions(tmp_path):
    """N107: `os.replace` swaps the inode, so the mode must be carried across.

    The old code chmod'ed every written file to 0644 unconditionally. On a
    group-writable file that is a colleague locked out of something they could
    edit a moment earlier; on a 0600 file it is a widening nobody asked for.
    An edit changes contents, never permissions.
    """

    import importlib
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    for mode in (0o660, 0o600, 0o640):
        target = tmp_path / f"f{mode:o}.txt"
        target.write_text("orig\n")
        target.chmod(mode)

        code = edit_plan.run(
            "fs_write",
            ["fs_write", f'{{"path": "{target}", "content": "new\\n"}}'],
            whole_file=True,
        )

        assert code == 0
        assert target.read_text() == "new\n"
        assert target.stat().st_mode & 0o7777 == mode, (
            f"writing a {mode:o} file left it {target.stat().st_mode & 0o7777:o}"
        )


def test_a_new_file_honours_the_umask_rather_than_a_hardcoded_mode(tmp_path):
    """So a setgid, group-writable share stays collaborative.

    The tool must not name a number: a share admin sets `umask 002` to make a
    directory's files group-writable, and a hardcoded 0644 overrides that
    silently. `0666 & ~umask` is what a plain `open()` would have produced.
    """

    import importlib
    import os
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    previous = os.umask(0o002)
    try:
        target = tmp_path / "collab.txt"
        code = edit_plan.run(
            "fs_write",
            ["fs_write", f'{{"path": "{target}", "content": "shared\\n"}}'],
            whole_file=True,
        )
    finally:
        os.umask(previous)

    assert code == 0
    assert target.stat().st_mode & 0o7777 == 0o664, (
        "a collaborative umask was overridden, so a colleague cannot edit this"
    )


def test_a_new_file_under_the_default_umask_is_still_readable(tmp_path):
    """The case the hardcoded 0644 existed to serve, still served."""

    import importlib
    import os
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    previous = os.umask(0o022)
    try:
        target = tmp_path / "ordinary.txt"
        edit_plan.run(
            "fs_write",
            ["fs_write", f'{{"path": "{target}", "content": "x\\n"}}'],
            whole_file=True,
        )
    finally:
        os.umask(previous)

    assert target.stat().st_mode & 0o7777 == 0o644, "mkstemp's 0600 leaked"


def test_a_temp_file_that_cannot_be_unlinked_is_at_least_emptied(tmp_path, monkeypatch):
    """N109: four of these were found with real content in project directories.

    When `os.unlink` is refused, the cleanup is denied by the same thing that
    denied the rename, so the temp file survives. A zero-byte leftover is
    something a person can ignore; a 1.7 KB one looks like content they might
    need. `truncate` is a write, not an unlink, so it succeeds exactly where the
    removal is refused.
    """

    import importlib
    import sys

    sys.path.insert(0, str(TOOLS_DIR / "lib"))
    try:
        edit_plan = importlib.import_module("edit_plan")
    finally:
        sys.path.pop(0)

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(edit_plan.os, "replace", denied)
    monkeypatch.setattr(edit_plan.os, "unlink", denied)

    target = tmp_path / "f.txt"
    target.write_text("orig\n")

    code = edit_plan.run(
        "fs_write",
        ["fs_write", f'{{"path": "{target}", "content": "new content here\\n"}}'],
        whole_file=True,
    )

    assert code == 0, "the write must still fall back"
    assert target.read_text() == "new content here\n"

    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".neuman-")]
    assert leftovers, "this test is pointless if no temp file survived"
    for leftover in leftovers:
        assert leftover.stat().st_size == 0, (
            f"{leftover.name} kept {leftover.stat().st_size} bytes of content"
        )


# -- env_probe ----------------------------------------------------------------
#
# The probe is registered and granted to nobody: the browser's
# `/api/environment` and the harness's prompt line dispatch it, and the model
# never sees it. These test the executable directly, like the resolver tests
# above, because its contract is the JSON on stdout.


def _probe(args: dict) -> tuple[int, dict | None, str]:
    """Run the probe as a dispatch would, returning its parsed answer.

    A subprocess rather than an import: the contract is a process that prints
    JSON and exits zero, and an imported `main` would prove the parsing agrees
    with itself while saying nothing about either.
    """

    import json
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "bin" / "env_probe.py"), json.dumps(args)],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(TOOLS_DIR / "lib"), "HOME": "/"},
    )
    try:
        report = json.loads(completed.stdout)
    except ValueError:
        report = None
    return completed.returncode, report, completed.stderr


def test_the_probe_reports_a_realised_environment(tmp_path):
    """The lit state, and the fact that makes it worth reporting.

    The interpreter is in the answer because that is what the prompt names, and
    naming it is what makes a later `ModuleNotFoundError` answerable: the reader
    can see which Python the directory meant.
    """

    import os
    import sys

    bindir = Path(sys.executable).resolve().parent
    project = _realised(tmp_path, f"PATH='{bindir}'\n")

    code, report, stderr = _probe({"cwd": str(project)})

    assert code == 0, stderr
    assert report["reason"] == "ok", report
    # The generic name, not `sys.executable`: the resolver scans for `python3`
    # before `python`, which is the point — it reports the name a dispatch would
    # run, and a versioned symlink's target is not that name.
    assert report["interpreter"] == str(bindir / "python3"), report
    assert os.access(report["interpreter"], os.X_OK), "the named interpreter must run"
    assert report["profile"].startswith("/nix/store/"), report


def test_every_environment_state_exits_zero(tmp_path):
    """A refusal is the answer here, not a failure.

    This is the difference between the probe and the `python` tool, which exits
    non-zero on the same states because it was asked to *run* something. A
    status light has no use for an exit code: `no_environment` is the ordinary
    condition of most directories (N85), and a non-zero exit would make the
    route report a failed dispatch for a folder of data.
    """

    plain = tmp_path / "plain"
    plain.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "flake.nix").write_text("{}\n")

    expected = {
        str(plain): "no_environment",
        str(project): "env_unrealised",
        "/nonexistent-directory-for-this-test": "no_such_directory",
    }

    for path, reason in expected.items():
        code, report, stderr = _probe({"cwd": path})
        assert code == 0, f"{path}: exited {code}: {stderr}"
        assert report["reason"] == reason, report


def test_the_probe_runs_no_subprocess(tmp_path):
    """Why this tool exists at all, rather than `python(action="describe")`.

    Describe spawns the resolved interpreter to inventory its packages — a
    hundred distributions on a scientific stack. This runs on every working
    directory change, so a spawn per click is the wrong price for an outline.
    Asserted against the source because the cost is structural: an import of
    `subprocess` here is the thing that would make it expensive again.
    """

    import ast

    source = (TOOLS_DIR / "bin" / "env_probe.py").read_text()
    tree = ast.parse(source)

    # The imports, not the text: the docstring explains at length why there is
    # no subprocess here, and grepping the file for the word matched that
    # explanation. An AST walk asks the question the cost actually depends on.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "subprocess" not in imported, (
        "the probe must not spawn: it runs on every working-directory change"
    )


def test_the_probe_is_granted_to_no_baseline_role(registry):
    """Registered, and a capability for nobody.

    The model has `python(action="describe")` for this question and a fuller
    answer to it. This manifest exists for the two callers that cannot make a
    tool call — the chooser's route and the harness's prompt line — and
    `grantable()` is what keeps it out of the model's tool schemas: a tool no
    role names is never advertised.

    And `diagnostic`, which is what keeps it out of the tool chooser. Ungranted
    alone is not enough: that row renders greyed out and struck through under
    "an administrator grants tools in a role", which invites someone to
    grant a capability that is not one, for a question no user asks. The flag is
    presentation only and appears nowhere in the gate, so this hides the row
    without changing a single dispatch decision.
    """

    manifest = registry.get("env_probe")

    assert manifest is not None, "the probe must be installed to be dispatchable"
    assert manifest.kind == "client", "the .direnv cache is on the user's machine"
    assert manifest.mutating is False
    assert manifest.diagnostic is True, (
        "an ungranted non-diagnostic shows in the chooser as a tool to grant"
    )
    assert manifest.needs == frozenset({"read"})
    assert "cwd" in manifest.parameters.get("properties", {})


def test_the_probe_checks_its_path_argument(registry):
    """`cwd` is declared a path, so the client resolves and checks it.

    Without the declaration the tool host checks nothing — a manifest that
    declares no paths is not checked at all — and the probe would read a
    `.direnv` cache outside the account's surfaces. The route checks
    `within_reach` as well; this is the half that survives a route forgetting to.
    """

    import tomllib

    document = tomllib.loads(
        (TOOLS_DIR / "env_probe" / "manifest.toml").read_text(encoding="utf-8")
    )

    assert document["tool"]["paths"] == ["cwd"], (
        "a path argument the client does not check is a path nothing checks"
    )
