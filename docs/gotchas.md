# Gotchas, bugs, and problems

Every way this has actually failed, and the fix. Numbered, and cited from the
code and the docs by that number: a comment that says *why* cites the entry
that constrains it.

An entry earns its place by being something a competent maintainer would
otherwise reintroduce — a constraint, a trap, or a non-obvious fact about a
dependency. A fixed bug with no general lesson is not one, and neither is
anything about a mechanism that no longer exists; the code that carries the fix
is the record of both.

---

### N2 — v1's policy fragment shape is nested; the v2 baseline schema is flat — **FIXED**
- **What:** v1's `PolicyFragment` was nested (`capabilities.tools`,
  `files.read`, `budget.*`) with a `ListOp` algebra
  (`extra`/`only`/`remove`). `contracts/schemas/baseline.schema.json` defines a
  **flat** fragment (`tools`, `files_read`, `files_write`, `network`,
  `auto_accept`, `max_iterations`) where every field is optional and `null`
  means inherit.
- **Decision:** the contract wins — it is the generated Nix module's output and
  the thing two parts agree on. `decision/policy/fragments.py` implements the
  flat shape. The `ListOp` algebra outlived the move in the runtime overlay and
  was removed by D87: an overlay list now replaces, and the baseline stays plain
  lists that union.
- **Consequence:** `auto_accept` and `max_iterations` are absent from the
  baseline schema but are real policy. They are carried in the overlay fragment
  and default in `SCALAR_DEFAULTS`; if the Nix module needs to set them, the
  baseline schema gets a version bump rather than an undeclared field.


### N3 — `tool-manifest` kinds changed between v1 and v2 — **FIXED**
- v1 had `primitive` | `skill` | `runner-native`; v2 has `primitive` |
  `runner-native` | `server-native` (**D13**). `skill` is gone; `server-native`
  names what v1 did informally by special-casing `session_query` and
  `rag_search` inside the loop.
- **v2:** `decision/harness/loop.py` dispatches on the manifest `kind` instead
  of on hardcoded tool names, so a server-native tool is a registry entry rather
  than an `if name == …` branch.


### N4 — The manifest's `parameters` is an unvalidated JSON Schema — **FIXED**
- **What:** `tool-manifest.schema.json` types `parameters` as
  `{"type": "object"}` with a prose note that it is a draft 2020-12 schema.
  Nothing checked that it *is* one, so a malformed `parameters` would surface
  only when advertised to the model — or be silently permissive.
- **Fix:** `adapters/registry.py` runs `Draft202012Validator.check_schema` on
  every manifest's `parameters` at load time, and a malformed one fails the
  registry load (fail closed: no tool set rather than a bad one). Closed.
  remaining gap, closed.


### N13 — The second implementation of a contract finds what the first assumed — **FIXED**
- **What:** the runner's TOML reader failed on every shipped manifest:
  `not a key/value line: Read a UTF-8 text file. Returns the file's contents…`
- **Cause:** the manifests use `"""` multi-line strings for `description`, and
  the reader treated the body as key/value lines. Python's `tomllib` handles
  this for the server, so nothing had ever exercised the case.
- **Why it is the point rather than a nuisance:** D3 says server and runner
  hold two installations of one contract and CI proves they match. This is that
  proof doing its job on the first day it existed — the ambiguity was in the
  manifests all along, invisible while only one reader existed.
- **Fix:** the reader skips multi-line string bodies. All eight shipped
  manifests now load identically through both implementations, which is the
  first time that has been true.


### N14 — Draining stdout before stderr deadlocks the runner — **FIXED**
- **What:** the runner's first output loop read the child's stdout to EOF, then
  its stderr. A pipe holds ~64KB, so any tool writing more than that to stderr
  blocks in `write()` forever while the runner waits on a stdout that will
  never close.
- **Who hits it:** anything noisy — a compiler, a test runner, `set -x`, a
  shell tool with warnings. Not an edge case.
- **Why it would have been hard to find later:** the dispatch does not error.
  It hangs until its own timeout and returns partial output with
  `timed_out: true`, which reads as a slow tool rather than a runner bug, and
  the model is handed truncated output that looks complete.
- **Fix:** both streams are drained by threads feeding one channel. The
  collector keeps assigning sequence numbers in a single place, because they
  must be gapless and ordered and two threads numbering independently is
  exactly how a gap appears (**D26**). `vm-runner` covers it with a tool that
  writes 4000 lines to stderr and one line to stdout.
- **Also fixed while here:** the collector polls with a 100ms tick rather than
  blocking on `recv`, so the deadline is checked even while a tool is silent —
  a sleeping tool would otherwise not time out until it spoke.


### N15 — Joining the output readers before enforcing the timeout waits out the tool — **FIXED**
- **Symptom:** a tool told to sleep 30s under a 2s timeout returned
  `EXIT 0`, `timed_out: false` — thirty seconds later. The dispatch reported a
  clean success for a tool that had blown its deadline by an order of
  magnitude.
- **Cause:** the fix for **N14** put the stream readers in threads, and the
  collector joined them before checking the deadline. A reader only finishes
  when the child closes its pipes, so joining waits for the tool to exit *on
  its own* — the timeout could never fire while a tool was still running,
  which is the only situation it exists for.
- **Why it is worse than a missed timeout:** `timed_out: false` on the wire
  tells the harness the tool completed normally, so the model is handed a
  result it should have been told was truncated, and the appliance's own
  tool-timeout policy silently does nothing.
- **Fix:** check the deadline and kill the child *before* joining. Killing is
  what closes the pipes, so the readers then end promptly; the join is for
  reaping, not for waiting. Covered by `vm-runner`'s timeout subtest.
- **Worth noting as a pattern:** N14's fix introduced N15. Moving work into
  threads moved a deadline check behind a join, and the new failure looked like
  success rather than a hang. A fix in a concurrency path deserves the same
  suspicion as the bug.


### N16 — A probe with its own timeouts tests a system production does not run — **FIXED**
- **What:** `vm-runner`'s probe constructed `RunnerClient` with
  `started_timeout_s=10.0`, while the production default in `Settings` is
  **5.0s**. Dispatches were measuring 6.9–9.2s wall in the VM, so the test was
  green on numbers production would have failed on.
- **The same shape as any probe with a hardcoded limit:** the probe was a
  *more permissive* client than the server, so the test could not see the bug
  it existed to catch.
- **Rule this settles:** a probe that drives production code must take its
  timeouts, retries, and limits from the same `Settings` production reads.
  Anything it chooses for itself is a difference between the tested system and
  the shipped one.
- **Fix:** both probes read `Settings()`. `vm-runner` additionally *measures*
  the time to the `started` frame and asserts it against the production
  deadline, so the number is checked rather than assumed — D15's whole point is
  that this deadline is short on purpose, and a short deadline nobody measured
  is how a crash-looping runner becomes a ten-minute request.


### N20 — `pgrep` exits 1 on no match, so `machine.succeed` fails the passing case — **FIXED**
- **What:** a cancellation subtest asserted that no process survived cancellation with
  `machine.succeed("pgrep -u alice -f 'sleep 300' | wc -l")`. `pgrep` exits **1**
  when it matches nothing — which is exactly the outcome the test wants — so
  the *successful* case failed the build.
- **Why it is worth an entry:** the failure mode is inverted. A test written
  this way passes only when the thing it guards against has happened, and the
  error message (`command ... failed (exit code 1)`) points at the harness
  rather than at the property. Easy to "fix" by deleting the assertion.
- **Fix:** `machine.execute` with `|| true`, then assert on the output lines.
  The same trap applies to `grep`, `test`, and `diff` — any predicate whose
  exit code *is* the answer should be read with `execute`, never `succeed`.


### N22 — A CI check that inspected nothing and reported success — **FIXED**
- **What:** a new `vm-testscripts` check was added to parse each VM test's
  Python locally, because a syntax error otherwise fails the *driver*
  derivation with a line number in generated code (`testScriptWithTypes`) after
  the whole guest closure has built. The check passed. It also passed with
  deliberately broken Python in a test script.
- **Cause:** the `${self}` interpolation was lost while editing the flake
  through a script, so the build command globbed `/nix/tests/*.nix` — a path
  that does not exist. The glob matched nothing, the loop body never ran, and
  `touch $out` succeeded.
- **Why it is worth its own entry:** this is the second time in two days a
  check has reported a property it never examined (the first was a seccomp
  subtest that could not fail). The shared shape is that *absence of
  failure* is not evidence, and a check has to be shown failing before its
  passing means anything.
- **Fix:** `shopt -s failglob`, count what was inspected, and refuse if the
  count is implausible. Verified by breaking a test script and watching the
  build fail with the file, line, and message.


### N23 — Steering is delivered between turns, not within one — **KNOWN, TEST CORRECTED**
- **What:** `test_steering_folds_into_the_conversation` was flaky (3 of 4
  runs). Made deterministic, it failed every time — so the flake was hiding a
  real answer rather than causing one.
- **Measured:** instrumenting `Inbox.post` and `drain_steers` shows the steer
  posted to the *correct* inbox, but after **every** drain the turn performs.
  The turn task and the WS receive loop share one event loop, and the turn
  runs its iterations without awaiting anything that yields long enough for
  the receive loop to process a queued frame. Adding a third iteration does
  not help: the steer still lands after the last drain.
- **What this does and does not mean.** Approvals and interrupts are
  unaffected — both are *awaited* (`await_approval` blocks, `interrupted()` is
  checked between chunks), so the loop yields and the frame is read. D12's
  central claim stands and `vm-runner`'s approval and interrupt subtests prove
  it. What does not hold is steering *mid-turn*: a steer sent while a turn is
  running is delivered at the start of the **next** turn, not folded into the
  current one.
- **Disposition:** the test now asserts what the system does — a steer is
  drained before the next turn — and says so. Changing the behaviour means the
  loop awaiting a yield point between iterations, which is a harness design
  decision (it interacts with how a steer should affect an in-flight tool
  call), not a bug fix to slip into an unrelated commit.
- **Worth keeping:** a flaky test is sometimes a *correct* test of a racy
  system, and sometimes an incorrect test of a deterministic one. Making it
  deterministic first is what tells you which — here it turned "sometimes
  wrong" into "always wrong", which was the useful answer.


### N24 — VM tests fail as "Shell disconnected" under host load — **PROCESS**
- **What:** `vm-runner` failed twice with `RuntimeError: Shell disconnected`
  and then `Shell did not start in time`, naming no subtest and no cause. The
  code was unchanged and fine.
- **Cause:** host load average of ~140, from an unrelated compile workload
  sharing the machine. The guest log shows it reaching Basic System after
  **167 seconds** with `hrtimer: interrupt took 2211454 ns` — the driver's
  shell timeout expiring against a guest that is merely very slow.
- **Why it is worth an entry:** the symptom is indistinguishable from a real
  crash, and the instinct is to hunt for the subtest that killed the guest.
  Two builds were spent doing exactly that, and a third was spent adding
  `virtualisation.memorySize` on a theory that turned out to be unrelated.
  `uptime` costs nothing and would have answered it first.
- **Rule:** before diagnosing a VM failure that names no subtest, check the
  load average. A VM test is a real machine boot and it inherits whatever the
  host is doing — including work that is not yours.
- **Kept anyway:** `virtualisation.memorySize = 4096` stays. The `hog` subtest
  deliberately allocates, and bounding it by its cgroup rather than by the
  guest's total memory is correct independently of what prompted the change.


### N26 — The admin API has no socket to listen on — **FIXED**
- **What:** `NEUMAN_ADMIN_SOCKET` is configured by the module, read by
  `Settings`, and bound by nothing. `composition/adminapi/` mounts its routes
  on the same Starlette app as the browser surface, so the only place they are
  currently reachable is the TCP port.
- **How it was found:** `vm-appliance` waited 900 seconds for
  `/run/neuman/admin.sock` to appear. Nothing had ever asserted it existed,
  because until the module there was no assembled appliance to assert it on.
- **Why it is not a hole:** it fails closed. `adminapi/deps.py::peer_name`
  reads `SO_PEERCRED`, and a TCP connection has none — so it raises
  `AdminUnauthorized` and every admin route refuses. The comment in that
  function already anticipates exactly this case. What is missing is the way
  to *succeed*, not a way to bypass.
- **Why D9 still matters:** "two surfaces, one core" means admin and HTTP are
  thin adapters over the same operations. They are. What is absent is the
  second *transport* — a uvicorn listener on the Unix socket with the peer
  socket threaded into the request scope, which is the fiddly part: ASGI does
  not surface the peer socket, so `peer_name` currently has to guess at where
  uvicorn hid it.
- **A second thing the same investigation surfaced:** the admin routes are
  mounted at `/admin/...` while the browser surface uses `/api/...`. Both are
  on the same app today, so the admin API occupies a path prefix on the
  *untrusted network surface* — and the only thing keeping it shut is that
  peer-credential authentication cannot succeed there. That is fail-closed,
  but it is one routing mistake away from not being, and it is the strongest
  argument for the socket listener being a separate app rather than a second
  prefix on this one.
- **Fix (M9):** `composition/serve.py` runs two uvicorn servers over one app
  in one event loop — the TCP port and the Unix socket — so they share a pool,
  a registry, and one startup. The network server owns the lifespan; the admin
  server runs with `lifespan="off"`, because uvicorn runs an app's lifespan
  per `Server` and two would migrate twice and start two copies of every
  background job. A useful side effect: the socket appears only after
  migration, so a CLI that can connect is talking to a server that can answer.
- **And the guess was wrong in both branches.** The note above says
  `peer_name` "has to guess at where uvicorn hid it". It looked for
  `scope["transport"]`, which uvicorn does not set, and then for
  `scope["_peer_socket"]`, which nothing set. Both were dead, so it raised
  unconditionally: the admin surface had no way to succeed for its whole
  existence, and nothing noticed because a surface that always refuses is
  indistinguishable from a correctly guarded one. Reading `h11_impl` rather
  than guessing is what settled it — `get_remote_addr` returns `None` for an
  `AF_UNIX` socket because `getpeername()` gives a path string rather than a
  `(host, port)` tuple, so `scope["client"]` arrives as `None` and the socket
  itself is never put in the scope at all.
- **Where the credential is captured:** `adminapi/peercred.py` subclasses the
  protocol and reads `SO_PEERCRED` in `connection_made`. The obvious next step
  — storing it in `self.scope` — does not work: uvicorn sets `self.scope =
  None` at connection time and rebuilds it per request. What *is* read per
  request is `self.app`, so the subclass wraps the app around the uid it just
  read. That depends on ASGI's contract rather than on uvicorn's internals.
- **Why the uid is read once, at connection:** a connection outlives a
  request. Reading it per request would mean an account deleted mid-connection
  still authorizes, and a uid reused by a new account silently becomes the
  operator.
- **The test that proves it is the positive one.** Every other admin test
  asserts a refusal, and a surface with no way to succeed passes all of them —
  which is exactly what shipped. `test_a_request_over_the_socket_carries_the_peers_uid`
  runs a real uvicorn over a real `AF_UNIX` socket and asserts the request is
  attributed to the uid that made it. Verified by disabling the capture and
  watching it fail.


### N27 — Every admin *read* route was unauthenticated — **FIXED**
- **What:** `GET /admin/audit` returned the entire governance record — every
  actor, action and payload — to any caller who could reach the port. So did
  `/admin/policy/{name}`, `/admin/grants`, `/admin/status`, `/admin/conflicts`,
  `/admin/models` and `/admin/usage`.
- **Cause, and why it survived:** every route that *mutates* calls
  `require_operator(await admin_actor(...))`. Every route that only *reads*
  calls an operation that takes no actor and checks nothing. The pattern
  looked right wherever anyone had looked, because the mutating routes are the
  ones a reviewer's eye goes to — and `adminapi/__init__.py` said
  "authorization is checked inside those operations, not here", which was true
  of half the surface and false of the other half.
- **Made reachable by N26:** the admin routes are mounted on the same app as
  the session surface, so "anyone who can reach the port" meant the untrusted
  network surface. The Unix socket that was supposed to be the only way in
  does not exist yet.
- **How it was found:** `vm-appliance` asserted the admin API refuses over
  TCP, expecting 401. It answered **200**. The assertion was written to
  confirm a fail-closed property I had just claimed in N26 — and the claim was
  wrong.
- **Fix:** `adminapi/guard.py`, one middleware refusing any `/admin` request
  without an identified peer, before routing. Deliberately not seven
  `require_operator` calls: that fixes today's routes and leaves tomorrow's
  one forgotten line from the same hole, and an unauthorized read looks
  exactly like an authorized one. A guard makes protection the default and
  makes an exemption something you have to write down.
- **The test is derived, not listed.** `test_every_admin_route_refuses_without_peer_credentials`
  reads the router, so a route added next week is covered today. Listing the
  paths by hand would reproduce the original failure mode one level up.
- **Worth keeping:** I wrote N26 asserting this surface failed closed, and
  then wrote a test to confirm it. The test disagreed. An assertion written to
  confirm a belief is worth far more than the belief — and the habit of
  checking a property rather than asserting it in prose is what turned a
  documentation entry into a fix.


### N29 — `ReadWritePaths` is not permission to write
- **What:** the server unit lists `/run/neuman` in `ReadWritePaths` and runs as
  `neuman`. It still could not create its admin socket there:
  `PermissionError: [Errno 13]` on `bind()`, then a restart loop 195 deep.
- **Cause:** `ReadWritePaths` controls systemd's *namespace* — which paths are
  left writable when `ProtectSystem=strict` remounts the world read-only. It
  does nothing about ordinary Unix permissions, and `/run/neuman` is
  `0755 root root`. Two independent gates, and satisfying the unusual one is
  not satisfying the ordinary one.
- **Why the directory could not simply be handed to `neuman`:** `/run/neuman`
  is root-owned, and the server only writes beneath it. A directory the server
  could replace wholesale is not a boundary; the socket inside it is the thing
  with the mode that matters.
- **Fix:** a dedicated `/run/neuman/admin` at `0750 neuman neuman`, with the
  socket inside it. The parent stays root-owned. 0750 rather than 0755
  because traversal should be limited to the `neuman` group — the socket's own
  0660 is the check that matters, but a directory anyone can list is a
  directory anyone can probe.
- **Why no local test caught it:** the end-to-end check ran as the user who
  owned the directory, so the permission question never arose. The property is
  "an unprivileged service account can bind here", and that needs a machine
  with a real service account — which is what the VM tests are for (see
  `nix/README.md`, "why a part's own tests are not enough").


### N32 — The N27 fix closed authentication and left authorization open
- **What:** an account with socket reachability but no operator group could
  read the whole admin surface. `neuman-admin status` printed the appliance's
  schema version, live session count, tool count and conflicted accounts;
  `GET /admin/policy/{name}` returned any account's effective policy;
  `/admin/audit`, `/admin/grants`, `/admin/models` and `/admin/usage` the same.
- **Cause:** N27's guard checks that a peer *exists*. Six read routes —
  `_get_policy`, `_list_grants`, `_search`, `_status`, `_list_models`,
  `_usage` — still call operations that take no actor and check nothing, so
  once past the guard they answered anyone. The mutating routes were fine, as
  before, for the same reason as before.
- **How it was found:** `vm-appliance` gained a third account with exactly one
  of the two properties. Two accounts (one who can do everything, one who can
  do nothing) cannot distinguish "both checks work" from "one check works" —
  the same hole would pass. The third account is the whole test.
- **Fix:** the guard calls `require_operator` as well, so authority is a
  consequence of the route being under `/admin` rather than of a handler
  remembering. The mutating operations keep their own call: they need the
  `Authorization` for the audit record, and a check inside the operation
  protects a future caller that is not an HTTP request.
- **Why this is not the surface deciding (D9):** the guard calls the same
  `require_operator` the operations call. There is still one implementation of
  "who is an operator". What moved is *when* it is guaranteed to run, not who
  decides.
- **Worth keeping, and this is the third time:** N26 was "it refuses, so it
  works" (it could never succeed); an earlier one was "it refuses, so it is
  correct" (it refused for an accidental reason); N32 is "I fixed that class of
  bug" — and the fix addressed the half the failing test happened to exercise. A fix
  aimed at a symptom closes the symptom. The way to find the rest is to ask
  what *other* combination of inputs the same structural flaw would admit, and
  then build an account that has it.


### N35 — A fixture that constructs the thing under test proves only that the fixture agrees with itself
- **What:** three implementations of one contract held three different beliefs
  about a field nothing carried — the producer omitted it, the schema forbade it,
  and the consumer required it. Every real dispatch would have failed before the
  tool started, and every test passed, because the fixture hand-wrote the thing
  it was testing rather than taking it from the producer.
- **Why nothing caught it.** The fixture was written under a docstring reading
  *"of the shape the server derives"*. It was not that shape. So the whole VM
  suite — including the assertions about the contract — ran against a value no
  conforming producer could emit, while the value the server actually produces
  had never been through the consumer.
- **Nothing validated it on the way out or in.** The producer never checked what
  it emitted; the consumer's validation checked *meaning* rather than *shape*. A
  contract with two implementations and no enforcement on the path between them
  is a document, not a contract.
- **Fix:** the field is required by the schema and produced from a *required*
  argument rather than a defaulted one — a default would make the same failure
  quiet again — and the test builds its value with the production code and
  validates it against the contract before using it.
- **The rule this earns:** anything crossing a contract boundary in a test
  should be built by the production code and validated against the contract. The
  fixture was not a shortcut; it was a second implementation, and the two drifted
  in the only direction that is invisible — the one where the real thing is
  broken and the fake works (N38).

### N37 — Two of the three ISO-naming options evaluate clean and do nothing
- **What:** the demonstration image built as
  `nixos-minimal-26.11...-x86_64-linux.iso` through three attempts at naming
  it.
- **The three spellings:**
  - `isoImage.isoName` — renamed. Evaluates with a deprecation warning, and
    has no effect.
  - `image.fileName` — evaluates to exactly the string you set. Still has no
    effect: the file is named from `image.baseName`, and `fileName` is
    *derived* from it rather than the reverse.
  - `image.baseName` — the one that works.
- **Why it took three builds:** `nix eval` on either wrong option returns the
  name you wanted. The setting is accepted, the value reads back correctly,
  and the artifact is named something else. The only way to see it was
  `ls` inside the built output — which is a 1.4 GB build away from the edit.
- **The rule:** for anything that produces an artifact, check the artifact.
  An option that evaluates is not an option that took effect, and a rename
  that keeps the old name evaluable is the most expensive kind: it removes
  the error that would have told you.
- **Also here:** the ISO failed to evaluate at all when `demo.nix` and the
  module both set `boot.kernel.sysctl."kernel.unprivileged_userns_clone"`
  with `mkDefault`. Two `mkDefault`s at equal priority are a *conflict*, not
  a tie-break. The requirement belongs to the module that owns it, and the image
  should not restate it.


### N38 — Every model request went out unauthenticated
- **What:** `build_services` constructed `ModelClient(api_key=settings.gateway_api_key)`
  — the `NEUMAN_GATEWAY_API_KEY` *environment variable*. The NixOS module only
  ever sets `NEUMAN_GATEWAY_API_KEY_FILE`, deliberately, because a secret in a
  unit's environment is readable through `/proc/<pid>/environ` and the store.
  So on any real appliance `api_key` was `None` and every request to the model
  went out with no `Authorization` header at all.
- **The function that fixes it already existed and had no callers.**
  `Settings.require_gateway_api_key()` prefers the file form, strips the
  trailing newline, and raises a `ConfigError` naming the path when it finds
  neither. Its docstring says exactly what the design is — "secrets are passed
  as file paths so they never appear in the unit's environment; the inline
  value exists for tests and local development" — and `grep` found zero call
  sites.
- **Both halves were individually right.** The module passes a path because
  that is correct. `Settings` offers a reader because that is correct. The
  adapter takes a key because that is correct. Nothing joined them up, and
  nothing tested the join: `ModelClient` appeared in no test of any kind, so
  the whole path from disk to header was unexercised.
- **How it was found:** writing the gateway's first test. Not by the test
  failing — by reading `build_services` to see what the test should construct,
  and noticing it passed the wrong one of two very similar attributes.
- **Why it would have been expensive:** the symptom is a 401 from the provider,
  which reads as a wrong or expired key. An operator would check the key, the
  secrets manager, and the provider's dashboard — all of which are fine — long
  before suspecting the appliance never sent it.
- **Fix:** `_gateway_key(settings)` in `composition/app.py`, which returns
  `None` when no gateway is configured (an appliance with no model must still
  start — the session surface, the admin socket and every local dispatch work
  without one) and otherwise calls `require_gateway_api_key`, so a configured
  gateway with an unreadable key is a startup error rather than a 401 per
  request.
- **`checks.vm-gateway`** asserts on what a stub endpoint *received*: the
  Authorization header, that it carries no trailing newline, that the body sets
  `stream` and `include_usage`, and that the key does not appear in
  `/proc/<pid>/environ` while the file path does.
- **The rule, and it is the same one as N35:** an adapter whose only exercise
  is a fake is an adapter with no test. `FakeGateway` agreed with the harness
  by construction, which is what a fake is for — and it could never have
  disagreed with the real client about a key it does not have.
- **A smaller thing the same work surfaced:** `nix/` was not in `python-lint`.
  The VM probes are Python that drives production adapters — `client_probe.py`
  and `gateway_probe.py` are the only callers of some of it — and they had never
  been linted. An unused import in a new probe was what made it visible. `nix/`
  now has a `pyproject.toml` and is in the check, which immediately found two
  pre-existing errors.


### N41 — `.gitignore` silently excluded three contract fixtures
- **What:** `.gitignore` had `result` and `result-*` for Nix's build symlinks.
  Both were unanchored, so `result-*` matched at any depth and swallowed
  three `contracts/examples/**/result-*.json` fixtures.
  All three had never been committed.
- **Why it mattered:** the flake's source is the git tree, so
  `contracts-test` in CI validated a *smaller* fixture set than the same command
  did locally. `result-missing-sequence.json` is the fixture guarding D26's
  sequence gap-checking — the invariant that a runner stream with a hole in it
  is refused — and CI had never seen it.
- **Why nothing caught it:** the check passes either way. It reports what it
  validated ("8 schemas, 38 valid fixtures, 36 invalid") and nothing compares
  that count to what is on disk, so three missing fixtures look exactly like
  a smaller contract.
- **How it was found:** `git status --porcelain --ignored` while looking for
  stray build artifacts. The fixtures were sitting in the ignored list between
  `__pycache__` and a ruff cache.
- **Fix:** anchor the patterns as `/result` and `/result-*`, so they apply only
  at the repository root where Nix actually puts them.
- **The general form:** an unanchored `.gitignore` pattern is a glob over the
  whole tree, and a pattern written for a build artifact will eventually match
  something that matters. Anchor anything meant for the root. And a check that
  reports a count is only as good as something that knows what the count should
  be — a fixture suite cannot tell you about the fixtures it never saw.


### N42 — The demonstration image could not be opened in a browser
- **What:** the ISO boots, the appliance is healthy, `curl localhost:8443`
  answers inside the guest — and a browser on the host hangs forever with no
  error anywhere. No log line, no refused connection, no timeout message.
- **Two independent causes, identical symptom.** Both had to be wrong, and
  fixing either alone looks like no progress:
  - the server binds `127.0.0.1` by default, and a QEMU port forward (or a
    host-only network, or any bridge) delivers to the guest's *interface* —
    so nothing was listening where the packets arrived;
  - NixOS's firewall blocks by default and nothing opened the port.
- **The first is correct behaviour inherited wrongly.** D1 makes exposing the
  browser surface an operator's decision rather than a default, and the module
  is right to default to loopback. But this image *is* that decision: its whole
  purpose is to be opened in a browser, and its own console text says "open it
  in a browser", which on loopback is impossible. A default that is right for
  the module was wrong for the one configuration whose reason to exist it
  contradicts.
