"""`python -m neuman_client`: the same entry point the console script names.

The package's wrappers start the client this way rather than through a venv's
`bin/`, so the interpreter is the host's and `PYTHONPATH` is the installation
contract — the same arrangement as the server's package (D107, D108).
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
