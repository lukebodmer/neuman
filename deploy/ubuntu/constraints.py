"""Write `constraints.txt` from the Python environment the tests run in.

The deb ships wheels from PyPI; the suite runs against the flake's `serverEnv`.
Generating the pins from that environment, rather than resolving afresh, is
what makes those the same set: an Ubuntu appliance then runs the dependency
versions CI proved, and the deb is not a second resolution that happens to
share a major number (D107).

Run inside the dev shell::

    nix develop -c python3 deploy/ubuntu/constraints.py > deploy/ubuntu/constraints.txt

`psycopg[binary]` is the extra the wheel install takes. Nix builds the C module
against libpq and has no `psycopg-binary` distribution, so it is pinned to the
`psycopg` version by hand.
"""

from __future__ import annotations

import re
from importlib import metadata

# What the two packages the deb installs require at runtime. Walking their
# metadata finds the rest; extras that pull test or dev tools are not followed
# because the environment does not carry them.
DIRECT = (
    "argon2-cffi",
    "httpx",
    "jsonschema",
    "psycopg",
    "psycopg-pool",
    "starlette",
    "uvicorn",
    "websockets",
)

# Distributions PyPI supplies as a separate wheel for the same dependency.
ALIASED = {"psycopg": ("psycopg-binary",)}


def _key(name: str) -> str:
    return name.lower().replace("_", "-")


def _requirements(dist: metadata.Distribution) -> list[str]:
    names: list[str] = []
    for requirement in dist.requires or []:
        # An extra is an install path this appliance does not take. Its marker
        # names test or format tooling that happens to be in the dev shell, so
        # following it would pin pytest into the deb.
        marker = requirement.split(";", 1)[1] if ";" in requirement else ""
        if "extra" in marker:
            continue
        # `attrs>=22.2.0`, `anyio (<5,>=3.0)`, `psycopg[binary]`: the name is
        # the leading identifier and everything after it is a constraint or an
        # extra. A requirement whose marker excludes this environment names
        # nothing installed, so the walk drops it.
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        if match:
            names.append(match.group(0))
    return names


def main() -> int:
    versions: dict[str, str] = {}
    stack = list(DIRECT)
    while stack:
        name = stack.pop()
        key = _key(name)
        if key in versions:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        versions[key] = dist.version
        stack.extend(_requirements(dist))

    for name, aliases in ALIASED.items():
        for alias in aliases:
            versions[_key(alias)] = versions[_key(name)]

    for name in sorted(versions):
        print(f"{name}=={versions[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
