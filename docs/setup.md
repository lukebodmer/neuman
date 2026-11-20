# Setup

A working NixOS deployment, in the order things actually go wrong. Every step
here corresponds to something that failed once on a real machine — the entries in
`docs/gotchas.md` from N42 onward are the receipts.

If you only want to *see* it work, skip all of this: `nix build .#iso` produces a
live image that boots into a running appliance. This document is for putting one
on a machine you keep.

The shape of it: the NixOS module installs the server and nothing else. A fresh
appliance has no accounts, roles, providers or models (D68). You create the first
administrator over a local socket, and everything after that is the
administration page — a provider is its tile and an API key, a model is its tile.

## 1. The flake input and the module

```nix
# flake.nix
neuman.url = "github:you/neuman";   # or path:/home/you/projects/neuman
neuman.inputs.nixpkgs.follows = "nixpkgs";
```

```nix
# configuration.nix
imports = [ inputs.neuman.nixosModules.neuman ];
```

**A local `path:` input is pinned like any other.** `nixos-rebuild` will not see
new commits until `nix flake update neuman`, which is easy to forget while
iterating on Neuman itself and produces the confusing symptom of a change that
"did not take".

## 2. The service

```nix
services.neuman = {
  enable = true;

  # Who may open the admin socket, and therefore who can create the first
  # administrator. Reachability only: authority is the `neuman-admin` group in
  # Neuman's own account store, which `neuman-admin bootstrap` creates.
  adminSocketAccess = [ "you" ];
};
```

That is the whole server configuration for an appliance used from the same
machine. The module's packages default to the flake's own; it creates the
`neuman` user, the database, the state directories and the hardened unit.

**A client on another machine** needs the server reachable and encrypted:

```nix
services.neuman = {
  bind = "0.0.0.0";
  tls.certificate = "/var/lib/acme/neuman.example.com/cert.pem";
  tls.key = "/var/lib/acme/neuman.example.com/key.pem";
};
networking.firewall.allowedTCPPorts = [ 8443 ];
```

Or keep the default loopback bind and put a reverse proxy in front. The default
is loopback because exposing the session surface is a decision, not something to
inherit (D1); the client refuses `http://` to anything but loopback (below).

### The client, on the machine the human uses

The core decides; nothing runs on it. The executor, the chat UI and the browser
are one package, and they belong on the machine the acting human is typing at:

```nix
# home-manager, on the user's own machine
imports = [ inputs.neuman.homeModules.neuman-client ];

programs.neuman-client = {
  enable = true;
  server = "https://neuman.example.com";
};
```

The client logs in as the Neuman account you name — the password prompt is at
`neuman-client login --user <name>`, and the session is kept in the client's
state directory, `0600`: `~/.local/share/neuman/client/session.json` on Linux,
or `~/Library/Application Support/Neuman/client/session.json` on macOS. That
account is created by an administrator in Neuman's own store, not necessarily
the local account the client runs under: the local account is what the tools run
as, and the two need not share a name (D67). The client serves the chat window
and the administration page on `http://127.0.0.1:8642`, and starts the browser
when `browse` needs one. The browser talks to the client, so the core's cookie
never reaches it and there is no second origin to get wrong.

A remote `server` needs `https://`, or a reverse proxy terminating TLS, and the
client refuses `http://` to anything but loopback rather than warning: the
password crosses first and the session cookie crosses on every request after
it, so both would be readable by anything on the path. On the same machine
`http://127.0.0.1:8443` is loopback and needs nothing.

**The administration page is the client's.** A server with no client anywhere
is administered with `neuman-admin` over the socket; everything this document
does in the page has a CLI or API equivalent, but the page is where providers
and models are tiles.

## 3. The first administrator

### Admin access is two groups, and they need a fresh login

Reachability is the OS `neuman` group, granted by `adminSocketAccess`.
Authority is membership in **`neuman-admin`** in Neuman's own account store,
which `neuman-admin bootstrap` grants when it creates the first administrator
over that socket. The two are separate on purpose: the OS group lets you
connect, and the database group decides what you may do.

