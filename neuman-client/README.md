# neuman-client

The executor, the chat UI and the browser, running on the machine the acting
human is typing at. It holds no policy authority: it receives dispatches that
were already gated and approved, runs them as the local account the client
process runs under, and reports what happened (D55, D67).

## Public surface

| Entry point | What it is |
|---|---|
| `neuman-client login` | Authenticate once as the account on the core and keep the session. `--user` names that account; `--password-file` for a service. |
| `neuman-client run` | The default. Holds the client channel, serves the UI on loopback, runs dispatches. |
| `neuman-browser` | Starts the user's Chromium and bridges its CDP endpoint onto a Unix socket `browse` dials. |
| `programs.neuman-client` | The Linux home-manager module (`nix/module/client.nix`): both services, the desktop entry, the paths. |
| macOS `.pkg` | The native Apple-silicon client; signed and notarised, not built with Nix on the customer's machine (in progress). |

The host support matrix is `docs/platforms.md`.

## The channel

The client is an API consumer with a session. It logs in over the session
surface and holds a WebSocket at `/client` for the dispatch RPC, authenticating
with the cookie from that login — one credential for the API and the channel
(D56). The daemon reconnects with backoff for as long as it runs, and a
disconnect fails the dispatches in flight rather than leaving them hanging: a
dispatch is answered by the client that was connected when it was sent.

A remote core must be `https://`. The client accepts `http://` for loopback only,
and refuses anything else at login and at startup: the password crosses first and
the session cookie crosses on every request after it, and both would be readable
by anything on the path. Loopback is not a network, so the same-machine case needs
no certificate.

`hello` names the tools this installation can run and the home directory on this
machine — the last because the core cannot look it up: the one it can see belongs
to its own host, and a `$HOME` policy entry expanded against the wrong host names
directories that do not exist here (N145). It names no account: the session is the
account, and the local account that runs the tools need not match it (D67). The
core intersects the list with the catalog and the account's grants. A tool the
core dispatches that this client cannot resolve answers `unknown_tool`, which is
a bug on the core's side — the advertisement is what keeps it from being asked.

## Running a dispatch

A dispatch names a tool, its arguments, a wait, a lifetime, the account's
surfaces and — when it may become a job — its session. The tool host resolves
the executable from the manifests it was packaged with — a binary with no
manifest is unreachable however executable it is (D5) — checks each declared
path argument against the surfaces with `realpath`, and runs it. Output is
streamed back in sequence and bounded: past a small preview it is spilled to a
file the read tools can reach, up to the manifest's cap (D42, D47).

The path check is a guardrail against a confused or injected model, not a
boundary: it covers the structured tools, which are the quiet path, and `shell`
and `python` reach whatever the user reaches.

A tool in a package (D101) is checked for the program its package `requires`
first: without it on the client's PATH, the dispatch is refused as
`missing_program`, naming the program and where to get it. A package with a
`host` has it started (`hosts.py`) when nothing answers on its socket — Blender,
opened in its own session and left running — and its output goes to
`hosts/<package>.log` in the state directory.

## Background jobs

A process still running at its wait, with lifetime left, a session to belong to
and one of four slots free, is kept rather than killed (D98): the dispatch ends
`detached` with a short job id, the output goes on to the spill files, and the
end is reported later as `job_settled`. `job_output` and `job_kill` are answered
by the daemon itself from the jobs it holds (`jobs.py`), and find only the
asking session's jobs. A look returns only output the model has not been shown,
and only its end — what it returns is re-sent with every later request, so it is
kept small on purpose. Jobs die with the connection, like everything in flight.

The person gets the same view: `/api/jobs` lists what is running and
`POST /api/jobs/{id}/stop` kills one, and the client answers those itself rather
than forwarding them — the jobs exist in this process, so the core could only be
a slower copy (see **The UI** below).

## The UI

The client serves the chat window (`web/`) on `127.0.0.1:8642` and proxies `/api`
and `/ws` to the core with the session it holds, so the browser and the executor
share one origin, the core's cookie never reaches the browser, and the browser
needs no certificate to reach a remote core. The browser authenticates to the
client with
a per-install token: `run` writes the tokenised URL to `ui-url` once the UI is
listening, and `neuman-browser` opens it. The module's browser unit waits for
that URL to answer before it starts, so a leftover file from a previous run
cannot send the browser to a dead page (N183).

The proxy forwards `x-csrf-token` and nothing else of the browser's request
headers. The core re-checks CSRF per unsafe request, so a proxy that dropped it
would turn every write into a 403 that looks like a policy refusal.

