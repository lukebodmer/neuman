"""Resolve a tool name to an executable the client trusts.

A dispatch carries a *name*, never a path (D3). The map from name to program is
the client's own manifest set, installed beside it, and never supplied by the
server — so a dispatch cannot ask for a binary that is not one of Neuman's tools,
and a compromised or buggy server cannot turn the client into an arbitrary
which runs it.

The manifest is the same file the server gates on, but the client reads it for
one thing only: which name to execute and what ceiling to apply. It re-derives
no policy from it — by the time a dispatch arrives it is already gated and
approved, and the client holds no authority to second-guess the verdict (SPEC
section 3).
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .executor import Command
from .platform import BROWSE_SOCKET_ENV, HOST_PROGRAM_ENV, HOST_SOCKET_ENV, host_socket

__all__ = [
    "HostSpec",
    "MissingProgram",
    "PathOutsideSurface",
    "Tool",
    "ToolHost",
    "ToolHostError",
]

DEFAULT_WAIT_MS = 120_000
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024


class ToolHostError(RuntimeError):
    """The client's own manifest set is malformed. A deployment error."""


class PathOutsideSurface(RuntimeError):
    """A declared path argument is outside the account's declared surfaces."""


class MissingProgram(RuntimeError):
    """A tool package needs a program this machine does not have (D101).

    Neuman never installs it. The message names the program and where to get
    it, and reaches the model as the dispatch's refusal, so the person is told
    in the conversation what to install.
    """


@dataclass(frozen=True, slots=True)
class Package:
    """What the client needs to know about one tool package."""

    id: str
    tools: tuple[str, ...]
    # {"program", "name", "url"}: a program the user installs themselves.
    requires: dict[str, str] | None = None
    # The host executable's bare name, resolved like a manifest's `exec`.
    host: str | None = None


@dataclass(frozen=True, slots=True)
class HostSpec:
    """A package host the daemon should have running before a tool dials it."""

    package: str
    executable: Path
    socket: Path
    environment: dict[str, str]


@dataclass(frozen=True, slots=True)
class Tool:
    """What the client needs to know about one tool."""

    name: str
    # Empty for a `daemon` tool, which the daemon answers with no process.
    executable: str
    wait_ms: int = DEFAULT_WAIT_MS
    lifetime_ms: int = DEFAULT_WAIT_MS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    persist_output: bool = True
    mutating: bool = False
    # Parameter names whose values are filesystem paths. Declared by the
    # manifest so the host has one source for which arguments to check; a tool
    # that declares none is simply not checked.
    paths: tuple[str, ...] = ()
    daemon: bool = False


