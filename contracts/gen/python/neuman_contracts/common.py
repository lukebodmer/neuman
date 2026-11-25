"""Generated from schemas/common.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "absPath": {
   "allOf": [
    {
     "not": {
      "pattern": "/$"
     }
    },
    {
     "not": {
      "pattern": "(^|/)\\.\\.($|/)"
     }
    }
   ],
   "description": "An absolute path without a trailing slash and without parent segments. Shape only: whether a path is within an account's reach is policy's answer, computed by the server from the composed fragments.",
   "pattern": "^/",
   "type": "string"
  },
  "machineLabel": {
   "description": "A machine's name, within one account. The label is the machine's identity: a branch names it, policy grants it, the record carries it, and a client says it at the handshake. Scoped to an account rather than global, because reach differs per person: Alice's `dgx` and Bob's `dgx` are the same physical box and two different machines. Lower case because people type it and strings compare it, and a name differing only in case would be two machines or one depending on who is asking.",
   "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$",
   "type": "string"
  },
  "network": {
   "description": "Whether the layer grants the network. `none` withholds every tool declaring `needs = [\"network\"]`; `any` admits them; null inherits. There is no host list: those tools run on the acting user's own machine over their network, so Neuman cannot narrow where they connect, and a list nothing enforced would read as a control that does not exist (N146).",
   "enum": [
    "none",
    "any",
    null
   ]
  },
  "policyPath": {
   "anyOf": [
    {
     "$ref": "#/$defs/absPath"
    },
    {
     "const": "$HOME"
    },
    {
     "not": {
      "pattern": "(^|/)\\.\\.?($|/)"
     },
     "pattern": "^\\$HOME(/[^/]+)+$",
     "type": "string"
    }
   ],
   "description": "A path a `files_read` or `files_write` entry names: an absolute path, or a path under the acting account's home written from its directory \u2014 `$HOME` for the home itself, `$HOME/Documents` for a folder in it, `$HOME/projects/neuman` for something deeper. The token is the spelling to prefer whenever the client may not be on the server's machine: it names the home of whichever machine runs the tool, which the client reports, while an absolute path names one machine's directory (N145). It also keeps a role valid across operating systems, where the home is `/home/<user>` or `/Users/<user>`. The bare token means the home whole, dotfiles included; a grant that wants particular folders names them (D105, D106).",
   "type": "string"
  },
  "userName": {
   "description": "A Linux account name.",
   "pattern": "^[a-z_][a-z0-9_-]{0,31}$",
   "type": "string"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "Definitions shared across contracts. This file describes no instance on its own; other schemas reference its definitions.",
 "title": "common"
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
