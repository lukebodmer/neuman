"""Neuman's tools, run on the user's own machine.

The client is the executor, the browser host and the UI (SPEC section 2). It
holds no policy authority: it receives dispatches that were already gated and
approved, runs them as the acting user, and returns what happened.
"""