class ToolHost:
    """A manifest set and the bin directory its `exec` names resolve against."""

    def __init__(
        self,
        *,
        manifests_dir: str | Path,
        bin_dir: str | Path,
        browse_socket: str | None = None,
        spill_dir: str | Path | None = None,
        drops_dir: str | Path | None = None,
    ) -> None:
        self._bin = Path(bin_dir)
        self._tools = _load(Path(manifests_dir))
        self._packages = _load_packages(Path(manifests_dir) / "packages", self._tools)
        self._package_of = {
            tool: package for package in self._packages.values() for tool in package.tools
        }
        # Where `browse` finds the browser bridge. The client owns both ends —
        # the bridge it starts and the tool it runs — so it hands the path over
        # rather than letting the two default independently and drift.
        self._browse_socket = browse_socket
        # Where the executor writes spilled output. Readable whatever the
        # account's surfaces say, because it holds only this account's own
        # dispatch output and a spill note the model cannot follow is worth
        # nothing (D42, N125). Never writable through a declared path.
        self._spill_dir = os.fspath(spill_dir) if spill_dir is not None else None
        # Where the UI server writes a file dropped on the window. Readable for
        # the same reason: it holds only what this account's own person handed
        # over, and an attachment the model cannot open is worth nothing (D102).
        self._drops_dir = os.fspath(drops_dir) if drops_dir is not None else None

    def tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def daemon_tool(self, name: str) -> Tool | None:
        """The manifest of a tool the daemon answers itself, or None."""

        tool = self._tools.get(name)
        return tool if tool is not None and tool.daemon else None

    def resolve(self, name: str) -> Path | None:
        """The executable for `name`, or None when the client cannot run it.

        None covers both "no such tool here" and "the executable is missing from
        the installation"; both are `unknown_tool` to a dispatch, and neither
        should be distinguishable to the server.
        """

        tool = self._tools.get(name)
        if tool is None or tool.daemon:
            return None
        executable = self._bin / tool.executable
        if not executable.is_file():
            return None
        return executable

    def host_for(self, name: str) -> HostSpec | None:
        """What must be running for `name` to work, once its program is checked.

        Raises `MissingProgram` when `name`'s package requires a program this
        machine lacks. Checked per dispatch rather than at startup, so a person
        who installs it is not also told to restart the client. None for a tool
        in no package, or in a package with no host.
        """

        package = self._package_of.get(name)
        if package is None:
            return None
        program = None
        if package.requires is not None:
            wanted = package.requires
            program = shutil.which(wanted["program"])
            if program is None:
                raise MissingProgram(
                    f"{name} needs {wanted['name']}, which is not installed on this "
                    f"machine (no `{wanted['program']}` on the PATH the Neuman client "
                    f"runs with). Install {wanted['name']} from {wanted['url']} and try again."
                )
        if package.host is None:
            return None
        socket = host_socket(package.id)
        if socket is None:
            raise OSError(
                f"the {package.id} package's host needs a private runtime directory "
                "for its socket, and this session has none"
            )
        executable = self._bin / package.host
        if not executable.is_file():
            raise FileNotFoundError(
                f"the {package.id} package's host `{package.host}` is not installed"
            )
        environment = {**os.environ, HOST_SOCKET_ENV: str(socket)}
        if program is not None:
            environment[HOST_PROGRAM_ENV] = program
        return HostSpec(package.id, executable, socket, environment)

    def names(self) -> frozenset[str]:
        """The tool names this client can actually run.

        What goes in the handshake, so the core never dispatches a name the
        client would answer `unknown_tool` to. An installed manifest whose
        executable is missing does not count: the client cannot run it, and
        saying otherwise would turn a deployment error into a failed dispatch.
        A `daemon` tool has no executable to miss, and counts.
        """

        return frozenset(
            name
            for name, tool in self._tools.items()
            if tool.daemon or self.resolve(name) is not None
        )

    def command(
        self,
        name: str,
        args: dict[str, object],
        *,
        dispatch_id: str,
        cwd: str | None = None,
        wait_ms: int | None = None,
        lifetime_ms: int | None = None,
        surfaces: dict[str, list[str]] | None = None,
    ) -> Command | None:
        """Build the `Command` for one dispatch, or None if the tool is unknown.

        The arguments ride as one JSON argv element, which is the convention
        every tool already reads (`json.loads(sys.argv[1])`).

        The lifetime is the lower of the manifest's and the dispatch's: neither
        side may extend the other. The wait is the dispatch's, held under that
        lifetime — and may be longer than the manifest's own `wait_ms`, which is
        only the default the core sends for a model's call. The core asks for
        more only for a command it wrote itself (D96); this side cannot tell the
        two apart and does not need to, because the lifetime still bounds both.
        """

        tool = self._tools.get(name)
        executable = self.resolve(name)
        if tool is None or executable is None:
            return None
        self._check_paths(tool, args, cwd=cwd, surfaces=surfaces)
        lifetime = min(lifetime_ms, tool.lifetime_ms) if lifetime_ms else tool.lifetime_ms
        extra: dict[str, str] = {}
        if self._browse_socket:
            extra[BROWSE_SOCKET_ENV] = self._browse_socket
        package = self._package_of.get(name)
        if package is not None and package.host is not None:
            socket = host_socket(package.id)
            if socket is not None:
                extra[HOST_SOCKET_ENV] = str(socket)
        env = {**os.environ, **extra} if extra else None
        return Command(
            argv=(str(executable), json.dumps(args, separators=(",", ":"))),
            # Unique per dispatch: the spill file is named from this and the
            # model reads it back by that path.
            name=f"{name}-{dispatch_id}",
            cwd=cwd,
            env=env,
            wait_ms=min(wait_ms or tool.wait_ms, lifetime),
            lifetime_ms=lifetime,
            max_output_bytes=tool.max_output_bytes,
            persist_output=tool.persist_output,
        )

    def _check_paths(
        self,
        tool: Tool,
        args: dict[str, object],
        *,
        cwd: str | None,
        surfaces: dict[str, list[str]] | None,
    ) -> None:
        """Refuse a declared path argument outside the account's surfaces.

        A guardrail against a confused or injected model, not a boundary: it
        covers the structured tools, which are the quiet path because they are
        not mutating and so do not prompt under `safe`. `shell` and `python`
        reach whatever the user reaches and are not checked at all.

        Resolved with `realpath` rather than compared as written, because the
        lexical check is the one a symlink defeats — a link inside a granted
        directory pointing out of it is exactly the shape a string prefix test
        misses.
        """

        if not tool.paths:
            return
        if surfaces is None:
            raise PathOutsideSurface(
                f"{tool.name} takes a path and the core sent no surfaces to check it against"
            )

        allowed = list(surfaces.get("write" if tool.mutating else "read", []))
        if not tool.mutating:
            allowed.extend(d for d in (self._spill_dir, self._drops_dir) if d is not None)
        base = cwd or os.getcwd()
        for parameter in tool.paths:
            value = args.get(parameter)
            if not isinstance(value, str) or not value:
                # An absent or non-string path is the tool's own error to
                # report; this check is about reach, not shape.
                continue
            candidate = value if os.path.isabs(value) else os.path.join(base, value)
            if not _within(candidate, allowed):
                raise PathOutsideSurface(
                    f"{parameter}={value!r} is outside the paths this account may "
                    f"{'write' if tool.mutating else 'read'}"
                )


