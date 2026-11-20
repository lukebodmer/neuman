# The harness

The harness is the loop that turns one message into one governed turn: it
builds the model's history, decides what the model may call, gates and runs
every call, and records all of it. It is the part of Neuman that is neither the
policy engine nor the client — it is the thing standing between them,
and this document is about how it works. Most of it is ordinary agent-loop
machinery; the part that decides whether a long task finishes or spirals is
**context management**, so that is where the detail is.

## A turn

Every boundary is logged, and history is re-derived from the log at each step
(`projection.py`), so a revocation or a compaction lands immediately rather than
at the next session.

- **Policy is re-read every step**, never cached for the turn. A group removed
  from a user denies their next tool call, not their next message
  (`docs/security-model.md`).
- **Dispatch is chosen by manifest `kind`**, not by a tool name list: a
  `server-native` tool is intercepted here, a `client` tool is dispatched to the
  acting human's machine and runs as them (`docs/tools.md`, D13).
- **Everything fails closed.** No approver, no verdict, no budget, an unknown
  tool, malformed arguments — each ends as a denial with a logged reason, never
  as an unguarded dispatch. A task's agent step is the one turn with a standing
  approval: its envelope is the consent, a call outside it is denied by the same
  gate, and a `critical` call is refused before the approval wait rather than
  blocking on an inbox nobody is watching (D92, D93). Its `TurnResult` carries
  the turn's cost and tokens, which the task run records per step.
- **The output dialect is per model.** Which sampling field asks for reasoning,
  and what a replayed assistant turn must carry, is a catalog fact, not a
  hardcoded guess (`reasoning.py`, D39).

## The prompt's three tiers

The system prompt is rebuilt every turn from live state (`prompt.py`), and three
sources of prose can steer it. They are ordered by specificity rather than by
authority (D65):

| tier | source | trust | scope |
|---|---|---|---|
| operator | the Prompt section, stored in `settings` | trusted, admin-written | the appliance |
| person | `accounts.instructions`, written in Settings | the account's own words, not wrapped | every session on that account |
| work | `<working_dir>/AGENTS.md` or `CLAUDE.md` | delimited as untrusted | one directory |

The person's text sits between the other two, and its section says so: the
project's conventions win for work in that directory, and the operator's house
style gives way to what the person asked for. None of the three is a boundary —
the operator's authority is policy, the project's file is data from a machine
the appliance does not control, and every dispatch is re-gated whatever any of
them says. After all three come the facts that are *derived* rather than
written: the working directory, the expanded reach, and the tools. Prose goes
first so it can never appear to qualify them.

The prompt is assembled as named sections — `prompt_sections` returns
`(source, lines)` and `system_prompt` is the join over it — so the
administration page can show the whole prompt with each part's origin instead
of a single textarea whose emptiness means nothing. That view is read-only by
construction: the derived sections state facts the gate enforces, and a saved
copy of one could disagree with what a dispatch gets (D81). Only the operator
tier is editable. The first word of the identity section is the appliance's
name, which is a setting of its own rather than a piece of that section (D82).

## History is a projection

There is no growing in-memory message list. The event log is the record; the
model's history is a function of it. Compaction is not an exception to that —
it is the reason the rule exists. A compaction appends a `harness_event` of type
`compaction` carrying the exact replacement list, and the projection applies it
to reproduce precisely what the next request saw. The originals stay in the log
forever, so a reopened session shows the conversation as it happened rather than
the tail the model was left with (D48).

## Fitting the window

Prompt and completion share one window, and the history is only one of the three
things that need room. Every budget is derived from the acting model's own
window rather than from a constant (D40), because one number cannot serve a
65,536-token local model and a 1,048,576-token hosted one. For the local Qwen3
preset — a 65,536-token window, a 16,384-token reply ceiling — the arithmetic is:

| quantity | value | derivation |
|---|---|---|
| margin (estimation error) | 6,553 | `max(512, ctx // 10)` |
| overhead (system prompt, tool schemas) | 4,096 | `max(2048, ctx // 16)` |
| prompt budget | 38,503 | `ctx − reply − margin − overhead` |
| compaction reserve | 8,192 | `max(2048, ctx // 8)` |
| **compaction fires above** | **30,311** | `prompt budget − reserve` |
| **kept verbatim through a compaction** | **8,192** | `max(8192, ctx // 16)` |

