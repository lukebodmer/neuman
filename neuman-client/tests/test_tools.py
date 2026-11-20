"""The client's tool resolution: a name and the client's own manifests decide.

The two facts worth pinning: a dispatch cannot name a path, and a tool the client
cannot run is indistinguishable from one it has never heard of.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neuman_client.tools import MissingProgram, PathOutsideSurface, ToolHost, ToolHostError

REPO = Path(__file__).resolve().parents[2]


def _manifest(directory: Path, name: str, body: str) -> None:
    tool_dir = directory / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "manifest.toml").write_text(body, encoding="utf-8")


def _install(bin_dir: Path, *names: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)


def test_a_known_tool_resolves_to_its_executable(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "fs_read",
        '[tool]\nname = "fs_read"\nexec = "fs_read"\n'
        "wait_ms = 30000\nmax_output_bytes = 4096\n",
    )
    _install(bin_dir, "fs_read")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    assert host.resolve("fs_read") == bin_dir / "fs_read"
    assert host.tool("fs_read") is not None
    assert host.tool("fs_read").wait_ms == 30000


def test_a_native_tool_without_an_executable_is_not_offered(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _manifest(
        manifests,
        "session_query",
        '[tool]\nname = "session_query"\nkind = "server-native"\n',
    )

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    assert host.tool("session_query") is None
    assert host.resolve("session_query") is None


def test_an_unknown_tool_and_a_missing_program_are_the_same_answer(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(manifests, "fs_read", '[tool]\nname = "fs_read"\nexec = "fs_read"\n')
    bin_dir.mkdir()  # the manifest is installed, the executable is not

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    assert host.resolve("fs_read") is None
    assert host.resolve("never_heard_of_it") is None


def test_a_command_carries_json_args_and_the_lower_lifetime(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "fs_read",
        '[tool]\nname = "fs_read"\nexec = "fs_read"\n'
        "wait_ms = 30000\nmax_output_bytes = 4096\n",
    )
    _install(bin_dir, "fs_read")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)
    command = host.command(
        "fs_read",
        {"path": "/etc/hosts"},
        dispatch_id="abc123",
        cwd="/tmp",
        wait_ms=10_000,
        lifetime_ms=10_000,
    )

    assert command is not None
    assert command.argv == (str(bin_dir / "fs_read"), '{"path":"/etc/hosts"}')
    assert command.name == "fs_read-abc123"
    assert command.cwd == "/tmp"
    assert command.lifetime_ms == 10_000  # the dispatch's, being lower
    assert command.wait_ms == 10_000


def test_the_manifest_lifetime_stands_when_the_dispatch_asks_longer(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests, "fs_read", '[tool]\nname = "fs_read"\nexec = "fs_read"\nwait_ms = 5000\n'
    )
    _install(bin_dir, "fs_read")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)
    command = host.command("fs_read", {}, dispatch_id="x", wait_ms=999_999, lifetime_ms=999_999)

    assert command is not None
    assert command.lifetime_ms == 5000
    assert command.wait_ms == 5000


def test_a_dispatch_may_wait_past_the_manifest_wait_within_its_lifetime(tmp_path):
    """D96: a task's run step waits its whole `timeout_s`.

    The manifest's `wait_ms` is the default the core sends for a model's call,
    not a ceiling; the lifetime is. So a dispatch asking to block for 30
    minutes on a tool that lives an hour gets its 30 minutes — and one asking
    for two hours gets the hour.
    """

    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "shell",
        '[tool]\nname = "shell"\nexec = "shell"\nwait_ms = 120000\nlifetime_ms = 3600000\n',
    )
    _install(bin_dir, "shell")
    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    step = host.command("shell", {}, dispatch_id="x", wait_ms=1_800_000, lifetime_ms=1_800_000)
    assert step is not None
    assert (step.wait_ms, step.lifetime_ms) == (1_800_000, 1_800_000)

    greedy = host.command(
        "shell", {}, dispatch_id="y", wait_ms=7_200_000, lifetime_ms=7_200_000
    )
    assert greedy is not None
    assert (greedy.wait_ms, greedy.lifetime_ms) == (3_600_000, 3_600_000)


def test_a_lifetime_shorter_than_the_wait_is_refused_at_load(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "shell",
        '[tool]\nname = "shell"\nexec = "shell"\nwait_ms = 120000\nlifetime_ms = 1000\n',
    )

    with pytest.raises(ToolHostError, match="lifetime_ms"):
        ToolHost(manifests_dir=manifests, bin_dir=bin_dir)


def test_a_daemon_tool_counts_as_runnable_with_no_executable(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _manifest(
        manifests,
        "job_output",
        '[tool]\nname = "job_output"\nkind = "client"\ndaemon = true\nwait_ms = 600000\n',
    )
    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    assert "job_output" in host.names()
    assert host.daemon_tool("job_output") is not None
    # Never a process: there is nothing to build a command from.
    assert host.command("job_output", {}, dispatch_id="x") is None


def test_a_daemon_tool_naming_an_exec_is_refused_at_load(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "job_output",
        '[tool]\nname = "job_output"\nexec = "job_output"\ndaemon = true\n',
    )

    with pytest.raises(ToolHostError, match="daemon"):
        ToolHost(manifests_dir=manifests, bin_dir=bin_dir)


def test_an_exec_that_is_a_path_is_refused_at_load(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _manifest(manifests, "sneaky", '[tool]\nname = "sneaky"\nexec = "/usr/bin/env"\n')

    with pytest.raises(ToolHostError, match="bare name"):
        ToolHost(manifests_dir=manifests, bin_dir=bin_dir)


def test_the_shipped_manifests_are_ones_the_client_can_read(tmp_path):
    """Every shipped tool that has an `exec` must resolve as a bare name.

    The source tree installs under different names than `tools/bin/` spells
    (`fs_read.py`), so this stands up the installed shape rather than resolving
    against the source. What it catches is a manifest whose `exec` is a path, or
    a ceiling that is not a positive integer — a deployment error the client
    would otherwise meet as a failed dispatch.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    host = ToolHost(manifests_dir=REPO / "tools", bin_dir=bin_dir)

    # Loading the whole directory already raises on a malformed manifest; this
    # asserts the shipped set is the set the client expects to be able to run.
    for name in (
        "fs_read",
        "fs_write",
        "fs_edit",
        "fs_list",
        "fs_image",
        "fs_search",
        "shell",
        "web_fetch",
        "browse",
        "git",
        "python",
    ):
        tool = host.tool(name)
        assert tool is not None, name
        assert "/" not in tool.executable

    # A server-native tool has no executable and is skipped, not an error.
    assert host.tool("session_query") is None


