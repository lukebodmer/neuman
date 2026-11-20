# Networking

Every place this appliance talks to something — process to process, and process
to the outside. `docs/security-model.md` states what each link is allowed to carry;
the decisions behind the shapes are `D1`, `D9`, `D20`, `D31`, `D56`.

There are two categories, and they are deliberately different:

- **The appliance's own links** are Unix sockets and loopback. One port can face
  a network — the session surface — and putting it on an interface is an
  operator's explicit choice rather than a default.
- **The client link is that port.** The client, and the browser behind it, reach
  the core over HTTP and a WebSocket per branch the window has open.
- **Outbound traffic from a tool runs on the client**, on the user's own
  network. The appliance's own outbound path is the model gateway.

## The map

| Link | Address | Who → whom | Protection |
|---|---|---|---|
| Session surface | TCP `bind:port`, default `127.0.0.1:8443` | the client, and an operator's tooling → server | TLS (optional at the server, a proxy may terminate), session cookie, CSRF and Origin per unsafe request |
| Client channel | WebSocket `bind:port/client` | client → server | the same session cookie, re-checked on the upgrade; the account is the session's, and `hello` carries the tools and the client's home |
| Client UI | TCP `127.0.0.1:8642`, and one WebSocket per open session through it | the user's browser → client | loopback only, a per-install token cookie, same origin as the executor, and the core's cookie never reaches the browser |
| Admin | Unix `/run/neuman/admin/admin.sock` | operator CLI → server | mode `0660`, group `neuman`; authority re-checked in the core |
| Browse / CDP | Unix socket in the user runtime directory: `$XDG_RUNTIME_DIR` on Linux, `$TMPDIR` on macOS | the client's `browse` tool → the user's Chromium | mode `0600` in the user's `0700` runtime directory; both ends are on the user's machine |
| Tool package host | Unix `$XDG_RUNTIME_DIR/neuman-<package>.sock` | a package's tools → its host, e.g. the add-on inside the user's Blender | mode `0600` in the user's `0700` runtime directory, set by the umask at `bind`; both ends are on the user's machine |
| Model, in use | loopback TCP, `runtime.chatPort` (8081) | server → the chat router in `neuman-runtime` | loopback; a local model is the only place a conversation does not leave the machine |
| Runtime control | Unix `/run/neuman/runtime/control.sock` | server → `neuman-runtime`: status, load, unload | mode `0660` in a `0750` directory; names a model, never a unit, flag or file |
| Weights | Unix `/run/neuman/fetch/control.sock`, then HTTPS out | server → `neuman-fetch` → the Hugging Face Hub | the socket as above; the request names a preset, and the bytes are checked against its pinned hash |
| GPU sample | a file under `/run/neuman/gpu`, not a socket | sampler → server | file mode; no network |

## The session surface

The one port that faces anything is `services.neuman.bind:port` — `8443` by
default, bound to `127.0.0.1` by default. It is the untrusted tier: the surface
is a public API and every request is authenticated, authorized and
origin-checked, so exposing it is a decision an operator makes rather than one
the module makes for them.

- **TLS** is set with `services.neuman.tls.certificate`/`key`. Without it the
  server still starts and warns. A reverse proxy may terminate TLS instead.
- **Sessions** are a cookie whose server-side record is keyed by the hash of the
  token (`docs/database.md`, `auth_sessions`). The cookie is `Secure` and
  `HttpOnly`.
- **CSRF and `Origin`** are re-checked on every unsafe request and on the
  WebSocket upgrade, because browsers do not apply CORS to WebSockets. This is
  why the surface can be on loopback and still be treated as hostile.

On the same machine as the server, loopback plus a `Secure` cookie works: a
browser treats `http://127.0.0.1:8443` as a secure context, and so does a
client. A core on another machine needs TLS or a reverse proxy in front, and
the client enforces that rather than warning: `http://` is accepted for
loopback only, because a browser refuses to send a `Secure` cookie over plain
HTTP but a daemon has no such notion — it would send the cookie, and the
password before it.

The browser never reaches this port. It talks to the client's UI on loopback,
and the client makes the API calls with the session it holds — so the browser
sees one origin, and the core sees one credential either way.

**The browser's WebSocket is per open branch.** The UI server proxies
`/ws/branches/{id}` to the core, and the page holds one for each branch it has
open — several at once while more than one turn is running. Each upgrade
carries the same cookie and is Origin-checked like the first. A session keeps
its socket for as long as it is running a turn, blocked on an approval, or
holding a reply the reader has not seen; switching sessions moves the transcript
rather than closing the connection (D63). Closing the window closes all of them
together, which is what keeps the core's rule that a vanished client cannot
leave a turn burning budget true. A second connection to a branch that already
has a turn running is answered `turn_in_flight` and starts nothing: one branch
runs one turn at a time whatever the number of sockets, because a second turn
would write into the same append-only log while the first is mid-dispatch
(D63, N179).

