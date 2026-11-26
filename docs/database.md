# The database

There is exactly one database on the appliance (D18). This document says what
is in it, table by table, and what deliberately is not.

The appliance runs its own Postgres cluster. The server connects over the local
Unix socket with peer authentication as the `neuman` role, so no password
appears in any configuration file. One pool per process, owned by `foundation`
and nowhere else. Decision code never writes SQL — it goes through repository
interfaces, which exist for testability, not to promise a second engine.

The schema is SQL in `neuman-server/src/neuman_server/foundation/db/migrations/`,
applied in name order, each file in one transaction and recorded with its
SHA-256; editing an applied file is a hard error, and a downgrade is a restore
from backup. It froze at the first release (D103): `0001_initial.sql` is the
initial schema and is never edited, and a change is the next numbered file. The
initial file carries the reasoning for every table; this page is the map.

## Everything in it

Nineteen tables plus the migrator's own bookkeeping. `→` marks a foreign
key and names its delete behaviour; **bold** marks the primary key.

| Table | Key | Columns | Holds |
|---|---|---|---|
| `accounts` | **`id`** TEXT | `name` TEXT UNIQUE (case-insensitively unique), `password_hash` TEXT, `must_change_password` BOOL, `disabled_at` TSTZ, `first_seen` TSTZ, `last_seen` TSTZ (moved by every login and session use), `palette` TEXT `'dark'`, `collapse_reasoning` BOOL, `active_minutes` INT `30`, `instructions` TEXT `''`, `default_model` TEXT NULL, `default_reasoning` TEXT NULL | One row per account. The row *is* the identity; disabling it is the revocation. |
| `groups` | **`id`** TEXT | `name` TEXT UNIQUE, `created_at` TSTZ | Policy groups. `neuman-admin` is built in. |
| `group_members` | **(`group_id`, `account_id`)** | both → their table CASCADE | Membership, read live for every policy composition. |
| `settings` | **`key`** TEXT | `value` JSONB, `updated_at` TSTZ | The runtime baseline and prompt (D68). |
| `events` | **`id`** BIGSERIAL | `at` TSTZ, `level` TEXT, `logger` TEXT, `message` TEXT, `fields` JSONB | Warnings and errors for the administration panel. Indexed by time. |
| `auth_sessions` | **`token_hash`** BYTEA | `account_id` → `accounts` CASCADE, `csrf_token` TEXT, `created_at` TSTZ, `expires_at` TSTZ, `last_seen_at` TSTZ | Login sessions, keyed by the BLAKE2b-256 hash of the token. Indexed by account and by expiry. |
| `machines` | **(`account_id`, `label`)** | `account_id` → `accounts` CASCADE, `label` TEXT, `kind` TEXT `'client'`, `notes` TEXT `''`, `created_at` TSTZ, `last_seen_at` TSTZ, `home` TEXT, `drops` TEXT, `tools` JSONB `'[]'`, `release` TEXT, `rpc` INT | One row per named place this account's dispatches run: the last hello from a client, kept after the socket goes (D112). `kind` is `client` today; `ssh` is reserved by a CHECK. Indexed by `label`, which spans accounts for the operator's "who is on the dgx". |
| `policy_generations` | **`account_id`** → `accounts` CASCADE | `generation` INT, `policy_hash` TEXT, `updated_at` TSTZ | One row per account: the counter and hash that make revocation take effect on the next call. |
| `policy_grants` | **(`group_name`, `role`)** | `created_at` TSTZ | Which groups hold which roles. A group with no members is inert. Who granted it is the audit chain's to record. |
| `agent_sessions` | **`id`** TEXT | `account_id` → `accounts` RESTRICT, `title` TEXT, `created_at` TSTZ, `closed_at` TSTZ, `removed_at` TSTZ, `pinned_at` TSTZ, `read_at` TSTZ, `task_run_id` TEXT | One row per conversation — what the rail shows — or per task agent step, when `task_run_id` names its run; chat's reads leave those out (D99). `read_at` is the owner's read watermark: a reply newer than it is unread, and opening the session moves it. Indexed by `(account_id, created_at DESC)`. |
| `session_branches` | **`id`** TEXT | `session_id` → `agent_sessions` CASCADE, `forked_from` TEXT, `forked_from_seq` BIGINT, `inherited_event_count` BIGINT `0`, `created_at` TSTZ, `machine` TEXT NOT NULL, `working_dir` TEXT, `attachments` JSONB `[]`, `tool_selection` JSONB NULL, `model` TEXT, `reasoning` TEXT, `approval` TEXT, UNIQUE (`session_id`, `id`) | One transcript inside a session: the settings its next turn runs with, and what a fork creates. `machine` is where a turn dispatches (D112) and is required: an upgrade from a database written before it needs one statement, named in the error (0003). Indexed by `(session_id, created_at)`. |
| `session_events` | **(`branch_id`, `seq`)** | `session_id` TEXT, `branch_id` → (`session_id`, `id`) of `session_branches` CASCADE, `ts` TSTZ, `kind` TEXT, `message_id` TEXT, `payload` JSONB, `redactions` JSONB | The append-only log of one branch — every model-visible request and output. Indexed by `(branch_id, kind)` and `(session_id, ts DESC)`. |
| `session_images` | **`sha256`** TEXT | `media_type` TEXT, `data` BYTEA, `created_at` TSTZ | The one copy of each image's bytes, content-addressed. No foreign key: one picture may be referenced by several sessions. |
| `session_image_refs` | **(`session_id`, `sha256`)** | `session_id` → `agent_sessions` CASCADE | The reference graph that roots each image. An image is collected only when no row names it. Indexed by `sha256`. |
| `session_search` | **`session_id`** → `agent_sessions` CASCADE | `document` TSVECTOR, `body` TEXT | A derived, rebuildable full-text view over what was *said* in a session. GIN on `document`. |
| `usage_daily` | **(`account_id`, `day`)** | `account_id` → `accounts` CASCADE, `settled` BIGINT, `reserved` BIGINT, `updated_at` TSTZ | Per-account, per-day token accounting. |
| `usage_reservations` | **`id`** TEXT | `account_id` TEXT, `day` DATE, `tokens` BIGINT, `created_at` TSTZ, (`account_id`, `day`) → `usage_daily` CASCADE | Individual in-flight reservations. Indexed by `created_at` so orphans can be swept by age. |
| `tasks` | **`id`** TEXT | `account_id` → `accounts` CASCADE, `name` TEXT, `workspace` TEXT, `run_root` TEXT, `machine` TEXT NOT NULL, `inputs` JSONB `[]`, `steps` JSONB, `schedule` JSONB, `outputs` JSONB `[]`, `enabled` BOOL `true`, `created_at` TSTZ, `updated_at` TSTZ, `next_run_at` TSTZ, `last_run_at` TSTZ | An account's saved recipe (D92–D97). `steps` and `inputs` are the `task` contract's shapes. Indexed by `(account_id, created_at DESC)`, and partially by `next_run_at` for the scheduler. |
| `task_runs` | **`id`** TEXT | `task_id` → `tasks` CASCADE, `account_id` → `accounts` CASCADE, `trigger` TEXT, `status` TEXT, `inputs` JSONB `{}`, `run_dir` TEXT, `steps` JSONB `[]`, `outputs_present` JSONB, `due_at` TSTZ, `started_at` TSTZ, `finished_at` TSTZ, `error` TEXT, `cost_usd` DOUBLE, `tokens` BIGINT, `created_at` TSTZ | One execution and a record per step, a run step's record holding the end of its output. Session ids inside `steps` are text, not keys, so the record outlives a purged transcript. Indexed by `(task_id, created_at DESC)` and `(account_id, created_at DESC)` for the overview, and partially by `(status, due_at)` as the runner's queue. |
| `audit_chain` | **`seq`** BIGSERIAL | `ts` TSTZ, `actor` TEXT, `action` TEXT, `payload` JSONB `{}`, `prev_hash` TEXT, `hash` TEXT UNIQUE | The hash-chained governance record. Indexed by `(actor, ts DESC)` and `(action, ts DESC)`. |
| `audit_search` | **`seq`** → `audit_chain` CASCADE | `document` TSVECTOR | Derived, rebuildable full-text index over the chain, with a GIN index. |
| `schema_migrations` | **`version`** INT | `name` TEXT, `checksum` TEXT, `applied_at` TSTZ | Bookkeeping. The only table not created by a file in `migrations/` — the migrator bootstraps it itself. |

