# Decisions, and the ground they rest on

Why Neuman is built this way rather than another way: the design principles
every decision is argued from, the decisions the code cites by number, and the
platform facts those decisions assume.

`docs/manifesto.md` says what Neuman believes. `docs/security-model.md` states
the security model and what enforces it. `docs/gotchas.md` records every way
this has been observed to fail.

Nothing here is a plan. Each entry is a decision already taken, with the
reasoning that made it, so a change that contradicts one has to argue with the
reasoning rather than rediscover it.

## Design principles

The manifesto's laws, made precise enough to decide a design question with.

1. **Linux is the foundation.** Use its users, groups, modes, units and
   conventions rather than reinventing them.
2. **The agent runs as the human it acts for, and does nothing the human has
   not approved.** Tools run on the person's own machine, under their own
   account; the appliance decides and never reaches into anyone's files.
3. **Consent enforces action, not the model.** A model asked to behave well
   behaves well until it is confused, convinced, or simply wrong.
4. **Reach and tools are scoped per person, and precisely.**
5. **Reach is generous, and anything irreversible stops for a verdict.**
   Because the agent runs as the person, wider reach exposes nothing they could
   not already reach. Grant what the work needs, and never restrict without
   naming what is lost.
6. **Security and usefulness are both requirements.** A deployment that leaks
   is a failure, and so is one nobody can get work done in.
7. **Neuman knows what it is and what it may reach, and nothing beyond.** What
   it is told must be true: a prompt claiming reach that policy denies costs a
   turn and the model's trust in its own instructions.
8. **Secrets are paths, never values.**
9. **The record is the product, not a diagnostic.** It is what lets someone who
   was not there check the claim, which is why an unrecorded action is a worse
   defect than a crash.
10. **Reasoning, tool calls, sessions and logs are preserved in full**, and
    history is derived from that record rather than held alongside it.
11. **Admin is a group in Neuman's own account store, `neuman-admin`.** It lets
    a person read the whole record, across accounts; everyone else reads their
    own, and an admin reading someone else's is itself recorded.
12. **The configuration is a document.** An operator can export it, review it
    and keep it in git.
13. **Prefer the smaller design.** The privilege the obvious implementation
    wants is usually privilege the feature does not need.
14. **Absence of failure is not evidence.** A check must be seen failing before
    its passing means anything.
15. **Built for organisations that have never met its authors.** A default that
    only suits the person who wrote it is a defect.
16. **Models are not interchangeable.** Each has its own window, limits and
    needs. A capability is stated per model, as a reviewed fact, because a claim
    about a model is only falsified by spending a turn on it.

## Decisions

Final until explicitly reversed here, with a new entry.

- **D1 — Tier split.** Parts as in §2. The decision core and the model
  gateway are the server; the executor, the browser host and the UI are the
  client, on the acting human's own machine. The server has no privileged
  component. Retrieval/search indexes are derived data: rebuildable, excluded
  from backups, and out of the core.
- **D2 — Contracts are data.** `contracts/` holds schemas, codegen, and
  fixtures only. Both the server and the client validate against the same
  schema; no hardcoded mirror lists in either.
- **D3 — The client's tool knowledge is local and immutable.** The client
  loads tool manifests from its own installation at startup, never from
  anything the server supplies at dispatch time — a dispatch carries a name,
  and the client resolves it, so a compromised server cannot turn the client
  into a general program runner. Server and client hold two installations of
  one contract; CI proves the sets match.
  *Held by D101:* a tool package enabled on the server chooses among tools
  every client already ships; it never sends one.
- **D5 — Every dispatchable name is a manifest.** The model can only ever
  call a tool that has one. A generic-binary dispatch path in the effect
  plane, if retained at all, is an internal mechanism, never advertised.
- **D7 — Layering is a checked rule.** Inside `neuman-server`:
  `foundation` imports nothing internal; `decision` imports only
  `foundation` and declares ports; `adapters` implement those ports and
  import `decision` and `foundation`; `composition` imports everything
  and wires. Enforced by an import linter in CI, not by review.
- **D8 — Audit is a core concern.** Governance events are emitted by core
  operations, never by transport surfaces; a surface cannot bypass the
  audit chain. Search and retention are core requirements, not
  follow-ups: an indexed, rebuildable derived view over the chain, and a
  retention policy.
- **D9 — Two surfaces, one core.** Admin operates through a local admin
  API (Unix socket, peer credentials). The HTTP API and the admin socket
  API are both thin adapters over the same core operations — no
  duplicated admin logic.
- **D10 — The log record is a contract, not a format string.** Records are
  checked against `contracts/schemas/log-record.schema.json`, with golden
  fixtures beside it, so a field is named once. The decision core emits it; the
  client's daemon logs in its own words to the user's own journal and is
  deliberately not held to it. Semantic equivalence, where there is more than
  one emitter, is the standard — never byte-for-byte.
- **D11 — The client is a separate derivation.** The client's package carries
  the UI and the tools, and the client serves the UI itself; the untrusted
  surface never enters the TCB build graph, and the core serves no static files.
- **D12 — The harness assumes an inbox.** The loop is built around a
  steering/approval inbox from day one: decisions (approvals, interrupts)
  arrive as messages; the default is fail-closed. Retrofitting
  concurrency later is the expensive direction.
