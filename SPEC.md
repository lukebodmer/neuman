# Neuman — architecture

Status: working draft, published as it stands. This is the alignment document for the
client-executor rearchitecture; §8 separates what is built from what is not. When the
design settles, the parts that survive become `docs/architecture.md` and the amended
`docs/security-model.md`.

Markers: **AGREED** = we have decided this.

---

## 1. Vision

Neuman is a governed AI agent. The **brain** — policy, approval, audit, the
harness, and inference — is a service (`neuman-server`). The **hands** — running
shell commands, Python, file tools, git, fetch, and a real browser — are a
program on the user's own machine (`neuman-client`), acting as that user.

The appliance never runs a tool. The client never decides whether a tool may run.
Every proposed call is gated and approved by the server; every executed call runs
on the client, as the human.

The principle, restated:

> **The agent runs as the human it acts for, and does nothing the human has not
> approved.**

This is a genuine weakening of the old guarantee — the agent is no longer
confined *below* the human — and §5 states the cost rather than burying it.

## 2. The two halves

**neuman-server** — decision core + inference. **AGREED**
- Composes policy, gates capability, asks for approval, records audit.
- Hosts the model gateway and the model catalog. Owns the provider credentials.
- Runs the harness — the agent loop and its machinery — and owns the conversation (sessions, context, compaction).
- Owns the tool *catalog* (names, argument schemas, mutating/critical flags).
- Executes nothing. It confines nothing and needs no kernel facility beyond a
  socket and a database.

**neuman-client** — executor + browser + UI. **AGREED**
- Runs the dispatched tool, as the user, in the user's environment.
- Resolves the project's own development environment.
- Hosts a real Chromium (the `browse` tool) and the chat UI.
- Holds no policy authority. It receives commands that were already approved and
  returns results.

**A company hosting a server sees the conversation**, including whatever tool
output is sent back for the next model call. That is the same exposure as a
hosted model provider, already in `security-model.md`'s does-not-cover — the client
executing tools does not change what the brain reads.

**Identity is where this forks.** The implemented story — the person has an
account on the server, the client logs in as them over the session surface, and
the session names them for every dispatch — fits a server that *is* the
organisation's appliance. A server hosting many organisations wants identity
federated to that organisation's provider instead of a POSIX account per person.
That is a second identity mode and a decision to make only if hosted inference is
a product; until then, the appliance model stands.

## 3. Component model

The names the code uses. Every one of these is a module, a directory or a
package in the tree; a name in this list that no reader can find is a bug in
this document.

### Server — the decision core
- **policy** — fragments, composition, baseline + overlay, provenance, the capability gate
- **identity** — accounts, groups, roles, authorization of operators
- **approval** — the consent inbox and `auto_accept` ceilings
- **audit** — the hash-chained record
- **harness** — the agent machinery: `model call → gate → dispatch → observe` plus the prompt, context, projection, reasoning and spill. Keeps its name: the package is more than the loop, and `loop.py` is *the loop*.
- **context** — window management: masking, compaction, the capacity bar's denominator (part of the harness)
- **projection** — re-derives what the model and the reader see from the event log; the reader sees a timeline (D41)
- **model gateway** — the provider client, streaming, usage accounting (D20)
- **model catalog** — declared models and providers (Nix presets + baseline)
- **tool registry** — the manifest catalog the model is told about and the gate checks
- **sessions** — conversation persistence and the event log
- **composition** — the transport surfaces and the wiring that joins the core to them: `http/`, `adminapi/`, the background jobs

### Client — the executor
- **executor** — runs one dispatch and streams its output
- **tool host** — the shipped tool implementations, in `tools/bin` (D5)
- **browser host** — starts Chromium, bridges CDP (`(was neuman-browser)`)
- **UI** — the chat surface, in `web/`
- **environment resolver** — `tools/lib/direnv_env.py`: the project's own dev environment. Read by `python` to get an interpreter as it runs, and by `env_probe` to report the directory's state to the chat window and the system prompt (D33)
- **local state** — the client's own state under `~/.local/share/neuman/client`: the session, the UI's token, the browser profile, spilled output