Every timestamp is `TIMESTAMPTZ` (`TSTZ` above) defaulting to `now()`, except
`auth_sessions.expires_at`, which is always supplied, and the nullable
`disabled_at` / `closed_at` / `removed_at` / `pinned_at`. The sections below say
*why* each table looks like this.

## What is *not* in it

Three stores are files by design, and the reasons are not interchangeable:

| Store | Path | Why not Postgres |
|---|---|---|
| The policy overlay | in the state directory, per user | It is the runtime-authoring surface and **must survive a wiped database**, or an operator's revocation silently reverts. |
| The audit JSONL mirror | `/var/log/neuman/audit.jsonl` | It is the integrity anchor — append-only on disk, which is what makes "the database was edited" detectable after a restore. |
| Provider keys | `/var/lib/neuman/state/secrets/<provider>.key`, `0600` | A secret must not be in a row every dump and backup carries. The baseline names the file; the file is never read by anything but the gateway (D77). |

A backup is therefore a `pg_dump` plus the first two, and deliberately not the
third: restoring an appliance means entering its provider keys again.

The **model catalog** is part of the runtime baseline in `settings`, made in
the administration page; an entry added from a preset records it, and its facts
are the preset's (D76). It is a document rather than a table of its own for the
reasons in D35.

Derived data in general stays out of the core: retrieval and search indexes are
rebuildable, excluded from backups, and not part of this schema (D1). The two
`*_search` tables are the acknowledged exception and prove the rule — both are
rebuildable from the record they index, neither is in a backup, and a startup
job repairs either if it diverges, so losing one costs a rebuild rather than
data.

