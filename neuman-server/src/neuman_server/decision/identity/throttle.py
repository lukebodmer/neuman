"""Login throttling.

A sliding window per account name, in process. It is the whole story now:
verification is an argon2id hash in this process, so a hash is a guess that
costs the CPU of every worker thread, and there is no host `pam_faillock`
behind the endpoint to absorb a flood. An unthrottled login route is therefore
a way to spend the appliance's cores from an unauthenticated socket.

Keyed by the submitted name, not by the account (which may not resolve), and
never revealing whether the name exists: an unknown name throttles exactly like
a known one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = ["LoginThrottle", "ThrottleDecision"]


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    allowed: bool
    retry_after_s: float = 0.0


@dataclass
class LoginThrottle:
    """A sliding-window counter of recent failures per name."""

    max_failures: int = 10
    window: timedelta = timedelta(minutes=5)
    # Bound the key space: a flood of distinct names must not grow unboundedly.
    max_tracked: int = 4096

    _failures: dict[str, deque[datetime]] = field(default_factory=dict, init=False, repr=False)

    def check(self, name: str, *, now: datetime | None = None) -> ThrottleDecision:
        """Whether an attempt for ``name`` may proceed."""

        moment = now or datetime.now(UTC)
        recent = self._recent(name, moment)
        if len(recent) < self.max_failures:
            return ThrottleDecision(True)
        retry_after = (recent[0] + self.window - moment).total_seconds()
        return ThrottleDecision(False, max(retry_after, 0.0))

    def record_failure(self, name: str, *, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        recent = self._recent(name, moment)
        recent.append(moment)
        if name not in self._failures:
            self._evict_if_needed()
            self._failures[name] = recent

    def record_success(self, name: str) -> None:
        """A successful login clears the window for that name."""

        self._failures.pop(name, None)

    def _recent(self, name: str, now: datetime) -> deque[datetime]:
        recent = self._failures.get(name)
        if recent is None:
            return deque()
        cutoff = now - self.window
        while recent and recent[0] < cutoff:
            recent.popleft()
        if not recent:
            self._failures.pop(name, None)
        return recent

    def _evict_if_needed(self) -> None:
        if len(self._failures) < self.max_tracked:
            return
        # Drop the entry whose most recent failure is oldest: it is the closest
        # to expiring anyway.
        oldest = min(self._failures, key=lambda key: self._failures[key][-1])
        self._failures.pop(oldest, None)
