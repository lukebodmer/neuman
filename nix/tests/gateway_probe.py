"""Drive the real `ModelClient` against a real endpoint, inside the guest.

Deliberately the production adapter and the production configuration path:
`Settings.from_env()` reads the same environment the systemd unit sets,
`build_services` constructs the client, and the stored catalog resolves the
model to its endpoint and its key. A probe that constructed a `ModelClient`
itself with a key it had read would prove that the probe can read a file —
which is exactly the kind of test that let N38 survive, where every half worked
and nothing joined them.

It resolves through `services.model_catalog()` for the same reason. The key and
the endpoint are per provider now, so a probe that passed either one directly
would skip the join this test exists to check — and the join is where a second
provider used to receive the first one's bearer token.

Prints a summary the test script asserts on:

    CONTENT <text>
    REASONING <text>
    TOOL_CALL <index> <name> <arguments>
    FINISH <reason>
    USAGE total_tokens=<n>
    ERROR <message>
"""

from __future__ import annotations

import asyncio
import sys


async def run(name: str) -> int:
    from neuman_server.adapters.gateway import GatewayError
    from neuman_server.adapters.persistence.settings import SettingsStore
    from neuman_server.composition.app import build_services
    from neuman_server.foundation import Settings

    settings = Settings.from_env()

    # Through `build_services` rather than constructing the client here: the
    # bug was in how the key reached the client, so the probe must not be the
    # thing that supplies it.
    services = build_services(settings)
    # `build_services` leaves the baseline empty; the server reads it from the
    # database at startup, and so does this — as `neuman`, over the same socket
    # with the same peer authentication. The catalog the test added through the
    # admin surface is the one resolved here, not a copy of it.
    await services.database.open()
    try:
        async with services.database.connection() as conn:
            services.baseline = await SettingsStore(conn).get("baseline") or {}
    finally:
        await services.database.close()
    gateway = services.gateway

    # And through the catalog rather than naming an endpoint: resolution is
    # what turns a local name into a provider's URL, its model string, and its
    # credential. Supplying any of those here would test the probe.
    target = await services.model_catalog().resolve(name)
    if target is None:
        print(f"ERROR the catalog did not resolve `{name}`")
        return 1
    print(f"ENDPOINT {target.endpoint}")

    request = {
        "model": target.provider_model,
        "messages": [{"role": "user", "content": "hello"}],
    }

    try:
        async for chunk in gateway.stream(
            request, endpoint=target.endpoint, api_key_file=target.api_key_file
        ):
            if chunk.kind == "content":
                print(f"CONTENT {chunk.data['text']}")
            elif chunk.kind == "reasoning":
                print(f"REASONING {chunk.data['text']}")
            elif chunk.kind == "tool_call":
                function = chunk.data.get("function") or {}
                print(
                    f"TOOL_CALL {chunk.data.get('index')} "
                    f"{function.get('name')} {function.get('arguments')}"
                )
            elif chunk.kind == "finish":
                print(f"FINISH {chunk.data['reason']}")
            elif chunk.kind == "usage":
                print(f"USAGE total_tokens={chunk.data.get('total_tokens')}")
    except GatewayError as error:
        print(f"ERROR {error}")
        return 1

    return 0


if __name__ == "__main__":
    # The catalog name to resolve: `stub` for the gateway test's hosted stub,
    # the local model's name for the runtime test.
    raise SystemExit(asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "stub")))