## Identity

**`accounts`** — the account itself (D16). A name, an argon2id `password_hash`,
a `disabled_at`, `must_change_password`, and `first_seen` / `last_seen`. There
is no `uid` and no NSS observation: the row *is* the identity, and disabling it
is the revocation (D17). There is no home either: the client reports the home
`$HOME` expands under on every connection (N145), and with no client connected a
`$HOME` grant reaches nothing.

It also carries what the person chose for themselves: the client palette,
whether reasoning is folded (D53), how long a session stays in the account's
working set after it is last looked at (D63), and their own standing
instructions for the model (D65). None of that is governed state — it grants nothing, and the
instructions ride into the prompt where they are bounded like everything else.
They live on this row rather than in a preferences table because they are read
alongside the identity, and a join on that read would buy extensibility an
`ALTER TABLE` already provides.

**`groups` / `group_members`** — policy keys (D16). A grant names a group; a
person reaches it through membership. `neuman-admin` is the built-in operator
group, so the admin socket and the web panel agree about who is an operator.

**`auth_sessions`** — login sessions. Login verifies the argon2id hash; Neuman
mints an opaque token and stores **only its BLAKE2b-256 hash** as the primary
key, so a database read cannot be replayed as a session. Also the CSRF token,
the owning account, and created / expires / last-seen timestamps. Deleted with
the account.

**`machines`** — the named places this account's dispatches run (D112). Keyed
`(account_id, label)`, because reach differs per person: Alice's `dgx` and
Bob's `dgx` are one physical box and two machines. The row is what a socket
used to be — every fact a `hello` reports, kept after the connection goes — so
`$HOME` expands the same way whether the computer is awake or asleep, and an
offline machine is an answer rather than a hole. `notes` is the only field an
operator writes: what is installed there, told to the model, because the
handshake advertises Neuman tools and says nothing about `PATH`.

