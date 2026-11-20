"""The event log, projection, and context management."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from fakes import FakeEvents, FakeGateway, FakeImages

from neuman_server.decision.harness import PROJECTION_KINDS, events, project, resolve_images
from neuman_server.decision.harness.context import (
    ContextConfig,
    ContextManager,
    estimate_tokens,
)
from neuman_server.decision.harness.ports import Chunk

# -- redaction --------------------------------------------------------------


def test_an_exact_leaf_match_is_redacted():
    value, pointers = events.redact(
        {"headers": {"authorization": "sk-secret-value"}}, {"api_key": "sk-secret-value"}
    )
    assert value["headers"]["authorization"] == "<redacted:api_key>"
    assert pointers == [{"path": "/headers/authorization", "reason": "secret:api_key"}]


def test_a_substring_is_not_redacted():
    """Substring matching over-redacts and gives false confidence."""

    value, pointers = events.redact(
        {"text": "the key is sk-secret-value, apparently"}, {"api_key": "sk-secret-value"}
    )
    assert value["text"].startswith("the key is sk-")
    assert pointers == []


def test_short_secrets_are_ignored():
    value, _ = events.redact({"text": "abc"}, {"tiny": "abc"})
    assert value["text"] == "abc"


def test_redaction_walks_lists():
    value, pointers = events.redact(
        {"items": ["safe", "sk-secret-value"]}, {"api_key": "sk-secret-value"}
    )
    assert value["items"] == ["safe", "<redacted:api_key>"]
    assert pointers[0]["path"] == "/items/1"


# -- NUL sanitisation (N105) ------------------------------------------------


def test_a_nul_is_replaced_and_reported():
    """`jsonb` is text underneath, so Postgres refuses `\\u0000` outright.

    Before this, the refusal escaped the turn and the `tool_result` being
    written was never stored — a tool that provably ran with no recorded
    outcome.
    """

    value, pointers = events.sanitize({"content": "--- stdout ---\n\x89PNG\x00\x00IHDR"})

    assert "\x00" not in value["content"]
    assert value["content"].count(events.NUL_PLACEHOLDER) == 2
    assert pointers == [{"path": "/content", "reason": "sanitized:nul:2"}]


def test_a_payload_without_nuls_is_untouched():
    """Verbatim is still the rule for everything this does not have to change."""

    payload = {"content": "ordinary output\n\twith tabs and � already in it"}
    value, pointers = events.sanitize(payload)

    assert value == payload
    assert pointers == []


def test_sanitisation_walks_lists_and_nesting():
    value, pointers = events.sanitize(
        {"calls": [{"out": "a\x00b"}, {"out": "clean"}]},
    )

    assert value["calls"][0]["out"] == f"a{events.NUL_PLACEHOLDER}b"
    assert value["calls"][1]["out"] == "clean"
    assert [pointer["path"] for pointer in pointers] == ["/calls/0/out"]


async def test_a_tool_result_carrying_binary_is_stored_rather_than_refused():
    """The N105 failure end to end: the append must succeed and say what it changed."""

    repository = FakeEvents()
    record = await events.append(
        repository,
        branch_id="s1",
        kind="tool_result",
        payload={"content": "render to /dev/stdout\n\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"},
    )

    assert "\x00" not in record.payload["content"]
    assert repository.redactions_for("tool_result") == [
        {"path": "/content", "reason": "sanitized:nul:3"}
    ]


async def test_a_secret_is_redacted_before_a_nul_is_substituted():
    """Order matters: redaction is exact-equality, and a substitution breaks it.

    Sanitising first would leave a credential that no longer equals the secret
    it came from, so it would reach the log unredacted.
    """

    repository = FakeEvents()
    record = await events.append(
        repository,
        branch_id="s1",
        kind="request",
        payload={"key": "sk-secret\x00value"},
        secrets={"api_key": "sk-secret\x00value"},
    )

    assert record.payload["key"] == "<redacted:api_key>"
    recorded = repository.redactions_for("request") or []
    assert [pointer["reason"] for pointer in recorded] == ["secret:api_key"]


# -- the log ----------------------------------------------------------------


async def test_an_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown session event kind"):
        await events.append(FakeEvents(), branch_id="s1", kind="gossip", payload={})


async def test_sequence_numbers_increase():
    repository = FakeEvents()
    for _ in range(3):
        await events.append(repository, branch_id="s1", kind="user_message", payload={})

    assert [event.seq for event in await repository.read("s1")] == [1, 2, 3]


# -- projection -------------------------------------------------------------


async def _log(repository, kind, payload):
    return await events.append(repository, branch_id="s1", kind=kind, payload=payload)


async def test_projection_builds_chat_messages():
    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "hello"})
    await _log(repository, "response_final", {"message": {"content": "hi"}})

    assert project(await repository.read("s1")) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


async def test_a_projection_read_skips_what_the_projection_ignores():
    """Opening a session must not fetch the token stream or the request snapshots.

    `response_chunk` is one row per streamed delta and `request` is the whole
    history once per step, so a long session's log is mostly rows `project`
    discards. A projection read asks the store for the kinds it can use; the full
    log is still there for the audit and admin views.
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "hello"})
    await _log(repository, "request", {"messages": [{"role": "user", "content": "hello"}]})
    await _log(repository, "response_chunk", {"kind": "content", "data": {"text": "hi"}})
    await _log(repository, "response_final", {"message": {"content": "hi"}})
    await _log(
        repository,
        "tool_result",
        {"tool_call_id": "c1", "name": "fs_list", "content": "a", "exit_code": 0},
    )

    full = list(await repository.read("s1"))
    filtered = list(await repository.read("s1", kinds=PROJECTION_KINDS))

    assert [event.kind for event in filtered] == [
        "user_message",
        "response_final",
        "tool_result",
    ]
    # The filter is invisible to the reader: dropping what the projection cannot
    # read leaves the projected messages exactly as they were.
    assert project(filtered, for_display=True) == project(full, for_display=True)


