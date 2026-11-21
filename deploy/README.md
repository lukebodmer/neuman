# deploy

Packaging for hosts that do not run NixOS. The reference build is the flake;
these artifacts are the same source, packaged for a distribution, and they
carry the same systemd unit so the sandbox the security model describes is not
optional (D107).

## ubuntu

`ubuntu/build.sh` produces `neuman_<version>_amd64.deb`, the server half of the
appliance for Ubuntu 24.04.

What it needs on the build host — a maintainer's machine, not the appliance:

- network access to PyPI;
- `python3` with `pip`, to build the wheels this repository's packages and to
  install them;
- `nfpm`, to write the deb.

It does not need the target's interpreter: the wheel install uses
`--platform`/`--python-version` and the deb is assembled from files.

`ubuntu/constraints.txt` pins the Python dependencies. It is generated from
the flake's `serverEnv` — the environment the test suite runs against — so the
deb ships the versions that were tested rather than a second resolution that
happens to share a major number. Regenerate it after a nixpkgs bump:

    nix develop -c python3 deploy/ubuntu/constraints.py > deploy/ubuntu/constraints.txt

The deb installs:

| Path | What |
|---|---|
| `/opt/neuman/lib` | the Python packages, including the generated contracts |
| `/usr/bin/neuman-{server,admin,runtime,fetch}` | wrappers that set `PYTHONPATH` and the build id |
| `/usr/share/neuman/registry` | tool manifests and packages (D3) |
| `/usr/lib/systemd/system/neuman-server.service` | the unit, with the module's sandbox |
| `/usr/lib/tmpfiles.d/neuman.conf` | `/run/neuman` and the backup directory |
| `/etc/neuman/server.env` | deployment settings — a conffile |

`postinst` creates the `neuman` user and group, the directories, and the
Postgres role and database; it is idempotent and is repeated by
`dpkg-reconfigure neuman` when Postgres was not running at install time.

**Not yet proven on its target.** Nothing in `nix flake check` exercises this
path: it runs on NixOS. The package has been inspected (control fields,
conffile, maintainer scripts, registry load, config-error exit 78) and imported
from its own tree, but the first install on a clean Ubuntu 24.04 host is still
the acceptance test. Until a container or VM test exists, treat that as the
gap it is.

The client is not in this package. The administration page is served by a
client, so until one connects the server is administered with `neuman-admin`
over the socket. Local models are not packaged either: `neuman-runtime` needs
`NEUMAN_LLAMA_SERVER`, which a distribution has to supply.

## ubuntu client

`ubuntu/build-client.sh` produces `neuman-client_<version>_amd64.deb`, the
client half, per user and with no root component (D108). It needs the same
tools as the server's build; its only PyPI dependency is `websockets`, taken
from the same `constraints.txt`.

The deb installs the client under `/opt/neuman-client` — a separate tree from
the server's `/opt/neuman`, so one machine can hold both packages — the tools
and their manifests under `/usr/lib/neuman-client` and
`/usr/share/neuman-client`, two **user** units, and a desktop entry. It
depends on `python3 (>= 3.12)` and `git`, and recommends
`chromium | google-chrome-stable` and `rsync`: the browser is the person's, and
only `browse` needs it (D108).

The user's steps after installing:

    neuman-client login --server https://appliance.example:8443 --user you
    systemctl --user daemon-reload
    systemctl --user enable --now neuman-client.service
    systemctl --user enable --now neuman-browser.service

`login` stores the server in `session.json`, so neither unit needs a per-user
config file. `neuman-client doctor` is the acceptance check.

**Not yet proven on its target**, and more so than the server: a container can
prove install, login, the daemon and a tool dispatch, but `browse` and the app
window need a display — Xvfb with a real Chromium, or an Ubuntu desktop VM.
