# neuman-admin

The operator's CLI for a running appliance. One command per admin route, over
the Unix socket.

## Public surface

- **`neuman-admin`** — the executable. `--help` on any subcommand.
- **`neuman_admin.client.AdminClient`** — requests over the socket, if
  something needs to script this in Python rather than shell out.

## Private surface

`format.py`. How a response is printed is not a contract; `--json` is.

## Why it is a separate part

A different trust position. The server is a long-lived daemon holding the
database pool and the sessions; this is a short-lived process an operator runs
and exits. It shares no code with the server — only the wire shapes of the
admin routes — so it installs on a machine that does not run one, and nothing
it does can affect a running appliance except through the socket.

It also decides nothing, and must not. The checks that matter live in the
decision core where both surfaces share them (D9). A CLI that validated policy
itself would be a second implementation of the rules, and two implementations
of an authorization rule is how one of them ends up wrong.

## Authentication

There isn't a step. The kernel tells the server which uid opened the socket
(`SO_PEERCRED`), and the server resolves it to an account. The CLI's
credential is the process it runs as.

That is the whole reason admin is a socket rather than a port. A token would
have to be stored somewhere, and anything stored can be copied off the
machine; a uid on a connection cannot be. It also means this has no
configuration beyond the socket path — nothing to get wrong, nothing to leak.

Reaching the socket needs membership in the `neuman` group
(`services.neuman.operators`). That is reachability, not authority — two
separate checks, and the difference is visible in the exit code:

| Situation | Result |
|---|---|
| Not in `neuman` | `69` — cannot open the socket at all |
| In `neuman`, no operator group | `1` — connected, then `forbidden` |
| In `neuman` and `wheel` (or `neuman-admin`) | the command runs |

Authority comes from `OperatorPolicy`, which reads group membership through
the directory inside the core operation, so the socket and the HTTP surface
cannot disagree about who is an operator (D9).

## Restore

`neuman-admin restore <dir>` replaces the appliance's state with a backup's.
It is the most destructive command here and the only one whose *success* needs
reading carefully: a restore can load cleanly and still produce a record that
is not intact, so the output leads with the chain verification rather than
burying it under the parts that worked.

Restore refuses before writing anything if the backup is not one this
appliance can read — a missing manifest, a dump the manifest names that is not
there, an overlay file that vanished. That matters more than it looks: a
restore that fails *halfway* leaves an appliance in a state neither backup
describes, and there is nothing to roll back to.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The server answered, and the answer was success. |
| `1` | The server refused — bad request, denied, not found. It said why. |
| `2` | Usage error, from argparse. |
| `69` | `EX_UNAVAILABLE`: the socket is not there. |

The last two rows are the point. An operator told "forbidden" checks their
authority; an operator told "unreachable" checks the service. Conflating them
costs an afternoon, so `69` also prints the two likely causes — the server is
not running, or this account is not in the `neuman` group.

## Output

Human-readable by default, `--json` for the server's response verbatim. The
default exists because an operator reading an audit chain as raw JSON is doing
the machine's job. The flag exists because anything a person can read, a
script eventually parses, and scraping the pretty form is worse than asking
for the real one.

`format.py` falls back to JSON for any shape it does not recognise, rather
than printing a confidently wrong subset. A formatter that silently drops a
field is worse than no formatter, and on the audit surface a dropped field is
a governance record that reads as complete and is not.

## Tests

`tests/test_cli.py` checks every command against the **server's real router**.
A CLI is a second description of an API, and two descriptions drift — without
that test, a renamed path is found by an operator, at the moment they need the
command, with a 404 and no hint about which side is wrong.

`test_the_command_list_covers_every_subcommand` guards the guard: a command
added without a test fails the suite rather than going unexercised.

```
nix build .#checks.x86_64-linux.admin-test
```

The round trip — CLI to socket to database and back — is in
`nix/tests/appliance.nix`, because it needs a booted machine with real
accounts and a real kernel reporting real uids.
