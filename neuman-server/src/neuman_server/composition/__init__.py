"""App assembly and the two transport surfaces.

Wires foundation, decision, and adapters together and owns process-level
concerns: lifespan, startup ordering, health, request correlation, background
jobs. It makes no decisions of its own.

Allowed imports: everything. Nothing imports composition (docs/decisions.md D7).
"""

from .app import Services, create_app, lifespan

__all__ = ["Services", "create_app", "lifespan"]