Two exceptions to the forward. The running-jobs row: `/api/jobs` and
`POST /api/jobs/{id}/stop` are answered by the daemon from the jobs it holds
(D98). And a file dropped on the window: `POST /api/drops?name=` writes the body
(25 MiB at most) to `drops/` and answers with the path, which the page then
attaches like any other (D102). Both take the same loopback cookie, and the two
POSTs are Origin-checked here because these routes are in front of the core's
middleware rather than behind it.

**The administration page is the same origin.** `/admin.html` is a separate
view served by the same client: accounts, groups, the runtime baseline and
prompt, role grants, sessions, usage, the audit chain and operational events.
It talks to `/api/admin/*`, which the client proxies with the session it already
holds; the core re-checks the administrator group on every route. The link,
**Manage Neuman**, is at the foot of the chat window's rail, drawn only when
`/api/me` says the account is an administrator. A
temporary password must be changed under **Settings → Account** before any
other route answers.

## The browser

`neuman-browser` starts a real Chromium — there is no headless mode, because
headless is blocked per-site unpredictably and a capability that fails after
someone relies on it is worse than none (D31) — and bridges its DevTools port
onto the user's runtime socket: `$XDG_RUNTIME_DIR/neuman-browse.sock` on Linux,
`$TMPDIR/neuman-browse.sock` on macOS. The socket is `0600` in a `0700`
directory; its mode is an access control a loopback port is not, and CDP carries
no authentication of its own.

On Linux Chromium is found by name. On macOS it is found in `/Applications` or
`~/Applications`; `NEUMAN_BROWSER_CHROMIUM` names an explicit executable.

The profile is the security-relevant choice: `own` (the default) belongs to
Neuman, so a page that injects the model acts with none of the user's logins;
`copy` seeds it from their real profile once at startup.

The client sets `NEUMAN_BROWSE_SOCKET` on every tool it runs, so `browse` and
the bridge come from one value rather than two defaults that drift.

## State

Under the host's state directory — `~/.local/share/neuman/client` on Linux,
`~/Library/Application Support/Neuman/client` on macOS — all of it private to
the account:

| File | What it is |
|---|---|
| `session.json` | The session cookie and the core's URL, `0600` from creation |
| `ui-token` | The browser's token for the local UI, `0600`, stable across restarts |
| `ui-url` | Where the UI is, token included — written once the UI is listening, and what `neuman-browser` opens |
| `spill/` | Tool output too large to stream, named by dispatch id (D42) |
| `drops/` | Files dropped on the chat window, by content hash; deleted once no conversation attaches them (D102) |

## Environment

The wrapper sets what the package knows; the rest is a flag.

| Variable | Meaning |
|---|---|
| `NEUMAN_CLIENT_MANIFESTS` | The manifest directory (the registry store path) |
| `NEUMAN_CLIENT_BIN` | The tool executables |
| `NEUMAN_CLIENT_WEB` | The static files the UI server serves |
| `NEUMAN_CLIENT_SERVER` | The core to connect to, when `--server` is not given |
| `NEUMAN_CLIENT_STATE` | Where the state lives, when `--state-dir` is not given |
| `NEUMAN_BROWSE_SOCKET` | The browser bridge's socket; set on every tool the client runs |
| `NEUMAN_BROWSER_CHROMIUM`, `NEUMAN_BROWSER_RSYNC` | Substituted into `neuman-browser` at build time |

## macOS development payload

Before the signed `.pkg` exists, a Mac with Python 3.12+ can build the same
runtime shape from the checkout:

```
python3 packaging/dev_payload.py --out /tmp/neuman-client
/tmp/neuman-client/bin/neuman-client doctor
/tmp/neuman-client/bin/neuman-client login --server https://neuman.example.com --user you
/tmp/neuman-client/bin/neuman-client run
```

The script creates a private venv, the tool wrappers, the manifest set and the
static UI. It is a development harness; the signed package uses a relocatable
target tree and a bundled interpreter instead.

## Testing

```
cd neuman-client && python3 -m pytest tests -q
```

`test_web.py` is the chat window's own suite: it reads `web/` as text and
asserts the rules that are invisible when they break — a control that stops
dismissing, a palette that drops below legible, a transcript that clips.
Everything else here runs against a stub core; the appliance VM test logs in
and completes the handshake against the real one (`checks.vm-appliance`), and
the demonstration image runs the UI, the proxy and a real dispatch end to end
(`checks.vm-iso`).