def _load(manifests_dir: Path) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    for manifest in sorted(manifests_dir.glob("*/manifest.toml")):
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ToolHostError(f"{manifest}: {error}") from error

        section = data.get("tool")
        if not isinstance(section, dict):
            raise ToolHostError(f"{manifest}: no [tool] table")

        name = section.get("name")
        if not isinstance(name, str) or not name:
            raise ToolHostError(f"{manifest}: name must be a non-empty string")
        wait_ms = _positive_int(section.get("wait_ms"), DEFAULT_WAIT_MS, manifest, "wait_ms")
        lifetime_ms = _positive_int(
            section.get("lifetime_ms"), wait_ms, manifest, "lifetime_ms"
        )
        if lifetime_ms < wait_ms:
            raise ToolHostError(f"{manifest}: lifetime_ms must be at least wait_ms")
        daemon = _bool(section.get("daemon"), False, manifest, "daemon")

        executable = section.get("exec")
        if daemon:
            if executable is not None:
                raise ToolHostError(f"{manifest}: a daemon tool names no exec")
            tools[name] = Tool(
                name=name,
                executable="",
                wait_ms=wait_ms,
                lifetime_ms=lifetime_ms,
                max_output_bytes=_positive_int(
                    section.get("max_output_bytes"),
                    DEFAULT_MAX_OUTPUT_BYTES,
                    manifest,
                    "max_output_bytes",
                ),
                persist_output=False,
                daemon=True,
            )
            continue
        if executable is None:
            # A server-native tool has no program to run here. Not an error:
            # the client simply does not offer it.
            continue
        if not isinstance(executable, str) or not executable:
            raise ToolHostError(f"{manifest}: exec must be a non-empty string")
        # A bare name joined to the bin directory. A path would make the
        # manifest's location the authority on what runs, which is the thing
        # this module exists to prevent.
        if "/" in executable or executable in (".", ".."):
            raise ToolHostError(f"{manifest}: exec must be a bare name, got {executable!r}")

        tools[name] = Tool(
            name=name,
            executable=executable,
            wait_ms=wait_ms,
            lifetime_ms=lifetime_ms,
            max_output_bytes=_positive_int(
                section.get("max_output_bytes"),
                DEFAULT_MAX_OUTPUT_BYTES,
                manifest,
                "max_output_bytes",
            ),
            persist_output=_bool(
                section.get("persist_output"), True, manifest, "persist_output"
            ),
            mutating=bool(section.get("mutating", False)),
            paths=_string_list(section.get("paths"), manifest, "paths"),
        )
    return tools


def _load_packages(directory: Path, tools: dict[str, Tool]) -> dict[str, Package]:
    """The client's half of the package contract (D101): which tools, needing what.

    The server validates the same files against the schema; this reads the
    three fields that decide what happens on this machine, and refuses a file
    whose shape would make one of them mean something else.
    """

    packages: dict[str, Package] = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ToolHostError(f"{path}: {error}") from error
        section = data.get("package")
        if not isinstance(section, dict) or section.get("id") != path.stem:
            raise ToolHostError(f"{path}: no [package] table whose id is {path.stem!r}")
        names = _string_list(section.get("tools"), path, "tools")
        unknown = [name for name in names if name not in tools]
        if unknown:
            raise ToolHostError(
                f"{path}: names tools this client has no manifest for: {unknown}"
            )
        requires = section.get("requires")
        if requires is not None and not (
            isinstance(requires, dict)
            and all(isinstance(requires.get(key), str) for key in ("program", "name", "url"))
        ):
            raise ToolHostError(f"{path}: requires must name a program, a name and a url")
        host = (section.get("host") or {}).get("exec")
        if host is not None and (
            not isinstance(host, str) or "/" in host or host in (".", "..")
        ):
            raise ToolHostError(f"{path}: a host exec must be a bare name, got {host!r}")
        packages[path.stem] = Package(id=path.stem, tools=names, requires=requires, host=host)
    return packages


def _positive_int(value: object, default: int, manifest: Path, field: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolHostError(f"{manifest}: {field} must be a positive integer")
    return value


def _bool(value: object, default: bool, manifest: Path, field: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolHostError(f"{manifest}: {field} must be a boolean")
    return value


def _string_list(value: object, manifest: Path, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolHostError(f"{manifest}: {field} must be an array of strings")
    return tuple(value)


def _within(candidate: str, surfaces: Sequence[str]) -> bool:
    """Is `candidate`, symlinks resolved, inside one of these surfaces?"""

    resolved = os.path.realpath(candidate)
    for surface in surfaces:
        root = os.path.realpath(surface).rstrip("/")
        if resolved == root or resolved.startswith(f"{root}/"):
            return True
    return False
