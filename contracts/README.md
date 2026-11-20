# contracts

The shared truth: everything two parts must agree on, expressed as **data
only** — JSON Schemas, the golden fixtures that pin their behavior, the
codegen that turns them into typed artifacts, and the generated artifacts
themselves. No logic, no policy semantics, no merge rules ever live here
(`docs/decisions.md` D2): the moment a schema needs an `if`, the logic belongs in
a part, not a contract.

## Public surface

- **`schemas/`** — the source-of-truth JSON Schemas. Hand-written,
  versioned, the only place a wire format is defined.
- **`gen/`** — committed generated artifacts, never hand-edited. `python/` is
  all of it, because the server, the client and the admin CLI are one language
  and one emitter covers all three. A second language gets a directory beside
  it, generated the same way — when something consumes it, not before.
- **`examples/`** — golden fixtures, valid and invalid, one set per
  schema. Executed by codegen tests and CI; the executable form of the
  contract's intent.

## Private surface

- **`codegen/`** — the generator internals (schema loading, validation,
  emitters). Nothing outside this directory may parse or emit a schema.

## Schemas

All schemas target JSON Schema **draft 2020-12** (decided at M1). All are
authored. `ws` was deliberately held as a stub until the harness inbox
(D12) and the client surface existed; both landed at M6/M7, so its
vocabulary is now derived from the loop rather than guessed.

| Schema | Crosses | Governs |
|---|---|---|
| `common.schema.json` | (referenced only) | shared definitions: absolute paths, account names, the network grant |
| `dispatch-rpc.schema.json` | server ↔ client | hello/welcome, dispatch/cancel ↔ started/stdout/stderr/result/detached/error, and job_settled when a background job ends; versioned |
| `tool-manifest.schema.json` | server registry ↔ client tool host | one manifest contract; two installations, CI proves they match (D2, D3) |
| `log-record.schema.json` | server + client logging | the one canonical record, checked semantically with golden fixtures (D10) |
| `baseline.schema.json` | admin surface ↔ server | the runtime baseline: policy layers and the model catalog (D68) |
| `presets.schema.json` | release → server | the providers and models the server ships and holds entries to (D76) |
| `ws.schema.json` | the chat UI ↔ server | the session socket: 4 inbound kinds (`message`, `approval`, `steer`, `interrupt`) and 9 outbound (D12) |
| REST API | the chat UI ↔ server | the routes the UI calls, listed by hand in `composition/http/`; `/api/browse` is how it descends a granted subtree |

## Tooling

From `contracts/`:

```
python3 -m codegen validate          # meta-validate schemas; assert fixtures behave
python3 -m codegen generate          # (re)write gen/python/neuman_contracts
python3 -m codegen generate --check  # CI: fail when committed artifacts are stale
python3 -m pytest tests              # the two assertions above, as tests
```

Generated modules expose `SCHEMA`, `validate(instance)` and
`is_valid(instance)` per contract; cross-file `$ref`s resolve through a
shared registry embedded in `_registry.py`. Runtime deps: `jsonschema`
(bringing `referencing`). Test deps: `pytest`. Both arrive via Nix with
the first part that consumes them (M3).

Fixture convention: `examples/<schema>/valid/*.json` must validate;
`examples/<schema>/invalid/*.json` must be rejected. The invalid set is
where trust rules become executable — author it with the schema, never
after.

## CI checks

1. Every example validates against its schema (valid ones pass, invalid
   ones fail).
2. Regenerating `gen/` produces zero diff — committed artifacts are never
   stale.
3. Both installations of a shared contract — the server's registry and the
   client's own manifest set — accept exactly the same set. The client's
   suite loads `tools/` and asserts it, so a manifest that parses for one
   and not the other fails here.

## Versioning

Every schema carries a version. Within a version, changes are
append-only (new optional fields, new message kinds); anything that
removes or renumbers bumps the version and both sides must be able to
speak the old one until the appliance upgrades them together. The
versioning policy is finalized with the first schema at M1.

## The fixture floor

`codegen validate` asserts a minimum fixture count as well as validating each
one. A fixture suite cannot tell you about the fixtures it never saw: an
unanchored `.gitignore` pattern once excluded three of these from the git tree,
and since the flake's source *is* the git tree, CI validated a smaller contract
than a developer did — silently, because the check reports what it found rather
than what it should have found (N41).

Raise `MINIMUM_VALID` / `MINIMUM_INVALID` when adding fixtures. Never lower them
to make a run pass; a drop means something stopped being visible.
