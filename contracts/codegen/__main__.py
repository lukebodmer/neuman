"""CLI: validate contracts, or (re)generate the Python package."""

from __future__ import annotations

import argparse
import sys

from . import generate, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="contracts", description="Neuman contracts tooling"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write gen/python/neuman_contracts")
    gen.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when committed artifacts would change",
    )
    sub.add_parser("validate", help="validate schemas and fixtures")
    args = parser.parse_args(argv)

    if args.command == "validate":
        validate.run()
        return 0
    if args.check:
        drift = generate.check()
        if drift:
            print(
                "stale generated artifacts: " + ", ".join(drift),
                file=sys.stderr,
            )
            return 1
        print("generated artifacts are fresh")
        return 0
    count = generate.write()
    print(f"wrote {count} modules to {generate.GEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