- **Why no test caught it:** `vm-iso` asserts the client is served, and does it
  with `curl` *inside the guest* — where loopback works. The test was checking
  the right property from the one vantage point that cannot see the bug. Every
  VM test in this repository talks to the guest from inside it (
  that: the driver runs on the host and cannot reach guest services), so this
  class of bug is structurally invisible to the whole suite.
- **Fix:** `bind = "0.0.0.0"` and an open firewall port, in `nix/iso/demo.nix`
  only. Safe because of what the image is — no persistent state, a public
  password, a lifetime in minutes. A real deployment keeps the loopback default
  and puts TLS in front.
- **Worth keeping:** the appliance was working the entire time. Health was
  green, the audit chain had entries, the socket answered. "Nothing is
  reachable" and "nothing is running" produce the same silence from outside,
  and the first diagnostic that separates them is a request made from *inside*.


### N43 — The client fetched the whole policy and showed one field of it
- **What:** `loadPolicy()` requested `/api/policy`, read `policy.models` for the
  model dropdown, and discarded the rest. Every composed value and its
  provenance was arriving in the browser with nowhere to display it.
- **Why it mattered:** the question an operator asks constantly while iterating
  on policy is *"what is in effect right now?"*, and the UI could not answer it.
  `neuman-admin policy show` could, which meant leaving the browser to check what
  the browser had already been told.
- **Fix:** a Policy panel fed by the response already being fetched — every
  field, ordered by what gets checked first (reach before limits), with the
  provenance layer in parentheses. No new endpoint and no extra request.
- **Worth keeping:** a client that fetches more than it renders is not wasteful,
  it is *hiding* something. The data was there the whole time.


### N44 — A missing key file was a restart loop, and a login form that could GET
- **What:** two failures on the same rebuild, both in how a configuration error
  surfaced.
- **The restart loop.** `services.neuman.gateway` configured with a key file that
  did not exist yet should be a clean startup refusal. It was a traceback and
  five restarts ending in `start-limit-hit`. `__main__` catches `ConfigError`
  around `Settings.from_env`, but `_gateway_key` raises it inside `create_app`,
  where only `RuntimeError` was caught — so the message naming the missing path
  was in the journal, buried under a stack trace and a restart loop. Now exits
  78, which `RestartPreventExitStatus=78` already knew what to do with.
  - **The lesson:** not every configuration error is findable by `from_env`. A
    gateway URL with an unreadable key is only detectable when the file is read,
    and that happens later than validation.
- **The login form.** It had `preventDefault()` in its submit handler and nothing
  else. The handler is assigned at the bottom of the script, so anything throwing
  above it left the form with its browser default — a GET, putting the username
  and password in the URL, the address bar, and history. Observed while the
  server was down.
  - `method="post"` was the first fix and was also wrong (N45). The form is a
    `<div>` now: it cannot submit, so there is no default to suppress.
- **Worth keeping:** a secret in a URL is not a bug you find by reading the
  code — it is one you see once, in an address bar, and cannot unsee. The
  handler was correct; what was wrong was relying on it being reached.


### N45 — Fixing the credential leak replaced it with a rejected form POST
- **What:** N44's login-form leak was fixed by adding `method="post"` to the
  `<form>`. The leak stopped and login stopped working: the page showed raw
  `{"error": "origin_not_allowed"}` and the journal logged
  `rejected cross-origin request  origin=null  path=/`.
- **Why:** a native form submission is not a `fetch`. It carries
  `Origin: null`, which the origin guard correctly refuses — the guard was
  right, the request was wrong. `method="post"` only changed *which* wrong
  thing the browser did by default: GET with credentials in the URL became
  POST with no usable Origin.
- **The actual fix is to have no default at all.** The element is now a `<div>`,
  not a `<form>`. A div cannot submit, so no ordering, no exception, and no
  early keypress can produce a native submission — and Enter is wired
  explicitly on both fields. What the browser would have done for free is
  exactly what leaked the password.
- **Both of my diagnoses were wrong before the right one.** First I assumed the
  handler was never attached (it was — `onsubmit` is assigned before the IIFE
  that could throw). Then I assumed a syntax error (the file parsed). The log
  line had said it plainly from the start: `origin=null path=/` is a form POST
  to `action="#"`, and nothing else produces that shape.
- **Worth keeping:** a guard that refuses a request is not evidence the guard is
  wrong. Two of the three "bugs" in this session's deployment were the appliance
  correctly refusing something it should refuse — an unauthenticated model
  request, and a form POST with no Origin. The thing to fix was upstream of the
  refusal each time.


### N46 — The CSP blocked the client's own script, and none of it had ever run
- **What:** `style-src 'self'; script-src 'self'` permits same-origin *files*
  and never inline content. The client was one HTML file with inline `<style>`
  and `<script>`, so the browser blocked both. **No JavaScript in the client had
  ever executed**, on any machine, ever.
- **Why it looked like it worked.** Everything visible was the server. The page
  rendered unstyled-but-recognisable; the login worked because a native `<form>`
  submission needs no script at all. Removing the form (N45) is what finally
  exposed it — "Sign in has no effect" was the first symptom, and the console
  had been full of CSP violations the whole time.
- **The header and its own comment disagreed.** The comment reads "the client is
  served from our own origin and inlines nothing it did not build", which
  describes what the client does while the policy forbids it. Two correct
  intentions, contradicting each other, written months apart.
- **Fix: split the client into `index.html` + `neuman.css` + `neuman.js`.** Not
  `unsafe-inline` — that would trade a real defence on the untrusted network
  surface (§3) for a single-file convenience, and the convenience survives
  anyway: those files still edit-and-reload with no toolchain. The script is
  `defer`red, which also removes N45's ordering hazard.
- **Why no test caught it:** `vm-iso` asserted `"<title>Neuman</title>" in page`.
  That passes with every script on the page blocked, because `curl` does not
  execute anything and CSP is enforced only by browsers. The test was checking
  that HTML *arrives*, and calling it "the client is served".
- **The test now** fetches every `href`/`src` the page names, asserts each
  answers 200, and — when the CSP has no `unsafe-inline` — asserts the page
  carries no inline `<style>` or `<script>`. It reads the real CSP header rather
  than assuming one, so relaxing the policy deliberately relaxes the test with
  it.
- **And the check first failed on its own explanation.** The page documents why
  it has no inline blocks, and a plain substring search found the word
  `<style>` inside that comment. Comments are stripped before the check now.
- **Worth keeping:** `curl` cannot test a browser. Every assertion about the
  client until now was "the bytes arrived", and the bytes arriving is compatible
  with a page that does nothing at all. Anything enforced *by the browser* —
  CSP, CORS, cookie flags, SameSite — is invisible to a test that does not run
  one.


### N47 — uvicorn does no WebSocket upgrades without a WS library, and none was declared
- **What:** the chat window showed "Not connected". The journal said
  `Unsupported upgrade request` and
  `No supported WebSocket library detected. Please use "pip install 'uvicorn[standard]'"`.
  `websockets` was declared in no `pyproject.toml`, no `flake.nix` package, and
  no VM test environment. **The entire `/ws` surface had never worked outside
  tests.**
- **Why the tests pass anyway:** `test_ws_surface.py` drives Starlette's
  `TestClient`, which implements WebSockets *in-process* and never goes through
  uvicorn. So the handler logic was well covered and the transport underneath it
  had no coverage at all — the same shape as N35 (a profile written by the test)
  and N38 (an adapter exercised only by a fake).
- **Why it survived every VM test too:** `vm-appliance`, `vm-restart` and
  `vm-iso` all drive the real server, but none of them opens a WebSocket. The
  HTTP surface is exercised with `curl`; the WS surface was assumed to work
  because its unit tests did.
- **Fix:** `websockets` declared beside `uvicorn` in every place uvicorn appears
  — `neuman-server/pyproject.toml`, the `neuman-server` package, `serverEnv`,
  and each VM test's Python environment. uvicorn's extras (`uvicorn[standard]`)
  are not usable here: Nix packages the base and expects dependencies named
  explicitly, which is the behaviour that made this silent.
- **`vm-iso` now performs a real HTTP upgrade** with `curl` and requires a
  `101`, plus asserts the phrase "No supported WebSocket library" never appears
  in the journal. A 101 is the pass even for a nonexistent session, because the
  session-not-found close (4404) arrives *after* a successful upgrade — the
  failure being caught is uvicorn declining to upgrade at all.
- **Worth keeping, and this is the third instance:** a test double that
  implements the transport tests everything except the transport. `TestClient`
  is the right tool for handler logic and cannot say whether the deployed server
  can accept a WebSocket. Anything where the double *is* the mechanism —
  in-process WS, a hand-written profile, a fake gateway — needs one test against
  the real thing or the mechanism is untested.


### N48 — The model catalog translated nothing
- **What:** sending a message answered
  `gateway returned 400: "deepseek is not a valid model ID"`. The harness passed
  the user's selected *local* name straight to the provider. `model_catalog` has
  carried `provider_model` and `endpoint` since the first migration, both written
  by `neuman-admin model set` and **read by nothing**.
- **What the catalog is for, and did not do:** policy grants a stable local name
  while an operator re-points it, and two providers can offer the same capability
  under different strings. That is D20's "routing is configuration, not a bundled
  service" — and none of the routing existed. Every request went to the single
  `gateway.url` whatever the catalog said.
- **Three dead things at once:** `provider_model` (never read), `endpoint` (never
  read), and `policy.models` (never checked against the catalog, so granting a
  name that resolves to nothing was silently accepted and became a provider error
  blaming the user's choice).
- **Fix:** a `ModelTarget` port, a `resolve_model` callable injected into the
  harness the same way `policy_for` is — the decision layer does not touch tables
  (D7) — and `ModelCatalogStore` doing the lookup, honouring `enabled`. An
  unresolvable name is now refused *before* the provider, with a message naming
  the operator action. `stream()` takes an optional `endpoint` that overrides the
  base URL per call.
- **The same drift, caught immediately this time.** Changing the `ModelGateway`
  port broke 18 tests because `FakeGateway.stream` did not accept the new
  keyword. That is N38's shape — port and fake drifting — except the tests
  actually drive the fake, so it failed loudly in seconds instead of shipping.
  The fake now *records* the endpoint, so a test can assert it arrived.
- **And a fixture that was silently wrong:** `surface.py` patches stores by name
  and had no entry for `ModelCatalogStore`, so the real store ran against a
  connection that does nothing, every name resolved to `None`, and six WS tests
  timed out ending in `model_error` before reaching the loop they existed to
  drive. A patch table keyed by name cannot tell you about the key you forgot.
- **Worth keeping:** a column written by one side and read by none is not
  harmless dead weight — it is a feature that appears to exist. `neuman-admin
  model set` accepted a `provider_model`, stored it, and displayed it back in
  `model list`, which is indistinguishable from working.


### N50 — root is not an operator, and that is the design

- **What:** a unit running as root was refused with `'root' is not an operator
  on this appliance`. The comment beside it said root "holds operator authority
  through `wheel`". It does not: `wheel` grants sudo to humans, and authority
  on this appliance is membership in `neuman-admin` in Neuman's account store.
- **Reachability and authority are separate, again.** Root reaches the admin
  socket trivially — it can traverse the directory and open the socket — but
  the core resolves the peer name and checks its groups, and uid 0 confers
  nothing there. This is the same conflation as N32's guard and N41's
  group-at-login.
- **Fix:** create an administrator account and put it in `neuman-admin`; give
  the process that account, not root. "Run it as root" is the reflex for a
  privileged automation step, and on an appliance whose authority comes from
  policy rather than uid the reflex is wrong.


### N51 — llama-server does not speak socket activation
- **What:** the first `on-demand` model started and died with
  `couldn't bind HTTP server socket, hostname: 127.0.0.1, port: 8081`. The unit
  was socket-activated with `Accept=no`, which hands the service systemd's
  *listening* fd — and llama-server ignores it, tries to bind the port itself,
  and finds systemd already holding it.
- **`Accept=no` is not "just start it on a connection".** It means "you are
  being handed a listening socket in `LISTEN_FDS`", and a program that does not
  implement the protocol cannot use it; llama-server has no such support.
- **Fix: `systemd-socket-proxyd`.** The socket activates a proxy, which does
  speak the protocol, and forwards to an internal port the runtime binds
  normally (`port + 1000`). `Requires` + `After` on the model unit is what pulls
  the runtime up, and the proxy's `TimeoutStartSec` has to cover a cold VRAM
  load rather than systemd's default.
- **The idle stopper has to stop the proxy too.** Leaving it running holds the
  model up through `Requires`, so stopping only the model restarts it
  immediately — an idle timer that achieves nothing while appearing to work.
- **A second port means a second collision.** The internal port can land on
  another model's public port: 8081 on-demand and 9081 always are fine until the
  first is selected. An assertion catches it — and the *first* version of that
  assertion counted every `always` model twice, because `boundPort` returns the
  public port for those, so it failed on valid configurations. An assertion that
  rejects a correct config is worse than no assertion, and it was caught only by
  checking the valid case as well as the invalid one.
- **Worth keeping:** socket activation is not a property you add to a unit. It is
  a protocol the program has to implement, and the failure when it does not is a
  bind error that says nothing about activation.


### N52 — `ProtectHome` hides the weights `BindReadOnlyPaths` was meant to expose
- **What:** an on-demand model failed with
  `failed to open GGUF file '/home/alice/models/…gguf' (Permission denied)` on a
  file that is `0644` and plainly readable to its owner.
- **Two causes stacked.** The unit had `ProtectHome = "read-only"` *and*
  `BindReadOnlyPaths = [ the model ]`, which cannot work: `ProtectHome` makes
  `/home` read-only or hidden at the namespace level, so there is nothing for the
  bind to reach. And underneath that, `/home/alice` is `0700` — no other account can
  traverse it at all, which no unit setting can fix.
- **Why the weights are there in the first place:** a quantized 27B is ~16 GB.
  Putting it in the Nix store makes every garbage collection a re-download, so in
  practice it lives in a home directory — which means the bind has to win over
  `ProtectHome` rather than the other way round.
- **Fix:** `ProtectHome = false` when the declared model path is under `/home`,
  keeping `read-only` otherwise, plus the bind. Still read-only: a model runtime
  has no reason to write to its own weights. And a build-time *warning* naming
  the 0700 problem, because the module cannot see directory modes and the symptom
  otherwise reads as a broken module.
- **Worth keeping:** two hardening settings that each look right can compose into
  something that cannot work. `ProtectHome` and `BindReadOnlyPaths` over the same
  path is not defence in depth; it is one setting cancelling the other.
- **Moving the weights was not sufficient either.** `/var/lib/neuman` is
  `0750 neuman:neuman`, and the unit ran as a `DynamicUser` — in neither the
  owner nor the group, so it still could not *traverse* to a file it could
  otherwise read. Three rounds of `Permission denied`, each on a different path,
  because I kept adjusting the path instead of the identity reading it. The
  runtime now runs as `neuman`, which is the honest identity anyway: these are
  the appliance's weights, served on the appliance's behalf, and an ephemeral
  per-unit user expresses nothing that account does not.
- **The real fix was to move the weights.** All of the above is about making a
  home directory work, and a home directory is the wrong place: the weights
  belong to the *appliance*. A second account uses the same model, deleting a
  user should not remove it, and nothing about a 16 GB blob is personal to
  whoever ran the download. `services.neuman.modelsDir` defaults to
  `/var/lib/neuman/models` — beside the rest of the appliance's state, owned by
  `neuman`, and traversable by the runtime without relaxing anything. The
  permission failure was a symptom of misplaced ownership, and fixing the
  symptom twice was the wrong instinct both times.


### N53 — Double-click to rename cannot coexist with click to open
- **What:** the session list opened a session on `click` and renamed on
  `dblclick`. Renaming was unusable: the *first* click of the double-click fired
  the row handler and loaded the session, so the transcript replaced itself while
  the title was being edited.
- **Why `stopPropagation` does not help.** It is on the `dblclick` handler, which
  runs after the `click` has already fired and bubbled. There is no ordering that
  rescues this — the events are sequential, and the first one has done its work
  before the second exists.
- **Fix:** a separate target. A small edit button, `visibility: hidden` until the
  row is hovered, with `stopPropagation` on its own `click`. Two actions, two
  targets, no ambiguity.
- **`visibility` rather than `display`,** so the button occupies its space while
  hidden — otherwise the title reflows the moment a row is hovered, which reads
  as the list twitching. And `:focus-visible` reveals it too, because a keyboard
  user never hovers.
- **Worth keeping:** "the title is the control" was a nice principle and wrong
  here, because the row already had a control. An interaction that overloads a
  target which already does something is not minimal; it is ambiguous, and the
  ambiguity is resolved by the browser rather than by the design.


### N57 — An attachment is a path, because everything else fights the architecture
- **What:** "attach a file to the session" looks like an upload. Three separate
  walls say otherwise, and each was found by trying:
  - **`~/.local/share/neuman/attachments/` is unreachable.** A `$HOME` grant
    expands to `home_data_dirs`, which never includes `.local`, so `within_reach`
    is `False`. A file copied there uploads fine and is invisible to every tool.
  - **The server cannot write into a home directory at all.** `/home/<user>` is
    `0700` and the server runs as `neuman`.
  - **No tool can accept bytes.** `fs_write` takes `content` as a *string*, so a
    PDF or image is corrupted; it is also `mutating`, so dispatching it trips
    approval. Base64 through a dispatch caps out near 6 MiB against the 8 MiB
    line limit (`_MAX_LINE_BYTES`), and no tool takes bytes at all — so neither a
    temp file nor a dispatch is a back door.
- **Fix:** attach a **path**, not bytes. The file already exists inside the user's
  reach, so the prompt names it and the agent reads it with `fs_read` — no copy,
  no duplicate, no retention policy, no new tool. A browser cannot supply an
  absolute path (deliberately), so the picker is the *existing* `/api/browse`
  chooser with `files=1`, which produces real paths because the listing runs in
  the runner as the user.
- **A dropped file is the same answer, not an exception to it** (D102). The
  walls above are the server's; since D55 the client runs as the user on the
  machine the browser is on, so it writes the dropped bytes into its own
  `drops/` and the attachment is still a path `fs_read` opens.
- **`python-multipart` is not installed,** so `request.form()` would have been an
  `AssertionError` → 500. Sidestepped entirely rather than declared: the raw-body
  design needs no parser, and a dependency declared in `flake.nix` but not
  `pyproject.toml` is the N47 shape waiting to recur.
- **The re-check is on every path, not the new one.** The client PATCHes the whole
  list, so checking only the addition would let a re-sent older list outlive a
  narrowed policy.
- **A test that only put the bad entry last proved nothing.** `attachments[-1:]`
  — a deliberately broken route — passed the first draft of
  `test_one_unreachable_path_rejects_the_whole_list`, because every case in it
  had the unreachable path in the final position. Fixed by asserting first,
  last, and middle. Another instance of the fixture-shaped blind spot this
  session (N35, N38, N47).


### N58 — An approval the socket dropped still looked approved
- **What:** clicking Approve removed the buttons and wrote "approved" on the
  card, whether or not the verdict reached the server. `send()` returns `false`
  when the socket is closed; `decide()` ignored the return value. The turn then
  waited out the full `approval_timeout_s` (300 s) and denied on timeout, so the
  session looked frozen for five minutes with no way to retry and no wrong-looking
  state to explain it — the only clue was a "Not connected." note that scrolls
  away.
- **Compounded by no reconnection.** `socket.onclose` noted the disconnect and
  stopped. A session parked on an approval is exactly when a socket idles out, so
  the two bugs met: dropped connection, dead buttons, no reconnect, recovery only
  by reload.
- **Fix:** `decide()` marks the card only if `send()` succeeded, so the buttons
  stay live and clickable; and `onclose` reconnects with capped backoff, except on
  the server's own 4401/4403/4404, where reconnecting would loop against a
  session that is genuinely gone.
- **Why no test caught it:** every approval test drives `fs_write` under the
  default `auto_accept = "safe"`, where reads bypass consent. `auto_accept =
  "never"` — the setting a cautious first deployment picks, and the one in use
  here — routes *every* call through consent, and had no test at all. There is
  now one. The client half is asserted against the source, like the route-existence
  guard: crude, and the only thing that cannot go stale without a DOM harness.


### N59 — "not valid UTF-8" is true, useless, and unanswerable from inside
- **What:** asked "can you see this image?", the agent ran `fs_read` on a PNG and
  got `not valid UTF-8`. It then tried `ls` and `file` via `shell` and got
  `command not found`, because the client runs a tool with the tool directory alone
  on `PATH` (D5) — anything else would be a binary no manifest describes. Two
  wasted turns, and it still could not say what the file was.
- **Fix:** `fs_read` reports what it found — `not a text file: /path (PNG image,
  107600 bytes)` — from a short magic-byte table. Enough to answer the question
  in one call; deliberately not `file(1)`.
- **The general point:** with no coreutils in the tool's environment, an error message is
  the *only* channel a tool has. An error that says what failed but not what is
  actually there leaves the model with no next move, and it will invent one.


### N61 — The flag was stored and the one surface that could confirm it hid the value
- **What:** `neuman-admin model set deepseek ... --vision` succeeded, and
  `model list` returned every column *except* `vision`. The list route ran
  `SELECT *` — so the row had it — and then hand-built a response dict that
  omitted the field. The CLI's table did the same.
- **Why that is worse than it sounds.** A registration that silently failed and
  one that worked were indistinguishable from outside. The only symptom was
  `fs_image` being refused mid-conversation with "the selected model does not
  accept image input", which reads as a server bug rather than as "you have not
  registered it yet". The operator has no way to check their own work.
- **Fix:** the route returns `vision`, and the table shows a `VISION` column.
  Tests assert the flag reaches the request body (`--vision` present and absent)
  and that the table distinguishes the two — the table test fails if the column
  is dropped, confirmed by dropping it.
- **The shape,** and the inverse of the usual one: this project keeps finding
  fields *written* by one layer and read by none (N43, N48). This is a field
  written and read correctly by the machinery, and invisible to the human who has
  to operate it. A `SELECT *` followed by an explicit projection is where it
  hides, because the query looks complete.
- **Worth noting for the next column added to `model_catalog`:** three places
  need it — the upsert, the resolve, and the *list*. Two of them are load-bearing
  for behaviour and fail loudly; the third is load-bearing for whether anyone can
  tell.


### N69 — `//` is a shallow update, and the first variable vanished
- **What:** the speak-aloud toggle disappeared from Settings. The unit was running,
  its socket existed, the model was resident — and `NEUMAN_SPEAK_SOCKET` was simply
  not in the server's environment, so `/api/me` reported `speak: false` and the
  client correctly declined to draw a control that could not work.
- **Cause:** two `lib.optionalAttrs` entries merged with `//`, each setting
  `neuman-server.environment.*`. `//` updates one level deep, so the second
  replaced the whole `neuman-server` attribute rather than merging into it.
  Confirmed in isolation: `({ s.environment.A = 1; } // { s.environment.B = 2; })`
  has attribute names `[ "B" ]`.
- **Why it was invisible:** nothing failed. The module evaluated, the system built,
  every unit started, and adding transcription silently removed speech. There is no
  log line for a variable that was never set, and the only symptom was a missing
  toggle in a panel.
- **Fix:** one `neuman-server.environment` assignment whose *value* is the merge of
  the two optional attrsets. `//` between sibling keys is fine; `//` between two
  entries that both descend into the same key is not.
- **The test was vacuous first.** The initial version counted assignments with a
  regex anchored to line start, and **passed against the deliberately broken
  module** because the split form put one assignment inline. Replaced with a test
  that evaluates the merge through `nix eval` and asserts both names survive —
  verified by reintroducing the split and watching it fail. A test of how something
  is *spelled* is not a test of what it *produces*.


### N70 — Headless browsing is blocked per-site, and the tell was the user-agent
- **What:** measured before building, because the whole design turned on it.
  Headless Chromium with its default user-agent returned **zero** results from
  DuckDuckGo's HTML endpoint — the endpoint that exists to be scraped — and eight
  block indicators from Google. With a real user-agent: **107 results** from
  DuckDuckGo, still blocked by Google and Reddit, fine on Hacker News and Amazon.
- **`curl` with the identical user-agent returned zero.** So a fetch-and-parse tool
  is *strictly worse* than a browser, which removes "just fetch the HTML" as an
  option rather than leaving it as a simpler alternative.
- **Conclusion:** no headless mode at all. A capability that works on some sites and
  not others, with no way to know which in advance, fails after someone has come to
  rely on it. `web_fetch` stays for what is reliably fetchable.
- **Worth keeping:** the user-agent string is the cheapest and largest single
  factor, and it is invisible unless you look. `HeadlessChrome/152.0.0.0` is what
  Chromium advertises by default, and sites match on it.


### N72 — `overflow-y: auto` does nothing without `min-height: 0`
- **What:** with several tool cards in the transcript, the chat window could not be
  scrolled. The user had to tab blindly to reach an Approve button, or zoom out until
  it appeared.
- **Cause:** `#log` is `flex: 1` in a column flexbox with `overflow-y: auto` — which
  looks correct and is not. A flex item's `min-height` defaults to `auto`, meaning
  "never shrink below your content", so the log was always exactly as tall as the
  transcript. The overflow moved up to the *page*, which pushed the composer and the
  approval buttons past the bottom of the viewport. `min-height: 0` is what allows
  the element to be shorter than what it holds, and it is the precondition for
  scrolling rather than an optimisation.
- **The symptom points away from the cause.** "I cannot scroll the log" sounds like a
  scroll bug; the log was fine and the *page* was overflowing. Nothing in the
  stylesheet looked wrong, because the missing declaration is absent rather than
  incorrect.
- **Also fixed:** panel caps were `45vh` each, and an aside plus a menu can be open
  together (they close their own kind, not each other), so 90vh of panels left the
  transcript and composer 10vh — the same off-screen-Approve symptom by another
  route. Now 38vh.
- **The test was vacuous first.** It searched the `#log` block for the string
  `min-height: 0` — and the explanatory comment above the declaration contains that
  string, so deleting the declaration still passed. Comments are now stripped and the
  match anchors to a declaration. Second time in two days that a test matched prose
  instead of code (cf. N69), and both times only reintroducing the bug revealed it.


### N73 — `ReadWritePaths` cannot override mode bits
- **What:** `neuman-gpu-sample.service` failed at every activation with
  `PermissionError: /run/neuman/gpu.json.new`. The unit had
  `ReadWritePaths = [ "/run/neuman" ]`, which looked like the answer and is not:
  that directive lifts *systemd's own* `ProtectSystem` protection. It cannot grant
  access the filesystem denies, and `/run/neuman` is `0755 root root`.
- **Which it is on purpose.** `/run/neuman` is root-owned, and the server only
  writes beneath it; a directory the server could replace wholesale is not a
  boundary. So loosening it would trade a boundary for a status file.
- **Fix:** `/run/neuman/gpu`, `0755 neuman neuman`, created by tmpfiles — the same
  pattern `/run/neuman/admin` already used for the admin socket. The unit's
  `ReadWritePaths` narrowed to that directory too, since a GPU sampler has no
  business writing outside its own directory even if it could.
- **Caught only at `nixos-rebuild switch`.** Every check passed: the module
  evaluated, the system built, and the unit is a timer whose first firing is after
  activation. A test now pairs the two — every `ReadWritePaths` entry under
  `/run/neuman` must have a matching neuman-owned tmpfiles rule — so the next one is
  a build failure rather than a failed unit.


### N74 — Chromium ignores `WAYLAND_DISPLAY` and defaults to X11
- **What:** every `browse` call failed with `the browser did not answer on port 9222
  within 25s`. The runner's environment was correct, `NEUMAN_CHROMIUM` resolved, and
  the Wayland socket was present — so the tool's own diagnosis ("probably no
  graphical session") was wrong, and the model reasoned itself toward `curl` on that
  basis.
- **Cause:** Chromium does not infer its display backend from the environment. It
  defaults to the X11 Ozone platform and exits with `Missing X server or $DISPLAY`
  even with a perfectly good `WAYLAND_DISPLAY` set. `--ozone-platform=wayland` is
  required and is not a hint.
- **Reproduced with `env -i`**, which is what made it findable: launching with
  *only* the variables `browse.py` sets showed the error immediately, where launching
  from an interactive shell inherits enough to mask it. A tool that runs from a
  systemd unit should always be tested with the environment that unit actually has.
- **Fix:** `_session_environment` already decides which backend it found, so it now
  reports that decision and the launch passes the matching `--ozone-platform`.
- **Worth keeping:** the tool's error message was accurate about *what* happened (no
  answer on the port) and misleading about *why*, and the model believed the
  speculation. An error that guesses at a cause is worse than one that reports only
  the symptom.
