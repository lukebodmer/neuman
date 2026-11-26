"""Generated from all schemas by contracts/codegen — do not edit."""
import json

RESOURCES: dict[str, dict] = {}

RESOURCES["baseline.schema.json"] = json.loads(r"""
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
RESOURCES["common.schema.json"] = json.loads(r"""
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
RESOURCES["dispatch-rpc.schema.json"] = json.loads(r"""
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
    "machine": {
     "$ref": "#/$defs/machineLabel",
     "description": "The machine the core believes this connection is. The client refuses a dispatch naming another one (`wrong_machine`), which turns a routing bug at the core into a refusal instead of a command run on the wrong computer \u2014 the failure this field exists to make impossible."
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
      "missing_program",
      "version_mismatch",
      "wrong_machine"
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
    },
    "versions": {
     "$ref": "#/$defs/versionReport",
     "description": "Present on `version_mismatch` and nowhere else. The client cannot derive it: the numbers it needs are the appliance's, and the refusal is the last frame it will get."
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
     "description": "The home directory on the machine this client runs on \u2014 what `~` means there. The core expands `$HOME` and `$HOME/<path>` policy entries against it and must not use the account's home on *its* host: a client on another machine has a different one, and a dispatch checked against the wrong home refuses paths the account can read (N145). A dishonest value cannot widen anything \u2014 `shell` and `python` are unchecked, so a client wanting more reach would not ask \u2014 but it decides which directories those entries name, and that is a question only the client can answer."
    },
    "machine": {
     "$ref": "#/$defs/machine"
    },
    "release": {
     "$ref": "#/$defs/release",
     "description": "The Neuman release this client is. Optional because a client too old to say still has a hello the core can use, and refusing it would turn a reporting gap into an outage (D111). Absent means \"unknown\", not \"unacceptable\": the gate is `rpc_version`. It is what an operator sees per machine, and what a refusal quotes back to the person."
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
    "machine",
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
  "machine": {
   "additionalProperties": false,
   "description": "The machine this connection runs dispatches for. Not an identity claim: the session already names the principal, and a machine is scoped to that account, so naming one grants nothing. It is what makes a branch's path mean *somewhere*: without it, a session created on one computer and reopened on another is silently pointed at a second filesystem.",
   "properties": {
    "kind": {
     "description": "How the core reaches this machine. `client` is a daemon holding this socket. `ssh` is reserved for a target the client dials and is not spoken yet, so a client claiming it is refused rather than half-served.",
     "enum": [
      "client"
     ]
    },
    "label": {
     "$ref": "#/$defs/machineLabel"
    }
   },
   "required": [
    "label",
    "kind"
   ],
   "type": "object"
  },
  "machineLabel": {
   "$ref": "common.schema.json#/$defs/machineLabel"
  },
  "minimumClient": {
   "additionalProperties": false,
   "properties": {
    "release": {
     "$ref": "#/$defs/release",
     "description": "The oldest client release this appliance accepts, named so a refusal can end in an install command rather than a version number to look up."
    },
    "rpc": {
     "$ref": "#/$defs/rpcVersion",
     "description": "The dispatch shape that release speaks. This is the gate; `release` is the same fact spelled for a person. They move together, because a release that speaks a shape the appliance no longer accepts is below the floor whatever its number says."
    }
   },
   "required": [
    "release",
    "rpc"
   ],
   "type": "object"
  },
  "release": {
   "description": "A product release, as its package version spells it (`0.1.0`). Not a build id: the build id answers \"which code served this turn\" (D69) and may be a store path, which is not comparable between two machines. This is the number a person is told to install.",
   "maxLength": 64,
   "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+([-+][0-9A-Za-z.+_-]+)?$",
   "type": "string"
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
   "description": "Every shape this contract knows how to write. It is a set rather than a constant because the two halves update on different days and an appliance update must not strand a client that has not been updated yet (D111): the handshake picks one of these per connection, and every frame on that connection carries the one it picked. Adding a value is the append-only case; removing one is what raises the appliance's floor.",
   "enum": [
    2
   ]
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
  "versionFacts": {
   "additionalProperties": false,
   "description": "What one end said about itself. Null rather than absent for a fact it could not state, so a refusal can name which end it is talking about without guessing.",
   "properties": {
    "release": {
     "maxLength": 64,
     "type": [
      "string",
      "null"
     ]
    },
    "rpc": {
     "minimum": 0,
     "type": [
      "integer",
      "null"
     ]
    }
   },
   "type": "object"
  },
  "versionReport": {
   "additionalProperties": false,
   "description": "Both ends of a handshake that could not agree, so the client can say what to do without a second request.",
   "properties": {
    "accepted": {
     "description": "The shapes the appliance speaks right now. A client below the smallest of them is the one to update; one above the largest is talking to an appliance that needs updating \u2014 which is the only way a refusal can say which half to install.",
     "items": {
      "$ref": "#/$defs/rpcVersion"
     },
     "minItems": 1,
     "type": "array",
     "uniqueItems": true
    },
    "client": {
     "$ref": "#/$defs/versionFacts"
    },
    "server": {
     "$ref": "#/$defs/versionFacts"
    }
   },
   "required": [
    "accepted"
   ],
   "type": "object"
  },
  "welcome": {
   "additionalProperties": false,
   "properties": {
    "machine": {
     "$ref": "#/$defs/machine",
     "description": "The machine the core recorded, echoed rather than assumed: a client that believes it is `ernest` and is told it is `dgx` has to be able to say so, and this is the frame where the two answers meet."
    },
    "minimum_client": {
     "$ref": "#/$defs/minimumClient",
     "description": "The oldest client this appliance accepts. Nothing below it completes a handshake, so a client that got this far is at or above it; it is stated for the quiet notice and for `doctor`, not as a condition."
    },
    "release": {
     "$ref": "#/$defs/release",
     "description": "The appliance's release, for the quiet fact \"a newer client exists\" and for `doctor` to print beside the client's own. Optional so a server older than this field can still complete a handshake."
    },
    "rpc_version": {
     "$ref": "#/$defs/rpcVersion",
     "description": "The shape this connection settled on. Not necessarily the appliance's newest: a client that speaks only an older one is answered in that one, so every frame on this socket is a shape the client can read (D111)."
    },
    "rpc_versions": {
     "description": "Every shape the appliance speaks, newest last. Advertised rather than inferred so a client can tell an appliance that dropped its shape from one that never accepted it, and say which half needs updating.",
     "items": {
      "$ref": "#/$defs/rpcVersion"
     },
     "type": "array",
     "uniqueItems": true
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
    "user",
    "machine"
   ],
   "type": "object"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "JSON-lines between the decision core and a connected client, one JSON object per line. The client opens the connection, says hello with the machine it is, the tool names it can run and the home directory there, and then receives dispatches and returns their output. The core decides; the client executes exactly what it is told. There is no profile on the wire: the client runs as the local account that holds the process and reaches what that account reaches, and the controls are the capability gate and the approval verdict, both decided before a dispatch is sent.",
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
RESOURCES["log-record.schema.json"] = json.loads(r"""
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
RESOURCES["presets.schema.json"] = json.loads(r"""
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
RESOURCES["task-run.schema.json"] = json.loads(r"""
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
RESOURCES["task.schema.json"] = json.loads(r"""
{
 "$defs": {
  "agentStep": {
   "additionalProperties": false,
   "description": "One harness turn: a session and a branch of their own, pre-approved inside the step's envelope intersected with the owner's live policy (D93). The step ends with the harness's terminal reason, never with the model's account of itself.",
   "properties": {
    "cwd": {
     "$ref": "#/$defs/stepPath",
     "description": "The turn's working directory. Absent means `$RUN_DIR`. It must be inside the envelope's read reach."
    },
    "envelope": {
     "$ref": "#/$defs/envelope"
    },
    "instruction": {
     "description": "The message the turn starts with. Written for the model, and the one place a task's judgement lives. The server opens it with the run's directory and input values, so the instruction can be the same words on every run.",
     "maxLength": 20000,
     "minLength": 1,
     "type": "string"
    },
    "kind": {
     "const": "agent"
    },
    "model": {
     "description": "The catalog name this turn asks for. Must be in `envelope.models` and the owner's policy.",
     "minLength": 1,
     "type": "string"
    },
    "name": {
     "$ref": "#/$defs/stepName"
    },
    "reasoning": {
     "description": "The reasoning level for this turn, or null for the model's declared default. Validated against the catalog at run time, like a branch's choice.",
     "type": [
      "string",
      "null"
     ]
    },
    "run_if": {
     "$ref": "#/$defs/runIf"
    }
   },
   "required": [
    "kind",
    "name",
    "instruction",
    "model",
    "envelope"
   ],
   "type": "object"
  },
  "draft": {
   "additionalProperties": false,
   "description": "What a client submits to create a task, or the merged form of what it submits to edit one. The server validates the merged draft against the owner's live policy, so an edit cannot smuggle in reach the create path would have refused.",
   "properties": {
    "enabled": {
     "description": "Whether the schedule fires. A disabled task can still be run by hand.",
     "type": "boolean"
    },
    "inputs": {
     "items": {
      "$ref": "#/$defs/input"
     },
     "maxItems": 10,
     "type": "array"
    },
    "machine": {
     "$ref": "common.schema.json#/$defs/machineLabel",
     "description": "The computer this task's steps run on (D112). Required, and not derived: a recipe is about files somewhere, and the machine is where they are. A run dispatches to it or waits for it \u2014 never to whichever client happens to be connected."
    },
    "name": {
     "maxLength": 80,
     "minLength": 1,
     "type": "string"
    },
    "outputs": {
     "description": "Artifacts the run is expected to leave in its directory. Declared so the reader knows what success looks like; the run record says which exist when it ends, and whether they are right stays outside Neuman.",
     "items": {
      "$ref": "#/$defs/outputPath"
     },
     "maxItems": 20,
     "type": "array",
     "uniqueItems": true
    },
    "run_root": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Where each run gets its own directory, `<run_root>/<due date>-<run id>`, made on the owner's client before the first step. It must be inside the owner's write reach and outside the workspace and every run step's `cwd`, since an agent step may write there (D96, D97)."
    },
    "schedule": {
     "$ref": "#/$defs/schedule"
    },
    "steps": {
     "$ref": "#/$defs/steps"
    },
    "workspace": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "The directory the task belongs to, and where a run step with no `cwd` runs. It must be inside the owner's read reach."
    }
   },
   "required": [
    "name",
    "workspace",
    "run_root",
    "machine",
    "steps",
    "schedule"
   ],
   "type": "object"
  },
  "envelope": {
   "additionalProperties": false,
   "description": "An agent step's declared reach, as a policy fragment. `files_read` and `files_write` are absolute paths chosen from what the owner already reaches, or `$RUN_DIR`; an entry outside the owner's reach is refused at authoring time and dropped again by the run-time intersection. No entry of `files_write` may contain the workspace, a run step's `cwd` or a run step's program, nor lie inside them, so an agent step can never write what a run step executes (D96). `models` is the set the step may choose from; the step's own `model` must be in it.",
   "properties": {
    "files_read": {
     "items": {
      "$ref": "#/$defs/stepPath"
     },
     "type": "array",
     "uniqueItems": true
    },
    "files_write": {
     "items": {
      "$ref": "#/$defs/stepPath"
     },
     "type": "array",
     "uniqueItems": true
    },
    "max_iterations": {
     "description": "The most model round-trips the step may take. The effective value is the smaller of this and the account's own policy, so a task cannot buy more steps than its owner.",
     "maximum": 500,
     "minimum": 1,
     "type": "integer"
    },
    "models": {
     "description": "Catalog names the step may use. The step's `model` is one of them.",
     "items": {
      "minLength": 1,
      "type": "string"
     },
     "type": "array",
     "uniqueItems": true
    },
    "network": {
     "description": "Whether the step may use tools that declare `needs = [\"network\"]`. `any` requires the account to hold network reach too; there is no host list (N146).",
     "enum": [
      "none",
      "any"
     ]
    },
    "tools": {
     "description": "Client tools the step may dispatch. A server-native tool or a `critical` tool is refused at authoring time: a task has no human to ask, and a critical call must never be pre-approved (D93). `shell` and `python` are refused in an agent step of a task with a run step after it, because neither declares its paths and either could rewrite what that step runs (D96).",
     "items": {
      "$ref": "#/$defs/toolName"
     },
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "tools",
    "files_read",
    "files_write",
    "network",
    "models",
    "max_iterations"
   ],
   "type": "object"
  },
  "input": {
   "additionalProperties": false,
   "description": "A parameter one run supplies. Values are strings and reach a run step only as environment variables, never spliced into its argv, so an input can change what a script is told and never which program runs (D96).",
   "properties": {
    "default": {
     "$ref": "#/$defs/inputValue"
    },
    "description": {
     "maxLength": 200,
     "type": "string"
    },
    "name": {
     "$ref": "#/$defs/inputName"
    },
    "required": {
     "description": "Whether a run must give a value. A required input with no default must also be given by `schedule.inputs` for the schedule to fire.",
     "type": "boolean"
    },
    "values": {
     "description": "The values a run may give, when the input is a choice. Absent means any string.",
     "items": {
      "$ref": "#/$defs/inputValue"
     },
     "minItems": 1,
     "type": "array",
     "uniqueItems": true
    }
   },
   "required": [
    "name"
   ],
   "type": "object"
  },
  "inputName": {
   "description": "Lower case, so the environment variable a run step sees is `NEUMAN_INPUT_` plus its upper-cased form.",
   "pattern": "^[a-z][a-z0-9_]{0,31}$",
   "type": "string"
  },
  "inputValue": {
   "maxLength": 2000,
   "pattern": "^[^\\u0000]*$",
   "type": "string"
  },
  "inputValues": {
   "additionalProperties": {
    "$ref": "#/$defs/inputValue"
   },
   "maxProperties": 10,
   "propertyNames": {
    "$ref": "#/$defs/inputName"
   },
   "type": "object"
  },
  "outputPath": {
   "allOf": [
    {
     "not": {
      "pattern": "(^|/)\\.\\.($|/)"
     }
    }
   ],
   "description": "A path relative to the run directory naming an artifact the run is expected to leave there. A declaration for the reader and the runner, not a grant: an agent step's `files_write` is what decides where a write may land.",
   "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$",
   "type": "string"
  },
  "runIf": {
   "description": "When the step runs, given the steps before it. `ok` (the default): every earlier step succeeded. `ok_or_budget`: as `ok`, except an agent step that stopped on its budget counts \u2014 the step after a research turn that ran long still has the work it left. `always`: regardless, for a step that records or cleans up. A step that does not run is `skipped`.",
   "enum": [
    "ok",
    "ok_or_budget",
    "always"
   ]
  },
  "runStep": {
   "additionalProperties": false,
   "description": "A command the owner wrote word for word. It is dispatched to the owner's client as a pre-approved `shell` call through the ordinary gate and recorded in the audit chain like one; the model neither chooses it nor sees its arguments before it runs. It reaches what the owner reaches \u2014 there is no write list, because nothing on the client could enforce one (N146) \u2014 so what keeps it safe is that its program, its arguments and its working directory are fixed at authoring and lie outside every path an agent step may write (D96). The owner's policy must grant `shell`, checked at authoring and again when the step starts. It sees `NEUMAN_RUN_ID`, `NEUMAN_RUN_DIR`, `NEUMAN_TASK_ID` and one `NEUMAN_INPUT_<NAME>` per input in its environment.",
   "properties": {
    "argv": {
     "description": "The program and its arguments, quoted by the server, never by a shell the model writes. `argv[0]` is a program name looked up on the owner's `PATH`, or an absolute path.",
     "items": {
      "maxLength": 4000,
      "minLength": 1,
      "pattern": "^[^\\u0000]*$",
      "type": "string"
     },
     "maxItems": 64,
     "minItems": 1,
     "type": "array"
    },
    "cwd": {
     "$ref": "common.schema.json#/$defs/absPath",
     "description": "Where the command runs. Absent means the task's workspace."
    },
    "kind": {
     "const": "run"
    },
    "name": {
     "$ref": "#/$defs/stepName"
    },
    "run_if": {
     "$ref": "#/$defs/runIf"
    },
    "timeout_s": {
     "description": "The longest the command may run. A command still running is killed and the step is `failed`.",
     "maximum": 3600,
     "minimum": 1,
     "type": "integer"
    }
   },
   "required": [
    "kind",
    "name",
    "argv",
    "timeout_s"
   ],
   "type": "object"
  },
  "schedule": {
   "additionalProperties": false,
   "allOf": [
    {
     "if": {
      "properties": {
       "kind": {
        "const": "daily"
       }
      }
     },
     "then": {
      "required": [
       "time",
       "timezone"
      ]
     }
    },
    {
     "if": {
      "properties": {
       "kind": {
        "const": "weekly"
       }
      }
     },
     "then": {
      "required": [
       "time",
       "weekday",
       "timezone"
      ]
     }
    },
    {
     "if": {
      "properties": {
       "kind": {
        "const": "interval"
       }
      }
     },
     "then": {
      "required": [
       "minutes"
      ]
     }
    }
   ],
   "description": "When the task is due. Times are local to `timezone`; the server computes `next_run_at` from this and never from the client's clock. A due run waits for the owner's client, so `scheduled` means due rather than guaranteed at that second (D94).",
   "properties": {
    "inputs": {
     "$ref": "#/$defs/inputValues",
     "description": "The input values a scheduled run is given. A manual run gives its own."
    },
    "kind": {
     "enum": [
      "daily",
      "weekly",
      "interval"
     ]
    },
    "minutes": {
     "description": "The interval's period, at least fifteen minutes. Required by `interval`.",
     "maximum": 10080,
     "minimum": 15,
     "type": "integer"
    },
    "time": {
     "description": "HH:MM in `timezone`. Required by `daily` and `weekly`.",
     "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$",
     "type": "string"
    },
    "timezone": {
     "description": "An IANA zone name. Required by `daily` and `weekly`; validated with `zoneinfo` rather than by this pattern.",
     "pattern": "^[A-Za-z][A-Za-z0-9_+/-]{0,63}$",
     "type": "string"
    },
    "weekday": {
     "description": "0 is Monday, matching `datetime.weekday()`. Required by `weekly`.",
     "maximum": 6,
     "minimum": 0,
     "type": "integer"
    }
   },
   "required": [
    "kind"
   ],
   "type": "object"
  },
  "step": {
   "oneOf": [
    {
     "$ref": "#/$defs/runStep"
    },
    {
     "$ref": "#/$defs/agentStep"
    }
   ]
  },
  "stepName": {
   "description": "Unique within the task. The run record reports each step under this name.",
   "pattern": "^[a-z][a-z0-9-]{0,39}$",
   "type": "string"
  },
  "stepPath": {
   "anyOf": [
    {
     "$ref": "common.schema.json#/$defs/absPath"
    },
    {
     "const": "$RUN_DIR"
    }
   ],
   "description": "A path a step names: an absolute path, or exactly `$RUN_DIR` for this run's own directory, which does not exist until the run does (D97)."
  },
  "steps": {
   "description": "Run in order, one at a time. There are no branches, loops or retries: a step that should not follow a failure says so with `run_if` (D95).",
   "items": {
    "$ref": "#/$defs/step"
   },
   "maxItems": 10,
   "minItems": 1,
   "type": "array"
  },
  "task": {
   "additionalProperties": false,
   "description": "A stored task as the API returns it. The server owns the id, the owner and every timestamp; a client that sends them is refused rather than trusted.",
   "properties": {
    "account": {
     "minLength": 1,
     "type": "string"
    },
    "created_at": {
     "format": "date-time",
     "type": "string"
    },
    "enabled": {
     "type": "boolean"
    },
    "id": {
     "pattern": "^[0-9a-f]{32}$",
     "type": "string"
    },
    "inputs": {
     "items": {
      "$ref": "#/$defs/input"
     },
     "maxItems": 10,
     "type": "array"
    },
    "last_run_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "machine": {
     "$ref": "common.schema.json#/$defs/machineLabel",
     "description": "The computer this task's steps run on (D112). Required, and not derived: a recipe is about files somewhere, and the machine is where they are. A run dispatches to it or waits for it \u2014 never to whichever client happens to be connected."
    },
    "name": {
     "maxLength": 80,
     "minLength": 1,
     "type": "string"
    },
    "next_run_at": {
     "format": "date-time",
     "type": [
      "string",
      "null"
     ]
    },
    "outputs": {
     "items": {
      "$ref": "#/$defs/outputPath"
     },
     "maxItems": 20,
     "type": "array",
     "uniqueItems": true
    },
    "run_root": {
     "$ref": "common.schema.json#/$defs/absPath"
    },
    "schedule": {
     "$ref": "#/$defs/schedule"
    },
    "steps": {
     "$ref": "#/$defs/steps"
    },
    "updated_at": {
     "format": "date-time",
     "type": "string"
    },
    "workspace": {
     "$ref": "common.schema.json#/$defs/absPath"
    }
   },
   "required": [
    "id",
    "account",
    "name",
    "workspace",
    "run_root",
    "machine",
    "inputs",
    "steps",
    "schedule",
    "outputs",
    "enabled",
    "created_at",
    "updated_at",
    "next_run_at"
   ],
   "type": "object"
  },
  "toolName": {
   "pattern": "^[a-z_][a-z0-9_]*$",
   "type": "string"
  }
 },
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "description": "A task is a standing approval: an account saves a recipe once \u2014 inputs, an ordered list of steps, the outputs it leaves \u2014 and runs it on demand or on a schedule. A step is either a `run` step, a command the owner wrote word for word, or an `agent` step, a harness turn inside an envelope. The envelope is a policy fragment, never a grant \u2014 the server intersects it with the account's live policy on every model step, so an agent step can narrow reach and can never widen it (D92, D93, D95, D96). A task is not a principal: it runs as its owner, on that owner's client (D94). Every run gets a fresh directory under `run_root`, the one place an agent step may write by default and where the run's record is left (D97).",
 "oneOf": [
  {
   "$ref": "#/$defs/task"
  },
  {
   "$ref": "#/$defs/draft"
  }
 ],
 "title": "task"
}
""")
RESOURCES["tool-manifest.schema.json"] = json.loads(r"""
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
RESOURCES["tool-package.schema.json"] = json.loads(r"""
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
RESOURCES["ws.schema.json"] = json.loads(r"""
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
