# Neuman

Neuman lets a business run AI it fully controls: an agent with real filesystem
and shell capability, on the business's own servers, shared with no one. It is
built for small and medium-sized organisations that need privacy and still want
the strongest AI they can get, and works just as well for one person or a
family. It is open source, and extended by adding tools.

The agent is governed so that

> **the agent runs as the human it acts for, and does nothing the human has not
> approved.**

Not by asking a model to behave. The brain (policy, approval, audit and
inference) is a service on the appliance. The hands are a program on the
user's own machine, running their tools as them, and every governed decision is
recorded in a tamper-evident chain.

`docs/security-model.md` states the security model precisely and says what it does not
cover; `docs/networking.md` maps every connection between the parts.

## Try it

```
nix build .#iso
```

Produces `neuman-demo.iso`: a live image that boots straight into a running
appliance. Sign in as `demo` / `neuman`, and watch a dispatch get approved and
run on the machine you are sitting at.

It is a demonstration image, not a deployment: the password is public, there is
no TLS, and the whole system runs in RAM, so nothing survives a reboot. The
console says so at every login.

## Models

Neuman runs open-weight models on the appliance's own GPU (D78) and speaks to
every model, local or hosted, through its own OpenAI-compatible gateway (D20). A
hosted provider and a model on your own GPU are the same thing to the gateway:
an endpoint and a model string, held in a catalog keyed by a local name.

Neuman supports a reviewed set of models and providers and ships a preset for
each (D76). In the administration page a provider is a tile and an API key, and
a model is a tile on a provider you have. There is nothing to declare in Nix.
The key is written by the server to a file only it can read, and is never shown,
logged, audited or backed up (D77).

The split matters, and it has three parts rather than two. A model's **window,
vision and reasoning dialect** are facts about the model, true wherever it runs.
Its **id, output ceiling and prices** are facts about the *route*: the same
DeepSeek model is four times cheaper per input token through OpenRouter and caps
output at a third of what the origin allows, which is why the provider is chosen
rather than defaulted. Facts come with the preset and a write that contradicts
one is refused, because a wrong claim about a model does not change the model, it
only makes Neuman wrong about one. **Reply length and costs** are the operator's,
and changing them is the point.

A model or provider Neuman does not ship can still be added by hand, carrying the
full exposure the presets exist to remove.

A model on this machine's GPU is a tile too. The Models section opens on **On this
machine**: the tile says how big the weights are and whether they fit this GPU,
downloads them from Hugging Face at a pinned commit, checks each file's hash, and
adds the model. Nothing is declared in Nix (D78, D79). Files you already have
under the preset's names are adopted by hash rather than downloaded again.

Serving a model and permitting its use are separate: adding it puts it in the
catalog, and a role's `models` grant decides who may select it.

## Models and the GPU

For an administrator, the model chooser under the chat window's message box
opens a card listing what this appliance serves, what is resident on the GPU,
what each costs, with load and unload.

One unit, `neuman-runtime`, holds the GPU and nothing else: it runs llama.cpp's
router for the local models, and builds every command line itself from the
presets and a few typed settings. Another, `neuman-fetch`, holds the network and the weights directory.
`neuman-server` keeps `PrivateDevices=yes` and can write neither; GPU telemetry
comes from a separate unit that runs `nvidia-smi` and writes a JSON file (D30).

Admin-only including the reads: GPU capacity and the model inventory are
appliance topology, and an ordinary user picks a name from what policy grants them.

## Where the data lives

Neuman does not own a share. Tools run on the acting human's machine, so they
reach what that machine reaches: a local disk, a mounted file server, object
storage through a client, or whatever else the organisation already uses. Who may read
what inside it is ordinary Unix ownership, modes and group membership, or the
storage's own ACLs, administered exactly as they were before Neuman existed.

A role names the paths an account's structured tools may touch, as a ceiling:

