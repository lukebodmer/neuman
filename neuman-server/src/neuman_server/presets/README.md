# Presets

The providers and models Neuman supports, as the server ships them (D39, D76).
One file per provider under `providers/`, one per model under `models/`; the
file's stem is the preset id. `contracts/schemas/presets.schema.json` is the
shape, and the server refuses to start on a file that violates it.

An operator adds a provider by choosing its tile and pasting a key, and a model by
choosing it on a provider they have. The server builds the baseline entry from
the file and records the preset it came from; from then on the entry's **facts**
are this file's, re-applied at every startup and refused on any write that
contradicts them. The **policy** values are only where the entry starts.

## Facts and policy

A fact is a property of the model or of the route to it, and getting one wrong
is how a turn dies at the provider after its tools have run (design principle 7 in `docs/decisions.md`):

- `context_tokens` — the window the provider serves, never a spending cap. The
  harness sizes compaction, the kept tail and the reply clamp as proportions of
  it; declaring it low to save money starves replies while the provider would
  have accepted far more (N116).
- `vision` — stated, never inferred from the id: `deepseek-v4.1-flash` accepts
  images and `deepseek-v4-flash` does not.
- `provider_model` — the route's own id, not derivable from the model's name.
  An id is only falsified by spending a turn on it: one pointed at a retired
  model for ten weeks (N102).
- `reasoning.control` and `reasoning.levels` — the dialect is the model's, but
  which levels arrive is the route's, because a route decides what it forwards.
  A level the route refuses is a 400 mid-turn.

Policy is the operator's: `max_tokens`, the three prices and
`reasoning_default`.

**Three kinds of fact, not two.** The model's own (`facts` at the top of a
file) and the route's (`routes.<provider>.facts`), merged with the route
winning. The same DeepSeek model is `deepseek-flash` with 384,000 output tokens
at $0.15/M input on DeepSeek, and `deepseek/deepseek-v4.1-flash-1017` with
131,072 on its lowest hosts at $0.038/M through OpenRouter. No route is a
default: which of those matters is the operator's question.

## Choices worth not undoing

- **DeepSeek offers no `medium`.** Its guide maps minimal→low, medium/xhigh→high
  and ultra→max, so `medium` would be served silently as `high` at `high`'s
  price. `off` is the `thinking` envelope disabled, with no effort string: the
  effort field has no off value, and omitting a level is the provider's own
  default, which is thinking on.
- **DeepSeek defaults to `high`**, the provider's own default, stated rather
  than left empty — for this dialect sending nothing is not off.
- **DeepSeek is the 1017 snapshot on every route.** DeepSeek serves it as
  `deepseek-flash`; OpenRouter lists it beside the earlier V4.1 Flash under an
  id of its own, and a preset whose routes reached two checkpoints would be two
  models under one name.
- **DeepSeek on this machine speaks `effort`, not `budget`.** The `thinking`
  envelope is the API's switch; under llama.cpp the chat template reads
  `reasoning_effort` alone, takes `none` as off, and serves `medium` and `xhigh`
  as `high`, so neither is offered.
- **DeepSeek's prices are off-peak.** Peak is double on weekday mornings UTC and
  deliberately not modelled; the client marks every computed cost `~`. The cache
  price is not a rounding error: $0.003 against $0.15, and an agentic turn
  resends the same prefix every step.
- **Qwen3.8 offers no `high`.** The model's template aliases it to `xhigh`, and
  two labels for one behaviour is a worse menu than one (N144). `medium` is the
  default because it is the level the template adds no instruction for.
- **GLM 5.3 and 5.3 Flash default to `max`**, where the provider's own default
  already put an unselecting user. `medium` is absent because the chat template
  honours `low` and `high` and serves anything else as `max`. Flash's `vision`
  is the route's: it was `false` until an image turn confirmed it. GLM 5.3 is
  text only, and publishes no projector.
- **Kimi K3.1 Pro is `preserved`, not `effort`.** The levels look like `effort`,
  and Moonshot requires `reasoning_content` back in a tool loop, which `effort`
  drops (D88). OpenRouter lists a `none` it may forward; Moonshot says the model
  always reasons, so `none` is not offered. `max` is the provider's own default.

## Adding one

A file here and a row in `docs/setup.md`. Read every fact from the provider —
its `/models` endpoint, its documentation, a real turn — and say where in
`verified`. The tests load every file, build an entry for every route, and check
each policy default is one of its levels.

## On this machine

A model may also carry a `local` route: the same weights run on this machine's
GPU. Its facts are a download pinned to a Hugging Face commit, with each file's
size and SHA-256 read from the Hub API (`GET /api/models/<repo>/revision/main?blobs=true`,
then the commit it reports), and the flags the model needs on any hardware
(`--jinja`). `vision` is not stated: a model sees exactly when a projector file
is named. `vram` is measured, never computed, and names
the card and the policy it was measured at; the panel uses it to say whether a
model fits, never to refuse one.

Everything the machine chooses is typed policy — `context_size`, `gpu_layers`,
`speculation`, `kv_cache`, `residency`, `idle_minutes`, `max_tokens`, `reasoning_default`.
There is no free-form flag list: the runtime builds its own command line from these, so a stored document
can never hand it an arbitrary argument or file.

- **Qwen3.8 27B at Q4 measured 22,774 MiB** on an RTX 3090 at 64k of window,
  with its vision projector and `draft-mtp` speculation — read per process from
  the GPU sampler on 2026-11-02, so a 24 GB card has about 1.7 GB spare. Lower
  `context_size` first on a smaller card, or set `kv_cache` to `q8_0`: 21,068
  MiB at 64k, 23,634 at 128k (N100). The preset leaves `kv_cache` unset because
  `f16` is llama.cpp's own default and the faster one.
- **Its local name is `Qwen3.8-27B-Q4`**, not the hosted `Qwen3.8-27B`, so an
  appliance can offer both. Every local route takes the same `-Q4` suffix.

A large model is published as a **split GGUF**, numbered shards under a
directory of the quantisation's name. Each shard is a `model` file, listed in
order; `path` is where it is in the repository and `name` is where it lands, so
every shard sits flat in `modelsDir` beside the first, which is the file the
runtime starts and from which llama.cpp finds the rest. A file name belongs to
one preset: `modelsDir` is flat, and two presets sharing `mmproj-F16.gguf` would
each refuse the other's download as a wrong file and delete it with their own
weights. A second projector of that name is saved under the model's name.

- **GLM 5.3 Flash, GLM 5.3 and DeepSeek V4.1 Flash 1017 at Q4 are 200, 467 and
  502 GB.** None fits a card; they are for a machine with that much GPU or
  unified memory. They state no `vram` because nobody has run them here, and
  `speculation` is left off because whether llama.cpp drives their
  multi-token-prediction heads from these files is not yet measured. Kimi K3.1
  Pro has no local route: unsloth publishes no GGUF of it.
