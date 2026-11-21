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
    }
   ],
   "description": "A path a `files_read` or `files_write` entry names: an absolute path, or exactly `$HOME` for the acting account's home directory. `$HOME` is the spelling to prefer whenever the client may not be on the server's machine \u2014 it names the home of whichever machine runs the tool, which the client reports, while an absolute path names one machine's directory (N145). The token means the home, whole: dotfiles such as `~/.ssh` included. A role that wants particular folders names them, and an account on another machine names that machine's paths (D105).",
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