### Shared
- **contracts** — the schemas and their generated code (one source, both halves)
- **transport** — the client↔server channel: one WebSocket at `/client` on the session surface, session-authenticated, carrying the dispatch RPC
- **scratch** — per-dispatch temp space and oversized-output spill
- **neuman-admin** — the operator CLI
- **the Nix module** — deployment, roles, the catalog presets

## 4. The dispatch contract

**AGREED:** the server mints dispatches; the client executes them.
- Server → client: a **dispatch** — tool name, validated arguments, working
  directory, deadline, and the account's declared surfaces. No profile, no path
  grants. The client is expected to run it as the user.
- Client → server: a **result** — streamed stdout/stderr, exit code, timing,
  truncation flag, and a reference to spilled output.
- Cancellation is a server→client message; the client kills the process tree.
- The RPC is `contracts/schemas/dispatch-rpc.schema.json`, carried by one
  WebSocket at `/client` on the session surface (see §4a).
- **AGREED:** one client per **(account, machine)** — the connection the hub
  holds for a machine owns that machine's calls, and a second connection *for the
  same machine* replaces the first (`ClientHub.register`), so a reconnecting
  socket cannot race the one it replaced. A connection from a different machine
  is a second channel and is kept: the machine is a named place dispatches run,
  and a branch names one (`session_branches.machine`, D112).* There is still no
  field on the wire by which a *client* asks for work another client can see: the
  caller dispatches to a machine, and the hub either has that machine or answers
  `unavailable`. The pre-D112 note read "no `target` field: a field nothing reads
  is forbidden, and the schema is extensible if a fleet ever exists" — the fleet
  arrived, and what it needed was one field, on `dispatch`, read by the client to
  refuse work aimed at another computer.

### 4a. The transport shape

The client holds a WebSocket at `/client` on the session surface — the same port
and the same TLS as the API — and authenticates with the session cookie it got
from `POST /api/login`. One credential for both, so there is no second
authentication path to keep in step, and the account the session resolves to is
the whole of the client's authority.

The `hello` frame names the tools the installation can run and the home directory
on the machine that will run them. It names no account: identity is the login the
host issued rather than a claim the client makes, and the local account that
holds the client process need not be named like the account on the appliance
(D56, D57, D67). The core intersects the tool list with the catalog and the
account's grants.

A peer credential was the other candidate, and it identifies a uid on *one*
machine — which is exactly what the two ends here usually are not. Crossing that
gap needs something relaying for a user, and the identity that survives the
crossing is a session either way. SSH would also add a second configuration
surface (keys, `Match` blocks, forwarding) for a channel that already has one.

## 5. Trust model

**AGREED:**
- The client runs tools as the acting user. File reach is the user's own reach —
  the client OS and whatever ACLs the storage enforces.
- The server enforces *which tools an account may dispatch* (the capability gate)
  and *whether a human approved* (the inbox). That is the whole control.
- The server still owns identity for governance — whose policy, whose budget,
  whose audit entry — but no longer for filesystem reach.
- The client is trusted *relative to the model* — code the model cannot rewrite —
  so the gate and the approval genuinely constrain injected behaviour; it is not
  trusted relative to the user, who can replace it, and must not be described as
  a boundary against them.

**What this costs, stated plainly:**
- **No confinement below the human.** An approved call does what the person could
  do; there is no boundary below them that they cannot cross.
- **Client egress is ungated.** A client-executed `shell`/`python` can reach any
  host the machine can. Exfiltration is an approval problem now, not a network
  problem. This belongs in `security-model.md`'s "does not cover".
- **The audit is reported, not attested.** The chain is still tamper-evident as a
  record; it records a claim from a machine the organisation does not control.
- **The record names the client.** A dispatch entry carries which machine
  executed it: there is one execution site per turn, but several possible
  machines. `tool.dispatch`, `tool.denied` and `job.detached` carry `machine`
  (D112). What that records is a claim from the machine, on a connection the
  login bound to the account — the name is not attested, and cannot be, because
  the two ends are different computers (D67).
- **The client is security-critical**, on the operating system it ships for (Linux
  first).

## 6. Directory structure

