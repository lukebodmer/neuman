"""`python -m neuman_admin`, equivalent to the `neuman-admin` script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
