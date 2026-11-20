"""The model gateway: the wire format, and a real HTTP server.

This adapter had no test of any kind. `ModelClient` was named nowhere under
`tests/` or `nix/tests/`, and everything touching the model path used
`FakeGateway` — a second implementation that agrees with the harness by
construction. That is structurally N35: the only exercise was a fake, so the
76 lines of SSE parsing had never met a byte from a server.

Two layers here, deliberately:

* `_parse_sse_line` is pure, so it is tested directly against payloads real
  providers send — including the shapes that are easy to get wrong (usage on
  its own event, arguments arriving as fragments, `[DONE]`).
* `ModelClient.stream` needs a socket. It runs against a real `httpx`
  transport serving real bytes, because framing bugs live between `aiter_lines`
  and the parser and nothing that stubs either side can see them.

The assertions are written against what the *harness* needs (`Chunk` kinds, and
`loop.py`'s accumulation of tool-call fragments by index), not against what the
parser happens to produce.
"""

from __future__ import annotations

import json

import pytest

from neuman_server.adapters.gateway import GatewayError, ModelClient, _parse_sse_line


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


def kinds(chunks) -> list[str]:
    return [c.kind for c in chunks]


# -- the pure parser --------------------------------------------------------


def test_a_content_delta_becomes_a_content_chunk():
    chunks = _parse_sse_line(sse({"choices": [{"delta": {"content": "hello"}}]}))
    assert kinds(chunks) == ["content"]
    assert chunks[0].data == {"text": "hello"}


def test_non_data_lines_are_ignored():
    """SSE carries comments and event/id fields the parser must skip."""

    for line in ("", ": keep-alive", "event: message", "id: 42", "retry: 1000"):
        assert _parse_sse_line(line) == [], line


def test_the_done_sentinel_yields_nothing():
    assert _parse_sse_line("data: [DONE]") == []


def test_a_malformed_payload_is_dropped_rather_than_raising():
    """One bad event must not end a turn.

    The stream is remote input; a provider that sends a truncated line should
    cost the response that line, not the conversation.
    """

    assert _parse_sse_line("data: {not json") == []


def test_an_empty_content_delta_yields_nothing():
    """Providers send `{"content": ""}` as a keep-alive on some endpoints.

    Emitting a chunk for it would put empty strings in the event log and the
    transcript.
    """

    assert _parse_sse_line(sse({"choices": [{"delta": {"content": ""}}]})) == []


def test_a_role_only_delta_yields_nothing():
    """The first event of an OpenAI stream is `{"role": "assistant"}`."""

    assert _parse_sse_line(sse({"choices": [{"delta": {"role": "assistant"}}]})) == []


def test_usage_arrives_as_its_own_chunk():
    usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    chunks = _parse_sse_line(sse({"usage": usage, "choices": []}))
    assert kinds(chunks) == ["usage"]
    assert chunks[0].data == usage


def test_reasoning_is_recognised_under_both_provider_keys():
    """Reasoning models disagree about the key; both are in the wild."""

    for key in ("reasoning_content", "reasoning"):
        chunks = _parse_sse_line(sse({"choices": [{"delta": {key: "thinking"}}]}))
        assert kinds(chunks) == ["reasoning"], key
        assert chunks[0].data == {"text": "thinking"}


def test_a_finish_reason_becomes_a_finish_chunk():
    chunks = _parse_sse_line(sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    assert kinds(chunks) == ["finish"]
    assert chunks[0].data == {"reason": "stop"}


def test_content_and_finish_on_one_event_both_arrive_in_order():
    """Some providers put the last token and the finish reason together.

    Order matters: a finish before its own content would truncate the message.
    """

    chunks = _parse_sse_line(
        sse({"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]})
    )
    assert kinds(chunks) == ["content", "finish"]


def test_a_tool_call_keeps_its_index_and_fragments():
    """`loop.py` accumulates by index and concatenates arguments verbatim.

    So the adapter must not reorder, merge, or reformat them — the JSON is only
    valid once every fragment has arrived.
    """

    first = _parse_sse_line(
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "fs_read", "arguments": '{"pa'},
                                }
                            ]
                        }
                    }
                ]
            }
        )
    )
    second = _parse_sse_line(
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": 'th": "/a"}'}}
                            ]
                        }
                    }
                ]
            }
        )
    )

    assert kinds(first) == ["tool_call"]
    assert first[0].data["index"] == 0
    assert first[0].data["id"] == "call_1"
    assert first[0].data["function"]["arguments"] == '{"pa'

    # The second fragment carries no id and no name: only the index ties it to
    # the first, which is why the index must survive.
    assert second[0].data["index"] == 0
    assert second[0].data["id"] is None
    assert second[0].data["function"]["arguments"] == 'th": "/a"}'

    # Reassembled the way the harness does it.
    joined = first[0].data["function"]["arguments"] + second[0].data["function"]["arguments"]
    assert json.loads(joined) == {"path": "/a"}