- **Later (D31 rewrite):** the server no longer starts Chromium, so the Ozone
  reconstruction is gone with `_session_environment`. Kept because the lesson is
  general: test a unit's code with the environment the unit actually has.


### N75 — `scrollHeight` after an append already counts the new element
- **What:** `add()` appended a node and *then* measured whether the reader was at the
  bottom. A tall node — a tool card with its arguments — pushes the distance past the
  120px threshold on the very frame that added it, so following stops immediately and
  the reader is parked above content they never scrolled away from.
- **Fix:** measure before appending. The arithmetic was right; the ordering was
  wrong, and ordering is what a later edit silently loses — hence a test that asserts
  the measurement appears before the `appendChild`.
- **The same bug was in the streaming path**, where `agentTurn.textContent +=`
  grows the element before the check. Fixed the same way.
- **Not the whole of the reported symptom.** The user also could not scroll at all,
  which was N72 (`min-height: 0`) — and the reason they still saw it after that fix
  was that the appliance had not been rebuilt, so the served CSS was the old one.
  Worth checking what is *deployed* before re-diagnosing: `curl`ing the served asset
  took one command and would have saved the second investigation.


### N76 — The scroll bug was growth, not appending, and I fixed the wrong thing twice
- **What the user reported:** with several tool calls awaiting approval, the page
  would not scroll down. It only caught up when the *next* model output arrived, and
  the Approve buttons sat below the fold — reachable only by tabbing blindly or
  zooming out.
- **What I fixed first (N72):** `min-height: 0` on `#log`, a real bug that made the
  page overflow instead of the log. Necessary, not sufficient.
- **What I fixed second (N75):** measuring `scrollHeight` before appending rather
  than after. Also real, also not it.
- **The actual cause.** A tool card is *created* short — a name and its arguments —
  and then **grows**: approval buttons appear, the state text changes, the output is
  appended. Those are `card.appendChild` and `textContent` writes on an element
  **already in the log**, fetched back out of `toolCards`. They never went through the
  append path, so nothing scrolled. The invariant was wrong: not "appends scroll" but
  "anything that changes the height of something already displayed scrolls".
- **Fix:** a `grow(mutate)` helper that measures, mutates, then follows. Every
  mutation goes through it — the two card handlers, the decide click, and both token
  streams. `add()` is now a thin wrapper over it.
- **Why it took three attempts.** Each earlier fix was a real bug in the same area
  with an overlapping symptom, so each one felt like the answer. The thing that would
  have found it sooner was reading the *mutation sites* rather than the scroll code:
  `grep -n "card.appendChild"` showed four writes to live elements, none near a
  scroll call. The user's description — "it doesn't scroll until the next LLM output"
  — said precisely this, and I kept mapping it onto the code I had already been
  looking at.
- **Two test corrections on the way.** The first version flagged card *construction*
  (before the card is in the log, where scrolling is meaningless), and the second
  flagged `replay`, which rebuilds a whole transcript and scrolls once at the end.
  Both would have pushed a pointless `grow()` into a loop that runs hundreds of times.
  Scoped to the live frame handler.
- **Also moved:** token counts were a `note` after every turn, so the transcript
  filled with lines that are only ever their latest value — and each one pushed the
  conversation further up, compounding the scroll problem. Now a status line below
  the composer, with reserved height so the message box does not shift as turns
  complete.


### N78 — Reasoning appended above the tool call it came after
- **What:** the model reasons, calls a tool, reasons again — and the second block
  appeared inside the *first* bubble, above the card. A turn with three tool calls read
  as one wall of thinking at the top followed by three cards, rather than
  reason → call → reason → call, which is the order it happened in.
- **Cause:** `reasoningTurn` and `agentTurn` are the elements tokens append to, and
  they were nulled only at `done`, on switching session, and on a new session — never
  when a tool call arrived. So `tool_proposal` added a card while both bubbles stayed
  open behind it.
- **Fix:** null both in the `tool_proposal` handler. Two lines, and the property is "a
  tool call closes the open bubbles", which is what the ordering means.
- **`replay` was already correct**, which is worth noting: it builds each element in
  document order and never reuses a bubble, so a reopened session always read
  correctly while the live view did not. A discrepancy between the two would have
  pointed straight at this.


### N79 — A second evaluation of the NVIDIA driver is not the running one
- **What:** the panel said "no GPU detected" on a machine with an RTX 3090. The
  sampler's own output had the answer: `Failed to initialize NVML: Driver/library
  version mismatch. NVML library version: 595.99`.
- **Cause:** the unit's PATH was pinned to `pkgs.linuxPackages.nvidia_x11.bin`, which
  evaluated to **595.99.02**, while the running kernel module was **595.45.04**. The
  `nvidia-smi` on the system PATH works and resolves to `nvidia-x11-595.45.04-bin` —
  two packages with the same name and different versions.
- **Fix:** `config.hardware.nvidia.package`, which is where the kernel module came
  from, exposed as `services.neuman.gpuTools` so a non-NVIDIA appliance can set it or
  leave it null.
- **The sampler now says *why*.** `available: false` previously meant four things — no
  driver, a version mismatch, a timeout, or a sampler that had never run — and an
  operator cannot act on any of them. It carries the reason, and the client shows it
  instead of "no GPU".
- **`nvidia-smi` prints that mismatch to stdout, not stderr.** Reading only stderr
  reported `exit 18` for the single most confusing failure available here: a tool that
  exists, runs, and finds nothing.


### N80 — `systemctl` is not on the server's PATH, and the failure was invisible
- **What:** unloading a model did nothing. The unit stayed active and the GPU stayed
  full.
- **Cause:** the server's PATH is postgresql only, so
  `create_subprocess_exec("systemctl", ...)` raised `FileNotFoundError` — which nothing
  caught. The route answered 500 rather than its own `unload_failed`, so the UI showed
  a generic error and the model kept running.
- **Fix:** an absolute path from the module (`NEUMAN_SYSTEMCTL`), and an `except
  OSError` so a missing binary is a *reported* failure. The guard matters more than the
  path: as written, the one thing the route could not do was tell anyone it had failed.
- **Worth noting:** the polkit rule granting this had never actually run, so "unload
  works" was unproven rather than broken. A capability added with a test that only
  covers the refusal path is a capability nobody has exercised.


### N81 — Three vacuous tests in one sitting, all matching prose
- **What:** the tests for N79 and N80 passed against the reintroduced bugs. Three
  separate versions:
  - searching the whole `.nix` file for the rejected package name — which appears in
    the comment explaining why it is rejected;
  - searching the *option block* — the name is in its `description` and `defaultText`
    too;
  - searching the whole sampler for `done.stdout` — which also appears in the success
    path, `return done.stdout.decode(...)`.
- **What worked:** matching the narrowest possible construct. The `default =`
  expression alone; the `if done.returncode != 0:` branch alone. Every broader match
  caught documentation.
- **The pattern, now five deep (N69, N72, and three here):** a test that greps for a
  string will match the comment that explains the string. Prose about code lives next
  to code. The only reliable check is to reintroduce the bug and watch the test fail —
  which took thirty seconds each time and caught all three.


### N82 — A directory listing ended the session, and the tool was working correctly
- **What:** a DICOM export of 4,080 files was listed, `fs_list` returned its first
  thousand names — `._FILE1`, `._FILE10`, `._FILE100`, … — and the next request died
  with `exceed_context_size_error` at 16,460 tokens against a 16,384 context. The
  session was over; the model never got to the work.
- **Cause:** not a bug in `fs_list`. It did exactly what it was asked, within its
  `limit`, under its `max_output_bytes`. The thousand names differed only in a serial
  number, so they carried about as much information as one line saying how many there
  were — and cost two thirds of the context to say it.
- **Fix:** past 200 entries, group by name shape with digit runs collapsed to `#`, one
  line per shape with its count and numeric range. The same directory now answers in
  263 bytes instead of ~12 KB. `summarise` forces either form, because the threshold
  is a default rather than a judgement about what a caller needs.
- **Worth noting:** every budget this tool respected was a budget on the *tool*. Cheap
  to run (one `scandir`, 5 s timeout, 64 KB cap) is not the same as cheap to *read*,
  and nothing in the manifest could express the second. A read-only tool with a
  bounded output is still able to end a session.
- **And:** the fix had to be kept away from the directory chooser. `/api/browse`
  dispatches the same tool and parses `d<TAB>name`; a grouped line parses as an entry
  named `FILE#`, which does not exist, so the chooser would have offered a directory
  that cannot be entered. It now asks for `summarise: false` explicitly, and `_parse`
  rejects a line with two tabs as well — the route and the parser get edited by
  different people at different times.


### N84 — The model was told where it was and then started somewhere else
- **What:** `python` refused a correctly-configured session with "`/` has no .direnv;
  nothing has realised its environment". The working directory was set, the project
  had a realised flake environment, and the same tool run by hand from that directory
  answered `numpy 2.5.2`.
- **Cause:** `working_dir` reached the *system prompt* and stopped. `run_turn` passed
  it to `build_request` with a comment calling it "a hint — this tells the model where
  to look", and nothing else ever read it. The runner had no concept of a working
  directory at all: nothing in the dispatch carried one. So
  every dispatch inherited the server's cwd, which is `/`. A tool asking "what is
  beside me" got the answer for `/`.
- **Fix:** an optional `working_dir` on `dispatch`, shape-checked in both
  implementations (D3), and a `cwd` for the tool process. `python` and `shell`
  stop needing a `cwd` default because
  `os.getcwd()` is finally true; `python`'s `cwd` survives as an override for *another*
  project's environment.
- **Worth noting:** this had been wrong for three features. The working-directory
  commit tested that the prompt names the directory, and that test passed throughout —
  it was asserting the half that worked. The dispatch was never checked, so "the model
  knows where it is" and "the tools run there" were never distinguished.
- **And the failure was legible in the wrong direction.** The refusal named `/`
  accurately, so it read as "this project has no environment" rather than "you are not
  in that project". A well-named error pointed at the wrong thing because the thing it
  described was correct.


### N85 — Refusing the majority case, with advice about a file that does not exist
- **What:** `python` in a plain directory answered "env_unrealised: has no .direnv;
  nothing has realised its environment. Ask the user to `cd` into this directory so
  direnv can build it (or run `direnv allow`)". The directory was a folder of DICOM
  files. There was no `.envrc`, there never would be, and the work — parsing a JSON
  file — needed nothing but the standard library.
- **Cause:** a design decision taken for the wrong workload. "Refuse rather than fall
  back" is right for a *project* whose environment is unbuilt, because running without
  the declared packages fails later as a `ModuleNotFoundError` that reads as a broken
  tool. It was wrong for every other directory, and most directories are not projects:
  14 of 37 project folders here have no `.envrc` at all, before counting the data
  directories where files merely serve as context.
- **Fix:** distinguish declaring from having. A `.envrc`, `flake.nix`, `shell.nix` or
  `default.nix` means somebody intended an environment, so an unbuilt one is still
  `env_unrealised`. A directory declaring nothing raises `no_environment`, which the
  tool treats as a non-failure and falls back to its own interpreter, saying so.
- **Worth noting:** the refusal was *accurate*. It correctly reported no realised
  environment and correctly described how to create one. It was useless because the
  question it answered was not the one being asked — the same shape as N84, where
  naming `/` truthfully pointed at the wrong problem. A precise error about the wrong
  thing is harder to notice than a vague one.
- **And the advice-coverage test earned itself.** Adding `no_environment` broke
  `test_every_resolver_refusal_carries_advice` immediately, because the resolver had a
  reason the tool had no sentence for. That test was written two commits earlier on the
  theory that a reason and its advice live in different files and would drift; they
  drifted on the next change.


### N86 — Compaction never fired, because nothing knew how big the window was
- **What:** two sessions ended mid-task. One on `exceed_context_size_error` at 16,460
  tokens against a 16,384 context; one on a tool call truncated mid-string that
  llama.cpp refused to parse and answered with a **500**, which ends the turn — the
  model's own malformed output arriving as a provider failure.
- **Cause:** `decision/harness/context.py` has had spill, prune and compact from the
  start, driven by `ContextConfig.context_budget_tokens` — default **96,000**. And
  `composition/app.py` built it as `ContextConfig()`. So a local model serving 16,384
  had its history managed against a ceiling six times too high: the manager reported
  that the turn fit, every time, and the provider disagreed. `grep -rn "context_size"`
  across the server returned nothing — `contextSize` went to llama.cpp's `--ctx-size`
  and was told to nobody else.
- **Fix:** `model_catalog.context_tokens` (migration 0005), forwarded by the module
  from the `contextSize` it already passes to the runtime, carried on `ModelTarget`,
  and used to build the budget per turn. Reserves `max_tokens` plus a margin, because
  prompt and completion share one window. Zero means unknown and keeps the configured
  default — unknown must not become a guess, since guessing is the bug.
- **Worth noting:** the model is now resolved *before* context is managed. It was the
  other way round, which is why the budget could not have been right even if the
  number had existed: history was trimmed before anything knew where the turn was
  going.
- **And the feature existed the whole time.** Two rounds earlier the right advice
  looked like "don't build compaction yet, see whether you need it" — while a working
  compaction system sat unreached behind one unwired constant. A subsystem with no
  configuration is indistinguishable from a subsystem that is absent.
- **The indicator is the other half.** A token count with no denominator cannot be
  read: 12,000 is comfortable on one model and fatal on another. The `usage` frame now
  carries the window and the client draws a fill bar beside the count — and draws
  nothing when the window is unrecorded, because a bar against a guessed ceiling is
  this bug with a nicer face.


### N87 — The step limit ended a turn without telling the model
- **What:** a turn debugging a DICOMDIR parser ran out of steps and ended with
  `max_iterations`. The model was mid-thought; the user got a turn-end reason and no
  account of what had been learned. Everything the turn established — which records
  parsed, which pydicom call worked, which invented method did not — was in the
  history and nowhere a next turn would look first.
- **Cause:** nothing told the model the budget was running out. The loop checked
  `steps < policy.max_iterations` and stopped, which is correct and silent. The limit
  itself is right: with `tokens_daily` unset — the default, and the state of this
  appliance — it is the *only* bound on a runaway turn.
- **Fix:** three steps from the end, a note is logged as a `user_message` instructing
  the model to stop investigating and reply with what it has established plus the one
  thing it would try next. Logged rather than attached to the request, because the
  projection turns a `user_message` into a `{"role": "user"}` entry — so it reaches
  the model the same way a steer does, survives a mid-turn compaction, and is in the
  transcript afterwards.
- **Worth noting:** the latch is load-bearing. Policy is re-read every step, so a
  version comparing the step count each iteration would re-arm the threshold if an
  operator raised `max_iterations` mid-turn, and send the note repeatedly. `warned`
  rather than arithmetic.
- **And the threshold needed two bounds, which running it found.** Flat at three
  steps, a four-step turn is warned on its first step and does no work at all — so it
  is capped at half the budget. Halved, a one-step turn computes a threshold of zero
  and was sent "you have 0 steps left", a note it could read and never act on — so a
  threshold of zero sends nothing. Both were found by printing the behaviour across
  limits 1–20 rather than by reasoning about the arithmetic, which had looked fine.


### N88 — A parser field that the baseline schema rejected
- **What:** adding `auto_accept_max` to `fragments.py` made the appliance refuse to
  load its own configuration: `BaselineError: baseline violates its schema:
  Additional properties are not allowed ('auto_accept_max' was unexpected)`. Every
  policy read failed, so `neuman-admin policy set` answered
  `unreadable_response: Internal Server Error`.
- **Cause:** a policy scalar has two homes — `SCALAR_FIELDS` in the parser and
  `$defs.fragment` in `baseline.schema.json` — and I added it to one. `fragments.py`
  names this exact failure in its own module docstring: the fields "were briefly
  absent from the schema while this parser accepted them, which made a baseline that
  set any of them fail validation at startup." It happened again, for the same
  reason, three fields later.
- **Fix:** the field in both, plus `test_every_scalar_the_parser_accepts_is_in_the_baseline_schema`
  — which compares `SCALAR_FIELDS | LIST_FIELDS` against the schema's properties, so
  the next field cannot be added to only one. The docstring said the two "are now
  checked against each other by fixture"; the fixture covered the values, not the
  field set.
- **Worth noting:** this cost a 12-minute VM boot to discover, and the test that
  catches it runs in 0.07s. The failure was unreachable from the Python suite because
  the surface harness fakes its connection and never loads a real baseline.
- **And it broke a neighbouring test by widening a `LIKE`.** `the CLI's write is in
  the audit chain` read `where action like 'policy%' order by seq desc limit 1` to
  check attribution. A user choosing their own approval mode is also a `policy.*`
  event — written by a *user*, not an operator — so the newest row became bob's and
  the assertion read the wrong operation's actor. Narrowed to
  `action = 'policy.overlay_set'`. A pattern match over an action namespace is a
  dependency on nothing else ever being added to it.


### N89 — Approve and Deny on calls nobody was asked about
- **What:** with approval set to green (`yolo`), every tool card still showed Approve
  and Deny. The buttons never went away, and pressing either did nothing — no inbox
  was waiting for a verdict, because the call had already run.
- **Cause:** two halves that did not meet. The client builds every card with an
  approve/deny row and removes it when `tool_decision` arrives. The harness's
  auto-approve branch called `_decide` — recording the decision in the log and the
  audit chain — and never emitted the frame. So the client was never told, and a
  verdict that had already been reached was still being offered.
- **Fix:** emit `tool_decision` with `consent: false` on that branch. The contract
  already described the case — *"consent=false means the gate decided alone"* — so
  the frame was specified from the start and simply never sent.
- **And the row should not have been visible to begin with.** It was drawn on
  `tool_proposal`, which the schema says is "emitted before the capability gate runs,
  so a proposal is not a decision and must never be rendered as one" — and buttons are
  a rendering of a decision about to be asked for. Under `safe` a read-only call
  flashed them for a few milliseconds; under `yolo` they stayed. There is now a
  `tool_approval_required` frame, emitted *after* the proposal and *before* the
  harness blocks, and the client reveals the row on that instead.
- **Worth noting:** the state line read `running · false`. That is a field's value
  rendered as prose, and the obvious misreading — "running: false" — says the opposite
  of what the frame means. It says `running · auto` or `running · you` now.
- **The order matters and is asserted.** The reveal frame must come after the proposal
  (the client needs a card to reveal controls on) and before the block (or they appear
  only once the verdict has timed out). A test drains frames and checks the indices
  rather than trusting that both exist.


### N90 — Reasoning, then silence: a truncated reply reported as a clean stop
- **What:** Neuman would reason for a minute and stop, with no answer and no error.
  Typing "continue" reliably picked it up and finished the thought.
- **Cause:** the loop asked only whether the reply contained tool calls:
  `if not tool_calls: return self._finish(STOP)`. A generation cut off at
  `max_tokens` has no tool calls and no finished content, so it took that path and
  the turn ended as a clean `stop`. `finish_reason` was captured from the provider,
  accumulated through the stream, and written to the event log as part of
  `response_final` — and then read by nothing.
- **Confirmed before changing anything.** The model's own journal showed five
  generations in six hours ending at `eval time = ... / 4096 tokens` — exactly
  `DEFAULT_MAX_TOKENS` — against natural stops at varied lengths. A direct request to
  the local llama.cpp endpoint with `max_tokens: 16` returned
  `finish_reason: 'length'`, so the string the fix keys on is the string this
  appliance actually sends.
- **Fix:** `finish_reason == "length"` continues the loop instead of ending the turn,
  which is what typing "continue" was doing by hand — the truncated reasoning is
  already in history, so the next step resumes from it. Bounded by `max_iterations`
  like any other step; with `tokens_daily` unset by default there would otherwise
  have been no second bound. A `truncated` frame tells the client, and a turn whose
  last step was cut off ends as `truncated` rather than `max_iterations`, which would
  name the bound hit second.
- **Worth noting:** this is the fourth bug in a row of one shape — a value recorded
  faithfully and consulted by nothing. `working_dir` reached the prompt and no
  dispatch (N84), `contextSize` reached llama.cpp and not the budget (N86), the
  auto-approve decision reached the log and not the client (N89), and here
  `finish_reason` reached the log and not the loop. Writing a thing down is not wiring
  it up, and the event log makes the gap invisible: every one of these *looked*
  observable.
- **The budget is now configurable**, which it was not: `HarnessConfig.max_tokens`
  existed and `Settings` did not carry it, so 4096 was the only reachable value —
  the `NEUMAN_HOME_DATA_DIRS` situation again, where "had no module option until it
  was needed" meant the default was the whole story. `services.neuman.maxTokens`
  now reaches it, with a test at each link: the module exports the variable, the
  settings parse and validate it, and the harness puts it on the request.
- **And a dead assignment hid a second bug.** The `truncated` flag was set inside the
  no-tool-calls branch, so a step that dispatched tools left it untouched — a turn
  that truncated once and then worked would still have ended as `truncated`. The line
  meant to clear it sat immediately before a `return`, where it could never run.
  Deleting it broke no test, which is how it was found: the flag is now assigned once
  per step, before anything branches on it.


### N91 — Four interacting failures made a long session unsurvivable
- **What:** six of fifteen turns in one afternoon ended as `model_error`, two of them
  after 21 and 35 steps of a research problem. Four distinct causes, which is why no
  single fix had helped.
- **The estimate was wrong in the dangerous direction.** `estimate_tokens` is
  characters/4 and its docstring called it "deliberately an over-estimate". Measured
  against this appliance's own tokenizer: prose 1.27x over, Python 0.88x, a JSON blob
  0.63x, **DICOM UIDs 0.26x** — `1.2.840.10008.5.1.4.1.1.88.59` is about one token per
  character. So a 25,396-token budget produced a 33,502-token request, compaction
  never fired once in four hours, and the provider ended the turn.
- **Fixed by anchoring to measurement, not by a better constant.** `usage.prompt_tokens`
  is the provider's own count and is in every OpenAI-compatible response, so history up
  to the last reply is *known* and only the few messages since are estimated. Taken
  from `pi` (`packages/ai/src/utils/estimate.ts`), which uses the same chars/4 ratio and
  the same anchor — the ratio was never the difference.
- **The prompt's own cost was unaccounted for.** `_prompt_budget` reserved `max_tokens`
  and a margin, and the system prompt carries every granted tool's description and
  parameter schema — thousands of tokens built per request and never projected from the
  log. Now reserved as `max(2048, window/16)`.
- **Overflow was fatal rather than recoverable.** It is the one model error with an
  obvious remedy: compact and send it again. Neuman returned `MODEL_ERROR` and ended
  the turn. Now classified from the provider's text (pi's pattern list, ~25 providers;
  the llama.cpp entry is the one this appliance emits), compacted *forcibly* — the
  arithmetic that said it fit was just proven wrong, so re-asking it would retry the
  same request — and retried twice before giving up with a message that names the real
  problem. The retry does not spend a step: the model never ran.
- **And the truncation fix from N90 introduced its own hard error.** Every
  `response_final` became an assistant message, so a truncated reply continuing
  produced two in a row — "Cannot have 2 or more assistant messages at the end of the
  list", a 400, arriving **213ms** after the second truncation. They are one message
  split by a token cap, so they are joined; a reply carrying `tool_calls` is never
  merged, because its `tool` answer belongs to it.
- **Worth noting about VRAM.** llama.cpp defaults to four parallel slots and gives
  *each* the full `--ctx-size`, so `contextSize = 32768` allocated 131,072 tokens of KV
  cache. Neuman runs one turn at a time per branch, and the workload it was sized for is one conversation, so three slots served nothing.
  `--parallel 1` buys a 4x larger window for the same memory: measured at ~19 KiB per
  KV token on a 27B Q4, 131,072 tokens costs the 2.4 GiB already in use.
- **Two of the nine tests written for this proved nothing** until they were checked by
  reintroducing the bug. The merge-guard test put a `tool` message between the two
  assistant turns, where the guard it was testing is never consulted. The budget test
  asserted `< window - reply - 2048`, which the margin alone satisfies, so it passed
  with the overhead reserve set to zero.


### N92 — `max_tokens` was a fixed ask, not a ceiling
- **What:** after the N90/N91 fixes a session still showed "…reply hit the length
  limit; continuing" twice in twenty steps. The recovery worked — the turn finished
  normally — but the seam was visible, and raising `maxTokens` would only have moved
  it later.
- **Cause:** the configured value was sent on every request regardless of how full the
  window was. So as history grew, prompt + reply eventually stopped fitting, and the
  failure landed as a provider error rather than as a smaller reply. A generous
  `maxTokens` made a late-session overflow *more* likely, which is the opposite of what
  raising it was meant to achieve.
- **Fix:** clamp per request to `min(configured, window − history − 4096)`, with a
  512-token floor and no clamping when the window is unknown. Taken from `pi`'s
  `clampMaxTokensToContext`, including its safety reserve. The ceiling can now be
  generous *because* it shrinks itself: measured on a 131k window with a 16k ceiling,
  the ask stays 16384 until history passes ~110k and then falls away smoothly.
- **And a `length` stop means two different things.** If the reply used its whole
  allowance the model had the room it asked for and wanted more — continuing is right,
  and compacting would discard history to fix something history did not cause. If the
  reply came back *shorter* than requested, the model cannot have caused that: the
  provider clamped it because the window was the binding constraint, and more steps
  would truncate again. `pi` draws the same distinction in `isRecoverableLength`. The
  second case now forces compaction, bounded by the same counter as an overflow retry,
  and does not spend a step.
- **Worth noting what pi does *not* do.** It injects no "tokens remaining" hint into
  the prompt — there is no such signal anywhere in it. The model is never told how full
  the window is; the harness sizes the request so the question does not arise. That is
  a better design than advising a model about its own budget, which it cannot verify
  and may ignore.
- **A wiring test proved nothing** until it was checked by deleting the clamp: it used a
  32k window with a 16k ceiling, where the *unclamped* value is already inside the
  bound being asserted. It uses an 8k window now, where no history size admits a 16k
  reply.


### N93 — Two limits that were the wrong *kind* of limit
- **`max_iterations` defaulted to 20**, which is a work limit dressed as a safety one.
  It was set to the restrictive end of the axis on the principle that every default
  here should be — and that principle is about *reach*: a forgotten field must not
  widen what an account may touch. Steps are not reach. A low value does not make the
  appliance safer, it makes legitimate work fail, and a research turn ended mid-thought
  at step 20 while debugging a parser (N87). Now 500: a number nobody reaches on
  purpose. What actually bounds a turn is `tokens_daily` and a human who can interrupt;
  this exists so a retry loop stops eventually.
- **`services.neuman.maxTokens` was one number for every model**, which is the
  `context_budget_tokens` mistake again (N86): a per-model fact held as a global
  default. A local 27B and a hosted frontier model have different output ceilings and
  one value can only be right for one of them. `model_catalog.max_tokens` (migration
  0006) states it per model, `--max-tokens` on `neuman-admin model set` and
  `localModels.<name>.maxTokens` set it, and the appliance-wide value is the fallback
  for a model that does not. The lower of the two wins — both are ceilings, and the
  window clamp still overrides both.