```json
"staff": { "files_read": ["/srv/share"], "files_write": ["/srv/share"] }
```

The server sends those surfaces with every dispatch and the client checks a
path-taking tool's argument against them, symlinks resolved. That is a guardrail
rather than a boundary, since `shell` and `python` reach whatever the user
reaches, and the real decisions are made on the filesystem, by the same groups an
organisation already runs.

Scale is the point. The same structure is one directory for one person, a few
for a family, and a tree of matters for a firm. And because the agent acts as
the person, what someone learns on a laptop is what they already know on
a share.

## Deploy it

```nix
imports = [ neuman.nixosModules.neuman ];
services.neuman = {
  enable = true;
  adminSocketAccess = [ "you" ];   # who may create the first administrator
};

# On the machine the human works at. It logs in as them, serves the chat window
# and the administration page on loopback, and runs the tools as their account.
# The core never reaches in.
programs.neuman-client = {
  enable = true;
  server = "https://neuman.example.com";
};
```

Then `neuman-admin bootstrap --name you`, and the rest is the administration
page: providers, models, roles, accounts and the prompt all live in Postgres
(D68), and none of them needs a rebuild.

**[`docs/setup.md`](docs/setup.md) walks this through properly**, including the
permission steps a first deployment gets wrong: admin access needs a group and
a fresh login, and model weights cannot live under `/home`.

Accounts are Neuman's own. The first administrator is created over the local
socket with `neuman-admin bootstrap`; everyone else is created with
`neuman-admin account create` or in the administration page. No host account is
needed for a user, and no rebuild is needed to add one.

Admin authority is membership in **`neuman-admin`** in Neuman's own group
store. The local admin socket is reachable by the `neuman` OS group; the core
checks the peer's name against `neuman-admin`. Neither `wheel` nor uid 0
confers admin.

Configuration lives in the database from the first boot; the module carries
none. `neuman-admin --json baseline show` and `baseline set` export and import
the whole of it (roles, defaults, providers and models, never keys), and
`neuman-admin role rename old new` moves a role's key, declared references and
group grants together.

## The parts

| Part | Language | Tier | What it is |
|---|---|---|---|
| [`neuman-server`](neuman-server/) | Python | unprivileged | The decision core and inference: policy, identity, approval, the agent loop, the model gateway, both APIs |
| [`neuman-client`](neuman-client/) | Python | the user's machine | The executor, the browser host and the chat window: runs Neuman's tools as the acting human, starts the Chromium `browse` drives and any tool package's host (Blender) when its tools first need it, and serves the UI from `web/` |
| [`neuman-admin`](neuman-admin/) | Python | operator | The CLI, over a Unix socket authenticated by peer credentials |
| [`contracts`](contracts/) | JSON Schema |  | The wire formats, with two independent implementations |
| [`tools`](tools/) |  |  | Manifests, executables and tool packages: the client's tools, the one the core answers itself, and the sets an operator enables |
| [`nix`](nix/) | Nix | composition | The module, the ISO, and the VM tests that are the release gate |

## Tests

```
nix flake check
```

The checks boot a real machine: the appliance composition with a real login and
dispatch, restart, the model gateway, and the demonstration image. The server,
client, contracts and admin suites run without one, and so do the linters and
the contract triad.

Two rules this project learned expensively, both recorded in full in
[`docs/gotchas.md`](docs/gotchas.md):

**Absence of failure is not evidence.** A check must be shown failing before its
passing means anything. Every fix here was verified by deliberately breaking the
thing it guards.

**A fixture that constructs the thing under test proves only that the fixture
agrees with itself.** Two of the most serious bugs found in this tree, a
capability that reached nothing anywhere and a model client that never sent its API
key, passed every test that existed, because in both cases the only thing
exercising the code was a fake that agreed with it.

## Documents

- [`docs/manifesto.md`](docs/manifesto.md): Neuman's ten laws, what it believes
  about open source and owning your AI, and the tiebreaker when nothing else
  decides a question