async def test_governance_events_are_invisible_to_the_model():
    """tool_proposal and tool_decision are records, not conversation."""

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "hi"})
    await _log(repository, "tool_proposal", {"tool_call_id": "c1", "name": "fs_read"})
    await _log(repository, "tool_decision", {"tool_call_id": "c1", "accepted": True})
    await _log(repository, "response_chunk", {"kind": "content", "data": {"text": "partial"}})

    assert len(project(await repository.read("s1"))) == 1


async def test_compaction_replaces_history_with_its_recorded_list():
    """The replacement is carried by the event, so the projection is exact."""

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "one"})
    await _log(repository, "user_message", {"content": "two"})
    await _log(
        repository,
        "harness_event",
        {"type": "compaction", "messages": [{"role": "system", "content": "summary"}]},
    )
    await _log(repository, "user_message", {"content": "three"})

    assert project(await repository.read("s1")) == [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "three"},
    ]


async def test_the_originals_survive_compaction():
    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "one"})
    await _log(repository, "harness_event", {"type": "compaction", "messages": []})

    stored = [event.payload.get("content") for event in await repository.read("s1")]
    assert "one" in stored


async def test_a_display_read_marks_which_user_rows_open_a_turn():
    """A steering row is a user message inside a turn, not a new one.

    The client offers an edit only on a turn-opening prompt. It cannot tell from
    the previous role — a steering row follows a reply as often as an opening
    does — so the display projection states it from the `turn_complete` rows it
    has read. The model path has no business carrying a reader's field.
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "go"})
    await _log(repository, "response_final", {"message": {"content": "working"}})
    await _log(repository, "user_message", {"content": "steer"})
    await _log(repository, "response_final", {"message": {"content": "done"}})
    await _log(repository, "harness_event", {"type": "turn_complete"})
    await _log(repository, "user_message", {"content": "next"})

    display = project(await repository.read("s1"), for_display=True)
    assert [message["opens_turn"] for message in display if message["role"] == "user"] == [
        True,
        False,
        True,
    ]

    model = project(await repository.read("s1"))
    assert all("opens_turn" not in message for message in model)


async def test_a_display_read_keeps_the_messages_compaction_folded():
    """The reader gets the conversation; the model gets the summary.

    Compaction replaces the model's history with `[summary, *tail]`, and the
    display projection used to apply the same replacement — so reopening a
    compacted session showed only the tail, with the user's own prompts gone and
    the `system` summary never drawn. The originals are all still in the log; a
    display read keeps them and marks where the model's view was cut (N129).
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "one"})
    await _log(repository, "user_message", {"content": "two"})
    await _log(
        repository,
        "harness_event",
        {
            "type": "compaction",
            "summarized_messages": 2,
            "messages": [{"role": "system", "content": "summary"}],
        },
    )
    await _log(repository, "user_message", {"content": "three"})

    display = project(await repository.read("s1"), for_display=True)
    assert [message.get("content") for message in display] == [
        "one",
        "two",
        "…context filled; summarized the earlier 2 messages and continuing",
        "three",
    ]
    assert display[2]["role"] == "note"
    # The display row keeps the event's position so the client can put a fork
    # node at the boundary above it rather than below it.
    assert display[2]["seq"] == 3

    # The model path is unchanged: it is handed the replacement list.
    assert project(await repository.read("s1")) == [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "three"},
    ]


