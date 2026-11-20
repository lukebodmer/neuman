"""The admin's CLI for a running Neuman appliance.

Separate from `neuman-server` because it is a different trust position: the
server is a long-lived daemon holding the database pool, and this is a
short-lived process an admin runs. It shares no code with the server — only
the wire shapes of the admin routes — so it can be installed on a machine that
does not run one, and so nothing it does can affect a running appliance except
through the socket.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