- **Worth noting how `pi` models this.** `maxTokens` is a *required* field on `Model`
  beside `contextWindow`, populated from a generated provider catalog rather than
  configured by a user: it is a fact to look up, not a knob to tune. And it has no step
  limit at all — `agent-loop.ts` is `while (true)`, ending when the model stops asking
  for tools. That is right for a local CLI with a watching human; an appliance serving
  accounts wants *some* ceiling, which is why the limit stays rather than going away.
- **The reservation had to follow.** `tokens_daily` meters against the reserved amount,
  so reserving 16,384 for a model that can emit 4,096 would refuse turns a tight daily
  budget could have afforded.


### N94 — Compaction had never once succeeded
- **What:** a 30-minute research session ended after "…reply hit the length limit;
  continuing". The log showed the overflow detected and compaction triggered — and
  then `POST https://openrouter.ai/api/v1/chat/completions 400 Bad Request`, one
  second later, for a session running entirely on the local qwen.
- **Cause:** the summarisation request in `_compact` carried no `model` and no
  `endpoint`, and `gateway.stream` is `base = (endpoint or self.base_url)`. So every
  compaction went to the configured default gateway — a hosted provider — naming no
  model, and was refused. One compaction attempt in three hours, one 400: an exact
  match. The N86 budget work was correct and made compaction *fire*, which is how a
  mechanism that had never run became visible.
- **Why the tests passed:** the fake gateway answers any request, including one with
  no model in it. A test asserting the *outcome* could not see a missing field. The
  new test asserts the request names the turn's model and goes to its endpoint.
- **Three things were wrong, and only the first was the reported bug.**
  - The request named no model or endpoint. Fixed by threading the turn's own through
    `manage` into `_compact`.
  - The whole head was sent as one message. The history being compacted is by
    construction about as large as the window it must fit inside, so a 68k-token
    history asks the model to read 68k tokens to free 68k tokens. It is chunked now,
    each piece summarised separately and the summaries folded once.
  - A failed summarisation was silent. It returns `""`, which looked identical to a
    history that needed no summary — so the turn ended on the retry bound reporting an
    overflow rather than a broken summariser. There is a `compact_failed` action now,
    and the harness ends the turn naming the cause.
- **Found only by running it against the real model.** Two failures no fake would have
  produced:
  - With a 128-token reply cap the model emitted 104 reasoning chunks and *no content*,
    so the request succeeded and the summary was empty. There is a reasoning fallback
    now, and a larger cap.
  - Reasoning cost 21x the answer: 64.1s with 2,140 reasoning chunks against 3.0s with
    `chat_template_kwargs: {enable_thinking: false}`. Across the nineteen chunks a long
    history needs, that is twenty minutes against seconds — the first chunked run was
    killed at a ten-minute timeout. Summarising is not a reasoning task.
- **Verified live, not only in unit tests.** 48 messages / ~4,082 tokens → 9 / ~1,280
  in one call; 160 messages / ~17,415 tokens → 9 / ~1,710 via nineteen chunks, both
  against this appliance's qwen with a deliberately tiny budget.


### N95 — The fit check fell back to the estimator it was built to replace
- **What:** a 29-step session ended after "…reply hit the length limit; continuing".
  The harness *did* continue — step 29 ran — and generated two tokens, because
  llama.cpp reported `n_tokens = 64103` against a 65,536 window. The context was 97.8%
  full, the reply clamped to its 512-token floor, and there was nothing useful to say.
- **Cause:** compaction never ran. Zero context actions in the whole session, with
  history at 64,103 tokens against a 38,503 budget. `manage` checks the size twice —
  after spilling and after pruning — and the N91 work set `edited = True` after any
  edit, which switched the check from the provider-anchored measurement to
  `estimate_tokens`. On this history the estimate is 14,040 against a real 64,337: a
  4.6x under-count, well inside the budget. So the anchor said "too big", pruning
  freed nothing, and then the estimate said "fits" and returned.
- **The cure was worse than the disease it treated.** Discarding the anchor after an
  edit is defensible — the measurement no longer describes the messages — but it hands
  the decision back to the estimator whose error on identifiers is the entire reason
  the anchor exists.
- **Fix:** spill and prune only ever *remove* content, so the measured total less the
  removed share is still anchored to a real count. Scaled by the proportion of
  characters removed, which over-states slightly because the removed part is
  estimated — and over-stating is the safe direction: it compacts a little early
  rather than overflowing. The bare estimate is now reached only when nothing has been
  measured at all, which is the first turn of a session.
- **Worth noting about the symptom.** "Continuing" was true and useless: the harness
  continued into a window with 1,400 tokens left. A truncation notice is only
  meaningful if there is room to continue *into*, which is a thing the harness can
  check and does not.
- **And the first regression test proved nothing.** It reintroduced the fallback on the
  `removed > 0` branch — unreachable for a fixture where spilling and pruning remove
  nothing, which is precisely the fixture that reproduces the bug. Breaking the
  *second fit check* instead fails it.


### N96 — A fallback that cleans up first can never run
**`fs_write`'s direct-write fallback had never run, and every write into a
directory that refuses rename failed with a traceback.**

Found by reading a five-hour research session's audit trail: the agent had
`fs_write` granted, `files_write = ["/home/alice"]`, `auto_accept = "yolo"`, and
every one of its three write attempts failed. It held five hours of intermediate
results in context because it could not put them on disk.

- **The failure is real and not exotic.** Writing a file atomically means
  `mkstemp` beside it and `os.replace`, which needs the removal right on the
  source. A directory can withhold that — a read-only mount, an ACL, an
  immutable file, a share with the sticky bit — and then the atomic write is
  denied while an ordinary one is not. The module docstring anticipated exactly
  this and said the tool "falls back to a direct write".
- **The fallback was unreachable.** The handler cleaned up its temp file first:

  ```python
  except OSError as error:
      if os.path.exists(temporary):
          os.unlink(temporary)          # <-- also EPERM, same reason
      if isinstance(error, PermissionError):
          return _write_directly(path, data)
  ```

  The cleanup is denied by the very rule that denied the rename, so it raised
  out of `main()` and the fallback below it never executed. The branch only
  worked in the configuration that does not need it — unlink permitted and
  *just* rename blocked.
- **The wreckage was mistaken for output.** Each failure left a `.neuman-*` temp
  file that nothing could remove, and `tool.dispatch` records a dispatch rather
  than its outcome. Reading the audit chain plus the orphaned temp files, it
  looked exactly like three successful writes.
- **Fix:** the fallback runs *before* any cleanup, and cleanup is best-effort and
  cannot raise. Best-effort cleanup must never be able to change which path the
  function returns on.
- **The test denies exactly the two calls** — `os.replace` and `os.unlink` —
  rather than making the directory unwritable: `mkstemp` fails there, taking an
  early return that never reaches the bug.

### N97 — An approval preview has to be the same call, not a weakened one
**Every preview was refused before the tool started, and the failure was logged
at a level nobody was running.**

The preview that renders a diff into an approval frame was dispatched through a
path granting it no write reach, on the reasoning that a preview must not be
*able* to write. Every one was refused: a tool's `needs` are declared by its
manifest and re-checked against what the account's policy grants, and an
`fs_edit` preview is an `fs_edit`.

- **The check was right and the dispatch was wrong.** Emptying the reach did not
  describe a safe call; it described an impossible one.
- **The failure was silent by construction.** `_preview_write` returns `None` on
  any failure, so an approval stays askable when its diff is not. That is the
  right behaviour and it hid this completely: the frame arrived without a diff,
  the client fell back to rendering the call's arguments, and the symptom was
  "the feature does nothing" with no error anywhere. The one log line that would
  have named it is at `debug`, and the appliance runs at `info`.
- **No test could have caught it.** `_preview_write` was tested against a fake
  execution that does not check reach, and the tool was tested directly in a
  shell. Neither exercises a preview through the real dispatch path, which is the
  only place the two disagree (N35).
- **Fix:** the preview is the same tool, the same arguments and the same reach as
  the write it describes. A preview derived any other way is a second
  implementation of the edit, and the two will drift.

### N98

**`use gpu = 1` and no GPU: transcription ran on the CPU for the whole life of
the feature, five times slower than realtime.**

Dictation felt broken — speak six words, wait ten seconds, text appears. The
suspicion was the new preview pipeline, which posts the audio so far while
recording so the message develops as you speak. It was not: the server log showed
a preview window posted at `20:37:13.278` and answered at `20:37:23.670`. The
previews were firing exactly as designed. The *model* took 10.4 s to transcribe a
2-second clip.

The reason was in the unit's own startup log, which had been saying so since the
feature was built:

```
load_backend: loaded CPU backend from .../libggml-cpu-haswell.so
whisper_init_with_params_no_state: use gpu    = 1
whisper_init_with_params_no_state: backends   = 1
whisper_model_load: CPU total size =  1623.92 MB
```

`use gpu = 1` is the *request*. `backends = 1` with only a CPU backend loaded is
the answer, and `CPU total size` is where the weights went. `nvidia-smi` agreed:
only `llama-server` on the device, with `large-v3-turbo` nowhere near it.

`pkgs.whisper-cpp` is a CPU build. The module defaulted to it —
`default = if config.backend == "whisper-cpp" then pkgs.whisper-cpp else pkgs.llama-cpp`
— with a comment justifying it: a GPU build is a multi-gigabyte compile, and the
module cannot see the hardware. Both clauses were true when written and neither
survives inspection now.

**Why this hid so well.** Nothing failed. A CPU backend is a *working* backend,
so there was no error, no refusal, and no degraded-mode warning — only a latency
that looked like a slow model, which is exactly what everyone expects a local
model to be. The one honest signal was three lines of startup log that read as
success unless you know that `use gpu = 1` is a question rather than a statement.
And the wrong suspect was adjacent and new: a feature *about* transcription
latency had just shipped.

**What was actually wrong with the reasoning.** "The module cannot see the
hardware" was the wrong frame. Everything else here already assumes the GPU
exists — the sampler, the VRAM panel, the speech unit that relaxes
`PrivateDevices` precisely to reach the device. An appliance whose purpose is a
local model on a local GPU had a *default* that quietly used neither.

The compile cost was also stale. `cache.nixos-cuda.org` serves these builds —
measured: 273 paths, 344 MiB, and of 17 "derivations to build" 16 were NVIDIA
tarball unpacks, leaving whisper-cpp itself as the only compile. The cache moved
from `cuda-maintainers.cachix.org` in November 2025 and the old host now answers
**401**, not a redirect, so a stale substituter fails as "not authorized" and
reads like a credentials problem rather than a moved cache.

Fixed with `services.neuman.gpu`, defaulting to `cuda`, which decides the one
runtime package; the flake declares the substituter so building Neuman is a
download rather than a discovery.

Measured after, on `samples/jfk.wav` (11 s) with the same `large-v3-turbo`
weights:

| build | encode | total |
| --- | --- | --- |
| CPU (`pkgs.whisper-cpp`) | 10,137 ms | 11,544 ms |
| CUDA (`cudaSupport = true`) | 170 ms | 2,963 ms |

Sixty times faster on the encode. Note which number was visible from the
outside: `total` differs by under 4x, because it includes model load and
ffmpeg — so even a direct before/after on wall-clock *understates* this by an
order of magnitude, and the encode figure is the one that tracks what a user
waits for per utterance.

- **CUDA is unfree, so the default can refuse to evaluate.** `nixpkgs.config`
  belongs to whoever instantiated `pkgs` and a module cannot set it
  retroactively. Forcing `allowUnfree` would decide a licence question on the
  operator's behalf — and for every *other* unfree package too. So the licence is
  checked (`builtins.tryEval` on the derivation path, because the refusal is a
  thrown error rather than a boolean) and reported as an assertion naming the one
  line that fixes it. Left to nixpkgs, the message is a wall of text about
  `cuda_cccl` that never mentions Neuman or the option that pulled it in.

- **The rule this is an instance of.** A capability that is *requested* and
  silently not granted is worse than one that fails, because the request reads as
  the grant. `use gpu = 1` in a log is a plan; `backends = 1` is the outcome, and
  only the outcome is a fact. When a local model is slower than it should be,
  check what backend it actually loaded before changing anything upstream of it —
  the first suspect here was a correct pipeline, and the evidence exonerating it
  was a timestamp pair in the server log.


### N99

**A finished turn presented as a frozen app: one uncaught render error left the
client stuck "busy" forever, with a healthy socket and nothing to interrupt.**

Reported as "neuman is frozen, perhaps something to do with the last python
script it ran". The script was the wrong suspect, and so was the server.

The evidence took about five minutes to assemble and all of it pointed away
from the reported cause:

```
10:05:17  turn complete  {"session":"869bf29…","steps":43,"reason":"stop"}
10:40:25  interrupt received  {"session":"869bf29…"}   ← and four more, 5s apart
```

The turn had ended cleanly **thirty-five minutes** before the first interrupt.
In `loop.py`, `_finish` awaits `_emit({"type": "done"})` *before* writing that
log line — so the `done` frame provably reached the browser. `ss` showed the
WebSocket still `ESTAB` from Firefox with empty send and receive queues, and the
server had never logged a close for it. No `python` process existed, no
model call was in flight, and no Firefox content process was above 1.5% CPU.

A healthy, idle, open socket attached to a UI that would not move.

**The defect.** `socket.onmessage` guarded only `JSON.parse` and called
`handle(frame)` bare. `setBusy(false)` lived in exactly two places: a socket
close, and the `done` case. So a throw anywhere in `done`'s handler before that
line skipped it, and because the socket was *fine*, the `onclose` fallback that
would also have cleared it never ran. The composer kept saying "Steer", Stop
stayed live, and every click sent a well-formed `interrupt` for a turn that had
been over for half an hour. The server dutifully logged all five.

**Why this hid so well.** Nothing failed in any place anyone was looking. The
server log ends with `turn complete` and a clean `reason: stop` — the record of a
*successful* turn. The socket stayed open, so there was no disconnect notice and
no reconnect. The transcript kept whatever it had already drawn, so the window
looked populated rather than broken. The single piece of evidence was an
exception in a browser console that nobody had open, and by the time it was
reported the only way to retrieve it was to not reload the tab — which is the
first thing anyone does to a frozen page.

The reported symptom also pointed at the wrong layer. "It froze after running a
python script" invites you to look at the dispatch, the tool, and the process it
started;
the actual fault was in a 10-line event handler in the browser, and the python
script was merely the last thing that happened to be interesting.

**What was actually wrong with the reasoning.** `setBusy(false)` was placed as
though it were part of *rendering* the end of a turn. It is not: the turn's end
is a fact about the session, and whether its last frame drew successfully is
unrelated to whether a turn is still running. Tying a state transition to the
success of the paint that reports it means any bug in the paint takes the
controls with it.

Fixed in `neuman-client/web/src/neuman.js`:

- `handle(frame)` runs inside `try`/`catch`/`finally`. The catch *shows* the
  failure in the transcript, naming the frame type, because a visible "this did
  not render" is reportable and a silent one is not; the original error is
  rethrown on a macrotask so the console still gets the real stack.
- The `finally` clears busy on `done` regardless. `setBusy` is idempotent
  (`paintComposerMode` reads DOM state and sets classes), so the healthy path
  calling it twice costs nothing.

- **The rule this is an instance of.** A state machine must not depend on the
  success of the code that *draws* its states. Put the transition where the fact
  is known, not where the fact is displayed — and make an unexpected failure
  loud in the surface the user is already looking at, because a diagnostic that
  exists only in a console is a diagnostic that will be destroyed by the
  reflex of reloading.

Found alongside it, same file, not the cause of this freeze but the same shape
of mistake: `fileView` built a numbered row — three elements, each a flex
container — for every line with no bound, while `.file` shows about forty rows
at `max-height: 420px`. `python` and `shell` may each return a megabyte, which
measured at 157,297 nodes for ~52,000 lines, all but forty of them laid out to
be scrolled past. Now capped at `FILE_VIEW_MAX_LINES = 2000` with the remainder
appended as one `pre`: 6,003 nodes for the same input, nothing hidden.


### N100

**The 27B drafted for itself and nobody had asked it to: tool-call generation
ran at 40 t/s for the whole life of the local model, with a 1.8x speedup
sitting behind one unset flag.**

Qwen3.8-27B ships a multi-token-prediction head, and the GGUF already on disk
carried it — `blk.64.nextn.{eh_proj,enorm,hnorm,shared_head_norm}.weight`,
sitting above the 64 main layers, with `qwen35.nextn_predict_layers` in the
metadata. Nothing had to be downloaded and no second draft model had to be kept
in sync with the first. The weights were there, loaded into VRAM on every start,
and never used.

`--spec-type draft-mtp` turns them on. Measured on the 3090, paired runs in one
session against an identical request, with and without the flag:

```
tool-call turns   40.7 t/s → 64.7–73.3 t/s   (1.6–1.8x)   acceptance 69–77%
prose turn        40.7 t/s → 47.9 t/s        (1.18x)      acceptance 39%
```

**The spread is the finding, not the average.** Speculation only pays when the
next few tokens are guessable, and a JSON tool call is close to the most
predictable thing a model emits: the key order is fixed by the schema, the
punctuation is forced, the argument names are given. Prose is the opposite and
got about a fifth of the gain. Neuman's workload is overwhelmingly the first
kind — a turn is a chain of tool calls with short reasoning between them — so
the number that matters here is the 1.8x, not the 1.18x. A benchmark that had
measured only prose would have reported this as marginal and it would have
stayed off.

What this cost, measured rather than estimated: 21,204 MiB resident without the
flag, 22,392 with, so **1,188 MiB** for the draft context. That leaves ~1.7 GB
free on a 24 GB card at `contextSize = 65536`. `contextSize` and this flag are
competing claims on the same memory, and this GPU has already failed a load with
`cudaMalloc failed: out of memory` — lower one before raising the other.

Re-measured on 2026-11-12, same card, llama.cpp build 11669, a tool call of
1,200 tokens and a prose answer of ~570: **83 t/s on tool calls and 56 t/s on
prose**, acceptance 80% and 43%. The head now roughly doubles a tool-call turn.

`--spec-draft-n-max` is deliberately left unset: this build defaults it to 3 and
the measured mean accepted length was 3.06–3.31, already at that ceiling.
Passing `3` explicitly would read as a tuned value while changing nothing.

One real caveat, stated because it is not visible from the throughput number:
speculative decoding accepts a drafted token only when it matches what the
target model would have sampled, so the output *distribution* is preserved, but
a given seed no longer reproduces a given string. If a future test ever asserts
on exact model text, that test and this flag are incompatible, and the flag is
the one worth keeping.

**The adjacent trap, not hit but one flag away.** The same tuning pass wanted
`--cache-type-k`/`--cache-type-v` to buy VRAM back. Mainline llama.cpp compiles
flash-attention kernels for `f16`, `bf16`, `q8_0` and `q4_0` only, and only when
K and V match; anything else silently drops the attention op to the **CPU** —
no error, no warning, no offload line. The nixpkgs build does not set
`GGML_CUDA_FA_ALL_QUANTS` (verified in the pinned tree's `cmakeFlags`: it sets
`GGML_CUDA` and `CMAKE_CUDA_ARCHITECTURES` and nothing else), so a mismatched or
unsupported KV pair here would present exactly as N98 did — a working appliance
that is inexplicably slow, with the cause invisible in the logs and the flag
that caused it looking like an optimization. `q8_0` for *both* is the only safe
smaller pair on this build, and it is now offered as exactly that: `kv_cache`,
one value written to both `cache-type-k` and `cache-type-v`, `f16` or `q8_0` and
nothing else. Measured on the 3090 at 64k against `f16`: 1,706 MiB less, the
same prefill at a 28k-token prompt (1,206 t/s against 1,216, so attention stayed
on the GPU), the same short-turn decode, and 6–12% slower decode with 28k
tokens in the window. 128k at `q8_0` holds 23,634 MiB, which loads but leaves
half a gigabyte beside a desktop. So `f16` stays the default and the compact
cache is the operator's trade of speed for window.

**The rule this is an instance of.** Weights the model already ships are not
self-enabling. A capability can sit fully loaded in VRAM, costing memory,
contributing nothing, and the only evidence is a flag that was never passed —
so "is every part of this model actually in use" is a question that has to be
asked deliberately, because nothing will ever report it as missing. The same
question applied to this model also found the vision projector unused: it is a
native vision-language model, `mmproj-F16.gguf` is published alongside the
weights, and only the speech runtime was ever started with `--mmproj`. The
projector is now a weights file in the preset, and the chat model is started
with it.

**The symmetric mistake, found by asking the same question and getting the
opposite answer.** The audit that found MTP also reported that sampling ran on
llama.cpp's defaults rather than the model's, and that this was "a quality
defect on every turn". It is not, and the reasoning is worth recording because
it is the same shape of error in the other direction.

This GGUF carries `general.sampling.temp = 1.0`, `general.sampling.top_k = 20`
and `general.sampling.top_p = 0.95` in its metadata, and llama.cpp reads and
applies them. With no sampling flags passed at all, `/props` reports:

```
temperature 1.0   (CLI default 0.80)   ← model's value, not the default
top_k       20    (CLI default 40)     ← model's value, not the default
top_p       0.95
repeat_penalty 1.0    presence_penalty 0.0
```

So `--temp 1.0 --top-k 20` would have been flags that change nothing — and the
two penalties the "defect on every turn" argument rested on were already at the
recommended values, the claim about them having been simply inverted. Only
`min_p` differs from the model card (0.05 effective, no `general.sampling.min_p`
key to override it), and it is left alone deliberately: a mild tail cut at
temperature 1.0 is defensible for tool-call reliability, and changing it in the
same pass as `--spec-type` would have made any quality shift unattributable.

The pairing is the lesson. "A setting that does nothing is worse than one that
refuses" (the `contextSize` docstring) has a converse: **a setting that is
already correct is worse to write than to leave absent**, because it reads as
tuned and invites the next person to tune around it. Verify the *effective*
value at `/props` before concluding anything is unset — the absence of a flag
is not the absence of a value, and here the weights themselves were the source.


### N101

**The assertion written to stop a key reaching the Nix store matched nothing,
and a check that cannot fail looks exactly like one that passes.**

`services.neuman.cloudProviders.<name>.apiKeyFile` takes a path to a secret.
The hazard it exists for is one keystroke away: `apiKeyFile = ./deepseek.key`
copies the file into `/nix/store`, which is world-readable, and the resulting
configuration is indistinguishable from the correct one at a glance.

So the option got an assertion:

```nix
assertion = lib.all (p: p == null || !(lib.hasPrefix builtins.storeDir (toString p))) ...
```

It never fired. `toString` on a Nix path literal yields the path *as written* —
the copy into the store happens later, when the derivation is instantiated — so
the comparison was against `/home/alice/projects/neuman/...` and `builtins.storeDir`
matched nothing. Every configuration passed, including the one the assertion was
written for. Measured directly:

```
{"asString":"/tmp/.../store.nix","hasPrefix":false,"storeDir":"/nix/store"}
```

What actually distinguishes the two forms is their **type**, not their text: the
safe form is a `str` and the copying form is a Nix `path`. Which also means the
option could not be `types.path` — that coerces both into the same thing before
any assertion sees them. It takes `either path str` so that the dangerous form
arrives intact and can be refused by name.

**Two general lessons.** A security assertion that has never been observed to
fail has not been tested, it has been *written* — and the failure mode of a
silent one is worse than having none, because it is read as coverage. This one
was caught only by evaluating a deliberately-bad configuration and expecting an
error; the three assertions beside it were checked the same way, and two of them
did fire correctly, which is what made the silence of the third visible.

And it is the same shape as N38, N48, N86, N89 and N94: a value that reached one
place and not the next, where everything *looked* wired up. Here the value was a
path and the next place was a comparison that could not see it.


### N102

**A model id is a claim about someone else's service, and nothing falsifies it
until a turn spends tokens on it.**

`services.neuman.cloudModels.deepseek.providerModel` was `deepseek-reasoner`.
DeepSeek retired that name on 2026-07-24 15:59 UTC — "fully retired and
inaccessible", not aliased to a successor. Every DeepSeek turn had been failing
at the provider for roughly ten weeks before anyone noticed, and it was found
while looking for something else entirely.

Nothing in the appliance was wrong. The baseline was valid, the schema accepted
it, the assertions passed, the provider was declared, the key was present, the
model appeared in the panel as selectable. The one fact that was false lived on
a server in another company, and the only thing that reads it is a turn.

**Why no check existed.** `vision`, `context_tokens` and `reasoning` are all
declared by the operator *precisely because* the provider's model id does not
carry them — see D35 and D36. The same reasoning stops one step short of its own
conclusion: if the capabilities attached to an id must be stated by a human
because the appliance cannot derive them, then the id itself is also a human's
statement and can also be wrong. The catalog treats `provider_model` as ground
truth and every other field as a claim about it.

**What the rates showed independently.** `inputCostPerM` and `outputCostPerM`
were 0.28 and 0.42, figures from a price list two model generations old. Pricing
drifts silently in the same way and for the same reason — it is a fact about a
remote service, recorded locally, with nothing watching it. The cost was
invisible while the numbers were only ever *displayed*; it became a correctness
problem the moment a session total was computed from them.

**The shape of the fix, and the shape rejected.** Fetching prices periodically
was considered and dropped: DeepSeek's `GET /models` returns context windows,
modalities and effort levels but no pricing, so a fetcher would mean scraping a
marketing page and trusting it to set what the operator is billed. The viable
check is narrower and uses structured data — poll `GET /models` and warn when a
declared `providerModel` is absent from the response. That would have caught
this in July. Rates stay operator-declared; the alert says when to re-read the
page.

Same family as N38, N48, N86, N89, N94 and N101: a value that was correct where
it was written and false where it was used. The novelty here is that the far end
is outside the repository, so no amount of internal consistency could have
surfaced it — only contact with the provider, or a check that deliberately goes
and asks.


### N103

**An asset added to `neuman-client/web/src` and not staged is invisible to the build,
and the build succeeds anyway.**

Adding the favicon was a handful of `cp`s and `<link>` tags. The derivation builds
from `src = ./src` and installs with `cp -r ./*`, so there is nothing to
configure — an asset in that directory is served. Except the first build
produced a store path holding exactly the three files it held before, and said
nothing.

`neuman-client/web/default.nix` takes `./src` as a path, but the flake's source is a
**git tree**: Nix copies tracked files, and the new image files were untracked, so
they never reached the build. The derivation did what it was told, `cp -r ./*`
copied everything it could see, and the result was a correct build of the wrong
input. `git add` on those files and nothing else fixed it.

The failure mode is what makes it worth recording. Nothing errors. The page
loads, the `<link>` tags are present and well-formed, and the icons 404 — which
in a browser is a silently absent favicon, the one asset whose absence looks
identical to a cache miss. It would have been found in a browser eventually and
blamed on anything but the packaging.

Same family as N38, N48, N86, N89, N94, N101 and N102: a value correct where it
was written and absent where it was used. The novel part is the boundary — not a
field that failed to be read, but a *file that was never there*, because the
thing deciding what exists is git rather than the filesystem.


### N104

**A `CancelledError` escaping one dispatch left a `tool.dispatch` in the chain
with no result after it, and the turn stopped without a terminal frame.**

Reported as "it just froze after an `fs_search`". The audit chain said exactly
that: session `840535d2`, seq 1648, `tool.dispatch` for `fs_search`, and
nothing after it. No `turn complete` in the journal, where every healthy
session has one.

What it was *not* is most of the work. The server was sleeping with **zero open
sockets** and no child processes — so it was not blocked waiting on a tool, and
the hang was not in the read path. `_collect` guards every branch with a
deadline and `await_approval` fails closed at 300s; neither can hang. The seven
dispatches in two seconds looked damning and were not: a healthy session shows
the same 30-40ms bursts, because parallel tool calls from one assistant message
arrive together. The turn *died*, it did not stall.

`_dispatch` caught `Exception`. `asyncio.CancelledError` inherits from
`BaseException`, so an interrupt or a dropped WebSocket skipped the handler and
propagated out between the `tool.dispatch` append and the `_tool_result` write.

