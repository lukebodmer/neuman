# nix

Composition: the NixOS module, packages, the ISO, and the VM tests that are
the release gate.

## Public surface

- **`nixosModules.neuman`** — `services.neuman.*`. The appliance: Postgres and
  the decision core, with its packages defaulted to this flake's. It carries no
  configuration: a fresh appliance has no accounts, roles, providers or models,
  and an administrator makes them over the admin socket and in the
  administration page without a rebuild (D68).
- **The runtime host and the fetcher** (`module/runtime.nix`) — two units on
  every appliance, built from the server's package. `neuman-runtime` holds the
  GPU and reaches only loopback; it runs whatever local models the database
  names, building each command line from the shipped presets (D78).
  `neuman-fetch` holds the network and the weights directory and downloads a
  preset's pinned, hashed files (D79). Which models run is not configured here:
  a model is added from its tile, with no rebuild. What is here is what only the
  machine can say:
  - **`services.neuman.gpu`** — `cuda` (the default) or `none`. The runtime is
    nixpkgs' `llama-cpp` built for it; with `none` it runs on the CPU and the
    GPU sampler is not installed.
  - **`services.neuman.runtime.{chatPort,maxLoaded}`** — the loopback port of
    the chat router, and how many chat models may be resident at once (1).
  - **`services.neuman.fetch.baseUrl`** — the Hugging Face Hub, or a mirror
    serving the same paths; the bytes are checked against the pinned hash
    either way.

  Neuman hosts no models of its own making — the runtime comes from nixpkgs and
  never enter Neuman's closure. Bundling a model runtime is what D20 deleted
  from v1, at the cost of a bespoke Nix package, a patched dependency, an 8 GB
  test VM and a 1800-second timeout.

- **`services.neuman.modelsDir`** — where weights live, default
  `/var/lib/neuman/models`, `0770 neuman`. Only `neuman-fetch` writes it; the
  server's unit mounts it read-only, because a server that could replace a file
  could choose what the GPU runs.

  Beside the appliance's other state rather than in a home directory, because the
  weights belong to the appliance: a second account uses the same model, and
  deleting a user should not take the models with them. It is also traversable by
  the model runtime, which a `0700` home is not — and that was an entire
  debugging session's worth of `Permission denied` (N52).

- **`checks.<system>.vm-*`** — the VM tests. Cross-part invariants live here
  rather than in a part's own test directory, because they need a booted
  machine: real systemd units, real sockets with real modes, and real account
  rows in a real Postgres.

## Private surface

Everything else. Test helpers, stub daemons, and module internals are not
consumed by any part.

## Why a part's own tests are not enough

`neuman-server`'s suite runs against fakes, by design — every port has one, so
the decision core is testable without a database, a socket, or a model. That
buys speed and isolation, and it cannot say anything about whether two
processes actually talk to each other. A socket's mode, a unit's ordering, a
secret reaching a process that should not see it: none of those are visible
from inside a single process, and all of them have broken before (see
`docs/gotchas.md`).

So the split is deliberate. A part's tests prove its logic; these prove the
seams.

## The tests