- [`docs/philosophy.md`](docs/philosophy.md): what Neuman is for, who it is
  for, and why running as the person is what the security model is built on
- [`docs/setup.md`](docs/setup.md): deploying one, in the order things go
  wrong. Every step is something that failed once on a real machine, including
  the permission traps (operator groups and a fresh login, model weights)
- [`docs/administration.md`](docs/administration.md): running it day to day:
  accounts, roles and groups, models, the record, and common jobs
- [`docs/security-model.md`](docs/security-model.md): the security model, and its edges
- [`docs/networking.md`](docs/networking.md): every socket, port, tunnel and
  outbound path, and what enforces each
- [`docs/tools.md`](docs/tools.md): what the tools let you do, every shipped
  tool and package, what happens between the model proposing a call and the
  human's own machine running it, and one message that tries them all
- [`docs/tasks.md`](docs/tasks.md): work Neuman does on a schedule or on demand,
  from commands you write and agent steps inside limits you set
- [`docs/context.md`](docs/context.md): what happens when a conversation
  outgrows the model's window, what a summary keeps and loses, and how to work
  with it
- [`docs/harness.md`](docs/harness.md): how the agent loop works, and how it
  keeps a long task on its feet: observation masking, the anchored structured
  summary, the pinned task statement, and the reasoning/truncation interaction
  that compaction exists to survive
- [`docs/decisions.md`](docs/decisions.md): the decisions the code cites by
  number, and the platform facts they assume
- [`docs/database.md`](docs/database.md): what is in the one database, table by
  table, and what is deliberately a file instead
- [`docs/gotchas.md`](docs/gotchas.md): every way this has been observed to
  fail, and what was done about it. Read before changing a boundary.

**`docs/*.md` is published.** [privateai.solutions/docs] fetches every
top-level file in `docs/` from the head of `main` at each build (`docs/plans/`
is not published) and renders it as a page: the title from its H1, the card
blurb from its first paragraph. So they are public-facing writing, not just
internal notes: a sentence that only makes sense next to the code will be read
by someone who has never seen it, and the first paragraph has to stand on its
own. A new doc appears without a change to the site, under "More" until the
site's `src/lib/docs.ts` gives it a place in the sidebar; renaming one changes a
public URL.

[privateai.solutions/docs]: https://privateai.solutions/docs

## Status

`0.1.0`, the first release, and in daily use on real hardware. The schema and the
wire contracts are frozen from here: a change is a new migration (D103) or a new
contract version. Today the appliance:

- authenticates people against its own account store and governs each one's
  agent by role: tools, paths, models and how much it asks;
- runs the agent's tools on the person's own machine, as them: files, shell,
  Python, git, the web, a real browser, and Blender as the first tool package;
- serves open-weight models from its own GPU, downloaded at a pinned,
  hash-checked version, beside any hosted provider an operator adds;
- holds conversations with forks, a working directory, files dropped on the
  window and images for vision models, and keeps long-running commands as
  background jobs the person can follow and stop;
- runs **tasks**, which are recipes of commands and agent steps on a schedule, each agent
  step inside the narrower of its envelope and the owner's live policy;
- records every governed decision in a verifiable chain, and can be backed up
  and restored.

Each of those was added without the server gaining a capability: the secure
design kept turning out to be the smaller one, because the privilege the obvious
implementation wanted was privilege the feature did not need.

`docs/decisions.md` holds why it is built this way; `docs/gotchas.md` is the
living record of how it has failed, and the entries worth reading most are the
ones found by *running* it rather than testing it: N42 onward, where the
failures stop being logic errors and start being permissions, socket protocols,
browser policy, and things a test harness cannot see.

## License

MIT. See [`LICENSE`](LICENSE). Neuman is provided as is, without warranty of any
kind; `docs/security-model.md` describes how it is designed, not a promise about
any deployment of it.
