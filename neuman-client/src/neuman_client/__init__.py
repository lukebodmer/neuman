"""Neuman's tools, run on the user's own machine.

The client is the executor, the browser host and the UI (SPEC section 2). It
holds no policy authority: it receives dispatches that were already gated and
approved, runs them as the acting user, and returns what happened.
"""

__all__ = ["__version__"]

# The product release this code is, from the same `pyproject.toml` the package is
# cut from. Not the build id: that answers "which code served this turn" and may
# be a store path (`build.py`). This is the number a person is told to install,
# and the one the appliance compares against its floor (D111).
__version__ = "0.1.1"