The cost is not one lost line of transcript. Every subsequent model call needs a
`tool` message per `tool_call_id`, so a missing result makes the conversation
**unsendable** — the next request never goes out. The journal agreed: two
completions for the whole session, then silence. And `_run_tool` had no handler,
so the escape killed `run_turn` before `_finish()`, meaning no `done` frame. The
client renders a turn as in-flight until `done` arrives, so a dead turn and a
working turn look identical from the browser. `live_sessions: 5` and a
`released stale reservations` warning were the same leak from earlier instances.

Fixed at both levels, because they are two different bugs wearing one symptom:
`_dispatch` now catches `BaseException`, writes the result, and re-raises a
cancellation; and `run_turn` wraps its body so *every* exit emits a terminal
frame. The inner fix prevents the lost result; the outer one guarantees the UI
unfreezes even if some other inner path escapes later.

Two things worth keeping. First, the `finally` that sends the runner's `cancel`
runs on the **success** path too, and `cancel` opens its own connection — so a
`RunnerUnavailable` raised there would discard a dispatch that had already
succeeded, and would do it *past* the handler rather than through it. That is
an earlier fix introduced this one. Second, the diagnosis came from the
audit chain and the absence of open sockets, not from the code: the chain's
"last thing that provably happened" is what distinguished a turn that died from
a turn that stalled, which is the whole point of recording governed decisions.


### N105

**A PNG on a tool's stdout killed the turn, and the `tool_result` that would
have explained it was the write that failed.**

Session `9b0519f1`, the Blender animations task. The model was probing where it
could write and ran a script calling `save_render("/dev/stdout")`, deliberately
to see whether that channel worked. It did: a real PNG came back through
`shell`'s stdout.

The chain, each link ordinary on its own:

* the runner decodes captured output with `errors="replace"`
  (`adapters/runner.py:272`), which repairs invalid UTF-8 and **leaves NUL
  untouched** — a NUL is a valid code point, so there is nothing for the
  replacement handler to object to;
* that string became the `tool_result` payload and went to `EventStore.append`,
  which inserts it as `Jsonb` into a `JSONB` column;
* `jsonb` is text underneath, and Postgres refuses a NUL outright:
  *"unsupported Unicode escape sequence … cannot be converted to text"*;
* the exception escaped `_run_turn` into the catch-all, which reported it as
  `model_error` and ended the turn.

**The cost was not a lost line of transcript.** The failing write *was* the
`tool_result`, in its own transaction, which rolled back — so a tool that had
been proposed, approved and actually run had **no recorded outcome**. The
session shows 32 proposals, 32 decisions, 31 results. That is a hole in the
governance record, which is a different and more serious thing than a formatting
defect, and it is why the fix is at the event store rather than at the one
decode that happened to produce the bytes.

Fixed by substituting the Unicode replacement character for NUL in
`events.sanitize`, called after `redact` — order matters, because redaction is
exact-equality and a value with a NUL already replaced would no longer equal the
secret it came from, so sanitising first would let a credential through. Every
substitution is recorded as a pointer in `redactions`, because a sanitised
payload is no longer verbatim and a log that quietly rewrote its own contents
would be worse than one that crashed.

The rule in `events.py` said *"Verbatim. Nothing is normalized except secrets."*
That was a policy statement colliding with a storage limit nobody had checked,
and the collision was invisible at the layer that wrote the rule. It now names
both exceptions.

Two things worth keeping. **Writing the fix reproduced the bug.** A comment in
`sanitize` originally contained a literal NUL to show what it matched; Python
refuses to compile a source file containing one (`SyntaxError: source code
string cannot contain null bytes`), so the module would not import. The
character has to be described, never included — which is the same lesson one
layer up. And **`FakeEvents` silently dropped the `redactions` argument**, so
every claim about what the log records as altered was untestable until the fake
was fixed. The real `EventRow` does not read the column back either, so nothing
anywhere exercised it: exactly the fixture-agrees-with-itself shape the README
warns about.


### N106

**Neuman called an earlier session's report "confidently wrong" when the
appliance had been rebuilt between them.**

Asked to diagnose a failed turn, Neuman read the current `session_query` handler,
saw that it returns event payloads, and concluded the previous session's claim
that the tool was metadata-only had been mistaken. The handler had in fact been
changed a few hours earlier, and that report was accurate when written.

The diagnosis was otherwise excellent — it found N105 from the payloads, traced
it through four files, and noticed the 32/32/31 count. The one wrong conclusion
came from a gap in the record rather than in the reasoning: **the log holds every
event and nothing about the code that produced them.** Reading a log written by
a different build is routine on an appliance whose owner rebuilds it, and there
was no way to tell.

Fixed by stamping the server's own store path into each turn as
`harness_event{type: "build"}`, from `NEUMAN_BUILD_ID`. A store path is a content
hash, so two turns carrying the same value provably ran the same code, and it is
the only build identifier that already exists and cannot drift from what is
running. Per turn rather than per session, because a session outlives a restart
and the useful question is what was running when *this* turn happened.
`harness_event` rather than a new kind, so `projection.py` already ignores it and
it costs the model no context.

Worth stating as a general shape: **an agent asked to diagnose its own history
will read current code against old logs, and will reach for "the earlier report
was wrong" before "the system changed".** Any record meant to be self-diagnosed
has to carry its own provenance, or the reasoning is sound and the conclusion is
not.


### N107

**Every file Neuman wrote came out `0644`, so an edit silently changed a file's
permissions — and a shared directory could not be shared.**

`write_plan` writes to a temp file and `os.replace`s it into position, which is
the right shape for an atomic write and swaps in a *new inode*. Whatever mode
the original had is therefore gone unless it is carried across. It was not:
`mkstemp` creates `0600`, which is not what a file the user asked for should
look like, and the fix for that was an unconditional
`os.chmod(temporary, 0o644)`.

Right for creating a file in your own home. Wrong as a universal rule, in two
directions at once:

* **a `0660` file in a group-writable directory came back `0644`** — so a
  colleague who could edit it a moment earlier now cannot. On a shared
  filesystem that is the agent silently locking people out of their own work;
* **a `0600` file came back `0644`** — a widening nobody asked for, on a file
  whose restrictive mode was presumably deliberate.

Both from the same line, and neither visible in the tool's output: the write
succeeded and reported success.

Fixed by deciding the mode from the file being replaced, stat'ed at write time
rather than read from `Plan.created` (which was decided when the diff was
computed, a different instant):

* **an existing file keeps its own mode.** An edit changes contents, never
  permissions.
* **a new file takes `0666 & ~umask`**, which is what a plain `open()` would
  have produced. Honouring the umask rather than naming a number is what lets
  the surrounding directory's convention win — so `umask 002` in a setgid share
  yields `0664`, group-writable, and two people's agents can collaborate on one
  file.

`_write_directly`, the fallback for a directory that denies rename (N96), was
always correct — `open()` preserves an existing mode and uses the umask for a
new file. Only the atomic path was wrong, which is the path almost every write
takes.

Worth noting how this was found: not from a bug report, but from asking what a
shared directory needs. The permission model for one turned out to need nothing
new — a dispatch runs as the acting user, so the filesystem's own permissions and
ACLs already apply — and the only thing genuinely missing was this. A feature
that needed no mechanism still surfaced a defect, because the question "what
would two people sharing a directory need" exercises paths that one person on a
workstation never does.

The three tests were each checked by reverting the fix: two fail, and the third
— that a new file under the default `umask 022` is still `0644` — passes either
way, because the old line existed to serve it and the new code
preserves it.


### N109

**A temp file left by a failed write looked like content.**

Four `.neuman-*` files were found in real project directories, carrying
1.3 KB and 1.7 KB of Python. They are the atomic write's temp files: `mkstemp`
beside the destination, then `rename`. Normally one exists for microseconds.

These survived because the dispatch's rename failed and the cleanup is denied
by the very rule that denied the rename — which N96 already documented and deliberately tolerated, because
best-effort cleanup must not mask the error it is cleaning up after.

Tolerating the leftover was right; leaving it *full* was not. A zero-byte file
with a dotted name is a leftover someone scrolls past; a 1.7 KB one looks like
work they might need, and two of these were copies of scripts that also existed
under their real names. So when unlink is refused the file is now truncated
instead: `truncate` is a write rather than an unlink, so it succeeds exactly
where the removal is refused.

The files cannot move somewhere tidier, which is worth recording because it
looks like they should. `rename` is only atomic within one filesystem, so a
temp file in a central directory breaks the moment the destination is a separate
mount — intermittently, and presenting as a permissions bug. And a central
directory would have to be added to every profile's write list, granting write
reach to a path nobody asked for. The temp file sits beside its target because
that is what makes the write atomic, which is what makes the diff in an approval
frame worth trusting.


### N112

**`nix log` answered "not available" for a VM test that had just passed, and the
reason was not a cache hit.**

A subtest was added to `vm-runner`, the check built and exited 0, and
`nix log .#checks…vm-runner` then reported *"build log is not available"*. The
obvious reading — Nix returned a cached output so the subtest never ran — was
wrong, and worth recording because it wasted a cycle and nearly produced a
confident claim in the other direction.

What actually happened: every `nix` invocation re-evaluates the flake, and the
test derivation depends on `${self}`, which is the whole working tree. Editing
*any* file — including `docs/gotchas.md`, to write this entry — changes the
derivation hash. So the `.#checks…` attribute the second command resolved was a
**different derivation** from the one the first command built. There was no log
for it because it had never been built, which looks identical to a cache hit and
is not one.

`--rebuild` is not the fix either: it compares against a prior valid build, so
with no output in the store it refuses with *"some outputs are not valid, so
checking is not possible"* rather than running anything.

The habit that actually works is to capture the **output path** at build time
(`nix build --print-out-paths`) and read the log for *that* store path, rather
than re-resolving the attribute afterwards. An attribute is a question about the
current tree; a store path is the thing that was built.

This is the second instance today of the harness misleading rather than the test
being wrong. The first was `nix build … | tail -3; echo "exit=$?"`, which reports
`tail`'s status and had already turned a *failing* `vm-appliance` into a reported
pass. "Absence of failure is not evidence" applies to the thing reading the
evidence as much as to the thing producing it — and in a repo where the test
inputs include the documentation, "the same test" is a narrower idea than it
looks.


### N114

**Every tool_call_id was present and the provider still answered 400; the run
was split, not short.**

A session died on its first turn with `gateway returned 400: An assistant
message with 'tool_calls' must be followed by tool messages responding to each
'tool_call_id'. (insufficient tool messages following tool_calls message)`.
The wording points at a missing reply, and that reading is wrong — all three
were there. What the provider objects to is a `user` turn *between* two of them.

An image cannot ride in a `tool` message: the OpenAI content-part format accepts
`image_url` only on a `user` message. So `project` answers the call normally and
follows it with a synthetic `user` turn carrying the picture, which is correct
and deliberate. The flaw was *when*: the turn was appended the moment the image's
own result arrived, with no awareness that the assistant may have proposed other
calls whose replies had not been emitted yet. With `fs_image` batched ahead of
two searches, the projection produced `assistant, tool, user, tool, tool`.

What makes this worth an entry is why it survived. Twice earlier **in the same
session** the same code path ran safely, because `fs_image` was the only call in
those turns — and when it is the only call, appending immediately *is* appending
after the last reply. Every vision test in `test_harness.py` used that shape.
The bug needs `fs_image` batched with another call *and* not positioned last, so
a passing suite said nothing about it. A test that can only construct the safe
arrangement is not evidence about the dangerous one.

It is also a replay bug, not only a live one. History is derived rather than
stored, so the malformed shape is rebuilt from the log on every subsequent
request — the session could not be resumed either, and the fix retroactively
repairs sessions already recorded.

The fix buffers the image turns and flushes them when the run of `tool` replies
ends — before any `user` or `assistant` turn, and once more at the end for a
turn that stopped on its results. Compaction clears the buffer instead, because
a replacement list is the whole history and anything still held belongs to the
history being replaced.


### N115

**The thinking was recorded in full, streamed to the client, and dropped from
the one place the provider needed it.**

A turn died with `gateway returned 400: The reasoning_content in the thinking
mode must be passed back to the API`. The step before it was a truncation:
16384 reasoning chunks, **zero content chunks**, `produced == requested`. The
model spent its entire allowance thinking and said nothing out loud.

That is the N90 continuation path working as designed — a reply that stops at
`max_tokens` is continued rather than ended. But continuing means resubmitting
the assistant turn, and the turn went back as
`{"role": "assistant", "content": ""}`. Every reasoning token was gone.

Worth being precise about *where* it was gone, because the first reading was
wrong. Reasoning is **not** ephemeral: the loop writes each chunk as a
`response_chunk` before dispatching on kind, so all 16384 were durably in the
event log and all 54KB reassembled cleanly from it afterwards. The governance
record never had a hole. What dropped it was `_accumulate`, which folded
`content`, `tool_calls` and `usage` and had no branch for `reasoning`; and
`_assistant`, which projected `content` and `tool_calls` and nothing else. A
**read** bug, twice, over data that was sitting right there — which is the
N38/N48/N86 family again: a value correct where it was written and never read
where it was needed.

The empty case is the same rule rather than a special one. `budget` wants
`reasoning_content` *present* on every assistant turn while thinking is on, so a
turn that thought nothing carries an empty string. Fixing only the truncation
would have left the identical 400 waiting on the next history containing an
ordinary tool call — the bug would have looked fixed and moved.

Keyed on the dialect, not on the provider. `pi` carries this as
`compat.requiresReasoningContentOnAssistantMessages: isDeepSeek`, set from the
same condition as its `thinkingFormat: "deepseek"` — two names for one fact.
Neuman already declares that fact as `ReasoningControl.control == "budget"`, so
`replay_fields` lives beside `reasoning_fields` and keys on it. The two are one
dialect seen from both ends: a provider that takes `thinking` on the way out
wants its thinking back on the way in. A check against a model id would have had
to be rewritten for the second provider to speak this shape.

Cross-checked against `pi` rather than reasoned from first principles, and the
first reading of its source was wrong in an instructive way: the `""` it assigns
at `openai-completions.ts:1391` looks like "send an empty field", but that is
the no-thinking fallback. The main path (line 1348) replays the real
accumulated thinking, keyed on the field name the reasoning arrived under. Both
behaviours are needed and they are not alternatives.


### N116

**Compaction built the malformed request that ended the turn, and the reply
clamp hid it for seventy steps first.**

Three sessions died inside one hour, each on a `400` the appliance had just
constructed. Two shapes, one bug:

    Messages with role 'tool' must be a response to a preceding message with
    'tool_calls'

    An assistant message with 'tool_calls' must be followed by tool messages
    responding to each 'tool_call_id'. (insufficient tool messages following
    tool_calls message)

The boundary was a fixed message count sliced with no regard for pairing:

```python
keep = self.config.keep_recent_messages   # 8
head, tail = messages[:-keep], messages[-keep:]
```

In `293dc089df06` history alternated assistant-with-`tool_calls` and its single
reply, so the last eight messages began *on* a `tool` — the assistant offering
`call_01_FWDTOeKcXulEPuS5SFxf2989` sat one index past the cut and went into the
summary. The other two turns called several tools at once, so the same slice
landed *inside* a fan-out group: the call survived, some of its replies did not.
Opposite symptoms, one cause.

The irony is that the module header already stated the invariant — prune
"replace[s] large *old* tool results with a stub, **preserving
tool-call/result pairing**" — and `_prune` honours it, because it only ever
rewrites `content`. `_compact` is the sole move that *removes* messages and the
sole move that never learned the rule. An invariant documented for the move that
cannot break it, and absent from the one that can.

**What delayed the diagnosis was the reply clamp.** The model was
`deepseek-flash`, declared at 262,144 tokens; peak history was **255,547 — 97.5%
of the window**. `_reply_budget` therefore computed `262144 − 255547 − 4096 =
2,501` tokens of room and sent the request anyway, so every reply came back a
fragment. Steps 85, 86, 87, 89, 90, 93, … 158 all recorded `produced == requested
== 512` and scrolled past as *"…reply hit the length limit; continuing"*. The
model never got room to finish a thought, so it never completed a tool call, so
history only grew. The length limit looked like the bug and was a symptom with
its sign flipped: it is what kept the provider from refusing outright, and it was
firing because nothing else relieved the pressure.

Three fixes, because there were three faults:

* **The cut point is chosen, not computed.** `_cut_point` walks back by *tokens*
  to a target, then moves the boundary **earlier** until it lands where a history
  may legally begin — never on a `tool`, never inside a fan-out. The boundary
  only ever moves earlier, so the tail grows slightly: a little less freed, never
  an invalid request. `pi`'s `findValidCutPoints` refuses a tool result outright
  for the same reason (`packages/coding-agent/src/core/compaction/compaction.ts`).
* **Compaction is triggered by a reserve** (`_compaction_budget`), so pressure is
  relieved by summarising rather than by starving the answer. The reserve is a
  proportion of the window, not a constant — see D40. `pi`'s `shouldCompact`
  reserves a flat 16,384, which is a quarter of this appliance's local qwen and
  1.6% of a 1M window.
* **A squeezed reply is reported.** Not only at the floor: by the time
  `available < MIN_REPLY_TOKENS` the turn is over, and the condition that
  mattered here lasted seventy steps well above it. With the reserve in place
  this should be unreachable, which is why it is logged — if it appears, the
  reserve is wrong.

Compaction's own input was slimmed in the same pass, since it was reading what it
was trying to free: tool results are truncated to 2,000 characters and reasoning
is dropped entirely before the summariser sees them, which is `pi`'s
`serializeConversation` and its note that replayed thinking "can make the
summarization request larger than the context it summarizes".

**The tests had every opportunity and took none.** Writing them first was not
enough — the first two drafts *passed against the broken code*, because a
fixture with a tidy `user`/`assistant`/`tool` cadence happens to put a legal
boundary at index −8. The single-pair case only reproduces with the truncated
final assistant the real session ended on (`content: ""`, no `tool_calls`), and
the fan-out case only with a width that does not divide the tail evenly — 4
reproduces it, 3 does not. Tenet 17 in its exact form: a check must be *seen*
failing, and "I wrote the test first" is not the same claim.


### N117

**The appliance-wide reply ceiling was documented as a fallback and implemented
as a cap, and it was subtracted from windows it had nothing to do with.**

`services.neuman.maxTokens` says "0 defers to `services.neuman.maxTokens`" on
the per-model option beside it — a fallback for a model that declares nothing.
Two things were wrong with that.

**It capped rather than deferred.** `_reply_budget` resolved
`min(configured, model)`, so a catalog preset declaring 131,072 on a model that
serves it was held to whatever the appliance-wide number happened to be. On this
appliance that was 16,384, so raising the hosted models to 131,072 had no effect
at all while appearing to have taken effect — the catalog said one thing, the
request carried another, and nothing reconciled them.

**It was reserved out of every model's window.** `_prompt_budget` subtracted
`self.config.max_tokens` outright while `_reply_budget` took the minimum, so the
two disagreed about the same quantity. Raising the global to let a 1M model emit
131,072 therefore took 131,072 out of the *local* 27B's 65,536-token window too
— because some other model in the catalog could emit that much — collapsing its
history budget from 38,503 to the `context_tokens // 4` floor of 16,384. The
appliance had a choice between a hosted model that could not finish a reply and
a local model that compacted constantly, with one number to make it with.

Both are N93 one layer up. A model's output limit is a fact about the model and
its hardware: a 65k local model and a 1M hosted one differ by a factor of
sixteen in the window a reply is carved from, so no single number is right in
either direction. `_reply_ceiling` now resolves it in one place — the model's own
figure where it states one, the configured default only as a fallback — and all
three sites that need it (the usage reservation, the reply clamp, the prompt
budget) go through it. Every model in the catalog states its own, so the global
is now what an unlisted model falls back to and nothing else.

Found by reading the config rather than by a failure: the question was "what is
this global for", and the answer was that it had been silently overriding the
per-model values the catalog had just been built to carry. The test that guarded
this chain end to end asserted on the *source text* of the old expression, so it
failed on the fix rather than on the bug — a check that pinned the
implementation instead of the behaviour, and the behaviour is now asserted in
`test_harness.py` where it can be.


### N118

**Removing a parameter updated two of four call sites, and the admin session
reads answered 500 with a green suite.**

`admin_actor` had taken a `conn` every caller passed and the body never read,
so it lost the argument and the `async` with it. `audit.py` and `system.py`
were updated to match. `sessions.py` and `policy.py` were not, and every route
in those two files raised on its first real call:

    TypeError: admin_actor() takes 1 positional argument but 2 were given

So `neuman-admin session list` — the command for reading what happened in a
turn — answered `unreadable_response: Internal Server Error`, while
`neuman-admin model list` and `status` worked perfectly. The appliance looked
healthy from every angle except the one that reads the record.

Nothing caught it. The suite had 654 passing tests, including a thorough
derived-from-the-router check that **every** admin route refuses without peer
credentials, and another that an identified non-operator gets 403 everywhere.
Both exercise the guard and stop. The only positive case —
`test_an_operator_gets_past_the_guard` — asked for `/admin/status`, which
lives in `system.py`: one of the two files that *had* been updated. A test
asserting authority was mistaken for a test asserting the route works.

`test_every_admin_read_answers_for_an_operator` now drives every GET as an
authorized operator and fails on any 5xx, derived from the router so a route
added tomorrow is covered today. Two of those reads append to the audit chain
and cannot complete against `_FakeConnection`, which implements what lifespan
asks and deliberately not `transaction()` — stubbing it would let the migration
runner "apply" DDL into a void and make a broken migration look fine. Those are
recorded as unreachable and counted, rather than skipped silently, so the test
cannot pass by reaching nothing.

The lesson is narrower than "test more". A refusal test and a works test look
alike and prove different things: the first can pass on a route that raises,
because raising is not answering. Where a guard is checked across the whole
surface, the positive case has to be too.


### N119

**Pruning ran on every step, was recorded by nothing, and starved the
compaction it was meant to precede.**

A long Qwen session reported a capacity bar alternating between two values. The
event log says why — `prompt_tokens`, step by step:

    13,120  51,734  12,937  54,275  14,662  56,788  16,633  62,502  19,848

Two interleaved series. The request bodies confirm it: one carries 46 tool
messages, the next carries 2. Then, at the end:

    gateway returned 400: request (68452 tokens) exceeds the available
    context size (65536 tokens)

In 70 requests that session logged 11 prunes, 8 spills, and ran compaction
**zero times** — then overflowed the window.

`_spill` and `_prune` edit the *projected* message list, and nothing records the
edit. History is derived from the log on every step, so the full tool
results come back next step and are stubbed again: their benefit is
per-request, their cost is per-step. That much is fine and deliberate. The
defect was letting prune's savings answer the question *"does this fit?"* — it
kept answering yes, so the compaction branch below it was never reached, and
the only move that persists never happened.

The fit check after pruning now asks about `baseline`, the size of the history
the *next* step will project, rather than the pruned copy being sent this once.
Only compaction changes that number, because only compaction is recorded as an
event the projection replays — which is the same mechanism `pi` uses, where a
compaction entry stores `firstKeptEntryId` and `buildContextEntries` starts
from the summary.

Verified by stepping a fixture over its own log. Before: every step projects
11,211 tokens, prunes to 3,831, `['prune']`, forever. After: step 0 compacts,
and the next projection *starts from the summary* — 11,211 → 1,616 → 2,416 →
3,215 → 4,015, then compacts again. No alternation, because what is projected
and what is sent are the same number on any step where nothing fires.

Two things this exposed beyond the fix.

**The alternation was legible only in the log.** The capacity bar reports the
last request's `prompt_tokens`, which is honest per request and misleading as a
gauge when two different histories are being sent alternately. Compaction is
now the only thing that moves the bar downward, and it emits a `compacted`
frame so the drop has a line in the transcript instead of looking like a defect
— the same role `truncated` plays for N90.

**The earlier fix was called verified on unit tests alone.** N116 corrected
where compaction cuts and added the reserve that triggers it, and the tests for
both pass against this bug: they call `manage` once and assert on what comes
back, where the defect only appears across steps sharing one log. A single
invocation cannot show that a transformation fails to persist. The new test
projects, manages, records, and re-projects — which is the shape the real loop
has and the shape the old tests lacked.


### N120

**Pruning was deleted, because it was a third copy of one idea and the only
copy that did not last.**

N119 fixed two symptoms of `prune` without asking whether it should exist. It
should not.

The move rewrote large tool results **already in history** into a stub, on every
step, and nothing recorded the edit. History is derived from the log each step
, so the full results returned and were stubbed again: the work was redone
forever and the benefit kept never. Both N119 bugs were consequences. Its
savings answered "does this fit?" with a number that did not persist, so
compaction never ran across 70 requests; and because it fired on alternate steps
the provider's `prompt_tokens` alternated with it — 48,884 / 11,799 / 50,429 /
11,701, with 21 and 23 stubs in the small ones — which corrupted the anchor the
compaction trigger reads and made the capacity bar visibly bounce.

`pi` has no equivalent, and that is the lesson. A large result is truncated
**once, where it is produced**: the full text is written beside it and a marker
takes its place, and from then on it is ordinary history at a fixed size. Spill
is that move, and Neuman already caps tool output at the tool layer as well
(`max_output_bytes`), so prune was the third layer of the same idea.

What is left is two moves with a clean division. **Spill** bounds one oversized
result. **Compaction** bounds accumulated history, and is the only move whose
effect survives a step, because it is the only one written to the log as an
event the projection replays. Stepped over its own log the result is a sawtooth
with no alternation: 11,183 projected → compacts → 1,612 → 2,410 → 3,207 →
4,005 → compacts → 1,611, and on every step that does not compact, what is
projected and what is sent are the same list.

Spill keeps the per-step rework, and that is tolerable where prune's was not: it
is idempotent, and the anchor rule added in N119 — take the provider's count
only when the outgoing request was *not* edited — keeps it from corrupting the
measurement.

The capacity bar measures against the compaction point now, rather than the
model's window. It had reported only `context_tokens`, so on a 65,536-token
model it sat a little under half full at the moment history was summarized —
because the window reserves the reply ceiling, a margin and the compaction
reserve before the conversation gets any: 65,536 − 16,384 − 6,553 − 4,096 −
8,192 = 30,311.

A mark on the track was the first attempt and was the wrong answer to the right
observation. Keeping the window as the denominator meant the gauge still
measured a quantity nobody is approaching, with a tick explaining why it never
filled; the honest fix is to measure the thing that governs the behaviour and
keep the window in the tooltip, where "the fact models are compared on" belongs.
That also removes a measurement whose own history is instructive: believing the
conversation may use the whole window is exactly what N116 and N117 were both
about.


### N121 — A grid item will not shrink below its content, and a border on a
zero-width one will not be clipped

**What:** folding the inspector away left a horizontal scrollbar under the whole
client. The pane looked gone and the timeline did not look crowded — the
scrollbar was 1px of document, from a control that was supposed to leave nothing
behind.

**Cause: two defaults, both of the same kind as N72's.**

`#inspector` is a grid item, and a grid item's `min-width` defaults to `auto` —
"never shrink below your content." Setting the column to `0` did not fold the
pane; the pane held the column open at the width of its content. `min-width: 0`
is what lets a grid item be narrower than what it holds, exactly as `min-height:
0` is the precondition for a flex item scrolling (N72). It is an absent
declaration rather than a wrong one, so nothing in the stylesheet read as broken.

Even with `min-width: 0`, the pane still had its `border-left`. An element's own
border is not clipped by its own `overflow: hidden` — that clips descendants — so
a 1px border on a 0-width column is 1px of document overflow. That was enough to
raise a scrollbar, which took 15px of width and re-flowed the very layout being
folded. The folded rule drops the border for the duration.

**Why the grip is where it is.** The handle is `position: fixed` and computes
`right` from the pane's width, because a grip placed *inside* the pane would fold
away with it and leave nothing to click. The width now lives in one
`--inspector-width` variable that both the grid column and the grip read, so the
handle cannot come apart from the divider it straddles — a second copy of the
clamp is precisely how that happens.

