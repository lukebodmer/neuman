"""Generated from schemas/presets.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "dialect": {
   "additionalProperties": false,
   "description": "The reasoning dialect and the levels this route accepts: baseline.schema.json#/$defs/reasoning without the default, which is policy.",
   "properties": {
    "control": {
     "enum": [
      "effort",
      "budget",
      "preserved",
      "toggle"
     ]
    },
    "levels": {
     "items": {
      "minLength": 1,
      "type": "string"
     },
     "minItems": 1,
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "control",
    "levels"
   ],
   "type": "object"
  },
  "id": {
   "pattern": "^[a-z0-9][a-z0-9_-]*$",
   "type": "string"
  },
  "label": {
   "minLength": 1,
   "type": "string"
  },
  "localFacts": {
   "additionalProperties": false,
   "properties": {
    "model_flags": {
     "description": "Flags the model needs on any hardware, such as `--jinja`, without which llama.cpp uses a generic chat template and the model's tool calls do not parse. Flags that are the machine's choice are policy fields, never these.",
     "items": {
      "pattern": "^-",
      "type": "string"
     },
     "type": "array",
     "uniqueItems": true
    },
    "reasoning": {
     "$ref": "#/$defs/dialect"
    },
    "vram": {
     "$ref": "#/$defs/vram"
    },
    "weights": {
     "$ref": "#/$defs/weights"
    }
   },
   "required": [
    "weights"
   ],
   "type": "object"
  },
  "localPolicy": {
   "additionalProperties": false,
   "description": "Starting values the operator may change. Typed fields, not a command line: the runtime builds its own argv from these, so a stored document can never hand it an arbitrary flag or file.",
   "properties": {
    "context_size": {
     "description": "The window the runtime is started with (`--ctx-size`). A VRAM decision, not a fact about the model.",
     "minimum": 1,
     "type": "integer"
    },
    "gpu_layers": {
     "description": "Layers offloaded to the GPU; 99 is all of any model shipped here.",
     "minimum": 0,
     "type": "integer"
    },
    "idle_minutes": {
     "description": "How long an on-demand model stays loaded unused.",
     "minimum": 1,
     "type": "integer"
    },
    "kv_cache": {
     "description": "The number format of the attention cache, both K and V (`--cache-type-k`, `--cache-type-v`). Absent is `f16`, llama.cpp's own. `q8_0` holds the cache in about half the memory, so the same card affords about twice the window, and decodes a little slower once the window is full. Only matched pairs the CUDA build has flash-attention kernels for are offered: any other type drops attention to the CPU without a word (N100).",
     "enum": [
      "f16",
      "q8_0"
     ]
    },
    "max_tokens": {
     "minimum": 1,
     "type": "integer"
    },
    "reasoning_default": {
     "minLength": 1,
     "type": "string"
    },
    "residency": {
     "enum": [
      "always",
      "on-demand"
     ]
    },
    "speculation": {
     "description": "Speculative decoding against the model's own multi-token-prediction head. Policy because the draft context costs VRAM the window could have.",
     "enum": [
      "none",
      "draft-mtp"
     ]
    }
   },
   "type": "object"
  },
  "localRoute": {
   "additionalProperties": false,
   "description": "The model run on this machine's GPU. Separate from `routes` because nothing here is a provider's: the weights are fetched from Hugging Face into `modelsDir`, and the runtime is started with the flags below.",
   "properties": {
    "facts": {
     "$ref": "#/$defs/localFacts"
    },
    "name": {
     "description": "The catalog name the local model is added under, when it must differ from the model's own: the same weights hosted elsewhere are a second entry, and two entries cannot share the name a user picks.",
     "pattern": "^[A-Za-z0-9_.-]+$",
     "type": "string"
    },
    "notes": {
     "$ref": "#/$defs/notes"
    },
    "policy": {
     "$ref": "#/$defs/localPolicy"
    },
    "verified": {
     "description": "When and from what the facts were read: the Hub API for the pin, a run on named hardware for `vram`.",
     "minLength": 1,
     "type": "string"
    }
   },
   "required": [
    "verified",
    "facts"
   ],
   "type": "object"
  },
  "logo": {
   "$ref": "#/$defs/id",
   "description": "A file stem under the client's `logos/` directory. The client draws a monogram when the file is absent."
  },
  "model": {
   "additionalProperties": false,
   "anyOf": [
    {
     "required": [
      "routes"
     ]
    },
    {
     "required": [
      "local"
     ]
    }
   ],
   "properties": {
    "facts": {
     "$ref": "#/$defs/sharedFacts"
    },
    "label": {
     "$ref": "#/$defs/label"
    },
    "local": {
     "$ref": "#/$defs/localRoute"
    },
    "logo": {
     "$ref": "#/$defs/logo"
    },
    "name": {
     "description": "The catalog name the model is added under unless the operator chooses another: the string a user picks in the composer and a role's `models` grant names.",
     "pattern": "^[A-Za-z0-9_.-]+$",
     "type": "string"
    },
    "notes": {
     "$ref": "#/$defs/notes"
    },
    "policy": {
     "$ref": "#/$defs/policy"
    },
    "routes": {
     "additionalProperties": {
      "$ref": "#/$defs/route"
     },
     "description": "Provider preset id -> what is true of this model reached there. A model reached through two providers shares its window and nothing else: the id, the output ceiling, the prices and which reasoning levels are forwarded all belong to the route. Absent for a model offered only on this machine.",
     "minProperties": 1,
     "propertyNames": {
      "$ref": "#/$defs/id"
     },
     "type": "object"
    }
   },
   "required": [
    "name",
    "label"
   ],
   "type": "object"
  },
  "notes": {
   "description": "One or two sentences an operator reads before choosing: what this is for, and what it trades.",
   "minLength": 1,
   "type": "string"
  },
  "policy": {
   "additionalProperties": false,
   "description": "Starting values the operator may change after adding the model. A route's policy is merged over the model's.",
   "properties": {
    "cached_input_cost_per_m": {
     "minimum": 0,
     "type": "number"
    },
    "input_cost_per_m": {
     "minimum": 0,
     "type": "number"
    },
    "max_tokens": {
     "minimum": 1,
     "type": "integer"
    },
    "output_cost_per_m": {
     "minimum": 0,
     "type": "number"
    },
    "reasoning_default": {
     "description": "Must be one of the route's reasoning levels; the server checks this when it loads the presets.",
     "minLength": 1,
     "type": "string"
    }
   },
   "type": "object"
  },
  "provider": {
   "additionalProperties": false,
   "properties": {
    "key_url": {
     "description": "Where an operator creates a key for this provider.",
     "pattern": "^https://",
     "type": "string"
    },
    "label": {
     "$ref": "#/$defs/label"
    },
    "logo": {
     "$ref": "#/$defs/logo"
    },
    "notes": {
     "$ref": "#/$defs/notes"
    },
    "url": {
     "description": "The OpenAI-compatible base URL; `/chat/completions` is appended. https only: a preset is a hosted provider, and the key crosses on every request.",
     "pattern": "^https://",
     "type": "string"
    }
   },
   "required": [
    "label",
    "url"
   ],
   "type": "object"
  },
  "route": {
   "additionalProperties": false,
   "properties": {
    "facts": {
     "$ref": "#/$defs/routeFacts"
    },
    "notes": {
     "$ref": "#/$defs/notes"
    },
    "policy": {
     "$ref": "#/$defs/policy"
    },
    "verified": {
     "description": "When and from what the route's facts were read, e.g. `2026-10-16, GET https://openrouter.ai/api/v1/models`. A fact without a source is a guess.",
     "minLength": 1,
     "type": "string"
    }
   },
   "required": [
    "verified",
    "facts"
   ],
   "type": "object"
  },
  "routeFacts": {
   "additionalProperties": false,
   "properties": {
    "context_tokens": {
     "minimum": 1,
     "type": "integer"
    },
    "provider_model": {
     "minLength": 1,
     "type": "string"
    },
    "reasoning": {
     "$ref": "#/$defs/dialect"
    },
    "vision": {
     "type": "boolean"
    }
   },
   "required": [
    "provider_model"
   ],
   "type": "object"
  },
  "sharedFacts": {
   "additionalProperties": false,
   "properties": {
    "context_tokens": {
     "minimum": 1,
     "type": "integer"
    },
    "reasoning": {
     "$ref": "#/$defs/dialect"
    },
    "vision": {
     "type": "boolean"
    }
   },
   "type": "object"
  },
  "vram": {
   "additionalProperties": false,
   "description": "What the runtime held on the GPU at the route's default policy. Measured, not computed: it depends on the window, the cache type, the projector and speculation as much as on the file. A proposal for whether the model fits, never a refusal.",
   "properties": {
    "measured": {
     "description": "Where and how, e.g. `2026-11-02, RTX 3090, nvidia-smi per-process`.",
     "minLength": 1,
     "type": "string"
    },
    "mib": {
     "minimum": 1,
     "type": "integer"
    }
   },
   "required": [
    "mib",
    "measured"
   ],
   "type": "object"
  },
  "weights": {
   "additionalProperties": false,
   "description": "What to download, pinned. A revision rather than a branch, and a hash per file, so the bytes a release was measured against are the bytes every appliance runs.",
   "properties": {
    "files": {
     "items": {
      "$ref": "#/$defs/weightsFile"
     },
     "minItems": 1,
     "type": "array"
    },
    "repo": {
     "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
     "type": "string"
    },
    "revision": {
     "description": "A commit, never `main`.",
     "pattern": "^[0-9a-f]{40}$",
     "type": "string"
    }
   },
   "required": [
    "repo",
    "revision",
    "files"
   ],
   "type": "object"
  },
  "weightsFile": {
   "additionalProperties": false,
   "properties": {
    "name": {
     "description": "The file's name under `modelsDir`, and in the repository unless `path` says otherwise. No directories: a name that could leave `modelsDir` is refused by the schema rather than by every reader.",
     "pattern": "^[A-Za-z0-9_.-]+$",
     "type": "string"
    },
    "path": {
     "description": "Where the file is in the repository when that is not `name` at its root, as for a quantisation published under a directory of its own. Only the download reads it: the file is saved as `name`, so every shard of a split model sits beside the first, where llama.cpp looks for them.",
     "pattern": "^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$",
     "type": "string"
    },
    "role": {
     "description": "`model` is the weights: one file, or every shard of a split GGUF in order (`-00001-of-0000N.gguf` first), of which the runtime is started with the first and finds the rest beside it. `mmproj` is the vision projector. A model sees exactly when it has one.",
     "enum": [
      "model",
      "mmproj"
     ]
    },
    "sha256": {
     "pattern": "^[0-9a-f]{64}$",
     "type": "string"
    },
    "size": {
     "minimum": 1,
     "type": "integer"
    }
   },
   "required": [
    "name",
    "size",
    "sha256",
    "role"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "additionalProperties": false,
 "description": "The providers and models Neuman supports, as the server ships them (D76). An operator adds a provider from a preset by supplying a key, and a model by choosing it on a provider they have; the server builds the baseline entry from the preset and refuses a later edit that contradicts one of its facts. Facts are what the model or the route *is* -- the window, whether it can see, the reasoning dialect and the levels a route forwards, the provider's id -- and belong to the release. Policy is what the operator may change: reply length, prices, the default reasoning level. A model reached on this machine's GPU is a `local` route beside the hosted ones: its facts are the weights pinned to a Hugging Face commit and the flags the model needs, and its policy is what the machine chooses. Assembled by the server from one file per entry under `neuman_server/presets/`, keyed by the file's stem.",
 "properties": {
  "models": {
   "additionalProperties": {
    "$ref": "#/$defs/model"
   },
   "propertyNames": {
    "$ref": "#/$defs/id"
   },
   "type": "object"
  },
  "providers": {
   "additionalProperties": {
    "$ref": "#/$defs/provider"
   },
   "propertyNames": {
    "$ref": "#/$defs/id"
   },
   "type": "object"
  }
 },
 "required": [
  "providers",
  "models"
 ],
 "title": "presets",
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
