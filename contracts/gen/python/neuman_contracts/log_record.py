"""Generated from schemas/log-record.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "additionalProperties": false,
 "description": "The canonical log record, emitted by the decision core (`foundation/logging.py`). The fields are named once here, with golden fixtures beside them, so a change to the record is a change to a contract rather than to a format string (D10).",
 "properties": {
  "fields": {
   "additionalProperties": {
    "type": [
     "string",
     "number",
     "boolean",
     "null"
    ]
   },
   "description": "Structured context; scalar values only \u2014 nested structure belongs in the audit chain, not the log.",
   "type": "object"
  },
  "level": {
   "enum": [
    "debug",
    "info",
    "warning",
    "error"
   ]
  },
  "message": {
   "minLength": 1,
   "type": "string"
  },
  "request_id": {
   "description": "Correlates every record emitted while handling one request.",
   "maxLength": 64,
   "minLength": 8,
   "type": "string"
  },
  "target": {
   "description": "Dotted component name, e.g. neuman.server, neuman.runner.",
   "pattern": "^[a-z0-9][a-z0-9._-]*$",
   "type": "string"
  },
  "ts": {
   "description": "RFC 3339 UTC timestamp.",
   "format": "date-time",
   "type": "string"
  }
 },
 "required": [
  "ts",
  "level",
  "target",
  "message"
 ],
 "title": "log-record",
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
