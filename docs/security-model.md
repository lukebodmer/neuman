# The security model

Neuman's security model rests on one principle, and every mechanism in this
document exists to enforce it.

> **The agent runs as the human it acts for, and does nothing the human has not
> approved.**

This document states that principle precisely, names the mechanisms that
enforce it, and says where its boundaries are. It describes how the software is
designed, not a promise about any particular deployment: Neuman is provided
under the MIT License, as is and without warranty.

## In short

- **The agent never reaches beyond its person, and usually reaches much less.**
  Its tools run on that person's own computer, under their own login, so their
  access is the hard limit. Within it, each person's role decides which tools,
  folders, models and network the agent may use.
- **Neuman's server decides, and never acts.** Every action the agent proposes
  is checked against the person's current permissions, and anything that would
  change something waits for their approval. The server itself has no way into
  anyone's files.
- **Everything is on the record.** Every login, permission change, action and
  result is written to a record in which any later edit shows.
- **Your conversations stay on your hardware** when the model runs on your own
  server.
- **Its boundaries are stated precisely.** The operating system holds the
  person's own access, Neuman holds each role's limits, and approval guards
  anything that cannot be undone. [Where the boundaries are](#where-the-boundaries-are)
  covers each one.

`docs/networking.md` maps every socket, port and connection between the parts;
`docs/decisions.md` holds the design principles and the decision log;
`docs/gotchas.md` holds every way these mechanisms have been observed to fail.

The principle has two halves, enforced in different places. **"Runs as the
human"** is a property of where code executes: tools run on the acting human's
own machine, as that human, so the operating system holds the agent to that
person's reach, and the appliance has no path to their files. **"Nothing
unapproved"** is the decision core's: every proposed call is checked against the
acting user's live policy, which usually grants far less than the person holds,
and a consequential one stops for a verdict.

## The claim, precisely

For a dispatch made on behalf of account `A`:

1. **Execution is on the acting human's machine, as that human.** A dispatch
   runs on the *machine* the branch names — one of the acting account's own
   computers, the one their client logged in from — under their own account, so
   it can never reach more than that account reaches. That is the ceiling, and
   the operating system enforces it. Within it, policy decides what the agent
   may actually use (claims 2, 3 and 5). A machine is a place, not a new reach:
   it is scoped to the account that enrolled it, so naming one cannot widen
   anything (D112).
2. **Every call is gated against live policy.** A tool `A`'s policy does not list
   is not dispatched, whatever caused the model to ask for it. Policy is composed
   fresh for every step, so a revocation lands on the next call.
3. **A consequential call needs a verdict.** Approval is the control that stands
   in front of the human's own reach. A mutating tool stops for a human under the
   default `auto_accept = "safe"`; a `critical` tool stops even under `yolo`. The
   verdict is decided by the core, never by the client.
4. **Identity is Neuman's, and the session is the credential.** The client logs
   in as the acting account (an argon2id check against the account row, over the
   session surface), and every dispatch carries the session it established, so a
   client can only act for the account it logged in as (D16, D17, D58). The
   client's local account is the *executor*, not the identity: the channel never
   asks its name, and it need not match the account on the appliance. The
   password is what binds the two (D67).
5. **A path a tool is given is checked against the account's surfaces, and both
   are on one machine.** A manifest declares which of its arguments are paths;
   the core sends the account's expanded surfaces — expanded against the
   *branch's* machine — with the dispatch; the client resolves each with
   `realpath` and refuses one outside them. A path is a path on one computer: a
   conversation's working directory, its attachments and the directory a drop
   came from are all facts about the machine it runs on, and moving the branch
   clears them rather than reinterpreting them (D112).
6. **Nothing is dispatchable without a manifest the client already holds.** A
   binary in the client's tool directory with no manifest is unreachable however
   executable it is (D5). Enabling a package chooses among tools every client
   already ships; it never sends one (D3, D101).
7. **Every governance event is in a hash chain.** Policy changes, grants,
   logins, dispatches and their results, and an administrator reading another
   account's session, are appended with the previous entry's hash, so an edit to
   the record is detectable rather than merely discouraged.
8. **A task is a standing approval within declared bounds.** A task runs as its
   owner, on that owner's client. Every call an agent step makes is gated
   against `step.envelope ∩ account.policy`, recomposed on every step; the
   envelope can only narrow the owner's reach, and a call outside it is denied.
   A run step is a command the owner wrote word for word, and no agent step may
   write the workspace, a run step's working directory or its program, so what
   a model reads cannot change what a later command runs. A `critical` tool is
   never pre-approved: a task run refuses it rather than waiting for a person
   who is not there (D92, D93, D96).

## What enforces each of these

Each claim, the mechanism that enforces it, and what must stay true for it to
hold.

| Claim | Enforced by | Holds as long as |
|---|---|---|
| Execution as the human | tools run on the client under the account that runs the client; the appliance executes no user-reach tool | no tool runs on the appliance with the user's reach |
| Execution on the named machine | the branch's `machine` resolves to one connection, held by that account's client on that computer; a client refuses a dispatch naming another (`wrong_machine`) | the hub has no fallback: a dispatch names a machine or is refused, and no route or port answers for "whichever client is connected" |
| Capability gate | `needs ⊆ reach` recomposed per step, in the core, before any dispatch | the gate is never cached across a policy change |
| Approval | the inbox in the decision core; the client receives a dispatch that was already approved | only the core decides a verdict |
| Identity | the session the client authenticated with; the channel never asks the client to name an account | the account row is re-read on every request, so a disabled account loses its session (D17) |
| Path surfaces | the client's tool host resolves each declared path with `realpath` and refuses one outside the account's surfaces, which `$HOME` in the policy is expanded into using the home *the client* reports | every manifest declares its path arguments; it does not hold for `shell` or `python` [^surfaces] |
| Manifest requirement | the registry fails closed at load; the client resolves an `exec` only from its own manifest set | every dispatched name is one a manifest describes |
| Packages add no code | a package names tools and a host that every client already ships; enabling it is a baseline change the gate reads, and a tool in a disabled package is refused as `package_not_enabled` at the next step | enabling a package never delivers a program or a manifest to a client (D3, D101) |
| An agent step stays inside its envelope | the authoring check and the run-time `intersect` are the same rules; `policy_for` returns the intersection, resolved to the run's own directory, on every step; a `critical` call is denied before the approval wait | the envelope is never cached across a policy change, and an agent step never falls back to the account's whole policy (D93) |
| A model cannot choose a task's commands | a run step's argv, working directory and timeout are stored at authoring and quoted by the server; inputs reach it only as environment variables; the authoring check refuses an agent step whose write paths overlap the workspace, a run step's working directory or program, and refuses `shell`/`python` in an agent step a run step follows | no agent step can write where a later run step's program or working directory lies, and no input is spliced into a command line (D96) |
| A long command stays on the record | the harness appends `job.detached` before the result that names the job; the core appends `job.settled` when the client reports the end, and records jobs as lost when the connection drops, since the client kills them then | the client reports `detached` for every dispatch that outlives its wait; a descendant that leaves the process group (`setsid`) is beyond any client-side mechanism |
| Audit integrity | SHA-256 chain over every entry, verified on demand and after every restore | verification is never a step a caller can skip |
| Device isolation | `PrivateDevices=yes` on the server and on `neuman-fetch`, relaxed only in the GPU sampler and `neuman-runtime` | no convenience is added to the server itself [^devices] |
| An approval describes its write | the preview is a dispatch of the same tool through the same `edit_plan.plan()` that performs the write | the preview is never derived separately from the write [^preview] |
| Model control | the server names a model to `neuman-runtime`, which matches it against what it was told to run and builds every command line from the shipped presets and typed settings; there is no free-form flag list | no stored document can carry an argument or a path, and the runtime takes only names it was told |
| Weights | only `neuman-fetch` writes `modelsDir`; the server's unit mounts it read-only; a file is renamed into place only when its SHA-256 is the preset's | the server never gains write access to the weights, and a fetch always names a preset rather than a URL |

[^surfaces]: `shell` and `python` reach whatever the user reaches and are not
    checked at all. That is why this is a guardrail and not a boundary, and why
    it is stated as one. The read tools additionally reach the client's own
    `spill/` and `drops/` directories, whatever the surfaces say: one holds only
    this account's dispatch output (D42), the other only files this account's
    person dropped on the chat window (D102), and neither is writable through a
    declared path. `$HOME` means the whole home, dotfiles included (D105), so a
    grant of the token includes `~/.ssh`; `$HOME/<path>` names a folder in it
    and is the narrow spelling (D106). The surfaces refuse what policy does
    not name, never what the operator did not intend.

[^devices]: A status bar or a local model is not worth the server holding a GPU
    handle (D29, D30).

[^preview]: A separately-derived preview becomes a second opinion rather than a
    description of the write (N35, N38).

## The trust tiers

Each tier holds the least authority that lets it do its job, and the boundary
between them is the point: a tier is defined by what it *cannot* do, so a
compromise of one yields the next tier's refusal rather than its authority.

There is no root tier. Every Neuman unit runs as `neuman`, and Neuman has no
privileged helper; systemd is the only thing that starts them as root.

### Unprivileged: the decision core

The server: policy composition, the agent loop, the approval inbox, the audit
chain. Never root, never reads `/etc/shadow`, and reaches only its own state and
its sockets.

This is where the *decisions* are made, which is why it holds no *capabilities*.
It decides that a dispatch may proceed; the human's own machine is what performs
it. The server runs no user-reach tool and has no path to the user's files.

- **Holds:** the database, the model gateway's credentials, and the authority to
  say yes or no.
- **Cannot:** read the host's password file, reach a client's filesystem, touch
  a device, or write the model weights. Authentication is its own database, not
  the host's.
- **If it fell:** policy could be rewritten and dispatches minted. But every
  rewrite is an entry in the hash chain, and a dispatch still runs as the human
  on the human's own machine.

### Single-purpose units: the sampler, the runtime and the fetcher

Three small units each hold one thing the server must not, so the server holds
none of it. Each is reached through a file or a socket that names what to do,
never how.

- **The GPU sampler** reads `nvidia-smi` and writes a JSON file. It holds the
  GPU device, and nothing else.
- **The runtime** (`neuman-runtime`) runs `llama-server` for the local models
  and answers a control socket. It holds the GPU, reads the weights, and has no
  network beyond loopback. It starts only what a shipped preset describes, with
  flags it builds itself (D78).
- **The fetcher** (`neuman-fetch`) downloads model weights. It holds the network
  to the model hub and write access to the weights directory, and no device. A
  request names a preset, never a URL, and a file is put in place only when its
  hash matches the one the preset pins (D79).

A compromise of the sampler or the runtime yields a lie about VRAM or a wrong
reply from a local model, not reach (D29, D30). A compromise of the fetcher
could place bad weights, which yields the same: a model that answers wrongly,
inside the same gate and the same approvals.

### The acting human's machine: the client

The executor, the browser host, the tool package hosts and the UI, on the
user's own computer, running as the user's own local account. This tier is not
confined by Neuman at all and is not meant to be: it *is* the human.

- **Holds:** everything the human already holds.
- **Cannot:** act for an account it did not log in as (the core identifies it
  by the session cookie, not by a uid, because the two ends are different
  machines), or alter its own policy or approval record.
- **Runs on:** Linux. A macOS client is in progress; `docs/platforms.md` has
  the support matrix, the per-host paths and what each platform weakens.
- **If it fell:** that human's own reach. A compromised client can lie about what
  it did; the record says so, and says it is a claim [^reported].

[^reported]: This is why every dispatch entry records the machine it ran on
    (D112). A client effect is *reported*: the appliance records a claim from a
    machine it does not control, in an order that cannot be altered afterwards.
    The machine's *name* is part of that claim — a label the account's own client
    chose, scoped to that account, so it can misdescribe which of the person's
    computers ran something and cannot reach past them.

### Untrusted: the browser, model output, and tool output

Every byte validated. CSRF and Origin re-checked per unsafe request.

The tier exists because all of these are *input from somewhere else*, regardless
of how they arrive or how plausible they look. **Tool output is inert data**,
and the reason is not that the model ignores injected instructions: it is that
every dispatch is re-gated against the acting user's live policy whatever caused
the model to ask for it. Injected content saying "run the shell tool" produces a
refusal in the audit chain, and if the policy does grant the tool, it produces
an approval card. The injection moved nothing.

That property is tested by putting hostile text in tool output, letting the
model obey it, and asserting the gate refuses anyway, and the test was verified
by neutering the gate and the approval check and watching both fail (D23).
Past the approval, the operating system enforces only the person's own reach.
That is why the surfaces check exists, and why it is described as a guardrail
rather than a boundary.

## Where the boundaries are

Every security model has a scope, and Neuman's is drawn deliberately. Each item
below says where a boundary sits, what holds it, and what to use when you need
more.

- **The person's reach is enforced by the operating system; role limits are
  enforced by Neuman.** Roles narrow the agent well below its person: fewer
  tools, fewer folders, no network, approval before acting. Neuman's gate and
  the client's path check hold those limits. `shell` and `python` work with the
  person's full file access, so a role that needs strict folder limits grants
  the file tools instead. For a boundary narrower than the person that the
  kernel itself enforces, run the agent's client under a dedicated, restricted
  Unix account; the agent then reaches only what that account reaches (D92).
- **Prompts guide; policy decides.** Three sources of text shape a turn: the
  operator's prompt, the account's own instructions, and a working directory's
  `AGENTS.md`. None of them grants anything. A request for a path policy does
  not grant is simply refused, because the gate checks every dispatch whatever
  the text says (D65).
- **Network access follows the person's machine.** Neuman decides whether tools
  that use the network are offered at all. Once granted, `shell` and `python`
  use the person's own network, as their terminal does, so approval is the
  control over what leaves (N146). The appliance's own traffic, to model
  providers and to clients, is separately enforced (`docs/networking.md`).
- **Approval guards what cannot be undone.** A delete is permanent, which is why
  actions that change things ask first under `safe`. `yolo` is an operator's
  explicit choice not to be asked.
- **Long-running commands become tracked background jobs.** Approval covers a
  command's words, not its duration. A `shell`, `python` or `git` call still
  running at its wait becomes a background job that may run for up to an hour
  (D98). It is recorded when it detaches and when it ends, and the model, the
  person or the client can stop it. Jobs are tracked by their process group: a
  process that deliberately detaches itself (`setsid`) leaves that tracking, and
  if the client itself is killed outright, its jobs run on until they finish.
- **The record holds what each client reports.** A client's reported effects
  are chained in an order that cannot be altered afterwards, together with the
  client that reported them. The record is of the report; the effect itself
  happened on a machine the appliance does not control.
- **Administrators are accountable.** An administrator can change any grant,
  including their own, and every change is written to the tamper-evident record.
- **Neuman governs actions, not intentions.** It does not constrain what a model
  tries. It decides what happens when it tries.
- **Tasks run within their owner's reach.** An agent step's envelope narrows the
  agent the way a role does. A **run step** runs a program its owner wrote,
  word for word, and that program reaches what its owner reaches; a script that
  reads a file an agent step wrote treats it as data (D96).
- **Scheduled tasks run while their owner's client is online.** A due run waits
  for the client; after the catch-up window it is recorded as `missed` (D94).
- **Task results are reported, and judged by whoever reads them.** A step's
  status is its exit code or how its agent finished, and the run record lists
  which declared outputs exist. A checking step the owner writes, or a person,
  decides whether they are right.
- **Browsing uses the client's own browser.** A Neuman-owned profile is the
  default; copying the person's own profile, with their signed-in sessions, is
  an explicit opt-in (D31).
- **A package's application runs as the person.** `blender` runs Python the
  model writes inside the person's own Blender, with the reach of the `python`
  tool and an approval in front of it. Each change is one step on Blender's undo
  stack, so Ctrl-Z takes back a change to the scene; files written, exported or
  saved are ordinary files. A scene's embedded scripts never run, and its
  contents reach the model as data (D100). Blender stays open when the client
  stops, because it is the person's window (D101).
- **A working directory focuses the agent; it never widens it.** A conversation
  may point the agent at a folder inside its reach, never outside it.
- **A machine is a place, not a boundary of its own.** Every machine an account
  can name is one its own client logged in from, so choosing between them is
  choosing among the person's own computers and grants nothing. What it decides
  is which filesystem a path means — which is why a branch names one, why a path
  and an attachment do not survive a move, and why the record names the machine
  on every dispatch. Policy may narrow a role to a set of machines (D112); it
  cannot reach one the account never enrolled, because such a machine has no
  connection to dispatch down.
- **A fork branches the conversation, not the world.** What happened before the
  fork (files written, approvals given) stays done, and the interface says so.
- **An approved edit is checked again when it is written.** `fs_edit` refuses if
  the text it was changing has moved since the preview. `fs_write` replaces a
  file whole, including any change made while the person was deciding.
- **Local models keep conversations on site; hosted ones do not.** A local model
  is served on loopback by `neuman-runtime`, so the conversation never leaves
  the machine. Anything sent to a hosted provider goes to that provider.
- **Two facts about the server are public.** `/healthz` and `/versions` answer
  without a session, because the callers that need them — a probe, and `doctor`
  on a machine whose session may be exactly what is broken — cannot present
  one. They name the product, its release, the dispatch shapes it speaks and the
  oldest client it accepts; never the bind, the build id, the schema or an
  account. Anyone who can reach the port therefore learns which release the
  appliance runs, which is the cost of an update story that works without an
  administrator carrying messages between machines (D111).
- **Cloud file connectors are deliberately out of scope.** They would be a
  separate, clearly labelled tier (API scopes plus audit), not part of this one.

## How the claim is kept honest

Not by review. By tests whose interesting assertions are attempts to break it,
run on booted machines:

- `checks.vm-appliance`: the composition, including the first-admin bootstrap,
  a real login and the `/client` handshake through the production path.
- `checks.vm-restart`: the appliance with data in it, restarting.
- `checks.vm-gateway`: the key reaching a request that actually left the
  process.
- `checks.vm-runtime`: a local model fetched, verified, added and served, and
  what only a booted machine can say: which unit can write the weights, which
  holds the GPU, and that the model server offers nothing a preset did not name.
- `checks.vm-iso`: the demonstration image's own configuration, booted, with
  the client serving the UI beside the core.

Two rules earned the hard way and recorded in full in `docs/gotchas.md`:

**Absence of failure is not evidence.** A check must be shown failing before its
passing means anything. Every fix in this repository was verified by
deliberately breaking the thing it guards.

**A fixture that constructs the thing under test proves only that the fixture
agrees with itself.** N38 was an adapter whose only exercise was a fake, and it
passed every test there was while being broken in production.