**The rail's working-set read is a poll, not a held request.** `GET /api/active`
goes through the same `/api` forward, which the client performs on a worker
thread per request (`ui.py`): a request held open as a long-poll would pin one
thread per window for as long as the window exists, and the page is meant to be
opened several times. So it asks every four seconds while it is in front and not
at all while it is hidden. The answer is scoped to the caller and names, per
session, the state (`running`, `approval`, `finished` or `idle`), the branch the
work is in, the tool a verdict is waiting on, and the activity that orders the
row — not the arguments or the preview, which are asked for separately and only
when a window actually draws the card. Opening a session writes the account's
read watermark (`POST /api/sessions/{id}/read`), which is what clears `finished`
in the other windows.

**Two `/api` routes are answered by the client, not forwarded.** `GET /api/jobs`
and `POST /api/jobs/{id}/stop` list and stop the background jobs the daemon
holds (D98), which exist only in that process: forwarding them would ask the
core about state it cannot see. The same loopback cookie is the credential, and
a stop is additionally Origin-checked — that route sits in front of the core's
middleware rather than behind it, so the check the core would have made has to
be made there (`ui.py`). The page reads it on an event (a `jobs` frame, the end
of a turn, the tab becoming visible), so this is less a poll than the rail's.

`POST /api/drops?name=` takes a file dropped on the window as the raw body,
writes it to the daemon's `drops/` directory and answers with its path (D102).
The bytes stay on the machine the browser is on; only the path goes to the core,
in the ordinary attachment PATCH. Same loopback cookie and Origin check as a
stop, and a body over 25 MiB is refused on its `content-length` before any of
it is read — for every route, since the daemon holds a proxied body in memory
whole. The daemon then asks the core `GET /api/attachments` — forwarded with
the session like any other read — for every path the account still attaches,
and deletes the drops nothing names.

**A blocked call's card is a second read on the same link.**
`GET /api/branches/{id}/pending` returns the tool, the arguments and the preview
for each call the branch's turn is waiting on, scoped exactly like the branch it
names. The preview is the one computed for *that* call — resolving one is a
dispatch to the human's machine, so serving a fresh one would describe a write
that may no longer be the write the verdict applies to. It is held with the wait
and never written to the log. The verdict itself is unchanged in where it goes:
the branch's inbox, which any socket on the branch can post to (D63).

## The client link

A client is an API consumer with a session. It logs in over `bind:port`, keeps
the session cookie, and holds a WebSocket at `/client` for the dispatch RPC. One
credential covers both, so the core never needs a second way to know who is
acting (D56).

The upgrade carries the cookie, and the account is the session's. The client's
`hello` names the tools it can run and the home directory on the machine it runs
on — the last because the core cannot look it up, and a `$HOME` policy entry it
expanded against the wrong host would name directories that do not exist where
the tool runs (N145). It carries no account name: the local account that holds
the process is the executor, not the principal, and it need not be named like the
account on the appliance (D67). The core intersects the tool list with the
catalog and the account's grants (D57). Identity is Neuman's — an argon2id
check against the account row at login — and over this link it travels as a
session rather than as a uid, because the two ends are on different machines
now.

The channel is stateful and sequence-checked: `started`, `stdout`, `stderr` and
`result` carry a sequence number, and a gap is a bug or an attack rather than
something to paper over. It is also the only way tools run at all — a dispatch
with no client connected for that account is refused, not queued (D26).

**A task run is dispatches on this same link.** No task socket exists: the
server's scheduler walks the recipe, sending each run step as a `shell`
dispatch, each agent step's tool calls as a turn's dispatches, and the run
record as an `fs_write`, and the client executes them exactly as it does for a
browser-started turn. If no client is connected, the run waits rather than
opening a second route (D94, D96, D97). The browser follows a run by polling
the core's `/api/tasks` routes through the client daemon like any other `/api`
call; there is no task stream to the browser either (D99).

## Why almost everything else is a Unix socket

A Unix socket has a mode, and a mode is an access control the kernel enforces. A
loopback TCP port does not: `127.0.0.1:9222` is reachable by every process on
the machine, whatever user it runs as. Since several accounts share one
appliance, the internal links are sockets whose ownership is the boundary.

- **There is no root component.** Checking a password is an argon2id verify
  against the account row; nothing reads `/etc/shadow` and nothing is
  socket-activated as root.
- **The admin socket** is `0660 neuman:neuman`, in a `0750` directory. Reaching
  it is not the same as being allowed to use it: the peer uid is resolved to a
  name and that name must be in `neuman-admin` in Neuman's own account store,
  checked inside the core operation both surfaces share (D9).

The **client link** is deliberately not a socket. A socket identifies a uid on
one machine, which is exactly what a client on another machine cannot offer, so
the identity that crosses the network is a session Neuman issued and the core
recorded (D56).

## The browse link