- **D13 — Manifest kinds:** `client` (run on the acting human's machine by
  the client) and `server-native` (intercepted by the core before any
  dispatch, e.g. querying one's own sessions). The kind must be true of where
  the tool actually executes; there is no third place (SPEC section 2).
- **D14 — Notifications, not polls.** Wherever state crosses a process
  boundary, the receiver is notified (file watch or signal), never a
  polling loop.
- **D15 — Fail fast on silent stalls.** A dispatch that never reports
  `started` within a few seconds is an error, independent of the tool
  timeout.
- **D16 — Neuman owns accounts.** The host account stopped being the executor
  when tools moved to the client (D55), and the deployments this product is
  for — an individual, a family, a small company — have no directory to
  observe. An account is a row: a name, an argon2id hash, a disabled flag and
  a set of Neuman groups. The host keeps exactly one identity job: the local
  operator who can open the admin socket. Grants are keyed to Neuman groups,
  and policy for a name with no row is inert.
- **D17 — Account state fails closed.** The row is re-read on every request and
  every policy step. A disabled account, or a deleted one, invalidates live
  sessions and loses its groups on the next request; there is no name→uid
  binding to drift because there is no host uid in the identity.
- **D18 — Postgres-only storage for the decision core.** The appliance
  runs its own cluster; the core connects as the `neuman` role with peer
  authentication over the local Unix socket, through a pool owned by
  `foundation`. Decision code never writes SQL — it goes through
  repository interfaces, which exist for testability, not to promise a
  second engine. The audit JSONL mirror on disk remains the durable
  tamper-evident artifact. The decision core's database is the *only*
  database on the appliance; the other stores are files by design — the
  policy overlay must survive a wiped database (it is the runtime-authoring
  surface), the audit mirror is the integrity anchor, and a provider's key is
  a file so that no row, dump or backup carries one (D77). Nothing else
  migrates into Postgres without a decision reversing this entry.
- **D19 — Scaling model.** An appliance is the complete, independent TCB for
  one organisation: its own database, policy, audit chain, model catalog and
  admin. Organizations scale by running one appliance each rather than by
  sharding one. Per appliance, target a few thousand accounts and hundreds of
  concurrent sessions; the process model (async harness, per D12) and the
  database must never be the reason to split. Cross-appliance aggregation, if
  ever needed, is derived data above the appliances and never a reason to
  widen one appliance's claim.
- **D20 — Neuman owns the model gateway.** No LiteLLM or any proxy
  framework. The gateway adapter is a thin OpenAI-compatible client, and
  keys, budgets, the model catalog, and usage accounting are Neuman's own
  tables in our own database, enforced by our own code. Multi-provider
  routing is configuration (endpoints + models), not a bundled service.
  One heavyweight runtime dependency and its operational fragility are
  gone; nothing crosses a part boundary that we don't own.
- **D22 — Style is enforced, not preferred.** `ruff` lints and formats
  every Python part (per-part config; generated code excluded), and the
  checks run in `nix flake check` alongside the contracts triad — the
  same mechanical-enforcement rule as the layering (D7), applied to
  style. Formatting churn is paid once, per file, at birth.
- **D23 — Prompt injection cannot escalate; it can only act in-reach.**
  The agent's whole purpose is reading files other people wrote, so
  injected instructions are a *routine input*, not an anomaly. Containment
  is structural, not textual: an injected instruction can at most cause the
  model to propose a tool call, and every proposed call is re-gated against
  the acting user's **live** policy. What injection can still do is in-reach
  damage — the user's own files with the user's own rights — and approval is
  the control that answers it: `mutating` stops for a human under the default
  `safe`, and `critical` stops even under `yolo`. The old second bound — the
  kernel refusing a path outside a profile — went with the sandbox. The
  declared-surface check replaces it for the structured tools and does not
  cover `shell` or `python`. The delimiters and the system-prompt warning
  (`decision/harness/prompt.py`) reduce the rate; they are not the boundary
  and must never be described as one.
- **D24 — The layering check is ours, not a dependency.** `import-linter`
  is not in nixpkgs, so the D7 contracts are enforced by an AST walk in
  `neuman-server/tests/test_layering.py`: layers point inward, decision
  imports no transport, decision domains stay independent, nothing imports
  composition. Each intended coupling is *named* in that file with its
  reason rather than waived. Same reasoning as the hand-written schema
  emitter: when the rule is specific, a purpose-built check is
  shorter than the configuration to make a general tool express it.

- **D25 — `mutating` is per-tool today, and that is a known approximation.**
  The approval flags are properties of a *tool*, but for a general tool they
  are properties of a *call*: `shell` running `ls` mutates nothing and `shell`
  running `tee` does, and one manifest cannot say both. Writing the first real
  tool set (`tools/`) made this concrete rather than theoretical. Until it is
  fixed, every such tool declares the **fail-closed** value — `shell` is
  `mutating = true`, so it asks under the default `auto_accept = "safe"` — on
  the reasoning that over-prompting is an annoyance while under-prompting
  silently skips consent for the whole class of in-reach damage D23 names as
  the residual risk. The eventual fix is per-call classification: a predicate
  over the parsed arguments, evaluated at gate time beside `needs ⊆ reach`, so
  `ls` and `rm -rf` are different decisions. It needs a `tool-manifest` schema
  field and must be designed against a real dispatch — a predicate language
  written before there is a caller to observe would be guessing at what it can
  see. Deliberately not fixed now; deliberately recorded so the
  approximation is not mistaken for the design.
- **D26 — The runner stream is gap-checked; byte counts are advisory.** Two
  questions the wire contract left open, settled before M2 so the Rust has one
  answer to implement rather than two to reconcile. **`sequence` stays
  required on every streamed message and the reader enforces it**: a gap is the
  only way to distinguish a dropped chunk from a chunk never sent, and the
  difference is truncated output presented to a model as complete — so a gap
  fails the dispatch rather than degrading it. Requiring a field no reader
  checked would have been ceremony; the check landed with the decision.
  **`bytes_out`/`bytes_err` become optional**, because the reader counts what
  it actually received and a second count can only confirm or contradict it;
  they stay in the schema as a diagnostic. The related profile invariant —
  every `write` path also appearing in `read` — is *not* expressible as data
  (a subset relation over two instance arrays needs an `if`, which boundary
  rule 4 forbids in `contracts/`), so it is stated in the schema's description
  and enforced on both sides: the server asserts it where it builds the
  profile, and the runner must re-check on receipt and refuse rather than
  silently add the missing read.

- **D28 — Vision is a tool, and the harness lifts its output into the request.**
  A model that accepts images cannot be *given* one through the tool channel:
  a tool returns text, and the runner decodes its stdout as UTF-8, so raw bytes
  arrive mangled. Three designs were possible — a flag on `fs_read`, a
  multimodal content path through the whole harness, or a dedicated tool whose
  output the harness intercepts. The third is what exists.
  `fs_image` returns `media_type base64data` on one line. The harness recognises
  any tool whose manifest declares `returns_image` — `fs_image` and `blender_view`
  today (D100) — rather than one name, stores the bytes once in the
  content-addressed image table (`session_images`), records the hash and the
  source path (none, for a live viewport) in an `image` field on the
  `tool_result` event, and replaces the model-visible content with one sentence. `project()` then emits **two** messages: the `tool` reply the
  protocol requires for every `tool_calls` entry, and a following `user` message
  carrying an image *reference* — a hash and a path — because the OpenAI format
  accepts an image only on a `user` message, and providers reject it elsewhere.
  `resolve_images` turns that reference into the `image_url` content part on the
  wire copy alone, so neither the log nor the derived history carries the
  picture; a compaction snapshot and the audit copy of a request both refer to
  it by hash (amended by N148, which found the bytes being copied into every
  snapshot and every turn).
  Why a separate tool rather than a `fs_read` flag: the gate can then grant them
  separately. Looking at an image costs real tokens in a way reading a file does
  not, so an operator can allow reads without allowing vision. And a text-only
  model refuses the call *before* dispatch, using the `vision` field an operator
  declares on the model — sending an image part to a
  text-only model is a provider error after the tokens are spent, in provider
  dialect the model cannot act on.
  The base64 never enters the emitted WebSocket frame: the frame carries
  `has_image`, and the browser fetches the bytes on demand from
  `GET /api/branches/{id}/tools/{call_id}/image`, which looks the hash up in the
  log and the bytes up in the store. Megabytes over the socket would stall every
  frame and every reopen for a picture that is usually scrolled past; a request
  made only when a card is looked at costs one fetch instead. The picture lives
  in exactly one place, the store, and every event that refers to it — the tool
  result, a compaction snapshot, the audit copy of a request — refers by hash
  (N148).

- **D29 — The GPU stays out of the server.**
  The server runs with `PrivateDevices=yes` and has no GPU, and it keeps it that
  way however a model comes to be run. The obvious implementation of anything
  that needs the device — shelling out to a runtime from the server — would mean
  giving the browser surface a GPU. So a separate unit holds the device,
  `PrivateDevices=false` appears in exactly one place, and the server remains a
  socket client. That unit is the runtime host (D78); verified from the built
  units, `neuman-server` keeps `PrivateDevices=true` while `neuman-runtime` does
  not.

- **D30 — GPU state is sampled by a separate unit; the server never sees a device.**
  *Amended by D78:* the sampler stays. There is no `models.json`, port probe or
  polkit rule: what is loaded is the runtime host's status, and loading and
  unloading are requests to it that name a model, never a unit.
  The model panel needs three things an operator asks at once — what this appliance
  serves, what is resident, and how much room is left — and the obvious
  implementation gives the server `PrivateDevices=no` plus `systemctl`. That trades
  the appliance's central hardening for a status bar, so none of it happens.
  Instead: a oneshot unit runs `nvidia-smi` on a five-second timer and writes
  `/run/neuman/gpu.json`; the module writes `/etc/neuman/models.json` describing
  what it declared, because the server cannot evaluate Nix; and liveness is a TCP
  probe of each model's *internal* port, which answers only while the runtime is up
  where the public port always answers because systemd holds it. The whole interface
  is two files and a connect, so the worst a compromise of the sampler yields is a
  lie about how full the GPU is.
  Loading needs no privilege at all — connecting to a held socket is what triggers
  activation — so only unloading is privileged, via a polkit rule scoped to the
  units this module generates. The unit name to stop comes from the manifest and
  never from the request, which is a second independent reason
  `/api/models/neuman-server/unload` cannot mean anything.
  The panel is operator-only including its reads: GPU capacity and the model
  inventory are appliance topology, and an ordinary user selects a name from what
  policy grants them. This is the first use of `authorize()` from the browser
  surface — every operator gate before it was on the admin socket, where peer
  credentials did half the work.

- **D31 — Browsing is a client-owned browser, and there is no headless fallback.**
  Three decisions, the first forced by measurement.
  **No headless.** Against live sites: headless Chromium with its default
  user-agent returned *zero* results from DuckDuckGo's HTML endpoint and eight block
  indicators from Google. A real user-agent fixed DuckDuckGo (107 results) and not
  Google or Reddit. So headless works per-site, unpredictably — which is worse than
  not offering it, because it fails after someone has come to rely on it. `curl`
  with the same user-agent returned zero, so a fetch-and-parse tool is *strictly
  worse* than a browser; `web_fetch` remains for the things that are reliably
  fetchable.
  **The browser lives with the user.** The appliance has no display and never
  starts a browser. `neuman-browser` runs on the acting user's machine, starts a
  real Chromium there, and exposes its CDP endpoint on a Unix socket the
  `browse` tool dials — both run on that machine.
  This is what removes the old `_session_environment`, `_start`, profile rsync and
  `--new-window` machinery: a system service reaching into a desktop session was
  the architecture being papered over. It also makes a headless appliance honest —
  the server needs no display because it never had a browser to put on one.
  **The profile is chosen on the client.** `own` (the default) is a profile
  belonging to Neuman; `copy` rsyncs the user's real profile in. The server has no
  profile option: the cookies in force are whatever browser the user pointed
  `neuman-browser` at, and the dangerous choice is opt-in where the browser is.
  **It is an ordinary `client` tool.** Its cost — model-chosen JavaScript in a
  browser whose cookies the client chose, on the user's own network — is what
  every tool costs now, which is why `docs/security-model.md` says so at the top. What
  it does not cost: the binary comes from the client's own trusted directory,
  `needs ⊆ reach` is still re-checked, and the tool is `mutating` so it asks.
  **CDP without a dependency.** The client is hand-written against the standard
  library. `websockets` is declared for the server and not for the tools, and a
  library present in one place and missing in another is N47 — which shipped a
  feature that failed only at runtime. A test asserts the client imports nothing
  outside the standard library.

- **D32 — The model panel is where models are chosen and loaded.**
  *Amended by D78:* loading and unloading are requests to the runtime host.
  Two controls had grown up in two places: a chat-model dropdown in the header and
  load/unload buttons in the panel. They are one question — what is running — so they
  are now one surface, mirroring the context bar at the bottom that answers "what can
  the model see".
  **A row says what a model is.** "deepseek or qwen" told a user nothing; a row now
  carries local-or-cloud (derived from the endpoint, so a stored column cannot disagree
  with it), vision, and cost per million tokens in and out — from catalog columns that
  had existed since the first migration and that nothing had ever read.
  **Policy is still the ceiling and privilege is still split.** The panel offers only
  what `policy.models` grants, enforced twice: the route iterates the granted names and
  `describe()` is scoped by `WHERE name = ANY(%s)`. Load and unload remain
  operator-only because they are appliance state — but *choosing* among granted models
  is not, so a non-operator keeps the dropdown rather than losing the ability to pick a
  model at all.

- **D33 — A development environment is a lookup, not a privilege.**
  Neuman kept reaching for Python and finding nothing, and the obvious reading —
  "there is no interpreter" — was wrong. Every entry of a realised flake
  environment's PATH is under `/nix/store`, and the interpreter was there all
  along. What was missing was not reach but a *name*: nothing told a dispatch
  which interpreter and which `PYTHONPATH` a directory meant. So the tool adds no
  capability, which is the property worth stating — it is the smaller design
  again, for the fourth time.
  **Parse the cache; never evaluate it.** `nix-direnv` has already realised the
  flake and left the result under `.direnv/` as declarative text, so resolving an
  environment needs no evaluator, no network, and no store write. Two alternatives
  were rejected and both are strictly worse. Running `nix develop` would need
  network and store-write and hand a dispatch the ability to build arbitrary
  derivations — a real escalation. *Sourcing* the rc would execute text found
  inside the working directory, turning "read this repository" into "run this
  repository", which inverts D23: injected text may propose work, never widen
  reach. The parser is therefore deliberately narrower than shell syntax — it
  accepts only `NAME='single-quoted'`, carries an allowlist of six variables
  (never `shellHook`, which is code), and requires every path entry to be under
  `/nix/store`. That last check is what makes "this widens nothing" structural
  rather than hopeful: an entry outside the store could not have been executed
  anyway, so refusing turns a confusing mid-script `Permission denied` into a
  named answer.
  **A client tool, like every other one.** It runs on the acting human's machine,
  as that human, with their environment and their network — so `pip install` and
  `urllib` work here, unlike under the old sandbox where egress collapsed to
  `mode: none`. The description says so rather than leaving it to be discovered.
  **It refuses a project, and falls back in a folder.** Running against a bare
  store interpreter when a *declared* environment is unbuilt was the wrong
  kindness: a script that silently runs without the project's packages fails as a
  `ModuleNotFoundError` deep in its output, which reads as a broken tool rather
  than as an environment nobody built. But refusing *every* directory without a
  `.direnv` — which the first version did — was worse, because most directories
  are not projects: they hold the data a script is pointed at, and the answer
  "ask the user to run `direnv allow`" named a `.envrc` that does not exist and
  never will (N85). So a `.envrc`/`flake.nix`/`shell.nix`/`default.nix` marks a
  directory as intending an environment, and only those refuse. Every refusal is
  named — `env_unrealised`, `env_stale`, `env_collected`, `env_untrusted` — because
  a model can act on a name and not on prose, and a test requires every reason the
  resolver can raise to have a sentence of advice in the tool.
  **What running it found that no fixture would have.** `env_collected` exists
  because the first real directory tried had a 135 KB rc file listing
  seventy-two store paths, a dangling GC root, and not one of those paths on
  disk — the ordinary state of any project not entered since the last
  `nix-collect-garbage`. The tool reported "declares no Python on its PATH",
  which is true, useless, and points at the project's flake instead of at the
  collected store. Also found by running it: the first version installed its
  executable as `python`, in a bin directory that was, at the time, the PATH a
  dispatch searched — so it shadowed the very interpreter a script asks for. It
  installs as `neuman-python`. `exec` is resolved by joining the tool directory
  with the name, so any name works; a package whose bin directory may end up on
  a PATH does not get to ship a file called `python`. The obvious name stays
  free deliberately, so a script asking for `python` always gets the interpreter.
  **The same lookup, asked by two callers who cannot make a tool call.** The
  resolver was reachable only as a side effect of running `python`, which made
  "does this directory have a usable environment" a question only the model
  could ask, and only by spending a turn on it. Two surfaces need the answer
  without one: the chat window, which outlines the working-directory control so
  a human can see at a glance that the project's own toolchain is live, and the
  system prompt, which states it for the same reason it states the directory
  itself (N84 — telling the model where it is and having the tools start there
  were different things for three features). So `env_probe` is a manifest that
  resolves and reports and runs nothing: no subprocess, no package inventory,
  where `python(action="describe")` spawns the interpreter to list a hundred
  distributions. That cost is right for a model deciding what it can import and
  wrong for a probe that fires every time the user picks a directory.
  **Registered, and granted to nobody.** `grantable()` advertises only what
  policy names, so a tool no role grants never reaches the model's schemas —
  while the client advertises every manifest it can run, and a route can
  dispatch it. That split already carries the directory chooser: `/api/browse`
  dispatches `fs_list` outside the gate, the approval flow and the audit chain,
  because the human clicked a folder in their own file picker and a card per
  click teaches people to approve without reading. `/api/environment` is the
  same shape, and the tool is named by the route in both — a route that let a
  request choose its tool would be a way to run `shell` with no approval card.
  And `diagnostic`, which is what hides the row. Ungranted alone was not
  enough, and the first version got this wrong by reasoning about the flag's
  name instead of its effect: an ungranted tool still draws in the chooser,
  greyed out and struck through under "an operator grants tools in
  `services.neuman`" — an invitation to grant a capability that is not one, for
  a question no user asks. The flag's real job is "registered, dispatched by the
  appliance, never chosen by a person", which is exactly this. It is
  presentation only and appears nowhere in the gate, so it hides the row without
  changing a dispatch decision; the shipped set that carries it is a named
  allowlist in the tests, because a manifest growing the flag by accident
  disappears from the UI with nothing saying it exists.
  **It must be a dispatch, not a read.** A `.direnv` cache lives in the acting
  human's home, which is `0700`, and the server runs as `neuman` — so resolving
  it here would fail on every normal policy, and making it succeed would mean
  loosening a home directory to feed a status light. It would also answer about
  the wrong machine: with `programs.neuman-client` on a separate workstation,
  the server's filesystem is not the one the tools run on (N152).
  **The state is never stored.** It is a fact about the user's disk at this
  moment — a `nix-collect-garbage` turns a resolved environment into
  `env_collected` without the working directory moving — so there is no column
  for it. The browser re-probes on every change,
  and a probe that cannot run draws nothing rather than drawing the last answer
  it had.

- **D35 — The model catalog is a document, not a table.** Providers and models
  are the `providers` and `models` halves of the runtime baseline (D68), read per
  turn by the decision layer. There is no catalog table.
  **What the table was.** Eight columns, a primary key, two readers — both
  single-row lookups by name. No foreign key referenced it, nothing joined
  against it, and `enabled` was written by nothing. Relational storage for data
  that was never relational.
  **What it cost.** Registering a model meant a row written by a generated unit
  shelling out to `neuman-admin model set` over the admin socket, behind a
  `sleep 2` — `requires=` orders a start but does not wait for a socket that
  appears after migration — and hosted models had no way in but the CLI by hand.
  In one document, hosted and local are the same shape: a provider is an
  endpoint and a credential, and a loopback model is one with no credential. A
  model's entry and the role granting it change in one compare-and-set, and
  editing one does not restart the server, so nobody mid-turn is disconnected.
  **The credential is per provider and read at call time.** `ModelClient` is a
  singleton shared by every model, and it held one key read once at startup —
  so a second provider received the first one's bearer token, a live credential
  disclosed to an unintended host, surfacing as a 401 mid-turn on a model the
  admin surface listed as healthy. The path now travels on `ModelTarget` beside
  the endpoint, for the same reason the endpoint travels: a target carrying one
  and not the other is a request to the right host authenticated as somebody
  else. Read per call, so replacing a key takes effect on the next turn.

- **D36 — How a model is asked to reason is a declared capability, not a
  tuning knob.** A catalog entry's `reasoning` — stated by the preset it was
  added from (D76), or by a local runtime's `reasoning` option — says which
  request shape a provider understands and which levels it accepts; the user
  picks a level per turn on the `message` frame.
  **Why it is declared rather than configured.** The three shapes share no
  field. llama.cpp's server takes a flat `reasoning_effort` and passes it to
  the model's jinja template; DeepSeek takes that string *alongside* a
  `thinking: {"type": "enabled"}` envelope; a template that understands nothing
  finer takes a boolean in `chat_template_kwargs`. Which one a provider speaks
  is a fact its model id does not carry — the same reason `vision` and
  `context_tokens` are stated — and sending the wrong one is an error mid-turn
  rather than a setting that quietly does nothing.
  **This is not sampler tuning, and the distinction matters.** N100 established
  that restating a GGUF's own `general.sampling.*` values as flags changes
  nothing while reading as deliberate tuning. Reasoning is the other kind of
  thing: a compatibility question with a discoverable right answer per model,
  which is what a driver encodes. The module exposes no temperature, top-k, or
  sampler here, deliberately.
  **Per turn, not per model.** The level rides on the `message` frame beside
  `model`, because it is a user's choice about one question — the same question
  asked twice in one session can deserve different answers. That makes it the
  same category as `auto_accept` and a different one from the window or the
  costs, which are facts about the model. So the two controls sit together:
  the level is chosen from a chip beside approval on the composer's status line,
  rebuilt from the current model's declared levels and hidden entirely for a
  model that declares none. It moved there from the models panel, which is
  appliance topology — what is installed and what it costs — rather than a
  choice about the next turn.
  **`off` is a level, and for `budget` it is the envelope rather than a
  string.** DeepSeek's OpenAI-format dialect is two independent controls: the
  `thinking` envelope switches reasoning and `reasoning_effort` sets its depth,
  and the string takes low/high/max with no off value (`none` is the
  *Anthropic*-format spelling, a different field on a shape we do not send). So
  `off` sends `thinking: {"type": "disabled"}` and no effort string, and the
  level list is where a model says whether it can be turned off at all. The
  first implementation of `budget` could only enable: the sole way to not reason
  was to name no level, which sends nothing — and for this provider sending
  nothing is not off, because thinking is documented as on by default at `high`.
  A user who turned thinking off got thinking, at full price, with no error.
  One spelling of `off` across the dialects that have one is deliberate, so
  neither the chip nor a preset has to know which dialect is underneath to say it.
  **Which levels exist is a fact of the (model, host) pair, not of the model.**
  The dialect travels with the model; the set of levels travels with the route,
  because a router decides what it forwards. The same DeepSeek weights are
  served elsewhere with `low` dropped and with `xhigh` added. `reasoning` is
  therefore declared per host in the catalog presets, beside the provider's
  model id and its prices (D39) — even where two routes agree today, since the
  failure from inheriting a level the route refuses is the mid-turn 400 the
  catalog exists to prevent.
  **A level the model does not declare is refused before anything is spent.**
  The alternative is the provider's own error for an unrecognised effort
  string, arriving mid-turn in the provider's dialect after the prompt is
  already paid for. The declared `levels` list is what the client offers and
  what the server admits; a declared `default` outside that list is dropped
  rather than enforced, because one typo would otherwise refuse every turn that
  named no level — a failure the user cannot act on.
  **Compaction never inherits the user's level, and it no longer assumes a
  dialect.** The summariser asks for the model's cheapest *live* level in the
  model's own dialect (`reasoning.summariser_fields`), never the level the user
  put on their turn. The previous version hardcoded
  `chat_template_kwargs: {"enable_thinking": false}`, which is the `toggle`
  shape, on the reasoning that thinking is 21x the cost of the answer (N94:
  64.1s against 3.0s). On a model whose catalog entry declares `effort` that
  field did not turn thinking off — it made the model continue the transcript it
  had been asked to summarise, and compaction replaced the head with an echo of
  itself (N143). The dialect therefore travels with the model as it does
  everywhere else, and the *level* asked for is the cheapest one that does not
  mean off, which keeps the N94 saving without lying about the shape.

- **D37 — A turn's cost is reported by the provider where possible, computed
  only where it is not, and the difference is carried to the surface.**
  The session footer shows a running total in USD beside the token counts.
  `decision/harness/pricing.py` is the only place that turns usage into money,
  and it prefers the provider's own figure to arithmetic of ours.
  **Reported beats computed, and it was already arriving.** OpenRouter returns
  `usage.cost` — credits actually charged — in the final SSE message of every
  stream, with no opt-in; its documentation states the older
  `usage: {include: true}` and `stream_options` parameters are deprecated and
  that full usage details are now always included. The gateway adapter already
  copied the whole usage dict (`Chunk("usage", dict(usage))`), so the field had
  been reaching `outcome.usage` and being discarded for the life of the
  feature. For such a provider the rates are never multiplied by anything.
  **Why computing needs the cache split to be viable at all.** DeepSeek reports
  no cost but does report `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`
  — and prices hits at $0.003 per million against $0.15 for misses, a factor of
  fifty. An agentic turn resends the same long prefix every step, so the hit
  path is what a conversation actually runs on: pricing the whole prompt at the
  miss rate overstates a warm session by nearly that factor. Hence
  `cachedInputCostPerM` as a separate declared rate, null when a provider does
  not discount — null rather than 0, because a provider without the discount is
  not one whose cache hits are free.
  **`estimated` is part of the number.** A figure the provider billed and a
  figure we multiplied are different claims, and the flag latches over a
  session: one computed step makes the total an estimate. The client renders
  that as a `~` prefix. Stating it is what makes the known inaccuracies
  tolerable rather than hidden — DeepSeek charges half rate off-peak, and
  pricing by wall clock would put a clock and a Chinese public holiday calendar
  in the cost path to turn one wrong number into a differently wrong one, so a
  computed figure can read up to 2x high during peak hours.
  **Unknown is not zero.** A local model with no declared rates reports no
  figure at all rather than $0.00: "free" and "not priced" are different claims,
  and the zero is the one that gets believed. The same reasoning makes a
  reported `0.0` a *price* rather than a missing field — checked for presence,
  not truth, because `or` would discard a genuinely free turn and fall through
  to arithmetic that invents a different number.
  **Per session, in memory.** The total sits beside the context bar, which
  describes this conversation, so a daily figure next to a per-session bar would
  read as the same scope when it is not — the daily token budget in
  `UsageRepository` remains the per-day accounting and stays separate. It is
  held in a dict keyed by session rather than persisted: it is a running
  read-out of a live conversation, not a record anything bills on, and it resets
  with the server. A cost column on the usage ledger is the right home if that
  ever stops being true.

- **D38 — There is one admin surface, and the model does not reach it.**
  `neuman-admin` is the only way to administer a running appliance. The admin
  socket is server-local, and the identity it authenticates by is the connecting
  *uid* — which a server-native handler, running as the server, does not carry.
  An admin has the CLI at a shell. `session_query` remains the one server-native
  tool, and it needs no authority check because it answers about the caller's own
  sessions and has no wider scope to gate.

  The socket still authenticates by **peer credentials**, so the kernel reports
  the connecting uid, and authority stays `require_admin` inside the server.
  Reaching it requires `neuman` OS group membership, granted by
  `adminSocketAccess`; authority is the `neuman-admin` group in the account
  store. A reachable non-admin connects and is refused. If a model-facing
  read-only admin tool comes back, it comes back as a designed server-native
  operation with its own argument for why the core may read what only an admin
  may — not as a subprocess over a socket.

- **D39 — Model and provider facts are vendored presets, not a detected or
  declared guess.** The facts the harness needs — context window, maximum
  output, vision, the shape of the reasoning control, a provider's base URL —
  ship with Neuman as reviewed presets. An operator chooses a preset and states
  policy; they do not restate what a model or a provider is. Where the presets
  live and how an entry is held to one is D76: both the hosted routes and the
  local ones are data the server ships, and a local route is run by the runtime
  host from that data (D78). They were Nix modules under `nix/module/runtimes/`
  until the runtime host made a unit per model unnecessary.

  Every kind is checked as shipped. `server-test` loads every preset, builds an
  entry for every route it claims and requires it to pass the rule a write is
  held to, and runs every local route through what the server writes and what
  the runtime host reads (N170). These are the files every deployment is built
  from, so a preset that stopped producing a valid entry must fail a check
  rather than every deployment that uses it.

  A preset must separate **facts** from **policy**, because overriding the first
  is a defect and overriding the second is the purpose. The window, vision, the
  reasoning dialect and its levels, the provider's id and URL describe the model
  or the route, and contradicting one is refused naming the field. Reply length,
  the default reasoning level and every cost figure are the operator's: rates
  are negotiated, and DeepSeek's are off-peak figures that double on a schedule
  nobody should encode.

  **No route is a default, because there is no sensible one.** This is the
  correction that produced the structure. The first version treated a model's
  provider id as a model fact with the provider defaulted, so switching hosts
  sent DeepSeek's short id to OpenRouter and earned a 400 mid-turn. Measured
  2026-10-16, `deepseek-v4.1-flash` is `deepseek-flash` at 384,000 output tokens
  and $0.15/M input direct, and `deepseek/deepseek-v4.1-flash` at 131,072 and
  $0.036/M through OpenRouter — four times the input price for triple the output
  ceiling. Which of those matters is the deployment's question.

  So there are **three kinds of fact, not two**: of the *model* (window, vision
  — shared across routes), of the *(model, route) pair* (id, output ceiling,
  prices, and the reasoning levels), and of the *deployment* (which route, the
  credential, reply length, residency, VRAM). The middle kind is what the first
  version had no place for. Reasoning moved into it after it was first written
  into the first: the dialect travels with the model, but the *levels* travel
  with the route — a router decides what it forwards, and the same DeepSeek
  weights are served elsewhere with `low` dropped and with `xhigh` added (D36).

  A model may be added twice — on two providers, or once local and once hosted —
  and each entry takes a name of its own, because the name is the string a user
  picks in the composer and one entry silently shadowing another would be a
  model answering as another.

  A local runtime differs in two ways a hosted route does not: `contextSize` is a
  VRAM decision that reaches llama.cpp as `--ctx-size` rather than a window the
  provider owns, and `vision` is *derived* from `mmproj` rather than stated,
  because the projector is the evidence and asking for the flag as well would be
  asking the same question twice with two answers possible. Both reach the
  catalog through the manifest the module writes, so the entry describes the
  process that was started. `contextSize` and `maxTokens` are proposed defaults
  rather than claims: 65,536 is a choice about one GPU's memory at one
  quantization, and llama.cpp `/props` is what checks the two agree.

  Runtime flags split the same way, into `modelFlags` and `extraFlags`.
  `--jinja` is a model fact: without it llama.cpp uses a generic chat template
  and the model does not emit tool calls in a shape the harness can parse, on
  any hardware. `--n-gpu-layers` is nothing of the sort. And `--spec-type
  draft-mtp` is the interesting case — the MTP head is an architecture fact, so
  it looks like one, but it costs ~1,188 MiB of draft context, which makes it
  and `contextSize` competing claims on the same memory. Asserting it would have
  made the documented remedy ("lower one before raising the other")
  unavailable, so speculation is policy: a resource decision wearing a
  capability's clothes. The cache type (`kv_cache`) is policy more plainly: a
  runtime option every model has, a number format chosen for the memory. It is
  offered as one value written to both K and V and limited to `f16` and `q8_0`,
  because every other type and every mixed pair runs attention on the CPU on
  this build without saying so (N100). Measured, `q8_0` buys back 1.7 GB at 64k
  for a decode 6–12% slower once the window is full, so the default stays
  llama.cpp's own and no preset restates it.

  A runtime preset does **not** set `package`. The accelerator is one
  appliance-wide decision — `services.neuman.gpu`, whose default exists because
  per-backend package defaults once disagreed with what the appliance was for
  (N98) — and a preset carrying its own `cudaSupport` would reintroduce exactly
  that: a per-model copy of a machine-wide fact. Nor can a preset check the
  weights: `modelsDir` is a runtime directory, so a GGUF's presence is not an
  evaluation-time fact, and the honest check is where the file is opened.

  Runtime detection was considered and does not work. OpenAI-compatible
  `/v1/models` returns `id`, `created` and `owned_by` and no context length, and
  neither DeepSeek nor OpenRouter reports one — the 1M figure for
  `deepseek-flash` comes from a documentation page, not an API. So "ask the
  provider" is not available for the one field the context manager cannot do
  without.

  Operator declaration was the previous design and it failed twice, both
  recorded: a provider id pointed at a `deepseek-reasoner` model the provider
  had retired ten weeks earlier, and a 16,384-token model managed against a
  96,000-token ceiling died at the provider every turn (N86). Both are the same
  defect — a claim about a model that nobody checks until a turn spends tokens
  on it — and both are what tenet 11 forbids. A preset moves the claim into a
  reviewed artifact that CI can check and a release can pin.

  The exception is a model served on this machine, where the fact *is*
  available: llama.cpp `/props` reports the real `n_ctx` of the loaded weights,
  which beats any declaration because it describes what was actually started.
  A local runtime is asserted against it at startup, and a mismatch is a
  deployment error rather than a surprise mid-turn.

  Neuman supports exactly the models it ships presets for. That is a smaller
  claim than "OpenAI-compatible" and a truer one: being a client of a protocol is
  not the same as getting the most out of a model (tenet 20), and an unlisted
  model is a contribution with a reviewable diff rather than a form an operator
  can get wrong in private. A custom entry remains possible and carries every
  claim unchecked.

- **D40 — Harness sizing derives from the window, never from a constant.**
  Every token budget the harness holds — the compaction reserve, the margin,
  the prompt overhead, how much recent history survives a compaction — is a
  proportion of the acting model's own context window with a floor, in the shape
  `max(floor, context_tokens // n)`. No budget is a bare number.

  A constant cannot be right across the catalog, and the error is silent in
  both directions. A fixed 16,384-token reserve is 25% of a 65,536-token local
  model and 1.6% of a 1,048,576-token hosted one: the same literal either
  compacts constantly or lets history reach 98% full before acting, and the
  second is how a turn dies. Proportions mean adding a model to the catalog
  requires no harness retuning, which is the property that makes "dozens of
  models" a catalog change rather than a tuning exercise.

  A model's facts and an operator's policy are also kept apart, because they
  disagree about what they want. A window is a fact and belongs to the catalog;
  how long a reply may run, or how much may be spent on one prompt, is a choice
  and belongs to config. Conflating them produced exactly one bug: a window
  declared low as a *spending* ceiling was read by the reply-sizing path as
  physics, so the appliance starved its own replies toward the floor and churned
  while the provider would have accepted another 745,000 tokens.

- **D41 — The transcript is a timeline, and each step's detail has one home.**
  A turn has two readings at once. *Following the conversation* wants the prose
  in a scroller; *watching the work* wants the current step large, without
  hunting for it in a wall of output. Three columns tried to serve both and
  served neither — the tool lane was too narrow for a diff, and the answer
  competed with the telemetry beside it. The transcript is a chronological
  **timeline** — the conversation in full, with each reasoning block and each
  tool call reduced to one line.

  The detail has two homes, and exactly one at a time. The step the turn is on
  is shown in the run's one **stage** at its foot (D62). Every other row is a
  disclosure under itself: a press mounts its detail after the row, where the
  reader is looking, and a second press takes it back out (D64). A detail is
  moved, never rendered twice, and `_mounted` says which home it is in —
  `"stage"`, `"inline"` or null (the hazard D32 names). `pressRow` is the one
  dispatcher: the live row toggles the stage, a history row toggles its own
  card. An earlier version made the stage the *only* home, which fixed the step
  swap but turned every history press into a replacement at the foot and left
  the finished turn's last card open; D64 is the correction.

  The stage is one fixed height (`--step-h`), with its body scrolling; a thought
  that streams for a minute and a call that returns a page of output are the
  same box, and once the stage exists a step adds only its one-line row. When
  prose starts, the stage folds into a reserved blank and the reply is written
  into it (D64), so the end of the run does not leave a card over the answer. A
  blocked call stages its card whether or not it is newest — a turn whose
  approval buttons are out of view is the N89 shape of bug. A tool's row and its
  detail remain one state string with one writer (`setToolState`). See D62 for
  the live stage and the crossfade, D64 for the inline history and the reserve.

- **D42 — Oversized tool output is a file the model reads, never an elision the
  harness performs.** A tool result past a small preview (`PREVIEW_BYTES`,
  16 KiB) is written by the *client* — the stage that runs as the acting user —
  to its own `spill/` directory, and the result carries the preview plus that
  ordinary path. The model opens it with `fs_read`, searches it with
  `fs_search`, parses it with `python` — the tools it uses on any file. The
  manifest's `max_output_bytes` is the ceiling of that file, not the size of the
  message: when the two were one number, `web_fetch` stopped itself just under
  it and so never spilled at all (N180). This replaced a design that wrote the
  full text to a server-owned file the model could not reach and then served the
  bytes back through a privileged reader (N125). The content is the dispatch's
  own output, so returning it to the same account grants nothing it did not
  have; cross-account isolation holds because the directory is in the client's
  own state. The spill directory is **plumbing, not reach**: the tool host
  admits it for the read tools whatever the account's surfaces say, and never
  for a mutating one, so no policy names it and it cannot satisfy a write.
  `persist_output = false` opts a tool out: one that bounds its own output by
  the caller's arguments (`fs_read`, `fs_search`) delivers inline up to its cap,
  and one whose stdout the server transforms (`fs_image`, `fs_write`,
  `fs_edit`) must. The event log remains the canonical record; the spill file
  is a working copy. See N125, N126, N127 and N180.

- **D43 — Reach is per tool, and one tool may combine what the split kept
  apart.** `needs` is a property of a manifest, so a single tool can hold
  `network` and `write` at once; `git` is the first to do it, because fetching a
  repository and reading it on disk is routine work and had no path at all while
  egress and writing lived in different tools. It runs as the user, in the
  branch's working directory, and writes where their filesystem permits: there
  is no longer a profile to narrow or a scratch directory to route through, and
  no host list to hold on the client. `needs` is still what the gate reads, and
  a tool that declares `network` is still the kind withheld from an account
  whose policy grants no network.

- **D44 — The work between two pieces of talk is one run, and a row's columns
  come from the run.** The timeline's tool calls and its reasoning blocks are one
  run per turn, not one run per unbroken sequence of tool calls. A reasoning
  block used to end the run, so a turn shaped `reason → call → reason → call`
  drew as four stubs of a rail rather than one block of working, and the spine
  that was meant to say "this is the working, not the talking" said almost
  nothing. The run now spans the turn's steps until prose, a note, a message or
  the end of the turn closes it; a thought stays distinguishable by a hollow node
  rather than by a break in the rail, and the acts remain in the order they
  happened. The wait is not talk and is not drawn in the transcript at all: it
  is a state, the rail's phase carries it (`trackViewFrame`), and the
  composer's Stop button carries the clock. It used to be a line drawn beside
  the run and removed on the first chunk, so the page moved twice per model
  call; a state in the event path is a seam waiting to happen (N151). The run
  is also the row's grid: each row takes its three columns
  from the run (`subgrid`, with the per-row template as the fallback), so verb,
  subject and outcome line up down the whole run instead of each row sizing its
  own. Three things follow on the rows themselves, and they are the point of the
  entry: the audit name leaves the scan line for the opened card's head, the
  node becomes the status (accent running, amber blocked on a human, red failed,
  dim refused, receded once done) while the right edge carries the result's size
  rather than a status word, and a row's verb may be a function of its arguments
  because a single tool — `browse` — is several acts and, with the name gone, the
  verb is the only thing left to say which. The mechanism this replaces was a
  "chain" of consecutive calls; the name went with it.

- **D45 — A fetched page is drawn from an inert parse, never the live
  document.** `web_fetch` returns HTML, and the card draws the page it stands for
  rather than the markup: the response is parsed with `DOMParser` into a document
  that is never attached to the client, walked for its leaf block elements, and
  re-emitted as the client's own nodes. It is the one parse in a file whose rule
  is "nodes, never `innerHTML`", and it is safe for exactly the reasons that rule
  protects: `DOMParser` runs no script, loads no resource, and the parsed tree
  never enters the live document, so reading `textContent` out of it cannot reach
  this page. **No URL from inside the page becomes a link** — the only links are
  the call's own, `http(s)` only — and that is what keeps a fetched page from
  turning into a window onto another site inside the transcript. The view is
  bounded by a block and a character budget, with the full response kept under a
  `raw HTML` disclosure, so the bound is a reading rather than a loss. A site's
  own HTML is never handed to an `<iframe>` and never inserted as markup; an
  iframe would be the live document by another name, and a sanitiser is the
  library this client does not carry.

- **D46 — A reopened session shows the thinking a live one did.** The display
  projection carries an assistant turn's chain of thought under a reader-only
  `reasoning` field, and `replay` draws it as the same thinking row the live path
  drew. Reasoning used to be absent from a reopened session on the grounds that
  it is not part of what the model is sent back — true for most dialects, and
  dropping it made the reader's record less than what they had watched. The two
  audiences
  want different things from one event, which is the split `for_display` already
  made for a tool's name, its exit status and a write's diff; the field is named
  `reasoning` rather than the dialect's `reasoning_content` so a display
  projection cannot satisfy a provider field by accident. The model path is
  otherwise unchanged: it gets thinking back in the dialect that asks for it
  (`replay_fields`), never from a display read, and — for a reply the cap cut
  mid-thought on a dialect with no replay field — as a following `user` turn
  rather than as assistant content (N139).

- **D47 — A scratch path is carried as data, not recovered from the note.**
  Output that outgrows a tool's preview is persisted to
  `spill/<tool>-<dispatch_id>.<stream>` (D42), and the dispatch id is minted per
  dispatch and deliberately is *not* the tool-call id — one write is dispatched
  twice, preview then real. The name therefore says nothing about what produced
  it. The harness hands the model a sentence naming the path, and the same path
  now rides the `tool_result` event and the display projection as `persisted`,
  keyed by stream. The client records each path against the call that wrote it
  and titles a later `fs_read`/`fs_search` of it — `stdout of Run “pytest -q”` —
  rather than parsing the English note, which would make its wording
  load-bearing. Reader-only for the same reason `structured` is, so the model
  path never acquires it; an unknown path (another session's spill file, a `git`
  checkout, a result from before the field) falls back to the filename.

- **D48 — A reopened session is the conversation, not the model's compacted
  context.** The display projection ignores a `compaction` event's replacement
  list and keeps every original message, appending a reader-only `note` where the
  model's view was shortened. It used to apply the same replacement the model
  path does, on the theory that the replay was "what the model saw" — which meant
  a reopened session showed only the tail of a compacted history, with the user's
  own prompts gone and the synthetic `system` summary never drawn at all. The two
  audiences want opposite things here, and `for_display` is already the seam that
  says so: the model needs a history that fits the window, and the reader needs
  the one that happened. Compaction appends a replacement list; it never deletes,
  so the originals are in the log either way — this is a projection choice, not a
  storage one. The model path is unchanged.

- **D49 — Chrome is emitted by the region it governs; there is no global bar.**
  The client had a `<header>` carrying a session toggle, a wordmark, a model
  dropdown, a GPU chip and an account chip — three disclosures pointing in three
  directions, held apart by an `#gpu-toggle.hidden + #account-toggle` auto-margin
  adjacency hack, with `--bar-height` measured at runtime from a bar whose height
  depended on the account name's font. Each control had been placed by local
  convenience because none of them had a region to belong to; the bar was where
  unowned controls went. The fix is not a better bar but no bar: a control is
  rendered by the region it governs, and the header emptied itself.

  The shell is one column — the transcript, with the composer beneath it, so the
  box you write into and the prose it writes into share a measure. The rail is
  **not a column**: it is a fixed overlay at every width, with a shadow, floating
  over the transcript. It was made a grid track once, and a track displaces —
  opening a glance squeezed the transcript you were reading, and dragging the
  rail's width squeezed it harder. An overlay leaves the transcript and the
  composer exactly where they were, which is what a list you open and dismiss
  should do. The grip that folds it is fixed, because a grip inside the pane
  would fold with it and leave nothing to click (N121). The width is dragged from
  the grip or from the rail's whole right edge — the edge is what a hand reaches
  for, and it has no fold to mean, so a press on it that does not move does
  nothing.

  Each region owns its chrome: the rail carries the wordmark, the New-session
  button and the filter. The model and GPU
  control moved to the composer's status line, beside approval and reasoning: the
  model is a per-turn scope choice, and reasoning levels are the model's own
  declaration, so the two belong adjacent. Settings, once a resizable full-height
  column for one read-only section, is a card opened from the account menu —
  approval had already moved to the composer and Sign out to the menu, so what
  remained was a reading.

  **The account is the exception**, and the exception is the point of the rule:
  nothing in the transcript, the rail or the composer owns "who am I", so the
  account has no region to be emitted by. It is a fixed circular avatar in the
  window's top-right corner — the one piece of chrome the corner keeps — opening
  its menu downward. It carries initials rather than the name, because the face
  has to stay one size for every account, with the whole name in `title` and
  `aria-label`. The header's version was a rounded rectangle carrying the name,
  which was a button among buttons; a circle reads as an identity.

  The transient surfaces — the models card, the account menu, both choosers, and
  the approval and reasoning popovers — are all anchored to their opener and
  dismissed by one click-away/Escape loop, and the rail joins them: it is an
  overlay, so a press outside it or Escape puts it away. Because the cards no
  longer share a corner, the hand-written mutual exclusion and the top-right
  special case are gone: the generic loop closes what the press was not inside.

- **D50 — A user's tool selection narrows the advertisement, and a deselected
  tool is absent rather than forbidden.** A branch carries `tool_selection`:
  the tools the user wants this conversation to have, chosen from a chip on the
  composer's status line. It is intersected with `grantable` where the schemas
  are built (`harness/prompt.py`), so it can only ever subtract — a name policy
  does not grant confers nothing by being listed, which is the same property
  that makes a working directory outside reach narrow to nothing instead of
  widening it.

  The alternative was to advertise everything and tell the model which tools it
  may use. Rejected on both counts: it spends window on schemas for tools that
  cannot run, on every turn of the conversation, and it makes a scope the user
  set depend on the model's compliance with a sentence rather than on a filter.
  The system prompt's tool list is *derived* from the schemas it ships with, so
  filtering once narrows both and they cannot come to disagree.

  So a deselected tool simply does not exist from the model's side, and it is
  not told that one is missing — the knowledge has no use, because the remedy
  belongs to the user and not to the model. Two consequences are handled rather
  than left implicit. An empty selection is legal and says so in the prompt
  ("you have no tools on this turn; answer from the conversation"), because a
  bare `(none)` reads as a broken appliance to a model that has no idea a human
  asked for it. And a model *can* still name a deselected tool, from earlier in
  the same conversation where its schema is still visible in history — that is
  refused at dispatch with its own reason, `tool_not_selected`, rather than the
  gate's `tool_not_granted`, because an operator reading the audit chain must
  not be sent looking for a policy fault when what happened is that a user
  unticked a checkbox.

  `NULL` is the default and means *every granted tool*, which is why the column
  is nullable rather than an array defaulted to the current registry. A stored
  list naming everything installed today would be indistinguishable from the
  default now and diverge from it the moment the appliance installs another
  tool, silently withholding it from every branch created before it. The client
  keeps the same distinction: `null` is Select all, `[]` is a user who switched
  everything off, and toggling the last tool back on stores `null` rather than a
  complete list.

  Per branch and not policy. It is state on the branch row, like `working_dir`
  and `attachments` (current state, not history), and deliberately not an
  overlay: the overlay is guarded policy authoring that bumps the generation and
  revokes live sessions, and a UI scoping toggle must not look like a grant
  changing. The PATCH that writes it *drops* names the gate no longer allows
  rather than refusing the whole request — unlike a path outside reach, an
  ungranted tool in the list is a stale checkbox from before a revocation, and
  refusing would leave the user unable to save anything until they reloaded.

- **D51 — Settings is a centred modal with its own scrim, and the account chip
  opens it directly.** This revises two parts of D49. The account menu is gone:
  it held exactly two items, Settings and Sign out, which put the thing people
  came for behind two clicks for no gain. The chip opens the dialog, Sign out is
  the last item in the dialog's sidebar, and one floating surface disappears.

  The panel is no longer a corner card either. A 360px card anchored under the
  chip was right while the content was one read-only policy list; Settings now
  has a column of sections — preferences the person changes, and readings of what
  policy grants — which needs a sidebar *and* a body, and a 640px dialog hung off
  a corner control falls off a narrow window. So it is centred by `inset: 0` plus
  `margin: auto` rather than a translate, which keeps a focused control inside
  off a half-pixel; and it is bounded by `min()` against the viewport so it
  shrinks rather than overflows. It deliberately does **not** fill the window —
  it is opened mid-conversation, and the transcript staying visible around it is
  how you keep your place.

  **A scrim, and that makes it modal rather than merely on top.** D49's generic
  click-away loop infers dismissal from "the press was not inside any listed
  card"; a real element is better here for two reasons: the press has a target
  instead of being inferred, and the scrim *blocks* it from reaching what is
  underneath. So Settings is deliberately absent from `CARDS` — that loop can
  only hide one element and would leave the scrim up, swallowing every click
  afterwards. It is still a disclosure, so the `aria-expanded` observer runs
  over `CARDS` plus the chip/dialog pair.

  Being modal has consequences two other handlers had to learn, and both were
  real bugs found while building it: the type-anywhere listener would focus the
  composer *behind* the scrim, putting the caret somewhere invisible and losing
  the keystroke; and the click-away loop would collapse the rail on the same
  press that dismissed the dialog, doing two things at once. Both now return
  early when the dialog is open, which is the same rule D49 states for the row
  menu and Escape — one press, one dismissal.

  **Tools are not in Permissions.** The composer's tool chip is where tools are
  seen and chosen (D50), so the old `#policy` list rendered the same grant
  twice. What is left is reach stated in a user's terms rather than policy's
  field names — where you can read and write, which hosts `web_fetch` can
  reach, and what bounds a turn — with provenance beside each value, because
  "why can I do this" is the follow-up. It remains a *reading*: the one policy
  field a user authors is `auto_accept`, and that is a chip beside the decision
  it governs.

  Usage is **today only**, from a new self-scoped `GET /api/usage`. `usage_daily`
  keeps a row per day, so history is there — but the cap it is measured against
  resets daily, so a week of totals is a different question with a different
  denominator. `tokens_daily` comes from composed policy rather than the usage
  row, because the cap can change under a running day and a percentage against
  a stale limit is worse than none; `null` means uncapped, and the client draws
  no bar rather than one against infinity. Settled and reserved are reported
  apart and never summed: a reservation is released when a turn fails, so one
  combined figure would visibly fall back down and read as broken accounting.

  Interface preferences live in `localStorage`, like the rail's width: they
  describe one browser, and syncing them would let one tab
  change another's reading. The first one keeps the reasoning chain off the
  stage — and it governs *staging only*. The row is still built and still
  filled, because a preference that suppressed it would hide the fact that the
  model thought at all, and the transcript is a record.

- **D52 — The session filter searches what was *said*, server-side, over a
  derived index.** The rail's filter was a substring match over session titles
  in the browser. A title is the first few words of the first message, so a
  conversation was findable only by how it opened — everything actually
  discussed in it was unreachable.

  **Server-side**, because the alternative is shipping every account's whole
  history to the page to filter it there: the payload grows without bound and a
  conversation ends up in a browser's memory to answer a question about which
  one to open. The client sends `?q=` debounced at 160ms, and `sessionsPaint`
  already discards an out-of-order answer, so the debounce is about not sending
  requests rather than about correctness.

  **Full-text, not fuzzy.** The ask was "fuzzy like fzf", and that was declined
  with the reason stated: `fzf` matches a scattered subsequence (`srh` →
  `search`), which no index can answer, so it degrades to scanning every
  message in every session on every keystroke. Postgres full-text matches words
  and prefixes in any order, is indexed, and stays correct as history grows —
  which is also what ChatGPT and Claude do. The honest summary is that "fuzzy
  like fzf" became "fuzzy like a search engine".

  **`session_search` mirrors `audit_search`** (D8): a derived, rebuildable
  `TSVECTOR` with a GIN index, maintained inline inside the same transaction as
  the event append, plus a startup reconcile that repairs divergence after a
  restore, a purge, or a crash between the two inserts. Derived data, so a
  rebuild can never damage the log it indexes. Two differences from the audit
  one, both deliberate: it is keyed **per session rather than per event**,
  because the question is "which conversation was that in" and per-event rows
  would mean deduplicating hits back into sessions on every keystroke; and it
  keeps the plain text beside the vector, because `ts_headline` needs the
  original document to produce a snippet and re-deriving it per result would be
  a second query per row.

  **Only `user_message` and `response_final` are indexed.** Reasoning is the
  model thinking aloud, and a tool result is a file's contents or a directory
  listing — indexing those would mean typing a filename matched every session
  that had ever listed the directory containing it, burying the ones that
  actually discussed it. `tool_proposal` and `tool_decision` are governance
  records, not speech.

  Results are ordered **by the same keys as the unfiltered list rather than by
  rank**: the reader's chosen date, with pinned sessions first (D61). A
  relevance ordering would scatter the date groups the rail is built from, so
  the match decides membership and the order the reader chose decides where
  among the hits it lands. A deliberately-renamed title still matches, because
  the name someone chose may never appear in the conversation.

  The snippet crosses the wire with `<<`/`>>` delimiters rather than markup, and
  the client builds elements from them. Conversation text is the most untrusted
  string in the appliance — whatever a person typed and whatever a model wrote —
  so handing the browser HTML to insert would make a session title an injection
  site (D1).
- **D53 — The interface is a semantic token set with named themes, and the
  chosen theme belongs to the account rather than to the browser.**

  **A theme is a palette.** The setting is labelled Theme; the stylesheet, the
  `data-palette` attribute, the API field and the database column all say
  palette, because a theme *is* a `:root[data-palette="…"]` block of semantic
  tokens. Two words answer two questions: theme is what a person chooses,
  palette is what the stylesheet defines. The code keeps the second.

  **Tokens are semantic, not indexed.** The stylesheet names a colour by what it
  is *for* — `--surface-raised`, `--ink-on-accent` — rather than by a slot
  number. The obvious alternative is the base16 shape: a flat list of 16 or 24
  indexed colours that elements are assigned to. That shape exists because a
  terminal has no semantics, only ANSI indices, and it cannot express the
  property that actually matters in a UI: contrast is a property of a *pair*,
  ink against a surface. With roles named, one table of pairs checks every
  palette at once (`test_every_palette_is_legible`); with indices, each new
  theme has to re-derive which combinations are load-bearing, which is the same
  as not checking them.

  A theme therefore sets the roots and nothing else. Hovers, tinted surfaces and
  the hue washes are `color-mix` over those roots, so adding a theme is filling
  in a form. Two values constrain the derivation rather than name a colour:
  `--tint` and `--tint-hover` say how much of a semantic hue a resting and a
  hovered surface take, and both are held where the hue stays legible as ink on
  its own tint. That ceiling is a property of the scheme — gruvbox dark reaches
  it at 8% and 11% where the original dark theme affords 11% and 16%. Where a
  derivation must run the other way in light mode — a hover is lighter on a dark
  surface and darker on a light one — the *direction* is itself a token
  (`--lift`), because one reversed token is cheaper and more honest than a
  parallel set of rules under a media query.

  **No rule may carry a colour literal**, and a test enforces it. This is not
  tidiness: the stylesheet had 35 hex literals plus 11 hardcoded black shadows,
  every one correct in the palette it was sampled from and wrong in any other,
  and invisibly so until someone switched theme. `#22262e` appeared seventeen
  times as a hover surface the palette had no name for. The only literals
  allowed are inside a `:root` block, which is where a palette is defined, plus
  the scrim — which is asserted by name so the exemption cannot widen.

  **Nord, Everforest and Gruvbox are mappings, not reproductions.** Nord's
  published `nord11` red measures 3.05:1 on `nord0`, and `--bad` carries real
  small text here (error messages, a failed tool's state, the login fault), so
  the red and the magenta are lightened and the surfaces sit a step below the
  official Polar Night. Everforest is taken at its **hard** background in both
  modes; its light `fg` measures 5.40:1 on its own bg0, and its light accents
  sit between 1.9 and 3.3:1 on the card, so those inks are darkened while the
  hues are kept. Gruvbox's bright red measures 3.37:1 on dark1 and its bright
  blue 4.31:1, so both are lightened; its light yellow and green measure 3.4:1
  and 4.3:1 on the card and are darkened. An unreadable error message is a bug,
  not a theme.

  **The choice is per-account, in the database.** This is a deliberate exception
  to the client's standing rule that interface preferences live in
  `localStorage`. That rule's stated reason — they describe *this browser*, and
  syncing them would let one tab change another's reading — is sound, and still
  governs the rail's width. Neither clause holds for a theme:
  someone signing in from a second machine wants their own colours rather than
  the default, and two tabs agreeing on the theme is the correct outcome rather
  than a surprise. The deciding argument is reach. These are the settings a
  `customize-neuman` tool must eventually be able to write, and the server
  cannot read or write `localStorage` at all — so storing them there would put
  the appliance's own customisation surface out of the appliance's reach.

  They ride with the identity, in the `/api/login` and `/api/me` responses the
  client already awaits, rather than in a request of their own: a second
  round trip means the page paints in the default theme and then corrects
  itself, which is a visible flash of the wrong theme. The administration page
  reads the same field: a theme belongs to the account, not to one document.

  Two columns on `accounts`, not a preferences table — both scalar, both read
  once per page load alongside the identity they belong to, and a key/value
  table would buy extensibility that `ALTER TABLE` already provides at the cost
  of a join on the client's startup path. The write is a PATCH rather than a
  PUT because absent must mean unchanged: two tabs are ordinary, and a
  whole-record write would let the theme picker in one revert a checkbox
  changed in the other. It is **not audited** — a colour scheme is not a
  governed decision, and the audit chain is for what was decided under which
  policy.

  The palette set is named in five places that cannot be collapsed into one (the
  stylesheet defines it, the client's list and the settings markup carry it, the
  server refuses anything else, and the CHECK constraint backs that up), so a
  test asserts they agree. A theme added to four of the five is a button
  that 400s, or a theme nobody can reach.

- **D54 — Context is managed cheapest-first: mask stale spilled results, then
  merge an anchored structured summary over a task statement pinned verbatim.**

  **Masking precedes summarization, and is not an event.** A tool result that
  outgrew a message is already a file the model can read, so once it is older
  than the kept tail its preview is redundant and is replaced with the pointer
  alone. This is the intervention with the best measured cost/benefit — JetBrains'
  "The Complexity Trap" (arXiv 2508.21433) measured clearing stale tool output
  cutting cost ~52% while *improving* SWE-bench solve rate ~2.6% — and it cannot
  lose data, because the file remains. It is deliberately *not* recorded as a
  replacement event: it is a pure function of the history and the window, so the
  next turn re-derives it from the same log. Compaction is an event for
  the opposite reason — summarization is not reproducible, so its output must be
  preserved.

  **The summary is a persistent anchor, not a reconstruction.** A later
  compaction summarizes only the newly evicted span and merges it into the
  standing summary; it never regenerates the whole thing. Regeneration drifts a
  little every cycle, and the generation that has forgotten the request is
  indistinguishable from the first until it is too late. Factory's evaluation
  scored anchored iterative summarization at 3.70 overall and 4.04 on accuracy,
  against Anthropic's 3.44/3.74 and OpenAI's 3.35/3.43 — and OpenAI's endpoint
  achieved the *highest* compression (99.3% of tokens removed) with the *worst*
  quality, which is why compression ratio is not the metric. The anchor carries
  an explicit schema (established facts with exact values, decisions with
  reasons, artifacts with paths, approaches ruled out) because the categories
  are precisely what a freeform summary drops.

  **The task statement is pinned verbatim, outside the anchor.** The first user
  turn is the one message the whole turn depends on, so it is never admitted to
  a summary; a `## Task` heading restates it for convenience, but the pinned
  message is the authority. This is the fix for the drift observed in session
  `1fefc5dc4220`, which was asked for a coordinate report and by its tenth
  compaction described itself as "explaining the frame convention".

  **Within one compaction, pieces are merged sequentially, not folded.** Each
  piece is merged into the running anchor in turn; an independently summarized
  piece has only its own text, so a fact stated earlier exists nowhere when a
  later piece is written. A single empty piece reply leaves the anchor unchanged
  rather than failing the turn, but a fold in which *every* reply was empty is a
  broken summariser and is reported as `compact_failed` rather than presenting a
  partial anchor as complete.

  **Reasoning is the lever, not the output ceiling.** A reply that spends its
  whole allowance thinking emits nothing usable and then feeds the next
  compaction; the published behaviour is that thinking expands to fill whatever
  budget it is given, so raising `maxTokens` makes it worse. The local Qwen
  preset therefore defaults to `low`, and the unfinished thought handed back to
  continue from is a bounded tail rather than all 16,384 tokens (N139 fixed
  where the thought belongs; D54 and `projection.RESUMPTION_TAIL_CHARS` fix how
  much of it goes there). `docs/harness.md` is the public write-up; the boundary
  invariant in `context.py` (never cut between `tool_calls` and their replies)
  is unchanged (N116).

- **D55 — The client executes; the server decides.** SPEC section 2.
  Everything the old architecture put in a server-side sandbox — the executor,
  the tool host, the browser — runs on the acting human's own machine, as that
  human. The server keeps the decision core (policy, the gate, approval, the
  audit chain, sessions, the model gateway) and executes no user-reach tool. The
  principle is restated for it — the agent runs as the human and does nothing the
  human has not approved — and the cost is stated at the top of
  `docs/security-model.md` rather than discovered: no confinement below the human,
  ungated client egress, and a record that is *reported* rather than attested.
- **D56 — A client reaches the core as an API consumer, with a session.** The
  dispatch RPC is a WebSocket at `/client` on the same port as the API, and it
  authenticates with the same session cookie; `hello` names the tools and the
  home directory on the machine that will run them, and the account is the
  session's. There is no client socket, no peer credential and no SSH transport.
  The two ends can be on different machines, so the only identity that can cross
  the link is one the host issued at login and the core recorded — a socket
  identifies a uid on one machine, which is exactly what a remote client cannot
  offer. One credential for the API and the channel also means there is no second
  authentication path to keep in step, and a stolen session is bounded by the
  account it belongs to, which is the whole of a client's authority.
- **D57 — A path a tool is given is checked against the account's declared
  surfaces, on the client.** With the kernel no longer confining a dispatch, the
  structured tools would otherwise run against whatever path the model named. A
  manifest declares which of its arguments are paths; the core sends the
  account's expanded surfaces with every dispatch, so a revocation lands on the
  next call rather than the next reconnect; the client resolves each declared
  argument with `realpath` and refuses one outside them, symlinks included. It is
  a guardrail against a confused or injected model and not a boundary — `shell`
  and `python` are not checked, and a manifest that declares no paths is not
  either — which is how `docs/security-model.md` describes it.

- **D58 — Login is an argon2id check against Neuman's own accounts.** The
  client logs in with the account's password; the server verifies the hash on
  the `accounts` row, mints a session and audits the result. There is no root
  helper and no PAM service: nothing reads `/etc/shadow`, so the appliance has
  no privileged component at all. The cost is that the database is a credential
  store — a stolen backup is a credential set — so hashes are argon2id with
  memory hardness, the login throttle stays, and backups are protected like the
  audit chain. The alternative, a client-proved key, is an enrollment ceremony
  (a second credential kind and a per-person binding) and is not what a family
  or a small lab should have to administer. An organisation that wants its
  directory to own identity gets OIDC or LDAP in front, not PAM.

- **D59 — The toolchain is the user's, and the read tools stay.** A dispatch runs
  on the acting human's machine with their own `PATH`, so `ls`, `cat`, `grep`,
  `find` and `git` are there: the question SPEC left open — bundle a known-good
  toolchain per platform, or use the user's — is answered "the user's", and the
  sandbox's tool-directory-only `PATH` that made `fs_read`, `fs_list` and
  `fs_search` necessary is gone. They stay anyway, and each reason is a property
  `shell` cannot have:

  * **They do not prompt.** `shell` declares `mutating = true` because a shell
    command can do anything, so under the default `safe` every `ls` and `grep`
    costs an approval card. The read tools declare `mutating = false`, which is
    the only place "reading my own files needs nobody's consent" is expressed.
    Per-call classification would say it better; it is unbuilt, and until then
    these tools are what says it.
  * **Shape is the product.** `fs_list` summarises a directory past 200 entries
    rather than spending a context on 4,080 filenames (N82); `fs_read` pages
    with a notice naming the offset to continue from, and reports a binary
    file's type rather than dumping bytes; `fs_search` groups hits by file,
    newest first, and bounds itself twice, saying which bound stopped it.
  * **They are path-checked.** Each declares `paths = ["path"]`, so the client's
    tool host resolves the argument against the account's surfaces before the
    tool runs (D57). `shell` cannot be, so these are the only checked way to
    read.
  * **They are separately grantable.** A role can allow `fs_read` without
    allowing a shell or an interpreter.

  `fs_list` is also how the working-directory and attachment chooser lists
  anything: `/api/browse` dispatches it by name.

- **D60 — Opening a session reads and builds only what the reader can see.** Two
  costs sat on the open path and neither was visible content. On the server,
  `EventStore.read` fetched every row — including one `response_chunk` per
  streamed delta and a `request` snapshot of the whole history per step — while
  `project()` discarded both. A projection read now asks for the four kinds it
  can use (`PROJECTION_KINDS`, through the `(branch_id, kind)` index), and the
  image route asks only for `tool_result`. The log is still complete and history
  is still *derived*; this is a read-side filter, not a stored projection, and
  the audit/admin views keep the full read. On the client, `replay` built every
  card's arguments, result, diff and reasoning body even though a reopened
  session stages none of them. The row and its one-line preview/outcome stay
  eager — they are the glance — and the detail is built by `revealDetail`
  through the card's `_reveal` on first open, before it enters either home (the
  live stage or an inline disclosure). `rememberScratch` is
  the one result-side effect that stays eager: a later `fs_read` of a scratch
  path needs the whole transcript's origins even when the card that wrote it was
  never opened.

- **D61 — The session list's order and its pins belong to the server, and "last
  used" is the log's last event rather than a second clock.**

  **The order is a query, not a sort in the browser.** `list_for_account` caps
  at fifty. Sorting those fifty by `created_at` and calling the result
  "recently used" is a different fifty from the fifty most recently used: it is
  correct-looking until an account outgrows the cap, which is exactly the shape
  the search had before `?q=` moved server-side (D52). So `?sort=used|created`
  is a closed set the route validates, the store turns into one of two `ORDER BY`
  fragments, and the client draws what arrives. The same rule retires the
  client's own `rows.sort(...)`: two orderings over one set of rows is one
  ordering that will disagree with the date headings drawn beside it.

  **Pinning is a column, and a PATCH field.** A fourth state on `agent_sessions`
  after open, closed and removed, and for `removed_at`'s reason it is server-side:
  a pin that holds on the laptop and not the phone is not a pin. It has to reach
  the server anyway, because only the server can order before the cap. A field
  on `PATCH` rather than a route of its own — unlike removal — because a pin has
  two values and the client can state the one it wants; `{pinned: false}` is a
  statement no "unpin" route could make differently. The ordering puts
  `pinned_at IS NOT NULL` first and then applies the reader's chosen date, so a
  pin never sinks as it ages; within the pinned section the same date ordering
  applies, which is why the timestamp is not used as a rank.

  **"Last activity" is derived, not recorded on open.** The tempting
  alternative is a `last_opened_at` written by every `GET
  /api/sessions/{id}`, so that reopening a session lifts it. It was declined for
  three reasons. It is a *second* clock: retention already has a definition of
  when a session was last touched — the newest of its creation, its newest
  event and its closing — and two definitions of "recent" in one table will
  drift. It writes on a read, which turns opening a transcript into a mutation
  of the list you are about to go back to, so the row moves under you. And the
  word would then be wrong in the other direction: a session you glanced at is
  not one you used. So the listing reuses the retention expression
  (`_LAST_ACTIVITY`, one string shared by both queries), and the cost is
  accepted and named: **reopening a session does not lift it; sending a message
  does.**

  **The choice of date is per browser.** It is `localStorage`, beside the rail's
  width, and deliberately not on the account: it says how this list is being
  read, not a fact about the person — the same distinction that keeps the rail's
  width in the browser and the theme on the account (D53). The pin is the opposite
  case and lives on the server, which is the whole difference between a
  preference and a fact about the list.

- **D62 — A run's live step has one stage at its foot, and a step change
  crossfades it.** The timeline's rows are the chain; the run's one **stage**
  at the foot shows the step the turn is on. New rows are inserted *before* the
  stage, so the chain and its rail grow upward out of a card that stays put. The
  stage is one fixed height (`--step-h`, `min(40vh, 420px)`); the stage owns the
  border, panel and radius, and the tool card or thinking block inside it is
  content. A step change is two opacity transitions over the same place — the
  incoming detail enters transparent, the outgoing one leaves — and the old
  node is removed after the fade. The stage's box is not touched by a step.

  **A picture shrinks to the card instead of scrolling.** An image result takes
  the height the arguments and the caption leave and is contained in it, rather
  than carrying the 420px cap it used to. The card's height is fixed, so the
  alternative was a scrollbar over the only part of the result there is. The
  cost is scale — a large screenshot is shown smaller than its pixels, and a
  tall one is small — and the picture is still bounded by the card's width, so
  it cannot widen the transcript.

  **Why not a fixed-height card under each row for the live step?** That was the
  first fix, and it removed the *size* difference but not the *motion*: every
  step still mounted one card under its row and folded another away, so the
  transcript moved for 150ms on every swap and the reader's place moved with it.
  A crossfade between two cards at different document positions cannot stay in
  one place; a single stage can. Once the stage exists, the only height a step
  adds is its one-line row, above the card; the card itself is not resized.

  **The stage is only the live step's home.** A history row's detail is mounted
  under that row when pressed, not moved to the foot; a detail is in exactly one
  place, and `pressRow` decides which (D64). The stage is not left open at the
  end of a run: when prose starts, it folds into a **reserved blank** and the
  reply is written into the blank, so the answer does not begin below a card
  that has nowhere else to go. That fold is safe because the blank holds the
  card's height; without the reserve it would be the H-shaped jump this entry
  exists to remove (D64).

  **The stage appears with the first detail**, so the first step of a run is one
  card-height addition; every later step is a row. Pre-reserving the stage would
  show an empty panel before the first step, so the boundary is accepted and
  named. **A short conversation sits at the foot of the transcript**: the log's
  first child takes the free space with `margin-top: auto` while the content is
  shorter than the window, so a new chat begins beside the composer and the stage
  is at the bottom from its first step; once the log overflows the margin is zero
  and the scroll range is the content's own. Without that, a short turn's card
  floated halfway up an empty page. `prefers-reduced-motion` makes the crossfade,
  the row arrival and the fold instant. A blocked call keeps its buttons visible
  because the approve/deny row is outside the scrolling body, which is N89's
  lesson applied to the card. See D41 for the row that owns the detail and D44
  for the run.

- **D63 — A turn belongs to its session's socket, so the client keeps a view per
  open session and the rail grows an Active section above Pinned.** The client
  had one `socket`, one `#log` and one set of turn globals. `resumeSession`
  closed the socket before opening the next one, and `ws.py` cancels the turn in
  the socket's `finally`: clicking another row was a Stop, even though nothing
  on screen said so. The owner is the socket, so the fix is one socket per open
  session — not a second meaning for `interrupt`, and not a server-side detach
  that would let a turn burn budget with nobody able to watch it.

  **A view owns what a turn writes into.** Its socket, its transcript nodes, the
  open agent bubble, the reasoning row, the work run, the tool cards and their
  rows, the scratch origins, the reader's scroll and prompt cursor, the
  branch's directory, attachments and tool selection, its usage frame, and the
  rail state (running, unread, phase, detail, start time). The module-level
  names the renderers use are swapped for the view being drawn — `withView`
  around a background frame, and a stash/load on a switch — so `neuman.js`
  keeps its single-view shape instead of every renderer growing a view
  parameter. The visible view's nodes live in `#log`; a hidden view's live in a
  detached holder and are moved, never rebuilt.

  **Nothing is replayed on a switch, and that is the point.** The first shape of
  this plan buffered frames and re-rendered on return. It would have landed on
  a reconstruction: the last complete reply flashing into place before the live
  step staged over it, or the whole history redrawn and the reader's place in it
  lost. A running session has to open on the step it is on — the live tool card,
  the thinking card, the streaming answer — with the history above it exactly
  where it was left. Moving the nodes keeps the stage, an open detail, a chosen
  scroll position, the tail reserve and its pin, and the streaming text without
  touching the renderer. A running view is pinned to its live edge on open
  because that is what the switch asked to see; an idle one restores where it
  was. `#log` stays the one scrollport, so the geometry, the live stage and the
  reserve from D62/D64 are unchanged.

  **Active is the account's working set, and it is above Pinned.** A session is
  in it while a turn is running in it, while a reply is newer than the last time
  somebody looked at it, or while it was opened or last had anything said or
  done in it inside the window the account set — `active_minutes`, 30 by default
  and 0 for keep nothing, labelled **Keep sessions active for**. Membership is
  the core's answer, from `GET /api/active`, so every window and every device
  draws the same list; this window contributes one word, `open`, for the session
  it has on screen, and `idle` for the rest. That is not an arrangement of
  history, which is what Pinned and the dates are, so it goes first. The row is
  two lines: the title keeps the rail's width, and a state line beneath it
  carries a dot, a word and what the turn is on. There is no clock — the row
  says what is happening, and how long it has been happening was a counter
  ticking at a row nobody was reading. `thinking` and `running` breathe in the
  accent, `needs you` is amber and says what it is asking for, `finished` is
  green and reads `reply ready` until somebody reads it; the word carries every
  state, so the colour is never the only signal. The section heading carries the
  count that matters — one needs you before two running — and the folded rail
  carries the same count on the grip, because that is the one surface left when
  the list is out of sight.

  **The order is the conversation, not the opening.** A row sits where the last
  thing said or done in it puts it, so clicking an old session adds it to the
  working set without lifting it over the one being worked in. The clock that
  keeps a session *in* the set is the later of that activity and `read_at`,
  which is the opening; the clock that decides *where* it sits is activity
  alone. Two clocks, because they answer two questions, and collapsing them was
  what made the old rail a list of whatever had been clicked most recently.

  **Unread is the account's, like everything else here.** `read_at` is a
  watermark on the session row: a reply is unseen while a `response_final` in
  the log is newer than it, so `finished` is derived rather than stored, and
  opening the session — in any window — moves the watermark. That is what makes
  a reply read on the phone stop being unread on the laptop on the next poll. It
  is a write, so it is a route (`POST /api/sessions/{id}/read`) rather than a
  field: nobody marks a session unread, which is the same distinction that makes
  removal a route and a pin a field. A reply that lands in the session already
  on screen marks itself read, because somebody is looking at it.

  **A view is kept warm for a window after it stops being live.** Dropping an
  idle view the moment the reader looked away meant every return was a `GET`, a
  rebuild and a scroll position lost. The fix is not a second cache but a
  lifetime: the same `active_minutes`, 30 by default and 0 for keep nothing,
  bounded at a day. It is an account preference like the palette (D53), because
  it says how the person works rather than how this browser is set up — but here
  it governs **memory**: which transcripts and sockets a window is willing to
  hold. It does not decide what the rail lists. That is the core's answer, and a
  session the core has aged out is gone from Active in every window whatever
  this one still has in memory. A turn running and a reply nobody has read are
  exempt from the sweep, because neither is a time since use and a clock is not
  an answer to either. A parked view keeps its nodes and loses its socket, since
  an idle session has nothing to listen for; a sweep retires what has expired.

  **Lifecycle is where the cost is bounded.** Switching away from an idle view
  closes its socket and drops its nodes; there is nothing to preserve, and an
  Active section that kept every session ever opened would be a tab bar with no
  close. A running view keeps its socket, so the server's cancel-on-vanish rule
  still means what it says: the reader left, the turn did not. A turn that
  finishes in the background closes its socket, keeps its transcript, and stays
  active as unread until it is looked at. A view waiting on an approval keeps
  its socket, because the turn is blocked rather than merely alive. Clicking New
  session keeps every running and unread view and opens the hero. Removing a
  session is the one deliberate cancellation.

  **The socket owns the turn's life; the branch admits only one.** The socket
  staying open is what keeps the turn alive; a page unload closes every socket
  at once, so a vanished client still cancels every turn it owned. What a second
  window may not do is start a *second* turn while the first is running. The
  branch is the unit that admits one turn: `Services.turns` registers branch → running
  turn, `turn_in_flight` consults it on any connection, and a turn that finishes
  removes only its own entry. Without that, the second turn's first request
  projects the first turn's assistant message before that turn has written its
  `tool` replies, and the provider refuses the half-answered history — then
  refuses every later request on the branch for the same reason (N179). The
  registry also answers which account is running where, which is what the
  overview reports. It does not widen what a window can see: a turn running on
  another window's socket is reported to the operator and refuses a second turn,
  but the second window holds no transcript of it.

  **The rail reports the account's work, not only this window's.** What a window
  holds is its own — views, sockets, transcripts, scroll, composer — but what is
  *active* is not: a turn belongs to a branch and the branch to the account, so
  the core is the only side that can answer it, and it gives one answer for
  every window and every device. `GET /api/active` composes it from three facts:
  the in-memory turn registry, the session's last activity, and `read_at`, which
  opening a session moves and which also decides what is unread. The rail draws
  that list in that order and adds `open` to the one it has on screen. The row
  is a pointer and not a transcript: clicking it opens that branch from the
  projection, which is the log so far, and the live step and the streaming text
  stay with the socket that owns the turn. **The prompt is the exception, and
  answering it is the point.** A blocked call's card is served on
  `GET /api/branches/{id}/pending` — the tool, the arguments the model asked
  for, and the preview the write resolved to — so the window the person happens
  to be looking at can answer it. The preview is *carried* from the wait and
  never re-derived: resolving one is a second dispatch to the human's machine,
  and a second one could describe a different write than the call the verdict
  applies to. That is also why it lives in the inbox rather than the log — a
  preview is an act that did not happen, and an entry for it would make the
  record claim two writes where one occurred. The verdict is the account's and
  the inbox is the branch's, so any socket on the branch can deliver one; the
  card opens one if this window has none, which also makes an approval
  recoverable after a dropped connection. When the verdict lands elsewhere the
  buttons go, because a card still offering Approve and Deny for a decided call
  is N89's bug in a second window. A session this window *does* hold is folded
  into its row rather than drawn beside it, and its phase comes from its frames
  because those are strictly finer than a four-second snapshot — two answers to
  one row is how a rail starts to flicker. The alternative was a
  `BroadcastChannel` between windows, refused for the reason `removed_at` was
  moved out of `localStorage`: true of one browser, false on the phone, and
  wrong again after a reload.

  **One race is closed in the send path, not left to luck.** A message typed in
  one session can wait on a connection while the reader opens another; the send
  therefore names its view's socket and marks the view running before it waits,
  so the message cannot be delivered to the session that came second and the
  view cannot be dropped as idle while its handshake is in flight. The
  composer's directory, attachments, tool selection and usage are per view for
  the same reason: they describe the conversation the next message will run in.

- **D64 — A fold reserves the tail, and a history row opens in place.** The
  stage fixed the live step swap and made the card a singleton. That was wrong
  in two ways the reader feels: a press on an old row moved that row's detail to
  the foot and scrolled to it, so history could not be inspected where it lives;
  and the finished turn's last card stayed open, so the answer began below a
  card that had nowhere else to go. The final shape is a hybrid. The live step
  is the stage (D62); every other row is a disclosure under itself
  (`openInline`), and `pressRow` is the one dispatcher. A detail is in exactly
  one home, `_mounted` is `"stage"`, `"inline"` or null, and the node is moved,
  never copied (D32).

  **The end of a run folds the stage into a reserved blank.** The blank is a
  zero-height element at the end of the transcript; every append goes in front
  of it. When prose starts, the stage's height is added to the reserve and the
  card folds; the reply bubble is appended after the run, is flush with it (the
  10px column gap is itself a height the reserve has not funded yet), and is
  written into the blank. The invariant is a **height**: the span from the first
  content child to the last, plus the reserve, is held constant. A position
  cannot be used — a short transcript is bottom-aligned by
  `#log > :first-child { margin-top: auto }`, so the reserve's top is pinned to
  the window and does not move when the content shrinks. `settle` keeps the pin
  through the frame the fold starts on, when the target is exactly zero and the
  card has not moved yet; dropping it there was the first bug (N155). Content
  growth consumes the reserve by exactly its height; when the reserve reaches
  zero the ordinary tail-following takes over. A reply shorter than the blank
  leaves the transcript above the composer, so `done` closes the remainder with
  `releaseReserve`.

  **Two scroll states need two mechanisms.** A short transcript has no scroll
  range to clamp, so the reserve grows frame by frame as the card shrinks and
  the auto margin stays still. An overflowing one does: if the reserve is a
  frame behind the card the browser clamps the scroll position by the height the
  card has already lost, so the card must be funded *before* it shrinks and the
  fold must not settle until it is out of the grid (N155). The reply bubble's
  first token is held until that fold lands, or the first line would ride the
  card's moving bottom edge up its whole height. The log has
  `overflow-anchor: none`, and its scroll listener reacts to an upward
  *direction*, not to distance: a clamp from a layout change is not the reader
  leaving the tail. `prefers-reduced-motion` makes the fold, the release and
  the crossfade instant.

  **The wait is not a transcript event.** `waiting` is a state; the rail carries
  it, the composer's Stop button carries the clock, and no line arrives and
  leaves in the log (N151). **A reopened session has no stage**: every row is
  history, so every row opens in place; the live stage exists only while a turn
  is running.

- **D65 — The person gets a prompt tier, ordered by specificity rather than
  authority.** Three tiers of prose can steer a turn, and the middle one was
  missing: the operator's prompt speaks for the appliance, the working
  directory's `AGENTS.md`/`CLAUDE.md` speaks for the work, and nothing spoke for
  the person. A style preference — "keep replies short, bullets for steps" —
  therefore had to be repeated in every session, or written into a repository's
  conventions file, which applies to everyone who works there and is not where
  one person's preference belongs. Every comparable tool already has this tier:
  Claude Code at `~/.claude/CLAUDE.md`, Codex at `~/.codex/AGENTS.md`, Cursor's
  User Rules, ChatGPT's custom instructions.

  **It is one more section, never a replacement.** Open WebUI's per-account
  field *is* the system prompt for that account's chats; copied here it would
  drop the identity sentence and the untrusted-output rule, which are the two
  things a generated prompt cannot lose. So the account's text sits between the
  operator's and the project's, before the computed reach, and the section says
  where it stands: above the operator's general house style, below the project's
  conventions for this directory. Specificity is the ordering, not authority —
  the operator's real authority is policy, which is structural, and its text is
  context and voice. The precedence is stated rather than left to the model
  because instruction density degrades adherence (IFScale measured the best
  frontier models at 68% on 500 simultaneous instructions, with a bias toward
  earlier ones) and because resolving conflicts by declared priority is the
  premise of the instruction-hierarchy work.

  **Trusted, but not a boundary.** It is not wrapped in the untrusted
  delimiters, unlike the project's text: the user is the principal this
  appliance acts for, the text is theirs, and wrapping it would tell the model
  to disregard the preferences the field exists to deliver. That is not a claim
  that a browser session is as trustworthy as a root-owned file — this is the
  first persistent prompt text writable from a session cookie, and a stolen one
  could plant text that applies to every later turn. What bounds it is what
  bounds everything else: every dispatch is re-gated against live policy, so the
  worst case is in-reach behaviour, not reach. It is not audited (D8: the chain
  is appended by core operations, and this is a preference), and it does not
  need to be — the text appears verbatim in the system prompt of every request
  the harness logs, which is where a reader would look for it.

  **Bounded at 2,000 characters.** Roughly 500 tokens, inside the 2,048-token
  floor of the prompt overhead `_prompt_budget` reserves even on a 16k window,
  so the feature cannot by itself make the arithmetic wrong. The route answers
  400 with the limit and the column's CHECK is the backstop — one number held
  together across the store, the markup, the client and the migration by a
  test. No per-session copy: the chat itself is the per-turn override, and two
  places to set one thing invites drift. No operator switch to disable it, and
  no model-written memory: each would be its own decision, and neither is needed
  for the preference this exists to carry.

- **D66 — The thinking card is read on a clock of its own.** The card used to
  re-render once per animation frame and follow its tail instantly, which is the
  right rate for a crossfade and the wrong one for prose. A thought streams at
  sixty updates a second; the eye cannot parse that, so every token reads as a
  small disturbance rather than a phrase, and every new line moved the card 22px
  in one frame — the "jumping line by line" that smoothing a scroll cannot fix,
  because the jump is the size of the smallest thing the browser can lay out.

  **Content moves on a reading clock, position on a rendering clock.** Text is
  released in whole-word chunks — about one line at the card's measure, so ~60
  characters — every 200ms, and each chunk blooms in over 260ms. The position is
  eased underneath it by an exponential in *time* (`1 - e^(-dt/τ)`, τ 140ms), not
  a per-frame constant, so the motion is the same on a display that refreshes at
  a different rate. A wheel up inside the card still hands the scroll back to the
  reader, compared against the position this code last wrote rather than against
  "is it at the bottom" — an eased follow is never at the bottom while it is
  moving, which is N141's lesson applied one scale down.

  **The live path appends, and that is load-bearing.** A bloom only works if the
  nodes already on screen are left alone: a renderer that clears its container
  replays the animation on every node on every update, so the card shimmers
  instead of settling. So the live thought is built by appending
  `inlineMarkdown` a chunk at a time, and `settleThought` draws the finished
  thought once with `renderMarkdown` — which is where a list, a fence or a table
  the live path showed as prose becomes the block the model wrote. For prose
  nothing changes at settle, so the swap is invisible. One consequence worth
  naming: a markdown marker split across two chunks stays literal until the
  thought settles, where the old renderer corrected it within a frame.

  **The lag is not tunable away, so it is paid for with the bloom.** The tail is
  the maximum scroll position, so a follow can never lead it; every pixel of
  smoothness is a pixel of the newest text sitting below the fold. Measured on
  the mock-up this was chosen from: τ 60ms moves on 64% of frames with a 40px
  lag, τ 200ms 96% and 58px, τ 300ms 100% and 69px. τ is therefore chosen against
  the fade rather than against the scroll — land the follow inside the 260ms
  bloom and the reader never sees the text it is still catching up to. Chunk
  size does not buy smoothness either: the tail steps in *lines*, so chunks
  under a line change only how the text appears, and chunks over one make each
  step taller (240 characters measured a 116px lag).

  **A backlog is skipped, not crawled.** If a tab was hidden or the model
  outran the clock, more than four chunks of text can be queued; the surplus is
  dropped rather than played back, because nobody read it, and the settled
  render still has every character.

  **What this deliberately does not do.** A sticky opening line and bionic
  reading were both built in the mock-up and left out here. Sticky needs its
  header to be *one line* — a sentence-sized header fills its own paragraph and
  never pins — and it fights the band for the top 30px, which costs it most of
  the window it would be visible for. Bionic spends an element per word on the
  render path that appending had just made cheap, and it spends bold, which
  already means `**emphasis**` here.

- **D67 — The client's local account is the executor, not the principal.** The
  dispatch RPC names no account from the client side. `hello` carries the two
  facts the core cannot learn from the session — the home directory on the
  machine that will run the tool, and the tools that installation can run — and
  the principal is the session's, resolved against the host's directory at login
  (D16, D56). The local account that holds the client process is where the reach
  comes from; its name is not an identity Neuman checks, and it need not match
  the account on the appliance. A Windows or macOS client has no Linux account
  name to send, and a workstation whose local account is spelled differently is
  the same person.

  **The password is the binding, and the core cannot attest the uid.** Whoever
  can log in as the account holds its session; the server cannot see which local
  account executes the dispatch. That was already true across two machines — two
  matching names prove nothing about two uids — and it is the limit the security model
  states rather than hides. A stronger binding is an enrollment (a key the client
  proves), which D58 considers and does not take.

- **D68 — Runtime configuration is Neuman's, not Nix's.** The policy/model
  baseline, the prompt and every administrator change live in the database:
  `settings` holds the baseline and the prompt, `accounts`/`groups` hold the
  people and their membership, `policy_grants` holds the role grants and the
  overlay remains the last policy layer. There is no seed. A fresh database
  starts with an empty baseline and an empty prompt, and the first
  administrator, created over the local socket, makes everything else in the
  administration page or with `neuman-admin baseline set`. An admin adds a
  user, changes a role, adds a provider or model, or replaces the prompt
  without rebuilding anything: policy is re-read per step, the catalog per
  turn, and the prompt on the next turn. Deployment settings — bind, TLS,
  database URL, paths, the accelerator — stay in the module and the
  environment, because changing them is a process restart, not a policy
  change. *Amended by D78:* which local models run is no longer a deployment
  setting; it is the baseline's, like any model. A baseline write bumps every account's policy generation; the panel
  and the local socket are two transports over the same operations (D9).

  The module used to seed the baseline and the prompt on first boot, under
  `services.neuman.bootstrap`, so an appliance installed from Nix came up
  configured. It was removed because a seed read once is a second source that
  stops being true after the first edit, and because the declarative copy was
  buying nothing an operator could not do in the panel — while costing a
  confusing rule (a seed added after first boot is silently ignored) and a
  catalog that existed only at evaluation time (D76).

- **D69 — The overview reports identity, and labels every value with when it
  was read.** The operations card answers "which code is this, how long has it
  been up, is anything disagreeing with itself" — not "how many tools are
  loaded". A count of the registry cannot change without a restart, includes
  diagnostics no user can see, and says nothing about what an account may call
  because gating is decided per dispatch, so it was three ways misleading as a
  headline; it belongs beside the policy chooser, where a grant makes it mean
  something. Two consequences are load-bearing. The build shown is
  `NEUMAN_BUILD_ID`, the same value stamped into every turn's audit entry, so
  the card and the chain answer "which code served this turn" with one fact
  rather than two. And the schema is reported as a pair — applied and expected
  — so a migration that did not apply is a disagreement the client can draw,
  rather than one number the reader has to already know the right answer to.

  Uptime is measured on the monotonic clock and carries the wall-clock start
  beside it: an NTP step must not be able to make an appliance look fresher
  than it is. Anything the card cannot check cheaply is shown with the time it
  was last checked rather than as a bare value, because a number with no
  timestamp is a rumour — which is also why the card is not polled yet rather
  than polling and saying nothing about when it last succeeded.

- **D70 — Usage is read from the ledger for the roster and from the log for a
  session, and money waits for a per-call record.** Three questions, three
  records, and none can answer another's: `usage_daily` holds a settled and a
  reserved total per account per day, so it is what the Usage section draws for
  today, for the trend and for the cap; `session_events` holds the provider's
  own `usage` on every `response_final`, so it is where a conversation's token
  total is summed from, because the ledger has no session in it. "Last call"
  is the latest day row's `updated_at`, read over the whole ledger rather than
  the trend window — an empty fortnight is not the same fact as no call ever —
  and every reservation moves it.

  **Cost and per-model are deliberately absent.** Neither is in the ledger, and
  summing them out of the event log would be a figure recomputed at today's
  declared rates and lost when a session is purged, drawn beside a total that
  is a real measurement. The right home is a per-call usage record — account,
  session, model, tokens in/out/cached, cost and whether it was estimated —
  written when a call settles, with `usage_daily` as the rollup the cap
  enforces. D37 already names a cost column on the ledger as where a surviving
  figure belongs; a call row is that column plus the model. Until it exists the
  tab shows tokens only, which is what the ledger can defend.

  **The ledger is not yet complete.** Summarisation calls are made by the
  context manager, which drops the usage chunk and never reserves or settles,
  so compaction spends tokens that reach neither the ledger nor the event log.
  The card says so rather than inviting the reader to trust a total that is
  slightly low. Metering that call belongs with the per-call record, not after
  it.

- **D71 — The account is the unit, and its page is a view swap.** The Accounts
  section is a searchable, server-paged roster; opening one account replaces the
  list with that account's page — Overview, Policy, Tools, Limits, Usage,
  Sessions — and Back, or Accounts in the rail, returns to the same search and page.
  An expanding row was
  rejected because the detail is a policy editor, a tool matrix and two
  histories at once; at five hundred accounts an expansion would push the list
  off the screen and turn "the next person" into a scroll hunt. The list asks
  for a page rather than the appliance (`?q=&limit=&offset=`), and groups,
  today's tokens and session counts are single queries over that page. The
  row's Today column is a token count and not a share of a cap, because the cap
  is composed policy and composing it for every row would put fifty policy
  reads behind one table; the account's own page composes once and shows the
  cap there.

  **Policy is read as the set in force; exceptions are edited as deltas.**
  Policy, Tools and Limits are read-only views of the composed result until the
  operator presses "Add exceptions", and a save writes only the difference from
  what the layers below give: a list field writes `extra`/`remove`, and a scalar
  is written only when it differs from the inherited value. A field left alone
  is carried forward from the overlay as written, so saving one field still
  cannot clear another. `only` is reserved for an explicit pin — a statement
  that the field stops following its roles — rather than
  being the ordinary spelling of an edit. Writing the whole effective policy
  would freeze every field on the first save; writing `only` for every touched
  list would do the same for that field.

  **The create form is a dialog.** It was a permanent card taking a third of
  the tab for a task most visits do not perform, which is also why the list had
  less width than it needed. The same dialog shape carries a password reset and
  the group checklist, so the three account mutations a visit actually makes do
  not each need a different corner of the page.

- **D72 — The baseline is edited by layer, and a save is conditional on the
  document it read.** The old Policy tab put defaults, roles, users, providers,
  models and the prompt behind one textarea. Each layer now has its own place —
  Roles (with Defaults and grants), Models, Providers, Prompt — and the
  baseline's `users` entry carries only role assignments; per-account changes
  are overlay exceptions, not a second baseline editor. The sections are still
  one JSONB document in `settings`, so a form writes
  the whole document plus the copy it started from; the server's `UPDATE … WHERE value = expected` refuses a stale
  write with 409 rather than merging, and the client reloads. A read followed by
  a write would let two tabs both start at version N and the second erase the
  first's role without either seeing a conflict.

  **A fragment's unset scalars are not empty ones.** The role and defaults
  editor carries an inherit option per scalar, because a scalar overrides: a
  missing one inherits from the layer below, and writing it as a value silently
  changes every account the layer reaches. A list carries no such toggle. A
  baseline list only adds (subtraction is the overlay's), so an empty list and a
  missing one compose alike; the form writes a list only when it grants
  something, so provenance never names a layer that added nothing.

- **D73 — A role is the ordinary grant; an account's own override is its final say.** An
  account holds roles either by declaration on its entry or through a group
  grant, and the account page's Roles card is where the first is added or
  removed. A role that arrived through a group is removed on the group, not by
  deleting a name the account does not hold; an account's own override replaces
  what its roles give for that field (D87). The
  baseline's `users` entry carries only role assignments; `users[].policy` was
  removed because it was a second authoring surface that could override roles
  without a visible diff. The panel writes account-level changes only to the
  overlay. A role can be
  renamed:
  the baseline key, every declared reference and every group grant move
  together, because a grant left on the old key is a silent revocation. Policy,
  Tools and Limits show where each field comes from, and their controls are
  disabled until Edit is pressed. The Roles tab's "Inherited by" column
  is the blast radius — declared names and groups — so editing a role shows who
  will receive the change.

  **Onboarding assigns roles before the account exists.** The create form
  chooses starting roles and groups; the server composes a draft from the same
  layer order, rejects a role set that disagrees on a scalar, and writes the
  account, its group memberships and its declared roles in one request. The
  preview is composed server-side rather than in the browser: a second
  composition path would drift from `source.py`, and the panel would show a
  policy different from the one it stores.

  Why: assigning a role and then editing every permission on the account is two
  ways to say the same thing, and when they disagree the panel cannot say which
  one the operator meant. The split here is the one the mature systems converged on: Kubernetes permissions are purely additive and
  have no deny, AWS makes a deny explicit and loud, Salesforce permission sets
  only add to a profile, and Keycloak shows direct and inherited mappings side
  by side. None of them presents "edit every permission of this person" as the
  primary workflow; granularity comes from composing small roles, not from
  editing the person.

- **D74 — The product is two programs; the NixOS appliance is a distribution.**
  `neuman-server` is the Linux service around Postgres; `neuman-client` is the
  executor, UI and browser on the human's machine. The NixOS module and the
  Neuman appliance image are deployment channels, not the architecture. The
  client is the front door — it serves both the chat and admin UI — so a
  server-only install is administered by `neuman-admin` over the local socket
  or SSH. Deployment settings stay in Nix/environment; runtime policy stays in
  Postgres (D68). Windows is out of scope until a customer asks; it is not a
  platform the current host seam pretends to support.

- **D75 — The macOS client is a native arm64 package, not a Nix install.**
  Apple silicon only, macOS 14 or later. The first artifact is a signed and
  notarised `.pkg` with a user LaunchAgent and a bundled Python runtime; it
  does not require Nix, Python or a terminal on the customer's Mac. Chrome or
  Chromium must already be installed. Nix remains the Linux/appliance build and
  a reference build for CI. The client's state is under
  `~/Library/Application Support/Neuman`, and its browser bridge is a Unix
  socket under `$TMPDIR`, not `XDG_RUNTIME_DIR`. The server stays Linux.

- **D76 — The presets are data the server ships, and it holds entries to them.**
  The providers and models Neuman supports are JSON under
  `neuman_server/presets/`, one file per entry, validated against
  `contracts/schemas/presets.schema.json` and refused whole at startup if any is
  wrong — they are part of the release, so a bad one is a broken build rather
  than a missing tile. The administration page offers them as tiles: a provider
  is added with its key, a model by choosing it on a provider the appliance has.

  An entry added from a preset records it (`preset`), and D39's split follows it
  into the database. The facts — the provider's id, the window, vision, the
  reasoning dialect and the levels a route forwards, a preset provider's URL —
  are refused on any baseline write that contradicts them, whichever route made
  the write, and re-applied at every startup, so a fact corrected in a release
  reaches an appliance with the upgrade rather than when someone thinks to
  re-add the model. Policy — reply length, prices, the default level — is where
  an entry starts. A hand-declared entry stays possible and carries every claim
  unchecked, which is the exposure D39 describes.

  On the server rather than shipped with the client, because the server is what
  refuses a wrong fact: a list the client drew from could only offer, and the
  check is the point. And not in Nix, because the catalog stopped being Nix's
  with D68 — a preset that exists only at evaluation time cannot be offered to an
  operator who has no Nix, which is every macOS install and every appliance
  configured from the panel. Adding a model still grants it to nobody.

  *Extended by D78 and D79:* a model preset may carry a `local` route — a
  download pinned to a Hugging Face commit and typed policy. A local entry's facts are held to its
  preset like a hosted one's; its window is the operator's, because it is what
  the runtime is started with.

- **D77 — A provider key is entered once and kept as a file the server writes.**
  The panel takes the key in a request body; the server writes it to
  `<state>/secrets/<provider>.key`, `0600` in a `0700` directory, by temporary
  file and rename, and the baseline holds the path. No response, log line,
  audit payload, database row or backup carries the key, so restoring an
  appliance means entering its keys again — the price of a backup that cannot
  leak them. Tenet 12 holds for the document, which still names a path; what
  changed is that the panel may be how the file comes to exist, not only a
  secret manager. A key file the baseline no longer names is removed after every
  write, so a deleted provider does not leave a credential on disk that nothing
  references and nobody revokes.

  Encrypting keys into the database was the alternative, and it buys a restore
  that brings keys back at the cost of a decryption key the server must hold
  beside the ciphertext — the same file on the same disk, one step further away.
  A custom provider's URL must be https unless it is on this machine, for the
  reason the client refuses plain http to a remote core: the key would cross on
  every request.

- **D78 — Local runtimes are data, run by one unit that builds its own command
  lines.** `neuman-runtime` replaces every unit, socket, proxy and idle timer the
  module used to generate per model, and the polkit rule that let the server stop
  and start them. It is the only unit with the GPU (D29). It runs as `neuman`,
  may reach loopback and nothing else, and can write only its runtime directory.
  Which models it runs is the baseline's: every local entry (one with `local` in
  place of a provider). The server writes those, and only those, to
  `state/runtime.json` whenever the baseline is stored. The host re-reads the
  file and refuses it whole unless every name is a shipped preset that runs on
  this machine and every number is in range; a refused document leaves
  running what was running.

  **The command line is the host's.** It is built from the preset — the files,
  joined to `modelsDir` there, and the flags the model needs — and from typed
  policy: window, offload, speculation, cache type, residency, idle minutes.
  There is no
  `extraFlags`. A stored list of arguments would let whatever can write the
  document hand a process holding the GPU any flag and any file. This is D30's
  "the unit comes from the manifest, never the request", carried to the whole
  argv. The cost is that a model wanting a flag nobody typed is a release, not
  a setting, which is D39's position anyway.

  **Chat is `llama-server` in router mode**, measured on a 3090 against
  one process per model before this was written. The router answers ready in
  4.3 s against 4.2 s for one process, gives the same 0.24 s warm reply and
  holds the same 23,428 MiB. It brings what the host would otherwise write: one
  endpoint routed by `model`, a request that waits through a cold load rather
  than failing, `--models-max`, unload, per-model status, and a per-model idle
  sleep that frees all but ~300 MiB. It also brought something to
  switch off. Started with a real home, it offered every model in
  `~/.cache/huggingface`, each with a download argument. So it runs with its
  home and every cache variable pointed at an empty directory, and no
  `--models-dir`.

  Amends D30 (no `models.json`; "loaded" is the host's status, not a port probe)
  and D32 (Load and Unload are requests to the host, never a unit).

- **D79 — Weights are fetched by a unit that has the network and the weights
  directory and nothing else.** `neuman-fetch` has no GPU. The runtime host has
  no network. The server can write neither the weights nor anything the runtime
  executes: its own unit mounts `modelsDir` read-only, though it sits inside the
  server's state directory, because a server that could replace a file could
  choose what the GPU runs. A fetch names a preset and nothing else; the repository, the commit
  (never a branch), and each file's name, size and SHA-256 are the shipped
  preset's. A file is downloaded to `.part`, resumed with a range request,
  hashed as it arrives and renamed only on a match. A mismatch deletes the
  partial file; it never renames it into place. A file already there under a pinned name is hashed rather than
  fetched again — how weights downloaded by hand are adopted — and one with
  another size or hash is refused, not overwritten: it is somebody's file. A
  verified file gets a record (hash, size, modification time) that the panel
  reads, so "verified" never costs a 16 GB read, and adding a local model
  requires it. A download that would leave less than 1 GiB on
  the disk the database shares is refused before it starts.

- **D80 — The administration page is a rail, not a tab bar.** Eleven sections
  in one horizontal bar wrapped into two ragged rows at an ordinary window, and
  the rule under the second row belonged to no row in particular; every label
  had equal weight, so nothing said that Accounts, Roles and Groups are one job
  and Sessions, Usage, Audit and Events another. The sections stack in a rail
  instead, grouped under Access, Models and Activity, each with an icon so a
  section is found by shape before it is read — and eleven of them cost no
  height in the content, which a bar spends on every scroll.

  Four consequences are load-bearing. The current section is
  `aria-current="page"`, not a class: the state is in the accessibility tree as
  well as in the stylesheet. The page also moves the hash to match, so a reload
  lands where the operator was — written with `replaceState`, because assigning
  `location.hash` fires `hashchange` and a second switch would fetch the
  section twice and leave a second overview watcher running. The rail carries
  the overview's attention items as badges on the section that has to be
  opened: the page already computed "3 errors on Events" and then made the
  operator read Overview to find out. And each section opens with a sticky
  head — its name, one line of what it is for, and the action the section is
  for — because a six-column roster is taller than a window and the reader
  should not have to scroll up to remember where they are; the primary action
  no longer lives in whichever card happened to own it. Overview is the one
  section without a head: the rail already says its name, it has no action of
  its own, and the line under the name could only describe the section — a
  sticky band on every scroll saying that this is the page you clicked.

  An account's own sections are a segmented control rather than a second
  underline bar. They are choices inside a page the rail already owns, and a
  second rule reads as a second page map. The chosen segment is raised off its
  track rather than accent-filled: the rail already uses the accent to mean
  "you are here", and a second filled control competes with it (D71). The rail
  collapses to icons on one attribute set by the operator, which is what keeps
  a 252px column honest on a small window, and the roster drops two columns
  rather than growing a scrollbar for them (N172).

- **D81 — The composed prompt is a view of one composition, not a second one.**
  An operator looking at the Prompt section saw one textarea and had no way to
  tell whether the prompt was empty, unset, or one tier of several — the
  question is answered by the whole prompt, so the page shows the whole prompt.
  `prompt_sections` returns it as `(source, lines)` and `system_prompt` is the
  join over that list, so the page and the model read one composition. The
  alternative — the page inferring where the boundaries are — is a second
  composition path, which drifts, and its drift is invisible because the page
  would look right while the turn was wrong.

  **Only the free text is editable.** The other sections interpolate facts the
  gate enforces: the reach this account has, the tools actually offered, the
  approval mode in force. Those are statements *about* policy, computed per
  turn, and a saved copy of one could disagree with what a dispatch gets — the
  prompt would promise reach the gate refuses, which is the failure N145
  recorded one layer down. What an operator edits is the text that was always
  editable, the operator prompt, and it is still one textarea.

  **What a preview cannot compose, it names.** `AGENTS.md` and the development
  environment are read on the acting machine, through a dispatch (N152), so the
  preview reports them as omitted rather than leaving a gap: a blank section
  reads as "this prompt says nothing here", which is the opposite of a section
  that will be filled by a read. A finished turn's logged `request` carries the
  text that was actually sent, which is where an operator goes to see those two.

  **The labels live in the page.** The server sends section ids and the client
  maps them to prose, because the sentence describing a source is not part of
  the prompt and putting it in the response would make the wire format carry
  two things at once. An id the page does not know renders as the id itself, so
  adding a section to `prompt_sections` shows up in the panel rather than
  disappearing from it.

- **D82 — The appliance's name is a setting, and it is voice rather than
  authority.** An organisation running this for its own people is not called
  "Neuman", and a prompt that insists otherwise reads as somebody else's
  product. The name is one row in `settings`, written with a default at
  startup and set back to that default by the write when the field is cleared,
  so the name in force is never an empty string and no reader has to decide
  what one would mean.

  **It is a name, not a prompt override.** D81 keeps the identity sentence in
  code because it sits above the reach list and states who the agent acts for;
  letting an operator save a replacement sentence would let them delete the
  part that is not theirs to delete. So the sentence stays and the *name* is
  interpolated into it, with whitespace collapsed — here and at the write — so
  a row edited by hand cannot open a paragraph inside a one-line sentence. It
  is edited on one line, never in a textarea, for the same reason.

  **It is edited where it is administered.** The wordmark at the top of the
  console's rail — "Neuman / manage" — is the field: an administrator
  clicks the name, types, and Enter commits. The name is a property of the
  appliance, not of the person reading it, so the control belongs in the one
  window only administrators open, beside the rest of what makes one appliance
  one appliance. A card on the Overview tab used to explain the name in two
  sentences — that renaming grants nothing, that it is voice and not policy —
  above a text box for a word; the explanation was longer than anything it
  explained, and the name is a wordmark, so the place it is drawn is the place
  to change it. Clearing it stores the default rather than refusing, because an
  empty wordmark is someone asking for the name back (N175). Everyone else
  reads the name in the chat window's rail, the sign-in heading and the window
  title.

  **It reaches the door as well as the room.** The name is read from
  `/api/brand` before sign-in, because the sign-in screen is the first place
  anybody reads it and an appliance renamed in the panel that still greets
  people as "Neuman" is the inconsistency the setting exists to remove. That
  route is unauthenticated and answers with one operator-chosen word and
  nothing about accounts, policy or the database. Signed in, the same name
  arrives on `/api/me`, which is where the client already reads its chrome,
  and it names the chat window's wordmark, its own title, and the console's.

  **What it cannot do is grant anything.** No dispatch reads the name. It
  appears in the identity sentence, above the reach list and above every
  computed fact, which is the position where a sentence can set voice and
  cannot appear to qualify capability.

- **D83 — A session has branches, and a fork is a branch.** The rail shows one
  row per conversation: a title, a pin, a removal, a date. A branch is one
  transcript inside it — an append-only log, one turn at a time — and a fork
  copies a prefix of one into a new one. **A fork never adds a row
  to the session list.** That is a schema fact, not a listing query: a second
  row would be a second title, pin, removal and search hit for one
  conversation, and grouping it back together in the listing would make the
  promise something every future read has to remember.

  The alternative considered and rejected was "fork = new session" plus a
  conversation id grouping rows in the listing. It keeps the event layer
  untouched, but it either makes "session" mean two things (the rail row and
  the row in `agent_sessions`) or duplicates the rail's metadata on every
  branch, and the grouping query has to run on every listing and search. This
  model pays a wider mechanical change once — `session_events` is keyed by
  branch, joined to the session by a composite foreign key — and every read
  afterwards is the read it was.

  **What a fork inherits is conversation, not history.** `copy_prefix` copies
  `user_message`, `response_final`, `tool_result`, and the `turn_complete` and
  `compaction` harness events. It does not copy chunks, request snapshots, tool
  proposals, decisions, build ids or turn failures. The source keeps the
  governance record; the child cannot claim an approval happened in it. The
  child's first event is a `fork` marker naming the source branch, the
  boundary and the copied count. `inherited_event_count` is the length of that
  prefix including the marker, so a branch's own events start one row after it;
  that is what labels a branch by its first *new* message and what the client
  uses to place the node at the boundary.

  **The client draws the fork at the boundary, in the transcript.** The
  transcript shows a centred node where a completed turn ends and the next one
  has not begun. For the branch that begins there, its first new prompt is
  immediately below the node, so the transcript does not add a second title for
  the lane. A node is one boundary, not one branch: branches recorded under
  different parents but leaving the same completed turn merge into one node, and
  a child forked at a branch's first prompt is drawn as a sibling at that
  branch's own origin. The parent row in the node's local menu is the branch
  whose turn ended there; the children are the continuations from it. The footer
  branch menu is the whole session's tree, nested by the same display parent. A
  flat list of branches could not show that two alternatives share every word
  above the fork.

  **Editing a prompt is the fork gesture.** The user bubble becomes the editor;
  Send forks *before* that turn and sends the edited text into the new branch.
  The source branch is never rewritten, and the reader does not have to retype
  the message in the composer: the text being corrected is the text being sent.
  The node marks the boundary; the local menu shows the parent and the branches.
  The footer branch menu's hint names the files and messages that already
  happened; the UI does not call a fork a rewind. The client's only fork-creation
  gesture is editing a prompt; the API's `after` and omitted-at-seq forms remain
  for other callers.

  **The boundary is a completed turn.** `turn_complete` is written on every
  exit path, after the last tool result and the reply, so a boundary that
  names one cannot cut between an assistant's `tool_calls` and its answers
  (N116). `before` resolves to the last completed turn at or before `at_seq`;
  `after` to the first at or after it; omitted to the last one. A turn left
  open by a crash is not a boundary, and a fork while a branch is running stops
  at the last thing that finished. The rule lives in
  `decision/harness/boundary.py` and is resolved against the same rows the
  copy reads.

  **A fork carries no authority.** The branch's working directory, attachments
  and tool selection are re-checked against freshly composed policy when it is
  created: a path or a tool the account has since lost is dropped, with a
  warning, rather than resurrected. Policy is re-read every step regardless, so
  the inherited transcript confers nothing. Model, reasoning and approval are
  the source's own choices and travel with the branch; approval is clamped to
  the operator's ceiling at the point of consent as well as at the route.

  **The branch is the log, and the log's settings are the branch's.**
  `working_dir`, `attachments`, `tool_selection`, `model`, `reasoning` and
  `approval` live on `session_branches`, not on the session: a fork may head
  somewhere else, and a person switching branches expects the composer to come
  back as they left it. The session keeps what names the conversation and what
  the rail arranges — title, pin, close, removal.

  **Two branches may run at once.** The append lock is on the branch row, and
  `turn_in_flight` is per branch, so a fork's new branch can start while the
  source keeps running. That is what makes "fork while it works" useful: the
  branch you left keeps going while the new one starts. The session row's
  state is the most urgent of its branches, and the row says which branch when
  it is not the one on screen.

  **Images and search stay session-scoped.** An image is rooted by the session,
  so a fork needs no reference copy and a purge of one branch cannot collect
  bytes another names. The search index is one row per session, folding every
  branch's speech, because the question it answers is which conversation, not
  which transcript. A hit opens the session's most recently active branch.

  **A fork does not roll back effects.** It branches the conversation; the
  files, messages and approvals before the fork already happened. The UI does
  not call it a rewind; the footer branch menu's hint names the files and
  messages that already happened.

- **D84 — The roster's search is fuzzy, where the session filter's is not.** D52
  declined `fzf`-style matching because a scattered subsequence cannot use an
  index and would scan every message of every session on every keystroke. That
  reason does not reach `accounts`: the haystack is a few hundred short names, a
  scan of them is nothing, and an operator often half-remembers a name, so
  `jsm` should find `john-smith`. The roster is paged, so the match stays in SQL
  (a browser-side filter could not find a person on another page), as a `LIKE`
  with `%` between the letters; no extension and no index. **Ranked**, because a
  scattered match is loose: names that start with the query, then contain it,
  then scatter it, then members of a matching group. It does not forgive a
  typo, which would take `pg_trgm` and a threshold to tune. The list follows the
  keyboard at an 80ms debounce, and `refreshAccounts` drops any answer but the
  newest, so a slow query cannot paint over a later one.

- **D85 — A group is authored in one place, and deleting it revokes what it
  gave.** The Roles tab defines what a role is; the Groups tab says who holds it
  through a group, so a group's roles and members are one dialog, for a new group
  and an existing one alike. Grants used to be a free-text form under the roles,
  and membership two `prompt()`s that asked the operator to type a name from
  memory. Members are found with the roster's search (D84) and shown beside a
  control that removes them.

  **Deleting a group revokes each grant through `revoke_role`.** `policy_grants`
  keys on the group's name with no foreign key, and a cascade would leave no
  `policy.role_revoked` in the audit chain, so the route revokes and then deletes,
  in that order: the pool autocommits, and a failure between the two leaves a group
  with fewer grants rather than a grant with no group. A leftover grant is
  inert until someone creates a group of that name, whose members would then
  receive a role nobody gave them. For the same reason `POST /admin/groups`
  refuses a name that exists, and a grant creates its group, so no grant can exist
  that the Groups tab does not show.

  **`neuman-admin` cannot be deleted, and it always keeps an administrator.** It
  carries administrator authority, so its members are edited like any group's, and
  also by the switch on an account's page. Every path that takes that authority
  away (leaving the group, the switch, disabling the account) refuses to leave no
  enabled administrator, because the console cannot then repair itself and the
  only way back is `neuman-admin bootstrap` at a shell. Roles can be granted to it
  like any
  other group. The panel saves a group as a sequence of the existing audited calls
  rather than one new endpoint, because each already checks role conflicts; the
  dialog tracks which writes landed, so a refused save resumes rather than repeats.

  **Accounts, Roles and Groups are one page shape.** Controls float above a card
  of rows, a count sits below it, and a row opens the thing rather than offering
  buttons in columns; removal lives in that dialog. Arrows appear only when there
  is a second page. The Roles table carries the defaults as its first row, because
  they are the layer every role composes on, not a separate kind of thing.

  **An explanation is a tooltip; a warning is not.** Static text about what a
  section does sits behind a `?` beside its heading, in plain words, so the page
  shows the thing. What changes the next action stays on the page: live status,
  empty states, and notices like a password shown once. Tips are a keyboard and
  touch target rather than a `title`, and draw in one fixed element because cards
  and dialogs clip.

- **D86 — An account's groups and roles are removed where they are read, and the
  page says what removing did.** The overview had `Edit groups` and `Add role`
  buttons over checklists, and a `remove` button that could report success while
  the card stayed: a role reaches an account directly and through a group, so
  taking away the direct copy leaves the group's. The two paths are one list now.
  Each group and role is a pill: the name opens what it is, a `×` removes it, and a
  last pill adds one through a search in the page. A role held only through a group
  has no `×`, because the account cannot lose it from here; its dialog offers leaving
  the group instead, when there is one to leave. A role held both ways keeps its
  pill after the `×` and shows `via` the group, so the pill answers why it stayed.
  Removal asks nothing, because it undoes with one press of the same page.

  **A field's source sits under its name and is written in words.** `from
  full-access role` rather than `role:full-access`, which is composition's label
  and not the reader's. Web tools is an On/Off switch, because `network` has
  two states and a sentence per option made a choice read like a paragraph. A list of models says where it comes from once, on the row;
  a model speaks only when it differs. While a section is read-only its add and
  remove controls are absent, not disabled, so the page shows what the account has
  rather than a form it cannot use.

- **D87 — An account's override is the final say on a field.** Policy, Tools and
  Limits are overrides: a field set there replaces what defaults, roles and groups
  give, and a list in the overlay is a plain list that means exactly itself. The
  overlay had three list forms (`extra`, `remove`, `only`) and the panel chose
  between them with per-field "reset" and "pin" controls, and an operator looking
  at the page could not predict which of two accounts would receive a role's next
  grant. One rule replaces all of it, so the algebra and both controls are gone.

  **This reverses what D73 used to say.** It recorded writing a whole list on any
  edit as a mistake, because the first save froze the field against every later
  role change, and such an account looked like one whose role had been edited. That
  cost is real and accepted, and the second half of it no longer holds: every
  field on the Policy, Tools and Limits tabs says `override` where it is read, so a
  frozen field is labelled as one. What remains is the price of "final": **a grant
  taken away from a role does not leave a list the account has overridden.**
  Revoking a person outright is still disabling the account, which is immediate
  (`docs/security-model.md`); revoking one path from a role reaches everyone except the
  accounts holding their own list. A list put back as the roles give it stops being
  an override rather than freezing a copy.

  The way back is one: Clear all overrides, which also drops the user's own
  `auto_accept`. A single field is put back by editing it to match. An overlay in
  the removed form fails closed at parse, and no shim reads it: no released
  version wrote it.

  **The account's tabs are what is in force, and overriding is a step from there.**
  Policy, Tools and Limits read "Effective policy", "Effective tools" and "Effective
  limits", with "Add override" as the way in, and the overview stopped repeating
  them: it says who the account is and what it belongs to. Approval moved from
  Limits to Policy, because the ceiling is a grant rather than a budget, and it and
  each tool are answered by a switch at the far edge of the row.

  **A model wears its maker's colour.** `GET /admin/models` reports each entry's
  preset `logo`, and a filled tag in that colour names the model wherever the panel
  does, so a list of them is read by colour first. Filled rather than tinted, and
  outside the palette, because the colour is the maker's: a tint of these blues is
  invisible on a dark panel.

  **An account's usage is a week on one scale.** The ledger is per day, so a bar is a
  day and there is no shape within one. Every bar is measured from zero against the
  busiest day, rounded up, which is what lets one day be compared with another. The
  cap is drawn only when it falls on that scale, because stretching to reach a
  distant cap would flatten the week, and it is stated in figures above the chart.

  **A role takes a stance on a field or it does not.** "Inherit" named a mechanism,
  and a "Default" option made not choosing look like a choice. A switch in the role and
  Defaults forms holds only real answers: pressing the chosen one again clears it, and
  the row greys out as not in play, saying what then applies (`Not set · defaults give
  Yolo`). The server sends Neuman's built-in values with the baseline so the Defaults
  form can say the same. It is the Policy tab's form, with switches, path lists and
  tags, so a role reads the way an account does.

  **The panel does not narrate edits.** A sentence at the top of the page repeated
  what the row, pill or table had just shown. `note()` says what went wrong, and
  what the page cannot show: a password was set, a key was replaced, a model waits
  on the runtime or on a role.

- **D88 — `preserved` is a fourth dialect, because replay and the envelope are
  separate facts.** `reasoning.py` used to say a provider that takes `thinking` on
  the way out wants its thinking back on the way in. Kimi K3.1 Pro breaks that:
  Moonshot's guides say reasoning is always on, `thinking` should not be passed,
  `reasoning_effort` takes low/high/max, and the complete assistant message
  including `reasoning_content` is required in tool loops. `effort` would drop the
  thinking the provider requires, and `budget` would send a parameter the provider
  says not to. Either would be a false fact in a preset, which is the failure the
  catalog exists to prevent (N115). `preserved` sends what `effort` sends and
  replays what `budget` replays. OpenRouter documents `reasoning_content` as an
  accepted alias on assistant messages, so the route needs nothing different.

  **Declared by the dialect rather than a replay flag beside `control`.** A flag
  would make four combinations out of what is, today, three shapes and one
  variation, and `reasoning.py` already keeps every answer to a dialect question
  in one place. A second model with this shape declares `preserved` and needs no
  code.

  **No `none`, though OpenRouter lists it.** Its `/models` reports `none` as a
  supported effort and `mandatory: false`, while the vendor says the model always
  reasons. The level list is where a model says whether it can be turned off, and
  this one says it cannot until a turn shows otherwise.

- **D90 — A tile is the object, and the administration page keeps no catalog table.**
  Models and providers were a grid of tiles to add from and a table beneath it to
  read, edit and delete from. The same entry appeared twice, and neither place
  held the whole of it: the table could not say a model was on disk, how far its
  download had got or whether it fits this GPU, and the tile could not say what
  it cost. An operator kept the two in step by eye, and edit and delete were
  buttons in a table cell whose row was also where a wrong number showed up.

  So the tile is the object. Pressing the tile of a model or provider that is
  already here opens its panel: what the preset fixes about it, drawn as prose,
  then every setting the operator may change, then the delete. Which settings
  are drawn is the preset's answer — a model on this machine has a window, an
  offload and a residency, and a hosted one has its prices — so one panel serves
  both kinds of model instead of the two forms the table's one edit dialog was
  becoming.

  What the table carried is not lost but moved: a preset added under several
  names is chosen from a list at the top of the panel, a second entry is one
  press at its foot that opens the add flow, the warning about a model no role
  grants is the card's last line, and a provider that still serves a model
  declines to be deleted in its own panel. What is given up is one screen of
  every added model at once; the filters and the tiles answer that, and a page
  one press deep is cheaper than two sources of truth.

  The same reasoning ordered the rail to match the setup: a provider holds the
  key, a model is chosen on one, and the prompt is added after both.

- **D91 — The way to the administration page is Manage Neuman, on the chat
  rail.** It was a button called Administration in the Settings sidebar, under
  the tabs, which got three things wrong. Settings is the account surface, and
  this is the one thing reachable from it that is not about the account. It
  sat in a `tablist` as a non-tab that looked like neither a tab nor Sign out,
  and pressing it left the window. And "Administration" names a department, so
  it did not say what the reader was about to do or that the scope was every
  account rather than their own.

  It is a link at the foot of the session rail now, a row only an administrator
  gets, because the administration page's own rail ends in Back to chat: the
  door and the way back are the same spot on the same edge. D49 does not place
  it, since it governs no region of this window; that symmetry does. The cost
  is that a folded rail hides it, which is acceptable for a page opened now and
  then. The account chip did not get it because that would bring back the
  two-item menu D51 removed.

  The label is a verb and the appliance's name (D82), so a renamed appliance
  reads "Manage Acme", and the page's title, sign-in heading and subtitle say
  the same. "Administrator" stays the name of the authority and "the
  administration page" the name of the page in prose and in `/api/admin`: the
  word was wrong as a button, not as a description of the group.

- **D92 — A task is a standing approval, not a principal.** A task is a row an
  account owns: a workspace, inputs, an ordered list of steps and a schedule
  (D95). It runs as that account, on that account's client, under that
  account's Unix user.
  The alternative — a service account per task — was the first shape tried for
  the a production site's sweep, and it made the operator create a Unix user, a
  client unit, a login and a sandbox before the first run. Three of the four
  sessions that attempt produced failed on that setup rather than on the work.
  A per-user task keeps the principle's first half literal ("runs as the human
  it acts for") and removes the second identity, at the cost that the envelope
  is a Neuman-layer narrowing rather than an OS sandbox. Work that needs the
  stronger boundary still uses a service-account client, and that client should
  become a Neuman-owned option rather than a hand-written unit.

- **D93 — An agent step's envelope narrows the owner's policy; it never
  grants.** The envelope is a policy fragment (tools, paths, models, network,
  step cap) the owner authors for each agent step of a task. At authoring it is validated against the owner's composed
  policy; at run time `intersect` keeps the narrower of each field on every
  step, and `policy_for` returns that intersection to the gate and to the
  client's path check. A tool the owner lost, a path they can no longer reach,
  or a model they were un-granted is gone from the next run without a second
  write. **A task run is pre-approved; `auto_accept` is not consulted.** That is
  what a task is: the human approved the job once, not each call. The exception
  is `critical`: a task has no inbox, so a critical call is refused before the
  approval wait rather than blocking for a person who is not there. The
  authoring check refuses a critical tool too, so the run-time refusal is the
  second layer, not the only one.

- **D94 — The server owns the task clock; the client executes.** A due run is
  created by a server-side loop, which owns `next_run_at` and the run rows. When
  the owner's client is connected the server walks the steps: a run step is one
  dispatch (D96), an agent step a session, a branch and an ordinary harness
  turn — a session that belongs to the run, not to chat (D99). The client needs
  no task-specific surface because it already executes
  every dispatch (D55, D56). When no client is connected the
  run is `waiting`, and after the catch-up window it is `missed`. The
  alternative — executing tasks on the appliance — would put user-reach code on
  the server and undo D55. The cost is stated in `docs/security-model.md`:
  "scheduled" means due, not guaranteed at that second.

- **D95 — A task is a recipe of steps, not a workflow graph.** The things a
  person thinks in — trigger, inputs, steps, outputs, notify — are task-level
  settings around an ordered list of at most ten steps, each a `run` step (a
  command, D96) or an `agent` step (a harness turn in its own envelope, D93).
  Steps run one at a time, in order; the only control flow is a step's `run_if`
  (`ok`, `ok_or_budget`, `always`). There are no branches, loops, retries or
  error edges, because each would be a second execution model beside the agent
  loop, and judgement about what to do next belongs to an agent step, which
  already has one.

  The first version had exactly one agent step and called a code node `shell`
  with a nicer face. Running the a production site's update on it showed the
  cost: the work around the judgement — API syncs before, a checker after —
  either went into the instruction, where the model spent tokens running
  scripts it could not be trusted to run in order, or into a scheduler outside
  Neuman, where a 06:00 timer and a 06:15 task agreed by clock. The useful split
  is deterministic steps that are code and one step of judgement, and the
  recipe is where that split is written down. What D95 first feared about code
  nodes — bypassing the manifest gate (D5), the client/server split (D55) and
  the path checks (D57) — is answered by making a run step a dispatch rather
  than a new executor (D96). The editor is the same vertical rail the task is
  read on, each step opened as a card of fields, and Run now works before the
  schedule is switched on; a graph, if wanted, is a read-only view of the list.

- **D96 — A run step is a command the owner wrote, and no agent step may write
  what it runs.** A run step is an `argv`, a working directory and a timeout,
  fixed at authoring. The server sends it to the owner's client as one
  pre-approved `shell` dispatch through the ordinary gate, quoted by the server,
  and the audit chain records it like any call; the owner's policy must grant
  `shell` when the task is saved and again when the step starts. It is
  pre-approved for the reason a task is (D93): the owner approved these exact
  words, and the model neither chooses nor sees them.

  It has no write list. A command reaches what its owner reaches, and nothing
  on the client could confine it — the shell's sandbox is gone (SPEC section 5)
  and N146 is why a control nothing enforces is not declared. So the protection
  is on the other side: **the integrity of what runs.** At authoring, no agent
  step's `files_write` may contain or lie inside the workspace, a run step's
  `cwd` or a run step's program, the run root must lie outside all three, and
  an agent step of a task with a run step after it may not hold `shell` or
  `python`, which declare no paths. Inputs reach a run step only as
  `NEUMAN_INPUT_*` environment variables, never in its `argv`. A prompt
  injection in a page an agent step reads can therefore change the data a later
  step is given, which that step must treat as untrusted, and cannot change the
  program it runs.

  A run step waits its whole `timeout_s`. The dispatch carries a wait and a
  lifetime (D98), both set to the step's deadline: the client holds the lifetime
  under the manifest's — `shell` lives an hour, which is also the most
  `timeout_s` may say — and honours the wait under that, though it is longer
  than the two minutes `shell` waits for a model. **Only the core asks for a
  wait past the manifest's, and only for a command its owner wrote**; the
  harness sends the manifest's wait for every model call and never takes one
  from the model's arguments. A run step is never a job: it carries no session,
  and the step after it must not start while it runs.

- **D97 — Every run gets its own directory, and leaves its record there.** A
  task names a `run_root`; each run works in `<run_root>/<due date>-<run id>`,
  which every step sees as `$RUN_DIR` (an envelope may name it, and run steps get
  `NEUMAN_RUN_DIR`). The first version had one fixed workspace and one fixed
  instruction, so a task could only ever work on the same folder — a daily job
  whose every run should leave its own claims and report could not be one. With
  a directory per run the instruction can be the same words every time ("Read
  brief.md in your working directory and follow it"), the first run step
  writes what is specific to this run, and an agent step's default write reach
  is a directory nothing else uses.

  When the run ends the server writes its record — the `task-run` contract,
  steps, cost and the sessions behind each agent step — to `neuman-run.json` in
  that directory. Whatever consumes a run's outputs reads how the run went from
  the same place, with no call back into Neuman and no credential to make one.
  The directory and the record are made with ordinary `fs_write` dispatches on
  the owner's client, which therefore creates missing parent directories inside
  its write reach; the client gains no task-specific surface (D94).

- **D98 — A command that outlives its wait becomes a job, not a kill.** A
  manifest declares two deadlines: `wait_ms`, how long a call blocks, and
  `lifetime_ms`, how long its process may live. `shell` and `git` wait two
  minutes and `python` five; all three live an hour. At the wait, a process
  with lifetime left becomes a background job on the client — the dispatch
  ends `detached` with the job's id, the process keeps running, its output
  keeps going to the spill files, and the model gets its turn back. Every other
  tool's lifetime is its wait, and it is killed there as before.

  The single timeout it replaces was not a bound on long work, only on
  observed work. The client kills a dispatch's process group, and a descendant
  that calls `setsid` leaves the group: `setsid nohup make &` outlives any kill,
  and a model that wanted a long build could already have one, paid for with
  the exit status, the record and any chance of a person stopping it. A job is
  that same build made honest — declared, capped, recorded and stoppable.
  Claude Code and deepseek-harness promote at the wait the same way; Codex's
  session-polling shape needs a terminal Neuman does not have (stdin is
  `/dev/null`), and pi's no-timeout contradicts D15.

  A call becomes a job only when the model can follow it: the model holds
  `job_output`, and the dispatch belongs to a session. Otherwise the core sends
  a lifetime equal to the wait. The client also needs a free slot — four per
  client; at the cap it kills at the wait and the result says why, because
  evicting a running build to make room for another would be worse. The job is
  tagged with the session, and `job_output` and `job_kill` find only that
  session's jobs. They are `daemon` tools: the client daemon answers them from
  the jobs it holds, with no process, because a subprocess could not see them.
  Neither is mutating — reading your own job and stopping a command you were
  already allowed to start ask nobody — and neither is advertised unless a tool
  that can become a job is.

  **What the model is told is designed for its context**, because every byte
  of it is re-sent with every later request of the session. A detached result
  is the preview the dispatch already forwarded and one sentence: the id, when
  the job will be killed, and the tools that follow it — no spill path. A look
  returns a status line and only the output the model has not been shown, the
  dispatch's preview counting as shown: at most a kilobyte of each stream's end
  while the job runs, four once it has ended, and the spill path only when
  something was left out. It waits for the job to end by default (five
  minutes), because one look that returns at the end is one result where
  polling is one per poll. A job that ends while no look waits on it is logged
  on its branch as a `harness_event` carrying a one-line notice, projected as a
  user turn marked as Neuman's — folded into the user turn beside it and held
  past any run of tool replies, so it never breaks a strict chat template — and
  logged rather than held in memory because history is re-derived from the log
  each step. The client keeps the last 64 KiB of each stream in memory as well
  as the file, so a build that outgrows the file's cap still shows its end.

  It is recorded before the model hears of it: `job.detached` in the audit
  chain ahead of the result that names it, and `job.settled` — exited, killed,
  killed at its lifetime, or lost with the connection — when it ends. The
  verdict that let the command run covered its words, not its duration; these
  two entries are where the duration goes on the record (security-model.md).

  A job dies with the client's connection: a process the core can no longer
  hear about is an effect with no record and nobody to stop it, so the daemon
  kills its jobs when the socket goes and the core records them as lost.
  Surviving a reconnect would need the hello to re-announce live jobs and the
  core to kill any it has no record of; that is not built. A task's agent step
  kills any job its session left when the step ends. Not built either: waking
  an idle session when a job ends (it needs a turn with no user message), and a
  `run_in_background` argument — the wait already does the job, and the model
  would have to guess.

  **The person can see and stop a job, not only the model.** The daemon's own
  `/api` surface answers `GET /api/jobs` and `POST /api/jobs/{id}/stop` itself
  rather than forwarding them, because the jobs exist in that process and a
  round trip through the core could only be a slower copy of what is already
  there (`ui.py`, `docs/networking.md`). The composer draws a chip per running
  job from it with a ✕ to stop, refreshed on a `jobs` frame, at a turn's end
  and when the tab comes back rather than on a timer. Without this a command
  could run an hour on someone's machine with the model as its only available
  stopper, which is not what "stoppable" should mean; the fence that keeps one
  conversation's model out of another's job is not applied here, because the
  person at the keyboard is the account the jobs belong to and can see the
  process in `ps` regardless.

- **D99 — A task's output lives in the task, and the task view is a reader.**
  Tasks are drawn inside the chat window rather than on a page of their own:
  the rail's foot turns the session list into the task list, and the main area
  shows an overview or a task, with the steps on the transcript's work rail and
  Run now where the composer is. A step opened on that rail shows what it did —
  a run step's own words, kept on its record as `output` (the end of stdout,
  then the end of stderr), and an agent step's transcript.

  **An agent step's session is the run's, not a conversation.** It carries
  `task_run_id`, and every read a person makes from the chat window — the
  list, the working set, search, opening it by id, a socket — leaves it out.
  The task view reads it only through the run
  (`GET /api/tasks/{task}/runs/{run}/steps/{index}`), which checks the task is
  the caller's and that the session is the one the run's record names, so the
  route is never a way into a conversation. The first version filed each agent
  step as an ordinary session titled `task · step`: a daily task with one agent
  step put thirty rows a month into the chat list, each a transcript nobody had
  asked to talk in, and the run that explained it was a different page away.
  The session row is kept rather than replaced by a run-owned log because the
  harness, the audit chain, retention and the admin reads already know it; the
  column is a scope, and an operator's session list still shows these rows.

  **Following a running task is polling, not a socket.** The run executes on
  the server whether any window is open (D94), and its progress is already
  durable: step records are written as each step starts and ends, and an agent
  step's calls are appended to its log as they happen. So the view reads the
  run and, for an open agent step, its projected log — every two seconds while
  something runs and someone is looking, every fifteen otherwise, which also
  keeps the rail's "needs a look" count current from inside a conversation. A
  stream would add a second fan-out for frames the log already holds, and a
  reader that joined late would still need the log; the cost is that a running
  step's tokens are not shown as they stream, only each call as it lands. A
  run is stopped by cancelling it on the server (`POST /api/tasks/{id}/stop`),
  which closes the open step as `cancelled` the way a shutdown does.

- **D100 — Blender is the user's, driven over a socket, and not through MCP.**
  Building a 3D model from a description is a loop: change the scene, look at
  it, change it again — with the person watching and able to step in.
  **No MCP.** The published Blender integrations are an add-on that runs
  commands inside Blender plus an MCP server that turns those commands into tool
  schemas. Neuman's manifests already are the second half, and an MCP bridge
  would cut across the decisions the security model rests on: its tool list is
  discovered from a running program at dispatch time (D3, D5), it would be a
  third place a tool runs (D13), it carries none of `needs`, `mutating`,
  `critical` or `paths` for the gate to read, and its SDK is a dependency on
  interpreters that have none (N47). Generic MCP support, if it is ever wanted,
  needs its own entry arguing with those; Blender is not a reason to start one.
  **The browse shape.** The tools are the Blender tool package (D101), and its
  host, `neuman-blender-host`, starts the user's Blender on their machine,
  visible, with Neuman's add-on as its startup script; the add-on binds a
  `0600` Unix socket in the user's runtime directory and answers there. The
  client starts the host the first time a Blender tool is dispatched and
  nothing answers, so nobody launches Blender by hand before asking for the
  work. A first version had a Nix option and a user service to start it, and
  the first person to use it was told Blender was not connected: the option
  was off and the service did not exist.
  Those integrations listen on loopback TCP with no authentication — every
  process on the machine could run Python as the user — which is what the
  socket's mode replaces, as it did for CDP (D31). Blender runs Python, so
  there is no bridge: the far end of the socket is inside Blender.
  **Main thread only.** `bpy` is not thread-safe. The socket thread queues each
  request, and a persistent timer runs it between redraws, so the person keeps
  using the window and both see each other's changes as they land.
  **Three tools, split by what asks.** `blender` changes things (`exec` runs
  Python inside Blender; `new`, `open`, `save`, `export`, `render`) and is
  mutating. `blender_scene` describes the scene as text and `blender_view`
  returns a picture; neither mutates, so under `safe` the looking half of the
  loop does not prompt — the reasoning of D59, applied to a loop where one look
  follows every change. Each `blender` call ends with an undo push named
  "Neuman: …", so the person takes back the agent's edit with Ctrl-Z; a change
  made through `bpy.data` pushes nothing of its own, and without this the only
  way back would be a saved file.
  **The picture is drawn, not captured.** `blender_view` sets the requested
  view, draws it with the viewport renderer offscreen, and puts the view back,
  all inside one main-thread call — so the person's viewport never visibly
  moves and nothing is written to disk. That is what keeps it non-mutating,
  where `browse`'s `shot` writes a PNG and asks. It returns the PNG on stdout
  under `returns_image`, a manifest field this decision adds to D28: the
  harness had recognised `fs_image` by name, and a second picture-producing
  tool either joins a name list in the harness or declares itself. One call per
  look also matters here, where the browse precedent — write a file, then
  `fs_image` it — would double the calls in the loop that runs most.
  **No scene script runs.** The host starts Blender with
  `--disable-autoexec`, and `open` passes `use_scripts=False`: a `.blend` can
  carry code that runs on load, and a file the model was told to open is exactly
  the one nobody vetted.
  **Never a background job.** `blender` waits up to ten minutes and does not
  detach (D98): stopping a job kills the tool process, and Blender would finish
  the code regardless, so `job_kill` would be a control that lies.
  **What it costs.** `exec` is arbitrary Python as the user, approved per call
  — the `python` tool's blast radius, nothing less. `paths` is checked against
  the write surfaces because the tool is mutating, so `open` cannot read a
  `.blend` from a read-only share without copying it first. Scene content —
  object names, text blocks, custom properties — is written by whoever made
  the file, and reaches the model as data like any other tool output (D23).
  Neuman does not install Blender: the person does, and a machine without it
  answers `missing_program` with where to get it (D101). Linux only, for now:
  on macOS Blender is an app bundle rather than a name on the PATH, and nothing
  of this has been run there.

- **D101 — Tools beyond the basic set are packages: shipped, enabled, granted.**
  The tool list will grow — tools Neuman maintains, and tools from the
  community — and most of them matter to some deployments and are noise to the
  rest. The model catalog already solved the same problem (D76): the release
  ships a reviewed entry, an operator adds it from a tile, a role grants it. A
  tool package is that for tools: a file in `tools/packages/` naming the tools
  it holds, validated against `contracts/schemas/tool-package.schema.json`. A
  tool no package names is built in and always present.
  **Three states.** *Shipped*: the package and its tools' manifests and
  programs are in the release, on the server and on every client, exactly as a
  built-in tool's are. *Enabled*: the baseline's `packages` lists it, which an
  operator sets on the administration page's Tools section; it is in the
  baseline so `baseline show` and `set` carry it with the roles that grant its
  tools. *Granted*: a role's `tools`, unchanged. Enabling grants to nobody, and
  disabling withdraws everywhere without editing a role, so turning a package
  back on restores what the roles already said.
  **Enabled is a view of the registry, not a field of policy.** Policy is
  composed in several places; the registry is read by all of them — the gate,
  the advertisement, the chooser, task authoring. So `services.registry` is the
  installed view: built-in tools plus those of enabled packages, re-reading the
  live baseline on every call, so a disable lands on the next step as a revoked
  grant does. A granted tool in a disabled package is refused as
  `package_not_enabled` rather than `unknown_tool`, because the remedy is the
  Tools page and not the role. `registry.catalog` is the whole release, for the
  page itself.
  **Nothing is downloaded, and D3 stands.** The obvious design fetched tool
  code when a package was enabled, and that is the server handing a client
  code: a compromised server could then make every client a general program
  runner, which is what D3 exists to prevent. Shipping every package in the
  release means enabling one only chooses among programs each client already
  holds. A program a package's tools need on the person's machine — Blender —
  is declared in `requires` and is the person's to install: the client checks
  for it on every dispatch and, when it is missing, refuses with
  `missing_program` and a sentence naming the program and where to get it,
  which the model passes on. Installing software onto someone's machine because
  an administrator ticked a box was the alternative, and it is not Neuman's to
  do.
  **A host is started on demand.** A package may declare a `host`: a
  companion in a window the person works in, which its tools reach over a
  `0600` socket at `$XDG_RUNTIME_DIR/neuman-<id>.sock`. Its `exec` resolves from
  the client's own tool directory, as a manifest's does, and no manifest names
  it, so no dispatch can run it. The client starts it the first time one of
  the package's tools is dispatched and nothing answers, in its own session,
  and leaves it running: it is the person's window and may hold unsaved work.
  The tool, not the client, waits for the socket, because a dispatch must
  report `started` within seconds (D15). The client names the socket to the
  host and to the package's tools in one variable, `NEUMAN_HOST_SOCKET`, so a
  new package adds no flag, option or variable anywhere. `browse` is not a
  package: its host is the window a person reads Neuman in, started by them,
  and always wanted.
  **Community tools enter by review.** A community tool is contributed to
  `tools/` like any other, reviewed, and shipped labelled `community` — the
  model presets' bargain of a reviewable diff over a form each operator fills
  in alone. Catalogs from outside the release would need a publisher key that
  the client's own configuration trusts, never one the server hands it, and an
  entry amending D3; that is not built.

- **D102 — A file dropped on the window is written by the client and attached
  by path.** Dragging a file from the desktop onto the chat window attaches it.
  The browser gives the page bytes and a bare name, never a path, and an
  attachment is a path the agent reads (N57) — so the page posts the bytes to
  `POST /api/drops?name=` on the daemon that serves it, the daemon writes them
  to its own `drops/<sha256>/<name>` (`0700`/`0600`) and answers with the
  absolute path, and the page attaches that path through the ordinary
  `PATCH /api/branches/{id}`. The bytes never reach the core.
  **Why this does not reopen N57.** Every wall N57 hit was the *server's*: it
  could not write into a home directory, the directory it could write was
  outside every surface, and no tool accepts bytes. Since D55 the client runs
  as the person on their own machine, and the browser is on that machine
  because the UI is served on loopback, so the write is the person's own and
  the reader is `fs_read` as for any other file. It is `spill/` again (D42):
  client state the read tools may open whatever the surfaces say, never a
  mutating one, and named by no policy. That is a widening of what the agent
  can read only in the sense that the person handed it the file; the content
  is untrusted data once read, like any other.
  **The core accepts a path in `drops/` only from the connected client's own
  directory.** The client reports it on `hello` beside its home (an absolute
  path, not `/`), and `attachable` (`policy/paths.py`) admits a path strictly
  inside it in addition to everything `within_reach` admits. With no client
  connected there is no `drops`, so such a path is refused — the same terms as
  a `$HOME` grant, and the same moment nothing could read it anyway.
  **Not inlined into the message.** The alternative was the file's contents in
  the turn. A path costs one line of the system prompt and the model reads what
  it needs; inline contents are paid on every turn until compaction, and a PDF
  or an image is not text — on disk they are what `python` and `fs_image`
  (D28) already read.
  **A drop lives until no conversation attaches it.** `GET /api/attachments`
  returns every path any of the account's live sessions' branches attach; the
  daemon sweeps `drops/` against it when it starts and after each new drop, and
  deletes a file nothing names. Only on a complete answer: a sweep that cannot
  reach the core keeps everything. A file also gets a day's grace from its last
  drop, because a drop staged in a new chat is attached only when the first
  message creates the session, and until then the core has never heard of it.
  **25 MiB, checked on `content-length`.** Documents and pictures fit; anything
  larger is already a file on disk, and the chooser attaches it with no copy.
  The daemon refuses a larger body before reading any of it, because every body
  it proxies is held in memory whole.

- **D103 — The schema froze at the first release.** `0001_initial.sql` is the
  initial schema and is never edited; a change is the next numbered file in
  `migrations/`, applied in name order, one transaction each, recorded with its
  SHA-256 (D18). Before the release the file was rewritten in place, which was
  affordable because no appliance had a schema to preserve — there was nothing
  to upgrade from. The release ends that: two appliances on different versions
  now both have to be describable, and a rewritten `0001` gives one version
  number two schemas with no way to tell them apart. The cost is the
  maintainer's, and it is real: a schema change now needs a migration file and
  an upgrade path where it used to need an edit, an index for one query is a
  migration rather than a line added in passing, and a mistake in an applied
  file is corrected by a new migration (or a restore) rather than by editing
  it. The rejected alternative was renumbering the initial file at each
  release, which would leave every deployment holding a schema its own version
  number does not identify.

- **D104 — The folders a `$HOME` grant expands to are runtime configuration,
  and the module has no option for them.** *Superseded in part by D105: the
  folders are not a setting at all, and `$HOME` names the whole home.*
  `services.neuman.homeDataDirs` and
  `NEUMAN_HOME_DATA_DIRS` are gone. The list is the `settings` row
  `home_data_dirs`, edited under **Files** in the administration page (or with
  `neuman-admin`), read by every expansion, and in force on the next turn. A
  fresh database has no row and uses the built-in four — `Documents`,
  `Desktop`, `Downloads`, `workspace` — and *Use defaults* deletes the row
  rather than writing today's list into it, so a release can still improve the
  default under an appliance that never chose one.

  **What it corrects in D68.** D68 listed "paths" among the deployment
  settings that stay in the module. This was one, and it should not have been:
  nothing at startup reads it, every use is a per-request or per-turn
  expansion (`paths.expand_paths` and its callers), and the value changes when
  an operator adds a folder to the places work happens — not when the process
  restarts. Filing it under paths also made it the one runtime value a NixOS
  install could set and an Ubuntu install could not, which is backwards: the
  server must not know which of them it is installed on. The rule that
  replaces it: the module owns what a restart changes — packages, ports, TLS,
  sockets, the units and their sandboxes — and the database owns every value
  an operator changes while it runs.

  **The cost.** A NixOS administrator can no longer pin the list in the flake;
  the only declarative route is a deploy step that writes the row over the
  socket (`neuman-admin`), which is a re-run rather than a rebuild. And a
  fresh install's list is now the default one until someone saves a set, so an
  organisation whose work lives outside those four folders has one more step
  after installation. It is not a security boundary either way: `shell` and
  `python` ignore the list (D57), and a role can name the same directory
  absolutely in `files_read`, so nothing is confined by where this lives.

  **Rejected: keeping the option and pinning it**, so a deployment-set value
  wins and the page renders it read-only. It buys reproducibility for exactly
  one value while every role, the prompt and the whole catalog stay database
  owned — the appliance is not deterministic in the sense the pin suggests —
  and it makes the panel's behaviour depend on a fact the panel itself has to
  explain. **Rejected: keeping the option as a default the page may
  overwrite**, which is the seed D68 removed: a second copy that stops being
  true after the first edit and says nothing when it does. **Rejected:
  deprecating for a release.** The removal landed days after the first
  release, with no installed appliance to migrate; a window would have kept
  the second source alive to protect nobody.

  When Nix-side configuration returns — and it may, for the whole runtime
  document rather than one field — the shape to build is the assertion the
  pin was trying to be: the deployment owns a value and the page says so,
  never a seed that is consumed once and then lies.

- **D105 — `$HOME` names the home, whole.** *Amended by D106: the token also
  composes — `$HOME/Documents` — so a role can name a folder under the home
  without naming a machine or an account.* The token resolves to the acting
  account's home directory and to nothing else — dotfiles included. The
  `home_data_dirs` row, `/admin/home-dirs`, `neuman-admin files` and the Files
  page that D104 introduced are gone; a role that wants particular folders
  names those folders, and an account on another machine names that machine's
  paths in its own policy.

  **Why the subset did not survive its first day.** A token named after the
  home that means a list of folders is a lie, and it failed as one twice in a
  day: a folder added after the list was written was invisible to every
  `$HOME` role, and a rebuild that reset the list emptied their reach at once.
  It also put the decision in the wrong place — whether a role needs the whole
  home or two folders is a question about the role, and the list made it an
  appliance setting they all shared.

  **What the list was for, and why nothing replaces it.** It existed because
  there was no way to say "the person's Documents" without naming a machine:
  the schema allowed an absolute path or exactly `$HOME`, and nothing in
  between. The answer could have been a compositional spelling
  (`$HOME/Documents`). It is not, because a client on another machine is its
  own configuration: its home is different, its files are different, and the
  account that runs there names that machine's paths. Reaching files across
  machines is a transport question — an ssh connection where the target's own
  permissions decide — not a policy-vocabulary one.

  **The cost.** A role granting `$HOME` now includes `~/.ssh`, `~/.gnupg`,
  `~/.aws`, browser profiles and everything else the home holds. A
  prompt-injected model with `fs_read` and `web_fetch` in the same role can
  exfiltrate a private key with one file read that looks like any other, where
  the old rule refused it. It is a guardrail, not a boundary — `shell` reaches
  all of it anyway (D57) — but it is now opted into per role rather than on by
  default. The setup walkthrough and the tool tour name folders instead of the
  token, and the policy response carries what a grant expands to, so an
  operator who grants the home sees the home.

  **Rejected: `$HOME` meaning the home minus dotfiles.** It keeps the
  guardrail without the lie, but it is another invisible subset with an
  arbitrary edge (`~/.config` secret, `~/secrets.txt` not) — the shape this
  entry removes. **Rejected: keeping D104's panel list**, which is what this
  entry deletes. D104's other half stands: runtime configuration belongs in
  the database, not the module, and nothing here puts it back in Nix.

- **D106 — The token composes: `$HOME/Documents` is that folder wherever home
  is.** A `files_read` or `files_write` entry may be the bare token, an
  absolute path, or a path under the home written from it — one or more
  segments, no `.` or `..`, no empty segment. The schema allows the shape,
  `paths._resolve` expands it against the home the client reports, and nothing
  else changes: `hello` carries the same home, `rpc_version` stays where it is,
  and there is still no appliance-level list.

  **Why D105's answer was half right.** D105 took the token down to the home
  and told roles to name folders absolutely. That is right for one person on
  one machine and wrong for the case the product exists for: a role is shared
  by a group, and `/home/lj/Documents` names one member's directory and nobody
  else's. Naming each home in that account's overlay would make the most
  personal grant a manual step for every person, which is the arrangement this
  entry removes.

  **And for the port to come.** A client may be on macOS, where the home is
  `/Users/<user>`. An absolute path in a role is wrong for the same person on
  a different OS; `$HOME/Documents` is right on both, because only the client
  knows its home (N145) and the platform seam already answers `home_dir()`
  per OS. One role, not one per platform.

  **The cost.** One more shape for the schema to validate and the resolver to
  expand — a pattern and one branch — and one more thing the policy preview
  must print correctly, which it already does because it prints the expansion.
  The security position is D105's, unchanged: the bare token is the whole
  home, dotfiles included; `$HOME/.ssh` is expressible because someone might
  mean it; roles ship with folder grants.

  **Rejected: dropping the token instead**, which was D105's alternative — it
  makes every home personal configuration rather than a role grant, and it
  cannot survive a second operating system. **Rejected: the appliance list**
  (D104) — a token whose value changes per appliance is the shape both of these
  replace.

- **D107 — The server also installs on Ubuntu 24.04, from a .deb.** The NixOS
  module stays the reference build, and the distribution package is the same
  source with the same systemd unit. Supported baseline: Ubuntu 24.04, whose
  Python is 3.12 — 22.04's 3.10 is below what `neuman-server` requires, and
  bundling an interpreter to serve it would be a second one to patch.

  **The unit ships in the package.** `ProtectSystem=strict`, `ProtectHome`,
  `PrivateDevices`, the `StateDirectory`/`LogsDirectory`/`RuntimeDirectory`
  set and the socket modes are what the security model's claims are enforced
  by. An install that left the unit to the operator would be a weaker appliance
  under the same name, so `deploy/ubuntu/neuman-server.service` is a copy of
  the module's and the two are checked against each other.

  **Dependencies are pinned from the environment the tests run in.**
  `deploy/ubuntu/constraints.txt` is generated by
  `deploy/ubuntu/constraints.py` from the flake's `serverEnv`, and the deb is
  assembled with exactly those versions. Resolving afresh would give the
  package a dependency set no test had run against, differing silently at
  every nixpkgs bump. The cost is a regeneration step: a `nixpkgs` update is
  not finished until the constraints file is rebuilt and the package rebuilt
  from it.

  **The contracts wheel travels with the server.** `neuman-contracts` is not
  on PyPI, and the server validates every manifest against it; a server
  installed without it would load tools with no schema check, reporting that
  only as a log line. The package installs it from the same release, which is
  D3's "both halves read one contract" in the one form a distro can carry: one
  source, cut together.

  **The build id is the package version.** `/usr/share/neuman/build-id` holds
  `deb <version>` and the wrappers export it as `NEUMAN_BUILD_ID` unless the
  environment already set one. It is the deb's answer to the store path the
  NixOS module stamps (D69): the same fact — which code served this turn —
  from a package that has no store path.

  **The costs.** Two build identities now exist, and only the Nix one is
  reproduced bit for bit; the deb is pinned by version but not by hash of the
  resulting tree. An upgrade is `apt install` plus a restart rather than
  `nixos-rebuild`, with the same forward-only migrations and the same backup
  step. And there is no test of the install path on its target yet: the Nix VM
  tests are NixOS, so the postinst and the unit are exercised by the first
  clean host that installs the package. That gap is the work left.

  **Rejected: a container image as the primary artifact.** It gives up the
  unit's sandbox, which is the enforcement the security model names, and makes
  the appliance's one database someone else's problem. **Rejected: `pip
  install` as the supported install** — unpinned, no unit, no user, no
  database, and every dependency resolved on the operator's machine.
  **Rejected: bundling a Python interpreter** — a second interpreter to track
  security fixes for, to serve one older distribution. **Out of scope for this
  package:** the client, which is the larger half, and local models, because
  `neuman-runtime` needs a `llama-server` a distribution has to supply.

- **D108 — The client also installs on Ubuntu 24.04, and the browser is the
  person's.** A `neuman-client` `.deb`, per user, with no root component: the
  client wheel, the tools (standard library only, one wrapper per manifest
  `exec`), the client's own copy of the manifests, the four web files, and two
  user units plus a desktop entry. Neither unit names the server, because
  `neuman-client login` stores it in the user's `session.json` — the login is
  the configuration, so there is no per-user file to write.

  **No bundled Chromium.** The Nix package pins one because Nix can — a store
  path with no maintenance. On a distribution, bundling means owning a
  browser's security updates on exactly the path where a model-injected page
  runs, and the client already prefers a system browser (`find_chromium` looks
  for `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable`).
  Only `browse` needs one, and only roles that grant `browse` reach it, so the
  package recommends `chromium | google-chrome-stable` rather than depending on
  it. A minimum major version is checked when the browser host starts: old
  enough that it refuses nothing that works, present so an ancient browser is a
  sentence rather than a protocol error.

  **Two bugs the packaging surfaced, both fixed here.** `--profile copy`
  returned quietly when rsync or the source profile was missing, leaving the
  browser on the `own` profile while the operator believed it held their
  logins — a silent change in whose identity a page acts as. It fails closed
  now, and `browser_copy_source()` knows the Chromium snap's profile under
  `~/snap/chromium/common`, which `~/.config/chromium` alone never found on a
  stock Ubuntu desktop. Where the profile Neuman *opens* can live depends on
  the same packaging, which is D110. Second, the readiness check the browser unit runs moved
  out of the home-manager module's shell script into
  `neuman-client wait-for-ui`, so the module and the deb run one implementation
  of N183.

  **The cost.** A second artifact to cut and publish in step with the server.
  The deb does not pin Chromium, so a CDP change in a future browser is the
  user's update to absorb — the version floor and `doctor` are the warning.
  And the last point is now stated in `networking.md` rather than implied: the
  DevTools endpoint is loopback TCP with no authentication, so the `0600`
  socket gates the tool and not the machine's other local accounts. The client
  assumes every account on the machine is trusted as the user is — true of a
  personal laptop, not of a shared workstation. `--remote-debugging-pipe`
  would remove the port, but the pipe is NUL-delimited CDP rather than
  HTTP/WebSocket, so it changes the tool↔host protocol and is its own decision,
  triggered by clients on shared machines.

  **Out of scope here:** local models (still a distribution's `llama-server`),
  the Blender add-on's install, and the macOS client, which is its own plan.

- **D109 — An Ubuntu package is built for one release's Python.** The wheels a
  package carries include compiled extensions — `psycopg-binary`, `rpds-py`,
  `argon2-cffi-bindings` in the server, `websockets`' optional speedup in the
  client — and their files are tagged for an interpreter (`*.cpython-312-*`).
  CPython's ABI is not forward compatible, so a newer release's 3.13 will not
  load them: the package would install on a satisfied `python3 (>= 3.12)` and
  then fail at import under systemd. Both packages therefore depend on
  `python3 (>= 3.12), python3 (<< 3.13)`: 24.04 installs, 22.04 (3.10) and
  25.04 or later (3.13, 3.14) are refused by apt with a sentence rather than
  installed and broken. Supporting a new Ubuntu release is a new pair of
  builds, not a flag.

  **Rejected: depending on the distribution's Python packages.** It would make
  one artifact span releases, but the versions available in each release are
  not the set the suite runs against — the pinning D107 exists to keep — and
  the packages the server needs (`starlette`, `uvicorn`, `psycopg` 3) are not
  all present or current. **Rejected: bundling an interpreter**, which is a
  second Python to track security fixes for. **Rejected: a single deb that
  falls back to pure-Python implementations**, which trades a clear refusal for
  a path nobody tested.

  **The cost:** the download page and the platform matrix name one Ubuntu
  release, and a person on the newest Ubuntu waits for its build. That is the
  honest state until there is a reason to support more than the LTS.

- **D110 — The browser's profile is decided with the browser, and a pairing
  that cannot work is refused.** A snap-confined Chromium reaches only
  non-hidden paths under `$HOME`, so `~/.local/share/neuman/browser` is a
  directory Ubuntu's Chromium can never open — and what it says instead is exit
  21 and a message about a lock file (N184). `browser_profile_dir()` therefore
  takes the browser: a confined one defaults to
  `~/snap/chromium/common/neuman-browser`, inside the snap's writable area and
  an ordinary directory an unconfined browser can use too, while an unconfined
  one keeps the documented path. `NEUMAN_BROWSER_PROFILE` overrides both,
  through the environment for the reason `NEUMAN_BROWSER_CHROMIUM` is: the
  package owns the unit an operator would otherwise have to edit.

  **Refused, not degraded.** `browser_profile_refusal()` answers whether a
  given browser can open a given profile, and has two callers so the answer
  cannot drift: `neuman-browser` refuses to start on a pairing that cannot
  work, and `doctor` fails its acceptance check on the same call. The browser
  host also waits for the DevTools port to answer before it binds the browse
  socket, because a Chromium running with nothing on that port is a
  healthy-looking unit serving a socket nobody can use — which is the snap's
  second confinement, `network-bind` not being connected by default.

  **Rejected: refusing the snap outright.** It is what the package's own
  `Recommends` installs on a stock 24.04 desktop, so a person whose only
  browser is the snap would lose `browse` with no path forward. **Rejected:
  keeping one profile path and requiring an override for the snap**, which
  leaves the default install broken and moves the diagnosis to whoever hits it
  first. **Rejected: detecting the snap by resolving the executable**, which
  finds `/usr/bin/snap` and loses the fact; the two spellings that reach the
  snap are named instead, and a false positive costs only an oddly-placed
  profile.

  **The cost:** "where is Neuman's browser profile" now has a conditional
  answer, and a profile inside the snap's `common` directory goes with `snap
  remove --purge`. The DevTools wait spends up to 20 seconds before declaring
  a browser failed, which is a slower first `browse` after login on a loaded
  machine. And the snap still needs a root-run `snap connect
  chromium:network-bind`, which no client can do for the user: on Ubuntu the
  free-software browser is the one with an extra step, which the postinst and
  `platforms.md` say rather than hide.

- **D111 — The appliance speaks a set and declares a floor; the client updates
  itself.** Two halves are installed on different machines, by different people,
  on different days. A release that can strand a client makes every appliance
  update a coordinated one, and coordination at every release is what makes a
  self-hosted appliance feel fragile. So the wire version is negotiated, not
  compared: `hello` says which dispatch shape the client speaks, the server
  answers with one of `SUPPORTED_RPC_VERSIONS`, and every frame on that socket
  carries the shape the connection settled on. `welcome` carries the appliance's
  release, every shape it speaks and the oldest client it accepts; `/versions`
  says the same without a session, for `doctor`.

  **A refusal is a sentence with a command in it.** When the intersection is
  empty the server sends an `error` frame with `code: version_mismatch` naming
  both releases and both shapes, then closes `4406` — a code the daemon treats as
  terminal. It is recorded on a `Link` the UI server hands to the page, so the
  person sees *"Neuman needs an update on this computer"*, the two versions, and
  `sudo apt update && sudo apt install --only-upgrade neuman-client` with a copy
  button. The same module's wording is what `doctor` prints. Without this the
  page is served by the client and works while the client's socket is refused,
  so the agent simply has no tools: the person reads that as permissions being
  taken away, which is the wrong diagnosis in the wrong place (D50, N184's
  lesson about a healthy-looking pair).

  **The half without a schema updates itself.** A client update has no
  migration and no database — `session.json`, a UI token and `spill/`, with a
  rollback that is one `apt install`. So the client package ships
  `/etc/apt/apt.conf.d/51neuman-client` adding `Neuman:stable` to
  `Unattended-Upgrade::Allowed-Origins`, and the server package ships
  `51neuman-server` blacklisting `^neuman$`. Ubuntu then installs client releases
  on its own and never the server, which is the asymmetry stated as a rule: the
  half with an irreversible step is manual. Because `apt` replaces files and a
  user unit is not restarted by anything, the running client compares its build
  id with `/usr/share/neuman-client/build-id` and exits `75` into the new code at
  its next idle moment — never mid-job, because a restart kills every job on the
  way out (D98).

  **Ship the message before the break.** The clients that must keep working are
  already installed, so a release that first refuses an old shape cannot explain
  itself to a client built before the explanation existed. The order is: this
  release adds the versions, the banner and the set `{1}` and refuses nobody;
  the first real shape change makes the set `{2, 1}`, so nobody is stranded; the
  set drops `1` only once the clients in the field can speak. The server also
  reports each connected client's release in the Overview, so an operator can see
  the fleet before installing a release whose floor moved — Kubernetes' skew
  policy, at the scale of a handful of machines.

  **Rejected: exact-match versions plus a good banner.** It is the same
  coordination problem with better words. **Rejected: VS Code Remote's model**, a
  client that downloads the server it matches, because the appliance is shared
  by everyone and one client's version cannot be the appliance's.
  **Rejected: the appliance as the clients' update server.** It would remove the
  internet dependency and make air-gapped installs work, but it turns the
  appliance into the source of the code that enforces the client's path
  surfaces: today a compromised appliance can ask for a permitted tool, and it
  cannot replace the binary that decides what is permitted. **Rejected: a button
  that runs `apt`**, which needs a privileged helper on the person's machine
  triggered from the window a model's output renders in — the worst place for a
  root path, and against D108's no-root-component rule.

  **The costs.** Two shapes live in the server for a release, with tests for
  both, though only when a shape genuinely breaks: additive fields need no bump
  at all, because every shipped client ignores what it does not know. A bad
  client release now reaches everyone overnight; the blast radius is the tools on
  each machine, no data, and the rollback is one `apt install`. `session.json` is
  a compatibility surface from here. And the first client below a raised floor
  sees the banner as the first it hears of it — which is why the floor is raised
  in a release whose notes say so, not in the quiet one.

- **D112 — A machine is a named place dispatches run, and a branch names
  one.** The appliance held one client connection per account and replaced it
  on the next connect. With one computer that is the whole of a client; with
  two it is the wrong shape, and it failed in four ways at once. Opening a
  laptop disconnected the desktop, killed its jobs and recorded them as
  `disconnected`. A branch's `working_dir` was an absolute path with no machine
  attached, so a conversation created on one computer and reopened on another
  was launched into a directory belonging to the other — or worse, to a
  directory that exists on both and holds different files. The prompt said
  "on their machine" and never which. And `SPEC.md` §5 promised that a dispatch
  entry carries which machine executed it, which nothing did.

  **The noun.** A *machine* is a named place dispatches execute: a client's
  host today, an ssh target later. The row is what a socket used to be — last
  home, drops, tools, release — so the facts survive the connection. The
  invariant everything else rests on:

      A machine is the unit of a filesystem. Everything that names a path
      belongs to one — a working directory, an attachment, a drop, `$HOME`,
      a dispatch's surfaces, a job's spill files.

  **Scoped to the account, not global.** `machines` is keyed
  `(account_id, label)`, because reach differs per person: Alice's `dgx` and
  Bob's `dgx` are one physical box and two machines, since the files each can
  read there differ. That is also why the label needs no attestation — the
  connection is already bound to an account by the login that produced it, so a
  name can only ever point at one of that account's own places, and a dishonest
  one widens nothing (`shell` and `python` are unchecked, and a client wanting
  more would not ask). **The cost:** the machine on a dispatch entry is a claim,
  like every other client-reported fact, and the record is of the report.

  **A branch names its machine, and the window is somewhere else.** A session's
  root branch records the machine it was created from; a turn reads it and
  dispatches there. The window may be on any computer at any time — that is the
  roaming case, and it works because the window is a viewer and the branch is
  the address. The model never chooses: it is told the machine, exactly as it is
  told a working directory, because a targeting decision that lands on the
  wrong computer is silent wrong data rather than an error.

  **Moving is a fork, unless nothing has run.** `PATCH` a branch's machine is
  allowed while the branch has no `tool_result` — nothing in the transcript
  names a filesystem yet — and refused with `branch_has_run` after that. The
  remedy is a fork, which keeps the old branch true to its own machine and marks
  the move in its first event. Either way `working_dir` and `attachments` are
  cleared: they are paths on a computer, and reinterpreting them elsewhere is the
  failure this entry exists to end. A fork may name a different machine, which is
  the one-click "continue this on the DGX".

  **`$HOME` becomes deterministic.** Expansion already used the client's reported
  home (N145), so it was right while exactly one client was connected and a
  function of connection order otherwise. It now expands against the branch's
  machine, live or from its last hello, so composing policy twice gives the same
  answer whether a lid is open.

  **The record names it.** `tool.dispatch`, `tool.denied` and `job.detached`
  carry the machine, which is the promise `SPEC.md` §5 made and the footnote in
  `docs/security-model.md` repeated while nothing implemented it.

  **Policy may narrow to a set of machines; an empty list is not a refusal.**
  A tool is something the appliance holds and hands out, so `tools: []` grants
  none. A machine is a place this person's own client logged in from, so
  enrolment is already the grant and naming machines can only subtract. Refusing
  an empty list would make every existing role unusable the day the field
  arrived and would say "you may not use the computer you are typing on".
  **The cost:** a role that forgets to list machines confines nobody, so an
  operator who wants a laptop-only role must say so.

  **The shape change, against D111's order.** D111 prescribes `{1}` → `{2, 1}` →
  `{2}` so that no installed client is stranded. This release goes straight to
  `{2}`, and the reason is that there is no installed fleet: the only clients
  are on one person's own two computers and are rebuilt together. A `machine` on
  `hello` is **required** rather than optional, which is why this is not the
  additive case D111 exempts — defaulting it is the ambiguity being removed, and
  a server that invented a label would write a record saying a dispatch ran
  somewhere it did not. When the second deployment arrives, the next shape change
  follows D111's order. **The cost:** an unrebuilt 0.1.0 client is refused with a
  sentence rather than tolerated, which is correct here and would not be once
  strangers are running it.

  **Rejected: giving the model `ssh` and letting it hop.** It solves the
  complaint without solving the problem, and it is a prompt solution to a
  targeting one: a forgotten `ssh` does not fail, it registers the images on the
  laptop. `shell` is deliberately unchecked (D57), so the target would be inside
  an opaque string — no machine in the record, no per-target grant, and ten
  machines' reach behind one ungranted tool. SSH belongs *behind* a machine kind,
  dialled by the client with the client's own key, which is D105's principle one
  layer up: reaching across machines is a transport question.
  **Rejected: a globally unique machine id.** It buys an attested label for a
  problem that does not have one — the account is already attested, and the
  label's only authority is within it.
  **Rejected: a machine per dispatch, chosen by the model.** Same category as a
  model-chosen working directory, and worse: a task *author* may name a machine
  per step, because that is a human writing a recipe.
  **Rejected: a `person` table.** One account is one person here, and a second
  noun for the same thing would be an indirection nothing reads.
  **Rejected: the machine on the session rather than the branch.** A fork is
  exactly "the same conversation, now over there", and the branch is what a turn
  reads.

  **The costs, in one place.** A second noun and a table to administer, though a
  personal machine enrols itself on first login and needs no ceremony. A column
  on the most-written table and a read on every turn. An upgrade statement for
  any appliance whose database predates the column (below). And a prompt that
  names a machine is a new way to be wrong if the name is stale — which is why
  the record, not the prompt, is the authority.

  **Amended before release: nothing is optional and nothing is inferred.** The
  first shape of this decision let `machine` be NULL and had a branch adopt "the
  account's most recently connected client" on its next turn, so a conversation
  written before the column kept working. Running it produced the bug it was
  meant to prevent, one turn later: a session created on one computer, opened
  from another, adopted the second and kept the first's `working_dir`, so every
  dispatch ran in a directory that was not there (N186). The fault is the
  *fallback*, not its implementation — an implicit "whichever is connected" is
  how a place stops being a fact. So:

  * `session_branches.machine` and `tasks.machine` are NOT NULL, and `0004`
    refuses to guess: an appliance with rows from before the column stops once,
    prints the statement to run, and starts when it has been run. The column is
    added by its own migration that commits (`0003`) so that statement has
    something to name (N188).
  * `POST /api/sessions`, `/api/browse`, `/api/environment`, `/api/policy` and
    the admin prompt preview all name a machine, and refuse a request that does
    not. `/api/sessions` also refuses a label the account has never connected
    from (`unknown_machine`) — a session on a computer that does not exist has
    nowhere to run and a prompt naming a fiction.
  * `ToolExecution.dispatch`, `available`, `home` and `drops` take `machine: str`.
    There is no method that answers for "any machine", and `newest_machine` is
    gone.
  * A **task** names one, recorded when it was written: a recipe is about files
    somewhere, and "wherever a client happens to be online" is not an answer a
    scheduled run can be trusted with.

  The cost is an upgrade step on this appliance and on any other with history,
  and a client that cannot find its own label cannot start a session — which is
  the intended refusal, not a failure: the window learns its machine from the
  client it is served by, and a window that does not know is a window that would
  guess.

## Platform ground truth

Facts about Linux, Postgres, Python, and Nix that the design must assume.
Each of these has been violated before; it does not work. When code here
hits one of these, add the encounter to `docs/gotchas.md`.

**Privilege**

- Passwords are argon2id. Hashing is CPU- and memory-bound, so it runs in a
  worker thread; doing it on the event loop stalls every other request for the
  duration of one login.
- The server runs as `neuman` and has no root path. `pwd` is used only to name
  the peer on the local admin socket; there is no NSS account observation for
  users and no `/etc/shadow` read.
- A deployment that wants an organisation directory in front of Neuman gets
  OIDC or LDAP; PAM is not a supported adapter.

**Storage**

- The decision core stores in the appliance's own PostgreSQL cluster,
  connecting over the local Unix socket with peer authentication; only
  the `neuman` role has credentials — no other component can write the
  core's state.
- Loops borrow connections from a pool owned by `foundation`; never open
  a connection per thread or per request.
- Migrations are plain SQL: Postgres DDL is transactional, so each
  migration runs in one transaction with no helper machinery.
- The audit chain lives as hash-chained rows in the database; the JSONL
  mirror on local disk is the durable tamper-evident artifact. Backup is
  `pg_dump` + the mirror + the policy overlay; restore rehearses both.
- State files (the runtime policy overlay) are written atomically:
  write-temp, `fchmod` (`mkstemp` yields `0600`), rename. A bad file must
  fail closed, never be ingested half-written.

**Python**

- Pin the interpreter explicitly; the distribution default drifts.
- `logging` `extra=` keys that collide with `LogRecord` attributes crash at
  emit time. Reserve a namespaced key and scan call sites in tests.
- ASGI servers (uvicorn) leak ANSI codes and chatter into JSON log streams
  unless quieted explicitly.
- Secure cookies need an https base URL even in tests.

**Browsers**

- A snap-confined Chromium reaches only non-hidden paths under `$HOME`. A
  profile under `~/.local` is unopenable, and the browser reports it as exit 21
  and a `SingletonLock` error that names neither (N184).
- The chromium snap has no `network-bind` connection by default, so a browser
  started with `--remote-debugging-port` runs and opens nothing until
  `snap connect chromium:network-bind` (N184).
- Ubuntu's `chromium` is `chromium-browser`, whose whole content is the snap;
  `find_chromium()` preferring the name `chromium` therefore prefers the snap
  to an installed `google-chrome-stable` (D110).

**Nix and process**

- Flake source is the git tree: a file must be tracked before a build can
  see it. Never batch an edit and a `nix build` in the same step.
- nixosTest drives the guest from the host: RPC and socket interactions
  must run *inside* the guest via `machine.succeed`.
- `systemctl restart` returning does not mean readiness; wait on a port or
  a log line.
- Backgrounding a process inside `machine.succeed` hangs; run test daemons
  as real systemd services in the guest.
- Emacs lock files (`.#foo`) show up as untracked; ignore `.#*`.