So compaction fires at roughly 46% of the nominal window, while there is still
room to perform it. Waiting until the arithmetic has already failed is what ends
a turn at the provider (N116).

## The two moves

Context management runs cheapest-first, which is what the field has converged
on: clearing stale tool output before reaching for summarization at all.

### Mask

The largest tool results are already files. When output outgrows what a message
should hold, the client writes it to a spill file and the model is handed a
sentence naming the path — it reads the file with `fs_read` like any other.
Once such a result is older than the kept tail, the preview attached to the
pointer is redundant, so it is replaced with the pointer alone
(`context.py::_mask_stale`).

This is *observation masking*, and it is deliberately not an event: it is a pure
function of the history and the window, so the next turn re-derives exactly the
same masking from the same log, so there is nothing for the log to preserve.
Compaction is an event for the opposite reason. Masking cannot lose data,
because the file is still there to read, and it frequently frees enough on its
own that no summarization call is needed at all.

### Compact

When masking is not enough, the oldest span is summarized and replaced. The head
becomes:

```
[ Conversation summary: <the anchor> ]
[ the original user request, verbatim ]
[ … the most recent history, verbatim … ]
```

The anchor leads because it is a `system` message and a chat template that
insists system messages come first — llama.cpp's Qwen template raises *"System
message must be at the beginning"* — refuses one that follows a user turn. The
request already opens with the system prompt, so the anchor joins the leading
run and the pinned task follows it. Getting this order wrong is a hard provider
`500`, not a degradation.

Three properties make this survive a long session.

**The summary is structured, not freeform.** The summariser is given a schema —
`Task`, `Established facts` (with exact values), `Decisions` (with reasons),
`Artifacts` (with paths), `Ruled out` (and why), `Next step` — because "why we
abandoned approach A" is exactly what a generic summary drops and exactly what
stops the agent retrying it.

**The task statement is pinned verbatim**, outside the anchor, so the one thing
that must never drift cannot. A summary may restate the task; the restatement is
where drift lives, and the fourth generation of a summary can describe a session
very differently from its first message.

**The anchor is merged into, never regenerated.** When a later compaction runs,
it summarizes only the newly evicted span and folds it into the standing anchor.
Regenerating the whole summary from scratch each cycle is the strategy that
drifts; merging is anchored iterative summarization, which is measured better at
keeping paths, values, and decisions.

Within one compaction, the summariser reads the head in pieces (a history being
compacted is about as large as the window it must fit inside, so sending it
whole would overflow exactly as the turn did). Each piece is merged into the
running anchor in turn rather than summarized independently and folded
afterwards: a piece summarized alone has only its own text, so a fact stated
earlier exists nowhere when a later piece is written.

**The boundary is an invariant.** Compaction removes messages and has to choose
where, so it cuts only where a history may legally begin — never between an
assistant's `tool_calls` and the replies it is owed. Both ways of getting that
wrong ended sessions at the provider (N116).

What compaction does *not* keep is worth stating: tool results are read by the
summariser only as a short preview, and a model's own reasoning is dropped
entirely — it is the largest field on a reasoning model's reply and the least
useful in a description of what happened.

## Reasoning, truncation, and the spiral

The failure this design was built against is not compaction losing information
in isolation; it is compaction and a reasoning overrun feeding each other.

