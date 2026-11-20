-- 0001_initial.sql — the decision core's schema.
--
-- One database on the appliance (D18). Everything the core decides about lives
-- here; what deliberately does not is the policy overlay (must survive a wiped
-- database), the audit JSONL mirror (the integrity anchor) and provider keys
-- (a secret must not be in a row every dump carries, D77).
--
-- One file until the first release, squashed from the nineteen migrations that
-- grew it: before release a schema change edits this file and an appliance is
-- started over rather than upgraded, so the schema carries nothing that exists
-- only to bring an older one forward. At release it freezes, and from then on a
-- change is a new migration and an applied one is immutable.
--
-- Timestamps are `timestamptz`; `now()` in a transaction is the statement
-- timestamp, which is what audit ordering wants. A state on a row is a nullable
-- timestamp saying when it happened, never a boolean.

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------

-- An account is Neuman's own (D16): a row with an argon2id hash, created by an
-- administrator, never observed from the host. The local account a client runs
-- under is what tools run as, and need not share this name (D67).
--
-- The preferences beside the identity — palette, reasoning display, standing
-- instructions, how long a session stays in the working set — are read once
-- with it, so a preferences table would buy extensibility an `ALTER TABLE`
-- already provides and cost a join on the hottest read. Each bound is in the
-- column as well as the route: a value refused by name is a better answer, and
-- the CHECK is the backstop under it. `instructions` is capped at roughly 500
-- tokens so it cannot by itself break the prompt arithmetic (D65).
CREATE TABLE accounts (
    id                   TEXT        PRIMARY KEY,
    name                 TEXT        NOT NULL UNIQUE,
    password_hash        TEXT,
    must_change_password BOOLEAN     NOT NULL DEFAULT false,
    disabled_at          TIMESTAMPTZ,
    first_seen           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Moved forward whenever one of the account's login sessions is used.
    last_seen            TIMESTAMPTZ NOT NULL DEFAULT now(),
    palette              TEXT        NOT NULL DEFAULT 'dark'
        CONSTRAINT accounts_palette_known CHECK (palette IN (
            'dark', 'light', 'nord',
            'everforest-dark', 'everforest-light',
            'gruvbox-dark', 'gruvbox-light'
        )),
    collapse_reasoning   BOOLEAN     NOT NULL DEFAULT false,
    instructions         TEXT        NOT NULL DEFAULT ''
        CONSTRAINT accounts_instructions_bounded CHECK (char_length(instructions) <= 2000),
    active_minutes       INTEGER     NOT NULL DEFAULT 30
        CONSTRAINT accounts_active_minutes_bounded CHECK (active_minutes BETWEEN 0 AND 1440),
    -- What a new branch starts with. The name is the model catalog's, and the
    -- level is one that model declares; both are validated against the live
    -- catalog at write time. A stored name whose model is later revoked or
    -- uninstalled is not repaired here: the client shows "no model" and says
    -- why, which is truer than silently answering with a different model.
    default_model        TEXT,
    default_reasoning    TEXT
);

-- One person, one name, whatever the case it was typed in.
CREATE UNIQUE INDEX accounts_name_lower ON accounts (lower(name));

-- Groups are Neuman's own too. `neuman-admin` is the administrator group, and
-- the bootstrap is what first puts someone in it.
CREATE TABLE groups (
    id          TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id    TEXT NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    account_id  TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, account_id)
);

CREATE INDEX group_members_by_account ON group_members (account_id);

INSERT INTO groups (id, name) VALUES ('neuman-admin', 'neuman-admin');

