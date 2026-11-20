# tools

One directory per tool holding its manifest, plus `bin/` holding the entry
points and `lib/` the modules they import. A tool runs on the acting human's machine, as that human (SPEC
section 2), so it reaches exactly what they reach. It does no path checking of
its own: a path it should not see is a path the human could not open either, and
a check inside the tool would be a second permission system that eventually
disagrees with the first.

## Public surface

- **`<name>/manifest.toml`** — validated against
  `contracts/schemas/tool-manifest.schema.json`. The directory name is the tool
  name; the loader rejects a mismatch, because the client resolves tools by
  directory and the two installations must agree (`docs/decisions.md` D3).
- **`packages.<system>.neuman-registry`** — the manifests as one store path,
  which is what the server reads at startup to gate and advertise the set.
- **`packages.<system>.neuman-tools`** — the executables, as a *separate*
  package. Separate because they are a different trust position (D3): a manifest
  is read by the server, while the binary a name resolves to is the *client's*
  own trusted configuration and never comes from a dispatch.

## Private surface

Nothing. A manifest is the entire interface; there is no per-tool
configuration, no hidden capability, and no way to be dispatchable
without one (D5).

## The set

| Tool | Needs | Mutating | Critical |
|---|---|---|---|
| `fs_read` | read | no | no |
| `fs_list` | read | no | no |
| `fs_image` | read | no | no |
| `fs_search` | read | no | no |
| `fs_write` | read, write | yes | no |
| `fs_edit` | read, write | yes | no |
| `shell` | read, write | yes | no |
| `python` | read, write | yes | no |
| `web_fetch` | network | no | no |
| `git` | network, read, write | yes | no |
| `browse` | read, write | yes | no |
| `blender` | read, write | yes | no |
| `blender_scene` | read | no | no |
| `blender_view` | read | no | no |
| `job_output` | — | no | no |
| `job_kill` | — | no | no |
| `session_query` | — | no | no |

The three `blender` rows are the Blender package: shipped like the rest,
present only on an appliance that has enabled it (below).

## Packages

`packages/<id>.toml` groups tools an operator enables together for the whole
appliance, validated against `contracts/schemas/tool-package.schema.json`
(D101). A tool no package names is built in. A package may declare a program
the person installs (`requires`) — never downloaded, and refused by name when
missing — and a `host` the client starts on demand, whose `exec` lives in
`bin/` like any tool's and is named by no manifest, so no dispatch can run it.
`blender` is the one there is: its host is `bin/blender_host.py`, installed as
`neuman-blender-host`, and the add-on it loads into Blender is
`lib/blender_addon.py`.

## What the fields mean

**`needs` is the gate's input, not a constraint on the client.** The capability
gate admits a tool only when `needs ⊆ reach`, where reach is *derived* from the
account's composed policy — having a readable path is what confers read reach.
On the client there is no profile left to narrow: a tool declaring no `network`
still runs on a machine that has the user's network. What keeps a tool from
reaching further than the account is the gate and the approval verdict, and
`needs` is how the gate knows what to require.

**`mutating` and `critical` decide who is asked.** Under the default
`auto_accept = "safe"` a mutating call stops for a human; under `"yolo"` only a
critical one does. These are the in-reach damage control that D23 relies on:
injected text cannot widen reach, but it can propose destructive work inside it.

## The executables

`bin/` holds one script per tool with an `exec`, installed under the bare name
the manifest gives. The client joins its own bin directory with that name, so a
mismatch is a `spawn_failed` on every dispatch of that tool.

`job_output` and `job_kill` have no script: they are `daemon` tools, answered
by the client daemon from the background jobs it holds, which no subprocess
could see (D98). Their manifests are still the whole interface — the schema
the model is given, the grant policy checks and the `needs` the gate reads all
come from the same file as every other tool's.

`lib/` holds the modules the tools import: `browse`'s CDP client, the socket
client the three Blender tools share, the add-on the Blender host loads, `python`'s environment resolver, and the
planner `fs_write` and `fs_edit` share. They are
split out because that is how they are installed — the wrapper in `$out/bin`
runs the module in `$out/lib` with `$out/lib` on `sys.path` — and because the
installed tool directory should hold nothing but the names manifests declare.
No manifest names a module, so the tool host cannot resolve one and no dispatch
can reach it.

Two entry points do not match their tool's name. `python` installs as
**`neuman-python`**: `exec` is resolved by joining the tool directory with the
name, so any name works, and this package is an ordinary one that may end up on
a PATH, where a file called `python` would shadow the interpreter for everything
that searches it. The obvious name stays free deliberately. `blender` installs
as **`neuman-blender-tool`** for the same reason: a file called `blender` would
shadow Blender itself.