async def test_tool_results_project_with_their_call_id():
    repository = FakeEvents()
    await _log(
        repository,
        "response_final",
        {
            "message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "fs_read"}}],
            }
        },
    )
    await _log(repository, "tool_result", {"tool_call_id": "c1", "content": "data"})

    history = project(await repository.read("s1"))
    assert history[-1] == {"role": "tool", "tool_call_id": "c1", "content": "data"}


async def test_the_model_never_sees_a_results_structured_half():
    """The default projection builds the model's context and nothing more.

    `structured` carries a write's diff. The model asked for that change and
    does not need it read back — and anything added here lands in the provider
    payload (D20), so the display fields have to be opt-in rather than filtered
    out later.
    """

    repository = FakeEvents()
    await _log(
        repository,
        "response_final",
        {
            "message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "fs_edit"}}],
            }
        },
    )
    await _log(
        repository,
        "tool_result",
        {
            "tool_call_id": "c1",
            "name": "fs_edit",
            "content": "wrote it",
            "exit_code": 0,
            "structured": {"path": "/srv/a", "diff": "-x\n+y"},
        },
    )

    history = project(await repository.read("s1"))
    assert history[-1] == {"role": "tool", "tool_call_id": "c1", "content": "wrote it"}


async def test_the_display_projection_carries_what_a_reader_needs():
    """Reopening a session must render the way it did live.

    The browser builds its cards from these fields: the name picks the renderer,
    the exit code picks success from failure, and `structured` is the diff. When
    the client rebuilt history from the model's view instead, every improvement
    the live path had — the numbered file, the diff, the stripped envelope —
    silently reverted on reload.
    """

    repository = FakeEvents()
    await _log(
        repository,
        "response_final",
        {
            "message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "fs_edit"}}],
            }
        },
    )
    await _log(
        repository,
        "tool_result",
        {
            "tool_call_id": "c1",
            "name": "fs_edit",
            "content": "wrote it",
            "exit_code": 0,
            "structured": {"path": "/srv/a", "diff": "-x\n+y"},
        },
    )

    history = project(await repository.read("s1"), for_display=True)
    assert history[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "wrote it",
        "name": "fs_edit",
        "exit_code": 0,
        "structured": {"path": "/srv/a", "diff": "-x\n+y"},
    }


async def test_a_result_without_structured_output_omits_the_field():
    """Absent rather than null, so a client can test for it plainly."""

    repository = FakeEvents()
    await _log(
        repository,
        "response_final",
        {
            "message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "fs_read"}}],
            }
        },
    )
    await _log(
        repository,
        "tool_result",
        {
            "tool_call_id": "c1",
            "name": "fs_read",
            "content": "data",
            "exit_code": 0,
            "structured": None,
        },
    )

    history = project(await repository.read("s1"), for_display=True)
    assert "structured" not in history[-1]
    assert history[-1]["name"] == "fs_read"


