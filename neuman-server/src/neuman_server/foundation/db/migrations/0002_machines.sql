-- 0002_machines.sql — a name for the place dispatches run.
--
-- Before this, "the client" meant whichever socket had most recently said
-- hello, and the core kept exactly one per account. That was enough for one
-- computer and wrong for two: opening a laptop disconnected the desktop,
-- killed its jobs, and left a conversation whose recorded working directory
-- named the other machine's filesystem — silently, because nothing in the
-- prompt or the record said which computer any of it meant.
--
-- The thing that was missing is a noun. A **machine** is a named place
-- dispatches execute: a client's host today, an ssh target later. A branch
-- names one, policy may narrow to a set of them, and every dispatch entry
-- carries the one it ran on. The invariant the rest of the schema can then
-- rely on:
--
--     A machine is the unit of a filesystem. Everything that names a path
--     belongs to one — a working directory, an attachment, a drop, `$HOME`,
--     the surfaces a dispatch is checked against, a job's spill files.
--
-- A machine is **scoped to an account**, not global. Reach differs per person:
-- Alice's `dgx` and Bob's `dgx` are the same physical box and two different
-- machines, because the files each can read there differ. That is also why the
-- label needs no attestation — a connection is already bound to an account by
-- the login that made it, so a machine name can only ever point at one of that
-- account's own places. The cost is that the label in the record is a claim,
-- like every other client-reported fact, and `docs/security-model.md` says so.
--
-- The row is also where a machine's last hello is kept: home, drops, the tools
-- it can run, its release. Those were connection state before, so they
-- vanished when the socket did and `$HOME` expanded against whichever client
-- happened to be connected. Now they belong to the machine, so a policy
-- expands the same way whether or not it is online, and an offline machine is
-- an answer rather than a hole.

CREATE TABLE machines (
    account_id   TEXT        NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    -- The identity, within the account. Unique per account, so naming two
    -- computers the same thing makes them one machine to Neuman — which is the
    -- honest reading of a name, and cheaper than a second identifier nobody
    -- types.
    label        TEXT        NOT NULL
        CONSTRAINT machines_label_shaped
        CHECK (label ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    -- How the core reaches it. `client` is a daemon holding the socket. `ssh`
    -- is reserved for a target the client dials and is not spoken yet: the
    -- column exists so the shape does not have to change to add it, and the
    -- CHECK is what keeps a half-implemented kind from being written.
    kind         TEXT        NOT NULL DEFAULT 'client'
        CONSTRAINT machines_kind_known CHECK (kind IN ('client')),
    -- What is installed here, in the operator's words. The model is told this,
    -- because `hello` advertises Neuman *tools* and says nothing about the
    -- programs on PATH — so without it a model discovers a missing toolchain
    -- one refused turn at a time, on every conversation.
    notes        TEXT        NOT NULL DEFAULT ''
        CONSTRAINT machines_notes_bounded CHECK (char_length(notes) <= 2000),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    -- The last hello. Nullable because a machine an operator wrote a row for
    -- has not introduced itself yet; a null home makes `$HOME` name nothing,
    -- which is the same answer as "not connected" and never the wrong one.
    home         TEXT,
    drops        TEXT,
    tools        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    release      TEXT,
    rpc          INTEGER,

    PRIMARY KEY (account_id, label)
);

-- For the administration page's one question about a shared box — "who is on
-- the DGX right now" — which spans accounts by definition.
CREATE INDEX machines_by_label ON machines (label);

-- A branch names the machine its transcript runs on. No foreign key, and the
-- reason is worth stating rather than discovering: the parent would have to be
-- `(account_id, label)` and the account lives two joins away on
-- `agent_sessions`, so a real reference means denormalising the account onto
-- every branch. A branch naming a machine with no row is not an error — it is
-- a machine this account has never connected from, or one whose row was
-- removed, and both read as *offline*, which is the same answer a machine that
-- is merely switched off gets. The alternative is a constraint that refuses to
-- record the very fact the window needs to show.
--
-- Nullable on purpose: branches written before this file have no machine, and
-- the core cannot invent one — it does not know which computer wrote them. Such
-- a branch adopts the machine of its next turn once and is pinned from then on
-- (`Harness`), so a conversation predating this table keeps working and stops
-- being ambiguous the first time it is used.
ALTER TABLE session_branches
    ADD COLUMN machine TEXT
        CONSTRAINT session_branches_machine_shaped
        CHECK (machine ~ '^[a-z0-9][a-z0-9._-]{0,63}$');