**The test:** `test_the_inspector_can_be_folded_away` asserts the grip is wired,
that the folded class is styled, that the triangle rotates, and — the part that
is easy to lose — that `min-width: 0` is a *declaration* in the inspector's
layout rule. It anchors on `grid-area: inspector` rather than the first
`#inspector` block, because the selector appears elsewhere as `display: none`
and a test that matched one of those would pass while the pane stopped folding
(cf. N72's vacuous `min-height` search).


### N125 — Spill: a file the reader cannot open is worth nothing

**The design that failed.** When a tool result was too large for the context
window, the server wrote the full text into its own state directory — mode
`0600`, owned by `neuman` — and put a head/tail preview plus a locator into the
model's history. It existed because the server was not the tool: the output
arrived at the server as a tool result, the server had nowhere the acting user
could read to put it, and so it put it where the server could write and then
described the file to a reader who could not open it.

**Why it was wrong, in one sentence.** It hid the agent's own tool output from
the agent and then served the identical bytes back through a privileged path, so
the file's protection bought nothing the reader did not already hand over, while
costing an unreachable path, an `ENOENT` that read as a broken appliance, and
eventually a `session_query action="result"` reader that required the model to
carry its own session id. Every layer was a workaround for the first mistake:
choosing to store the bytes on the far side of a boundary the model was on the
other side of.

**What replaced it.** Large output is persisted *where it is produced*. The
client — the stage that runs as the acting user — writes a stream that outgrows
a small preview into its own `spill/` directory (D42) and hands the model an
ordinary path. The model opens it with `fs_read`, searches it with `fs_search`
and parses it with `python`: the same tools it uses on any file.

**Why this is not a loosened boundary.** The content is the dispatch's own
output, written under the account's own state directory on the account's own
machine, and the core keeps no copy of it. What the failure taught is the rule:
the bytes belong where their reader can reach them, and a privileged path that
re-serves them is a workaround for having put them in the wrong place.

**What went with it.** `ContextManager._spill`, `_write_spill`, `spill_dir`, the
`spill_threshold_bytes`/`preview_bytes` settings and the server-side scratch
directory the dispatches used to bind.

### N126 — An archive that decoded as text was called corrupt
**A tool that worked was blamed for a content type that was not on the list.**

Falling back from `git` to `web_fetch` on `codeload.github.com/.../tar.gz`, the
model got mojibake and reported the response as corrupt. Codeload serves
`application/x-gzip`, and the binary list in `web_fetch.py` had
`application/gzip` and `application/x-tar` but not `application/x-gzip` — so the
archive decoded as text, and the tool that was working took the blame.

`x-gzip`, `x-bzip2`, `x-xz`, `x-7z-compressed` and `vnd.rar` are now refused
with their content type, which is what turns "corrupt response" into "an archive
this tool cannot read".

**The rule:** a content type a tool cannot handle is refused *by name*. A list
of binary types is never finished, and the alternative to refusing is guessing.

### N127 — The paging tools persisted their own pages

**The failure.** With output persistence on by default (D42), a session asked
`fs_read limit 180` and `fs_search limit 100` and got a 4 KB preview plus an
`[output too large to show; the full output is in /scratch/…]` note — not the
page it asked for. Every large `fs_read` and `fs_search` result was persisted;
the model opened none of the scratch files, re-reading with different offsets
instead, so each page quietly lost its tail past the first 4 KB. This is the
read-back failure the preview design is exposed to, and it is worst exactly
here: the model bounded these results itself, by `limit`.

**The cause.** `persist_output` defaults true, so the reader and the searcher
inherited the `shell`/`python` behavior even though their output size is a
function of the caller's arguments, not of the tool's reach. The runner's 4 KB
preview silently overrode the page the model chose.

**The fix.** `fs_read` and `fs_search` set `persist_output = false` and deliver
inline, each bounding itself: `fs_read` at a 32 KB page with its
`[showing lines X-Y of N; use offset=Z]` notice, `fs_search` at a 32 KB byte
budget with a `(stopped at N matches to stay under the output budget)` notice.
Only genuinely unbounded tools — `shell`, `python`, `web_fetch`, `browse`,
`git` — persist. In the same session the `browse` results persisted and the
model *did* read them back, which is the split working as intended: a preview is
a reasonable summary of an unbounded page and the full file is optional; it is
not a substitute for a page the caller asked for.


### N128 — The focused detail was registered before its row existed, so a narrow window showed none

**The failure.** On a window narrower than the breakpoint the inspector is hidden
and the focused detail is mounted *inline, after its row*. The row appeared and
lit up as focused, but the detail never did; and clicking the row toggled it
"closed": the first click removed a detail that had never been inserted and
cleared the focus, so it took two clicks to see anything. Streamed reasoning read
as though it had been dropped, because its body never appeared.

**The cause.** `toolCard` and `reasoningBlock` called `registerFocus` *before*
`appendWork(row)`. `showFocus` mounts the narrow-window detail with
`entry.row.after(entry.detail)`, and a node with no parent has no `after` to
insert into — the call was a no-op. It still set `focused = entry`, which is why
the row read as focused and the next click was taken as a close. The
width-dependent half of `showFocus` is what hid it: on a wide window the detail
goes into the inspector with `replaceChildren`, which never needed the row in the
tree.

**The fix.** Both append the row first and register inside `grow`, in that order,
with a comment saying why the order is load-bearing — `grow` so that mounting an
inline detail follows the transcript's tail like any other height change.


### N129 — A reopened session showed only the tail of a compacted history

**The failure.** Opening a past session that had been compacted showed a fraction
of it: the user's own prompts and everything before the compaction were gone, and
nothing on screen said anything had been dropped. It read as data loss.

**The cause.** `project(..., for_display=True)` applied the `compaction`
`harness_event`'s replacement list exactly as the model path does, so the history
became `[system summary, *tail]`. The early prompts were in the folded head, and
the summary is a `system` turn the client never draws — so the visible transcript
was only the tail. Compaction appends a replacement; it never deletes, and the
originals were in the log the whole time.

**The fix.** A display read ignores the replacement and keeps the originals,
appending a `role: "note"` message at the point the model's view was cut — the
same line the live `compacted` frame draws — so the reader sees the whole
conversation and why the model may have forgotten it. The model path is unchanged
(`test_compaction_replaces_history_with_its_recorded_list` still passes). See
D48.


### N132 — A wide row scrolled the page sideways and clipped the fixed controls

**The failure.** The client showed a page-wide horizontal scrollbar (a grey bar
along the bottom) and the two `position: fixed` controls on the right — the
account circle in the corner and the inspector grip — appeared half cut off.
Both symptoms had one cause.

**The cause.** Something in flow was wider than the window, so the document
scrolled horizontally. That scrollbar changed the arithmetic for everything
positioned from the right: `height: 100vh` is the *large* viewport and includes
the horizontal scrollbar's height, so the body became a hair taller than the
visible area and a vertical scrollbar appeared. Now two scrollbars reduced the
layout viewport, and the fixed controls, whose `right: 16px` / `right: 4px` are
measured from that viewport, slid under the vertical scrollbar. The horizontal
scrollbar is the grey bar; the clipping is its consequence.

**The fix.** The document does not scroll sideways at all. `html` is
`overflow-x: clip` — not `hidden`, so no scroll container is created and nothing
can scroll the page sideways, not even `scrollIntoView` — and its height is
`100%`, with `body` following, so the body tracks the area the scrollbars
actually leave rather than the large viewport. The transcript additionally sets
`overflow-x: hidden`, because a wide row there is the source that started it; the
rows that can be wide (code, diffs, file views, listings) already scroll inside
their own card.

The broader shape is the same as N121: a size was read from one place (`100vh`)
while a different place (`100vw` with a scrollbar) decided the visible area, and
nothing reconciled them.


### N134 — A panel capped at 60vh ran off the top of a window it had half of

**The failure.** On the new-session screen, opening the tool chooser produced a
panel that extended past the top of the window. The overflow was not merely
ugly: the panel is the scrolling element, so its scrollbar began above the
viewport and the first tools in the list could not be scrolled to or clicked.
In an existing conversation the same panel was fine.

**The cause.** The upward popovers are anchored to `#context`'s top edge
(`bottom: 100%`) and each carried a viewport fraction as its cap — 38vh for the
path choosers, 60vh for the tool chooser, 72vh for the models card. A fraction
of the window is only a bound on the space above the composer if the composer is
*at the bottom* of the window, which is the assumption nobody wrote down. In the
draft it is not: `body.drafting` centres the card, so the room above it is about
half the window and a panel entitled to 60vh of it overflowed by the difference.

The two states differed by where one box sits, so every check performed in a
conversation — which is every check — saw the caps working.

**The fix.** The bound is measured instead of assumed. A `ResizeObserver`
publishes `--headroom`, the distance from the top of the viewport to the top of
`#context`, and every upward panel takes `min(…, var(--headroom-cap))` where the
cap is that distance less the popover's own gap and the account circle it would
otherwise open behind. The `vh` numbers stay as the taste limit — how much of
the transcript a panel may cover when there *is* room — which is a different
question from whether the panel fits, and conflating the two is what broke.

Two details are load-bearing. The observer watches `body` as well as the card:
in the draft the card is centred, so a shorter window moves its top edge while
leaving its own box identical, and watching the card alone misses exactly the
state that produced this. And the cap is floored at 120px, because `max-height`
clamps a negative value to zero — on a window too short to satisfy the
clearance, an unfloored cap would collapse every panel to nothing and make the
chips look dead.

The shape is a constant standing in for a measurement: a cap chosen against the
layout that existed when it was written, with nothing tying it to the layout
added later. That is N132 again — a value set in one place and the
geometry it depends on decided in another — differing only in that here the two
places are a stylesheet rule and a state class rather than two stylesheet rules.


### N136 — The session list was a different width in each browser, and one kept shrinking

**The failure.** The session list rendered narrower in Chromium than in Firefox,
by enough to truncate titles that fitted in the other browser, and it stayed that
way across reloads while Firefox looked right.

**The cause.** Two faults in one commit, in the same control.

The width had been `16rem` — a constant, identical in every browser at every
window size. The change replaced it with `clamp(220px, 22vw, 380px)`: a reading
measure decided by the *viewport*. Two browsers whose windows were not the same
width therefore showed two different rails, which had not been possible before,
and on any window under 1164px the new default was *narrower* than the constant
it replaced, with the 220px floor biting below 1000px. Measured at a 1204px
window: 264.875px, exactly `22vw`. No per-browser rendering difference was
involved at any point — the two windows simply disagreed, and nothing in the
client or the stylesheet was in a position to notice.

The same commit made the grip one control doing two jobs — drag to resize, click
to fold — and measured the new width from `event.clientX`, the pointer's position
from the window's left edge. The press lands *on the grip*, which straddles the
rail's own edge, so `clientX` at pointerdown is already a rail width. A click
that jittered five pixels therefore did two wrong things: it crossed the 3px drag
threshold and so did not fold, and it stored `clientX` — a rail some 12px
narrower than the one it started from — in `localStorage`. Repeated, that
ratchets the rail down to the 180px floor, which is below the stylesheet's own
220px minimum, because a stored number replaces the token and the only bound left
is the client's 180–560 clamp.

**The fix.** The width is a constant again, with the window as a ceiling:
`min(16rem, calc(100vw - var(--rail-strip)))`. There is room for a title where
there is room, and the rail takes the window minus a strip where there is not.
The strip is *derived* rather than chosen — the grip needs 14px of overhang to be
grabbable and the account circle is 36px at `right: 16px`, so 52px is the width
at which neither is clipped — and it is written once as `--rail-strip`, because
the drag handler names it too.

A stored width rides through the same `min()`, which is the case the old clamp
only looked as if it prevented: clamping the *number* still leaves a stored 400
in a 300px window as a rail with no visible edge to drag back. The drag is
measured as `startWidth + (clientX - startX)`, and a release that moved the edge
by less than the drag threshold stores nothing at all.

Two smaller consequences of the same edit, worth stating because both look like
regressions from outside. The client can no longer `parseInt` the variable to
persist the width — it holds a `min()` now, and that parses to `NaN` — so the
saved value is read from the width on screen. And a drag performed on a narrow
window stores the width that was visible, so the preference it leaves behind is
for a narrow rail rather than for the wider one it would have been on a large
screen; the alternative is a drag whose result is discarded, which is worse.

The stored width is read under `neuman.sessions.width.v2`, not the key the old
handler wrote. A per-browser preference outlives the code that produced it: the
first cut of this fix kept honouring the stored number, so the browser that had
been ratcheted down to 180px stayed there and looked from outside like the fix
had never shipped. Renaming the key is how a persisted value that was never a
width is abandoned; there is nothing to migrate it from.

The shape is a width read from one place — the viewport — while the thing it
stands in for, a reading measure, was decided in another; plus a *measurement*
taken from the wrong origin, since `clientX` is a position and not a size. Both
are N121/N132/N134 again, differing only in that the two places here are a
stylesheet token and a pointer event rather than two stylesheet rules.


### N138 — The tool chooser was cut off again, on the screen N134 was about

**The failure.** On the new-session screen, opening the tool chooser produced a
panel whose top was above the viewport — the same first-rows-unreachable bug as
N134, on the same panel, on the same screen.

**The cause.** N134's fix measured the room above the composer with a
`ResizeObserver` and capped every upward panel against it. A `ResizeObserver`
reports *size*; entering the draft changes *position* and nothing else. Measured
at a 1100×800 window, the composer's box is 81px tall both at the floor of a
session (`top: 705`) and in the middle of a draft (`top: 359`) — no observed box
changes size, so the observer never fired and `--headroom` kept the session's
704px. The tool chooser then took its 60vh/520px cap against a 704px gap that no
longer existed and opened 480px into a 350px one, landing at `top: -128`.

It went unnoticed because the observer *does* fire for the cases that share this
shape — a window resize, the field growing as you type — and because every
manual check of N134's fix happened after some other resize had corrected the
value. Only the exact sequence *session → New session → open the chooser*, with
nothing resizing in between, shows it.

**The fix.** The measurement is a named function (`measureHeadroom`), and the two
places that toggle `drafting` (`paintHero`, `leaveDraft`) call it directly. A
`ResizeObserver` cannot be made to see a move, so the mode change has to ask.

**The shape.** A fix that observes one property of the thing that moved (its
size) standing in for the property the rule actually depends on (its position).
It is N134 one layer down: N134 replaced a constant with a measurement, and this
replaced a measurement of the wrong thing with one of the right thing — and the
second only surfaced on the one screen the first was written for.


### N139 — The resumption of a truncated thought was in the wrong role

**The failure.** A reply cut off at the token cap mid-thought was continued by
folding the unfinished reasoning into the assistant message's `content`, on the
dialects that have no inbound thinking field (`effort`, `toggle`). The model read
its own half-thought as speech it had already made: at seq 33091 it re-emitted
the bracketed label and all 16,384 tokens of reasoning as visible content, 16.5s
after the request, then carried on. The continuation spent its whole cap
transcribing instead of advancing, and the echo entered history permanently.

**The cause.** Position outranks prose. The label said the reasoning was
unfinished thinking, but the message's role said it was the assistant's own
words. No instruction inside the speech can outrank the fact that it is speech.
`budget` never showed this because `reasoning_content` carries the thought back
on the assistant turn; `effort` and `toggle` define no such field, so the thought
had to be surfaced somewhere, and `content` was the only assistant-side place
available.

**The fix.** The resumption is a following `user` message, which is the position
that means context rather than speech. The assistant turn keeps its real content,
usually empty, and the thought rides the user turn the same way a steer does.
`projection.py` synthesizes it for the model path only — a display read still
shows the truncated thinking under `reasoning` and still joins the two assistant
halves — and never between an assistant's `tool_calls` and its `tool` replies,
where a user turn would be malformed; a truncated tool call's resumption waits
until the run of replies ends. The thought stays whole; slicing it is a separate
question from where it belongs.

**Worth noting.** The user turn is the fallback, not the ceiling. Current
llama.cpp accepts `continue_final_message: "reasoning_content"` and can continue
the assistant turn in place, preserving the thinking block; that path skips the
user turn entirely. The user turn is what works for every provider, including
the hosted ones that reject a trailing assistant message (Anthropic 4.6+).


### N140 — A tool call cut off at the cap was replayed as an unparseable fragment

**The failure.** A reply could reach the token cap while emitting a tool call,
leaving `arguments` an unterminated JSON string. The harness declined to execute
it, but the fragment went back into history on the next request. llama.cpp parses
`tool_calls` on the way in and answered `[json.exception.parse_error.101] ...
missing closing quote` with a 500, killing the turn 170ms later with a prompt no
provider could read.

**The cause.** Every truncation branch lived in the `not tool_calls` case, so a
truncation that ended inside a call was neither announced nor sanitized: the
loop fell straight through to execution and then replayed whatever fragment the
model had emitted.

**The fix.** `_sendable_call` replaces unparseable arguments with valid JSON that
says the call did not run and asks for a shorter re-issue, keeping the id and
name so the `tool` reply the harness already wrote stays paired. The
truncated-tool-call branch announces the cap and continues the turn, because the
model still has a call to finish saying. Arguments that do parse are left alone;
a truncation lands inside at most the last call.

**Worth noting.** This is N90/N91 one layer down: a value (`finish_reason`) was
recorded and consulted in one branch, and the other branch's shape was
invisible. And the recovery names the cause, not the symptom — the model told
only "missing closing quote" re-sends the same oversized call.


### N141 — Auto-scroll fought the reader, and slow scrolling lost

**The failure.** While a turn streamed, scrolling up in the transcript did not
stay put. A slow wheel moved the view up a little and the next streamed frame
snapped it back to the bottom; only a fast flick escaped far enough to stick.
The reader was pinned to the live view until they outran the token rate.

**The cause.** Following the tail was decided by a *measurement* taken fresh on
every mutation: `scrollHeight - scrollTop - clientHeight < 120`. Distance cannot
distinguish "the reader scrolled up" from "content just grew". A wheel tick that
moved less than the 120px slack was still "at the bottom" on the next frame, so
`grow` re-scrolled, and each suppressed tick was undone. Nothing listened to the
scroll or wheel at all — the reader's intent was never recorded, only inferred
from where they happened to be between two frames.

**The fix.** `followTail` is an explicit state. A `wheel` listener on `#log`
clears it *synchronously* on an upward delta, before any queued frame can consult
it, and a `scroll` listener restores it only on arrival at the bottom (2px
slack). `grow` follows iff the flag is set. The same flag, stored per-box, drives
`growInner` for the chain-of-thought scrollport. The arrow above the composer
appears whenever the flag is clear and the log overflows, and pressing it sets
the flag and jumps to the tail.

**Worth noting.** The bug is the statelessness, not the threshold. Any distance
test compares two different quantities — where the reader is and where the
content ends — and streaming moves the second one. Intent has to be an event,
not a snapshot.


### N143 — The summariser hardcoded a reasoning dialect, and compaction echoed the head

**The failure.** Live, on the local Qwen3 preset: the first compaction after a
rebuild replaced the head with a **verbatim slice of the serialized
transcript** — `Conversation summary:\n[Assistant tool calls]: fs_search(...)…`,
ending mid-JSON — rather than a summary. The anchor was useless, and because
each later compaction merges into the anchor, a second one would have carried
the echo forward. It happened to self-correct on the next cycle, but the
mechanism was not doing what it claimed.

**The cause.** The summariser request hardcoded
`chat_template_kwargs: {"enable_thinking": false}`, which is the `toggle`
dialect, with a comment asserting "this is how Qwen3 exposes it". The local
Qwen3.8 catalog entry declares `effort` (`reasoning_effort`), not `toggle`.
Reproduced against the provider's endpoint with the same request and a
realistic chunk:

| fields sent | result |
|---|---|
| `chat_template_kwargs:{enable_thinking:false}` | continues the transcript (317 bytes) |
| `reasoning_effort:"none"` | continues the transcript (317 bytes) |
| `reasoning_effort:"low"` | correct structured summary, 1,459 bytes of reasoning |
| no reasoning field | degraded summary, 9,685 bytes of reasoning |

So turning thinking *off* was not the cheap setting here — it was the setting
that produced an echo. The cheapest *live* level was both cheaper and correct.
The hardcoded field also meant the dialect knowledge lived in two places: the
catalog declared one shape and the summariser sent another.

**The fix.** `reasoning.summariser_fields(control)` derives the request fields
from the model's declared dialect and picks the lowest declared level that is
not an off value (`none`/`off`), preferring `low`. `context.py` threads the
turn's `ReasoningControl` down to `_summarise_once`, and the hardcoded
`chat_template_kwargs` is gone. A `toggle` model — which has no live level to
offer — is still asked with `enable_thinking: false`, and a model declaring no
reasoning is asked for nothing.

**Worth noting.** This is the same shape as N94 (compaction had never run
because the request named no model) and N140 (a branch's shape was invisible):
one field, hardcoded from the model that was in front of the author at the
time, sent to whichever model the catalog happened to route to. The catalog is
where a model's dialect is declared, and anything that sends a dialect field
has to read it from there or it will be wrong for the next model added.


### N144 — The catalog called a reasoning level `high`; the model's template called it `xhigh`

**The failure.** On the local Qwen3.8 preset, running the benchmark at
"high" reasoning produced a 100-minute verification spiral. The level is not a
token budget and does not buy more thinking: the model's own Jinja template
(`/props.chat_template`) turns `reasoning_effort` into **one sentence prepended
to the system prompt**. `high` is aliased to `xhigh`, whose sentence is
*"consider plausible alternatives"* — on a task whose whole difficulty is
committing to one matrix direction, an instruction to keep reconsidering. The
catalog offered `none`, `low`, `medium`, `high`; the template accepts `xhigh`,
`medium`, `low` (with `high` mapped to `xhigh`) and, when the field is absent,
defaults to `xhigh`. So the composer showed "high" while the model read "xhigh",
and the level the model actually names — `xhigh` — could not be selected.

**The cause.** The `levels` list is a model fact, and it had been written from
the model's *name* rather than its template. Two of the four names did not match
the vocabulary: `high` is a second spelling of `xhigh`, and `none` is not a
template level at all — llama.cpp intercepts it and disables thinking before the
template runs (verified: `reasoning_effort: "bogus"` returns a template `500`,
`"none"` returns `200`). Nothing failed loudly, which is the point: a level the
model silently remaps is a menu label that lies about what the model was told.

**The fix.** The levels are now the template's own: `none`, `low`, `medium`,
`xhigh`, read from `/props` rather than assumed. `high` is gone — two names for
one behaviour is a worse menu than one. The default is `medium`, which the
template leaves uninstructed: neither "consider plausible alternatives" nor
"move directly to the conclusion". The context manager still asks for `low` on
its summarisation call, where "keep thinking brief and move directly to the
conclusion" is exactly right (`reasoning.summariser_fields`).

**Worth noting.** "Reasoning effort" is a misleading name for this mechanism on
this model family; it is a prompt nudge, not a compute dial, and the higher
setting can be actively harmful on a task that needs closure rather than
exploration. The catalog is the one place a model's dialect is declared, and a
level list must be read from the model's template — the same lesson as N143,
one layer up: a field named from the model in front of the author is wrong for
the next model added.

### N145 — `$HOME` expanded against the server's idea of the home, and the token was refused outright

**Two failures in one rule, found by asking what a policy means when the client
is on another machine.**

An entry naming a home directory expands to the configured data subdirectories,
so the home is never named wholesale and dotfiles are excluded by construction.
Both halves of how that was implemented were wrong:

- **It expanded against *this host's* home.** `expand_paths` was called with the
  account's `pw_dir` from the server's own host. On one machine that is the right
  directory; on a client somewhere else it is a different one. So a grant naming
  the client's home stayed a literal path, the expansion never ran, and the whole
  home — dotfiles included — was offered by the working-directory chooser,
  because the same wrong home is what the check behind it used.
- **The documented spelling did not work at all.** The module's `homeDataDirs`
  description and the tool tour (now in `docs/tools.md`) both write `$HOME`, and every entry was
  required to match `^/` — so the one spelling that names no machine was refused
  at load, and the one that worked named a machine.

**Fix:** the client reports its home on `hello`, because it is the only party that
knows it; the core expands against that; and `$HOME` is a legal entry expanding
the same way. Both spellings mean the same thing while they agree, and the token
is the one to prefer because it keeps meaning it when they do not.

**The rule:** when a policy value means "somewhere on the machine that acts", the
core cannot resolve it — that may not be this machine. Ask the side that knows,
and let policy name the *role* rather than one machine's path.

### N146 — The network grant was a host list nothing enforced

**A policy field that reads as a control and is not one.**

`policy.network` was `{mode = "proxy", hosts = [...]}`, written when a
per-dispatch egress proxy checked every request against a HashSet of hosts.
Execution moved to the client, the proxy went, and the field stayed: the gate
read it for truthiness — an account with no host named got no `needs =
["network"]` tool — and the prompt printed the hosts as a limit. On the wire
nothing looked at them, because there was nothing to look at them with.

**Why it is worse than an unused field.** It is read as a guarantee by the person
writing the config (this file's own `network` block argued at length about what
the proxy would refuse), and it is read as a limit by the model, which then stops
at those hosts when the machine could reach any. A stale field that only wastes
space is one thing; one that both sides believe is a control is another.

**Fix:** `network` is `"none" | "any"` — whether the tools that declare
`needs = ["network"]` are offered at all, and nothing else. The prompt says what
is true: those tools run on the user's own machine, over their own network.

**The rule:** when the component that enforced a field is deleted, delete the
field. "The gate still reads it" is not enforcement; it is a decision about
whether to offer a tool, which is a different claim and needs its own name.

### N147 — A hand-rolled WebSocket handshake with a key no server would accept

**Every session socket failed as an opaque 502 while `/api` worked.**

The client's UI server proxies `/ws` so the browser and the executor share an
origin. It does not parse frames — it relays bytes — but it does have to *make*
one handshake upstream, and it derived `sec-websocket-key` from a hash of the
target path: 44 characters of base64 that decode to 32 bytes. RFC 6455 says the
key is sixteen, and a server is entitled to check. The core's does, with a 400
named `invalid Sec-WebSocket-Key header`, which the proxy could only report as
"the core refused the upgrade" — a 502 the browser shows as a failed handshake,
with nothing to connect to the real cause.

**Why no test caught it.** There was no test of the `/ws` proxy at all: the UI
tests covered static files, the API proxy and the token check, and stopped at
the edge of the one path with a hand-written request in it. Written against a
stub upstream that echoes anything, it would have passed anyway.

**Fix:** sixteen random bytes, and a test whose upstream is the real `websockets`
library — which refuses the malformed header exactly as the core does. The
second half is the part worth keeping: a proxy is tested against a strict
implementation, or it is tested against a permissive one nobody runs.

**The rule:** a hand-rolled request is only as correct as the strictest server it
meets. Do not stand in a stub for the protocol implementation — stand in the
implementation, and let it reject you.


### N148 — A base64 image was counted as text, so a 7k prompt read as 324k

**A session died at step 17 reporting `compaction_failed`, on a conversation
that was ~7,000 tokens.**

The agent called `fs_image`. The result projects as a multimodal `user` turn
carrying a `data:image/png;base64,…` URL — 1,291,180 characters. `estimate_tokens`
counted every message as `len(json.dumps(...)) // 4`, so that one message
estimated to **322,795 tokens** on a prompt the provider counted at **6,865**.
`measured_tokens` exists to correct exactly this, but it could not: the image sat
in the unmeasured tail, and after the first compaction `measured_upto` (34) was
larger than the new history (4), so the guard fell back to estimating the whole
history — the phantom count, whole.

Everything downstream followed from that one number. The harness believed 323,981
tokens against a 65,536-token window, so `_reply_budget` clamped the ask to the
512-token floor (`"the window squeezed the reply", "history_tokens":323981`). The
model emitted exactly 512 and stopped at `length`; because `produced == requested`,
the loop read it as "used its whole allowance, continue" rather than a window
clamp, so it kept going. The next step compacted again, and this time the head was
the image itself: `_serialise` stringified user content raw, so the summariser
request carried the base64 and `llama.cpp` refused it with `request (954348 tokens)
exceeds the available context size (65536 tokens)`. The 400 was swallowed,
`compact_failed` was recorded, and the turn ended `model_error`. Nothing was wrong
with the conversation.

**Why the estimator was wrong in the other direction from usual.** The note on
`CHARS_PER_TOKEN` says the error is content-dependent and runs both ways — prose
over-counted, identifiers under-counted. Base64 is the extreme of the under-count
case: it tokenizes near one token per character, not one per four, so `len() // 4`
invented a quarter-million tokens that were never sent. The anchor that hides the
error on ordinary text cannot hide a field whose length is a transport encoding.

**Why it took a vision model to find.** `fs_image` refuses to send an image to a
text-only model, so the estimator never met a data URI until the local Qwen3
preset — declared `vision = yes` — looked at one.

**Fix.** `estimate_tokens` redacts every `data:image/` string before measuring and
adds `ESTIMATED_IMAGE_CHARS` (4,800, as `pi` does) per image; `_serialise` renders
a content-part image as `[image omitted]` instead of its base64. Both are the same
principle: an image is a fact to record, not pixels to re-read. And a length stop
at the window's floor is now detected as a window clamp (`_reply_budget_and_window`
returns the flag it already logged), so a squeezed window compacts instead of
looping.

**The other half: stop resending the pixels.** Bounding the estimate keeps the
window honest, but it does not stop the base64 being resent on every turn until
it ages out — the thing the estimate was measuring. `projection._age_images`
keeps the newest `keep_images` (derived from the window in `loop._keep_images`,
D40) and replaces older ones with a line naming the source file, recorded on the
tool result. This is observation masking applied to the largest observation, and
it is why the estimate only has to be good for a couple of images rather than
every image in the session. The move is positional and deterministic.

**And stop storing the pixels at all.** The bytes live once, in a
content-addressed table (`session_images`, migration 0012), keyed by the
SHA-256 of the picture. The `tool_result` event records the hash and a path;
the derived history carries an *image reference* (`{"type": "image_ref",
"sha256", "path"}`); and `projection.resolve_images` puts the `data:` URI back
only on the wire copy, after the context manager has measured and split the
small form and after any compaction has recorded it. Even the `request` event —
the audit record of what was sent — now logs the reference, so a turn that
repeats an image stores it zero times rather than once.

Before this, the base64 sat in the `tool_result` payload, and because a
compaction event records the messages it kept, the same screenshot was copied
into every snapshot that retained it and re-logged in every request it stayed
hot in — the Codex session-growth bug (#34268, >100 GiB of duplicated image
data) in miniature. The seam is the same one a spilled tool result makes: the
log keeps the pointer, the payload lives once.

**And collect what nothing points at.** Content addressing means a picture can
be shared by two sessions, so purging one cannot delete "its" images by a
cascade — it would take bytes a live session still uses. `session_image_refs`
(migration 0013) records one edge per (session, image), cascading with the
session; `ImageStore.put` writes the bytes and the edge in one transaction, and
`ImageStore.sweep` in the retention pass deletes every image no edge names.
That is the Nix-store shape — a shared, content-addressed store collected by
reachability from roots (the surviving sessions) rather than by ownership — and
it is what makes retention true for images the same way it is for events.
Without it the bytes outlived the session that held them, in the database and in
every backup, forever.

**The rule:** the estimator measures what is sent, and a transport encoding is not
content. Any field whose length is not its token count — a data URI, a hex blob, a
minified source map — has to be named and bounded, or `len() // 4` will report a
number that is both enormous and wrong. And a large payload that appears more
than once belongs in a content-addressed store, referenced by hash everywhere —
including in the audit copy of the request it was sent in.

### N149 — Retention purged nothing, because nothing ever closed a session

**`neuman-admin session purge --older-than 5d` said "nothing to purge" on an
appliance with weeks of sessions.**

The predicate was `closed_at IS NOT NULL AND closed_at < cutoff`, used by both
the manual purge and the retention job. `closed_at` is written by exactly one
statement, `AgentSessionStore.close`, reached by exactly one route,
`DELETE /api/sessions/{id}` — and the client never calls it. The UI's only
session actions are `remove` (`removed_at`, which hides a row) and `PATCH`. The
server never closes a session on its own. So on a real appliance every
`closed_at` was NULL and the cutoff matched nothing: the scheduled retention job
had never been able to delete a session, and the new purge command inherited the
same empty set. The rows were all there — sessions from the 18th to the 20th,
idle six to eight days — and the query deliberately excluded every one.

**The cause was a predicate describing a lifecycle nobody performed.** The
design assumed the client closed conversations when a user was done; it does
not. A comment in the client had already noticed the symptom — an abandoned
session "never gets" `closed_at` — without following it to the consequence. A
field whose only writer is a route no client calls is not a signal, it is a
constant, and every query on it answers the same thing.

**Fix.** "Older than" is now **no activity for**: the clock is the newest of
`created_at`, the session's newest event, and `closed_at` if it was ever set.
`AgentSessionStore.purgeable` is the single predicate and `purge_ids` the single
delete, shared by the manual purge and the retention job, so "what the command
would remove" and "what the job removes" cannot disagree. The plan shows each
session's `last_activity`. Closing still counts — it is a later timestamp, so it
resets the window — but it is no longer required to become eligible.

**The rule:** a deletion predicate must be satisfiable by the lifecycle the
system actually performs. Check it against real data before building a command
on it; `nothing to purge` against a full database is the one-run test that
catches a flag nobody sets.

### N150 — The session search promised prefixes and matched whole words

**Typing `retent` found nothing; only the complete word `retention` matched.**

D52 says Postgres full-text "matches words and prefixes in any order", and the
client README repeated it. The query was built with `plainto_tsquery('simple',
…)`, which parses free text into *whole lexemes*: `ret` became the lexeme `ret`,
and the `simple` dictionary stored the message's token as `retention`. Different
lexemes, no match. The only partial match was the title clause, `title ILIKE
'%retent%'`, which is why a partial of a session's name worked while a partial
of its conversation did not.

**Why it went unnoticed.** `plainto_tsquery` is the correct *parser* — it treats
`&`, `|` and `:` as words rather than operators, which is the injection story
D52 relies on — and everything around it was right: the index, the rebuild, the
headline, the title fallback. What was missing was `:*`, one suffix per lexeme,
and the sentence that promised it lived in a decision rather than a test. The
appliance search test only searched whole words (`retention`, `six`), so a query
builder that could never match a prefix passed.

**Fix.** `AgentSessionStore.search_for_account` rewrites `plainto_tsquery`'s
output — which is already quoted and normalized — by suffixing each quoted
lexeme with `:*`, then parses that with `to_tsquery`. The rewrite cannot
introduce tsquery syntax, because the only text it edits is the parser's own
output. The rewritten query is built once in a CTE and used by both `@@` and
`ts_headline`; a headline built from a different query matches no terms and
returns a blank snippet for a row that did match.

**The rule:** a prefix search is a property of the *query builder*, not of the
index — Postgres full-text does not do it unless asked. When a decision promises
matching behaviour, the test that proves it has to use the input the promise is
about: the partial word, not the whole one.


### N151 — The waiting state was drawn as an event in the transcript

**The failure.** The waiting note was a line in the transcript that appeared
after two seconds of model silence and vanished when the first chunk arrived,
so the page moved twice per model call. Drawn through `add`, it also ended the
work run: the call before it and the thought after it became two runs, and the
rail's seam was left after the note was gone.

**The fix.** The wait is not drawn in the transcript at all. It is a state: the
rail's phase (`trackViewFrame`) says "waiting for the model", and the composer's
Stop button carries the clock. A state that comes and goes belongs on a surface
that has no height in the conversation.

**Worth keeping.** A state drawn through the event path leaves a seam when it is
removed, and a line that changes height twice per model call is a jump
generator. If a value is only ever its latest value, it belongs beside the
composer or on the rail, never in the log.

### N152 — The project's standing instructions are read on the wrong machine
- **What:** `_read_project_context` loads the working directory's `AGENTS.md` or
  `CLAUDE.md` with `Path(working_dir).read_text()` — a read on the *server's*
  filesystem. The files are on the acting human's machine. On a single-box
  appliance the two coincide and it works; with `programs.neuman-client` on a
  separate workstation, which is the deployment `README.md` documents, the path
  does not exist where the read happens and the function returns an empty
  string. A project's conventions are then silently absent from every prompt,
  and nothing says so — the turn runs normally, slightly worse, forever.
- **Cause:** the same confusion N84 records, in the other direction. There,
  `working_dir` reached the prompt and no dispatch; here a *file* in the working
  directory is read by the component that cannot see it. The tell is structural:
  the server runs as `neuman` and the tools run as the user, so any `open()` in
  the decision core against a user's path is either a bug or a privilege nobody
  granted. A home directory is `0700`, so even co-located it reads only because
  the appliance happens to be the workstation.
- **Found:** while adding `env_probe`, which asks a question about the same
  directory. The probe has to be a dispatch — a `.direnv` cache is as
  unreadable to `neuman` as an `AGENTS.md` is — and writing down why made the
  neighbouring function's read visible. Not a new bug; an old one nothing had
  reason to look at.
- **Fix:** the probe's shape — dispatch `fs_read` to the client and use what
  comes back. No new tool: `fs_read` is installed, declares `path`, and is
  already checked by the tool host against the dispatch's surfaces. Both of the
  turn's opening reads now take one `opening_surfaces` built once from policy,
  because two surfaces derived separately from the same policy are two things
  that can disagree.
- **And the bound travels with the request.** The ceiling used to be applied
  after reading the whole file, which over a socket would mean a 2 MB
  conventions file crossing the wire to be cut to 8 KB. `fs_read` takes a line
  limit, so the trim happens on the machine holding the file; the character
  ceiling stays as the backstop. `fs_read`'s own paging notice — `[showing
  lines X-Y of N; use offset=Z to continue]` — is stripped and replaced with a
  sentence saying the file was truncated: that notice is advice to a model about
  how to fetch the rest, and here there is no rest to fetch, so leaving it in
  would invite a call to retrieve exactly what the prompt bounded.
- **The silence was the real defect, so the fix logs.** A missing file stays
  quiet — most directories have neither, and `fs_read` exits non-zero for all of
  it — but a read that fails for any other reason now warns. The original bug
  was not the wrong path alone; it was that a project's conventions could vanish
  from every prompt with nothing anywhere saying so, which is what let it
  survive three features. A fix that failed silently in a new way would have
  kept the symptom that made this expensive to find.
- **The old tests proved nothing, and that is the lesson worth keeping.** Four
  of them wrote `AGENTS.md` into `tmp_path` and called the reader with that
  path. They passed before the fix and after it, because a fixture that puts the
  file where the code looks cannot tell "reads the user's machine" from "reads
  its own" — the same self-agreeing shape as the capability that reached nothing
  and the model client that never sent its key. Rewritten against the recorded
  dispatch, they fail on the old implementation; reintroducing the bug now
  breaks eight.
- **Worth noting:** this is the third time the boundary has been crossed in the
  same way, and all three were invisible to the tests. A fixture puts the file
  where the code looks, so "the server can read the user's working directory"
  and "the test wrote a file in tmp_path" are indistinguishable. The thing that
  catches it is asking *which machine* a path is on, every time one appears in
  the decision layer.


### N153 — `direction: rtl` turns a short path's leading slash into a trailing one
- **What:** the crumb bar read `home/alice/projects/`. The path was correct —
  `textContent` said `/home/alice/projects` — and the browser painted it with the slash
  at the far end.
- **Why:** the rule is `direction: rtl; text-align: left` on the path, which is the
  standard way to clip a long path's *head* rather than its tail: in a right-to-left
  box the text grows leftward and overflow is hidden at the start, so
  `/home/alice/projects/neuman/neuman-client/web` shows `…/web` and not `/home/alice/…`.
  The bidi algorithm also applies, and a leading `/` is a neutral character. In an
  RTL paragraph it resolves to the trailing edge, so any path short enough to fit
  loses its leading slash to the end of the line.
- **Why it survived:** it looked right on exactly the paths the trick exists for.
  The bug only shows on a path short enough to fit, and the deep paths — the ones
  that get clipped — were correct all along.
- **Fix:** wrap the text in `<bdi dir="ltr">`. The box stays RTL, so the clipping
  still takes the head, while the path itself is one left-to-right run that the
  bidi algorithm will not reorder.
- **Worth keeping:** the CSS was right; the bug was in what it did to text it was
  not designed for. A screenshot of the chooser in its ordinary state caught it in
  a second, and no source assertion could have: the string in the DOM was never
  wrong. Now asserted by `test_the_crumb_path_keeps_its_leading_slash`, which
  checks the wrapper rather than the pixels — and says so.

### N154 — Following the tail kept the bottom, not the reader's place

**The failure.** While a turn ran, the transcript jerked every time the step
changed. The chain of thought grew as it streamed, then a tool call replaced it
with a different-sized card; a result grew the card again, and the next thought
replaced that. The view was at the bottom the whole time, so every one of those
resizes moved the rows above it under the reader's eye. Following the tail is
not the same thing as holding still: a scroller can be pinned to its end and
still move everything the reader was looking at.

**The first cause.** A detail was content-sized. The thinking block grew until
its `min(40vh, 420px)` cap, and a tool card grew with its arguments and output,
with a `max-height` and a scrollbar on each output shape inside it. `followStep`
then swapped one card for the next on every step, and `grow` restored
`scrollTop = scrollHeight` after the swap — which kept the newest pixel visible
and did nothing for the reader's place.

**The second cause, found after the first fix.** Giving every detail one fixed
height (`--step-h`) removed the *size* difference, but the card was still mounted
under one row and unmounted from another on every step. The open and the close
were each a 150ms height transition, so the transcript still moved on every swap
— a fixed-height card folding out above while the next folded in below is still
a card-shaped motion. A crossfade between two cards at different document
positions cannot stay in one place.

**The fix.** The live step's detail is moved to one **stage** at the run's
foot; new rows are inserted before the stage, so the chain and the rail grow
upward out of a card whose box never moves. A step change is two opacity
transitions in the same place. The stage is one fixed height, with its body
scrolling and its head and approve/deny row outside the scrollport. Measured in
the browser: the stage's top stayed within a pixel across a thought → tool →
thought sequence, the tool step grew the transcript by 29px (its row), the
500-line result by 0, and the stage itself had no animations on any step.

The end of the turn is a separate case, and the final shape is a hybrid: the live
step is the stage, every history row is an inline disclosure (D64), and when
prose starts the stage folds into a **reserved blank** that the reply fills. The
fold is the same height change this entry warned about, made safe by the
reserve; `done` closes a blank the reply did not use.

**Worth keeping.** A live list whose items change size has a layout jitter
generator at every swap, and scrolling to the end only hides the symptom at the
bottom edge. A fixed height is necessary but not sufficient: if the card is
still mounted and unmounted per step, the fold moves it. Make the live item's
outer box constant, put the variable content inside it, and crossfade in place.
Also: a `max-height` is not a fixed height — a short card still stops at its
content, and the first small result brings the jump back. Folding the live card
at the end of the turn is the same jump *unless the vacated height is reserved*:
the stage folds into a blank the reply fills (D64), and `done` closes what the
reply did not use.

### N155 — The reserved blank was funded a frame late, and the clamp ate it

**The failure.** In the mockup, the card folded and the reserve stayed at zero,
so the whole transcript slid up by the card's height — exactly the jump the
reserve exists to remove. In the client, an overflowing transcript held the
blank but lost 2–32px of scroll position as the stage left the layout, and the
scroll listener then zeroed the reserve.

**The cause.** Three frames of the same invariant.

1. `pinBottom()` sets the pin as the fold starts. The measured gap is exactly
   zero then, and `settle` treated zero as "the content caught up", cleared the
   pin, and never grew the reserve. The pin has to survive its own creation
   frame; only content growing *past* it ends the hold.
2. In an overflowing log, the stage's transition shrinks the content a frame
   before the reserve grows. The browser clamps `scrollTop` to the smaller
   height, and the clamp is not undone when the reserve catches up. The blank
   has to be funded *before* the stage starts to shrink, and the fold must not
   settle until the stage is out of the grid.
3. A scroll event from that clamp is an upward move of a few pixels. The scroll
   listener read it as the reader leaving the tail and cleared the reserve. A
   programmatic or layout move has to be recorded after `settle`, and the
   listener must judge direction with a threshold, not distance alone.

**The fix.** `settle` keeps the pin at target zero; an overflowing fold
pre-charges the reserve and holds the frame loop until the stage is removed;
`settle` records `lastScrollTop`; the log has `overflow-anchor: none`. The short
case keeps the frame-by-frame reserve because there is no scroll range to clamp.

**Worth keeping.** The invariant is a height (`contentSpan + reserve`), not a
position: a short transcript is bottom-aligned, so the reserve's top is pinned
to the window and does not move. A reserve is not a spacer added after the fact;
it has to exist before the height it replaces is removed, or the browser has
already clamped. And a scroll listener that cannot tell a clamp from a gesture
will undo the blank it has just measured.

### N156 — A saved instruction cleared the box that had just stored it

**The failure.** The Assistant pane's Save called `applyInstructions(await
api("PATCH", "/api/me/instructions", …))`. That reply is `{instructions: "…"}`,
and the function took a *string*: `typeof text === "string" ? text : ""`. The
object became `""`, `applyInstructions` wrote it into the textarea, and a
*successful* save wiped the box. The person who had just saved their standing
instructions saw an empty field and could not tell whether anything had been
stored. It had: `/api/me` returned the text, and the next turn's system prompt
carried it in the right place.

**The cause.** Two callers, two shapes. The identity paths had the field in hand
(`body.instructions`); the save had a payload. A parameter documented as "the
text" is correct for one of them, and the guard that made the function tolerant
of a *missing* field — a login that failed before the row was read — silently
converted "wrong argument" into "no instructions", which is the same value as a
user who deliberately cleared it. A tolerant reader turns a caller's mistake
into a plausible answer.

**Why nothing caught it.** Every client test asserts source strings, and the
route test asserts what the server stored. Nothing looked at what the box showed
after a successful reply, and the 200 made the write test green. The source
assertion now checks *which argument each caller passes* and that the field is
read inside the function, but the honest lesson is narrower than "add a test":
a display path with no behavioural test needs its contract pinned, because its
bugs are invisible to both the route's tests and the transcript's.

**The fix.** `applyInstructions(payload)` reads `payload.instructions`, and all
three callers — login, reload and save — pass the payload they already have. The
save and the identity responses now take the same shape, so there is no second
way to call it.

**Worth keeping.** When a function accepts "one of these" and a wrong shape
degrades to a valid value, the wrong shape is what it will eventually get. Make
the argument the thing every caller actually holds.

### N157 — A client whose local account name differed from the appliance account could never connect

**A legitimate deployment was refused by the handshake, and the refusal named
the wrong thing.**

`hello` carried a `user` field, filled from
`pwd.getpwuid(os.getuid()).pw_name`, and the core refused a connection unless
that string equalled the account the session cookie was issued to. The check was
a remnant of the Unix-socket transport, where it compared the name to
`SO_PEERCRED`; once the session cookie became the identity it only asserted that
the client's local account was spelled like the server's. So a person whose
workstation account was `alice` and whose appliance account was `bob` could log in
and use the API and the UI, but the daemon was refused on every reconnect
(`connection is not that account`), and every dispatch was `unavailable`. The
`run --user` flag could satisfy the string check, but it did not change the uid
that ran the tools or the home they were checked against.

**Fix:** the session names the account; `hello` carries only the tools and the
home directory on the client machine. The local account is the executor, not the
principal (D67).

**The rule:** a check that only proves two names are spelled the same is not an
identity check. If the two ends are different machines, send the facts the other
end cannot learn and let the credential name the person.

### N158 — The administration page loaded the chat window's stylesheet, so its `#sessions` host became a fixed rail

