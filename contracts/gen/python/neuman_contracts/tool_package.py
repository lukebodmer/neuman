"""Generated from schemas/tool-package.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "package": {
   "additionalProperties": false,
   "properties": {
    "description": {
     "description": "What the package lets someone do, what it needs, and what enabling it exposes \u2014 written for the operator, not the model. Each tool's own `description` is what the model is told.",
     "minLength": 1,
     "type": "string"
    },
    "host": {
     "additionalProperties": false,
     "description": "A long-running companion \u2014 a program in a window the user works in \u2014 that the package's tools talk to over a `0600` Unix socket in the user's runtime directory. The client starts it when one of the tools is dispatched and nothing answers, and leaves it running: closing its window ends it, and the next call starts it again.",
     "properties": {
      "exec": {
       "description": "Resolved from the client's own trusted tool directory, exactly as a manifest's `exec` is (D3) \u2014 never a path. No manifest names it, so no dispatch can run it.",
       "pattern": "^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
       "type": "string"
      }
     },
     "required": [
      "exec"
     ],
     "type": "object"
    },
    "id": {
     "description": "Must equal the file's name without `.toml`. The name the baseline's `packages` lists.",
     "pattern": "^[a-z][a-z0-9_]*$",
     "type": "string"
    },
    "publisher": {
     "description": "Who wrote it. Both are reviewed into the release; `community` says the tools came from outside the project and the project maintains them less closely.",
     "enum": [
      "neuman",
      "community"
     ]
    },
    "requires": {
     "additionalProperties": false,
     "description": "A program the user installs on their own machine for the tools to work. Neuman never downloads it: when it is missing, a dispatch is refused with `missing_program` and a sentence naming it and where to get it, which the model passes on.",
     "properties": {
      "name": {
       "description": "What a person calls it.",
       "minLength": 1,
       "type": "string"
      },
      "program": {
       "description": "The executable's name, looked for on the PATH the Neuman client runs with.",
       "pattern": "^[a-zA-Z0-9][a-zA-Z0-9._+-]*$",
       "type": "string"
      },
      "url": {
       "description": "Where to get it.",
       "pattern": "^https://",
       "type": "string"
      }
     },
     "required": [
      "program",
      "name",
      "url"
     ],
     "type": "object"
    },
    "summary": {
     "description": "One line under the heading, for a person deciding whether to enable it.",
     "maxLength": 120,
     "minLength": 1,
     "type": "string"
    },
    "title": {
     "description": "The tile's heading.",
     "maxLength": 40,
     "minLength": 1,
     "type": "string"
    },
    "tools": {
     "description": "The tool names this package holds. Each must have a manifest, and a tool belongs to at most one package.",
     "items": {
      "pattern": "^[a-z_][a-z0-9_]*$",
      "type": "string"
     },
     "minItems": 1,
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "id",
    "title",
    "summary",
    "description",
    "publisher",
    "tools"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "additionalProperties": false,
 "description": "The parsed content of one tools/packages/<id>.toml: a set of tools an operator enables together from the administration page's Tools section (D101). A tool no package names is built in and always present. A package is part of the release \u2014 its tools' manifests and programs ship with the server and the client like every other tool's \u2014 so enabling one downloads nothing and never hands a client code it does not already hold (D3). What it may need on the user's machine is a program the user installs themselves (`requires`), and a long-running companion the client starts when a tool needs it (`host`).",
 "properties": {
  "package": {
   "$ref": "#/$defs/package"
  }
 },
 "required": [
  "package"
 ],
 "title": "tool-package",
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