That the label is a *claim* is the same limit as every other client report. It
is scoped to the account, so a wrong name can only point at one of that
account's own computers — but it is not attested, and cannot be, since the two
ends are different machines (D67). The footnote on `[^reported]` in
`docs/security-model.md` is the long version.

## Policy

**`policy_generations`** — one row per account: a `generation` counter and the
hash of the composed policy. A dispatch validates against the *live* policy, so
revocation takes effect on the next call rather than when a session-lifetime
snapshot expires.

**`policy_grants`** — role grants keyed to **groups, not users** (D16). A grant
naming a group with no members is inert, not an error. It names the group
rather than referencing it, so a grant creates its group and deleting a group
revokes its grants first (D85). Records who granted it and when.

Note what is *not* here: the policy itself. Grants bind groups to roles; the
roles, the reach they imply and the per-account overlay live in the `settings`
row above and the overlay file beside it. This table says who is in scope, not
what they may reach.

**`settings`** — the policy, model and tool-package baseline under `baseline` and the operator's
prompt under `prompt`. Created empty by the server on a fresh database, and
written only by an administrator (D68).

## Operational events

**`events`** — warnings and errors for the administration page's "what is
wrong" view. Not the audit chain: governance is in `audit_chain`, and this is
an operational record with a timestamp, level, logger, message and fields.
Retention is the same job that purges sessions.

## Sessions, branches, and the event log

**`agent_sessions`** — one row per conversation: owning account, optional
title, created / closed timestamps, the pin, the removal and the read
watermark. Deletion is `RESTRICT`ed against the account, so history is not
silently destroyed by an account removal. This row is the rail: a fork does not
create one, so a fork does not add a list entry. A task's agent step is a row
too, marked with `task_run_id`; it never appears on the chat rail and is read
through its run (D99).

`read_at` is what the account has looked at, and it does two jobs because they
are one fact: a reply newer than it is **unread**, and opening the session moves
it, so the same row keeps the session in the working set for the account's
`active_minutes` (D63). It is a column rather than a browser flag because
reading on one device has to clear the badge on another — and because the rail's
membership and order are the account's, not a window's.

**`session_branches`** — one transcript inside a session. A session always has
at least one (the root, created with it), and a fork inserts another. Deletion
cascades from its session, and the whole session is what retention purges.

The settings a turn runs with live here rather than on the session, because
they are statements about the conversation a branch is having:

- `machine` — **where it runs.** A named place this account's dispatches go
  (D112), set when the branch is created from the computer that created it. The
  model is told it; a move clears the two path fields below and is refused once
  the branch has run anything, where the remedy is a fork.
- `working_dir` — a **hint**, not a grant. The model is told where the work is;
  the route checks it against composed policy, so a working directory outside
  `files_read` is refused rather than widening anything. It is current state
  rather than history, so it is a column; a change shows up in the next turn's
  logged `request`, which carries the system prompt naming it, rather than as an
  entry of its own.
- `attachments` — a JSONB array of absolute **paths, not bytes**. An attachment
  references a file the agent reads with `fs_read` like any other file: one
  already inside the user's reach, or one dropped on the chat window, which the
  client wrote to its own `drops/` directory (D102). Storing contents would mean
  two copies that drift. A path is accepted only if policy reaches it or it is
  inside the connected client's `drops/`, and the list is re-checked when
  handed to a turn rather than trusted because it was valid once. The union
  over an account's live sessions is what the client sweeps `drops/` against
  (`GET /api/attachments`).
- `tool_selection` — which tools the user wants this conversation to have
  (D50). A **narrowing of what is advertised**, never a grant: it is
  intersected with `gate.grantable` where the schemas are built, so a name that
  policy later stops granting silently stops being offered; `NULL` means every
  granted tool, which keeps tracking the registry, while `[]` is a deliberate
  "no tools".
