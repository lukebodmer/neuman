# Working in this repository

Neuman is a self-hosted appliance that gives an organisation an AI agent with
real filesystem and shell capability, confined so it can never reach further
than the human it acts for. `README.md` is the front door; `docs/security-model.md`
states the security model and what enforces it.

## The rule that overrides tidiness: this is pre-release

Neuman has not shipped a first version. Until it has:

- **There is no backwards compatibility to preserve.** Do not add migration
  shims, deprecation paths, compatibility branches, config aliases, or `# legacy`
  comments. If an old way of doing something is wrong, delete it and rewrite the
  callers. A half-removed feature is worse than either state, because the next
  reader cannot tell which half is load-bearing.
- **The tree must read as if it were written this way.** No comment may explain a
  design that is no longer in the code. No dead field, no unused import, no
  helper kept "just in case", no doc line describing a removed mechanism. When a
  concept is removed, remove its name from the repository — the only place it may
  survive is `docs/gotchas.md`, as the record of why it failed.
- **A schema change is a rewrite, not a migration.** `contracts/` is generated;
  edit the schema and regenerate (`contracts/codegen`). The database schema is
  one file, `neuman-server/.../migrations/0001_initial.sql`: change it in place,
  and an appliance that already applied it refuses to start (its SHA-256 is
  recorded) until it is started over (`docs/setup.md`). At the first release the
  file freezes, and from then on a change is a new migration and an applied one
  is immutable.
- **Tests are not exempt.** A test whose fixture describes a removed feature is
  cruft; rewrite it against the current design or delete it. Do not keep a test
  green by asserting the absence of something that no longer exists.

The bar is: a maintainer reading the tree after the first release should find a
coherent design with no archaeology except where the archaeology is the point.

## Documentation is part of the code

The docs carry the reasoning, and each has a job:

- **`docs/security-model.md`** — the security model, what enforces each part of it, and
  where its boundaries are, including the threat model. Claims in the table are
  load-bearing; if a change makes one false, fix the claim or the change, and add
  a footnote for any exception.
- **`docs/networking.md`** — every socket, port, tunnel and outbound path, and
  what enforces each. Update it when a link is added, moved, or its protection
  changes.
- **`docs/decisions.md`** — why the system is built this way rather than
  another way (D-numbers). A change that contradicts a decision must argue with
  its reasoning and amend it, not quietly ignore it. Add an entry when a design
  choice is made that a future reader would otherwise rediscover.
- **`docs/gotchas.md`** — every way this has actually failed, with the fix
  (N-numbers). This is the *only* place a removed mechanism may be named, and
  only when the failure it caused still informs the current design. Entries that
  no longer teach anything should be deleted.

When you change behaviour, update the docs it touches or say why one does not
apply. Stale documentation is a bug: it is how a proven property gets silently
dropped.

## Conventions

- **Python**: `ruff` lints and formats every part (line length 96). Generated
  contract code is excluded. Decisions are lowercase-hyphenated filenames under
  `docs/`; no other prose is normative.
- **Nix**: `nixfmt`. The appliance is NixOS; there are no imperative installs.
- **Contracts**: schemas are the source, `contracts/gen/` is output. Run
  `python3 -m codegen generate` after editing a schema, then
  `python3 -m codegen generate --check` and `python3 -m codegen validate`.
- **Comments** explain *why*, and cite the decision/gotcha that constrains the
  choice. A comment that restates the code is noise; a comment that justifies an
  unusual choice is the point.
- **A restriction names its cost.** A comment justifying a restriction also says
  what it costs the person using Neuman. Stating only the reason makes a
  tradeoff read as a pure gain, and a reader can no longer tell a deliberate
  trade from an unexamined default.

## Checking a change

```bash
# server tests
( cd neuman-server && python3 -m pytest tests -q )

# mechanical style, and the contract triad
nix build .#checks.x86_64-linux.python-lint \
          .#checks.x86_64-linux.nix-lint

# the parts
nix build .#neuman-server .#neuman-admin .#neuman-client .#neuman-tools .#neuman-registry
```

`nix flake check` runs the whole matrix including the VM tests; it is slow and
worth it before a commit that touches policy, the harness, or the module.

## What this file is for

This is the standing instruction set for anyone — human or agent — editing the
repository. It exists so the pre-release cleanliness rule survives past the
session that adopted it. Delete it at the first release, when the rule stops
being true.
