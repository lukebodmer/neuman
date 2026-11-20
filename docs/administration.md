# Administration

Running Neuman day to day: who can use it, what each person's agent may do,
which models are available, and what has happened. Almost all of it is done in
the administration page, with nothing to rebuild or restart. Getting an
appliance running in the first place is [setup](setup.md).

## Who can administer

An administrator is an account in Neuman's built-in **`neuman-admin`** group.
Make someone one with **Make administrator** on their account page. Being an
administrator is separate from roles: it opens the administration page, and it
does not change what that person's own agent may do.

The page is opened from **Manage Neuman** at the foot of the chat window's
rail. Everything in it can also be done from the server's command line with
`neuman-admin`, by someone whose system account may open its socket
([setup](setup.md#3-the-first-administrator)).

## How access works

Three things decide what a person's agent may do, applied in order:

1. **Defaults for everyone**: the starting point every account gets.
2. **Roles**: named sets of permissions, such as `staff` or `litigation`. An
   account holds a role directly, or through a group it belongs to.
3. **Overrides**: changes made on one account, which have the final say.

A role can grant:

| Permission | What it means |
|---|---|
| Tools | Which tools the agent may use |
| Files read, Files write | Which folders the file tools may read and write. `$HOME` means the person's home folders (`workspace`, `Documents`, `Desktop` and `Downloads` by default), on whichever computer runs the tool |
| Web tools | Whether tools that reach the network are offered |
| Models | Which models the person may choose |
| Approval ceiling | The least the agent must ask before acting: `never` (asks before everything), `safe` (asks before anything that changes something) or `yolo` (asks only before critical actions). The person can choose to be asked more, never less |
| Daily tokens, Max steps per turn | How much model use the person gets per day (reset at midnight UTC) and per request |

Changes take effect on the agent's **next action**, not its next conversation.
Removing a tool, a folder or a whole account stops it immediately.

A role is a ceiling, not a boundary on its own. The agent always runs as the
person, on their computer, so the operating system still decides which files
they can open at all; roles narrow the agent within that. The
[security model](security-model.md) explains exactly where each limit is held.

## The administration page

### Overview

What needs you, and what is happening. **Needs attention** lists problems you
can fix right now (a provider with no key, a model whose weights are missing,
a model that failed to load), each with a link to the page that fixes it.
Below it: who is active and what is running now, today's usage, and the most
recent configuration changes.

### Accounts

Everyone who can sign in. **New account** asks for a name, a password (which the
person changes at first sign-in unless you say otherwise) and the roles they
start with.

An account's page shows:

| Section | What it shows |
|---|---|
| Overview | Its groups and every role in force, and where each came from |
| Policy | What the account may do right now, with an override for any field |
| Tools | The tools it may use, with an override for the list |
| Limits | Its daily token cap and steps per turn |
| Usage | Tokens used on each of the last seven days |
| Sessions | Its conversations |

From the same page you can **reset a password**, **make or remove an
administrator**, and **disable** the account. Disabling signs it out everywhere
at once; enabling it again restores it as it was.

### Roles

Every role, what it grants, and who holds it. A change to a role reaches every
account that holds it on their next action. **Defaults for everyone** is edited
here too.

If two roles an account holds set the same setting differently (one says `safe`,
another `yolo`), Neuman refuses the combination rather than guessing which you
meant. Remove one, or give the account an override instead.

### Groups

A group gives all its members the same roles, so a team is managed in one
place: put `eng` in the `engineer` role, and everyone added to `eng` gets it.
`neuman-admin` is the one built-in group.

### Providers

Hosted model services. Choose a provider and paste its API key. The key is
stored in a file only the server can read, and is never shown again, logged or
included in a backup. Other OpenAI-compatible services can be added as custom
providers. [Setup](setup.md#4-providers-and-models) covers choosing between
providers.

### Models

Every model Neuman supports is a tile, under **On this machine** for models that
run on your own GPU and **Cloud** for hosted ones. Choose a tile to add it,
under the name people will see. Adding a model grants it to nobody; a role's
**Models** decides who may use it.

A local model's tile downloads and verifies its weights, and says whether it
fits your GPU. Once added, its settings are behind its tile: context size,
reply length, how it is held in GPU memory, and whether it stays loaded. In the
chat window, an administrator's model chooser also shows what is loaded on the
GPU now, and loads and unloads models. [Setup](setup.md#6-local-models) has the
details.

### Tools

Built-in tools, and **packages**: optional sets of tools, such as Blender, that
you turn on for the whole appliance. Each package says what it does and what a
person needs installed to use it. Enabling a package grants its tools to nobody;
add them to a role. [Tools](tools.md) describes every tool.

### Prompt

Text added to the start of every conversation: what your organisation does,
where shared files live, house style. It guides the agent and grants nothing:
an instruction to open a folder a role does not grant is simply refused. Below
it, **Composed prompt** shows the whole of what the model is told for any
account, section by section, with where each part comes from.

### Sessions

Every conversation on the appliance, its account, and what it cost. Opening
another person's conversation is itself recorded in the audit log, so
administrators can review what an agent did, and everyone can see that they
did.

### Usage

Tokens used today, by account. **Settled** counts finished model calls, as the
provider reported them; **in flight** is held for calls still running.

### Audit

The tamper-evident record of who did what: sign-ins, permission changes, every
tool call and its result, and administrators reading others' sessions. Search
it by text. **Verify chain** re-checks every entry and reports the first one
that has been altered, if any.

### Events

Warnings and errors from the server, newest first. The first place to look when
something has gone wrong.

## Common jobs

**Add a person.** Accounts → New account: a name, a temporary password, and
their starting roles. They sign in from their own Neuman client and choose a
new password.

```
neuman-admin account create alice --role staff
```

**Give a team the same access.** Create a role with what the team needs, create
a group, grant the role to the group, then add people to the group.

```
neuman-admin group create eng
neuman-admin grant add eng engineer
neuman-admin group add eng alice
```

**Keep two matters apart.** The boundary is the file server's own permissions:
give each matter's folder its own system group, and add only the people on that
matter. In Neuman, give each matter a role whose file permissions name its
folder. Because the agent runs as the person, it can never open a folder the
file server would refuse them.

**Remove someone's access.** Disable their account: every session ends at once.
To take away one permission instead, remove them from the group or role that
grants it, or add an override; it applies to their agent's next action.

```
neuman-admin account disable alice
```

**Review what an agent did.** Sessions shows the conversation, with every tool
call and result. Audit shows the same actions in the tamper-evident record,
alongside permission changes and sign-ins.

**Check the record is intact.** Audit → Verify chain, or:

```
neuman-admin audit verify
```

**Back up and restore.** A backup holds the database (accounts, configuration,
conversations and the audit record), the audit log's file copy, and the
per-account overrides. Provider keys are never included, so they are entered
again after a restore, and model weights are not either, since they can be
downloaded again. A restore re-verifies the audit chain before it finishes.

```
neuman-admin backup /var/lib/neuman/backups/2026-11-16
neuman-admin restore /var/lib/neuman/backups/2026-11-16
```

**Keep configuration in version control.** Export roles, defaults, providers and
models (never keys) as one file, and load it back on another appliance:

```
neuman-admin --json baseline show > baseline.json
neuman-admin baseline set baseline.json
```

**Clear out old conversations.**

```
neuman-admin session purge --older-than 90d
```

## The command line

`neuman-admin` runs on the appliance itself. Its commands mirror the page:

| Command | For |
|---|---|
| `account` | List, create, reset passwords, disable, enable, make administrator |
| `group`, `grant`, `role` | Groups, which roles they hold, renaming a role everywhere |
| `policy` | See an account's effective permissions, and set or clear its overrides |
| `baseline` | Export or import the whole configuration |
| `model` | List the models and what each resolves to |
| `prompt` | Show or replace the organisation's prompt |
| `session` | Read or purge any account's conversations |
| `usage` | An account's token use today |
| `audit` | Search or verify the record |
| `events` | Recent warnings and errors |
| `status` | What the appliance reports about itself |
| `backup`, `restore` | Back up or restore the appliance |

`neuman-admin <command> --help` lists each command's options.