- `model`, `reasoning`, `approval` — what the next turn of this branch uses,
  restored when the reader switches back. `NULL` follows the account default
  (the first two) or composed policy (approval). Approval is clamped to
  `auto_accept_max` at the point of consent as well as at the route, so a
  branch is a per-conversation preference and not a second policy.
- `forked_from`, `forked_from_seq`, `inherited_event_count` — provenance. The
  first two say which branch and which inclusive seq this one copied; the
  third says how many leading events are inherited, which is how the branch
  switcher labels it by its first *new* message.

A fork copies the projection-relevant prefix of another branch
(`user_message`, `response_final`, `tool_result`, `turn_complete`,
`compaction`) and starts with a `harness_event` marker naming the source. It
does not copy requests, chunks, proposals or decisions: the source keeps the
governance record, and the child cannot claim an approval happened in it.

**`session_events`** — the append-only log of one branch, keyed
`(branch_id, seq)`. **Model-visible means logged**: every request the harness
sends and every output a model or tool produces is one row, verbatim except for
secret redaction. Each row has a `kind` (`user_message`, `response_final`,
`tool_result`, `harness_event`, `response_chunk`, …), an optional `message_id`,
a JSONB `payload`, and JSONB `redactions` recording where secrets were removed.

`session_id` is carried beside `branch_id` so a session's activity and its
search document are one indexed read, and the composite foreign key
`(session_id, branch_id)` makes it impossible for the two to disagree.

The model's conversation history is **derived** from this log by
`decision/harness/projection.py` — never held as an in-memory list that can
drift from the record. Kinds the projection ignores are deliberately
invisible to the model. A projection read fetches only the kinds it can use
(`PROJECTION_KINDS`), through the `(branch_id, kind)` index; the per-token
`response_chunk` rows and the per-step `request` snapshots remain in the log for
the audit and admin views, but opening a session does not read them (D60).

**`session_images`** — the bytes of every image a `returns_image` tool produced,
keyed by the SHA-256 of the content. A `tool_result` event and
the derived history both carry the hash and never the bytes, and the request
builder and the `…/image` route fetch the one copy by hash.

It is a table rather than a blob directory because the appliance's backup is a
`pg_dump` plus the audit mirror and the overlays, and a table keeps that true
without a fourth artifact, a configured path, or a separate permission story.
Content addressing makes a re-read free and makes "which pictures does this
session hold" answerable without a scan. `get` returning `None` is a missing
image, reported as a dropped picture rather than a crash. The bytes are rooted
by `session_image_refs` below; that is how a shared image is told from an
orphaned one at collection time.

**`session_image_refs`** — the reference graph that makes a shared, content-
addressed image safe to collect. One row per (session, image),
cascading with the session. `ImageStore.put` writes the bytes and this edge in
one transaction; `ImageStore.sweep` deletes every image no edge names.

This is the Nix-store shape, and it is why a purge cannot delete "the session's
images" by ownership: an image two sessions read is rooted by both, so purging
one drops its edge and the bytes survive for the other. Purging the last one
leaves the image unreachable and it is collected. The edge is written at the one
place an image enters, so the roots are complete by construction and collection
is an indexed anti-join rather than a scan of `session_events.payload`; and it is
written *before* the `tool_result` is logged — a temporary root — so a sweep
racing the put cannot collect a live picture.

The rows are a superset of what the events reference (a crash between `put` and
the event leaves an over-root). That is the safe direction: an over-rooted image
dies with its session, an under-rooted one would be collected while in use. It
also means there is nothing to rebuild — unlike `session_search`, these rows can
never describe an event that does not exist.

`sweep` runs in the retention pass, after the session rows are removed by
`purge_ids`, and whenever session retention is enabled, so it retries a
collection a crash interrupted instead of leaving the bytes forever. See
`docs/decisions.md` D28 and N148.