def test_parallel_tool_calls_keep_distinct_indices():
    """Two calls in one event. Losing the index would merge their arguments."""

    chunks = _parse_sse_line(
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": "a"}},
                                {"index": 1, "function": {"arguments": "b"}},
                            ]
                        }
                    }
                ]
            }
        )
    )
    assert [c.data["index"] for c in chunks] == [0, 1]


def test_a_tool_call_without_an_index_defaults_to_zero():
    """Anthropic-compatible shims omit it on a single call."""

    chunks = _parse_sse_line(
        sse({"choices": [{"delta": {"tool_calls": [{"function": {"name": "x"}}]}}]})
    )
    assert chunks[0].data["index"] == 0


def test_an_event_with_no_choices_key_is_survivable():
    """Keep-alive and metadata events in the wild have neither key."""

    assert _parse_sse_line(sse({"id": "chatcmpl-1", "model": "m"})) == []


# -- against a real HTTP server ---------------------------------------------
#
# Everything above tests a pure function. What N35 taught is that the gap
# between two layers is where the bug hides, so these drive the real
# `ModelClient` over a real transport: request construction, status handling,
# and the framing between `aiter_lines` and the parser.


class Recorder:
    """An ASGI app that serves a fixed SSE body and records the request."""

    def __init__(self, body: bytes, status: int = 200, chunk_size: int | None = None):
        self.body = body
        self.status = status
        # Splitting the body at arbitrary byte boundaries is the point: an SSE
        # event does not arrive in one read, and a parser that assumed it did
        # would pass every test above.
        self.chunk_size = chunk_size
        self.request: dict = {}
        self.headers: dict = {}

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "http"
        self.headers = {k.decode(): v.decode() for k, v in scope["headers"]}

        payload = b""
        while True:
            message = await receive()
            payload += message.get("body", b"")
            if not message.get("more_body"):
                break
        if payload:
            self.request = json.loads(payload)

        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )

        if self.chunk_size is None:
            await send({"type": "http.response.body", "body": self.body})
            return

        for start in range(0, len(self.body), self.chunk_size):
            piece = self.body[start : start + self.chunk_size]
            more = start + self.chunk_size < len(self.body)
            await send({"type": "http.response.body", "body": piece, "more_body": more})


async def collect(app, **kwargs):
    """Drive the real ModelClient against `app`, returning its chunks.

    Injects a transport rather than monkeypatching `httpx.AsyncClient`. Only
    the socket is faked: URL construction, headers, `stream=True`, status
    handling and line iteration are all the production path.
    """

    import httpx

    client = ModelClient(transport=httpx.ASGITransport(app=app), **kwargs)
    return [
        chunk
        async for chunk in client.stream(
            {"model": "m", "messages": []}, endpoint="http://model.test/v1"
        )
    ]


async def test_a_real_stream_is_parsed_end_to_end():
    body = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7},"choices":[]}\n\n'
        b"data: [DONE]\n\n"
    )
    chunks = await collect(Recorder(body))

    assert kinds(chunks) == ["content", "content", "finish", "usage"]
    assert "".join(c.data["text"] for c in chunks if c.kind == "content") == "Hello"
    assert chunks[-1].data["total_tokens"] == 7