Neuman never starts a browser, and the appliance is not involved at all.
`neuman-browser` runs on the acting user's machine, starts a real Chromium, and
bridges its Chrome DevTools Protocol endpoint onto a Unix socket in the user's
runtime directory — `$XDG_RUNTIME_DIR/neuman-browse.sock` on Linux,
`$TMPDIR/neuman-browse.sock` on macOS. The `browse` tool runs on the same
machine and dials that path: the client sets `NEUMAN_BROWSE_SOCKET` on every
tool it runs, so the two ends come from one value instead of two defaults that
drift.

**CDP is not authenticated.** Whoever can reach the endpoint can drive the
browser, run JavaScript in any page, and read every cookie the profile holds.
That is why the link is a socket with a mode (`0600` inside the user's `0700`
runtime directory), and why the tool only speaks TCP when it is pointed at a
port explicitly. Nothing sets that for you.

The profile is the client's choice (`own` by default, `copy` to seed the user's
real cookies), and the browser's outbound traffic is the user's own network, not
the appliance's.

## Tool package hosts, and the Blender link

A tool package may have a host: a program in a window the person works in, which
the package's tools reach over `$XDG_RUNTIME_DIR/neuman-<package>.sock` (D101).
The client starts the host when one of those tools is dispatched and nothing
answers there, and names the same path to the host and to the tools in
`NEUMAN_HOST_SOCKET`. Both ends are on the user's machine; the appliance is not
involved.

Blender's is the first. The same shape as the browse link, with one hop fewer:
the host starts the user's Blender with Neuman's add-on as its startup script,
and the add-on itself binds `neuman-blender.sock`, so there is no TCP port
anywhere and nothing to bridge. The protocol is one line of JSON each way per
connection (D100).

**Whoever reaches the socket runs Python as the user.** `exec` is exactly that,
so the socket's mode is the whole access control: `0600` inside the user's
`0700` runtime directory, applied by the umask at `bind` so it never exists any
wider. The published Blender integrations listen on loopback TCP with no
authentication, which every process on the machine can reach; this is the
change from them that matters.

Blender's own outbound traffic — an add-on of the user's that checks for
updates, an asset library — is the user's network, as the browser's is.

## The local models and the decoration around them

A model on this machine is served by `neuman-runtime`, which runs llama.cpp's
router on loopback (`runtime.chatPort`, 8081). The server sends a conversation
there the way it sends one to a provider, with no key. Nothing about this link
leaves the machine, and that is the point: a conversation with a local model has
no third party to be sent to, which is the strongest configuration the appliance
offers (`docs/security-model.md`, where the boundaries are).

The router loads a model on the first request that names it, and the request
waits through the load rather than failing. It is started with an empty home and
cache, because with a real one it also offers whatever models are cached there,
each with a download argument (N169). The runtime unit may reach loopback and
nothing else.

Weights arrive through `neuman-fetch`, the one unit with outbound access besides
the server's model gateway. It is told a preset's name and nothing else; the
host, repository, commit and hashes are the release's.

The GPU stays out of the server (D30). A sampler writes a JSON file, and the
server reads a file — no device handle, no socket, no protocol.

## Outbound from a tool

A tool runs on the client, on the user's own network, and the appliance has no
say in it. `shell`, `python`, `web_fetch`, `git`, `browse` and `blender` (through
the Python it runs inside Blender) all reach whatever the machine can reach,
exactly as the human's own programs do (D55). There is no allowlist to hold and
nothing to hold one: a host list that no proxy enforces is a limit in the
configuration that is not a limit on the wire (N146).

That is the deliberate cost of the rearchitecture, and it is stated at the top
of `docs/security-model.md` rather than implied away: **exfiltration is an approval
problem, not a network problem.** What the core decides is whether a
network-declaring tool is *offered* — a `needs = ["network"]` tool is withheld
from an account whose policy grants no network — and what stands between a
request and the wire after that is the verdict that let the call run.

The appliance's own outbound path is the model gateway: the server connects to
the configured provider endpoint (or to a local runtime on loopback), and the
credentials are a file path read at call time — a `0600` file the server wrote
when an administrator entered the key (D77), or one a secret manager placed.
A custom provider must be `https://` unless it is on this machine, because the
key crosses on every request. That link is the server's, is
not proxied, and is the one place a conversation can leave the building — which
the security model states.

## What is and is not a boundary here

- **Loopback is not a security boundary between users.** Every process on the
  machine reaches a loopback port. Modes on Unix sockets and the peer credential
  are what separate accounts.
- **A Unix socket is not automatically safe.** `0660` is only meaningful if the
  group is right, and a world-connectable socket is fine only when something
  behind it checks identity — the admin socket does, and the mode does not.
- **TLS protects the session surface, not the internal links.** The internal
  links are already inside the machine; their protection is ownership and the
  peer check.
- **The session cookie protects the client link.** It is the same credential the
  API uses, over the same port, and the account it resolves to is the whole of
  the client's authority.
- **Nothing protects client egress.** The client is the human's machine and has
  the human's network.
