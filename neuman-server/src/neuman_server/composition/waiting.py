"""Holding a request until the GPU sample is newer than what the caller has.

Shared by the model panel and the overview, which both draw the GPU and both
used to poll for it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

__all__ = ["until_newer_sample"]


# How long a panel's request is held waiting for a newer sample. Under the
# client proxy's timeout, and long enough that an idle open panel costs a
# request every half minute rather than every few seconds.
_HOLD_S = 25.0
# How often the held request looks at the sample: a stat and a small read of a
# local file, so a quarter second is both prompt and nearly free.
_LOOK_S = 0.25


async def until_newer_sample(gpu: Any, after: float) -> None:
    """Return when the GPU sample is newer than `after`, or after `_HOLD_S`.

    The bar used to lag by two clocks — a sample every five seconds and a poll
    every five — so a change took up to ten to show. The sample is now written
    when the runtime host loads or unloads something (D30, D78), and this lets
    the panel see it within a quarter second without polling the server: one
    request per change, or one per `hold_s` when nothing changes.
    """

    deadline = time.monotonic() + _HOLD_S
    while time.monotonic() < deadline:
        sampled = gpu.gpu().get("sampled_at")
        if not isinstance(sampled, (int, float)) or sampled > after:
            # No sample to wait for is an answer too: the panel falls back to
            # a slow poll rather than holding requests that cannot be answered.
            return
        await asyncio.sleep(_LOOK_S)
