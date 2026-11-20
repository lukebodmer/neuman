# macOS client — working plan

**Status:** working plan, not normative. Decisions belong in
`docs/decisions.md`; this file records the execution order and the acceptance
criteria for the macOS client. Delete it when the work is done or the plan is
superseded.

## Goal

A signed native macOS client that logs in to a remote Linux Neuman server, runs
a turn end to end, executes the structured tools as the signed-in human, and
drives the user's own Chrome. The first consumer is the business client that
requires macOS.

## Scope

- macOS client, Apple silicon first. Intel only if the packaging cost is
  acceptable.
- Linux server unchanged. The client connects over HTTPS; there is no macOS
  server.
- The server-side package/authority refactor is a separate track; it landed
  first, before the install workflow, so Postgres decides
  and the module carries no configuration.
- Windows is explicitly out of scope.

## Non-goals

- Running `neuman-server` on macOS.
- A Windows client, or a platform abstraction designed around hypothetical
  Windows requirements.
- Auto-update, a menu-bar app, or a Mac App Store build. A signed `.pkg` and a
  LaunchAgent are enough for the first deployment.
- The admin-panel parity work, which can run in parallel and does not block
  the macOS client.

## Decisions to lock before code

- **Packaging:** native Python bundle (`python-build-standalone` plus a venv
  installed by the `.pkg`), not Nix on the customer's Mac. Nix remains the
  Linux/appliance build and a reference build for CI.
- **State:** `~/Library/Application Support/Neuman/client`.
- **Runtime socket:** `$TMPDIR/neuman-browse.sock`. macOS has no
  `XDG_RUNTIME_DIR`; `$TMPDIR` is per-user and `0700`, and the path stays under
  the `AF_UNIX` length limit.
- **Browser profile:** `~/Library/Application Support/Neuman/browser` for the
  `own` profile; `~/Library/Application Support/Google/Chrome` as the default
  `copy` source.
- **Logs:** `~/Library/Logs/Neuman`.
- **LaunchAgent:** `~/Library/LaunchAgents/dev.neuman.client.plist`, user-level,
  `RunAtLoad`, `KeepAlive`.
- **Chrome:** require Chrome or Chromium in `/Applications` or
  `~/Applications`; do not bundle a browser in the first package.
- **Signing:** Developer ID Application + Developer ID Installer, notarised.
  Confirm the certificates and Apple account before Phase 4.
- **Minimum macOS:** decide and record it in `docs/platforms.md`.

## Phase 0 — decisions and inventory

- [x] Add `docs/platforms.md`: server Linux-only; client Linux and macOS
  supported; Windows unsupported; platform-specific caveats.
- [x] Record the product-boundary decision and the macOS client decision in
  `docs/decisions.md` (D74, D75).
- [x] Confirm the target Mac: Apple silicon, M4 Air/Pro, macOS 14+.
- [ ] Decide whether Nix must build a Darwin reference client for CI. If yes,
  the current `platforms = lib.platforms.linux` in `neuman-client/default.nix`
  and `tools/default.nix` are the first blockers, and the hardwired `chromium`
  input needs a runtime-discovery alternative.
- [x] Freeze the paths and conventions above; they become the installer's
  contract.

**Acceptance:** the scope, support matrix and paths are written down; no code
has changed.

## Phase 1 — platform seam

Create `neuman_client/platform/` with a small, explicit interface. The Linux
implementation must preserve current behaviour exactly.

- [x] `home_dir()` — replaces `pwd.getpwuid(os.getuid()).pw_dir` in
  `neuman_client/cli.py`.
- [x] `state_dir()` — Linux keeps `~/.local/share/neuman/client`; Darwin uses
  `~/Library/Application Support/Neuman/client`.
- [x] `runtime_dir()` / `browse_socket()` — Linux keeps `XDG_RUNTIME_DIR`;
  Darwin uses `$TMPDIR`.
- [x] `find_chromium()` — Linux keeps `shutil.which`; Darwin checks the app
  bundle paths and falls back to `PATH`.
- [x] `browser_copy_source()` — Linux `~/.config/chromium`; Darwin
  `~/Library/Application Support/Google/Chrome`.
- [x] Keep `NEUMAN_CLIENT_STATE`, `NEUMAN_BROWSE_SOCKET` and
  `NEUMAN_BROWSER_CHROMIUM` as overrides.
- [x] Route `neuman_client/cli.py`, `neuman_client/tools.py` and
  `neuman_client/browser/cli.py` through the seam.
- [x] Tests for both backends with `sys.platform`/environment patched.

**Acceptance:** the Linux client's tests and behaviour are unchanged; the
Darwin backend is unit-tested; no macOS-only import breaks Linux.

## Phase 2 — macOS runtime

- [ ] `neuman-client login --server https://…` stores the session under the
  macOS state directory with `0600` (code in place; needs a Mac).
- [ ] `neuman-client run` serves the UI on loopback and writes `ui-url`
  (code in place; needs a Mac).
- [ ] `neuman-browser` finds Chrome, starts it with the Neuman profile, and
  bridges CDP over the `$TMPDIR` socket (code in place; needs a Mac).
- [ ] `browse` dials `NEUMAN_BROWSE_SOCKET`; no `XDG_RUNTIME_DIR` dependency
  (code in place; needs a Mac).
