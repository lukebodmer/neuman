# neuman-server

The decision core. A single unprivileged Python process that owns the
database and decides everything that may happen: who is logged in, what
policy applies, which tools may run, what the model sees and does, and
what lands in the audit chain. It executes no user-reach tool, it never runs as
root, and it has no privileged helper: passwords are argon2id hashes checked in
this process.

## Public surface

What other parts consume:

- **HTTP/WS API** for the client and its chat UI (TLS; session cookie +
  CSRF/Origin re-checked on every unsafe request).
- **Admin API** on a local Unix socket for `neuman-admin` (kernel peer
  credentials, then the peer's name against the `neuman-admin` group in the
  account store), and the same operations under `/api/admin` for the web panel
  (session + administrator group).
- **The client channel** — the `/client` WebSocket the dispatch RPC rides.
  Dispatches go out approved and gated; whatever comes back is a report from a
  machine this process does not control (`docs/security-model.md`).
- **Ownership** of the core database, the runtime policy overlay, and the
  audit chain. Nothing else writes any of these.

## Private surface

Everything else: DB schema and migrations, policy fragment composition,
baseline/overlay merge, harness internals, login throttling state.

## Layout

Every directory and file, one line each.

```
src/neuman_server/         — the Python package: the decision core
  __init__.py              — package marker; carries the version
  __main__.py              — entry point: settings, the two listeners, shutdown

  composition/             — wiring and transport; sees everything, decides nothing
    app.py                 — app factory, lifespan, startup ordering, health
    runtime.py             — writes what the runtime host should run, from the baseline (D78)
    http/                  — the session surface: REST + WS over TLS, cookies
      auth.py              — login/logout: resolves a principal through identity
      browse.py            — descending a granted subtree, for the chooser
      client.py            — the `/client` channel: the dispatch RPC's WebSocket
      deps.py              — principal resolution and the CSRF check, in one place
      middleware.py        — request correlation, CSRF/Origin checks, security headers
      models.py            — the models-and-GPU panel: state, load and unload, for operators
      policy.py            — a user reading their own effective policy
      sessions.py          — a user's own agent sessions: list, open, delete
      ws.py                — WebSocket bridge: streams harness events, feeds the inbox
    serve.py               — both listeners: the network port and the admin socket
    adminapi/              — the operator surface: Unix socket, peer credentials
      deps.py              — the peer's identity, from SO_PEERCRED and pwd
      peercred.py          — capturing that uid: uvicorn puts no socket in the scope
      guard.py             — peer *and* operator, for the whole /admin prefix (N27, N32)
      accounts.py          — create/disable/password/groups, and the first-admin bootstrap
      settings.py          — the runtime baseline, prompt and events
      catalog.py           — the presets as tiles: add a provider with its key, a model, a local runtime (D76, D77)
      policy.py            — policy authoring calls (delegates to decision/policy ops)
      sessions.py          — reading any account's conversations, for operators
      audit.py             — audit search and export for operators
      system.py            — backup, restore, service health (delegates to decision/ops)
      gateway.py           — usage accounting; what each catalog name resolves to, read-only
    jobs/                  — background tasks; process-level, decide nothing
      retention.py         — scheduled purge of expired sessions/audit per retention rules
      audit_index.py       — refresh of the derived audit search index
      session_index.py     — repair of the derived session search index (D52)
      watchers.py          — file watchers: notification, never polling (D14)
      settle.py            — sweep of stale budget reservations

  decision/                — the product's judgment, by domain
    identity/              — who you are and what you may operate
      directory.py         — the group-directory port; policy depends on it too
      ports.py             — PasswordHasher and identity's repo ports
      sessions.py          — login, session lifecycle: create, validate, revoke
      tokens.py            — token issue/validate/rotate; CSRF token pairing
      throttle.py          — login throttling (sliding-window, in-process)
      authz.py             — operator authorization guard, checked inside operations
    policy/                — what a user may do, composed from layered fragments
      ports.py             — policy's repo ports (fragments, generations)
      fragments.py         — the fragment model and its validation
      source.py            — baseline + overlay layered load and merge (one owner)
      compose.py           — fragment composition → the effective policy
      gate.py              — the capability gate: a tool's needs ⊆ the user's reach
      roles.py             — role-name validation and the pure baseline rewrite
      grants.py            — group-keyed grants resolved through IdentityDirectory
      generations.py       — revocation counters; policy is re-read per dispatch
      paths.py             — expanding `~` and `$HOME` against the account's home
      ops.py               — authoring operations: guarded, audited, generation-bumped
    harness/               — the agent loop and its state
      ports.py             — ModelGateway, ToolExecution, event-log repo ports
      loop.py              — the agent loop: model call → tool dispatch → observe
      inbox.py             — steering/approval inbox; decisions arrive as messages (D12)
      events.py            — the append-only session event log (replayable history)
      projection.py        — re-derives model-visible history from events
      reasoning.py         — the three reasoning dialects, asked for and replayed
      spill.py             — bounded output, spilled beside the client that produced it (D42)
      catalog.py           — resolves a granted model name to where it sends
      presets.py           — what a preset means for an entry: build, check, refresh (D76)
      context.py           — compaction when history outgrows the window
      pricing.py           — what a turn cost, from the provider's usage numbers
      prompt.py            — system prompt and tool-advertisement rendering
    audit/                 — the governance record
      ports.py             — audit's repo port (chain rows)
      chain.py             — hash-chained append and verification
      search.py            — derived, rebuildable search index over the chain
      retention.py         — retention and purge rules for logs and chain
    ops/                   — irreducibly cross-domain operations only
      backup.py            — backup orchestration: pg_dump + audit mirror + overlay
      restore.py           — the other half: verify the chain, then load it back
      sessions.py          — operator reads and removals over any account's sessions

  adapters/                — the only place outside-world knowledge lives
    directory.py           — group membership from the database
    passwords.py           — argon2id hashing and verification
    clients.py             — the client channel hub: connections, dispatch, sequence gaps
    gateway.py             — thin OpenAI-compatible client (D20)
    gpu.py                 — the sampler's status file, read not polled
    runtime.py             — the runtime host's and the fetcher's control sockets (D78, D79)
    registry.py            — tool-manifest loader and validator
    weights.py             — whether a preset's weights are in `modelsDir`, by size
    persistence/           — the only place SQL is written; mirrors decision domains
      identity.py          — accounts (D16/D17) and login sessions
      policy.py            — group-keyed grants and generation counters
      harness.py           — the session event log and token accounting
      sessions.py          — agent sessions; every read is scoped by account
      audit.py             — the chain and its derived search view

  foundation/              — dumb shared services; no opinion, no business logic
    config.py              — environment → Settings
    logging.py             — the one canonical log record (schema in contracts/)
    db/                    — the database, owned here and nowhere else
      pool.py              — the connection pool every loop borrows from
      migrate.py           — the migration runner; nothing else runs DDL
      migrations/          — plain SQL, one transaction each (Postgres DDL is transactional)
    state/                 — file-backed state, deliberately not in the database (D18)
      overlay.py           — atomic read/write of the runtime policy overlay
      presets.py           — loads and validates the presets the server ships
      secrets.py           — provider keys: written 0600, swept when nothing names them (D77)

  presets/                 — the providers and models Neuman supports, one JSON file each (D76)
  runtime/                 — the two units beside the server; standard library only
    plan.py                — the server's document checked against the presets, and the router's INI
    host.py                — `neuman-runtime`: the chat router and a control socket
    fetch.py               — `neuman-fetch`: pinned downloads into modelsDir, verified

tests/                     — part-level unit tests
  fakes.py                 — an in-memory implementation of every port
  test_layering.py         — the D7 contracts, checked mechanically (D24)
```