-- A browser's login session. Neuman mints an opaque token and stores only its
-- BLAKE2b-256 hash, so a database read cannot be replayed as a session.
CREATE TABLE auth_sessions (
    token_hash   BYTEA       PRIMARY KEY,
    account_id   TEXT        NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    csrf_token   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX auth_sessions_by_account ON auth_sessions (account_id);
CREATE INDEX auth_sessions_by_expiry ON auth_sessions (expires_at);

-- ---------------------------------------------------------------------------
-- Settings and policy
-- ---------------------------------------------------------------------------

-- The runtime baseline (policy layers and the model catalog) under `baseline`,
-- and the operator's prompt under `prompt`. Created empty on a fresh database
-- and written only by an administrator (D68).
CREATE TABLE settings (
    key         TEXT        PRIMARY KEY,
    value       JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A role granted to a group, so everyone in `eng` gets `engineer`. Who granted
-- it is the audit chain's to record, once.
CREATE TABLE policy_grants (
    group_name TEXT        NOT NULL,
    role       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_name, role)
);

-- A generation bumps whenever an account's composed policy changes. A dispatch
-- validates against the *live* policy, so revocation takes effect on the next
-- call; the generation is how a live session is told rather than finding out.
CREATE TABLE policy_generations (
    account_id  TEXT        PRIMARY KEY REFERENCES accounts (id) ON DELETE CASCADE,
    generation  INTEGER     NOT NULL DEFAULT 0,
    policy_hash TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Agent sessions and the event log
-- ---------------------------------------------------------------------------

-- A conversation: the row the rail shows, and what a person means by "I
-- have three sessions". Its states are separate decisions by different people:
-- `closed_at` says it is over and arms retention; `removed_at` says stop
-- showing it to me, and is a column rather than a DELETE because the transcript
-- is referenced by the audit chain; `pinned_at` says show it first. Server-side
-- rather than in a browser, so they hold on every device and apply before the
-- listing's LIMIT rather than after it.
--
-- The log is not here. A session has one or more **branches** (below), and a
-- fork adds one; that is what lets a branch be explored without a second row in
-- the list. Everything about a branch — its working directory, attachments,
-- tool selection, model, reasoning level, approval mode — lives on the branch,
-- because it is a statement about the conversation that branch is having.
CREATE TABLE agent_sessions (
    id          TEXT        PRIMARY KEY,
    account_id  TEXT        NOT NULL REFERENCES accounts (id) ON DELETE RESTRICT,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at   TIMESTAMPTZ,
    removed_at  TIMESTAMPTZ,
    pinned_at   TIMESTAMPTZ,
    -- When the owner last had this session in front of them: opening it writes
    -- this, and a reply newer than it is unread. One column because the
    -- watermark and "recently looked at" are one fact, and because it is the
    -- account's rather than any window's — reading on the phone has to clear the
    -- badge on the laptop (D63).
    read_at     TIMESTAMPTZ,
    -- The task run whose agent step this session is, or NULL for a
    -- conversation. A task's transcript belongs to the task, not to chat: every
    -- read a person makes from the chat window — the list, the working set,
    -- search, opening it, a socket — leaves these rows out, and the task view
    -- reads them through the run (D99). Text, not a key, for the reason
    -- `task_runs.steps` holds session ids as text: the transcript and the run
    -- record have different lifetimes, and neither deletion may take the other.
    task_run_id TEXT
);

CREATE INDEX agent_sessions_by_account ON agent_sessions (account_id, created_at DESC);

-- One transcript inside a session. A branch is the unit the harness runs: one
-- append-only log, one socket, one turn at a time. A fork copies a prefix of
-- another branch into a new one, so the original stays exactly as it was.
--
-- `forked_from` is provenance, not a foreign key: the source branch is in the
-- same session and the whole session is purged together, but a future branch
-- deletion must not cascade or block. `inherited_event_count` is the length of
-- the copied prefix, which is what labels a branch by its first *new* message.
--
-- `model`, `reasoning` and `approval` are the branch's choices for the next
-- turn, restored when the reader switches back. NULL means "follow the account
-- default" for the first two and "follow composed policy" for approval; the
-- harness clamps approval to the operator's ceiling every step, so a branch
-- cannot widen what the account may approve.
CREATE TABLE session_branches (
    id                    TEXT        PRIMARY KEY,
    session_id            TEXT        NOT NULL REFERENCES agent_sessions (id) ON DELETE CASCADE,
    forked_from           TEXT,
    forked_from_seq       BIGINT,
    inherited_event_count BIGINT      NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    working_dir           TEXT,
    attachments           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    tool_selection        JSONB,
    model                 TEXT,
    reasoning             TEXT,
    approval              TEXT,
    -- The target of the composite key `session_events` uses, so a row can
    -- carry the session for querying without being able to name another one.
    UNIQUE (session_id, id)
);

CREATE INDEX session_branches_by_session ON session_branches (session_id, created_at);

-- The append-only log of one branch. Model-visible means logged: every request
-- the harness sends and every output a model or tool produces is a row,
-- verbatim except for secrets and NULs — and `redactions` records each JSON
-- pointer that was altered, so a changed row says so. Model history is derived
-- from this by `decision/harness/projection.py`, never held as a list that can
-- drift.
--
-- `session_id` is carried beside `branch_id` so a session's activity and its
-- search document are one index scan rather than a join, and the composite
-- foreign key makes it impossible for the two to disagree. (branch_id, seq) is
-- the log's position; a branch's first event is 1, a fork's inherited prefix is
-- contiguous, and the next append continues from its end.
CREATE TABLE session_events (
    session_id TEXT        NOT NULL,
    branch_id  TEXT        NOT NULL,
    seq        BIGINT      NOT NULL,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind       TEXT        NOT NULL,
    message_id TEXT,
    payload    JSONB       NOT NULL,
    redactions JSONB,
    PRIMARY KEY (branch_id, seq),
    FOREIGN KEY (session_id, branch_id)
        REFERENCES session_branches (session_id, id) ON DELETE CASCADE
);

CREATE INDEX session_events_by_kind ON session_events (branch_id, kind);
CREATE INDEX session_events_by_session ON session_events (session_id, ts DESC);

-- A derived, rebuildable search view over what was *said*: one row per
-- session, folding every branch's user messages and final replies, because the
-- question is "which conversation was that in" and the rail shows sessions, not
-- branches. Indexing tool output would bury it. The text is kept beside the
-- vector so a match can show why it matched; a hit opens the session's most
-- recently active branch.
CREATE TABLE session_search (
    session_id TEXT     PRIMARY KEY REFERENCES agent_sessions (id) ON DELETE CASCADE,
    document   TSVECTOR NOT NULL,
    body       TEXT     NOT NULL DEFAULT ''
);

CREATE INDEX session_search_document ON session_search USING GIN (document);

-- The bytes an `fs_image` call produced, stored once and named by hash
-- everywhere else, so a screenshot kept through compaction is not copied into
-- every snapshot that retains it. Decoded bytes rather than base64, which is a
-- wire encoding. In the database rather than a directory so the backup already
-- covers it.
CREATE TABLE session_images (
    sha256     TEXT        PRIMARY KEY,
    media_type TEXT        NOT NULL,
    data       BYTEA       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The reference edges that let a shared, content-addressed image be collected:
-- one row per (session, image), written with the bytes, cascading with the
-- session. Rooted at the session rather than the branch because a fork inherits
-- the conversation and therefore may show the same picture: a copy that rooted
-- its own images would need every fork to copy refs, and a purge of the source
-- could collect bytes the fork still names. An image no edge reaches is
-- sweepable — reachability from roots, the Nix-store shape — and a crash
-- between the write and its event over-roots, which is the safe direction.
CREATE TABLE session_image_refs (
    session_id TEXT NOT NULL REFERENCES agent_sessions (id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    PRIMARY KEY (session_id, sha256)
);

CREATE INDEX session_image_refs_by_hash ON session_image_refs (sha256);

-- ---------------------------------------------------------------------------
-- Tasks (D92–D97): a standing approval, not a principal
-- ---------------------------------------------------------------------------

-- One row is an account's saved recipe: a workspace, a run root, inputs, an
-- ordered list of steps and a schedule. It is deliberately *not* an account or
-- a service user. Each agent step's envelope is a policy fragment, intersected
-- with the owner's live policy on every model step, so a task can narrow reach
-- and never widen it. `steps` and `inputs` are the contract's own shapes,
-- validated by the server before they are stored. `next_run_at` is the
-- server's clock, computed from the schedule in the task's own timezone; the
-- run records below are what the UI lists.
CREATE TABLE tasks (
    id          TEXT        PRIMARY KEY,
    account_id  TEXT        NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    workspace   TEXT        NOT NULL,
    run_root    TEXT        NOT NULL,
    inputs      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    steps       JSONB       NOT NULL,
    schedule    JSONB       NOT NULL,
    outputs     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ
);

CREATE INDEX tasks_by_account ON tasks (account_id, created_at DESC);

-- The scheduler's only scan. Partial, because a disabled task and a task with
-- no next run are not candidates and should not keep the index warm.
CREATE INDEX tasks_due ON tasks (next_run_at)
    WHERE enabled AND next_run_at IS NOT NULL;

-- One execution of a task. `steps` is one record per task step — status, exit
-- code, and for an agent step the session that is its transcript and audit
-- chain. Those session ids are kept as text rather than foreign keys: a session
-- may be purged by retention, but the record that the task ran, when, and how
-- each step ended is a governance fact and outlives the transcript.
--
-- Status is derived from each step's structured result — an exit code, the
-- harness's terminal reason — never from the model's prose report. `waiting` is
-- the honest state for a due run whose owner has no client connected: the
-- client is the executor, and there is nowhere else a dispatch can go (D55).
-- `missed` is the catch-up window expiring.
CREATE TABLE task_runs (
    id              TEXT        PRIMARY KEY,
    task_id         TEXT        NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    account_id      TEXT        NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    trigger         TEXT        NOT NULL
        CONSTRAINT task_runs_trigger_known CHECK (trigger IN ('schedule', 'manual')),
    status          TEXT        NOT NULL
        CONSTRAINT task_runs_status_known CHECK (status IN (
            'due', 'waiting', 'running', 'succeeded',
            'failed', 'budget', 'missed', 'cancelled'
        )),
    inputs          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    run_dir         TEXT,
    steps           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    outputs_present JSONB,
    due_at          TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error           TEXT,
    cost_usd        DOUBLE PRECISION,
    tokens          BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX task_runs_by_task ON task_runs (task_id, created_at DESC);

-- The overview's read: every task's recent runs for one account at once.
CREATE INDEX task_runs_by_account ON task_runs (account_id, created_at DESC);

-- The runner's queue: a run created but not yet started, or one waiting for
-- its owner's client. Ordered by when it was due, so a catch-up does not
-- overtake the run that has been waiting longest.
CREATE INDEX task_runs_pending ON task_runs (status, due_at)
    WHERE status IN ('due', 'waiting');

-- ---------------------------------------------------------------------------
-- Gateway accounting (D20: our tables, our code, no proxy framework)
-- ---------------------------------------------------------------------------

-- Per-account daily token accounting with reservation. Counting only completed
-- calls is not enough: an account at 199k of a 200k cap can overshoot by a full
-- completion. A call reserves its ceiling before dispatch and settles against
-- the reported usage afterwards, or releases on failure.
CREATE TABLE usage_daily (
    account_id TEXT        NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    day        DATE        NOT NULL,
    settled    BIGINT      NOT NULL DEFAULT 0,
    reserved   BIGINT      NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, day)
);

-- Individual reservations, so settle and release are idempotent by id and one
-- orphaned by a crash can be swept by age; an aggregate alone would have to be
-- zeroed at startup, which is right only while calls are single-flight. They
-- cascade from their day, and the day from the account.
CREATE TABLE usage_reservations (
    id         TEXT        PRIMARY KEY,
    account_id TEXT        NOT NULL,
    day        DATE        NOT NULL,
    tokens     BIGINT      NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, day) REFERENCES usage_daily (account_id, day) ON DELETE CASCADE
);

CREATE INDEX usage_reservations_by_age ON usage_reservations (created_at);

-- ---------------------------------------------------------------------------
-- Audit and operations
-- ---------------------------------------------------------------------------

-- Append-only, hash-chained governance record: hash = H(prev_hash || canonical
-- (row)), the first row chaining from 64 zeros. Tampering is not prevented —
-- root can edit the database — but it is evident: the chain breaks. Rows are
-- mirrored to an append-only JSONL file, the durable artifact (D18).
CREATE TABLE audit_chain (
    seq       BIGSERIAL   PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor     TEXT,
    action    TEXT        NOT NULL,
    payload   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash TEXT        NOT NULL,
    hash      TEXT        NOT NULL UNIQUE
);

CREATE INDEX audit_chain_by_actor ON audit_chain (actor, ts DESC);
CREATE INDEX audit_chain_by_action ON audit_chain (action, ts DESC);

-- A derived, rebuildable search view over the chain (D8). Rebuilding it never
-- touches the chain, so a corrupt index is an inconvenience, not an integrity
-- problem.
CREATE TABLE audit_search (
    seq      BIGINT   PRIMARY KEY REFERENCES audit_chain (seq) ON DELETE CASCADE,
    document TSVECTOR NOT NULL
);

CREATE INDEX audit_search_document ON audit_search USING GIN (document);

-- Warnings and errors for the administration page's "what is wrong" view. Not
-- the audit chain: governance is there, and this is the server's own account
-- of trouble.
CREATE TABLE events (
    id          BIGSERIAL   PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    level       TEXT        NOT NULL,
    logger      TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    fields      JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX events_by_time ON events (at DESC);