async def test_a_display_read_marks_an_image_without_carrying_it():
    """The reader gets a flag; the model gets the bytes.

    A screenshot is megabytes of base64. The model's history has to carry it —
    OpenAI accepts an image only on a `user` message, so the projection invents
    one — but a reader wants the picture on the call that produced it, fetched
    from the session's image route. The display projection therefore marks the
    tool message and emits no synthetic user turn, which also keeps megabytes of
    base64 out of every reopen of the session.
    """

    repository = FakeEvents()
    images = FakeImages()
    await _log_image(repository, 1, path="/srv/a.png", images=images)

    shown = project(await repository.read("s1"), for_display=True)
    assert shown == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "fs_image", "arguments": "{}"},
                }
            ],
            "seq": 1,
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Looked at the image (image/png). It follows.",
            "name": "fs_image",
            "exit_code": 0,
            "has_image": True,
        },
    ], shown

    # The model path carries a *reference* — a hash and a path — not the bytes:
    # the picture lives in the content-addressed store and is resolved only when
    # a request is built. That is what keeps a stored history, including a
    # compaction replacement list, from copying the base64.
    stored = await repository.read("s1")
    model = project(stored)
    assert [m["role"] for m in model] == ["assistant", "tool", "user"], model
    ref = model[2]["content"][1]
    assert ref["type"] == "image_ref"
    assert ref["sha256"] == _image_sha()
    assert "data:image" not in json.dumps(model)
    # And resolution puts the picture back on the `user` turn, the only place a
    # provider accepts an `image_url`.
    resolved = await resolve_images(model, images)
    assert resolved[2]["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJD"


# Encodes to "QUJD", the base64 the tests assert a resolved URI carries.
IMAGE_BYTES = b"ABC"


def _image_sha() -> str:
    return hashlib.sha256(IMAGE_BYTES).hexdigest()


async def _log_image(repository, index: int, *, path: str | None = None, images=None) -> None:
    """Log an `fs_image` call and its result.

    The assistant message is not decoration: a `tool` result must answer a call
    the history still holds, and `project` repairs a history that does not
    (N179). These tests are about the image's shape, so the call is the
    smallest one that makes the result valid.
    """

    await _log(
        repository,
        "response_final",
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{index}",
                        "type": "function",
                        "function": {"name": "fs_image", "arguments": "{}"},
                    }
                ],
            }
        },
    )
    if images is not None:
        await images.put(session_id="s1", media_type="image/png", data=IMAGE_BYTES)
    await _log(
        repository,
        "tool_result",
        {
            "tool_call_id": f"c{index}",
            "name": "fs_image",
            "content": "Looked at the image (image/png). It follows.",
            "exit_code": 0,
            "image": {"sha256": _image_sha(), "path": path or ""},
        },
    )


async def test_older_images_are_replaced_by_a_note_that_names_the_file():
    """The newest few images, the rest a pointer.

    An image is the largest thing a request resends on every turn, so an old
    screenshot is paid for again until something removes it. Past the newest
    few the model has stopped looking; the replacement names the file so it can
    ask for the picture again (N148's lifecycle half).
    """

    repository = FakeEvents()
    for index in range(4):
        await _log_image(repository, index, path=f"/shots/shot{index}.png")

    model = project(await repository.read("s1"), keep_images=2)

    user_turns = [m for m in model if m["role"] == "user"]
    assert len(user_turns) == 4, model
    kinds = [[part.get("type") for part in turn["content"]] for turn in user_turns]
    assert kinds == [
        ["text", "text"],
        ["text", "text"],
        ["text", "image_ref"],
        ["text", "image_ref"],
    ], kinds
    # The two older images name the file they came from...
    assert "/shots/shot0.png" in user_turns[0]["content"][1]["text"]
    assert "/shots/shot1.png" in user_turns[1]["content"][1]["text"]
    # ...and the newest two keep a live reference, resolved on demand.
    for turn in user_turns[2:]:
        assert turn["content"][1]["type"] == "image_ref"


async def test_projection_carries_a_reference_not_the_bytes_and_is_stable():
    """The derived history is small, and re-projects identically.

    A reference instead of a data URI is what lets a compaction snapshot be
    stored without copying the picture; determinism is the prompt-cache rule.
    """

    repository = FakeEvents()
    images = FakeImages()
    await _log_image(repository, 0, path="/shots/a.png", images=images)

    stored = await repository.read("s1")
    first = project(stored, keep_images=4)
    second = project(stored, keep_images=4)

    assert first == second
    assert "data:image/" not in json.dumps(first)
    # The bytes are one resolution away, and land where a provider accepts them.
    assert "data:image/png;base64,QUJD" in json.dumps(await resolve_images(first, images))


