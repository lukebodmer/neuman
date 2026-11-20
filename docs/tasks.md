# Tasks

A task is work Neuman does for you on a schedule, or whenever you ask, without
you in the conversation. It is a short recipe of commands you write and steps
where the agent works from an instruction, run on your own computer, inside
limits you set.

## What a task is made of

A task is up to ten **steps**, run one at a time, in order. There are two kinds:

- **A command step** runs a program exactly as you wrote it, as you. Use it for
  anything that should happen the same way every time: fetching data, building,
  checking results.
- **An agent step** gives the agent an instruction and lets it work, inside
  limits you set for that step. Use it where the work needs judgement: reading,
  researching, writing.

Most useful tasks mix the two: commands for the predictable parts, and one agent
step for the part that needs thought. The commands then run reliably every time,
and the agent spends its effort only where it is needed.

Around the steps, a task has:

| Setting | What it is |
|---|---|
| **Works in** | The task's workspace: the folder it belongs to, and where command steps run by default |
| **Keeps runs in** | The folder where each run gets a fresh folder of its own |
| **Inputs** | Values a run is given, such as which kind of update to do |
| **When it runs** | Its schedule, which you can turn on and off |
| **Files a run leaves** | The files a run is expected to produce, so you can see at a glance whether it did |

To make one, open **Tasks** at the foot of the left rail in the chat window and
choose **New task**.

## Steps

### Command steps

A command step is a program and its arguments, a folder to run in (the
workspace unless you choose another), and a time limit of up to an hour. A
command still running at its limit is stopped, and the step fails.

Each command can read these environment variables:

| Variable | Value |
|---|---|
| `NEUMAN_RUN_DIR` | This run's own folder |
| `NEUMAN_RUN_ID`, `NEUMAN_TASK_ID` | Which run and which task this is |
| `NEUMAN_INPUT_<NAME>` | Each input's value, such as `NEUMAN_INPUT_KIND` |

Command steps need your role to grant `shell`.

### Agent steps

An agent step is an **instruction**, a **model**, and a set of **limits**:

| Limit | What it controls |
|---|---|
| Tools | Which tools the agent may use |
| Can read, Can write | Which folders it may read and write; by default, its run folder |
| Internet | Whether it may use tools that reach the network |
| Model | Which models it may use |
| Step limit | The most rounds of work it may take before it stops |

The limits can only narrow what you yourself are allowed. They are checked
against your current permissions at every step of every run, so if your role
loses a tool or a folder, your tasks lose it too, with nothing to edit.

The instruction can be the same words every run. Neuman opens it with the run's
folder and input values, so "Read brief.md in your working directory and follow
it" works when an earlier command writes a fresh `brief.md` each time.

### When a step runs

Each step says whether it should run, given the steps before it:

| Setting | Runs when |
|---|---|
| `ok` (the default) | Every earlier step succeeded |
| `ok_or_budget` | The same, except an agent step that hit its step limit still counts, so work it left can be checked |
| `always` | Always, for a step that records or cleans up |

A step that does not run is marked `skipped`. There are no branches, loops or
retries: a decision about what to do next belongs in an agent step.

## Inputs

An input is a named value a run is given. It can be free text or a choice from a
list, and it can have a default and be required. A scheduled run gets the
values set on the schedule; a run you start yourself gets the ones you choose.
Command steps read inputs as environment variables, never as part of the
command itself, so an input can change what a program is told but never which
program runs.

## Running a task

**Run now** starts a run straight away, and works before the schedule is on, so
you can try a task before trusting it to the clock. **Stop run** stops one. A
task has one run at a time.

A **schedule** runs it every day at a set time, every week on a set day, or on
repeat every 15 minutes to 24 hours, in the time zone you choose.

**Runs happen on your computer.** Neuman's server keeps the clock, and your
Neuman client does the work, so a run needs your client connected. If it is not,
the run shows as **Waiting for your computer** and starts when it connects. A
run that is still waiting after six hours is recorded as `missed`.

## What each run leaves