def test_a_declared_path_outside_the_surface_is_refused(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.txt").write_text("x", encoding="utf-8")
    (root / "link").symlink_to("/etc")
    _manifest(
        manifests,
        "reader",
        '[tool]\nname = "reader"\nexec = "reader"\npaths = ["path"]\n',
    )
    _install(bin_dir, "reader")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)
    surfaces = {"read": [str(root)], "write": []}

    assert (
        host.command(
            "reader", {"path": str(root / "inside.txt")}, dispatch_id="d", surfaces=surfaces
        )
        is not None
    )
    with pytest.raises(PathOutsideSurface):
        host.command("reader", {"path": "/etc/passwd"}, dispatch_id="d", surfaces=surfaces)

    # A link inside the surface pointing out of it: the case a lexical prefix
    # check passes and this one must not.
    with pytest.raises(PathOutsideSurface):
        host.command(
            "reader",
            {"path": str(root / "link" / "passwd")},
            dispatch_id="d",
            surfaces=surfaces,
        )

    # No surfaces is a refusal, not an unchecked run.
    with pytest.raises(PathOutsideSurface):
        host.command("reader", {"path": str(root / "inside.txt")}, dispatch_id="d")


def test_a_mutating_tool_checks_against_the_write_surface(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    root = tmp_path / "root"
    (root / "work").mkdir(parents=True)
    _manifest(
        manifests,
        "writer",
        '[tool]\nname = "writer"\nexec = "writer"\nmutating = true\npaths = ["path"]\n',
    )
    _install(bin_dir, "writer")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    # Readable is not writable.
    with pytest.raises(PathOutsideSurface):
        host.command(
            "writer",
            {"path": str(root / "work" / "f")},
            dispatch_id="d",
            surfaces={"read": [str(root)], "write": []},
        )

    assert (
        host.command(
            "writer",
            {"path": str(root / "work" / "f")},
            dispatch_id="d",
            surfaces={"read": [str(root)], "write": [str(root / "work")]},
        )
        is not None
    )


def test_a_tool_without_declared_paths_is_not_checked(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(manifests, "worker", '[tool]\nname = "worker"\nexec = "worker"\n')
    _install(bin_dir, "worker")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    # No declaration, no check: `shell` and `python` are the unbounded path and
    # a manifest that does not claim a path argument is not pretending
    # otherwise.
    assert (
        host.command(
            "worker",
            {"path": "/etc/passwd"},
            dispatch_id="d",
            surfaces={"read": [], "write": []},
        )
        is not None
    )


def test_the_spill_directory_is_readable_whatever_the_surfaces_say(tmp_path):
    """D42: a spill note names a file the model must be able to open.

    The research account's surfaces were its runs directory alone, which does not
    contain the client's state directory, so a spilled page would have been
    named to the model and then refused to it (N180).
    """

    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    spill = tmp_path / "state" / "spill"
    spill.mkdir(parents=True)
    _manifest(
        manifests, "reader", '[tool]\nname = "reader"\nexec = "reader"\npaths = ["path"]\n'
    )
    _manifest(
        manifests,
        "writer",
        '[tool]\nname = "writer"\nexec = "writer"\nmutating = true\npaths = ["path"]\n',
    )
    _install(bin_dir, "reader", "writer")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir, spill_dir=spill)
    surfaces = {"read": [str(tmp_path / "runs")], "write": [str(tmp_path / "runs")]}
    spilled = str(spill / "web_fetch-d.out")

    assert host.command("reader", {"path": spilled}, dispatch_id="d", surfaces=surfaces)
    # Plumbing, not reach: it grants nothing to a write.
    with pytest.raises(PathOutsideSurface):
        host.command("writer", {"path": spilled}, dispatch_id="d", surfaces=surfaces)
    # And nothing beside it in the state directory.
    with pytest.raises(PathOutsideSurface):
        host.command(
            "reader",
            {"path": str(tmp_path / "state" / "session.json")},
            dispatch_id="d",
            surfaces=surfaces,
        )


def test_persist_output_rides_from_the_manifest_to_the_command(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(manifests, "fetch", '[tool]\nname = "fetch"\nexec = "fetch"\n')
    _manifest(
        manifests, "pager", '[tool]\nname = "pager"\nexec = "pager"\npersist_output = false\n'
    )
    _install(bin_dir, "fetch", "pager")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    # Undeclared persists: the failure the field prevents is a huge result inline.
    assert host.command("fetch", {}, dispatch_id="d").persist_output is True
    assert host.command("pager", {}, dispatch_id="d").persist_output is False


def test_a_persist_output_that_is_not_a_boolean_is_refused_at_load(tmp_path):
    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _manifest(manifests, "odd", '[tool]\nname = "odd"\nexec = "odd"\npersist_output = "no"\n')

    with pytest.raises(ToolHostError, match="persist_output"):
        ToolHost(manifests_dir=manifests, bin_dir=bin_dir)


def test_the_shipped_unbounded_tools_persist_and_the_pagers_do_not(tmp_path):
    """N127 and N180 together: which side of the split each shipped tool is on."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    host = ToolHost(manifests_dir=REPO / "tools", bin_dir=bin_dir)

    for name in ("shell", "python", "web_fetch", "browse", "git"):
        assert host.tool(name).persist_output, name
    for name in ("fs_read", "fs_search", "fs_image", "fs_write", "fs_edit"):
        assert not host.tool(name).persist_output, name


def _package_tree(tmp_path: Path, requires: str) -> tuple[Path, Path]:
    """One package with one tool and a host, as the shipped tree lays it out."""

    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    _manifest(
        manifests,
        "scene",
        '[tool]\nname = "scene"\nexec = "scene"\nwait_ms = 30000\nmax_output_bytes = 4096\n',
    )
    (manifests / "packages").mkdir()
    (manifests / "packages" / "studio.toml").write_text(
        '[package]\nid = "studio"\ntools = ["scene"]\n'
        f'[package.requires]\nprogram = "{requires}"\nname = "Studio"\n'
        'url = "https://studio.example/download"\n'
        '[package.host]\nexec = "studio-host"\n',
        encoding="utf-8",
    )
    _install(bin_dir, "scene", "studio-host")
    return manifests, bin_dir


def test_a_package_tool_whose_program_is_missing_says_what_to_install(tmp_path, monkeypatch):
    """The user installs the program; the refusal tells them which, and where (D101)."""

    manifests, bin_dir = _package_tree(tmp_path, "studio-that-is-not-installed")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    with pytest.raises(MissingProgram) as missing:
        host.host_for("scene")

    message = str(missing.value)
    assert "Studio" in message and "https://studio.example/download" in message


def test_a_package_tool_and_its_host_are_told_the_same_socket(tmp_path, monkeypatch):
    """One value for both ends, chosen by the client, so they cannot drift."""

    manifests, bin_dir = _package_tree(tmp_path, "sh")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    spec = host.host_for("scene")
    command = host.command("scene", {}, dispatch_id="d1")

    assert spec is not None and command is not None and command.env is not None
    assert spec.executable == bin_dir / "studio-host"
    assert spec.environment["NEUMAN_HOST_SOCKET"] == str(tmp_path / "neuman-studio.sock")
    assert command.env["NEUMAN_HOST_SOCKET"] == spec.environment["NEUMAN_HOST_SOCKET"]
    assert spec.environment["NEUMAN_HOST_PROGRAM"].endswith("/sh")


def test_a_built_in_tool_has_no_host(tmp_path):
    manifests, bin_dir = _package_tree(tmp_path, "sh")
    _manifest(
        manifests,
        "fs_read",
        '[tool]\nname = "fs_read"\nexec = "fs_read"\nwait_ms = 30000\nmax_output_bytes = 4096\n',
    )
    _install(bin_dir, "fs_read")
    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir)

    assert host.host_for("fs_read") is None


def test_the_shipped_packages_load_on_the_client(tmp_path, monkeypatch):
    """The client reads the same `tools/packages/` the server validates (D2, D3).

    With no Blender on the PATH, a Blender tool is refused by name — which is
    only possible if the client knows the tool is in the package.
    """

    monkeypatch.setenv("PATH", str(tmp_path))
    host = ToolHost(manifests_dir=REPO / "tools", bin_dir=REPO / "tools" / "bin")

    with pytest.raises(MissingProgram, match="Blender"):
        host.host_for("blender_view")
    assert host.host_for("fs_read") is None


def test_the_drops_directory_is_readable_and_never_writable(tmp_path):
    """D102: an attached drop must open for `fs_read` whatever the surfaces say,
    and a mutating tool gets nothing from it — the same terms as `spill/`."""

    manifests = tmp_path / "manifests"
    bin_dir = tmp_path / "bin"
    drops = tmp_path / "state" / "drops"
    (drops / "abc").mkdir(parents=True)
    _manifest(
        manifests, "reader", '[tool]\nname = "reader"\nexec = "reader"\npaths = ["path"]\n'
    )
    _manifest(
        manifests,
        "writer",
        '[tool]\nname = "writer"\nexec = "writer"\nmutating = true\npaths = ["path"]\n',
    )
    _install(bin_dir, "reader", "writer")

    host = ToolHost(manifests_dir=manifests, bin_dir=bin_dir, drops_dir=drops)
    surfaces = {"read": [str(tmp_path / "runs")], "write": [str(tmp_path / "runs")]}
    dropped = str(drops / "abc" / "notes.pdf")

    assert host.command("reader", {"path": dropped}, dispatch_id="d", surfaces=surfaces)
    with pytest.raises(PathOutsideSurface):
        host.command("writer", {"path": dropped}, dispatch_id="d", surfaces=surfaces)
