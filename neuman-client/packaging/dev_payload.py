#!/usr/bin/env python3
"""Build a runnable client payload from a Python interpreter and this tree.

This is the pre-package test layout: a private virtual environment, the client
console scripts, the tool executables, the manifest set and the static UI. It
exists so the Mac checklist can run from a checkout before the signed package
is built.

It is not the `.pkg` layout. A virtual environment is not relocatable, so the
signed package will install the bundled interpreter and a target tree instead.
The tool payload and the wrappers here are the same shape that build will use.

It deliberately does not use Nix. The macOS artifact must not require Nix,
Python or a terminal on the customer's machine.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


def _version(python: Path) -> tuple[int, int]:
    output = subprocess.check_output(
        [str(python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
        text=True,
    )
    major, minor = output.split()
    return int(major), int(minor)


def _wrap(path: Path, python: Path, lib: Path, source: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"#!{python}",
                "import runpy, sys",
                f"sys.path.insert(0, {str(lib)!r})",
                f"runpy.run_path({str(lib / source)!r}, run_name='__main__')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _manifest_exec(manifest: Path) -> str | None:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    if tool.get("kind") == "server-native":
        return None
    value = tool.get("exec")
    return value if isinstance(value, str) and value else None


def build(repo: Path, out: Path, python: Path, *, force: bool) -> None:
    if out.exists():
        if not force:
            raise SystemExit(f"{out} exists; pass --force to replace it")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    venv = out / "venv"
    subprocess.run([str(python), "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--disable-pip-version-check",
            str(repo / "neuman-client"),
        ],
        check=True,
    )

    lib = out / "lib"
    lib.mkdir()
    for source in sorted((repo / "tools" / "lib").glob("*.py")):
        shutil.copy2(source, lib / source.name)

    tools = out / "tools"
    tools.mkdir()
    registry = out / "registry"
    registry.mkdir()
    git = shutil.which("git")
    if git is None:
        raise SystemExit(
            "git is required for the git tool; install the Xcode command line tools"
        )

    for manifest in sorted((repo / "tools").glob("*/manifest.toml")):
        name = manifest.parent.name
        exec_name = _manifest_exec(manifest)
        if exec_name is None:
            continue
        source = repo / "tools" / "bin" / f"{name}.py"
        if not source.is_file():
            raise SystemExit(f"manifest {name!r} names {exec_name!r}, but {source} is missing")

        installed = lib / f"{name}.py"
        if name == "git":
            installed.write_text(
                source.read_text(encoding="utf-8").replace("@git@", git),
                encoding="utf-8",
            )
        else:
            shutil.copy2(source, installed)
        _wrap(tools / exec_name, venv / "bin" / "python", lib, installed.name)

        target = registry / name
        target.mkdir()
        shutil.copy2(manifest, target / "manifest.toml")

    web = out / "web"
    shutil.copytree(repo / "neuman-client" / "web" / "src", web)

    client = out / "bin" / "neuman-client"
    browser = out / "bin" / "neuman-browser"
    client.parent.mkdir()
    client.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"export NEUMAN_CLIENT_MANIFESTS={shlex.quote(str(registry))}",
                f"export NEUMAN_CLIENT_BIN={shlex.quote(str(tools))}",
                f"export NEUMAN_CLIENT_WEB={shlex.quote(str(web))}",
                f'exec {shlex.quote(str(venv / "bin" / "neuman-client"))} "$@"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    client.chmod(0o755)
    browser.write_text(
        f'#!/bin/sh\nexec {shlex.quote(str(venv / "bin" / "neuman-browser"))} "$@"\n',
        encoding="utf-8",
    )
    browser.chmod(0o755)

    print(f"payload: {out}")
    print(f"try:     {client} doctor")


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=repo)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    major, minor = _version(args.python)
    if (major, minor) < (3, 12):
        parser.error(f"{args.python} is Python {major}.{minor}; 3.12 or later is required")

    build(args.repo.resolve(), args.out.resolve(), args.python.resolve(), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