| Test | Proves |
|---|---|
| `vm-gateway` | The model seam: the real `ModelClient`, a real socket, and a key entered the way an operator enters one — posted to the admin socket and filed by the server. Asserts on what a stub endpoint *received* — the Authorization header, that it carries no trailing newline, that the body sets `stream` and `include_usage` — and that the key file is `0600 neuman` while the key is absent from `/proc/<pid>/environ`, a database dump, the audit mirror and a restored backup. Found N38, where the key never left the appliance. |
| `vm-iso` | The demonstration image's *configuration*, booted. `nix build .#iso` proves the image compiles; this proves an operator who boots it gets a working appliance — the advertised password works, the policy is what the server serves, the tools run, and the client beside the core serves the UI, proxies the API as the account, and dispatches a real tool across the real transport. It imports `nix/iso/demo.nix` rather than restating it, so the test cannot drift from the image. |
| `vm-restart` | The case every other test skips: an appliance that already has data, restarting. Migrations against a populated database, state that must survive a tmpfs, a socket left behind that would make the next bind fail, a provider key still the provider's — all of which look fine on a first boot. Ends with a full reboot. |
| `vm-runtime` | A model on this machine, end to end: a 1.2 MB GGUF served by a stand-in for the Hub on loopback, refused while a different file holds its name, fetched and verified, added over the admin socket, listed alone by the real llama.cpp router — no cached model, no download argument — and completed through the server's own resolver on the CPU, then unloaded. And, in each unit's own mount namespace, only the fetcher can write the weights. |
| `vm-appliance` | The module: `services.neuman.enable = true` on a booted machine, with Postgres migrated, the server listening on its session surface and nothing configured, the first administrator bootstrapped over the local socket and the configuration set through it, and a real client completing the `/client` handshake as the account it logged in as. The first test of the *composition* rather than of a seam — the dispatch itself, with production code on both sides, is `vm-iso`'s. Also the admin socket: an operator identified by the uid the kernel reports and the `neuman-admin` group in the database, a non-operator refused at the socket, and a policy write that comes back from the *server* rather than from the CLI's own memory. |

They are kept separate so a failure is attributable.

## Rules these tests follow

Each is a gotcha that cost real time. They are stated here because a new test
that ignores one will look like it works.

- **`pkgs.testers.nixosTest`**, never the removed `pkgs.nixosTest`.
- **The driver runs on the host, not the guest**. Every socket or RPC
  interaction must happen inside the guest via `machine.succeed`.
- **Never background a process in `machine.succeed`** — it hangs. Test
  daemons are real systemd services.
- **A started unit is not a ready one**. Wait on a socket, a port, or a
  log line.
- **Flake source is the git tree**: a file must be `git add`-ed before a
  build can see it, and an edit must never be batched with a `nix build` in
  the same step.
- **A predicate's exit code is its answer, so read it with `execute`, not
  `succeed`** (N20). `pgrep` exits 1 when it matches nothing — which for an
  "assert nothing survived" check is the *passing* case — and `machine.succeed`
  turns that into a build failure. The same applies to `grep`, `test`, and
  `diff`. The failure mode is inverted and easy to "fix" by deleting the
  assertion.
- **Assert on values, with the value in the message.** A bare
  `assert x == y` gives `AssertionError` and nothing else across the VM
  boundary; `assert x == y, x` is the difference between a diagnosable failure
  and a rerun (N11 was unreadable for exactly this reason).

## Running one

```
nix build .#checks.x86_64-linux.vm-socket-protocol
nix flake check                       # everything, including the VM tests
```

A VM test builds a guest system closure, so the first run is slow and later
ones are cached.

## The image

`nix build .#iso` produces `neuman-demo.iso` — a live image that boots
straight into a running appliance with a demonstration account. It is not a
production install: the password is public, there is no TLS, and the whole
system lives in RAM, so the audit chain does not survive a reboot. The console
says all three at every login.

What it is *for* is the question an operator asks before deploying anything:
what does this actually do. Boot it, log in as `demo`, watch a dispatch get
approved and confined.

`nix/iso/demo.nix` is what the image *is* — the account, its policy, the
workspace. `nix/iso/default.nix` is only packaging. They are split so
`vm-iso` boots the first one directly instead of a copy, because a test that
restates its subject goes on passing while the two drift.

## Still to land

Nothing blocking. The admin socket landed in M9: the server runs two
uvicorn listeners over one app, and reachability is the `neuman` OS group.

**Two checks, two groups.** *Reachability* is membership in the OS `neuman`
group, granted by `adminSocketAccess`, which is what lets the kernel accept a
connection to the socket; *authority* is membership in `neuman-admin` in
Neuman's account store, read by the decision core. They are separate on
purpose; the bootstrap creates the first administrator's database row, and an
OS account without that row connects and is refused.

The checks stay separate, and `vm-appliance` still has an account with each
combination: `adminSocketAccess` grants reachability alone, so carol connects
and is refused. Two accounts cannot tell "both checks work" from "one does"
(N32).
