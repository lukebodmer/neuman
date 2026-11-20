"""Generated from schemas/ws.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "approval": {
   "additionalProperties": false,
   "description": "Inbound: a verdict on one pending tool call (D12). A verdict that never arrives times out as a denial \u2014 a disconnected client must never become a grant.",
   "properties": {
    "approved": {
     "type": "boolean"
    },
    "reason": {
     "maxLength": 1024,
     "type": "string"
    },
    "tool_call_id": {
     "$ref": "#/$defs/toolCallId"
    },
    "type": {
     "const": "approval"
    }
   },
   "required": [
    "type",
    "tool_call_id",
    "approved"
   ],
   "type": "object"
  },
  "compacted": {
   "additionalProperties": false,
   "description": "Outbound: the oldest span of the conversation was replaced by a summary, so the capacity bar is about to fall by most of its width and the agent has lost detail it may be asked about. Advisory, like `truncated` \u2014 a client that ignores it is correct, just less informative. It exists because an unexplained drop reads as a defect: while an earlier version edited history without recording the edit the reported prompt size alternated between two histories and nothing on screen accounted for either (N119, N120). Compaction is the only move that changes what the next step projects, so it is the only thing that moves the bar downward, and the only one that needs to say so.",
   "properties": {
    "summarized_messages": {
     "description": "How many messages the summary replaced. Zero is legitimate: a history short enough to need no summary produces no action at all, so a frame carrying zero means the span was empty rather than that nothing happened.",
     "minimum": 0,
     "type": "integer"
    },
    "type": {
     "const": "compacted"
    }
   },
   "required": [
    "type"
   ],
   "type": "object"
  },
  "done": {
   "additionalProperties": false,
   "description": "Outbound: the turn ended, for the stated reason. Exactly one done frame per turn; the client may send a new message after it. `seq` is the `turn_complete` event, the boundary a fork resolves `after` to.",
   "properties": {
    "reason": {
     "enum": [
      "stop",
      "max_iterations",
      "budget_exhausted",
      "interrupted",
      "model_error",
      "reasoning_error"
     ]
    },
    "seq": {
     "minimum": 1,
     "type": "integer"
    },
    "type": {
     "const": "done"
    }
   },
   "required": [
    "type",
    "reason",
    "seq"
   ],
   "type": "object"
  },
  "error": {
   "additionalProperties": false,
   "description": "Outbound: something went wrong. A turn-ending error is always followed by a done frame carrying the matching reason.",
   "properties": {
    "code": {
     "enum": [
      "unknown_message",
      "turn_in_flight",
      "invalid_request",
      "budget_exhausted",
      "model_error",
      "reasoning_error"
     ]
    },
    "message": {
     "type": "string"
    },
    "type": {
     "const": "error"
    }
   },
   "required": [
    "type",
    "code"
   ],
   "type": "object"
  },
  "interrupt": {
   "additionalProperties": false,
   "description": "Inbound: stop now. Cancels any in-flight dispatch (the runner kills the cgroup, not the process group) and denies every pending approval.",
   "properties": {
    "type": {
     "const": "interrupt"
    }
   },
   "required": [
    "type"
   ],
   "type": "object"
  },
  "message": {
   "additionalProperties": false,
   "description": "Inbound: start a turn. Rejected with error code turn_in_flight when one is already running \u2014 a second concurrent turn would race on the event log's sequence and on the token budget.",
   "properties": {
    "content": {
     "minLength": 1,
     "type": "string"
    },
    "model": {
     "description": "A model catalog name; the core admits it only if the user's composed policy grants it.",
     "minLength": 1,
     "type": "string"
    },
    "reasoning": {
     "description": "How hard the model should think on this turn, as one of the levels its catalog entry declares. Per-turn and chosen by the user, like `auto_accept` and unlike the window or the costs -- the same question asked twice in one session can deserve different answers.\n\nOmitted means the model's declared default. A level the model does not declare is refused by name rather than forwarded: a provider's own error for an unknown effort string arrives mid-turn, after the tokens are spent.",
     "minLength": 1,
     "type": "string"
    },
    "type": {
     "const": "message"
    }
   },
   "required": [
    "type",
    "content",
    "model"
   ],
   "type": "object"
  },
  "reasoning": {
   "additionalProperties": false,
   "description": "Outbound: streamed reasoning content, when the model produces it.",
   "properties": {
    "text": {
     "type": "string"
    },
    "type": {
     "const": "reasoning"
    }
   },
   "required": [
    "type",
    "text"
   ],
   "type": "object"
  },
  "steer": {
   "additionalProperties": false,
   "description": "Inbound: extra user input folded into the conversation at the next safe point, without ending the turn.",
   "properties": {
    "content": {
     "minLength": 1,
     "type": "string"
    },
    "type": {
     "const": "steer"
    }
   },
   "required": [
    "type",
    "content"
   ],
   "type": "object"
  },
  "token": {
   "additionalProperties": false,
   "description": "Outbound: one streamed piece of assistant content.",
   "properties": {
    "text": {
     "type": "string"
    },
    "type": {
     "const": "token"
    }
   },
   "required": [
    "type",
    "text"
   ],
   "type": "object"
  },
  "toolApproval": {
   "additionalProperties": false,
   "description": "Outbound: a human is being asked about this proposal, and the harness is now blocked waiting for a verdict. Separate from `tool_proposal`, which is emitted before the gate runs and therefore cannot know \u2014 and separate from `tool_decision`, which reports the answer. A client shows its approve/deny controls on this frame and removes them on the decision; showing them on the proposal meant they appeared for calls nobody was asked about, and under `yolo` they never went away (N89).",
   "properties": {
    "preview": {
     "$ref": "#/$defs/writePreview"
    },
    "tool_call_id": {
     "$ref": "#/$defs/toolCallId"
    },
    "type": {
     "const": "tool_approval_required"
    }
   },
   "required": [
    "type",
    "tool_call_id"
   ],
   "type": "object"
  },
  "toolCallId": {
   "description": "The model-assigned tool call id, or a server-generated hex id when the model omitted one. Correlates proposal \u2192 decision \u2192 result.",
   "maxLength": 128,
   "minLength": 1,
   "type": "string"
  },
  "toolDecision": {
   "additionalProperties": false,
   "description": "Outbound: the verdict on a proposal. consent=true means a human was asked; false means the gate decided alone.",
   "properties": {
    "accepted": {
     "type": "boolean"
    },
    "consent": {
     "type": "boolean"
    },
    "tool_call_id": {
     "$ref": "#/$defs/toolCallId"
    },
    "type": {
     "const": "tool_decision"
    }
   },
   "required": [
    "type",
    "tool_call_id",
    "accepted",
    "consent"
   ],
   "type": "object"
  },
  "toolProposal": {
   "additionalProperties": false,
   "description": "Outbound: the model proposed a tool call. Emitted before the capability gate runs, so a proposal is not a decision and must never be rendered as one.",
   "properties": {
    "arguments": {
     "type": "object"
    },
    "name": {
     "type": "string"
    },
    "tool_call_id": {
     "$ref": "#/$defs/toolCallId"
    },
    "type": {
     "const": "tool_proposal"
    }
   },
   "required": [
    "type",
    "tool_call_id",
    "name",
    "arguments"
   ],
   "type": "object"
  },
  "toolResult": {
   "additionalProperties": false,
   "description": "Outbound: what running one tool produced. content is untrusted output wrapped in the harness's delimiters and must be rendered as data, never interpreted as markup or instructions (D23).",
   "properties": {
    "cancelled": {
     "type": "boolean"
    },
    "content": {
     "type": "string"
    },
    "exit_code": {
     "description": "The tool's exit status, or -1 when it never ran (denied, malformed arguments, or an unreachable runner).",
     "type": "integer"
    },
    "has_image": {
     "description": "The result carries a picture (fs_image), which is not in this frame. Present rather than sending megabytes of base64 over the socket: the client fetches the bytes from /api/branches/{id}/tools/{call_id}/image. Absent when there is no image.",
     "type": "boolean"
    },
    "name": {
     "type": "string"
    },
    "persisted": {
     "additionalProperties": false,
     "description": "Paths in the dispatch scratch holding output too large for the preview, keyed by stream. The client titles a later fs_read/fs_search of such a path with the call that produced it; the model is handed the same path in a note. Absent when nothing was persisted.",
     "properties": {
      "stderr": {
       "type": "string"
      },
      "stdout": {
       "type": "string"
      }
     },
     "type": "object"
    },
    "structured": {
     "description": "Structured output from a runner-native operation (e.g. a trash destination).",
     "type": [
      "object",
      "null"
     ]
    },
    "timed_out": {
     "type": "boolean"
    },
    "tool_call_id": {
     "$ref": "#/$defs/toolCallId"
    },
    "type": {
     "const": "tool_result"
    },
    "wall_ms": {
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "type",
    "tool_call_id",
    "name",
    "content",
    "exit_code",
    "wall_ms",
    "timed_out",
    "cancelled"
   ],
   "type": "object"
  },
  "truncated": {
   "additionalProperties": false,
   "description": "Outbound: the reply hit the token cap mid-thought and the turn is continuing from it. Advisory, like `waiting` \u2014 a client that ignores it is correct, just less informative. It exists because the alternative looked identical to a finished answer: `finish_reason: \"length\"` was recorded in the event log and read by nothing, so a generation cut off at 4096 tokens ended the turn as a clean stop and the user saw reasoning followed by silence (N90).",
   "properties": {
    "step": {
     "description": "Which step of the turn was cut off.",
     "minimum": 1,
     "type": "integer"
    },
    "type": {
     "const": "truncated"
    }
   },
   "required": [
    "type",
    "step"
   ],
   "type": "object"
  },
  "usage": {
   "additionalProperties": false,
   "description": "Outbound: token accounting for one model call, as the gateway reported it, with the window those tokens were spent against.",
   "properties": {
    "compacts_above": {
     "description": "The history size, in tokens, above which this turn will summarize the oldest span and continue. This is what a capacity bar should measure against, not `context_tokens`: the window reserves room for the reply, a margin and the compaction reserve before the conversation gets any, so on a 65,536-token model compaction fires at 30,311 and a bar against the window sat a little under half full at the moment it happened \u2014 which reads as a defect rather than as the designed behaviour. `context_tokens` remains the fact models are compared on and belongs in the detail; this is the number being approached. Absent when the window is unknown, like `context_tokens`.",
     "minimum": 1,
     "type": "integer"
    },
    "context_tokens": {
     "description": "The resolved model's context window, when the operator recorded one. Present so a client can show how full the context is: a token count without a denominator cannot be read, since 12,000 is comfortable on one model and fatal on another. Absent rather than zero when unknown, so a client draws nothing instead of a bar against a guessed ceiling \u2014 budgeting against an unrecorded window is what ended two sessions (N86).",
     "minimum": 1,
     "type": "integer"
    },
    "type": {
     "const": "usage"
    },
    "usage": {
     "properties": {
      "completion_tokens": {
       "minimum": 0,
       "type": "integer"
      },
      "prompt_tokens": {
       "minimum": 0,
       "type": "integer"
      },
      "total_tokens": {
       "minimum": 0,
       "type": "integer"
      }
     },
     "type": "object"
    }
   },
   "required": [
    "type",
    "usage"
   ],
   "type": "object"
  },
  "userMessage": {
   "additionalProperties": false,
   "description": "Outbound: the turn's opening input, or a steering message, echoed as it entered the log. `seq` is its position in the branch's log; the opening message's seq is the `at_seq` a fork resolves `before` to retry it.",
   "properties": {
    "content": {
     "type": "string"
    },
    "seq": {
     "minimum": 1,
     "type": "integer"
    },
    "type": {
     "const": "user_message"
    }
   },
   "required": [
    "type",
    "content",
    "seq"
   ],
   "type": "object"
  },
  "waiting": {
   "additionalProperties": false,
   "description": "Outbound: the turn has started and is waiting on something slow before any token can arrive. Advisory \u2014 a client that ignores it is correct, just less informative. Sent at most once per wait, never as a heartbeat.\n\nIt exists because a locally-hosted model may need to be loaded into VRAM on first use, which takes tens of seconds. Without a frame saying so the client shows nothing between send and the first token, which is indistinguishable from a hung appliance.",
   "properties": {
    "detail": {
     "description": "Human-readable, for display. Never parsed.",
     "maxLength": 200,
     "type": "string"
    },
    "on": {
     "description": "What is being waited on. `model` means the endpoint is reachable but not yet ready to generate \u2014 typically loading weights.",
     "enum": [
      "model"
     ]
    },
    "type": {
     "const": "waiting"
    }
   },
   "required": [
    "type",
    "on"
   ],
   "type": "object"
  },
  "writePreview": {
   "additionalProperties": false,
   "description": "What a mutating write would do, resolved by the tool itself before any verdict exists. Present on `tool_approval_required` for fs_write and fs_edit, and on a `tool_result`'s `structured` once the write has happened. Absent when the preview dispatch failed, which is not an error: an approval must still be askable when the diff is not, and a client without one falls back to rendering the call's arguments. Computed by the same code that performs the write, so it describes that write rather than offering a second opinion about it.",
   "properties": {
    "created": {
     "description": "True when the file does not exist yet, so there is nothing to diff against.",
     "type": "boolean"
    },
    "diff": {
     "description": "Unified diff, truncated for display; the whole patch is in the event log.",
     "type": "string"
    },
    "new_bytes": {
     "minimum": 0,
     "type": "integer"
    },
    "old_bytes": {
     "minimum": 0,
     "type": "integer"
    },
    "path": {
     "description": "Absolute path, as the tool resolved it.",
     "type": "string"
    },
    "rows": {
     "description": "The same diff as numbered rows, so a client renders line numbers without reimplementing the walk through hunk headers that carry them. Each row is {kind, old, new, text} where kind is context|add|remove|hunk and old/new are line numbers, null on the side where the line does not exist. The `diff` string stays authoritative: rows are for looking at, a patch is for applying.",
     "items": {
      "additionalProperties": false,
      "properties": {
       "kind": {
        "enum": [
         "context",
         "add",
         "remove",
         "hunk"
        ]
       },
       "new": {
        "minimum": 1,
        "type": [
         "integer",
         "null"
        ]
       },
       "old": {
        "minimum": 1,
        "type": [
         "integer",
         "null"
        ]
       },
       "text": {
        "type": "string"
       }
      },
      "required": [
       "kind",
       "old",
       "new",
       "text"
      ],
      "type": "object"
     },
     "type": "array"
    }
   },
   "required": [
    "path",
    "diff",
    "created",
    "old_bytes",
    "new_bytes"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "The WebSocket message vocabulary between the chat UI and the decision core, on /ws/branches/{branch_id}. A socket is one branch of a session: the branch is the transcript a turn appends to, and two branches of one session may have turns running at once. Authored once the harness inbox (D12) and the client surface existed \u2014 designing it earlier would have been guessing. The socket is authenticated on the upgrade by the session cookie, and Origin is checked explicitly because browsers do not apply CORS to WebSockets; this schema describes message shape only and confers no authority. Inbound messages come from the untrusted network surface (D1) and must be validated before they reach an inbox. Outbound messages are a projection of the branch's event log, never a second source of truth: the log is authoritative and a client that missed a frame re-reads the branch's session rather than reconstructing from the stream.",
 "oneOf": [
  {
   "$ref": "#/$defs/message"
  },
  {
   "$ref": "#/$defs/approval"
  },
  {
   "$ref": "#/$defs/steer"
  },
  {
   "$ref": "#/$defs/interrupt"
  },
  {
   "$ref": "#/$defs/userMessage"
  },
  {
   "$ref": "#/$defs/token"
  },
  {
   "$ref": "#/$defs/reasoning"
  },
  {
   "$ref": "#/$defs/toolProposal"
  },
  {
   "$ref": "#/$defs/toolApproval"
  },
  {
   "$ref": "#/$defs/toolDecision"
  },
  {
   "$ref": "#/$defs/toolResult"
  },
  {
   "$ref": "#/$defs/usage"
  },
  {
   "$ref": "#/$defs/truncated"
  },
  {
   "$ref": "#/$defs/compacted"
  },
  {
   "$ref": "#/$defs/error"
  },
  {
   "$ref": "#/$defs/done"
  },
  {
   "$ref": "#/$defs/waiting"
  }
 ],
 "properties": {
  "type": {
   "enum": [
    "message",
    "approval",
    "steer",
    "interrupt",
    "user_message",
    "token",
    "reasoning",
    "tool_proposal",
    "tool_approval_required",
    "tool_decision",
    "tool_result",
    "usage",
    "truncated",
    "compacted",
    "error",
    "done",
    "waiting"
   ]
  }
 },
 "required": [
  "type"
 ],
 "title": "ws",
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
