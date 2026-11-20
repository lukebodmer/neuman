"""Port implementations: the only place outside-world knowledge lives.

Each adapter implements a port declared in `decision/` and is only ever called
through it. Allowed imports: decision, foundation. Never imported by decision
(docs/decisions.md D7).
"""
