"""Generated from schemas/task-run.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "run": {
   "additionalProperties": false,
   "properties": {
    "account": {
     "minLength": 1,
     "type": "string"
    },
    "cost_usd": {
     "minimum": 0,
     "type": [
      "number",
      "null"
     ]
    },
    "created_at": {
     "format": "date-time",
     "type": "string"
    },
    "due_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "error": {
     "description": "Why the run as a whole ended, when that is not one step's error: no client before the catch-up window, a workspace now outside reach.",
     "maxLength": 2000,
     "type": [
      "string",
      "null"
     ]
    },
    "finished_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "id": {
     "pattern": "^[0-9a-f]{32}$",
     "type": "string"
    },
    "inputs": {
     "additionalProperties": {
      "maxLength": 2000,
      "type": "string"
     },
     "description": "The values this run was given, defaults filled in.",
     "type": "object"
    },
    "outputs_present": {
     "description": "Which of the task's declared outputs existed in the run directory when the run ended. Presence only; whether they are right is the reader's answer.",
     "items": {
      "type": "string"
     },
     "type": [
      "array",
      "null"
     ]
    },
    "run_dir": {
     "description": "The run's own directory, null until the client has created it.",
     "oneOf": [
      {
       "$ref": "common.schema.json#/$defs/absPath"
      },
      {
       "type": "null"
      }
     ]
    },
    "started_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "status": {
     "$ref": "#/$defs/status",
     "description": "`due` is created and ready; `waiting` has no connected client; `running` has a step in flight; `succeeded`: every step that ran succeeded and none was skipped; `failed`: a step failed; `budget`: no step failed and an agent step hit its cap; `missed` passed its catch-up window; `cancelled` was interrupted or the task was deleted."
    },
    "steps": {
     "description": "One record per task step, in order, written as each one starts and ends.",
     "items": {
      "$ref": "#/$defs/step"
     },
     "type": "array"
    },
    "task_id": {
     "pattern": "^[0-9a-f]{32}$",
     "type": "string"
    },
    "tokens": {
     "minimum": 0,
     "type": [
      "integer",
      "null"
     ]
    },
    "trigger": {
     "description": "Why this run exists. A schedule trigger is a due clock; a manual trigger is a person pressing Run now, which does not move `next_run_at`.",
     "enum": [
      "schedule",
      "manual"
     ]
    }
   },
   "required": [
    "id",
    "task_id",
    "account",
    "trigger",
    "status",
    "inputs",
    "steps",
    "created_at"
   ],
   "type": "object"
  },
  "status": {
   "enum": [
    "due",
    "waiting",
    "running",
    "succeeded",
    "failed",
    "budget",
    "missed",
    "cancelled"
   ]
  },
  "step": {
   "additionalProperties": false,
   "properties": {
    "branch_id": {
     "pattern": "^[0-9a-f]{32}$",
     "type": [
      "string",
      "null"
     ]
    },
    "cost_usd": {
     "minimum": 0,
     "type": [
      "number",
      "null"
     ]
    },
    "dispatch_id": {
     "description": "A run step's dispatch, as the audit chain records it.",
     "type": [
      "string",
      "null"
     ]
    },
    "error": {
     "description": "The terminal reason, named from the structured result. Never the model's prose summary.",
     "maxLength": 2000,
     "type": [
      "string",
      "null"
     ]
    },
    "exit_code": {
     "description": "A run step's exit code; null for an agent step or a command that was killed.",
     "type": [
      "integer",
      "null"
     ]
    },
    "finished_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "iterations": {
     "minimum": 0,
     "type": [
      "integer",
      "null"
     ]
    },
    "kind": {
     "enum": [
      "run",
      "agent"
     ]
    },
    "name": {
     "pattern": "^[a-z][a-z0-9-]{0,39}$",
     "type": "string"
    },
    "output": {
     "description": "A run step's own words: the end of its standard output, then the end of its standard error. The task view shows it under the step; an agent step's words are its transcript instead (D99).",
     "maxLength": 8000,
     "type": [
      "string",
      "null"
     ]
    },
    "session_id": {
     "description": "An agent step's session: its transcript and audit chain.",
     "pattern": "^[0-9a-f]{32}$",
     "type": [
      "string",
      "null"
     ]
    },
    "started_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "status": {
     "$ref": "#/$defs/stepStatus"
    },
    "tokens": {
     "minimum": 0,
     "type": [
      "integer",
      "null"
     ]
    }
   },
   "required": [
    "name",
    "kind",
    "status"
   ],
   "type": "object"
  },
  "stepStatus": {
   "description": "`succeeded`: a run step exited 0, or an agent turn ended on the model's stop. `failed`: a non-zero exit, a timeout, or a model or dispatch error. `budget`: an agent turn hit its step or token cap. `skipped`: its `run_if` did not hold.",
   "enum": [
    "pending",
    "running",
    "succeeded",
    "failed",
    "budget",
    "skipped",
    "cancelled"
   ]
  }
 },
 "$ref": "#/$defs/run",
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "One execution of a task: the input values it was given, the directory it worked in, and a record per step. A run step's status comes from its exit code and an agent step's from the harness's terminal reason, never from the model's own report (D94, D96). When the run ends the server writes this record to `neuman-run.json` in the run directory, through the owner's client, so whatever reads the run's outputs can read how it went without calling Neuman (D97).",
 "title": "task-run"
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