- [ ] `fs_read`, `fs_write`, `fs_edit`, `fs_list`, `fs_search`, `fs_image`,
  `git` and `shell` run on macOS (needs the real-Mac checklist).
- [ ] Decide and document the `python` tool: detect a project venv/uv/conda
  environment, or fall back to the client's bundled interpreter and say so.
  It is Nix/direnv-specific today and will not behave as on Linux.
- [ ] Check path handling on case-insensitive APFS: `realpath`, symlinks and
  case variants of the same path. Document any reach check that is weaker on
  macOS.
- [x] Add a `neuman-client doctor` that reports OS, state path, socket path,
  Chrome path, server reachability, and macOS TCC/Full Disk Access problems.
- [x] Document TCC: Desktop/Documents/Downloads consent and Full Disk Access
  for the LaunchAgent.

**Acceptance:** a terminal-run client on a Mac completes a full turn, executes
the structured tools, and drives Chrome. No `.pkg` yet.

## Phase 3 — verification checklist

Run this on a clean Mac account before packaging:

- [ ] `neuman-client login` against a remote HTTPS server.
- [ ] `neuman-client run` serves the UI; the browser opens the tokenised URL.
- [ ] Chat sends and receives; a turn completes.
- [ ] `fs_read`/`fs_write`/`fs_list`/`fs_search` on a granted path.
- [ ] `shell` and `git` run as the local user.
- [ ] `python` either resolves the project environment or reports the fallback.
- [ ] `browse` opens Chrome and a page; the CDP bridge survives a browser
  restart.
- [ ] Client restart reconnects and preserves the session.
- [ ] A path under `~/Documents` produces the expected TCC prompt rather than
  a silent permission failure.
- [ ] Killing the LaunchAgent/daemon leaves no orphaned browser or tool
  process.

**Acceptance:** the checklist passes on a real Mac; failures are recorded in
`docs/gotchas.md`.

## Phase 4 — packaging

- [x] Development payload: `neuman-client/packaging/dev_payload.py` builds a
  runnable layout and was exercised on Linux. It takes a Python 3.12+
  interpreter; it is not the relocatable `.pkg` layout.
- [ ] Bundle a relocatable Python runtime and the client's dependencies; the
  user must not install Python.
- [ ] Install client and browser wrappers under `/usr/local/bin` or an app
  bundle, and the Python payload under `/usr/local/lib/neuman-client`.
- [ ] Install the LaunchAgent and the UI URL/token layout.
- [ ] Build a signed `.pkg`; sign the payload and the installer; notarise and
  staple.
- [ ] Test install, first login, logout/login, upgrade over an existing
  install, and uninstall.
- [ ] Add `neuman-client doctor` to the installed payload.
- [ ] No auto-update in v1; document the manual upgrade path.

**Acceptance:** a clean Mac with no Python/Nix installed can install the
`.pkg`, log in, complete a turn, and browse.

## Phase 5 — Nix follow-up

Only after the macOS client works:

- [ ] Decide whether `neuman-client`/`neuman-tools` should build on Darwin
  under Nix for CI or a reference closure.
- [ ] If yes: change `platforms`, make Chromium optional/runtime-discovered,
  and add an `aarch64-darwin` build check.
- [ ] Keep `nix/module/client.nix` for Linux and the Neuman OS image; do not
  make it the macOS installer.
- [ ] Document the two build paths and the single source of truth for client
  code.

## Parallel track — server authority and package boundary

Not blocking macOS, but required for the product model agreed separately:

- [x] Remove the Nix policy seed once a setup flow exists (D68).
- [x] Make Postgres the unambiguous runtime policy authority; D68 already says
  this, so fix the module comments and option descriptions that contradict it.
- [x] Baseline export/import: `--json baseline show` and `baseline set` are
  the pair; the obsolete overlay-to-Nix export is gone.
- [x] Add role rename, so the database-authoritative panel can do what the Nix
  key could (UI, CLI, baseline, declared references and grants).
- [x] A first-run setup flow for providers and models: the server ships the
  presets and the panel offers them as tiles, a provider added with its key
  (D76, D77).
- [x] Update `docs/setup.md`, `README.md` and `docs/security-model.md` for the
  server/client product boundary and the platform matrix.

## Risks and open questions

- **Chrome remote debugging with an existing Chrome process.** A separate
  `--user-data-dir` should start an independent instance, but Chrome's
  single-instance behaviour needs a real test on macOS.
- **AF_UNIX path length.** `$TMPDIR` is usually short enough; verify on both
  Intel and Apple silicon.
- **Case-insensitive APFS.** Path reach checks may treat different casings as
  different strings. Decide whether to canonicalise on Darwin and document the
  weaker case.
- **TCC.** A LaunchAgent may inherit different privacy grants from a
  terminal-run process; test both.
- **Python tool semantics.** A bundled interpreter is not a project
  environment. Do not silently pretend it is.
- **Protocol skew.** Independent client installs make a client/server version
  handshake necessary; decide it before the first external install.
- **Signing identity.** Notarisation needs an Apple Developer account and
  network access; start the certificate work before Phase 4.

## Definition of done

A business Mac user can install a signed package, log in to the existing Linux
server, run a complete turn, use the structured tools and `browse`, and survive
a client restart — without Python, Nix, or a terminal. The Linux client still
passes its existing tests, and `docs/platforms.md` states exactly what is and is
not supported.