**Group membership is fixed at login.** After the rebuild that adds you, your
existing shell still has the old groups, so this is what you see:

```
$ neuman-admin status
neuman-admin: cannot reach /run/neuman/admin/admin.sock: Permission denied
```

Log out and back in, or `newgrp neuman` for that shell. (N41)

Then create yourself:

```
$ neuman-admin bootstrap --name you
```

It asks for a password (12 characters minimum; `--password-file` reads one from
a `0600` file instead). The bootstrap is the one admin request the server accepts
before an administrator exists, and only over the local socket. Every account
after it is created with `neuman-admin account create` or in the administration
page; no host account and no rebuild is involved. The one imperative step is
deliberate: it is the only place a first credential is chosen.

```
$ neuman-admin status
neuman-admin: forbidden: 'root' is not an admin on this appliance
```

That is the other failure, and it reads differently on purpose: the first is the
kernel refusing the connection, this is the server refusing the request. **Root
is not automatically an admin**, and neither is `wheel`: uid 0 and sudo confer
nothing here, because authority comes from the account store. A sysadmin can
still reach everything by editing the database — what this stops is the
appliance handing the governance record to everyone who can sudo. (N32, N50)

Adding a second operator is two steps: create their account and add it to
`neuman-admin`, and if their OS account needs to run `neuman-admin`, add it to
`adminSocketAccess`. Reachability without authority connects and is refused.

Now log the client in as that account and open the page:

```
$ neuman-client login --server https://neuman.example.com --user you
$ cat ~/.local/share/neuman/client/ui-url
```

Open that URL — the client's loopback UI, token included — and follow
**Manage Neuman** at the foot of the chat window's rail.

## 4. Providers and models

### A provider is its key

Under **Providers**, each provider Neuman supports is a tile. Choose one and
paste its API key; the dialog links to where the provider issues keys. That is
all a provider needs: its URL is part of the preset, and the key is the one
thing no preset can supply.

