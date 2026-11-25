# Platforms

This file states support, not intent. An unsupported host is not a promise with
a date on it; it is a host the code has no backend for.

## Support matrix

| Component | Linux | macOS | Windows |
|---|---|---|---|
| `neuman-server` | Supported (NixOS module; Ubuntu 24.04 `.deb`) | Not supported | Not supported |
| `neuman-admin` | Supported over the local socket | Not applicable (server is Linux) | Not supported |
| `neuman-client` | Supported (Nix/home-manager; Ubuntu 24.04 `.deb`) | In progress: Apple silicon, macOS 14+ | Not supported |
| Neuman appliance image | Supported (NixOS) | Not applicable | Not applicable |

`neuman-server` is deliberately Linux-only. A macOS or Windows host that needs
a server can run the Linux service in a container; the client is the part that
runs on the human's machine.

## Server hosts

### NixOS

The reference install: the flake's module, described in `setup.md`. It owns the
packages, the unit and its sandbox, the directories, the database and the
operator group.

### Ubuntu 24.04

The same server as a `.deb`, installed and updated from the Neuman apt
repository — or, on a machine that cannot reach it, from the download page. The
package carries the same systemd unit as the NixOS module, so the sandbox is not
something the installer chooses, and its Python dependencies are pinned to the
versions the suite runs against (D107).

```bash
# once: the key, then the source. The fingerprint must match the one the
# release notes publish; a key fetched from the same host as the packages is a
# starting point, not a check.
curl -fsSL https://<host>/neuman/neuman-archive-keyring.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/neuman-archive-keyring.gpg
sudo install -m 0644 /dev/stdin /etc/apt/sources.list.d/neuman.sources <<'EOF'
Types: deb
URIs: https://<host>/neuman
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/neuman-archive-keyring.gpg
EOF

sudo apt update && sudo apt install neuman
sudo usermod -aG neuman "$USER"     # then log out and back in
neuman-admin bootstrap --name you
```

`deploy/ubuntu/repo.sh` writes that repository from the built `.debs`. It is
also the rollback archive: every release stays in its pool, so
`apt install neuman=<version> --allow-downgrades` names the way back without a
file to find.

- 24.04 only, for now: the package is built for its Python 3.12 and depends
  on `python3 (<< 3.13)`. 22.04 ships 3.10, and releases since 25.04 ship 3.13
  or newer, whose interpreter cannot load the bundled extension modules — so
  `apt` refuses rather than installing a server that cannot start. Each Ubuntu
  release needs its own build (D109).
- The package creates the `neuman` user and group, the directories and the
  Postgres role and database. If Postgres was not running at install time,
  `sudo dpkg-reconfigure neuman` repeats the provisioning.
- **The administration page is the client's.** A server with no client
  connected is administered with `neuman-admin` over the socket; the page
  appears when a client connects.
- Deployment settings — bind, port, TLS, paths — are in
  `/etc/neuman/server.env`, a conffile. Accounts, roles, providers, models and
  the prompt are settings of the appliance, made in the administration page.
- Local models are not in the package: `neuman-runtime` needs
  `NEUMAN_LLAMA_SERVER`, which means a `llama-server` the distribution supplies.

**Upgrading.** `apt` is the whole mechanism (D111); the two commands above
already installed it.

```bash
neuman-admin backup /var/lib/neuman/backups/pre-<version>-$(date +%F)
sudo apt update && sudo apt install --only-upgrade neuman
neuman-admin status
```

`status` prints the build and the schema pair, and `applied == expected` is the
check. The new release applies the migrations it carries as the server starts —
one transaction each, forward-only (D103) — so a migration that fails leaves the
schema exactly as it was and the previous package starts again with nothing to
restore. A release that *did* migrate is a different case: install the old
package and it refuses to start against a schema it does not know, so the way
back is the backup first and the package second. That refusal is deliberate; it
is the sentence that replaces a silent mismatch.

The server is **not** taken by unattended-upgrades, and the package says so in
`/etc/apt/apt.conf.d/51neuman-server`. The undo for a migration needs a backup, a
backup needs a Neuman administrator account, and root is not one — so an
appliance that migrated at 3am to a release with a bug would need a person,
awake, holding an account. The cost is that Neuman's own fixes arrive when the
operator installs them. The client is the other half of that rule.

## Client hosts

### Linux

- State: `~/.local/share/neuman/client`.
- Browser bridge: `$XDG_RUNTIME_DIR/neuman-browse.sock`, mode `0600` in the
  user's `0700` runtime directory.
- Browser profile: `~/.local/share/neuman/browser`, or
  `~/snap/chromium/common/neuman-browser` when the browser is Ubuntu's snap,
  which reaches only non-hidden paths under `$HOME` (D110); either can be
  overridden with `NEUMAN_BROWSER_PROFILE`. `--profile copy` seeds from the
  user's own profile — `~/.config/chromium`, the Chromium snap's
  `~/snap/chromium/common/chromium`, or `~/.config/google-chrome`, the first
  that exists, or `--copy-source`.