**The Sessions tab drew a 256px column pinned over the left edge of the page,
above everything, with its table clipped and no scrollbar.** The tab was not
broken; the page had loaded a stylesheet written for another page.

`admin.html` linked `/neuman.css` to get the palette tokens `admin.css`
uses. `neuman.css` is the chat window's document stylesheet end to end, and it
styles bare element ids — and the two documents had four names in common:

- `#sessions` is the admin session table's host; in the chat window it is the
  session rail, `position: fixed` at `top`/`left`/`bottom: 0` with
  `width: var(--sessions-width); overflow: hidden; z-index: 30`. The admin's
  table therefore painted over the page at the left edge, in a box that could
  not scroll and clipped its content.
- `#models` is the admin overview's models card; in the chat window it is the
  popover that opens *upward* from the model chip (`position: absolute; bottom:
  100%`). The card measured `y = -42px`: it was laid out, and above the
  viewport.
- `#login` is the admin sign-in card; the chat window sizes its login box to
  `width: 300px; margin: auto; display: flex; gap: 10px`. An id selector beats
  `admin.css`'s `.admin-login` class, so the card was 300px wide and `auto` from
  the top instead of the 360px card 12vh down that the page specifies.

**Why nothing caught it.** No test read `admin.html` at all — the client's suite
opens the chat window's files and nothing else. The page loads, the modules run,
every API call it makes is correct, and the server never sees the browser. A
layout fault in a document with no test is invisible to every check this tree
had.

**Fix:** the palette moved out of `neuman.css` into `palette.css` — every token,
`color-scheme`/`accent-color`, and the two base rules both documents share
(`box-sizing`, the scrollbar). `index.html` loads `palette.css` then
`neuman.css`; `admin.html` loads `palette.css` then `admin.css`, and nothing
else. The admin page now spells out the few control affordances it had been
inheriting — the hover and focus edges, and the ink of a failed sign-in — so
`admin.css` is the whole truth about that page. Two tests pin the shape:
`test_each_document_loads_the_palette_and_only_its_own_layout` reads the
stylesheet list out of each page, and
`test_the_administration_page_borrows_no_token_from_the_chat_window` holds every
`var()` in `admin.css` to a declaration in `palette.css`.

**The rule:** sharing a stylesheet shares its selectors, and a document
stylesheet's selectors are written for its own document. Share the tokens, not
the file — and the moment a second document needs them, the tokens have to be
in a file that holds nothing else. An undefined custom property is not an error
either: it renders the property's initial value, so a page that reaches into
another page's sheet for a value loses it silently. That is why the token test
is worth its cost.

### N159 — A `<pre>` in a table cell is as wide as its longest line, and so is the table

**Pressing `read` on a session made the whole administration page as wide as the
log's longest line** — a base64 tool payload measured 1814px in a 953px window,
so the card, the page and the scrollbar all grew with it.

The log is a `<pre>` inside a `<td>`, and the box had everything a scroll
container should have: `overflow: auto` and a `max-height`. None of it worked,
for one reason. In table *auto* layout a cell's minimum width is its content's
intrinsic width, and a `<pre>` with no width has an intrinsic width equal to
its longest line. `width: 100%` on the box was "at least 100%" of a cell that
the table was already sizing to the line, so the box had room to grow into and
`overflow: auto` never kicked in. The table itself, `width: 100%`, is the same:
in auto layout that is a minimum, not a target.

**The first fix traded the bug for a narrower copy of itself.** `table-layout:
fixed` on the sessions table makes `width: 100%` exact, so the cell takes its
share of the column and the log scrolls inside the box. But fixed layout sizes
columns from the first row or not at all, so the three short columns were pinned
in pixels — 120 + 168 + 84. At 953px that looked right. At 360px those columns
were wider than the window on their own, the table grew to 372px in 312px of
room, and the page scrolled sideways again: the same failure, now reached by a
narrow window instead of a long payload. Verifying a width fix at one window
size is how that got through.

**Fix now, and the one that holds:** nothing inside a cell may set the table's
width. The log is `white-space: pre-wrap` — JSON keeps its indentation and a
long line ends at the box's edge — plus `overflow-wrap: anywhere`, and every
`.admin-table td` gets `overflow-wrap: anywhere` too, because a long title or
session id sets the same floor. `anywhere` rather than `break-word`: only
`anywhere` changes the intrinsic size the layout algorithm reads; `break-word`
breaks the line and leaves the minimum where it was. No `table-layout: fixed`
and no column widths — an auto-laid-out table whose content cannot fail to wrap
is already exactly `width: 100%`.

**The rule:** `overflow: auto` is a promise the browser can break — it holds
only when the box has a definite size, and a table cell with auto layout does
not. The durable answer is to remove the content's minimum width rather than to
force the container's. This is the same shape as N72 (`overflow-y: auto` does
nothing without `min-height: 0`) and N121 (a grid item will not shrink below
its content): the scroll container was declared and the constraint that gives it
a size was not. Pinned by
`test_the_admin_session_log_wraps_instead_of_widening_the_page`.

### N160 — A page of fifty was cut out of a read of the whole session

**Pressing `read` on a long session took about half a second**, and the wait grew
with the session — so the log you most need to inspect was the slowest one to
open.

`_read` did `list(await EventStore(conn).read(session_id))` — `SELECT *` over
every row of the session — and then handed the list to `session_ops.events`,
which sliced the fifty rows the page shows out of it. The same for `kinds`,
which reads only counts, and for `event`, which reads one row. On a session
shape a real one reaches (12,300 rows, 13 MB of payload) the fetch measured
**480ms against 69ms** for the same window resolved in SQL, and every row of
that difference was decoded into a Python object and discarded.

**Why it survived review.** The ops module's own docstring said the right thing
— "a session is routinely tens of thousands of rows" — and the adapter already
had a `kinds=` projection for exactly this reason. What was missing was not
knowledge but a place for the window to be resolved: the ops functions took a
list, so the only way to call them was with the whole log, and the caller did
what the signature allowed. A function that takes everything gets everything.

**Fix:** the store resolves the window (`EventStore.window`), the orientation
(`summary`) and the single row (`at_seq`), and `session_ops` keeps what a reader
sees — payload clipping, `next_after`, the order of the kind counts — from the
`EventWindow`/`SessionSummary` it is handed. Both surfaces (`session_query` and
the admin route) call the same pair, so the shape stays in one place (D9).
`kinds` now reads no payload at all: three indexed aggregates and a `GROUP BY`.

**The rule:** a read whose page size is fixed must not be implemented by
fetching the set and slicing. And a fake repository cannot show you the cost —
it answers from a list either way — so `FakeEvents` now records every whole-log
read and `test_the_admin_event_window_reads_a_window_and_not_the_log` asserts
the route makes none. That test was written to fail first: the whole-log fetch
was put back, the assertion fired, and only then was it removed.

**Measurement note:** taken with `psql` against a local cluster holding 12,300
rows (12,000 streamed chunks at 120 B, 300 tool results at 40 KB, 60 `request`
rows at 300 KB, incompressible so the bytes are real). It is the transfer and
decode that dominates, which is why the fix is about *which rows* are read
rather than about indexing.

### N161 — A cursor row is a dict, and unpacking it positionally yields its keys

**Every press of `read` on the administration page returned HTTP 500** — the
page said nothing, the server log had the traceback. It was a fresh regression,
introduced by the N160 fix.

The pool connects with `row_factory=dict_row` (`foundation/db/pool.py`), so
`cursor.fetchone()` returns a mapping, not a tuple. The new aggregate was
written `total, last_seq = await cursor.fetchone()`, which on a mapping **does
not fail**: unpacking iterates the keys, so `total` was the string `"count"` and
`last_seq` the string `"max"`. The failure came two frames later, where
`rows[-1].seq < last_seq` compared an int to a str. The `GROUP BY` version was
worse — it produced `{"kind": "count"}` and only raised when those strings were
summed.