On a dialect with no inbound thinking field (`effort` and `toggle` — a flat
`reasoning_effort`, or a template's `enable_thinking`), a reply cut off at the
output cap mid-thought has
nowhere on the assistant turn to carry the thought back. The projection writes
it as a following `user` turn — the position that means *context* rather than
*speech* — labelled as unfinished and asked to conclude (N139). The tail of that
thought is what is handed back, not the whole of it: replaying all 16,384 tokens
grows the history until compaction fires, and the compaction then throws the
thought away, so the model re-derives what it had already worked out.

The engine behind that is that thinking expands to fill whatever output
allowance it is given; raising the cap makes the overrun worse rather than
better. On some model families the level is not a compute dial at all — the
Qwen template turns `reasoning_effort` into one sentence in the system prompt,
where `xhigh` says "consider plausible alternatives" and `medium` says nothing —
so a level is a behavioural instruction and the catalog has to name it in the
model's own vocabulary. The local preset therefore declares the template's real
levels (`none`, `low`, `medium`, `xhigh`) and defaults to `medium`, and the
context manager asks for `low` only on its own summarisation call (N144).

## Why it is built this way

The design follows the measured literature rather than intuition:

- **Anchored iterative summarization** — merging into a persistent structured
  summary rather than regenerating — scored highest in Factory's evaluation of
  compression across real engineering sessions: 3.70 overall, 4.04 on accuracy,
  against Anthropic's 3.44/3.74 and OpenAI's 3.35/3.43. The decisive number is
  that OpenAI's endpoint achieved the *highest* compression (99.3% of tokens
  removed) and the *worst* quality: compression ratio is the wrong metric.
- **Observation masking before summarization** — JetBrains' "The Complexity
  Trap" (arXiv 2508.21433) measured clearing stale tool output cutting cost
  ~52% while *improving* SWE-bench solve rate ~2.6%, against pure summarization
  that matched the cost and produced trajectories up to 15% longer, because
  summaries obscured the signal that a task was finished.
- **A schema, not prose** — the categories above map one-to-one onto the ways
  freeform summaries silently drop load-bearing facts.
- **Failure-driven tuning** — ACON (arXiv 2510.00615) treats the compressor
  prompt as something to improve from recorded failures, reducing peak tokens
  26–54% at ~95% of task accuracy, and needs no fine-tuning. Neuman's compaction
  events are the record such tuning would read.
- **Reasoning is not free** — a reasoning model's thinking is billed as output
  and spends the answer's allowance before the answer begins; the published
  behaviour is that the thinking expands to fill the budget (arXiv 2511.04108).

Parallel compaction (arXiv 2605.23296), which bounds summary volume per block
and moves the summarizer off the critical path, is a latency optimization on top
of this, not a correctness fix.

## Branches and forking

The harness runs a **branch**, not a session: a branch is one transcript, one
append-only log, one turn at a time (enforced per branch, not per connection —
D63). A session is the conversation
the rail shows and may hold several branches; a fork copies a prefix of one
into a new one in the same session, so the original stays exactly as it was
(D83).

A fork boundary is a **completed turn**. `turn_complete` is written on every
exit path, after the last tool result and the reply, so a boundary naming one
cannot cut between an assistant's `tool_calls` and its answers. `before` takes
the last completed turn at or before the requested seq, `after` the first at or
after it, and omitting the seq takes the last one; a turn left open by a crash
is not a boundary. `decision/harness/boundary.py` owns the rule, resolved
against the same rows the copy reads.

What is inherited is **conversation, not history**: user turns, final replies,
tool results, and the `turn_complete` and `compaction` events a later turn or
fork needs. Requests, chunks, tool proposals and decisions stay in the source.
The child starts with a `fork` marker naming the source branch, the boundary and
the copied count; `inherited_event_count` records the prefix length including the
marker, so the child's own rows begin one row after it.

The projection makes this safe without a special case: a forked branch's
history is the projection of its own log, exactly as any other branch's is, and
compaction's pinned task still works because the inherited prefix contains the
root's first user message — either directly or inside the compaction
replacement's `[anchor, task, tail]`. The harness re-reads policy every step, so
the inherited transcript confers no authority.

## Where the code is

- `decision/harness/loop.py` — the turn, the budget arithmetic, the truncation
  and overflow recovery.
- `decision/harness/projection.py` — history from the event log, and the
  resumption of an unfinished thought.
- `decision/harness/context.py` — masking and compaction.
- `decision/harness/boundary.py` — where a branch may be forked from.
- `decision/harness/spill.py` — the note that stands in for spilled output.
- `decision/harness/jobs.py` — what the model is told about a background job:
  that a call became one, that one could not, and that one ended (D98).
- `decision/harness/reasoning.py` — the output dialects.
- `decision/harness/prompt.py` — the system prompt and tool advertisement.
- `decision/ops/fork.py` — copying a prefix, the marker and the audit entry.
