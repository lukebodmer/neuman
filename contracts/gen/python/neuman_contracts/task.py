"""Generated from schemas/task.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "agentStep": {
   "additionalProperties": false,
   "description": "One harness turn: a session and a branch of their own, pre-approved inside the step's envelope intersected with the owner's live policy (D93). The step ends with the harness's terminal reason, never with the model's account of itself.",
   "properties": {
    "cwd": {
     "$ref": "#/$defs/stepPath",
     "description": "The turn's working directory. Absent means `$RUN_DIR`. It must be inside the envelope's read reach."
    },
    "envelope": {
     "$ref": "#/$defs/envelope"
    },
    "instruction": {
     "description": "The message the turn starts with. Written for the model, and the one place a task's judgement lives. The server opens it with the run's directory and input values, so the instruction can be the same words on every run.",
     "maxLength": 20000,
     "minLength": 1,
     "type": "string"
    },
    "kind": {
     "const": "agent"
    },
    "model": {
     "description": "The catalog name this turn asks for. Must be in `envelope.models` and the owner's policy.",
     "minLength": 1,
     "type": "string"
    },
    "name": {
     "$ref": "#/$defs/stepName"
    },
    "reasoning": {
     "description": "The reasoning level for this turn, or null for the model's declared default. Validated against the catalog at run time, like a branch's choice.",
     "type": [
      "string",
      "null"
     ]
    },
    "run_if": {
     "$ref": "#/$defs/runIf"
    }
   },
   "required": [
    "kind",
    "name",
    "instruction",
    "model",
    "envelope"
   ],
   "type": "object"
  },
  "draft": {
   "additionalProperties": false,
   "description": "What a client submits to create a task, or the merged form of what it submits to edit one. The server validates the merged draft against the owner's live policy, so an edit cannot smuggle in reach the create path would have refused.",
   "properties": {
    "enabled": {
     "description": "Whether the schedule fires. A disabled task can still be run by hand.",
     "type": "boolean"
    },
    "inputs": {
     "items": {
      "$ref": "#/$defs/input"
     },
     "maxItems": 10,
     "type": "array"
    },
    "name": {
     "maxLength": 80,
     "minLength": 1,
     "type": "string"
    },
    "outputs": {
     "description": "Artifacts the run is expected to leave in its directory. Declared so the reader knows what success looks like; the run record says which exist when it ends, and whether they are right stays outside Neuman.",
     "items": {
      "$ref": "#/$defs/outputPath"
     },
     "maxItems": 20,
     "type": "array",
     "uniqueItems": true
    },
    "run_root": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Where each run gets its own directory, `<run_root>/<due date>-<run id>`, made on the owner's client before the first step. It must be inside the owner's write reach and outside the workspace and every run step's `cwd`, since an agent step may write there (D96, D97)."
    },
    "schedule": {
     "$ref": "#/$defs/schedule"
    },
    "steps": {
     "$ref": "#/$defs/steps"
    },
    "workspace": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "The directory the task belongs to, and where a run step with no `cwd` runs. It must be inside the owner's read reach."
    }
   },
   "required": [
    "name",
    "workspace",
    "run_root",
    "steps",
    "schedule"
   ],
   "type": "object"
  },
  "envelope": {
   "additionalProperties": false,
   "description": "An agent step's declared reach, as a policy fragment. `files_read` and `files_write` are absolute paths chosen from what the owner already reaches, or `$RUN_DIR`; an entry outside the owner's reach is refused at authoring time and dropped again by the run-time intersection. No entry of `files_write` may contain the workspace, a run step's `cwd` or a run step's program, nor lie inside them, so an agent step can never write what a run step executes (D96). `models` is the set the step may choose from; the step's own `model` must be in it.",
   "properties": {
    "files_read": {
     "items": {
      "$ref": "#/$defs/stepPath"
     },
     "type": "array",
     "uniqueItems": true
    },
    "files_write": {
     "items": {
      "$ref": "#/$defs/stepPath"
     },
     "type": "array",
     "uniqueItems": true
    },
    "max_iterations": {
     "description": "The most model round-trips the step may take. The effective value is the smaller of this and the account's own policy, so a task cannot buy more steps than its owner.",
     "maximum": 500,
     "minimum": 1,
     "type": "integer"
    },
    "models": {
     "description": "Catalog names the step may use. The step's `model` is one of them.",
     "items": {
      "minLength": 1,
      "type": "string"
     },
     "type": "array",
     "uniqueItems": true
    },
    "network": {
     "description": "Whether the step may use tools that declare `needs = [\"network\"]`. `any` requires the account to hold network reach too; there is no host list (N146).",
     "enum": [
      "none",
      "any"
     ]
    },
    "tools": {
     "description": "Client tools the step may dispatch. A server-native tool or a `critical` tool is refused at authoring time: a task has no human to ask, and a critical call must never be pre-approved (D93). `shell` and `python` are refused in an agent step of a task with a run step after it, because neither declares its paths and either could rewrite what that step runs (D96).",
     "items": {
      "$ref": "#/$defs/toolName"
     },
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "tools",
    "files_read",
    "files_write",
    "network",
    "models",
    "max_iterations"
   ],
   "type": "object"
  },
  "input": {
   "additionalProperties": false,
   "description": "A parameter one run supplies. Values are strings and reach a run step only as environment variables, never spliced into its argv, so an input can change what a script is told and never which program runs (D96).",
   "properties": {
    "default": {
     "$ref": "#/$defs/inputValue"
    },
    "description": {
     "maxLength": 200,
     "type": "string"
    },
    "name": {
     "$ref": "#/$defs/inputName"
    },
    "required": {
     "description": "Whether a run must give a value. A required input with no default must also be given by `schedule.inputs` for the schedule to fire.",
     "type": "boolean"
    },
    "values": {
     "description": "The values a run may give, when the input is a choice. Absent means any string.",
     "items": {
      "$ref": "#/$defs/inputValue"
     },
     "minItems": 1,
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "name"
   ],
   "type": "object"
  },
  "inputName": {
   "description": "Lower case, so the environment variable a run step sees is `NEUMAN_INPUT_` plus its upper-cased form.",
   "pattern": "^[a-z][a-z0-9_]{0,31}$",
   "type": "string"
  },
  "inputValue": {
   "maxLength": 2000,
   "pattern": "^[^\\u0000]*$",
   "type": "string"
  },
  "inputValues": {
   "additionalProperties": {
    "$ref": "#/$defs/inputValue"
   },
   "maxProperties": 10,
   "propertyNames": {
    "$ref": "#/$defs/inputName"
   },
   "type": "object"
  },
  "outputPath": {
   "allOf": [
    {
     "not": {
      "pattern": "(^|/)\\.\\.($|/)"
     }
    }
   ],
   "description": "A path relative to the run directory naming an artifact the run is expected to leave there. A declaration for the reader and the runner, not a grant: an agent step's `files_write` is what decides where a write may land.",
   "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$",
   "type": "string"
  },
  "runIf": {
   "description": "When the step runs, given the steps before it. `ok` (the default): every earlier step succeeded. `ok_or_budget`: as `ok`, except an agent step that stopped on its budget counts \u2014 the step after a research turn that ran long still has the work it left. `always`: regardless, for a step that records or cleans up. A step that does not run is `skipped`.",
   "enum": [
    "ok",
    "ok_or_budget",
    "always"
   ]
  },
  "runStep": {
   "additionalProperties": false,
   "description": "A command the owner wrote word for word. It is dispatched to the owner's client as a pre-approved `shell` call through the ordinary gate and recorded in the audit chain like one; the model neither chooses it nor sees its arguments before it runs. It reaches what the owner reaches \u2014 there is no write list, because nothing on the client could enforce one (N146) \u2014 so what keeps it safe is that its program, its arguments and its working directory are fixed at authoring and lie outside every path an agent step may write (D96). The owner's policy must grant `shell`, checked at authoring and again when the step starts. It sees `NEUMAN_RUN_ID`, `NEUMAN_RUN_DIR`, `NEUMAN_TASK_ID` and one `NEUMAN_INPUT_<NAME>` per input in its environment.",
   "properties": {
    "argv": {
     "description": "The program and its arguments, quoted by the server, never by a shell the model writes. `argv[0]` is a program name looked up on the owner's `PATH`, or an absolute path.",
     "items": {
      "maxLength": 4000,
      "minLength": 1,
      "pattern": "^[^\\u0000]*$",
      "type": "string"
     },
     "maxItems": 64,
     "minItems": 1,
     "type": "array"
    },
    "cwd": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Where the command runs. Absent means the task's workspace."
    },
    "kind": {
     "const": "run"
    },
    "name": {
     "$ref": "#/$defs/stepName"
    },
    "run_if": {
     "$ref": "#/$defs/runIf"
    },
    "timeout_s": {
     "description": "The longest the command may run. A command still running is killed and the step is `failed`.",
     "maximum": 3600,
     "minimum": 1,
     "type": "integer"
    }
   },
   "required": [
    "kind",
    "name",
    "argv",
    "timeout_s"
   ],
   "type": "object"
  },
  "schedule": {
   "additionalProperties": false,
   "allOf": [
    {
     "if": {
      "properties": {
       "kind": {
        "const": "daily"
       }
      }
     },
     "then": {
      "required": [
       "time",
       "timezone"
      ]
     }
    },
    {
     "if": {
      "properties": {
       "kind": {
        "const": "weekly"
       }
      }
     },
     "then": {
      "required": [
       "time",
       "weekday",
       "timezone"
      ]
     }
    },
    {
     "if": {
      "properties": {
       "kind": {
        "const": "interval"
       }
      }
     },
     "then": {
      "required": [
       "minutes"
      ]
     }
    }
   ],
   "description": "When the task is due. Times are local to `timezone`; the server computes `next_run_at` from this and never from the client's clock. A due run waits for the owner's client, so `scheduled` means due rather than guaranteed at that second (D94).",
   "properties": {
    "inputs": {
     "$ref": "#/$defs/inputValues",
     "description": "The input values a scheduled run is given. A manual run gives its own."
    },
    "kind": {
     "enum": [
      "daily",
      "weekly",
      "interval"
     ]
    },
    "minutes": {
     "description": "The interval's period, at least fifteen minutes. Required by `interval`.",
     "maximum": 10080,
     "minimum": 15,
     "type": "integer"
    },
    "time": {
     "description": "HH:MM in `timezone`. Required by `daily` and `weekly`.",
     "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$",
     "type": "string"
    },
    "timezone": {
     "description": "An IANA zone name. Required by `daily` and `weekly`; validated with `zoneinfo` rather than by this pattern.",
     "pattern": "^[A-Za-z][A-Za-z0-9_+/-]{0,63}$",
     "type": "string"
    },
    "weekday": {
     "description": "0 is Monday, matching `datetime.weekday()`. Required by `weekly`.",
     "maximum": 6,
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "kind"
   ],
   "type": "object"
  },
  "step": {
   "oneOf": [
    {
     "$ref": "#/$defs/runStep"
    },
    {
     "$ref": "#/$defs/agentStep"
    }
   ]
  },
  "stepName": {
   "description": "Unique within the task. The run record reports each step under this name.",
   "pattern": "^[a-z][a-z0-9-]{0,39}$",
   "type": "string"
  },
  "stepPath": {
   "anyOf": [
    {
     "$ref": "common.schema.json#/$defs/absPath"
    },
    {
     "const": "$RUN_DIR"
    }
   ],
   "description": "A path a step names: an absolute path, or exactly `$RUN_DIR` for this run's own directory, which does not exist until the run does (D97)."
  },
  "steps": {
   "description": "Run in order, one at a time. There are no branches, loops or retries: a step that should not follow a failure says so with `run_if` (D95).",
   "items": {
    "$ref": "#/$defs/step"
   },
   "maxItems": 10,
   "minItems": 1,
   "type": "array"
  },
  "task": {
   "additionalProperties": false,
   "description": "A stored task as the API returns it. The server owns the id, the owner and every timestamp; a client that sends them is refused rather than trusted.",
   "properties": {
    "account": {
     "minLength": 1,
     "type": "string"
    },
    "created_at": {
     "format": "date-time",
     "type": "string"
    },
    "enabled": {
     "type": "boolean"
    },
    "id": {
     "pattern": "^[0-9a-f]{32}$",
     "type": "string"
    },
    "inputs": {
     "items": {
      "$ref": "#/$defs/input"
     },
     "maxItems": 10,
     "type": "array"
    },
    "last_run_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "name": {
     "maxLength": 80,
     "minLength": 1,
     "type": "string"
    },
    "next_run_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "outputs": {
     "items": {
      "$ref": "#/$defs/outputPath"
     },
     "maxItems": 20,
     "type": "array",
     "uniqueItems": true
    },
    "run_root": {
     "$ref": "common.schema.json#/$defs/absPath"
    },
    "schedule": {
     "$ref": "#/$defs/schedule"
    },
    "steps": {
     "$ref": "#/$defs/steps"
    },
    "updated_at": {
     "format": "date-time",
     "type": "string"
    },
    "workspace": {
     "$ref": "common.schema.json#/$defs/absPath"
    }
   },
   "required": [
    "id",
    "account",
    "name",
    "workspace",
    "run_root",
    "inputs",
    "steps",
    "schedule",
    "outputs",
    "enabled",
    "created_at",
    "updated_at",
    "next_run_at"
   ],
   "type": "object"
  },
  "toolName": {
   "pattern": "^[a-z_][a-z0-9_]*$",
   "type": "string"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "A task is a standing approval: an account saves a recipe once \u2014 inputs, an ordered list of steps, the outputs it leaves \u2014 and runs it on demand or on a schedule. A step is either a `run` step, a command the owner wrote word for word, or an `agent` step, a harness turn inside an envelope. The envelope is a policy fragment, never a grant \u2014 the server intersects it with the account's live policy on every model step, so an agent step can narrow reach and can never widen it (D92, D93, D95, D96). A task is not a principal: it runs as its owner, on that owner's client (D94). Every run gets a fresh directory under `run_root`, the one place an agent step may write by default and where the run's record is left (D97).",
 "oneOf": [
  {
   "$ref": "#/$defs/task"
  },
  {
   "$ref": "#/$defs/draft"
  }
 ],
 "title": "task"
}
""")


def _build() -> Draft202012Validator:
    registry: Registry = Registry()
    for name, contents in RESOURCES.items():
        registry = registry.with_resource(name, Resource.from_contents(contents))
    return Draft202012Validator(SCHEMA, registry=registry)


_VALIDATOR = _build()


def validate(instance: object) -> None:
    """Raise jsonschema.ValidationError unless instance satisfies the schema."""
    _VALIDATOR.validate(instance)


def is_valid(instance: object) -> bool:
    """Return True when instance satisfies the schema."""
    return _VALIDATOR.is_valid(instance)
