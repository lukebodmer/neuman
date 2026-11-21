# Platforms

This file states support, not intent. An unsupported host is not a promise with
a date on it; it is a host the code has no backend for.

## Support matrix

| Component | Linux | macOS | Windows |
|---|---|---|---|
| `neuman-server` | Supported (NixOS module; Ubuntu 24.04 `.deb`) | Not supported | Not supported |
| `neuman-admin` | Supported over the local socket | Not applicable (server is Linux) | Not supported |
| `neuman-client` | Supported (Nix/home-manager today) | In progress: Apple silicon, macOS 14+ | Not supported |
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

The same server as a `.deb`, from the download page. The package carries the
same systemd unit as the NixOS module, so the sandbox is not something the
installer chooses, and its Python dependencies are pinned to the versions the
suite runs against (D107).

```bash
sudo apt install ./neuman_<version>_amd64.deb
sudo usermod -aG neuman "$USER"     # then log out and back in
neuman-admin bootstrap --name you
```

- 24.04 is the baseline: it ships Python 3.12, which is what the server
  requires. 22.04 is not supported.
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

## Client hosts

### Linux

- State: `~/.local/share/neuman/client`.
- Browser bridge: `$XDG_RUNTIME_DIR/neuman-browse.sock`, mode `0600` in the
  user's `0700` runtime directory.
- Browser profile: `~/.local/share/neuman/browser`; `--profile copy` seeds from
  `~/.config/chromium`.
- Service hint: `systemctl --user status neuman-client`.
- The Nix `programs.neuman-client` module is the current install path.

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
