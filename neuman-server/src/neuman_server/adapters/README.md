# adapters

Port implementations, each the only place its outside-world knowledge
lives:

- **directory** — the `IdentityDirectory` port over Postgres: the groups an
  account is in, read from `groups`/`group_members`. A grant lands on the next
  policy read, and a disabled account loses every role immediately.
- **passwords** — argon2id hashing and verification, on a worker thread so a
  login does not block the event loop. The hash is a column on the account row;
  there is no other component to ask.
- **clients** — the client hub: one connected daemon per account, the
  dispatches riding on it, and the sequence check that makes a dropped
  chunk distinguishable from a chunk never sent.
- **gateway** — the model gateway; a thin OpenAI-compatible client. The
  only code aware of provider APIs — Neuman owns keys, budgets, the model
  catalog, and usage accounting in its own database (see `docs/decisions.md` D20).
- **registry** — the tool-manifest loader and validator.
- **gpu** — the sampler's status file.
- **runtime** — the runtime host's and the fetcher's control sockets (D78, D79).
- **persistence** — repository implementations over the pool: one module per
  decision domain (`identity.py`, `policy.py`, `harness.py`, `audit.py`),
  each implementing exactly that domain's repository ports. The only place
  SQL is written.

Each adapter implements a port declared in `decision/` and is only ever
called through it.

Allowed imports: decision (port definitions and types), foundation.
Never imported by decision.