**Why no test saw it.** Every surface test fakes the event store, and the fake
returns lists of objects the decision layer already understands. The real
adapter had no test at all, so the shape psycopg actually hands back was never
exercised — the test suite agreed with a fiction. It also survived my own
verification: I measured the *queries* against a real cluster with `psql`, which
proves the SQL and says nothing about the Python reading it.

**Fix:** read by name, with an alias, like every other aggregate in the file
(`count_for_sessions` already did). Worth stating as the rule it is: **a row
from the pool is a mapping; the only way to read it is by column name.**
`test_the_event_store_reads_its_rows_by_name` and
`test_the_event_summary_counts_by_name` drive the real `EventStore` against a
stub whose rows are dicts, and both were seen failing against the broken lines
before they were kept.

The general lesson is N35's, one layer down: a fixture that constructs the thing
under test proves only that the fixture agrees with itself. Here the fake did
not even construct the thing under test — it constructed something the real
adapter could never return.


### N162 — A model error recorded only `str(exc)`, which transport failures do not have

**Session `6e71973b4f8f` ended `model_error` with nothing to read.** The last
event was `{"type": "model_error", "error": ""}`, the client rendered the bare
code `model_error`, and journald said `model call failed` with `error: ""`. The
connection had died mid-stream after four seconds of reasoning, but nothing in
the record said whether that was a dropped socket, a provider abort, or a
timeout.

**The empty string is structural, not exceptional.** httpx wraps whatever the
transport raised: a `ReadTimeout` carries the bare `TimeoutError`'s message, and
a `ReadError` carries `anyio.EndOfStream`'s — both empty. Recording `str(exc)`
was recording the one part of the exception that is routinely absent. The class
name is what survives every wrapper, and it was the only fact thrown away. The
same session's earlier `model_error` was exactly 300 seconds after its request,
the gateway's `timeout_s` — a timeout the record did not name.

**A second gap in the generic path:** the catch-all's error frame omitted
`code`, which the ws schema requires, so the client would render
`undefined: the turn failed: …` for the failures that path exists to report.

**Fix:** `_describe()` names the class beside the message for the strings a
person reads (journald, the client frame), while the harness event keeps the
provider's message verbatim and carries `error_type` beside it. The catch-all's
frame now carries `code: model_error`. Pinned by
`test_a_model_failure_names_the_exception_it_was`; the administration page's
session log now opens at the tail (`last=50`), where a failed turn says why,
pinned by `test_the_admin_session_log_opens_at_its_tail`.

### N163 — The account page had a second policy editor, and the copy described the wrong layer order

**An operator assigned a role and then edited the account's permissions, and
the role stopped meaning what the Roles section said it meant.** The account page
had a full “Declared (baseline)” editor beside the Roles card. That layer
composes *after* roles (`source.py`: defaults → roles → user → overlay), but
the card said it composed before roles. The Effective policy hint omitted the
layer entirely, and the exceptions editor called its inherited value “what the
roles give” even when it included the baseline policy. The panel therefore
presented two account-level editors — a baseline fragment and a runtime
overlay — with neither the source chain nor the override shown as a diff.

**Fix:** `users[].policy` was removed from the schema and from composition; a
migration converts an existing stored fragment to a per-account role so the
composed policy is preserved. The panel writes per-account changes only to
the overlay, where they are a visible delta with reset and pin. Account
creation assigns roles (and groups)
in one request, previews the composed policy server-side, and rejects a role
set that disagrees on a scalar instead of letting alphabetical role order pick
the winner. The account page reports such conflicts for roles that arrived
through a group. Pinned by the create/preview tests and by
`test_the_account_page_shows_roles_in_force_and_keeps_bootstrap_read_only`.

### N164 — The browser host crash-looped because the host seam handed a `Path` to `socket.bind`

**What:** after the changeover, `neuman-browser.service` restarted every two
seconds. `serve()` in `browser/bridge.py` called `socket.bind(socket_path)`
where `socket_path` was a `PosixPath`, and `socket.bind` takes bytes or `str`:
`TypeError: a bytes-like object is required, not 'PosixPath'`. Nothing listened
on `neuman-browse.sock`, so `browse` could not run, and the desktop entry —
which starts that unit — did nothing when clicked.

**Why the seam introduced it:** the socket change replaced `os.path.expanduser(
args.socket)` (a `str`) with `Path(args.socket).expanduser()`, and the socket
default with `browse_socket()`'s `Path`, while `serve`'s annotation still said
`str`. The annotation was the only place the contract was written down, and
Python does not check it at the boundary. A `TypeError` is not an `OSError`
either, so it escaped the handler that prints the runtime-directory hint and
became a traceback and a restart loop, each iteration relaunching Chromium for
the second or so it survived. The platform seam is the right place to return a
`Path`; the conversion belongs at the one call that speaks to the socket API.

**Why nothing caught it:** no test in `neuman-client` bound a Unix socket at
all. `test_transport.py` and `test_ui.py` exercise `websockets.serve` on TCP,
and the bridge had no test. Every path into `serve` the suite exercised was a
`str`, so it stayed green through Phase 1 while the Linux browser host was
dead — a reminder that "the client's tests are unchanged" is not the same claim
as "the client behaves the same".

**Fix:** `serve` takes `str | os.PathLike[str]` and converts once with
`os.fspath`, so the seam stays free to answer with the type it means.
`tests/test_bridge.py` binds a real socket through both a `Path` and a `str`,
asserts the `0600` mode, forwards bytes to a loopback echo server and asserts
the socket is unlinked on stop. Seen failing: with `bind(socket_path)`
restored, the `Path` case raises the production traceback while the `str` case
passes — the exact shape of the outage.

### N165 — A new test file is invisible to `nix build .#checks` until git knows about it

**What:** with `tests/test_bridge.py` written, `nix build
.#checks.x86_64-linux.client-test` reported `183 passed` — the count from
before the file existed. The file was untracked, and a flake's source is the
git tree: untracked files are not copied into the store, so the check ran the
suite without it. The same build after `server.bind(socket_path)` had been
reintroduced also passed, which reads as "the fix was unnecessary" rather than
"the test never ran".

**Why it matters:** the check matrix is the gate, and this failure is silent
success rather than an error. `nix develop -c pytest` reads the working tree
directly, which is why it saw 185 where the check saw 183.

**Fix:** `git add` the file — intent-to-add is enough — before trusting a check
run, and treat a check whose test count disagrees with the local suite as a
sign that something is not being seen. Diagnosed by running both and comparing.

### N166 — The backup route outlived its caller, and a retry wrote two dumps into one file

**What:** `neuman-admin backup` answered "cannot reach … timed out" after 30
seconds while its `pg_dump` was still running on the server, writing into the
destination. Running the command again — the obvious response — started a
second `pg_dump` into the same directory. Two processes writing one file both
exit 0, so the result is neither backup: the manifest was written by whichever
finished last, over a dump that is two interleaved streams. Found while taking
a pre-migration backup, which is the moment the artifact has to exist.

**Why:** the client's deadline is a fixed 30 seconds, chosen for a request
against a local socket that either answers or is not going to. A `pg_dump` of a
database with an audit chain, sessions and a search index takes minutes, and
the route runs it inside the request — so the deadline measured the dump rather
than the connection. Nothing refused a destination already in use, and a
timed-out client does not stop the work it started.

**Fix:** `AdminClient.request` takes `read_timeout`, and `backup`/`restore`
pass `None`; the connect deadline stays, because a missing server is still
worth failing fast on. `create_backup` refuses a destination that already holds
anything — `409 destination_in_use` — so a retry is safe to make and a
half-written directory is unmistakable rather than plausible. Pinned by
`test_the_dump_commands_wait_for_the_dump`,
`test_a_destination_that_already_holds_a_backup_is_refused` and
`test_a_backup_will_not_write_over_an_existing_destination`.

**The lesson:** a deadline has to bound the thing being waited for. "Either
answers or is not going to" is true of a socket handshake and false of a route
whose work is a whole database.

### N167 — Starting over left a unit that could not start, and no database to start against

**What:** the first real walk of "Starting over" in `docs/setup.md`: stop the
server, drop the `neuman` database, delete `/var/lib/neuman/state`,
`/var/log/neuman` and `/run/neuman/admin`, then `nixos-rebuild switch`. The
switch reported `neuman-server.service` failed with `status=226/NAMESPACE`,
`neuman-admin bootstrap` found no socket, and the document had promised that
re-activating "recreates the database, `/run/neuman`, and the state
directories". It recreated none of them.

**Why:** two halves, both about what runs when. The server's directories came
from tmpfiles rules, and a switch re-runs tmpfiles only when its rules change —
they had not, so `/var/log/neuman` stayed deleted, and a `ReadWritePaths`
entry naming a path that does not exist is a sandbox systemd cannot build,
which is 226 before a line of Python runs. And `ensureDatabases` runs in
`postgresql-setup.service`, a oneshot with `RemainAfterExit` that had already
run at boot, so starting `postgresql` again did not recreate the dropped
database; the server was also ordered only after `postgresql.service`, so even
a first boot raced the database's creation and leaned on `Restart=on-failure`.
The VM tests could not see either: every one boots a fresh machine, where
tmpfiles and the setup unit have just run.

**Fix:** the unit makes its own directories on every start — `StateDirectory`,
`LogsDirectory` and `RuntimeDirectory=neuman/admin`, each `0750 neuman` — and
the server makes `state/`, `policies/` and the `0700` `secrets/` beneath with
their modes. The remaining `ReadWritePaths` entry is optional (`-/run/neuman`).
The server orders after, and requires, `postgresql-setup.service`. Starting
over is now: stop, `dropdb`, delete, `systemctl restart postgresql-setup`,
start — no rebuild. `vm-restart` runs exactly that and checks every directory's
owner and mode and an empty baseline; against the previous module it fails the
way the real machine did, with 226/NAMESPACE.

**The lesson:** a procedure in the documentation is code, and it had never
run. Of everything the appliance does, the steps an operator follows by hand
are the ones no test exercised — and a test that boots a fresh machine is the
one machine on which "rebuild puts it back" is true.

### N168 — The idle timers never fired, so an on-demand model was resident for a day

**What:** measuring the runtime spike on the workstation, the GPU was full before anything
ran: Qwen had been loaded since the previous evening, 19 hours earlier, though
its residency was `on-demand` with a 15-minute idle stop. `systemctl
list-timers` showed both idle timers with no next elapse and no last trigger.

**Why:** each timer had `OnUnitActiveSec` and `OnUnitInactiveSec`, both measured
from the last activation of the unit the timer starts — the idle-stop service.
That service had never run, so neither had a start to count from, and nothing
else ever scheduled one. The GPU sampler's timer worked because it also has
`OnBootSec`. No test could see it: the VM tests declared no on-demand model,
and the unit tests read the module's text.

**Fix:** the timers are gone with the units they belonged to (D78). The runtime
host stops an idle model itself through the router's per-model
`sleep-idle-seconds`, which `test_runtime.py` reads back from the file the
router is started with.

**The lesson:** a timer relative to its own unit needs something to start the
first cycle. A schedule that cannot start looks exactly like one that never
needed to fire.

### N169 — The chat router offered models nobody chose, with a download argument

**What:** `llama-server --models-preset` with two sections listed four models.
The other two were from `~/.cache/huggingface` on the machine it ran on, each
with `--hf-repo` in the arguments the router would start it with, and the log
said `router mode enabled - do not expose to untrusted environments`.

**Why:** router mode discovers models in the user's Hugging Face and llama.cpp
caches as well as the preset file, and a request naming one loads it; a name
of the `org/repo:quant` shape is something it knows how to fetch. On the
appliance it runs as `neuman`, so the set offered would be whatever happened to
be cached in that account's home.

**Fix:** the host starts the router with `HOME`, `LLAMA_CACHE`, `HF_HOME` and
`XDG_CACHE_HOME` pointed at an empty directory and no `--models-dir`; measured,
it then lists only the preset sections, and a request for a cached model or an
`org/repo:quant` name is a 400 `not found` with no download attempted. The unit
has no network beyond loopback regardless. `test_runtime.py` checks the
router's environment, and `vm-runtime` checks that it lists only the one model
the test added.

**The lesson:** a dependency's convenience defaults are part of its interface.
"Loads what the preset file names" was true and was not the whole of what it
did.

### N170 — The server and the runtime host each passed their tests, and refused each other

**What:** the first walk of the Models section on real hardware chose Qwen3-TTS to
speak, and the runtime host's status said `the speak entry has fields this host
does not take: ['idle_minutes', 'residency']` — and, since a refused document
is refused whole, it was also not running the chat model the same document
named.

**Why:** the speech preset's policy carried `residency` and `idle_minutes`, the
decision layer's table of settings per backend allowed them for `llama-tts`,
so `voice_entry` copied them into `voice.speak`. The host, written separately
from the same intent, allows `llama-tts` only `gpu_layers`, because a process
that runs once per utterance has no residency. The server's tests checked what
it writes, the host's what it reads, each against its own idea of the shape;
nothing ran a real preset through both.

**Fix:** the preset and the table were made to agree with the host. Speech has
since been removed, but the test that caught this stays:
`test_every_shipped_local_preset_is_a_document_the_host_accepts` builds every
shipped local preset's entry the way the routes do, writes it the way
`publish_runtime` does and parses it the way the host does.

**The lesson:** two processes that share a document need one test that
crosses the boundary with the real data. Two halves each tested against a
fixture is how N38 shipped, and this is the same shape.

### N171 — A page title with `flex: 1` absorbed the whole shortfall and wrapped to one word a line

**What:** the first render of the new section head in a 762px window showed the
section's description as one word per line — "Who / can / sign in, / what /
they / may / do" — beside a search box and a button. The title column measured
44px of a 434px row, and the actions measured 374px.

**Why:** the title was `flex: 1` with `min-width: 0`, and `min-width: 0` is
exactly what allows a flex item to shrink past its content. With a zero basis
and every sibling `flex: none`, it was the only item that could give, so it
absorbed the entire shortfall and stopped at whatever was left. The wrap that
would have saved it needed `flex-wrap`, which the head did not have. The
account page's detail head had the same shape, with the same result.

**Fix:** the head wraps (`flex-wrap: wrap`) and the title takes a basis
(`flex: 1 1 280px`), so a narrow window drops the actions onto a second line
instead of squeezing the text.

**The lesson:** `min-width: 0` on a flex child is permission to shrink to
nothing, not an instruction to shrink sensibly. A column that must keep a
width needs a basis, and a row that must not crush needs to be allowed to wrap.

### N172 — Six columns fitted a narrow card only by breaking words inside them

**What:** the roster at 762px painted past the right edge of its card. The card
was 434px and the table was wider, so the Last seen column was cut off; letting
the card scroll sideways instead gave a horizontal scrollbar for two figures
that are the first thing on the account's own page.

**Why:** a table's floor is its columns' floors added up — six of them, each
with a header word that cannot break and 20px of padding — and "make the cells
narrower" was not available. `overflow-wrap: anywhere` is what N159 added so a
long path cannot set a table's floor; it does the same thing to a column of
token counts, turning `128,410` into `128,4` over `10`. The floor here was not
one long token, it was six short columns.

**Fix:** the roster drops Today and Sessions below 900px — both are one click
away on the account page — and the two figures are `white-space: nowrap` so a
number never breaks mid-digit. `admin-accounts .opt` is the contract between
the header row in `admin.html` and the cells `renderAccountRows` builds; a
seventh column added without it silently changes what the breakpoint protects.

**The lesson:** a table can be too wide without any single cell being too long.
When it is, the answer is fewer columns, not smaller type and not a scroll
container.


### N173 — The model picker chose a model nobody had chosen, and could not say "no model"

**What:** the composer said "no model" on load, and opening the models panel
selected `deepseek` — a model the person had not picked. It was not a panel
bug: the hidden `<select id="model">` was rebuilt from the granted list on
every policy read, and a `<select>` with no explicit value selects its first
`<option>`. The panel drew from that select, so opening it showed a choice that
had already been made by the browser.

**Why:** "which model is in force" was never stated, only implied by the first
option. That is indistinguishable from a real choice, and it made a default
setting impossible: there was no state in which nobody had chosen. The same
shape meant switching sessions could change the model silently, because the
select was per window rather than per conversation.

**Fix:** the select has an explicit `(no model)` option with value `""`, and
`loadPolicy` never selects a model on its own. The account has a default model
and reasoning level (`accounts.default_model`, `default_reasoning`, set in
Settings › Assistant), and a branch restores its own pair, falling back to that
default only when the branch has never chosen. A default whose model is no
longer granted or registered leaves the composer on "no model" and says which
name is missing, rather than substituting the first granted one;
`/api/policy` carries `model_info` with `registered`/`local`/`loaded` so a
non-operator can tell the difference without the operator-only panel.

**The lesson:** a default has to be a value somebody can hold, not the absence
of a decision resolved by the widget. A control that cannot represent "nothing
chosen" will choose something, and the first option is the least considered
choice available.

### N174 — A branch renderer threw `view is not defined`, and every user message vanished

**What:** sending a prompt in the chat window drew no bubble, and the transcript
read `Could not render a "user_message" frame: view is not defined`. The turn
still ran; only its own message was missing.

**Why:** the branch change added turn bookkeeping (`turnOpen`, `turnIndex`, the
edit action that forks before a prompt) to the `user_message` and `done` cases of
`handle`, written against `view` — the parameter `trackViewFrame` takes. `handle`
has no such parameter: renderers address the shared single-view names, and a
background branch's frames are drawn through `withView`, so the branch a frame
belongs to is `renderView`. The throw was contained by the N99 catch and shown
as a note, which is why the failure named itself instead of freezing the turn.

**Fix:** `handle` names the render context once (`const view = renderView`) and
the cases read it. A second gap in the same bookkeeping went with it: `replay`
counted turns to place its own edit actions and never wrote the count to the
view, so the opening message of the next turn typed into a reopened session
looked like turn one and lost its edit action. `replay` seeds
`renderView.turnIndex`, and `makeView` states `turnOpen: false` rather than
leaving it undefined. The two paths that end a turn without a `done` — a send
that never left and a socket that closed — clear it too, so a message after a
dropped connection opens a turn instead of steering a cancelled one.

**The lesson:** a renderer added to the frame switch is not handed a view and
must not invent one. `renderView` is the only name that says which branch a
frame belongs to, and it is the render context — not the branch on screen —
because a background branch's frames arrive too.

### N175 — An empty name was an error, in a field whose default already had a name

**What:** an administrator who cleared the appliance's name and saved was told
`name_empty`. The name has a default and is written at startup, so the box was
never meant to be empty — clearing it had no answer the server would take.

**Why:** the write treated "nothing" as a missing value rather than as a
request for the value the release ships. The name has a default — it is written
at startup so no reader has to decide what an empty one means — and a box that
refuses to be cleared contradicts the default it already has. The operator's
next move was to retype the word they had just deleted.

**Fix:** an empty name is collapsed to the default and stored, and the route
answers with the name it stored, so the wordmark shows "Neuman" rather than a
reversion the operator has to interpret. The refusal survives only where it
describes something real: a name longer than 64 characters, or one carrying a
control character, answers with a sentence rather than an error token.

**The lesson:** a field with a default must accept emptiness as the default.
Rejecting it makes the default unreachable from the one control built to reach
it, and names an implementation token to a person who only meant "put it back".

### N176 — The pencil vanished on the way to it

**What:** the first fork mock put the edit control in a strip after the message
bubble and revealed it on the bubble's hover. Moving the pointer from the text
toward the pencil crossed the gap between them; the bubble lost `:hover`, the
control disappeared, and the click landed on nothing. The affordance looked
present in a screenshot and was impossible to use.

**Why:** the reveal was scoped to the bubble, but the control was a sibling
outside its box. A gap between a hover target and the thing it reveals is a dead
zone: the pointer is over neither the element that owns the rule nor the element
the rule shows. Padding would have widened the bubble's box, but the text would
still have had to make room for a control that is only occasionally there.

**Fix:** the action is a child of the bubble and overlaps its corner
(`top: -12px; right: -12px`), so `:hover > .edit-action` and
`:focus-within > .edit-action` keep it visible while the pointer is on either the
text or the pencil. The bubble's measured width is still the text's width, and a
touch screen gets the action without a hover (`@media (hover: none)`). The source
test asserts the child relationship, not only the position.

**The lesson:** a hover-revealed control has to live inside the hover target's
hit area, or carry its own hover through the whole path to it. A gap is not a
margin when the thing on the far side is waiting for a pointer event.

### N177 — The fork menu's branch rail stopped at the first child and missed the dots

**What:** the local branch tree's vertical rail ended halfway down the children
container, so every child after the first had an elbow with no rail above it.
The rail was also left of the source dot, and each elbow stopped at the row's
edge instead of reaching the child's dot. The tree read as three unrelated
marks rather than one fork.

**Why:** the rail was one `::after` on the children container with
`bottom: 50%` — the midpoint of the **whole container**, not the last child's
centre. Its `left` and the elbows' width were offsets guessed from the row, but
a flex row's dot sits inside its padding, so the row edge is not the dot and a
fixed 14px elbow could not span the gap. The parent's rail had the same guessed
`left`, so it did not line up with the parent dot either.

**Fix:** position the parent rail at the parent dot's centre
(`left: 13.5px` from the row's padding box), and give every child its own
vertical segment at the same x (`left: -9.5px`). A child's elbow is 22px — from
that rail to the child dot's centre — and a non-last child's segment continues
through the 2px gap (`bottom: -2px`), while the last child's segment stops at
`50%`, its elbow. The container has no rail of its own; the segments are per
row, so the rail ends where the last row does.

**The lesson:** a connector between two boxes must be placed from the geometry
of the things it connects — the dot's centre inside the row's padding — not
from a guessed offset from the row's edge. And a rail's height belongs to the
last child, not to the container's midpoint, or every branch after the midpoint
is left floating.

### N178 — The add search said it closed when focus left, and it could not close

An account's `+ Add to group` opened a search that was meant to close when focus
left it, and the only thing that closed it was pressing the same pill again. The
handler was assigned as `panel.onfocusout = …`. Browsers have no such property:
`focusin` and `focusout` are events without an `on` attribute, so the assignment
set an ordinary expando and nothing ever called it. No error, and the browser
check that passed had only clicked things inside the panel.

**The fix:** `addEventListener("focusout", …)`, on a host made for each opening so
a redraw cannot stack listeners, and the pill reads Cancel while it is open. The
pill's `mousedown` is prevented, or a press on Cancel takes focus from the search,
closes it, and the click that follows opens it again.

**The lesson:** a control's way out is tested by leaving, with real input. A
dispatched event cannot move focus, so it cannot show this failing.

### N179 — A second turn on a live branch made the conversation unsendable

**What:** session `2b3895c13955`, branch `b75f3f39…`. Its last turn started at
03:35:29 and was 127 steps in. At 03:47:09 it dispatched a `shell` call that ran
for 41 seconds. At 03:47:34 a second WebSocket to the same branch was accepted
and started a turn with “continue”: its first request carried the assistant
message with that `tool_calls` and no `tool` reply yet, and the gateway answered
400 “An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id'”. Two more “continue” attempts got the same.
The shell result landed at 03:47:50, but by then two `user` messages sat between
the call and its result, so the running turn's own next request got the same
400. Every later request on that branch was refused too: the history is derived
from an append-only log, so the interleaving could not be undone.

**Why:** `turn_in_flight` was a local variable in `_branch_socket` — per
connection, not per branch. The contract already said it meant “one is already
running”, and D63 already named the missing server-side registry as the
gap; a second browser window could start a turn while the first was mid-dispatch.
`project()` then emitted whatever the log held, including an assistant
`tool_calls` with no contiguous replies, which every provider refuses. Nothing
repaired a history after a turn was killed between its calls either, so a
cancelled fan-out could brick a branch the same way.

**Fix:** `Services.turns` is a registry of branch → running turn, consulted
before any socket starts one; a finished task is pruned even if its done
callback has not run, and the callback removes only its own entry. `project`
pairs every assistant `tool_calls` with its replies: a result that arrived after
another turn is moved back beside its call, a call with no result anywhere gets
a `tool` message saying so, and a `tool` message with no open call becomes a
`user` note rather than an orphan. The client treats `turn_in_flight` as a
failed send, so the composer does not stay in Steer over a turn it never
started.

**The lesson:** “one at a time” belongs to the resource being written — the
branch's log — not to the connection that happens to be attached to it.
Connection-local checks are per *client*, and a second client is exactly the
case they miss. And a history derived from an append-only log must be made
*sendable*, not merely faithful: the log cannot take back a proposal, so the
projection is the last place that can answer one.

### N180 — `web_fetch` never spilled, so every page arrived inline whole

**What happened.** A research session (`f75a901079e0`, 2026-11-09) fetched eleven
retail and vendor pages of 70–640 KB each with `web_fetch`, and every byte of raw
HTML went into the model's history. One step's prompt was 374k tokens, almost all
of it cache misses; the turn compacted twice, re-fetched a page compaction had
dropped, spent 16.4M prompt tokens over 142 steps, and finished three of the
seventeen facts it was asked for. Its own report named the cause correctly.

**Why.** When execution moved to the client, the 4 KB preview the runner used to
apply (N127) collapsed into the manifest's `max_output_bytes`, which became both
what was forwarded and where spilling began. `web_fetch` stops itself at
`1 MiB − 256` so its truncation notice fits under its 1 MiB cap — so its output
could never cross the threshold that would spill it. `shell` and `python` had the
same 1 MiB "preview". The schema still said "past a small preview", and
`persist_output` was in every manifest and read by nothing.

**And the spill it would have made was unreadable.** The note names a file under
the client's state directory, and the tool host checked `fs_read`/`fs_search`
against the account's surfaces alone. An account whose surfaces do not cover its
own state directory — any service account confined to a work directory, as this
one was — would have been handed a path and refused it.

**Fix.** The executor forwards `PREVIEW_BYTES` (16 KiB) for a persisting tool and
writes the whole stream to the spill file up to `max_output_bytes`; a
`persist_output = false` tool forwards up to its cap and writes nothing. The
tool host admits the spill directory for the read tools whatever the surfaces,
and never for a mutating one (D42). `truncated` now means bytes were lost, so a
spilled file is no longer described as cut when it is whole.

**The lesson:** a threshold that a producer bounds itself just under is a
threshold that never fires. When two limits mean different things — what the
model is shown, and what is kept — they need two numbers.

### N181 — The first unattended sweep could not read any large page

**What happened.** The site's task ran its first real sweep
(`2026-11-11-9b792330b67c416691d532888780b5a3`, 2026-11-11). Both big-tracker
pages and every vendor page it fetched spilled, as N180's fix intends — and the
spill files were single lines of 25 KB to 1 MB, because that is what a minified
page is. `fs_read` refused them: *"line 1 is 129448 bytes, over the 32768-byte
read limit; use fs_search or shell to extract the part you need"*. The research
step's envelope granted neither `fs_search` nor `shell` — a command tool cannot
be granted to an agent step that a run step follows (D96) — so the tool's advice
was a dead end, and every large page was unreadable past its first screenful.

`fs_search` would not have rescued it either: one match printed the whole line,
and the client stops forwarding at the manifest's cap, so a hit near the end of a
130 KB line produced an entry that was truncated *before* the match. A search
that says it found something and shows no sign of it is worse than a miss.

**Why.** Both tools were written for files that are lines of prose. "No line is
ever cut" is the right rule for `fs_read` on a source file and the wrong rule on
a document that is one line; the tool had no third option, so it refused. The
refusal named tools rather than describing the file, and the envelope made the
name wrong.

**Fix.** `fs_read` shows the head of a too-long line with
`[line 1 is 1,048,321 bytes, longer than one page; showing its first 32,256 …
fs_search finds text anywhere in it, by pattern.]`, and `fs_search` prints a hit
with 600 characters of context, cut ends marked, so the hit is inside what the
client forwards. Both were checked against the real spill file the sweep could
not read.

**The lesson:** a bounded reader needs a story for the shape it cannot page.
"Use another tool" is not a story when the caller's envelope decides which tools
exist.
