"""The model catalog: the baseline's models, resolved to where they actually send.

The catalog is the `providers` and `models` halves of the runtime baseline, the
document in Postgres that the administration page edits (D68). It is one
document rather than a table of its own because it has no joins and two
readers, both single-name lookups, and because a model's entry and the role
granting it then change in one compare-and-set.

What an entry *is* comes from two places. One added from a preset carries that
preset's facts, which the server holds it to (D76, `presets.py`); one declared
by hand carries whatever the operator stated. This module does not distinguish
them: by the time a name is resolved, both are claims the gate has accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...foundation.logging import get_logger
from .ports import ModelTarget, ReasoningControl

__all__ = ["ModelCatalog"]

_log = get_logger("neuman.server.harness.catalog")


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Resolves a granted model name against the declared baseline.

    Holds the baseline document rather than a path, because reading and
    validating a file is foundation's job (`foundation/state/baseline.py`) and
    deciding what it *means* is this layer's (docs/decisions.md (D2, D7)).
    """

    baseline: dict[str, Any]
    # Where a model on this machine is reached: the runtime host's router
    # (D78). A deployment fact, so it is the server's setting rather than a
    # value in the document; empty on an appliance with no runtime host.
    local_endpoint: str = ""

    @property
    def _models(self) -> dict[str, Any]:
        return self.baseline.get("models") or {}

    @property
    def _providers(self) -> dict[str, Any]:
        return self.baseline.get("providers") or {}

    async def resolve(self, name: str) -> ModelTarget | None:
        """The target for `name`, or None when it is not declared.

        Async because it implements `Harness.resolve_model`, which is a
        `Callable[[str], Awaitable[ModelTarget | None]]` — the port was shaped
        around a database read and stays that way so a future source that
        genuinely does I/O needs no change at the call site.
        """

        entry = self._models.get(name)
        if not isinstance(entry, dict):
            return None

        provider = self._provider_of(entry)
        if provider is None:
            # Declared against a provider that is not. The module asserts this
            # cannot happen, so reaching it means the baseline was written by
            # something else — refuse rather than fall back to the default
            # endpoint, which would send this model's request to a different
            # provider with a different key and fail as a confusing 401.
            _log.warning(
                "model names an undeclared provider",
                fields={"model": name, "provider": entry.get("provider")},
            )
            return None

        return ModelTarget(
            name=name,
            provider_model=entry["provider_model"],
            endpoint=provider["url"],
            api_key_file=provider.get("api_key_file"),
            vision=bool(entry.get("vision", False)),
            context_tokens=max(0, int(entry.get("context_tokens", 0) or 0)),
            max_tokens=max(0, int(entry.get("max_tokens", 0) or 0)),
            reasoning=_reasoning(entry.get("reasoning")),
            input_cost_per_m=float(entry.get("input_cost_per_m", 0) or 0),
            cached_input_cost_per_m=(
                float(entry["cached_input_cost_per_m"])
                if entry.get("cached_input_cost_per_m") is not None
                else None
            ),
            output_cost_per_m=float(entry.get("output_cost_per_m", 0) or 0),
        )

    def _provider_of(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """The endpoint and key `entry` is sent with, or None when there is none."""

        if "local" in entry:
            if not self.local_endpoint:
                return None
            return {"url": self.local_endpoint, "api_key_file": None}
        provider = self._providers.get(entry.get("provider"))
        return provider if isinstance(provider, dict) else None

    def describe(self, names: list[str]) -> dict[str, dict[str, Any]]:
        """What the catalog knows about each of `names`, for the model panel.

        Scoped to the names the caller passes — which are the ones policy grants
        — so the panel cannot become a way to enumerate the whole catalog.

        Costs are included because a user choosing between "free, on my GPU,
        slower" and "paid, someone else's, faster" is making an informed choice
        only if both facts are in front of them. They were in the old table from
        the first migration and read by nothing, and the CLI that wrote the rows
        had no flags to set them, so every registered model cost zero.
        """

        out: dict[str, dict[str, Any]] = {}
        for name in names:
            entry = self._models.get(name)
            if not isinstance(entry, dict):
                continue
            provider = self._provider_of(entry)
            if provider is None:
                continue
            out[name] = {
                # Stated rather than inferred from the endpoint: a loopback URL
                # is also what a hand-declared provider on this machine has.
                "local": "local" in entry,
                "endpoint": provider["url"],
                "provider_model": entry["provider_model"],
                "vision": bool(entry.get("vision", False)),
                "context_tokens": max(0, int(entry.get("context_tokens", 0) or 0)),
                "max_tokens": max(0, int(entry.get("max_tokens", 0) or 0)),
                "input_cost_per_m": float(entry.get("input_cost_per_m", 0) or 0),
                "output_cost_per_m": float(entry.get("output_cost_per_m", 0) or 0),
                # None rather than 0 when the operator declared nothing: a
                # provider that does not discount cache hits is not a provider
                # whose cache hits are free, and `0` would read as the latter
                # and price a warm prompt at nothing.
                "cached_input_cost_per_m": (
                    float(entry["cached_input_cost_per_m"])
                    if entry.get("cached_input_cost_per_m") is not None
                    else None
                ),
                # Verbatim, so the client offers exactly the levels the
                # operator declared and the server will accept. A client that
                # inferred them would be a second opinion about the catalog.
                "reasoning": entry.get("reasoning"),
            }
        return out


def _reasoning(entry: Any) -> ReasoningControl | None:
    """The declared reasoning control, or None when the model declares none.

    None is the ordinary case and the safe one: a model that is not asked to
    reason runs at whatever its own default is, which is correct both for a
    model without the capability and for one already tuned.
    """

    if not isinstance(entry, dict):
        return None
    control = entry.get("control")
    if not control or control == "none":
        return None

    levels = tuple(str(level) for level in entry.get("levels") or ())
    default = str(entry.get("default") or "")
    # A default outside the declared set would refuse every turn that did not
    # name a level — the model would be unusable rather than misconfigured.
    # Dropped here so the failure is one unusual turn, not all of them.
    if default and levels and default not in levels:
        _log.warning(
            "declared reasoning default is not among its levels; ignoring it",
            fields={"default": default, "levels": list(levels)},
        )
        default = ""
    return ReasoningControl(control=str(control), levels=levels, default=default)