The key is written by the server to `/var/lib/neuman/state/secrets/<name>.key`,
`0600` and owned by `neuman`, and the baseline holds only that path (D77). It is
never shown again, never logged, never in an audit entry, a database row or a
backup — so restoring an appliance means entering its keys again. Pressing a
provider's tile opens it: pasting a key there replaces the filed one, and
deleting the provider deletes the file. A provider a model still names is not
deleted, because a model whose provider is gone is one nobody can run. A second
account at the same provider (a team's key beside your own) is the same tile
chosen again, with a name; the panel's first field chooses between them.

| Provider | URL |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |

**Custom** is any other OpenAI-compatible endpoint: a name, a base URL (the part
before `/chat/completions`) and an optional key. The URL must be `https://`, or
`http://` on this machine — a key over plain HTTP crosses the network on every
request.

### A model is its tile

Under **Models**, each model Neuman supports is a tile, enabled once a provider
that serves it is here; otherwise it says which one it needs. Choosing one that
is not added shows what you are adding — the window, whether it sees, its prices
and reasoning levels, and when those were last read from the provider — and asks
for the name users will pick in the composer. Once it is added the same tile
opens its settings, and its caption is the name it was added under. There is no
list beside the tiles: a model appears where it is chosen (D90).

| Model | Served on | Name | Notes |
|---|---|---|---|
| DeepSeek V4.1 Flash 1017 | DeepSeek, OpenRouter | `DeepSeek-V4.1-Flash-1017` | 1M window, vision, reasoning off/low/high/max |
| GLM 5.3 | OpenRouter | `GLM-5.3` | 1M window, text only, reasoning low/high/max |
| GLM 5.3 Flash | OpenRouter | `GLM-5.3-Flash` | 1M window, vision |
| Kimi K3.1 Pro | OpenRouter | `Kimi-K3.1-Pro` | 1M window, vision, always reasons at low/high/max |
| Qwen3.8 27B | OpenRouter | `Qwen3.8-27B` | 1M window hosted, vision |

**The provider is chosen, never defaulted, because it changes what you get.** The
same model through two providers is not the same deal — read 2026-11-13:

| DeepSeek V4.1 Flash 1017 | on DeepSeek | on OpenRouter |
|---|---|---|
| provider's model id | `deepseek-flash` | `deepseek/deepseek-v4.1-flash-1017` |
| max output | 384,000 | 131,072 on its lowest hosts |
| input $/M | 0.15 | 0.038 |

Four times the input price for triple the output ceiling. Which matters is
yours to decide. Adding the same model on both means giving the second a name of
its own, because the name is what a user picks.

**What you can change, and what you cannot.** A model added from a tile records
the preset it came from (D76). Its **facts** — the provider's id, the window,
vision, the reasoning dialect and which levels the route forwards — are the
preset's: the panel draws them read-only, the server refuses a write that
contradicts one, and an upgrade that corrects one applies it at startup. Its
**policy** is yours: reply length, prices, the default reasoning level. A wrong
claim about a model does not change the model; it only makes Neuman wrong about
one, and the failure arrives mid-turn after a tool has run (N86, N102).

**Custom** adds a model by hand, against any provider, with every field yours —
and every claim unchecked until a turn spends tokens on it. `vision` is what
allows `fs_image`, and it is stated rather than detected because the id does not
say: `deepseek-v4.1-flash` accepts images and `deepseek-v4-flash` does not.
`context_tokens` is what decides whether a turn fits; 0 means unknown, which
Neuman handles by not pretending — no capacity bar, and its own trimming default
(N86).

An unlisted model or provider is a file in `neuman-server/src/neuman_server/presets/`
and a row above — a reviewable diff, rather than a form each operator fills in
and gets wrong in private. The `README.md` there says how.

### Adding a model grants it to nobody

Declaring a model and permitting its use are separate decisions. The Models section
names any model no role grants yet; the grant is the next step.

## 5. Roles, accounts and the prompt

A **role** is a named grant: which tools, which paths, which models, and how
approval starts. Create one under **Roles**:

- **Tools** — start with `fs_read`, `fs_list`, `fs_search`, `fs_image`,
  `fs_write`, `shell`; add `python`, `git`, `browse`, `web_fetch`, and the tools
  of any package you have enabled (section 8) as needed.
- **Files read / write** — `$HOME` is the spelling to use: it names the home of
  whichever machine runs the tool, so a role that means "their home" keeps
  meaning it when the client is not on this host. It does not grant the home
  directory: it expands to `services.neuman.homeDataDirs` (`Documents`,
  `Desktop`, `Downloads`, `workspace` by default), so dotfiles stay out of reach.
- **Models** — the names from the Models section.
- **Approval** — `never` asks for every call, `safe` for mutating ones, `yolo`
  only for critical ones. A user may move it in Settings within the role's
  ceiling; cap a shared account at `safe`.
- **Network** — `any` offers tools that need the network; omit it and they are
  withheld.

Start `files_read` narrow. A too-wide role is an agent with your whole home
directory: the server sends the account's surfaces and the client checks the
structured tools against them, but `shell` and `python` reach whatever you do.

Then give it to an account. **Accounts → New account** asks for the starting
roles before it creates the row; selecting a role is a live assignment, so the
account follows later edits to it. A role can also be granted to a group, so
everyone in `eng` gets `engineer`. Two roles that set the same scalar differently
are refused rather than resolved by alphabetical order. A per-account change made
afterwards is an override on the account's Policy, Tools or Limits section.

**The prompt.** Under **Prompt**, text for things only this organisation knows:
what the business does, where shared files live, who to suggest asking about
what, house style. It is placed before the generated prompt — which still states
the acting user, the reachable paths, the tools and the approval mode from live
policy — so it cannot appear to qualify a fact about reach, and an instruction
in it to read a path policy does not grant is a sentence the gate ignores (D65).
Each account may add their own instructions in Settings; neither grants anything.

**A configuration as a file.** `neuman-admin --json baseline show` exports the
roles, defaults, providers and models as one document, and `baseline set` imports
one — the way to keep an appliance's configuration in git, or to rebuild one
after starting over. Keys are not in it: a provider's key file is named, never
read.

## 6. Local models

A model on this machine's GPU is added the way a hosted one is: from its tile.
The Models section opens on **On this machine**, and each model Neuman ships with a
local route is a tile that says how big its weights are and whether it fits
this GPU. Nothing is declared in Nix and nothing needs a rebuild (D78).

| Tile | Download | GPU memory, measured |
|---|---|---|
| Qwen3.8 27B (Q4) | 18.5 GB | 22.2 GiB at a 64k window with its projector and speculation |
| GLM 5.3 Flash (Q4) | 200.8 GB | not yet measured |
| GLM 5.3 (Q4) | 467.3 GB | not yet measured |
| DeepSeek V4.1 Flash 1017 (Q4) | 501.7 GB | not yet measured |

Only Qwen fits one card. The other three are for a machine with hundreds of
gigabytes of GPU or unified memory; their tiles say nothing about fit until
someone has run them and recorded what they held. Each is a split GGUF, several
files the runtime starts from the first of.

The tile does the next thing:

- **Download** fetches the files from Hugging Face at the commit the release
  pinned, checks each against its SHA-256, and only then puts it in place
  (`neuman-fetch`, D79). The tile is the progress bar; an interrupted download
  resumes. A model that does not fit still downloads, after a dialog naming the
  numbers, because a smaller window fits a smaller card.
- **Add** puts the model in the catalog, under a name of its own — the local
  Qwen is `Qwen3.8-27B-Q4`, so it can sit beside the hosted `Qwen3.8-27B`. As
  with any model, it grants it to nobody.

Its settings are behind its tile once it is added — the window, reply length,
layers on the GPU, speculation, context memory, residency, idle minutes and
default reasoning.
They are typed fields, not a command line: `neuman-runtime` builds the command
from them and the preset, so the flags a model needs (`--jinja`, its projector)
come with it and nothing stored can add another (D78). The window is GPU
memory; lower it first if a model does not fit. **Context memory** is how that
window is stored: full precision is the default and the fastest, and compact
(`q8_0`) holds it in about half the memory — on a 24 GB card, room for 128k
instead of 64k — at a decode 6–12% slower once the window is full (N100).

Deleting the last model that uses a set of weights offers to delete the
weights too.

### Weights you already have

Files already in `services.neuman.modelsDir` (`/var/lib/neuman/models`) under
the names a preset pins show their tile as **on disk · check it**. Checking reads
each file once and compares its hash; if it is the pinned file it is adopted and
nothing is downloaded. A different file under the same name is reported and
left alone — it is somebody's, so Neuman never overwrites it; move it aside and
download.

The directory is `0770 neuman:neuman`. Only `neuman-fetch` writes it; the
runtime and the server read it, and the server's unit mounts it read-only. Keep
weights there rather than under a home directory, which the runtime, running as
`neuman`, cannot traverse (N52).

### Residency and the GPU

A model **loads when chosen and unloads when idle** by default — the first
request after a pause pays a cold load of a few seconds from page cache, which
the client shows as waiting for the model. **Keep loaded** holds it from boot.
`services.neuman.runtime.maxLoaded` (default 1) is how many chat models may be
resident at once; choosing another unloads the first.

**A load that does not fit is refused.** The model panel's Load compares the
model's measured memory with what the GPU has free and, if it is short, says so
and names what to unload, rather than starting it to fail. On one 24 GB card
that matters: Qwen at its 64k window leaves too little for a second model, which
is refused while it is loaded — unload it, or lower its window. A model whose window you changed is not held to
the measurement, and if a load does run out of memory the panel says that
instead of llama.cpp's "failed to init model/context".

The runtime is a CUDA build, because `services.neuman.gpu` defaults to `cuda`.
You will need unfree packages allowed, and the CUDA binary cache, or the first
build compiles a toolchain from source:

```nix
nixpkgs.config.allowUnfree = true;
nix.settings = {
  substituters = [ "https://cache.nixos-cuda.org" ];
  trusted-public-keys = [
    "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
  ];
};
```

Set `services.neuman.gpu = "none"` on a machine with no NVIDIA GPU: the runtime
is then a CPU build, and the GPU sampler is not installed.

## 7. Web browsing

`browse` is an ordinary client tool: it runs on the acting human's machine, as
that human (D31, D55). The client's `neuman-browser` starts a real Chromium and
bridges its DevTools endpoint onto a Unix socket in that user's runtime
directory; `browse` dials the same path, which the client hands to the tool. It
is never a network port, because Chrome DevTools Protocol has no authentication
and `127.0.0.1:9222` is reachable by every process on the machine. Grant it by
adding `browse` to a role.

**The profile is chosen on the client.** `programs.neuman-client.profile = "own"`
(the default) is a profile belonging to Neuman, so a page that injects the model
acts with none of your logins. `"copy"` copies your real profile in once at
startup, so Neuman can read pages you are signed into — and a page that injects
the model then acts with your identity.

**There is no headless mode.** Measured against live sites, headless Chromium is
blocked unpredictably, and a capability that fails per-site after you have come
to rely on it is worse than none. **Screenshots compose with vision**: `browse`
with `action = "shot"` writes a PNG, and `fs_image` hands it to a vision-capable
model.

## 8. Tools beyond the basic set

The built-in tools are always there. Others ship as **packages** that an
administrator turns on for the whole appliance under **Tools** in the
administration page (D101). A tile says what a package does, which tools it
holds, what each needs and whether it asks for approval, and what a person needs
installed on their own machine for it to work.

Enabling a package downloads nothing and grants nothing. Add its tools to a role
under **Roles**, as with any tool. Disabling it withdraws the tools from
everyone at their next step and leaves the roles as they were.

**Blender** is the first package: the agent builds 3D models in a Blender that
the person watches and can work in at the same time (D100). Linux only, for now.
Each person who uses it installs Blender themselves — Neuman does not — and a
machine without it is told so, with where to get it, the first time a Blender
tool is called. Nothing has to be started by hand: Blender opens on its own the
first time a tool needs it, and closing it is fine — the next call opens it
again.

`blender_scene` and `blender_view` look and do not prompt under `safe`;
`blender` changes the scene — it runs Python inside Blender as the person — so
it asks. `blender_view` needs a model with vision. Paths given to `blender`
(open, save, export, render) are checked against the role's **write** paths.
Every change the agent makes is one step on Blender's undo stack, named
`Neuman: …`, so Ctrl-Z in the window takes it back, and a `.blend`'s embedded
scripts never run. If Blender opens and then closes, its output is in
`~/.local/share/neuman/client/hosts/blender.log`.

## 9. Check it

```
$ systemctl status neuman-server
$ curl -s localhost:8443/healthz
$ neuman-admin status
$ neuman-admin account list
$ neuman-admin model list
```

`account list` is who may use the appliance — accounts are created, not observed
(D16), and `disabled` is the revocation. `model list` is what each catalog name
actually resolves to: the endpoint, the provider's id, the window and whether it
sees. An entry that resolves wrongly looks exactly like one that works until a
turn spends tokens on it.

## Upgrading

A new version arrives the way the first one did: move the flake input and
`nixos-rebuild switch`. The server applies whatever migrations the new version
carries as it starts — in name order, one transaction each, each recorded with
its SHA-256 (D103) — so a migration that fails leaves the schema exactly as it
was, and the journal names the file that failed instead of the appliance coming
up half-migrated.

Take a backup first: `neuman-admin backup`, and the audit mirror beside it. A
downgrade is a restore from that backup, because migrations are forward-only and
there is no down-migration to run.

An appliance that will not start and says `has changed since it was applied` is
not an upgrade problem: a file that had already run was edited, so the schema in
the database is not the one in the tree. Restore from backup, or start over —
unless the edit changed no DDL, a comment or whitespace being the whole diff. The
checksum covers the file's bytes (N182), so a cosmetic edit trips it while the
schema is unchanged: the bytes the appliance ran are still the truth, and the
fix is to restore the file from the commit that ran
(`git show <commit>:<path> > <path>`), not to restore a database that agrees
with the edit.

## Starting over

Recovery, not the upgrade path: an appliance starts over when its database is
worth less than the reason to replace it, not to reach a new version. A fresh
appliance is three things removed and one thing kept, and the one thing kept is
the expensive one.

**Keep the weights.** `/var/lib/neuman/models` is tens of gigabytes, and a new
database does not need them gone: after starting over, each tile shows **on disk
· check it**, and checking adopts the files by hash without downloading them.

**Keep a copy of the record, even if you think you do not want it.** The
database holds the audit chain, every transcript and the whole configuration;
the overlays hold the per-account overrides, which are files:

```bash
sudo systemctl stop neuman-server
sudo mkdir -p /var/backups/neuman-pre-wipe
sudo -u postgres pg_dump -Fc neuman | sudo tee /var/backups/neuman-pre-wipe/neuman.dump >/dev/null
sudo cp -a /var/lib/neuman/state/policies /var/backups/neuman-pre-wipe/ 2>/dev/null || true
sudo cp -a /var/log/neuman/audit.jsonl /var/backups/neuman-pre-wipe/
```

A dump taken this way is not restorable with `neuman-admin restore`, which wants
a directory written by `neuman-admin backup`; `pg_restore` into a recreated
database is the path back. It is a copy of the record, not a rehearsed restore.
`neuman-admin --json baseline show > baseline.json` beforehand keeps the
configuration in a form `baseline set` takes back.

**Remove the rest, and start it again:**

```bash
sudo -u postgres dropdb neuman
sudo rm -rf /var/lib/neuman/state /var/lib/neuman/spill /var/log/neuman
sudo systemctl restart postgresql-setup   # recreates the database, owned by neuman
sudo systemctl start neuman-server        # recreates its directories
```

No rebuild. `/var/lib/neuman/state` holds the overlays and the provider keys;
`/var/log/neuman` is the audit mirror, which must go with the database or the two
records disagree from the first boot. The unit makes its own directories each
time it starts, with their modes, and `postgresql-setup` is what runs
`ensureDatabases` — restarting `postgresql` alone does not, because the setup
unit has already run since boot (N167). Leave the database *cluster* alone:
unless this machine runs Postgres for nothing else, drop the database and not
the service.

The server comes up empty, and the first account is the bootstrap again (§3). On
the client machine, remove `~/.local/share/neuman/client` (`session.json`,
`ui-token`, `spill/`) for a clean login; the browser profile beside it is just
Chromium's and can stay.

## When something fails

`journalctl -u neuman-server -f` while reproducing it. The server logs canonical
JSON, and a configuration error exits 78 with a message naming the variable
rather than a traceback.

A login that says nothing is answering means the core is down, and a core that
restarts until systemd gives up (`start-limit-hit`) has said why in the journal.
`migration 0001_initial has changed since it was applied` means a migration file
was edited after an appliance ran it, so the database's schema is not the one in
the tree. Applied migrations are immutable (D103); the way forward is a restore
from backup, or [Starting over](#starting-over). If the edit was cosmetic — a
comment, a header — the schema did not change and neither remedy is needed:
restore the file's applied bytes from git (N182). `neuman-admin` cannot help here, because its
socket goes down with the server.

For a local model, `journalctl -u neuman-runtime -f`, and `-u neuman-fetch` for a
download. A cold load of 16 GB takes a few seconds from page cache and tens of
seconds from disk, which the client shows as waiting for the model; a load that
fails is usually GPU memory, which the model panel names, and lowering the
model's window is the cheapest thing to try.

A reply cut short is usually its model's reply length. A reasoning model spends
thousands of tokens before its first visible word, so a low one is easy to hit — a
truncated reply is recovered from automatically (N90) but costs a step each time.
Raise it on the model's row; the prompt and the reply share the window, so a
larger reply leaves less room for history.

A provider answering 401 means the key it was sent. **Replace key** writes a new
one, and the next turn reads it; nothing restarts.

Two failures that look like bugs and are not:

- **`origin_not_allowed` on login** — something submitted a form natively rather
  than through the client's `fetch`. The guard is correct; the request was wrong.
  (N45)
- **A refusal you did not expect** — check whether it is reachability or
  authority before changing configuration. Most of the failures above were the
  appliance correctly refusing something, with the fix upstream of the refusal.