- Service hint: `systemctl --user status neuman-client`.
- Two install paths: the Nix `programs.neuman-client` module, and the Ubuntu
  24.04 `.deb` below.

#### Ubuntu 24.04

The client as a `.deb`, from the same repository as the server (or the download
page, on a machine that cannot reach it). Per user, with no root component and
no bundled browser (D108):

```bash
sudo apt install neuman-client
neuman-client login --server https://appliance.example:8443 --user you
systemctl --user daemon-reload
systemctl --user enable --now neuman-client.service
systemctl --user enable --now neuman-browser.service
```

- `login` stores the server in `session.json`; neither unit needs it, so there
  is no per-user configuration file.
- Built for 24.04's Python 3.12, and depends on `python3 (<< 3.13)` for the
  same reason as the server package (D109).
- The browser is the person's: `chromium`, `chromium-browser`, `google-chrome`
  or `google-chrome-stable` on `PATH`, or `NEUMAN_BROWSER_CHROMIUM`. Only the
  `browse` tool needs it, so it is a `Recommends`. `chromium` on Ubuntu is the
  snap, which reads only non-hidden paths under `$HOME` and opens no DevTools
  port until `network-bind` is connected: Neuman puts that browser's profile
  inside the snap's own directory (D110), and the postinst names
  `sudo snap connect chromium:network-bind` because `browse` cannot work
  without it. Google Chrome's `.deb` needs neither step, and is what the
  documentation suggests where the snap is not already connected.
- The DevTools endpoint is loopback TCP with no authentication. The `0600`
  socket gates the tool, not the machine's other local accounts, so this
  assumes every account on the machine is trusted as the user is.
- `neuman-client doctor` reports the interpreter, the client's release and
  build id, the appliance's release, the session, the browser, the profile and
  the socket length; a clean exit is the first acceptance check.

**Updating.** The client updates itself, and nobody has to coordinate it
(D111). `apt` installs a new `neuman-client` through unattended upgrades — the
package adds `Neuman:stable` to `Unattended-Upgrade::Allowed-Origins` in
`/etc/apt/apt.conf.d/51neuman-client`, and the server package blacklists itself
in the file beside it. Nothing needs to be run: the files are replaced, the
running client notices its build id changed and exits into the new code at its
next idle moment — never mid-job — and the window reconnects on its own.

When the appliance refuses this client because it speaks a shape the appliance
no longer does, the window says so: the two versions, which one is older, and
`sudo apt update && sudo apt install --only-upgrade neuman-client` with a copy
button. It is the same sentence `doctor` prints.

A machine installed from a downloaded `.deb` with no repository configured does
not update itself; the banner and `doctor` still name the command, and the
download page is the way to a newer file.

### macOS

- Apple silicon only. Minimum macOS 14 (Sonoma).
- State: `~/Library/Application Support/Neuman/client`.
- Browser bridge: `$TMPDIR/neuman-browse.sock`, mode `0600` in the user's
  per-session temporary directory. AF_UNIX path length is checked on Apple
  silicon when the package is built.
- Browser profile: `~/Library/Application Support/Neuman/browser`;
  `--profile copy` seeds from `~/Library/Application Support/Google/Chrome`.
- Chrome or Chromium must be installed in `/Applications` or
  `~/Applications`. The first package does not bundle a browser.
- Service hint: `launchctl print gui/$UID/dev.neuman.client`.
- The install artifact is a signed and notarised `.pkg` with a user
  LaunchAgent. It is not built with Nix on the customer's machine.
- macOS TCC applies: Desktop, Documents and Downloads need the user's consent;
  a LaunchAgent may need Full Disk Access for paths a terminal-run process can
  already reach. A `$HOME/Documents` grant can be legal in Neuman and still
  refused by macOS until that consent exists, so the client reports it as the
  OS's refusal rather than as a policy refusal. `neuman-client doctor` reports
  what it can see.

## What differs by platform

The principle that the client runs as the human is the same on both hosts. The
mechanisms underneath it are not:

- POSIX file modes and ACLs differ. Path reach is a string check in the client,
  not a kernel confinement, on both hosts.
- APFS is case-insensitive by default. A path with different casing can name the
  same file; the macOS release must not claim a stricter reach check than it
  implements.
- `shell` is the host's shell. On Linux it is `sh`; on macOS it is `sh` as well,
  but the installed tools and defaults differ. The capability is the same; the
  commands are not.
- The `python` tool's project-environment discovery is Nix/direnv-based today.
  macOS support needs venv/uv/conda detection or an explicit fallback to the
  bundled interpreter.

## Windows

No client and no server. The client's process supervisor, runtime transport,
shell and path policy would all need a separate design; none of them is
implemented by pretending POSIX semantics exist on Windows.
