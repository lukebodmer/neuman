"""Generated from schemas/dispatch-rpc.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "cancel": {
   "additionalProperties": false,
   "properties": {
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "type": {
     "const": "cancel"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id"
   ],
   "type": "object"
  },
  "detached": {
   "additionalProperties": false,
   "description": "The process outlived the dispatch's wait and continues as a background job (D98). Terminal for the dispatch, like `result`: the sequence closes here and no output frames follow, because the job's output now goes to the spill files named below. How the job ends arrives later as `job_settled`.",
   "properties": {
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "job_id": {
     "$ref": "#/$defs/jobId"
    },
    "lifetime_ms": {
     "description": "The lifetime the client applied, so the core can say when the job will be killed.",
     "maximum": 3600000,
     "minimum": 1,
     "type": "integer"
    },
    "persisted_err": {
     "description": "Client path of the file the job's stderr goes to, from its first byte.",
     "type": [
      "string",
      "null"
     ]
    },
    "persisted_out": {
     "description": "Client path of the file the job's stdout goes to, from its first byte.",
     "type": [
      "string",
      "null"
     ]
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "sequence": {
     "$ref": "#/$defs/sequence"
    },
    "type": {
     "const": "detached"
    },
    "wall_ms": {
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "sequence",
    "job_id",
    "wall_ms",
    "lifetime_ms",
    "persisted_out",
    "persisted_err"
   ],
   "type": "object"
  },
  "dispatch": {
   "additionalProperties": false,
   "properties": {
    "args": {
     "description": "Tool arguments; the tool manifest's parameters schema governs their content.",
     "type": "object"
    },
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "lifetime_ms": {
     "description": "The longest the process may live, from spawn. The client applies min(this, its own manifest's `lifetime_ms`): neither side may extend the other. Equal to `wait_ms` means the dispatch is killed at its wait rather than becoming a job \u2014 what the core sends when the session cannot follow a job, and for a task's run step (D96).",
     "maximum": 3600000,
     "minimum": 1,
     "type": "integer"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "session_id": {
     "description": "The session the dispatch belongs to. A job is tagged with it, and `job_output`/`job_kill` find only the jobs of the session that asks (D98), so one of an account's conversations cannot read or stop another's. Absent for a dispatch no session owns \u2014 a route's own question, a task's run step \u2014 which never becomes a job and sees none.",
     "minLength": 1,
     "type": "string"
    },
    "surfaces": {
     "additionalProperties": false,
     "description": "The account's declared surfaces, expanded. The client resolves a path-taking tool's path arguments against these and refuses one outside them, which is the only check there is on the client \u2014 the kernel does not confine a dispatch there (SPEC section 5). Required rather than optional so a client cannot be asked to run a path-taking tool with nothing to check against.",
     "properties": {
      "read": {
       "items": {
        "$ref": "common.schema.json#/$defs/absPath"
       },
       "type": "array",
       "uniqueItems": true
      },
      "write": {
       "items": {
        "$ref": "common.schema.json#/$defs/absPath"
       },
       "type": "array",
       "uniqueItems": true
      }
     },
     "required": [
      "read",
      "write"
     ],
     "type": "object"
    },
    "tool": {
     "$ref": "#/$defs/toolName"
    },
    "type": {
     "const": "dispatch"
    },
    "wait_ms": {
     "description": "How long the dispatch may block. The client applies min(this, the lifetime it applied), and this may exceed the manifest's own `wait_ms`: that is the default the core sends for a model's call, and a longer one is the core's alone to ask for \u2014 a task's run step waits its whole `timeout_s` (D96). A process still running at the wait with lifetime left becomes a background job (`detached`, D98); otherwise it is killed and the result says `timed_out`.",
     "maximum": 3600000,
     "minimum": 1,
     "type": "integer"
    },
    "working_dir": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Directory to start the dispatch in. Optional: absent means the client's own working directory. A hint rather than a grant \u2014 on the client there is no profile to fail against, and a directory that does not exist is a spawn error."
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "tool",
    "args",
    "wait_ms",
    "lifetime_ms",
    "surfaces"
   ],
   "type": "object"
  },
  "dispatchId": {
   "pattern": "^[A-Za-z0-9_-]{8,64}$",
   "type": "string"
  },
  "error": {
   "additionalProperties": false,
   "properties": {
    "code": {
     "enum": [
      "bad_request",
      "unknown_tool",
      "spawn_failed",
      "internal",
      "unavailable",
      "outside_surface",
      "missing_program"
     ]
    },
    "dispatch_id": {
     "description": "Null when the error has no dispatch context (a bad hello, a malformed line).",
     "type": [
      "string",
      "null"
     ]
    },
    "message": {
     "minLength": 1,
     "type": "string"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "type": {
     "const": "error"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "code",
    "message"
   ],
   "type": "object"
  },
  "hello": {
   "additionalProperties": false,
   "properties": {
    "drops": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Where this client keeps files a person dropped on its window (D102). The core accepts an attachment inside it although no policy names it, because the bytes are ones that person handed over from their own machine; only while this client is connected, and only as an attachment the read tools open. Resolved by the client, so the core compares it as a string."
    },
    "home": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "The home directory on the machine this client runs on \u2014 what `~` means there. The core expands a `$HOME` policy entry against it and must not use the account's home on *its* host: a client on another machine has a different one, and a dispatch checked against the wrong home refuses paths the account can read (N145). A dishonest value cannot widen anything \u2014 `shell` and `python` are unchecked, so a client wanting more reach would not ask \u2014 but it decides which directories a `$HOME` grant names, and that is a question only the client can answer."
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "tools": {
     "description": "The tool names this client can run. The core intersects this with the catalog and the account's grants; dispatching a name the client did not advertise is a bug on the core's side, and asking is cheaper than a round trip that ends in unknown_tool.",
     "items": {
      "$ref": "#/$defs/toolName"
     },
     "type": "array",
     "uniqueItems": true
    },
    "type": {
     "const": "hello"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "home",
    "drops",
    "tools"
   ],
   "type": "object"
  },
  "jobId": {
   "description": "Short on purpose: the model reads and writes it, so every character is a token in its context.",
   "pattern": "^[a-z0-9]{6,16}$",
   "type": "string"
  },
  "jobSettled": {
   "additionalProperties": false,
   "description": "A background job ended. Sent with no dispatch to answer, whenever it happens, so the core records it (`job.settled`) and tells the session that started it at its next step. Not sequenced: it belongs to no dispatch's stream.",
   "properties": {
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId",
     "description": "The dispatch that became the job."
    },
    "ended": {
     "description": "`exited` on its own; `killed` by `job_kill` or a core `cancel`; `lifetime` at its cap.",
     "enum": [
      "exited",
      "killed",
      "lifetime"
     ]
    },
    "exit_code": {
     "description": "As in `result`: the status, or 128+signal when it was killed.",
     "maximum": 255,
     "minimum": 0,
     "type": "integer"
    },
    "job_id": {
     "$ref": "#/$defs/jobId"
    },
    "observed": {
     "description": "A `job_output` or `job_kill` call was waiting on it and has already told the model how it ended, so the core records it without a notice.",
     "type": "boolean"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "type": {
     "const": "job_settled"
    },
    "wall_ms": {
     "description": "From spawn to the end.",
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "job_id",
    "dispatch_id",
    "exit_code",
    "wall_ms",
    "ended",
    "observed"
   ],
   "type": "object"
  },
  "result": {
   "additionalProperties": false,
   "properties": {
    "cancelled": {
     "type": "boolean"
    },
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "exit_code": {
     "description": "The process's exit status, or 128+signal when the client killed it. A dispatch that never ran has no exit status and is reported as an `error` message instead.",
     "maximum": 255,
     "minimum": 0,
     "type": "integer"
    },
    "persisted_err": {
     "description": "Client path of the file holding stderr that outgrew the preview; see persisted_out.",
     "type": [
      "string",
      "null"
     ]
    },
    "persisted_out": {
     "description": "Client path of the file holding stdout that outgrew the preview, or null when the stream fit. The core names it in the note it hands the model, which reads it back through the same client.",
     "type": [
      "string",
      "null"
     ]
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "sequence": {
     "$ref": "#/$defs/sequence"
    },
    "structured": {
     "description": "A tool's structured result, when it produces one.",
     "type": [
      "object",
      "null"
     ]
    },
    "timed_out": {
     "type": "boolean"
    },
    "truncated": {
     "description": "The client stopped forwarding output at its cap. The reader ORs this with its own truncation, since either side may hit a limit first.",
     "type": "boolean"
    },
    "type": {
     "const": "result"
    },
    "wall_ms": {
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "sequence",
    "exit_code",
    "wall_ms",
    "timed_out",
    "cancelled",
    "truncated"
   ],
   "type": "object"
  },
  "rpcVersion": {
   "const": 1
  },
  "sequence": {
   "description": "Monotonic per dispatch, with no gaps: started=0, then each stdout/stderr chunk, then result or detached. Required on every streamed message because it is the only way the reader can tell a dropped chunk from a chunk that was never sent \u2014 the difference between truncated output and complete output, which a model cannot be allowed to confuse.",
   "minimum": 0,
   "type": "integer"
  },
  "started": {
   "additionalProperties": false,
   "properties": {
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "pid": {
     "minimum": 1,
     "type": [
      "integer",
      "null"
     ]
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "sequence": {
     "$ref": "#/$defs/sequence"
    },
    "type": {
     "const": "started"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "sequence"
   ],
   "type": "object"
  },
  "stderr": {
   "additionalProperties": false,
   "properties": {
    "chunk": {
     "description": "Base64-encoded output bytes.",
     "pattern": "^[A-Za-z0-9+/]*={0,2}$",
     "type": "string"
    },
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "sequence": {
     "$ref": "#/$defs/sequence"
    },
    "type": {
     "const": "stderr"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "sequence",
    "chunk"
   ],
   "type": "object"
  },
  "stdout": {
   "additionalProperties": false,
   "properties": {
    "chunk": {
     "description": "Base64-encoded output bytes.",
     "pattern": "^[A-Za-z0-9+/]*={0,2}$",
     "type": "string"
    },
    "dispatch_id": {
     "$ref": "#/$defs/dispatchId"
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "sequence": {
     "$ref": "#/$defs/sequence"
    },
    "type": {
     "const": "stdout"
    }
   },
   "required": [
    "type",
    "rpc_version",
    "dispatch_id",
    "sequence",
    "chunk"
   ],
   "type": "object"
  },
  "toolName": {
   "description": "A tool *name*, never a path: the client resolves it against its own manifest set, so a dispatch cannot ask for a program that is not one of Neuman's (D3).",
   "pattern": "^[a-z_][a-z0-9_]*$",
   "type": "string"
  },
  "welcome": {
   "additionalProperties": false,
   "properties": {
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion"
    },
    "type": {
     "const": "welcome"
    },
    "user": {
     "$ref": "common.schema.json#/$defs/userName",
     "description": "The account the core resolved for this connection. Present so the handshake has an acknowledgement rather than silence, and so a client can report which account it is acting as when it is not the one it expected."
    }
   },
   "required": [
    "type",
    "rpc_version",
    "user"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "JSON-lines between the decision core and a connected client, one JSON object per line. The client opens the connection, says hello with the tool names it can run and the home directory on its machine, and then receives dispatches and returns their output. The core decides; the client executes exactly what it is told. There is no profile on the wire: the client runs as the local account that holds the process and reaches what that account reaches, and the controls are the capability gate and the approval verdict, both decided before a dispatch is sent.",
 "oneOf": [
  {
   "$ref": "#/$defs/hello"
  },
  {
   "$ref": "#/$defs/welcome"
  },
  {
   "$ref": "#/$defs/dispatch"
  },
  {
   "$ref": "#/$defs/cancel"
  },
  {
   "$ref": "#/$defs/started"
  },
  {
   "$ref": "#/$defs/stdout"
  },
  {
   "$ref": "#/$defs/stderr"
  },
  {
   "$ref": "#/$defs/result"
  },
  {
   "$ref": "#/$defs/detached"
  },
  {
   "$ref": "#/$defs/jobSettled"
  },
  {
   "$ref": "#/$defs/error"
  }
 ],
 "properties": {
  "rpc_version": {
   "$ref": "#/$defs/rpcVersion"
  },
  "type": {
   "enum": [
    "hello",
    "welcome",
    "dispatch",
    "cancel",
    "started",
    "stdout",
    "stderr",
    "result",
    "detached",
    "job_settled",
    "error"
   ]
  }
 },
 "required": [
  "type",
  "rpc_version"
 ],
 "title": "dispatch-rpc",
 "type": "object"
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
