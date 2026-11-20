# Install workflow — working plan

**Status:** working plan, not normative. Decisions belong in
`docs/decisions.md`; this file records the execution order and acceptance
criteria. Delete it when the work is done.

## Goal

A NixOS server install that is `services.neuman.enable = true`, one
`neuman-admin bootstrap`, and then the administration page: an operator adds a
provider by clicking its tile and pasting an API key, and adds a model by
clicking its tile. No catalog preset, role or provider is declared in Nix. The
macOS client follows once this is complete.

## Decisions

D76 and D77 are recorded. The D68 amendment lands with Phase 3, when the seed
is actually removed.

- **D76 — The catalog is data the server ships, and it enforces the facts.**
  The presets move from `nix/module/catalog/*.nix` to JSON beside the server
  (`neuman-server/.../catalog/`), validated by a new
  `contracts/schemas/catalog.schema.json`. The shape keeps D39's three kinds of
  fact: shared model facts, per-route facts (id, output ceiling, reasoning
  levels, prices), and policy. A baseline model entry records the preset and
  route it came from (`preset`, a new optional field); a baseline write that
  contradicts a recorded preset's facts is refused by name — the server-side
  form of the assertion `lib.nix` makes today. A custom entry (no `preset`)
  stays possible and carries the exposure it always did. Amends D39, removes
  D35's "declared, not stored".
- **D77 — An API key is entered once and kept as a file the server writes.**
  `POST` takes the key; the server writes `<state>/secrets/<provider>.key`,
  `0600 neuman`, by temp-file and rename (the `overlay.py` write), and the
  baseline holds only the path. The key is never returned, logged, put in the
  database, the audit payload or a backup; a restore means re-entering keys.
  Tenet 12 still holds for the document — it is a path — and the amendment is
  that the *panel* may now be how the file comes to exist.
- **D68 amended** — there is no Nix seed. A fresh database starts empty; the
  baseline, prompt and catalog are created in the panel or with
  `neuman-admin baseline set`. `bootstrap.*`, `cloudProviders`, `cloudModels`,
  `/etc/neuman/baseline.json`, `NEUMAN_BASELINE` and `NEUMAN_PROMPT_FILE` go.

## Local models in this round

The runtimes stay Nix-declared (`localModels`, units, polkit, `models.json`)
until the runtime-host step. What changes is how one becomes selectable: the
facts `localCatalog` computes today (`context_tokens` from `contextSize`,
`vision` from `mmproj`, `reasoning`, `max_tokens`, the loopback URL) move into
`/etc/neuman/models.json`, and the Models tab offers each unregistered chat
runtime as a "served on this machine" tile that registers it in one click.
The Nix local presets (`catalog.models.*.on.local`) stay for now, because they
carry runtime flags (`--jinja`, the projector) rather than catalog facts.

## Phase 1 — presets as data, and the write routes (server + contracts) — done

- [x] `presets.schema.json`, fixtures, codegen; `preset` on baseline provider
  and model entries.
- [x] Presets under `neuman_server/presets/` for `deepseek`, `openrouter`, and
  the hosted routes of DeepSeek V4.1 Flash, GLM 5.3 Flash and Qwen3.8 27B; the
  measured reasoning is in its `README.md`.
- [x] `decision/harness/presets.py`: build an entry, refuse a contradicted fact,
  re-apply facts at startup.
- [x] `Settings.secrets_dir`, `0700`; `foundation/state/secrets.py`.
- [x] Routes (`composition/adminapi/catalog.py`), also served as `/api/admin/…`:
  `GET /admin/catalog`, `POST /admin/providers`,
  `PUT /admin/providers/{name}/key`, `POST /admin/models`,
  `POST /admin/models/local/{name}`. A provider is removed through the baseline;
  every baseline write sweeps key files nothing names.
- [x] `PUT /admin/baseline` refuses a contradicted fact, a dangling provider and
  a reasoning default outside its levels.
- [x] Stale "declare it in Nix" text in `catalog.py`, `adminapi/gateway.py`,
  `ports.py` and the model-error message.

## Phase 2 — the Providers and Models tabs — done

- [x] Square tiles from `/api/admin/catalog`: a provider tile opens a one-field
  key dialog (a second account at the same provider asks for a name); a model
  tile is enabled once a provider with its route is here, and says which it
  needs otherwise. "Custom" keeps the hand-declared forms.
- [x] Providers table: key `set`/`none` (the path in the tooltip), replace key,
  delete; URL editing only for a hand-declared provider.
- [x] Models table marks preset entries; their facts are drawn read-only. The
  card names models no role grants.
- [x] Logos as same-origin SVGs in `neuman-client/web/src/logos/` (DeepSeek,
  OpenRouter, GLM); Qwen falls back to its monogram until a file exists.
- [x] Walked in a browser against a real server and Postgres: add DeepSeek with
  a key, add DeepSeek V4.1 Flash, edit its reply length. The key is in the
  `0600` file and in no log, audit line or database dump.

## Phase 3 — the NixOS install — done

- [x] The server reads no seed: a fresh database gets an empty baseline and
  prompt. `NEUMAN_BASELINE`, `NEUMAN_PROMPT_FILE` and `load_baseline` are gone;
  the test surface injects the baseline through a fake settings store whose
  compare-and-set really compares.
- [x] `bootstrap.*`, `cloudProviders`, `cloudModels`, `/etc/neuman/baseline.json`
  and `prompt.md` removed from the module. `nixosModules.neuman` defaults the
  three package options to the flake's own.
- [x] `/var/lib/neuman/state/secrets` created `0700 neuman`.
- [x] `models.json` carries a `catalog` object per llama-cpp runtime.
- [x] The hosted Nix presets are deleted; the local ones moved to
  `nix/module/runtimes/` and are the flake output `localModels`;
  `catalog-eval` became `runtimes-eval`.
- [x] VM tests configure through the admin socket. `vm-gateway` adds its
  provider and key over the socket and checks the key file's owner and mode and
  the key's absence from the environment, a database dump, the audit mirror and
  a restored backup; `vm-restart` checks a key survives a reboot.
- [x] The demo ISO bootstraps and sets its policy through the socket at boot.
- [x] D68 amended.

## Phase 4 — documents — done

- [x] `setup.md` rewritten around the install as it now runs: the module, the
  first administrator, providers and models from tiles, roles and the prompt,
  local runtimes. README "Models", "Voice" and "Deploy it"; `database.md`,
  `tools.md`, `harness.md`, `networking.md`, `nix/README.md`,
  the server README's tree; decisions D18, D35, D36, D39, D65; the macOS plan's
  first-run item closed.
- [x] `neuman-admin baseline set` reads what `--json baseline show` prints, so
  the export/import pair `setup.md` documents is one.

What is left of this plan is the walk on real hardware under Verification.

## Verification

- `( cd neuman-server && python3 -m pytest tests -q )`, client and admin suites,
  `python-lint`, `nix-lint`, contracts triad.
- `nix build .#checks.x86_64-linux.vm-{gateway,appliance,restart,iso}`.
- On real hardware: "Starting over" in `setup.md`, then the documented install,
  start to a completed turn on a hosted model and on the local GPU.