Three conventions worth stating, because each is a way to get a tool subtly
wrong:

- **Exit 0 for an empty result.** `fs_search` with no matches prints
  `(no matches)` and exits 0. `grep` exits 1, which the client would read as a
  failed dispatch.
- **No deadline of its own.** The client executor owns both of them — how long
  the call blocks and how long the process may live — and kills the process
  group at the second. A deadline inside a tool could only be the weaker of the
  two, and the split is what lets one owner decide when a call becomes a job
  (D98).
- **No path checking.** See above.

## The development environment, and the two tools that resolve it

`python` runs a script in the working directory's own flake environment. The
environment is a lookup, not a privilege: `lib/direnv_env.py` **parses** the
text `nix-direnv` already cached under `.direnv/` and never runs `nix`, never
runs direnv, and never sources the cache. Sourcing is the one that matters:
`.direnv` lives inside the working directory, so sourcing it would execute text
found in a repository Neuman was merely pointed at, which inverts D23 — injected
text may propose work, never widen reach. The parser accepts only
`NAME='single-quoted'`, carries six variables and never `shellHook`, and
requires every path entry to be under `/nix/store`.

**Two different "no environment" answers.** A directory that *declares* one —
a `.envrc`, a `flake.nix` — and has not built it is refused: running against a
bare interpreter would silently lack the packages the project means to provide.
A directory that declares nothing falls back to the current interpreter, because
most directories are not projects — they hold the data a script is pointed at.
Each refusal is named (`env_unrealised`, `env_stale`, `env_collected`,
`env_untrusted`, and the non-failure `no_environment`) and each name has a
sentence of advice in `bin/python.py`.

**`describe` asks the interpreter, not the flake.** Reading `flake.nix` is the
wrong source even though it is the file a human would open: it is an expression,
not a result. `describe` runs `importlib.metadata` in the resolved interpreter,
so a name it reports is a name that will import. The list is capped at sixty
with the true count always printed, because a `describe` too expensive to call
is one a model stops calling.

**`env_probe` asks the same resolver for a different answer.** `python` wants an
interpreter to run code with; the probe wants the *name of the state* the
directory is in, as JSON on stdout, and runs nothing at all — no subprocess, no
inventory. That is the whole reason it is a second tool: `describe` spawns the
resolved interpreter to list a hundred distributions, which is the right price
for a model deciding what it can import and the wrong one for a probe that fires
every time the user picks a directory in the chat window.

Every state exits **zero**, including the refusals, because for this tool a
refusal is the answer. `no_environment` is the ordinary condition of most
directories, and a non-zero exit would make the caller report a failed dispatch
for a folder of data.

It is installed and **granted to no role**. The two callers are
`/api/environment`, which colours the working-directory control, and the
harness, which states the environment in the system prompt — neither can make a
tool call. A model wanting this has `python(action="describe")` and a fuller
answer; because `grantable()` advertises only what policy names, a tool no role
grants never reaches the model's schemas, while the client advertises every
manifest it can run so a route may still dispatch it.

It is also `diagnostic`, which is what keeps it out of the client's tool
chooser. Ungranted alone is not enough: that row still draws, struck through
under "an operator grants tools in `services.neuman`", which invites someone to
grant a capability that is not one. The flag is presentation only and appears
nowhere in the gate (D33).

## Known limitation: `mutating` is per-tool, not per-call

`shell` is declared `mutating = true`, so every shell call asks for approval
under `safe`. That is correct-but-coarse: `ls` mutates nothing and `tee` does,
and a manifest can only describe the tool. Declaring it `false` to avoid the
prompting would skip approval for every destructive command a shell can run, so
the flag errs closed.

`python` has the same shape and feels it more sharply — `print(1 + 1)` mutates
nothing, `open(p, "w")` does, and arithmetic is something a model reaches for far
more often than it reaches for a shell.

The fix is per-call classification — a predicate over the parsed arguments,
evaluated at gate time — which needs a decision-log entry and a schema field.
Until then: the read-only tools above are not mutating, so a model should reach
for `fs_read`/`fs_search` first.

## Adding a tool

1. `tools/<name>/manifest.toml`, with `name` equal to the directory.
2. The executable in `bin/`, named by the manifest's `exec`.
3. `neuman-server/tests/test_tools.py` asserts the set's invariants (mutating
   implies write reach, read-only tools do not mutate). A new tool that violates
   one fails the build rather than review.
