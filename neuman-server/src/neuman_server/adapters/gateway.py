"""The model gateway: a thin OpenAI-compatible streaming client.

Neuman owns this (docs/decisions.md D20). No LiteLLM, no proxy framework — keys, budgets, the
model catalog, and usage accounting are our own tables enforced by our own code,
and multi-provider routing is configuration (an endpoint plus a model name), not
a bundled service.

The v1 alternative cost a bespoke Nix package that generated a Prisma client at
build time, a patched `recursive_type_depth`, a pre-seeded CLI cache so the
Python wrapper would not shell out to `npm`, an 8 GiB test VM, and a 1800-second
startup timeout — and still left spend views that were never created. None of
that bought anything this file does not do.

This adapter translates the wire format into :class:`Chunk`s and nothing else.
It makes no decisions: budget admission happens before the call, in the harness,
against our own accounting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..decision.harness.ports import Chunk
from ..foundation.logging import get_logger

__all__ = ["GatewayError", "ModelClient"]

_log = get_logger("neuman.server.adapters.gateway")

_DONE = "[DONE]"


class GatewayError(RuntimeError):
    """The gateway refused or failed the request."""


@dataclass(slots=True)
class ModelClient:
    """Implements `decision.harness.ports.ModelGateway`."""

    # No base URL and no key. Both are per provider: the catalog resolves a
    # model to its endpoint and its credential's path, and both arrive as
    # arguments to `stream`. This adapter is a singleton shared by every model,
    # so anything held here is sent to every provider — which is exactly how a
    # second provider came to receive the first one's bearer token.
    timeout_s: float = 300.0
    # An `httpx` transport, injected only by tests. Present because the
    # alternative was monkeypatching `httpx.AsyncClient` globally, which is
    # fragile in a way that can mask the bug it is meant to catch: a patch that
    # silently stops applying leaves a test that passes without testing
    # anything. `None` in production, so the real path is the default path.
    transport: Any = None

    async def stream(
        self,
        request: dict[str, Any],
        *,
        endpoint: str | None = None,
        api_key_file: str | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream a chat completion as :class:`Chunk`s.

        ``endpoint`` comes from the model catalog. It was carried in the schema
        and read by nothing for three migrations, so every request went to the
        one configured gateway however the catalog was set up (N48).

        ``api_key_file`` is that provider's credential. It travels per call for
        exactly the reason the endpoint does: this adapter is a singleton shared
        by every model, so a key held on it is a key sent to every provider. The
        endpoint was fixed and the credential was not, which meant declaring a
        second provider sent the first one's bearer token to it.
        """

        import httpx

        if not endpoint:
            # Unreachable through the catalog, which resolves a model's
            # provider before returning a target. Named rather than left to
            # fail as a request to "/chat/completions" against no host.
            raise GatewayError("no endpoint for this request")

        url = endpoint.rstrip("/") + "/chat/completions"
        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        key = self._key_for(api_key_file)
        if key:
            headers["authorization"] = f"Bearer {key}"

        body = {**request, "stream": True, "stream_options": {"include_usage": True}}

        options: dict[str, Any] = {"timeout": self.timeout_s}
        if self.transport is not None:
            options["transport"] = self.transport

        async with httpx.AsyncClient(**options) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", errors="replace")
                    if "failed to load" in detail and response.status_code == 500:
                        # The runtime host's router, saying a model on this
                        # machine did not come up — on a single card usually
                        # because another one holds the memory. Its own words
                        # name neither cause nor remedy.
                        raise GatewayError(
                            "the model on this machine could not be loaded; the GPU may "
                            "not have room for it. The model panel shows what is loaded "
                            "and why it failed."
                        )
                    raise GatewayError(
                        f"gateway returned {response.status_code}: {_scrub(detail[:512], key)}"
                    )

                async for line in response.aiter_lines():
                    for chunk in _parse_sse_line(line):
                        yield chunk

    def _key_for(self, api_key_file: str | None) -> str | None:
        """This provider's key, read now rather than at construction.

        Read per call so rotating the file takes effect on the next turn instead
        of needing a restart, and so a missing file is an error on *that* model
        rather than a boot failure for the appliance. The file is small and the
        OS caches it; the cost is not worth a staleness bug.

        None when the provider declares no key, which is the ordinary case for a
        model served on loopback.

        A read failure is logged and not raised. Returning None sends the
        request unauthenticated, which the provider answers with a 401 naming
        itself — a better error than a 500 from Neuman, because it reaches the
        user as a failed turn on a named model rather than a crashed handler.

        The likely failure is permissions, not absence: a secret gets `chmod
        0400` and is left owned by root while the server runs as `neuman`. That
        is worth saying in the log, because the file plainly exists and the
        error otherwise reads as a missing key (N44).
        """

        if api_key_file is None:
            return None

        try:
            return Path(api_key_file).read_text(encoding="utf-8").strip() or None
        except PermissionError as exc:
            _log.error(
                "cannot read provider key: the server runs as `neuman`, so the "
                "key file must be readable by it — `chown neuman:neuman` on a "
                "0400 file",
                fields={"path": api_key_file, "error": exc.strerror or str(exc)},
            )
            return None
        except OSError as exc:
            _log.error(
                "cannot read provider key",
                fields={"path": api_key_file, "error": exc.strerror or str(exc)},
            )
            return None


def _scrub(text: str, key: str | None) -> str:
    """Remove the key from text about to be logged or raised.

    Providers do not normally echo credentials, but some echo request headers in
    debug responses, and this string reaches the event log and the client. A
    secret that leaks through an error message leaks just as thoroughly as one
    in a config file.
    """

    if not key or key not in text:
        return text
    return text.replace(key, "[redacted]")


def _parse_sse_line(line: str) -> list[Chunk]:
    """Turn one server-sent-events line into zero or more chunks."""

    if not line or not line.startswith("data:"):
        return []

    data = line[len("data:") :].strip()
    if not data or data == _DONE:
        return []

    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        _log.warning("gateway sent a malformed SSE payload")
        return []

    chunks: list[Chunk] = []

    # Usage arrives on its own event when `include_usage` is set, and on the
    # final event for some providers. Both are handled.
    usage = event.get("usage")
    if usage:
        chunks.append(Chunk("usage", dict(usage)))

    for choice in event.get("choices") or []:
        delta = choice.get("delta") or {}

        if delta.get("content"):
            chunks.append(Chunk("content", {"text": delta["content"]}))

        # Reasoning models stream thinking under a provider-specific key.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            chunks.append(Chunk("reasoning", {"text": reasoning}))

        for call in delta.get("tool_calls") or []:
            chunks.append(
                Chunk(
                    "tool_call",
                    {
                        "index": call.get("index", 0),
                        "id": call.get("id"),
                        "function": call.get("function") or {},
                    },
                )
            )

        if choice.get("finish_reason"):
            chunks.append(Chunk("finish", {"reason": choice["finish_reason"]}))

    return chunks