async def test_an_image_that_arrives_through_a_compaction_still_ages():
    """A compaction replays its replacement list straight into `messages`.

    That path skips the branch that builds image turns, so aging has to be a
    pass over the finished history instead — otherwise an image folded into a
    compaction snapshot is resent in full for the rest of the session.
    """

    repository = FakeEvents()
    await _log(
        repository,
        "harness_event",
        {
            "type": "compaction",
            "summarized_messages": 3,
            "messages": [
                {"role": "system", "content": "Conversation summary:\n…"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "an older image"},
                        {
                            "type": "image_ref",
                            "sha256": "0" * 64,
                            "path": "/shots/old.png",
                        },
                    ],
                },
            ],
        },
    )
    images = FakeImages()
    await _log_image(repository, 1, path="/shots/new.png", images=images)

    stored = await repository.read("s1")
    model = project(stored, keep_images=1)

    # The compacted image is older than the new one, so it becomes a placeholder,
    # and the replacement still names the file it came from.
    compacted = model[1]
    assert [part["type"] for part in compacted["content"]] == ["text", "text"]
    assert "omitted" in compacted["content"][1]["text"]
    assert "/shots/old.png" in compacted["content"][1]["text"]
    # The new image is a live reference, resolved to bytes only on the way out.
    assert model[4]["content"][1]["type"] == "image_ref"
    resolved = await resolve_images(model, images)
    assert resolved[4]["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJD"


async def test_a_compaction_records_a_reference_not_the_image_bytes():
    """The replacement list is stored in the log, so it must not carry the picture.

    The bytes live once, in the tool result. Recording them again in every
    compaction snapshot is the Codex session-growth bug this change exists to
    avoid (N148): one real session hit >100 GiB of duplicated image data.
    """

    repository = FakeEvents()
    # A large head to fold, with the image in the kept tail so it lands in the
    # recorded replacement list — the list the event stores.
    await _log(repository, "user_message", {"content": "the task"})
    await _log(repository, "user_message", {"content": "x" * 20_000})
    await _log(repository, "response_final", {"message": {"content": "working"}})
    await _log_image(repository, 0, path="/shots/a.png")

    # Through the real projection, so this is the shape production actually
    # compacts — an old projection that built data URIs here would store them.
    history = project(await repository.read("s1"), keep_images=4)
    manager = ContextManager(config=ContextConfig(context_budget_tokens=50))
    gateway = FakeGateway([[Chunk("content", {"text": "a summary"})]])

    _, actions = await manager.manage(
        list(history), gateway, budget_tokens=50, keep_tokens=2_000, model="m"
    )

    compact = next(action for action in actions if action.kind == "compact")
    recorded = json.dumps(compact.detail)
    assert "image_ref" in recorded
    assert "data:image/" not in recorded, "the snapshot copied the picture"


async def test_a_shared_image_survives_one_purge_and_an_orphan_does_not():
    """Collection is a reachability walk, not an ownership rule.

    An image two sessions read is rooted by both: purging one drops its
    reference edge (the cascade) and the bytes stay, because the other still
    names them. When the last reference goes, the bytes are collected. This is
    the Nix-store shape — content-addressed and shared, collected from roots —
    and the reason a purge cannot simply delete "its" images.
    """

    images = FakeImages()
    shared = b"the same screenshot"
    only_a = b"a screenshot only alice looked at"
    await images.put(session_id="a", media_type="image/png", data=shared)
    await images.put(session_id="b", media_type="image/png", data=shared)
    await images.put(session_id="a", media_type="image/png", data=only_a)

    shared_sha = hashlib.sha256(shared).hexdigest()
    only_a_sha = hashlib.sha256(only_a).hexdigest()

    # Purging session A cascades its reference edges away.
    images.forget("a")
    assert (await images.sweep()).images == 1
    assert await images.get(only_a_sha) is None
    assert await images.get(shared_sha) is not None, "a live session still uses it"

    # The last reference gone, the bytes go too.
    images.forget("b")
    assert (await images.sweep()).images == 1
    assert await images.get(shared_sha) is None


# -- thinking that has to come back (N115) -----------------------------------


def _budget():
    """A model whose dialect wants its thinking replayed."""

    from neuman_server.decision.harness.ports import ReasoningControl

    return ReasoningControl(control="budget", levels=("low", "high"), default="low")


def _effort():
    """A model whose dialect has no field to replay thinking on."""

    from neuman_server.decision.harness.ports import ReasoningControl

    return ReasoningControl(control="effort", levels=("none", "high"), default="high")


async def test_a_continuation_joins_its_thinking_rather_than_replacing_it():
    """Both halves of one thought, in order.

    The cap fell mid-thought, so the truncated reply and its continuation are
    one assistant message — content *and* reasoning. Assigning the
    continuation's would discard the truncated half, which is exactly the part
    the provider is asking for.
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "hi"})
    await _log(
        repository, "response_final", {"message": {"content": "", "reasoning_content": "first"}}
    )
    await _log(
        repository,
        "response_final",
        {"message": {"content": "done", "reasoning_content": " second"}},
    )

    history = project(await repository.read("s1"), reasoning=_budget())
    assistant = [m for m in history if m["role"] == "assistant"]
    assert len(assistant) == 1, history
    assert assistant[0]["reasoning_content"] == "first second", assistant[0]


async def test_a_truncated_thought_becomes_a_user_turn_on_effort():
    """No assistant-side field means the thought rides a user turn (N139).

    The assistant turn keeps its real content, so the model does not read its own
    half-thought as speech it already made.
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "go"})
    await _log(
        repository,
        "response_final",
        {
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "half a thought"},
        },
    )

    history = project(await repository.read("s1"), reasoning=_effort())
    assert [m["role"] for m in history] == ["user", "assistant", "user"], history
    assert history[1] == {"role": "assistant", "content": ""}, history[1]
    assert "half a thought" in history[2]["content"], history[2]
    assert "reasoning_content" not in history[1], history[1]


