"""Generated from schemas/tool-manifest.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "tool": {
   "additionalProperties": false,
   "allOf": [
    {
     "if": {
      "properties": {
       "daemon": {
        "const": false
       },
       "kind": {
        "const": "client"
       }
      }
     },
     "then": {
      "required": [
       "exec"
      ]
     }
    },
    {
     "if": {
      "properties": {
       "daemon": {
        "const": true
       }
      },
      "required": [
       "daemon"
      ]
     },
     "then": {
      "not": {
       "required": [
        "exec"
       ]
      },
      "properties": {
       "kind": {
        "const": "client"
       }
      }
     }
    },
    {
     "if": {
      "properties": {
       "kind": {
        "const": "server-native"
       }
      }
     },
     "then": {
      "not": {
       "required": [
        "exec"
       ]
      }
     }
    },
    {
     "if": {
      "properties": {
       "returns_image": {
        "const": true
       }
      },
      "required": [
       "returns_image"
      ]
     },
     "then": {
      "properties": {
       "persist_output": {
        "const": false
       }
      },
      "required": [
       "persist_output"
      ]
     }
    }
   ],
   "properties": {
    "critical": {
     "description": "Always ask for a verdict, even under auto_accept 'yolo'. Names the consequence rather than a property: it was `irreversible`, which was true of `move` and a lie about anything else needing a human in the loop \u2014 reading the whole appliance is critical without being undoable.",
     "type": "boolean"
    },
    "daemon": {
     "default": false,
     "description": "A `client` tool the client daemon answers itself, with no process: `job_output` and `job_kill`, which read and stop the background jobs the daemon holds (D98). A subprocess cannot see those, so there is no program to name. Still a client tool in D13's sense \u2014 it executes on the acting human's machine, against state that exists only there.",
     "type": "boolean"
    },
    "description": {
     "description": "What the tool does, written for the *model*: the full instructions, caveats and worked argument shapes it needs to call the thing correctly. Advertised in the request's tool schemas. Length is a feature here \u2014 it is the only place a tool can warn about what its environment lacks \u2014 so it is not what a human reads in a list. See `summary`.",
     "minLength": 1,
     "type": "string"
    },
    "diagnostic": {
     "default": false,
     "description": "This tool is dispatched by the appliance and never chosen by a person, so the client leaves it out of the tool chooser. Two kinds qualify. Appliance test scaffolding, which is where the flag came from \u2014 none of that ships; the four that did proved sandbox mechanisms that no longer exist. And a tool a *route* dispatches to answer its own question, which `env_probe` is: the chat window asks it what a working directory's development environment is, and no baseline grants it, because a model wanting the same answer has `python(action=\"describe\")` (D33).\n\nThe test is the effect, not the category: an ungranted row renders struck through under \"an operator grants tools in `services.neuman`\", which is an invitation to grant a capability that is not one.\n\nDeclared rather than inferred from a name list, because the alternative is two lists \u2014 a set of names in the client, another in the nix tests \u2014 drifting apart (N48). It changes **presentation only** (D50): it confers nothing and withholds nothing, and the capability gate decides dispatch for a diagnostic exactly as for any other tool.",
     "type": "boolean"
    },
    "exec": {
     "description": "Executable name resolved from the client's own trusted tool directory \u2014 never a path (D3). Required for `client` unless `daemon`; forbidden for `server-native` and for a `daemon` tool.",
     "pattern": "^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
     "type": "string"
    },
    "kind": {
     "enum": [
      "client",
      "server-native"
     ]
    },
    "lifetime_ms": {
     "description": "The hard cap on the process, measured from spawn: a background job still running at it is killed. At least `wait_ms` (the registry and the client refuse a manifest where it is less). Equal to `wait_ms` means the tool never becomes a job.",
     "maximum": 3600000,
     "minimum": 1,
     "type": "integer"
    },
    "max_output_bytes": {
     "description": "The most output the client will forward. For a `persist_output` tool that is the ceiling of the file it writes to the client's spill directory; for an `inline` tool it is the most streamed to the core.",
     "maximum": 33554432,
     "minimum": 1,
     "type": "integer"
    },
    "mutating": {
     "type": "boolean"
    },
    "name": {
     "description": "Must equal the directory name containing the manifest.",
     "pattern": "^[a-z_][a-z0-9_]*$",
     "type": "string"
    },
    "needs": {
     "description": "The reach a user must hold for the capability gate to admit the tool (needs \u2286 reach). A tool that omits `network` may still use the user's own network \u2014 on the client there is no profile to narrow \u2014 so this is the gate's input, not a constraint on what the client's environment allows.",
     "items": {
      "enum": [
       "read",
       "write",
       "network"
      ]
     },
     "type": "array",
     "uniqueItems": true
    },
    "parameters": {
     "description": "A JSON Schema (draft 2020-12) for the dispatch args advertised to the model; {} when the tool takes none.",
     "type": "object"
    },
    "paths": {
     "description": "Parameter names whose values are filesystem paths. The client resolves each with `realpath` and refuses one outside the account's declared surfaces before the tool runs, so a symlink inside a granted directory that points out of it is caught where a lexical check would miss it. A guardrail against a confused or injected model rather than a boundary: `shell` and `python` can reach anything the user can, and a tool that declares no paths is not checked at all.",
     "items": {
      "type": "string"
     },
     "type": "array",
     "uniqueItems": true
    },
    "persist_output": {
     "default": true,
     "description": "True (the default) means output past a small preview is written to a file in the client's spill directory, which the read tools reach whatever the account's surfaces, and the model is handed the preview plus the path. False is for tools that bound their own output by the caller's arguments and deliver it inline \u2014 a paging reader, a search result \u2014 and for tools whose stdout the server transforms rather than shows, such as an image's base64 or a write's diff. A tool that never declares it persists, because the failure of delivering a huge result inline is the one this field exists to prevent.",
     "type": "boolean"
    },
    "returns_image": {
     "default": false,
     "description": "The tool's stdout is one picture, written as `<media type> <base64>`, and the core lifts it into an image the model sees rather than text it reads (D28). Declared rather than recognised by name, because more than one tool produces a picture \u2014 `fs_image` reads one from disk, `blender_view` captures one from a live viewport \u2014 and a name in the harness is one more list to drift. A model with no vision is refused such a tool before dispatch. Requires `persist_output = false`: the core transforms the stdout, so a spilled preview of base64 would stand for nothing.",
     "type": "boolean"
    },
    "summary": {
     "description": "One short line, written for a *person*: what the tool does, in a handful of words, for the client's tool chooser (D50). Separate from `description` because the two audiences want opposite things \u2014 the model needs every caveat, and a human scanning nineteen checkboxes needs a label. Capped at 80 characters so it cannot quietly grow back into a paragraph; a chooser row that wraps to four lines is the thing this field exists to prevent. Optional: a tool without one falls back to its `description`, truncated by the client.",
     "maxLength": 80,
     "minLength": 1,
     "type": "string"
    },
    "wait_ms": {
     "description": "How long a call blocks before the model gets control back. A process still running then is killed when `lifetime_ms` equals this, and otherwise continues as a background job the model follows with `job_output` (D98). The core may send a longer wait, up to `lifetime_ms`, for a command it wrote itself (a task's run step, D96); it never takes one from the model.",
     "maximum": 3600000,
     "minimum": 1,
     "type": "integer"
    }
   },
   "required": [
    "name",
    "kind",
    "description",
    "wait_ms",
    "lifetime_ms",
    "max_output_bytes",
    "mutating",
    "critical",
    "needs",
    "parameters"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "additionalProperties": false,
 "description": "The parsed content of one tools/<name>/manifest.toml (TOML maps to JSON). Both installations of the contract \u2014 the server's registry and the client's own manifest set \u2014 validate against this schema, and CI proves they accept the same set (D2, D3). The kind must be true of where the tool executes (D13): `client` tools are run by the acting human's own machine, and `server-native` tools are answered by the decision core before any dispatch. There is no third place: a tool runs either on the acting human's own machine or in the core.",
 "properties": {
  "tool": {
   "$ref": "#/$defs/tool"
  }
 },
 "required": [
  "tool"
 ],
 "title": "tool-manifest",
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