```
neuman-server/        decision core + inference (Python), and no execution
  src/neuman_server/
    foundation/       config, database, logging
    decision/         policy, identity, approval, audit, the harness, context
    adapters/         persistence, clients (the channel hub), the runtime host
    composition/      http, adminapi
neuman-client/        per-user executor + browser host + UI, one Python package
  src/neuman_client/
    executor.py       runs one dispatch: streams, bounds, kills the group
    tools.py          the manifest set and the declared-surface check
    session.py        login, and the cookie it keeps
    transport.py      the `/client` WebSocket
    daemon.py         the channel loop
    ui.py             the local UI server and the core's API and WS proxied
    browser/          Chromium + the CDP bridge onto a socket
  web/                the chat surface: static files, no build step
  tests/
neuman-admin/         operator CLI
tools/                manifests + executables, both halves' one source (D3)
contracts/            schemas + codegen
nix/                  module, ISO, VM tests
docs/
```

A directory is top level when it is one of the two halves, the operator's CLI,
or something both halves need. That is why the admin API and the account store
are inside `neuman-server/` — they are the server's authority and cannot be
deployed alone — and why the chat surface is inside `neuman-client/`: the client
serves it and nothing else consumes it. There is no privileged component.

The client is a **Python package**, not a native binary. The tool host, the
browser host and the environment resolver are all Python; a Rust executor would
still need a Python runtime to run the tools, and the client needs a browser
anyway, so "one binary" is not on offer either way. Process control — drain both
streams concurrently, kill the process group on timeout or cancellation — is
behaviour, and it is the part worth testing.

The client serves the UI itself, so the chat surface is same-origin with the
executor and the server is reached only over the channel. The server keeps the
admin API; it serves no static files.

## 7. Modularization — the messes

Largest single files, and the plan for each. **None of these is done** — they are
refactors with no behaviour change, so each is worth doing when that file is
being touched for another reason, not on its own.

| File | Lines at 0.1.0 | Plan |
|---|---|---|
| `neuman-client/web/src/neuman.js` | 9822 | Split into ES modules by concern: `transport`, `transcript`, `toolcards`, `diff`, `sessions`, `settings`, `theme`. No build step required (native modules). |
| `neuman-server/tests/test_http_surface.py` | 4370 | Split by route group. |
| `neuman-server/tests/test_harness.py` | 6390 | Split by loop concern (streaming, approval, compaction, tools). |
| `decision/harness/loop.py` | 2771 | Extract model streaming, tool dispatch, and approval waiting into siblings. |
| `decision/harness/context.py` | 924 | Keep; it is one algorithm. |
| `docs/gotchas.md` | 4376 | Grouped by theme; every entry is a constraint someone would otherwise reintroduce. |

## 8. Where it stands

Built: the **WebSocket session transport**; the client-local UI; one client per
account (a second connection replaces the first, which is what the hub does);
`files_read`/`files_write` as declared surfaces, `$HOME` included, expanding
against the home the *client* reports rather than the one this host can look up
(N145); the **path check in the client tool host** (a guardrail, not a boundary —
`shell`/`python` are not checked); the **Python client daemon**; and the **task
surface**: an account authors a recipe — a workspace, a run root, inputs, an
ordered list of run steps (commands it wrote) and agent steps (turns inside an
envelope), and a schedule. The server owns the clock and walks the steps when
the owner's client is connected; each agent step's envelope is intersected with
live policy on every step, no agent step may write what a run step runs, and
each run leaves its record in its own directory (D92–D97). A task is not a
principal and does not need a second account or a second client.

Settled: **login is an argon2id check against Neuman's own account row** (D58),
and the account row is the identity (D16, D68). A client key would bring back
the enrollment ceremony the transport decision deleted; an organisation that
wants its directory to own identity gets OIDC or LDAP, not PAM.

**Nothing else is in flight.** What follows is what is *not* built; none of it blocks
the architecture above, and each is here so that leaving it out is a decision
rather than an omission.

Not built, in the order worth doing:

1. **Client packaging beyond Linux.** A bundled Python and a browser-profile
   story per platform; not worth designing until one is asked for.
2. **Tool distribution beyond the bundle** — the org bundles and excludes, the
   user installs more. Exclusion is the per-role grant already; a server-level
   deny and a manifest format for a user-installed tool are not designed.