async def test_a_display_read_shows_the_thought_without_the_resumption_turn():
    """The reader gets the thinking; only the model needs the user turn.

    A display read is bound for no provider, so the truncated thought stays on
    the assistant turn under the reader-only `reasoning` field and no synthetic
    user bubble is drawn (D20, D46).
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "go"})
    await _log(
        repository,
        "response_final",
        {
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "half a thought"},
        },
    )

    history = project(await repository.read("s1"), for_display=True, reasoning=_effort())
    assert [m["role"] for m in history] == ["user", "assistant"], history
    assert history[1]["reasoning"] == "half a thought", history[1]
    assert history[1]["content"] == "", history[1]


async def test_a_truncated_tool_call_keeps_its_tool_replies_contiguous():
    """A user turn cannot sit between a call and its answer.

    The resumption is held until the run of `tool` replies ends, exactly as an
    image is (N114), so the provider never sees `assistant` -> `user` -> `tool`.
    """

    repository = FakeEvents()
    await _log(repository, "user_message", {"content": "go"})
    await _log(
        repository,
        "response_final",
        {
            "finish_reason": "length",
            "message": {
                "content": "",
                "reasoning_content": "half a thought",
                "tool_calls": [{"id": "c1", "function": {"name": "fs_read"}}],
            },
        },
    )
    await _log(repository, "tool_result", {"tool_call_id": "c1", "content": "data"})
    await _log(repository, "response_final", {"message": {"content": "done"}})

    history = project(await repository.read("s1"), reasoning=_effort())
    assert [m["role"] for m in history] == [
        "user",
        "assistant",
        "tool",
        "user",
        "assistant",
    ], history
    assert history[2]["tool_call_id"] == "c1", history[2]
    assert "half a thought" in history[3]["content"], history[3]


async def test_a_display_read_carries_the_thought_but_no_dialect():
    """The reader's view is bound for no provider, so it speaks no dialect.

    `for_display` exists so the model path cannot pick up a reader's fields by
    accident (D20); the reverse holds too — a session being read back in the UI
    is bound for no provider. The thinking is therefore shown under a *reader*
    field (`reasoning`), never the dialect's `reasoning_content`: a review of a
    past session should read like the live one did, without the display
    projection carrying a field a provider would understand.
    """

    repository = FakeEvents()
    await _log(
        repository,
        "response_final",
        {"message": {"content": "hi", "reasoning_content": "mulled"}},
    )

    history = project(await repository.read("s1"), for_display=True)
    assert "reasoning_content" not in history[0], history[0]
    assert history[0]["reasoning"] == "mulled", history[0]


# -- context ----------------------------------------------------------------


def test_token_estimate_is_conservative():
    assert estimate_tokens([{"role": "user", "content": "x" * 400}]) > 90


async def test_history_within_budget_is_untouched():
    manager = ContextManager(config=ContextConfig(context_budget_tokens=10_000))
    messages = [{"role": "user", "content": "short"}]

    result, actions = await manager.manage(messages, FakeGateway())
    assert result == messages
    assert actions == []


async def test_compaction_records_its_replacement_list():
    # The tail is bounded in tokens rather than messages (N116), so the history
    # has to out-weigh `keep_recent_tokens` for there to be anything to
    # compact — a message count no longer decides it.
    manager = ContextManager(
        config=ContextConfig(context_budget_tokens=1, keep_recent_tokens=20)
    )
    gateway = FakeGateway([[Chunk("content", {"text": "a summary"})]])
    messages = [{"role": "user", "content": "x" * 100} for _ in range(5)]

    result, actions = await manager.manage(messages, gateway)

    compaction = next(action for action in actions if action.kind == "compact")
    assert compaction.detail["messages"] == result
    # The anchor leads, with the task statement pinned verbatim behind it
    # (docs/harness.md).
    assert "a summary" in result[0]["content"], result[:2]
    assert result[1]["role"] == "user", result[:2]


# -- how a row is read ------------------------------------------------------


class _Rows:
    """A connection whose rows are dicts, because the pool's row factory is.

    `foundation/db/pool.py` connects with `row_factory=dict_row`, so every
    cursor row in this tree is a mapping keyed by column name. The stub returns
    that shape and nothing else: a stub returning tuples would be the one shape
    the real adapter never sees, which is how the fault below reached `main`.
    """

    def __init__(self, *results: tuple[tuple[str, ...], list[tuple[object, ...]]]) -> None:
        self._results = list(results)
        self.queries: list[str] = []

    async def execute(self, sql: str, params: object = ()) -> _Rows:
        self.queries.append(sql)
        columns, rows = self._results.pop(0)
        self._built = [dict(zip(columns, values, strict=True)) for values in rows]
        return self

    async def fetchone(self) -> dict[str, object] | None:
        return self._built[0] if self._built else None

    async def fetchall(self) -> list[dict[str, object]]:
        return self._built


async def test_the_event_store_reads_its_rows_by_name():
    """A cursor row is a dict, and unpacking it positionally yields its keys.

    The pool connects with `row_factory=dict_row` (`foundation/db/pool.py`), so
    `total, last_seq = await cursor.fetchone()` does not give two values — it
    gives the two *column names*, and the comparison that follows raises
    `TypeError: '<' not supported between instances of 'int' and 'str'`. That
    was a 500 on every press of `read` in the administration page, with no test
    able to see it: this store is faked everywhere else, and a fake returning
    lists is the one shape the real one never returns (N161).
    """

    from neuman_server.adapters.persistence.harness import EventStore

    conn = _Rows(
        (("n", "newest"), [(8, 8)]),
        (
            ("session_id", "seq", "ts", "kind", "message_id", "payload", "redactions"),
            [("S", 1, datetime.now(UTC), "response_chunk", None, {"text": "a"}, None)],
        ),
    )

    window = await EventStore(conn).window("S", limit=3)  # type: ignore[arg-type]

    assert window.total == 8, "the count was read as a column name, not a value"
    assert window.last_seq == 8
    assert [row.seq for row in window.rows] == [1]


async def test_the_event_summary_counts_by_name():
    """The same rule for the orientation: `GROUP BY` rows are dicts too."""

    from neuman_server.adapters.persistence.harness import EventStore

    conn = _Rows(
        (("kind", "n"), [("response_chunk", 5), ("tool_result", 3)]),
        (("first_ts", "last_ts"), [(datetime.now(UTC), datetime.now(UTC))]),
    )

    summary = await EventStore(conn).summary("S")  # type: ignore[arg-type]

    assert summary.total == 8
    assert summary.counts == {"response_chunk": 5, "tool_result": 3}
    assert summary.first_ts is not None and summary.last_ts is not None