Every run works in its own folder, `<Keeps runs in>/<date>-<run id>`, so one
run's files never mix with another's. Agent steps write there by default, and
commands find it in `NEUMAN_RUN_DIR`.

When the run ends, Neuman writes `neuman-run.json` into that folder: the inputs,
each step's status and cost, and which of the expected files exist. Anything
that consumes a run's output can read how the run went from the same folder,
without calling Neuman.

## Reading results

A task opens in the chat window, with its steps laid out beside the run. Open a
step to see what it did: a command's output, or the agent's full conversation.
A scheduled task whose latest run failed, hit its step limit or was missed is
listed under **Needs a look**. An agent step's conversation belongs to its run,
so it never clutters your list of conversations.

| Status | Meaning |
|---|---|
| `succeeded` | A command exited cleanly, or the agent finished its work |
| `failed` | A command failed or ran out of time, or the agent hit an error |
| `budget` | An agent step reached its step limit |
| `skipped` | A step did not run, because of its `ok` / `always` setting |
| `cancelled` | Someone stopped the run |
| `missed` | The run never started, because your computer was not connected in time |

A status comes from a command's exit code or from how the agent's turn ended,
never from the agent's own account of how it did. Whether the output is
*right* is for you to judge, or for a command step you add to check it.

## Why a task asks for no approvals

You approve a task once, when you save it, and its steps then run without
stopping to ask. What keeps that safe:

- **Agent steps can only narrow your permissions**, and are re-checked at every
  step.
- **The agent can never change what a command runs.** When you save, Neuman
  refuses an agent step that could write the workspace, a command's folder or
  its program, and refuses `shell` and `python` in an agent step that comes
  before a command. Inputs reach commands only as environment variables. So a
  web page the agent reads can change the data a later command is given, but
  never which program runs. A command that reads what an agent wrote should
  treat it as data.
- **Tools marked critical are refused**, because there is no one to ask.
- **Everything is recorded** in the audit chain, as it is for any conversation.

The full rules are in the [security model](security-model.md).

## Example: a daily research update

This task keeps a reference website's data current. Every morning it gathers
what has changed, has the agent research it, then checks the agent's work
before anything is published:

```json
{
  "name": "site update",
  "workspace": "/srv/site",
  "run_root": "/srv/site-runs",
  "inputs": [
    {
      "name": "kind",
      "description": "queued: the oldest request. sweep: prices, re-checks, new releases.",
      "values": ["queued", "sweep"],
      "required": true,
      "default": "queued"
    }
  ],
  "steps": [
    {
      "kind": "run",
      "name": "prepare",
      "argv": ["nix", "develop", "-c", "pnpm", "task:prepare"],
      "timeout_s": 900
    },
    {
      "kind": "agent",
      "name": "research",
      "instruction": "Read brief.md in your working directory and follow it.",
      "model": "DeepSeek-V4.1-Flash",
      "envelope": {
        "tools": ["fs_read", "fs_list", "fs_write", "web_fetch"],
        "files_read": ["$RUN_DIR"],
        "files_write": ["$RUN_DIR"],
        "network": "any",
        "models": ["DeepSeek-V4.1-Flash"],
        "max_iterations": 200
      }
    },
    {
      "kind": "run",
      "name": "check",
      "argv": ["nix", "develop", "-c", "pnpm", "check"],
      "timeout_s": 1800,
      "run_if": "ok_or_budget"
    }
  ],
  "schedule": {
    "kind": "daily",
    "time": "06:15",
    "timezone": "America/Los_Angeles",
    "inputs": {"kind": "sweep"}
  },
  "outputs": ["claims.json", "report.md", "result.json"]
}
```

1. **prepare** is a command. It syncs data from public APIs and writes a
   `brief.md` for this run into the run folder.
2. **research** is the agent. It reads the brief, researches on the web, and
   writes its findings to the run folder. It can read and write only that
   folder, so it cannot touch the site's code or the scripts around it.
3. **check** is a command. It verifies each of the agent's findings against its
   source before anything changes. It runs even if the agent hit its step limit,
   so partial work still gets checked.

The judgement is in one step, and everything around it is code that runs the
same way every morning.
