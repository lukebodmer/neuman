"""Neuman's decision core.

A single unprivileged process that owns the database and decides everything that
may happen: who is logged in, what policy applies, which tools may run, what the
model sees and does, and what lands in the audit chain. It never enforces — the
kernel does that — and it never runs as root.
"""

__version__ = "0.1.1"

__all__ = ["__version__"]
