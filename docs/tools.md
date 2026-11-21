# Tools

A tool is how the agent does anything at all: read a file, run a command, open
a web page. Each tool declares what it needs; Neuman decides whether the acting
person's role allows it and whether to ask them first; and the tool runs on
that person's own machine, as them.

[`security-model.md`](security-model.md) states the security model this serves.
This document says what the tools let you do, what each one is, and the
mechanism between the model proposing a call and your machine running it.

## What you can do with them

| You want to | Tools |
|---|---|
| Understand an unfamiliar codebase or document set | `fs_search`, `fs_read`, `fs_list`. None of them changes anything, so under `safe` approval they never ask |
| Change files, including several at once | `fs_edit`, `fs_write`, with the diff shown on the approval card before anything is written |
| Hand the agent a file | Drop it on the chat window; the agent reads it with `fs_read`, or looks at it with `fs_image` if it is a picture |
| Run the project's own tooling | `shell`, `python`, in the directory's own development environment |
| Do arithmetic or data work | `python`, with the project's packages importable |
| Run a long build or analysis without waiting on it | `shell` or `python`; a command still running at its wait becomes a background job the agent follows with `job_output` and either of you can stop |
| Look at a screenshot, diagram or scan | `fs_image`, on a model that can see images |
| Read a web page | `web_fetch`, on your own network |
| Clone a repository to read it | `git` |
| Use a real browser | `browse`, which drives a Chromium on your own machine |
| Build a 3D model from a description | `blender` to change the scene, `blender_scene` and `blender_view` to check it, in a Blender you watch and work in |
| Find something from an earlier conversation | `session_query` |
| Run work on a schedule, unattended | a [task](tasks.md): a recipe of commands and agent steps, run by your client inside limits you set |