Each layer directory carries a `README.md` stating its role and allowed
imports. The layering and the names above are the commitment. Each
decision domain owns its own tables, ports, and events — domains compose
through each other's public operations, never each other's internals.

## Testing

```
nix build .#checks.x86_64-linux.server-test    # the unit suite
nix build .#checks.x86_64-linux.python-lint    # ruff lint + format (D22)
```

Every port has a fake, so the whole suite runs without a database, a connected
client, or a model. That is the layering paying for
itself: the things that are slow and stateful are on the far side of a
Protocol.

Two kinds of test, deliberately distinguished:

- **Part-level**, against the fakes in `tests/fakes.py` — the decision
  layer's rules, checked in isolation.
- **Surface-level**, against the real ASGI app in `tests/surface.py` —
  `test_http_surface.py` and `test_ws_surface.py` stand up real routes,
  real middleware, and the real dependency plumbing, replacing only the
  outside world. They exist because CSRF validation, Origin checking,
  cookie flags, session scoping, and the one-turn-per-session rule live
  in `composition/` and nowhere else, so a test that stops at the
  decision layer cannot see them. The WS tests bound every receive: a
  blocked receive loop would drop mid-turn messages (D12), and that must
  fail by name rather than by hanging.

What these tests do **not** cover, and must not be mistaken for: any SQL, and
any real counterpart. The persistence stores are substituted, so the schema and
its queries are exercised only by the VM tests, against a real Postgres; the
client hub is driven through a fake channel, so a dispatch crossing a real
WebSocket is proved in `vm-iso`. Those VM tests are the release gate:
`nix flake check` runs them.

## Dependency rule

Enforced by an import linter in CI (see `docs/decisions.md` D7), not by review:

```
foundation  → (nothing internal)
decision    → foundation            (declares ports)
adapters    → decision, foundation  (implement decision's ports)
composition → all of the above      (wires them; nothing imports it)
```

The decision layer never imports an adapter: it declares a port (a
`Protocol`), an adapter implements it, and composition injects the
implementation. That is what makes decision logic testable with fakes and
keeps provider APIs, sockets, and transports out of the decision path.