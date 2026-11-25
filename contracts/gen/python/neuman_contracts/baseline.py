"""Generated from schemas/baseline.schema.json by contracts/codegen — do not edit."""

import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._registry import RESOURCES

SCHEMA = json.loads(r"""
{
 "$defs": {
  "fragment": {
   "additionalProperties": false,
   "description": "One policy layer. Every field is optional; null means inherit. A baseline fragment lists what it grants: the subtractive {extra,only,remove} form is valid only in the runtime overlay, which this schema does not describe, so a reader of the declared configuration sees the whole grant by reading forward.",
   "properties": {
    "auto_accept": {
     "description": "Approval mode. 'never' asks for every call, 'safe' asks for mutating or critical ones, 'yolo' still asks before a critical action (D23). A user may change this for themselves within `auto_accept_max`, so a value here is a starting point rather than a fixed setting.",
     "enum": [
      "never",
      "safe",
      "yolo",
      null
     ]
    },
    "auto_accept_max": {
     "description": "The most permissive approval mode this layer allows the account to choose. Declared policy, not runtime tuning: `auto_accept` is what is in force and a user may move it, and this is the operator's bound on that movement \u2014 a role capped at 'safe' can stop approving every read and still cannot authorise its own destructive work. Null inherits; the composed default is 'yolo', which bounds nothing, because the mode actually in force defaults to 'safe' and tightening that is what a limit is for.",
     "enum": [
      "never",
      "safe",
      "yolo",
      null
     ]
    },
    "files_read": {
     "items": {
      "$ref": "common.schema.json#/$defs/policyPath"
     },
     "type": [
      "array",
      "null"
     ],
     "uniqueItems": true
    },
    "files_write": {
     "items": {
      "$ref": "common.schema.json#/$defs/policyPath"
     },
     "type": [
      "array",
      "null"
     ],
     "uniqueItems": true
    },
    "machines": {
     "description": "The machines this layer may dispatch to, by label. An empty or absent list is not a refusal: enrolment is the grant, so naming machines *narrows* to the ones a role should use and never widens past a machine the account's own client logged in from. A non-empty list is a ceiling checked at the gate like any other reach.",
     "items": {
      "$ref": "common.schema.json#/$defs/machineLabel"
     },
     "type": [
      "array",
      "null"
     ],
     "uniqueItems": true
    },
    "max_iterations": {
     "description": "Model/tool steps one turn may take before the harness stops.",
     "minimum": 1,
     "type": [
      "integer",
      "null"
     ]
    },
    "models": {
     "description": "Model catalog names the layer grants; matched against the `models` keys declared in this same baseline. Granting a name that is not declared is inert rather than an error, the same way policy for an unresolvable account is.",
     "items": {
      "minLength": 1,
      "type": "string"
     },
     "type": [
      "array",
      "null"
     ],
     "uniqueItems": true
    },
    "network": {
     "$ref": "common.schema.json#/$defs/network"
    },
    "tokens_daily": {
     "description": "Per-account daily token budget enforced by the core's own accounting (D20); null inherits, and no layer setting it means unlimited.",
     "minimum": 1,
     "type": [
      "integer",
      "null"
     ]
    },
    "tools": {
     "items": {
      "pattern": "^[a-z_][a-z0-9_]*$",
      "type": "string"
     },
     "type": [
      "array",
      "null"
     ],
     "uniqueItems": true
    }
   },
   "type": "object"
  },
  "localPolicy": {
   "additionalProperties": false,
   "description": "What the machine chooses for a local runtime, beyond the window and reply length the entry already states. Typed values only: the runtime host builds its command line from these and the preset, so nothing stored here can become an arbitrary argument.",
   "properties": {
    "gpu_layers": {
     "maximum": 999,
     "minimum": 0,
     "type": "integer"
    },
    "idle_minutes": {
     "maximum": 1440,
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
    "residency": {
     "enum": [
      "always",
      "on-demand"
     ]
    },
    "speculation": {
     "enum": [
      "none",
      "draft-mtp"
     ]
    }
   },
   "type": "object"
  },
  "model": {
   "additionalProperties": false,
   "description": "One catalog entry. Every capability here is *stated* rather than detected: a provider's model id carries none of it, and guessing in the permissive direction surfaces as a provider error mid-turn, after the tool has run and the tokens are spent. A model on this machine's GPU names no provider: it has `local` instead, its endpoint is the appliance's runtime host, and it must come from a preset (D78).",
   "oneOf": [
    {
     "required": [
      "provider"
     ]
    },
    {
     "required": [
      "local"
     ]
    }
   ],
   "properties": {
    "cached_input_cost_per_m": {
     "description": "Cost per million input tokens that hit the provider's prompt cache. Absent means this provider does not discount them and `input_cost_per_m` prices the whole prompt. Separate because the discount is not a rounding error: DeepSeek charges $0.003 against $0.15, a factor of fifty, and an agentic turn resends the same prefix every time -- so pricing every prompt token at the miss rate overstates a warm session by up to that factor.",
     "minimum": 0,
     "type": "number"
    },
    "context_tokens": {
     "description": "The context window, in tokens: the number that decides whether a turn fits. When it was known to nobody, a local model serving 16,384 had its history managed against a 96,000-token default, so compaction never fired and the turn died at the provider instead (N86). 0 means unknown, and unknown is handled by not pretending -- the context manager keeps its own default and the client draws no capacity bar at all rather than one against a guessed denominator.",
     "minimum": 0,
     "type": "integer"
    },
    "input_cost_per_m": {
     "description": "Cost per million input tokens that missed the provider's prompt cache, shown in the model panel and used to price a turn when the provider reports no cost of its own. A user choosing between \"free, on my GPU, slower\" and \"paid, someone else's, faster\" is making an informed choice only if both facts are in front of them.",
     "minimum": 0,
     "type": "number"
    },
    "local": {
     "$ref": "#/$defs/localPolicy",
     "description": "Run on this machine by `neuman-runtime`, with these settings. `provider_model` is then the catalog name itself, the window the runtime is started with is `context_tokens`, and `preset` is required: the runtime starts only what a shipped preset describes, so there is no hand-declared local model."
    },
    "max_tokens": {
     "description": "The most this model may emit in one reply. A property of the model, like the window beside it: one appliance-wide number can only ever be right for one model (N93). A ceiling rather than a fixed ask, since the harness clamps it per turn to what the window can still hold (N92). 0 defers to the server's configured default.",
     "minimum": 0,
     "type": "integer"
    },
    "output_cost_per_m": {
     "description": "Cost per million output tokens.",
     "minimum": 0,
     "type": "number"
    },
    "preset": {
     "description": "The model preset this entry was added from. Its facts -- `provider_model`, `context_tokens`, `vision`, and the reasoning `control` and `levels` -- are those the preset states for the route its provider records, and a write that contradicts one is refused by name; the server re-applies them at startup, so a corrected fact reaches an appliance with the release that corrects it. Absent for a model declared by hand, which carries every claim unchecked.",
     "pattern": "^[a-z0-9][a-z0-9_-]*$",
     "type": "string"
    },
    "provider": {
     "description": "The `providers` key serving this model. A write naming a provider that is not declared is refused, because a dangling name is otherwise a 401 or a wrong-host request at turn time. Absent for a model run on this machine.",
     "minLength": 1,
     "type": "string"
    },
    "provider_model": {
     "description": "What the provider calls this model. Distinct from the catalog name so a session keeps a stable name while an operator re-points it, and so two providers can offer one capability under different strings. This translation existed in the schema and nowhere in the code once already: the local name went on the wire and the provider answered \"deepseek is not a valid model ID\" (N48).",
     "minLength": 1,
     "type": "string"
    },
    "reasoning": {
     "$ref": "#/$defs/reasoning"
    },
    "vision": {
     "description": "Whether the model accepts image input, which is what allows `fs_image`. Stated, because the id does not say -- `deepseek-v4.1-flash` accepts images and `deepseek-v4-flash` does not, and a local GGUF needs a separate projector file besides. Absent means text-only, so the gate refuses with a reason the model can act on rather than failing at the provider.",
     "type": "boolean"
    }
   },
   "required": [
    "provider_model"
   ],
   "type": "object"
  },
  "provider": {
   "additionalProperties": false,
   "description": "One OpenAI-compatible endpoint and its credential. Neuman hosts no models (D20), so a hosted provider and a model on this machine's GPU differ only in these two fields.",
   "properties": {
    "api_key_file": {
     "$comment": "Not common.schema.json#/$defs/absPath: that forbids a trailing slash and parent segments, both of which this also wants, but the null case needs the union type and absPath is a bare string schema.",
     "description": "A *path* to a file holding the key, never the key itself (design principle 8, `docs/decisions.md`). A key entered in the administration page is written by the server to its own secrets directory, `0600` and owned by `neuman`, and this names that file (D77); a key placed by a secret manager is named the same way. The document is returned to every admin read and copied into backups, so a literal here would be published to both. Null for a provider needing no credential, which is the ordinary case for a model served on loopback.",
     "pattern": "^/",
     "type": [
      "string",
      "null"
     ]
    },
    "preset": {
     "description": "The provider preset this entry was added from (presets.schema.json). Absent for a provider declared by hand. A model added from a preset may only name a provider that records one of its routes.",
     "pattern": "^[a-z0-9][a-z0-9_-]*$",
     "type": "string"
    },
    "url": {
     "description": "The base URL. `/chat/completions` is appended to it, so this is the part before that -- `https://api.deepseek.com/v1`, not the completions path itself.",
     "minLength": 1,
     "type": "string"
    }
   },
   "required": [
    "url"
   ],
   "type": "object"
  },
  "reasoning": {
   "additionalProperties": false,
   "description": "How this model is asked to reason, and at what levels. A capability, not a tuning knob: the dialects below are mutually unintelligible, and which one a model speaks is a fact about the model that its id does not carry -- the same reason `vision` and `context_tokens` are stated rather than detected.\n\nAbsent means the model is not asked to reason at all, which is correct for a model without the capability and for one whose default is already right. The client draws no control in that case: a control that cannot work is not drawn rather than drawn and broken.",
   "properties": {
    "control": {
     "description": "The request shape this provider understands.\n\n`effort` sends a flat `reasoning_effort` string. llama.cpp and the OpenAI-compatible providers take this; llama.cpp passes the value to the model's jinja template and treats `none` as off.\n\n`budget` sends `thinking: {\"type\": \"enabled\"}` *alongside* `reasoning_effort` -- DeepSeek's shape, where the envelope turns reasoning on and the effort string sets its depth. Named for the envelope rather than a token count: Anthropic's `budget_tokens` form is removed on current models and returns a 400. The two halves are independent controls, so the level `off` sends `thinking: {\"type\": \"disabled\"}` and no effort string: this dialect's `reasoning_effort` takes low/high/max and has no off value, and omitting a level is not off because the provider's own default is thinking-on.\n\n`preserved` sends the same flat `reasoning_effort` as `effort`, and no `thinking`, and every replayed assistant turn carries its `reasoning_content`. Kimi K3.1 Pro's shape: reasoning cannot be turned off, the `thinking` parameter must not be sent, and a tool loop requires the thinking back.\n\n`toggle` sends `chat_template_kwargs: {\"enable_thinking\": bool}`, which is how Qwen3 exposes it for a template that understands nothing finer. The narrow case: prefer `effort` where the runtime accepts it.\n\n`none` is the default and sends nothing.",
     "enum": [
      "effort",
      "budget",
      "preserved",
      "toggle",
      "none"
     ]
    },
    "default": {
     "description": "The level a turn uses when the client names none. Must appear in `levels`. For `toggle`, the strings are \"on\" and \"off\".",
     "minLength": 1,
     "type": "string"
    },
    "levels": {
     "description": "The levels an operator may choose from, in the order the client offers them. Declared rather than fixed because the sets genuinely differ -- llama.cpp documents minimal/low/medium/high/xhigh/max and a hosted provider may accept three of those. A level the provider does not know is its own error at turn time, which is why the server refuses a level this list does not contain.",
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
    "control"
   ],
   "type": "object"
  },
  "user": {
   "additionalProperties": false,
   "properties": {
    "name": {
     "$ref": "common.schema.json#/$defs/userName"
    },
    "roles": {
     "items": {
      "type": "string"
     },
     "type": "array"
    }
   },
   "required": [
    "name"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "additionalProperties": false,
 "description": "The appliance's runtime baseline: the policy layers and the model catalog, stored in Postgres (the `settings` row `baseline`) and edited in the administration page or with `neuman-admin baseline set` (D68). Merging with the runtime overlay is logic and lives in the decision core (docs/decisions.md D2): composition order is defaults -> roles -> overlay, lists union, set scalars win, null inherits from the layer below. The catalog half is `providers` and `models`; an entry added from a preset records it in `preset`, and the server keeps that entry's facts equal to the preset's (D76).",
 "properties": {
  "defaults": {
   "$ref": "#/$defs/fragment"
  },
  "models": {
   "additionalProperties": {
    "$ref": "#/$defs/model"
   },
   "description": "Catalog name -> what the provider is actually sent. The key is the name a user selects and that `fragment.models` grants; `provider_model` is what goes on the wire.",
   "type": "object"
  },
  "packages": {
   "description": "The tool packages this appliance has enabled, by id (D101). A tool a package holds is present only while its package is listed here; a tool no package holds is built in. Enabling a package grants its tools to nobody  14 a role's `tools` does that. A name the release does not ship is inert, as a role granting an undeclared model is, so a baseline restored onto a release without the package loads and enables nothing for it.",
   "items": {
    "pattern": "^[a-z][a-z0-9_]*$",
    "type": "string"
   },
   "type": "array",
   "uniqueItems": true
  },
  "providers": {
   "additionalProperties": {
    "$ref": "#/$defs/provider"
   },
   "description": "Provider name -> where it is and how to authenticate. Separate from `models` because the credential is per provider rather than per model: five models at one provider name it once, where a key path repeated five times is five places to drift.",
   "type": "object"
  },
  "roles": {
   "additionalProperties": {
    "$ref": "#/$defs/fragment"
   },
   "description": "Role name \u2192 fragment.",
   "type": "object"
  },
  "schema_version": {
   "const": 2
  },
  "users": {
   "items": {
    "$ref": "#/$defs/user"
   },
   "type": "array"
  }
 },
 "required": [
  "schema_version",
  "defaults",
  "roles",
  "users"
 ],
 "title": "baseline",
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