async def test_an_event_split_across_reads_still_parses():
    """The assertion the unit tests cannot make.

    `aiter_lines` has to reassemble events that arrive in pieces. Serving the
    same body eight bytes at a time puts every split point — mid-JSON,
    mid-`data:`, between `\\n` and `\\n` — into the test.
    """

    body = (
        b'data: {"choices":[{"delta":{"content":"one"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"two"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    chunks = await collect(Recorder(body, chunk_size=8))

    assert [c.data["text"] for c in chunks] == ["one", "two"]


async def test_the_request_carries_streaming_and_usage_options():
    """Without `include_usage` the usage event never arrives, and budget
    accounting silently records nothing (D20 puts accounting in our tables)."""

    app = Recorder(b"data: [DONE]\n\n")
    await collect(app)

    assert app.request["stream"] is True
    assert app.request["stream_options"] == {"include_usage": True}
    assert app.request["model"] == "m"


async def test_the_api_key_is_sent_as_a_bearer_token(tmp_path):
    key = tmp_path / "provider.key"
    key.write_text("secret-key", encoding="utf-8")

    app = Recorder(b"data: [DONE]\n\n")
    await collect_with(app, api_key_file=str(key))
    assert app.headers["authorization"] == "Bearer secret-key"


async def test_no_authorization_header_without_a_key():
    """A local endpoint that wants none must not receive `Bearer None`."""

    app = Recorder(b"data: [DONE]\n\n")
    await collect(app)
    assert "authorization" not in app.headers


async def test_an_error_status_raises_with_the_body_in_the_message():
    """A 429 or a 400 from the provider has to say what it said.

    The detail is what tells an operator whether the key is wrong, the model
    name is unknown, or the budget is exhausted upstream.
    """

    app = Recorder(b'{"error":{"message":"model not found"}}', status=404)

    with pytest.raises(GatewayError) as caught:
        await collect(app)

    assert "404" in str(caught.value)
    assert "model not found" in str(caught.value)


async def test_a_stream_that_ends_without_done_is_not_an_error():
    """A provider that closes cleanly after the last event is common.

    Raising here would turn a complete response into a failed turn.
    """

    body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    chunks = await collect(Recorder(body))
    assert [c.data["text"] for c in chunks] == ["hi"]


async def test_an_empty_stream_yields_nothing_and_does_not_raise():
    assert await collect(Recorder(b"")) == []


async def test_keepalive_comments_between_events_are_skipped():
    """Providers send `: ping` to hold the connection open."""

    body = (
        b': ping\n\ndata: {"choices":[{"delta":{"content":"x"}}]}\n\n: ping\n\ndata: [DONE]\n\n'
    )
    chunks = await collect(Recorder(body))
    assert [c.data["text"] for c in chunks] == ["x"]


async def test_a_tool_call_streams_through_the_real_transport():
    """The whole point, end to end: fragments out of a socket reassemble.

    This is the shape a dispatch actually takes — a name, then arguments in
    pieces, then a finish — and it is the path that had no test at all.
    """

    body = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        b'"function":{"name":"fs_read","arguments":""}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"{\\"path\\":"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"\\"/srv/a\\"}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    chunks = await collect(Recorder(body, chunk_size=13))

    assert kinds(chunks) == ["tool_call", "tool_call", "tool_call", "finish"]

    # Reassembled exactly as `loop.py` does it.
    arguments = "".join(
        c.data["function"].get("arguments", "") for c in chunks if c.kind == "tool_call"
    )
    assert json.loads(arguments) == {"path": "/srv/a"}
    assert chunks[0].data["function"]["name"] == "fs_read"
    assert chunks[-1].data["reason"] == "tool_calls"


# -- the key reaches the client ---------------------------------------------
#
# N38 is the failure these guard. `build_services` passed the *environment*
# variable while the module only ever set the file path, because a secret in a
# unit's environment is readable through /proc — so the key was None on every
# real appliance, every request went out unauthenticated, and the function
# written to read the file had no callers at all.
#
# The settings those tests exercised are gone: there is no appliance-wide key
# to configure, because one held centrally is one sent to every provider. What
# survives is the question they were asking — does the key an operator wrote
# down reach the wire — asked of the path that exists now.


def test_the_client_holds_no_key_of_its_own():
    """The structural half of the fix.

    A field here is a field sent to every provider, so the absence is the
    guarantee: there is nothing for a second provider to inherit.
    """

    import dataclasses

    fields = {f.name for f in dataclasses.fields(ModelClient)}

    assert "api_key" not in fields, fields
    assert "base_url" not in fields, fields


async def test_a_request_without_an_endpoint_is_refused_by_name():
    """There is no configured default to fall back to.

    Unreachable through the catalog, which resolves a provider before returning
    a target — but a silent fallback is what sent every compaction to the wrong
    provider (N94), so the absence is named rather than papered over.
    """

    import httpx

    client = ModelClient(transport=httpx.ASGITransport(app=Recorder(b"data: [DONE]\n\n")))

    with pytest.raises(GatewayError) as excinfo:
        async for _ in client.stream({"model": "m", "messages": []}):
            pass

    assert "endpoint" in str(excinfo.value)


async def test_an_unreadable_key_file_says_which_permission_is_wrong(tmp_path, caplog):
    """The likely misconfiguration, and it does not look like one.

    A secret gets `chmod 0400` and is left owned by root while the server runs
    as `neuman`. The file plainly exists, so the failure reads as a missing key
    rather than an unreadable one — which cost five systemd restarts and a
    traceback before the message said so (N44).
    """

    import os

    key = tmp_path / "root-owned.key"
    key.write_text("sk-unreachable")
    os.chmod(key, 0o000)

    app = Recorder(b"data: [DONE]\n\n")
    try:
        with caplog.at_level("ERROR"):
            await collect_with(app, api_key_file=str(key))
    finally:
        os.chmod(key, 0o600)

    assert "authorization" not in app.headers
    assert "neuman" in caplog.text, caplog.text


# -- per-provider credentials -----------------------------------------------
#
# The bug these exist for: `ModelClient` is a singleton shared by every model,
# and it held one key read once at startup. `endpoint` was already per call, so
# a second provider got the right host and the *first* provider's bearer token
# — a live credential disclosed to an unintended party, and a 401 mid-turn on a
# model the admin surface listed as healthy.
#
# Asserting the header rather than the outcome is the point. The fake gateway
# answers any request, so a test that checked only "the turn completed" could
# not see a missing or wrong credential — which is how N86 and N94 survived.


async def collect_with(app, **kwargs):
    """Like `collect`, but passing per-call arguments to `stream`."""

    import httpx

    client = ModelClient(transport=httpx.ASGITransport(app=app))
    kwargs.setdefault("endpoint", "http://model.test/v1")
    return [chunk async for chunk in client.stream({"model": "m", "messages": []}, **kwargs)]


async def test_a_per_call_key_file_is_read_and_sent(tmp_path):
    key = tmp_path / "deepseek.key"
    key.write_text("sk-deepseek\n", encoding="utf-8")

    app = Recorder(b"data: [DONE]\n\n")
    await collect_with(app, api_key_file=str(key))

    # Stripped: a key file written by a text editor or a secret manager ends
    # with a newline, and `Bearer sk-deepseek\n` is a malformed header.
    assert app.headers["authorization"] == "Bearer sk-deepseek"


async def test_a_provider_without_a_key_sends_no_header(tmp_path):
    """A model on loopback needs no credential and must not inherit one.

    `Bearer None` is the shape this prevents, and an appliance serving only
    local models is the ordinary case for it.
    """

    app = Recorder(b"data: [DONE]\n\n")
    await collect_with(app)

    assert "authorization" not in app.headers


async def test_two_providers_on_one_client_get_their_own_keys(tmp_path):
    """And the same client, used twice, does not leak one into the other.

    The singleton is the hazard: a key cached on the instance by the first call
    would be sent by the second, which is the original bug wearing a cache.
    """

    import httpx

    a = tmp_path / "a.key"
    a.write_text("sk-a", encoding="utf-8")
    b = tmp_path / "b.key"
    b.write_text("sk-b", encoding="utf-8")

    seen = []
    for path in (a, b, a):
        app = Recorder(b"data: [DONE]\n\n")
        client = ModelClient(transport=httpx.ASGITransport(app=app))
        async for _ in client.stream(
            {"model": "m", "messages": []},
            endpoint="http://model.test/v1",
            api_key_file=str(path),
        ):
            pass
        seen.append(app.headers["authorization"])

    assert seen == ["Bearer sk-a", "Bearer sk-b", "Bearer sk-a"]


async def test_a_rotated_key_takes_effect_without_reconstruction(tmp_path):
    """Read at call time, not at construction.

    The key used to be read once into the client at startup, so rotating the
    file needed a server restart — and a restart disconnects whoever is
    mid-turn.
    """

    import httpx

    key = tmp_path / "rotating.key"
    key.write_text("sk-old", encoding="utf-8")

    app = Recorder(b"data: [DONE]\n\n")
    client = ModelClient(transport=httpx.ASGITransport(app=app))

    async for _ in client.stream(
        {"model": "m", "messages": []},
        endpoint="http://model.test/v1",
        api_key_file=str(key),
    ):
        pass
    assert app.headers["authorization"] == "Bearer sk-old"

    key.write_text("sk-new", encoding="utf-8")

    app2 = Recorder(b"data: [DONE]\n\n")
    client2 = ModelClient(transport=httpx.ASGITransport(app=app2))
    async for _ in client2.stream(
        {"model": "m", "messages": []},
        endpoint="http://model.test/v1",
        api_key_file=str(key),
    ):
        pass
    assert app2.headers["authorization"] == "Bearer sk-new"


async def test_an_unreadable_key_file_sends_no_header_rather_than_crashing(tmp_path):
    """A missing key is the provider's 401, not Neuman's 500.

    The provider's refusal names the provider and reaches the user as a failed
    turn; an exception here would be a crashed handler, which says nothing about
    which of several providers was misconfigured.
    """

    app = Recorder(b"data: [DONE]\n\n")
    await collect_with(app, api_key_file=str(tmp_path / "does-not-exist.key"))

    assert "authorization" not in app.headers


async def test_an_error_body_echoing_the_key_is_redacted(tmp_path):
    """Some providers echo request headers in debug responses.

    This string reaches the event log and the client, so a key that arrives in
    it leaks as thoroughly as one written to a config file.
    """

    key = tmp_path / "echoed.key"
    key.write_text("sk-echoed-secret", encoding="utf-8")

    app = Recorder(b'{"error":"bad auth: Bearer sk-echoed-secret"}', status=401)

    with pytest.raises(GatewayError) as excinfo:
        await collect_with(app, api_key_file=str(key))

    assert "sk-echoed-secret" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)