**`session_search`** — a derived, rebuildable full-text view over what was
*said* in each session, for the client's session filter (D52).
Same shape as `audit_search` and the same guarantee: rebuilding it never touches
the log it indexes, so a corrupt index is an inconvenience rather than an
integrity problem.

Two differences from the audit index, both deliberate. It is keyed **per session
rather than per event**, because the question it answers is "which conversation
was that in" — per-event rows would mean deduplicating hits back into sessions
on every keystroke. And it keeps the plain `body` beside the vector, because
`ts_headline` needs the original document to produce the snippet a result shows,
and re-deriving that from the log per row would be a second query each.

**Only `user_message` and `response_final` are indexed.** Reasoning is the model
thinking aloud, and a tool result is a file's contents or a directory listing —
indexing those would mean typing a filename matched every session that had ever
listed the directory containing it, burying the ones that actually discussed it.
`tool_proposal` and `tool_decision` are governance records, not speech.

Maintained inline in the same transaction as the event append, so a session
matches its own words on the next keystroke rather than after an indexing lag.
A startup job (`jobs/session_index.py`) repairs divergence after a restore, a
retention purge, or a crash between the two inserts — compared on sessions that
have spoken rather than on row counts, since one row per session and one per
event are never equal.

## Gateway accounting

Neuman owns these tables rather than bundling a proxy framework (D20).

**`usage_daily`** — per-account, per-day token accounting: `settled` and
`reserved`. Counting only completed calls is not enough, because an account at
199k of a 200k cap can overshoot by a full completion; a call reserves its
`max_tokens` before dispatch and settles against reported usage afterwards, or
releases on failure.

**`usage_reservations`** — the individual reservations, so settle and release
are idempotent by id and a reservation orphaned by a crash can be swept by age.
An aggregate column alone would have to be zeroed at startup, which works only
while calls are single-flight — and D19 targets hundreds of concurrent
sessions.

## Audit

**`audit_chain`** — the append-only, hash-chained governance record:
`hash = H(prev_hash || canonical(row))`, the first row chaining from 64 zeros.
Each entry has a sequence number, timestamp, actor, action, and JSONB payload.
Actions include `identity.account_created`, `identity.bootstrap_admin`,
`identity.admin_granted`, `identity.group_created`,
`identity.group_member_added`, `identity.group_member_removed`,
`identity.login`, `identity.login_denied`, `identity.logout`,
`identity.password_changed`, `identity.password_reset`,
`identity.sessions_revoked`, `policy.role_granted`, `policy.role_revoked`,
`policy.overlay_set`, `policy.overlay_cleared`, `policy.auto_accept`,
`settings.baseline_set`, `settings.role_renamed`, `settings.prompt_set`,
`settings.tool_package_enabled`, `settings.tool_package_disabled`, `tool.dispatch`,
`tool.denied`, `ops.backup`, and `ops.restore`.

Governance events are emitted by core operations, never by transport surfaces,
so a surface cannot bypass the chain (D8). Tampering is not prevented — root
can edit the database — but it is **evident**: the chain breaks. Rows are
mirrored to the JSONL file on disk, which is the durable artifact; verification
runs on demand and after every restore.

**`audit_search`** — a derived, rebuildable `TSVECTOR` index over the chain
with a GIN index, because search is a core requirement rather than a follow-up
(D8). Rebuilding it never touches the chain, so a corrupt index is an
inconvenience rather than an integrity problem.

Retention purges chain rows older than a configured window
(`NEUMAN_AUDIT_RETENTION_DAYS`, `0` meaning never).

## Bookkeeping

**`schema_migrations`** — version, name, SHA-256 of the file's bytes with its
comments, and applied-at for every migration (N182: a cosmetic edit still
changes the checksum, which is why an applied file is frozen as text). Created by
the migrator's bootstrap; nothing outside `foundation/db/migrate.py` runs DDL.