Where each tool's boundaries sit, and where a person's approval is the
safeguard, is in the security model's
[Where the boundaries are](security-model.md#where-the-boundaries-are).

## The shipped tools

| Tool | Kind | Needs | Asks under `safe` | What it is |
|---|---|---|---|---|
| `fs_read` | client | read | no | Read a text file; names the format of one that is not text |
| `fs_list` | client | read | no | List a directory, grouping one over 200 entries |
| `fs_search` | client | read | no | Regex over file contents or names, newest-modified first |
| `fs_image` | client | read | no | Hand an image to a vision-capable model |
| `fs_write` | client | read, write | yes | Write a whole file |
| `fs_edit` | client | read, write | yes | Replace exact regions; all edits apply or none do |
| `shell` | client | read, write | yes | Run a command in the user's own environment |
| `python` | client | read, write | yes | Run Python in the directory's own development environment |
| `web_fetch` | client | **network** | no | Fetch one URL, as text |
| `git` | client | **network**, read, write | yes | Run the real git, to clone a repository and read it on disk |
| `browse` | client | read, write | yes | Drive the user's Chromium over the Chrome DevTools Protocol, one tab reused |
| `blender` | client | read, write | yes | Run Python in the user's live Blender; new, open, save, export, render |
| `blender_scene` | client | read | no | Describe the open Blender scene as text |
| `blender_view` | client | read | no | Look at the Blender scene from a named view, as a picture |
| `job_output` | client (daemon) | none | no | Follow a background job: wait for it, and see the output not yet shown |
| `job_kill` | client (daemon) | none | no | Stop a background job and its process tree |
| `session_query` | server-native | none | no | Read your own sessions and their branches: what was said, which tools ran, why a turn ended |
| `env_probe` | client | read | no | Report a directory's development environment. **Granted to no role** (below) |

No shipped tool is `critical`, so under `yolo` nothing asks; see
[Approval](#approval-what-asks-and-when).

Four things about this table explain its shape:

- **`git` is the only tool that combines `network` with `write`.** `web_fetch`
  reaches the network and writes nothing, `shell` reads and writes, and `git` is
  the one place the two meet, so fetching a repository is one deliberate grant
  rather than a general "download this to disk".
- **`needs` decides whether a tool is offered, not what it can do once it
  runs.** A tool that omits `network` still runs on a machine that has the
  user's network. What the gate decides is whether the tool is *offered* to an
  account whose policy lacks the matching reach.
- **The Blender tools are one capability split three ways by what asks.**
  `blender` changes the scene and asks under `safe`; `blender_scene` and
  `blender_view` only look, so the look that follows every change does not
  prompt. All three talk to the Blender the client starts on the user's machine
  when one of them is first called, the way `browse` talks to Chromium (D100).
  Each `blender` call is one step on Blender's undo stack, so the person can
  Ctrl-Z the agent's edit. They are a package (below), so an appliance has them
  only once an administrator enables it.
- **`env_probe` is installed and granted to nobody.** The chat window and the
  harness use it to report a working directory's development environment; a
  model wanting the same answer has `python(action="describe")`, which also
  lists what can be imported. It is marked `diagnostic`, so it never appears in
  the tool chooser either, where an ungranted row would invite someone to grant
  a capability that is not one (D33).

## Packages

Most tools are **built in**: every appliance has them, and a role decides who
may use them. A tool useful to some deployments and noise to the rest ships in a
**package** instead, a file in `tools/packages/` naming the tools it holds, and
an administrator turns it on for the whole appliance from the **Tools** page of
the administration interface (D101). Blender is the first:

```toml
[package]
id = "blender"                # equals the file's name; what the baseline lists
title = "Blender"
summary = "Build and look at 3D models in a Blender the user watches and works in"
description = """..."""       # for the administrator deciding whether to enable it
publisher = "neuman"          # or "community": both reviewed into the release
tools = ["blender", "blender_scene", "blender_view"]

[package.requires]            # a program the person installs themselves
program = "blender"           # looked for on the PATH the client runs with
name = "Blender"
url = "https://www.blender.org/download/"

[package.host]                # a companion the client starts on demand
exec = "neuman-blender-host"  # resolved like a manifest's `exec`, never a path
```

A package moves through three states, the way a model preset does:

| State | Where it is | Who decides |
|---|---|---|
| **Shipped** | the release: the package file, its tools' manifests and programs, on the server and every client | Neuman, by review |
| **Enabled** | the baseline's `packages` | an administrator, on the Tools page |
| **Granted** | a role's `tools`, as for any tool | an administrator, on the role |

**Enabling downloads nothing and grants nothing.** Every package's code is
already on every client, so the server only chooses among programs a client
already trusts (D3). Granted tools in a disabled package are refused as
`package_not_enabled` at the next step, and the roles that name them keep the
names, so enabling the package again restores what they said.

**What a package needs on a person's machine is theirs to install.** `requires`
names it. The client checks on every dispatch, so installing it needs no
restart, and without it refuses with `missing_program`: *"blender_view needs
Blender, which is not installed on this machine … Install Blender from
https://www.blender.org/download/ and try again."* The model tells the person.

**A host is started when it is first needed.** The first time one of the
package's tools is dispatched and nothing answers on
`$XDG_RUNTIME_DIR/neuman-<id>.sock`, the client starts the host in its own
session and leaves it running, because it is the person's window. Its output
goes to the client's `hosts/<id>.log`. The host and the package's tools are told
the socket in one variable, `NEUMAN_HOST_SOCKET`, and the host is told the
program the client found in `NEUMAN_HOST_PROGRAM`.

## Approval: what asks, and when

Two manifest flags decide whether a person is asked. `mutating` means the call
can change something in reach. `critical` means always ask, even under `yolo`.
The account's `auto_accept` then decides what asks:

| Mode | Asks about |
|---|---|
| `never` | everything, including reads |
| `safe` (the default) | anything `mutating` or `critical` |
| `yolo` | `critical` only |

`yolo` still asks about a critical action: it is a statement about convenience,
not consent to whatever the tool is grave enough to carry the flag for. A role
can also cap a user with `auto_accept_max`, and the clamped value is what the
loop reads, so a person's own setting can never exceed the role's ceiling.

**No shipped tool is `critical`.** `shell` and `python` are the administrator's
`yolo` to make: `critical = false` records the decision that `yolo` runs them
without asking, and a delete on the client is a real delete.

**A task run does not consult `auto_accept`.** A task is a standing approval
(D92): an agent step's envelope is the consent, so every call the gate admits
inside it is pre-approved, and a run step is a `shell` dispatch of a command the
owner wrote, pre-approved for the same reason (D96). The one exception is
`critical`, which is refused before the approval wait rather than left to time
out: a task has no one to ask, and "critical always asks" must not become
"critical never answers" (D93). The authoring check refuses a critical tool in
an envelope as well, so the run-time refusal is the second layer.

**Approval is per tool, not per command.** `shell` is `mutating` because some
commands change things, so under `safe` `ls` and `rm -rf` are the same
decision. Marking it non-mutating to avoid the prompts would skip approval for
every destructive command, the exact class of damage the flags exist to catch
(D23), so it errs on the side of asking. `python` feels this most, since
arithmetic is a frequent reason to reach for it. The fix is a predicate over
parsed arguments evaluated at gate time; it needs a decision entry and a schema
field, and it is not built yet.

## Background jobs

`shell`, `python` and `git` wait two, five and two minutes respectively, and may
live an hour (D98). A call still running at its wait becomes a **background
job** when four things hold: the tool's lifetime is longer than its wait, the
model holds `job_output` (otherwise it could never look at the job again), the
dispatch belongs to a session (a job is found by its session), and the client
has a free slot, of which there are four. Otherwise it is stopped at the wait,
and the result says which of the two happened.

The model is handed the output so far and one sentence: the job's id, when it
will be stopped, and that `job_output` follows it and `job_kill` stops it. The
process keeps running, its output keeps going to the spill files, and the model
gets on with the turn. `job_output` waits up to five minutes for the job to end
by default and then answers with a status line and only the output the model
has not yet been shown: a few lines while the job runs, more once it has ended,
and the spill file's path only when something was left out. A job that ends
while nothing is waiting on it is told to the session as a one-line notice at
its next step.

That shape is about context. Every byte a look returns is re-sent with every
later request of the session, so returning the whole log would make a long
build cost its log once per look. What a look returns is the tail the model has
not seen, and waiting is the default because one look that returns at the end
is one result, where polling is one per poll.

A job belongs to the session that started it, and a look or a stop finds only
that session's jobs. It is recorded twice: `job.detached` before the model is
told, and `job.settled` with how it ended. It ends with the client's
connection, and a task's agent step stops any its session left running when the
step ends, so the step after it never starts beside a build still writing.

The person is not limited to asking the model. The message box carries a chip
per running job with a ✕ that stops it; the list is served by the client itself,
which holds the jobs (`docs/networking.md`, D98).

A task's run step is the other side of the same split. It waits its whole
`timeout_s`, up to the tool's hour, and is never a job: the core asks for a wait
longer than the manifest's own, which only it may do (D96).

## How a call happens

The loop is `model call → gate → dispatch → observe`, and policy is **re-read
every step**, not once per session or once per turn. A revocation lands on the
next tool call.

**1. Advertise.** The model is told only the tools that pass the gate right now
(`gate.grantable`), intersected with the session's own tool selection if the
person set one (D50). The advertisement and the gate read the same rule, because
advertising a tool the gate would deny wastes a turn and teaches the model to
propose things it cannot have.

The selection can only subtract: a name it carries that policy does not grant
is dropped here, so a session's selection is never a grant. A deselected tool is
*absent* rather than present-and-forbidden, because mentioning it would cost
context on every turn and the remedy belongs to the person. An empty selection
says so explicitly, since a bare "(none)" reads to the model as a broken
appliance rather than as something a person asked for.

> **This is why a misconfiguration looks like absence.** A tool you granted but
> whose `needs` your policy does not satisfy is not refused; it is never
> mentioned. "I don't have that tool" is the symptom of "not granted", of
> "granted without the matching reach", and of "switched off for this
> conversation". The last of these a person can see and fix in the tool chooser
> on the message box's status line.

**2. Gate.** `needs ⊆ reach`, where reach is *derived* from policy rather than
granted by name:

```python
if self.files_read:  kinds.add("read")
if self.files_write: kinds.add("write")
if self.network == "any":
    kinds.add("network")
```

So a user with no writable path has no write reach, whatever a role fragment
claims. The order of checks decides what the refusal *says*: a tool that was
never granted reports `tool_not_granted` even if it would also fail reach,
because that is the fact an administrator can act on. The reason codes
(`tool_not_granted`, `package_not_enabled`, `unknown_tool`, `insufficient_reach`,
`tool_not_selected`) are stable strings that reach the audit chain, so they can
be searched for.

A bug in the gate can deny service. **It cannot widen the human's reach**,
because the tool runs as that human and the gate is not what makes it so.

**3. Approve.** If the flags and `auto_accept` require it, the call waits for a
verdict. No approver, no verdict, or no budget all end as denials with a logged
reason, never as an unguarded dispatch.

For a write, the harness first resolves it through the same tool with
`preview: true` and sends the diff to the approval card: the person approves the
change they were shown, and `edit_plan.plan()` is the one function both paths
use. What keeps the preview from writing is the tool honouring `preview`, and
that is stated rather than implied.

**4. Check the paths.** A manifest's `paths` names the arguments that are
filesystem paths. The core sends the account's expanded read and write surfaces
with the dispatch, and the client's tool host resolves each declared argument
with `realpath`, symlinks included, refusing one outside them as
`outside_surface` (D57). The surfaces travel with every dispatch rather than
once at connect, so a revocation lands on the next call. This is a guardrail
against a confused or injected model, not a kernel boundary: `shell` and
`python` are not checked, and neither is a manifest that declares no paths.

**5. Dispatch.** The client receives the dispatch over its one authenticated
WebSocket to the core (`/client`, D56), wherever the client is. It resolves the
tool *name* against its own manifest set and runs the program as the user, in
the conversation's working directory, with the user's environment.

A tool in a package gets two more checks first. If the package `requires` a
program and the client's PATH has none, the dispatch is refused with
`missing_program` and a sentence naming the program and where to get it; the
model passes that on. If the package has a `host` and nothing answers on its
socket, the client starts the host and the tool waits for it to come up.

**6. Observe.** The client streams `started`, stdout and stderr with sequence
numbers; a gap fails the dispatch, because truncated output presented as
complete is the one thing a reader of the stream must never pass on (D26). A
tool that persists its output forwards a 16 KiB preview, and past it the whole
stream is written to a file in the client's `spill/` directory, up to
`max_output_bytes`; the model reads it back with `fs_read` or `fs_search`, which
reach that directory whatever the account's surfaces say. A
`persist_output = false` tool is forwarded inline, up to `max_output_bytes`
(D42). At `wait_ms` the client either stops the process group or, for a tool
whose `lifetime_ms` is longer and a session that can follow it, keeps it running
as a background job. A dispatch that never reports `started` within a few
seconds is an error in its own right (D15).

Everything lands in the session log, which is authoritative: the model's
history is re-derived from it each step, so a summary or a live policy change
can never leave the loop holding stale state. A tool's output reaches the log
**verbatim, with two exceptions**, and both record themselves in the row's
`redactions` so an altered payload says so:

- a string that *equals* a known secret is replaced;
- NUL characters are substituted, because the log's `payload` is `JSONB` and
  Postgres cannot store a NUL in text at all.

The second is worth knowing before you write a tool that might emit binary.
`fs_image` base64-encodes its output for exactly this reason.

## Your own environment

A `shell` dispatch runs with the person's own `PATH`, so `ls`, `cat`, `grep`,
`find`, `git`, `tar` and the rest are there: it is their terminal, reached
through the agent. `python` runs in the working directory's own development
environment when it has one (its `.direnv`), and with the standard library when
it does not.

The dedicated tools are still worth preferring where they fit. `fs_list`,
`fs_read` and `fs_search` answer in a shape a pipeline has to be parsed for, and
because they change nothing they do not ask under `safe`. That is the reason
they exist, so it is a decision (D59) rather than a preference.

Deletion is real: `rm` deletes, and the approval given to the call is what
stood between it and the file.

## Tools do not check permissions

A tool does no path checking of its own. The client's tool host checks the
paths a manifest declares before the tool runs, so by the time the tool starts,
the question of reach has been answered once; answering it again inside every
file tool would be a second implementation that eventually disagrees with the
first.

What a tool *does* own is **shape**. If the manifest declares `offset` and
`limit`, they have to mean something, and a file that is not valid UTF-8 has to
fail as a tool error rather than a traceback. A page that is not the whole file
ends with `[showing lines X-Y of N; use offset=Z to continue]`, because a
fragment that does not announce itself is one a model will mistake for the
whole.

A **single line larger than a page** (a minified page, a document spilled to one
line) cannot be paged by line at all. `fs_read` returns its first bytes with the
line's real size in the notice, and `fs_search` prints a hit inside such a line
as a bounded window around the hit, with the cut ends marked (N181).

`shell` does no command filtering, and should not. A blocklist of dangerous
commands cannot be complete, the shell defeats any list with `command`, a
substitution or a script on PATH, and a filter here would mainly create the
impression that something else is enforcing a boundary. Approval is the control.

Note on arguments: the registry checks every manifest's `parameters` schema at
load, so a malformed *schema* fails the registry. The server does not then
validate each call's arguments against it; each tool validates its own input
and returns a tool error.

## Prompt injection is routine input

The agent's whole purpose is reading files other people wrote, so instructions
hidden in them are expected input rather than an anomaly (D23). Containment is
structural, not textual:

- injected text can at most cause the model to **propose** a tool call;
- every proposed call is re-gated against the acting user's **live** policy;
- a consequential call stops for a **verdict**, decided by the core.

For the file tools, the path check on the client is a further guardrail. For
`shell` and `python`, approval is the control, which is why they ask under
`safe`.

The delimiters around tool output and the warning in the system prompt reduce
how often a model is swayed; **they are not the boundary and must never be
described as one.** `web_fetch` is the likeliest carrier, which is why its
description tells the model the content is untrusted data to report rather than
instructions to follow.

## Trying every tool

One message can ask the agent to call every built-in tool in turn, which is the
quickest way to see what each one does and how its result looks in the chat
window. Each step produces a different kind of result: a listing, search hits
grouped by file, a diff, a command's output, a screenshot.

**Before you send it**, give your account a role that grants the tools. Create
one under **Roles** in the administration page:

```json
"tour": {
  "tools": [
    "fs_read", "fs_list", "fs_search", "fs_image",
    "fs_write", "fs_edit",
    "shell", "python",
    "web_fetch", "browse",
    "session_query"
  ],
  "files_read":  ["$HOME/workspace"],
  "files_write": ["$HOME/workspace"],
  "network": "any",
  "auto_accept": "safe",
  "models": ["<a vision-capable model>"]
}
```

`$HOME/workspace` expands to `~/workspace` on the machine that runs the tool —
the token is the client's home, so the same role works on Linux and macOS — and
nothing outside that folder. So make a folder such as `~/workspace/tools-tour`
and choose it as the working directory in the message box. `network = "any"` is
what `web_fetch` needs to be offered, and `fs_image` needs a model that can see
images. With `auto_accept = "safe"`, the steps that change files stop for your
approval, so you see the approval cards too; switch to `yolo` and repeat it to
watch the same cards decide themselves.

**The message:**

> I want to exercise every tool you have, in order, as a smoke test. Work inside
> the working directory. Do exactly these steps, **one tool call at a time**,
> waiting for each result before starting the next. Do not skip a step; if one
> fails, say so in one line and continue.
>
> 1. `fs_list` the working directory. Say how many entries it has.
> 2. `fs_write` a file `calc.py` defining `circle_area(r)` and
>    `circle_circumference(r)` using `math`.
> 3. `fs_read` `calc.py` back and confirm it matches what you wrote.
> 4. `fs_edit` `calc.py` to add a module docstring and an
>    `if __name__ == "__main__":` block that prints the area for radius 2.
> 5. `fs_search` for the literal `def ` under the working directory and report
>    every hit with its file and line.
> 6. `shell` a one-liner that prints a greeting and exits 0.
> 7. `python` run `calc.py` and report its output.
> 8. `web_fetch` `https://example.com` and quote the first heading.
> 9. `browse` a `search` for `PETSc` and report the top three result titles.
> 10. `browse` `open` the first of those results, then `read` it, then `shot` a
>     screenshot to `shot.png` in the working directory.
> 11. `fs_image` on `shot.png` and describe what you actually see in it.
> 12. `session_query` with `action = "list"`, then `kinds` for this branch, and
>     report the event counts.
> 13. Finally, answer in plain prose: one line per tool saying what it did.

## For builders

### The two kinds

`kind` must be true of where the tool actually executes (D13). It is a
declaration about mechanism, not a category for people.

**`client`**: an executable the acting person's machine runs, as that person
(D55). It needs an `exec`, which names a program in the client's own trusted
tool directory. The server gates the call, asks for approval where required,
and sends it; the client runs it and streams the output back.

**`server-native`**: answered by the decision core before any dispatch exists.
`session_query` is the only one: it touches no filesystem and never leaves the
core, which is why its `needs` is empty. The loop dispatches on kind, and a
manifest claiming `server-native` with no registered handler fails closed
rather than falling through to a client. There is no third kind.

**`daemon = true`** marks a `client` tool the client daemon answers itself, with
no process: `job_output` and `job_kill`, which read and stop the background jobs
the daemon holds (D98). A subprocess could not see those, so there is no program
to name, and the schema forbids an `exec` beside it. It is still a client tool
in D13's sense: it executes on the acting person's machine, against state that
exists only there.

### A manifest

The manifest is the single statement of what a tool needs. Both the server's
registry and the client's own manifest set validate against
`contracts/schemas/tool-manifest.schema.json`, and CI proves the two accept the
same manifests (D2, D3).

```toml
[tool]
name = "fs_read"              # must equal the directory name
kind = "client"
exec = "fs_read"              # a *name*, resolved from the client's own
                              # trusted bin directory, never a path (D3)
summary = "Read a text file"  # one short line, for the person's tool chooser
description = """..."""       # what the model is told
wait_ms = 30000               # how long a call blocks
lifetime_ms = 30000           # how long its process may live; longer than the
                              # wait means it can become a background job (D98)
max_output_bytes = 1048576
diagnostic = false            # true = used by the appliance, hidden in the UI
returns_image = false         # true = stdout is a picture the model sees (D28)
mutating = false
critical = false
needs = ["read"]              # the reach the user must hold
paths = ["path"]              # argument names the client checks against surfaces

[tool.parameters]             # JSON Schema, advertised to the model
type = "object"
required = ["path"]
```

`exec` is required for `client` and **forbidden** for `server-native`; the
schema enforces that with `if`/`then`, so a manifest cannot claim to be answered
by the core and also name a program.

**`summary` and `description` are for different readers.** `description` is the
model's instructions, and its length is a feature: every caveat and worked
argument shape is useful to the model. `summary` is the line a *person* reads in
the tool chooser (D50). The schema caps it at 80 characters so it cannot grow
into a second description. It is optional in the schema, so the loader tolerates
a tool without one and the client clips the long text instead, but
`tests/test_tools.py` holds the shipped set to having one.

**`diagnostic` marks a tool a person never picks**, so the tool chooser leaves
it out. `env_probe` is the only one. The flag is presentation only: the gate
decides a diagnostic's dispatch exactly as any other tool's, which is why
`tests/test_tools.py` holds the shipped set to a named allowlist and requires
every tool on it to be granted to nobody. A granted diagnostic *is* listed,
because a live capability with nothing on screen saying it exists is the one
worse outcome.

**`returns_image` marks a tool whose stdout is a picture**: one line of
`<media type> <base64>` that the harness turns into an image the model sees
rather than text it reads (D28). `fs_image` reads one from disk and
`blender_view` draws one from a live viewport. It is declared rather than
recognised by name, because a name list in the harness is the list a second
picture tool would be missing from (D100). A model without vision is refused
such a tool before dispatch, and the schema requires `persist_output = false`
beside it: the core transforms that stdout, so a spilled preview would stand for
nothing.

Loading **fails closed as a whole**: one bad manifest fails the entire registry
rather than yielding a partial tool set. A silently missing tool is a confusing
outage; a silently present malformed one is worse.

### Adding a tool

1. `tools/<name>/manifest.toml`, with `name` equal to the directory.
2. The executable, for `client` tools, in `tools/bin/`, named in `exec`. A
   module it imports goes in `tools/lib/`. Note the `python` precedent: its
   `exec` is `neuman-python`, not `python`, because this package may end up on
   a PATH, where a file called `python` would shadow the interpreter for
   everything that searches it.
3. Declare `needs` honestly. It is the grant requirement a person sees:
   declaring less than the tool uses produces a tool that is offered where it
   cannot work.
4. Set `mutating` and `critical` to fail closed. If some calls change things,
   the tool is mutating.
5. Declare `paths` for any argument that is a filesystem path, so the client
   checks it.
6. Grant it in a role. Reach is derived: a tool declaring `needs = ["network"]`
   also needs `policy.network = "any"`, or it is not offered. A tool not every
   deployment wants goes in a package instead of the built-in set: add it to a
   file in `tools/packages/`, with `requires` for any program the person must
   install and `host` for a companion the client should start.
7. Test it against the real transport. Two of the most serious bugs in this
   tree, a capability that reached nothing anywhere and a model client that
   never sent its API key, passed every test that existed, because the only
   thing exercising the code was a fake that agreed with it.
8. Where the tool restricts something, say in a comment what the restriction
   costs the person using it, not only why it exists.

## Where Neuman keeps things

Two roots, and the split is the point: one place for what belongs to the
appliance, one for what belongs to a person. Nothing Neuman writes lands outside
these except the work you asked for.

**The appliance**: `/var/lib/neuman`, owned by `neuman`, `0750`, outside every
user's reach.

| Path | What | Mode |
|---|---|---|
| `state/` | Server state; `state/policies/` holds the per-account overrides | `0750` |
| `state/secrets/` | Provider keys entered in the administration page, one `0600` file each, never in a backup (D77) | `0700` |
| `backups/` | `pg_dump` output: the whole database, including the audit chain. The most sensitive artifact here, and the one most likely to be copied somewhere careless | `0700` |
| `models/` | Model weights, written only by `neuman-fetch`, with `.verified/` recording each file it hashed. Beside the appliance's other state rather than in a home directory, because they belong to the appliance: a second account uses the same model, and deleting a user should not take the weights (N52) | `0770` |
| `state/runtime.json` | What `neuman-runtime` should run: the local models the database names, written by the server on every change (D78) | `0640` |
| `/var/log/neuman/audit.jsonl` | The audit chain's append-only mirror | `0750` |
| `/run/neuman/` | Sockets, the GPU sample. Gone on reboot | varies |

`/etc` holds what the module *declares* and the server only reads; `/var/lib`
holds what the server owns and manages. Policy, the catalog and the prompt are
neither: they are rows in Postgres (D68).

**A person**: the client state directory, `~/.local/share/neuman/` on Linux.

| Path | What |
|---|---|
| `client/spill/` | Output a dispatch produced that outgrew its preview. The model reads it back by name, whatever its surfaces |
| `client/drops/` | Files the person dropped on the chat window, which the agent may read whatever its surfaces (D102) |
| `client/hosts/` | One log per tool package host the client started, such as `blender.log`: where to look when a host opens and then stops |
| `browser/` | The Chromium profile `neuman-browser` uses when its profile is `own`. Deliberately *not* `~/.config/chromium`, so nothing Neuman drives touches the profile your own browsing depends on |

**In your own directories**, Neuman writes the work you asked for, and briefly
a `.neuman-*` file next to the target. That is the atomic write: a temporary file
beside the destination, then `rename`. It has to be the same directory because
`rename` is only atomic within one filesystem. Normally one exists for
microseconds; a leftover means a rename failed, and the cleanup is best-effort:
the file is emptied rather than left looking like content (N109).

## See also

- [`security-model.md`](security-model.md): the security model and its
  boundaries
- [`networking.md`](networking.md): every link a dispatch uses
- [`context.md`](context.md): what happens when tool output fills the context
- [`decisions.md`](decisions.md): D2, D3, D13, D23, D26, D28, D31, D42, D50,
  D55, D56, D57, D59, D98, D100, D101, D102
- [`gotchas.md`](gotchas.md): every observed failure. Read before changing a
  boundary
