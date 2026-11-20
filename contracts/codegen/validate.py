"""Validate every schema and its fixtures.

The executable form of the contracts' intent: valid fixtures must pass,
invalid fixtures must be rejected. A schema without a fixtures directory
(`common`, or a stub like `ws`) is still checked against the meta-schema.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# Raise these when fixtures are added; never lower them to make a run pass.
MINIMUM_VALID = 40
MINIMUM_INVALID = 37

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parent / "schemas"
EXAMPLES = HERE.parent / "examples"


def load_schemas() -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))
    return schemas


def build_registry(schemas: dict[str, dict]) -> Registry:
    """Register each schema under its filename; cross-file $refs use that name."""
    registry: Registry = Registry()
    for name, contents in sorted(schemas.items()):
        registry = registry.with_resource(name, Resource.from_contents(contents))
    return registry


def check_schemas(schemas: dict[str, dict]) -> None:
    """Raise if any schema is not itself a valid 2020-12 schema."""
    for name, schema in sorted(schemas.items()):
        Draft202012Validator.check_schema(schema)


def check_fixtures(schemas: dict[str, dict], registry: Registry) -> tuple[int, int]:
    """Assert every valid fixture validates and every invalid fixture is rejected."""
    checked_valid = checked_invalid = 0
    for name, schema in sorted(schemas.items()):
        base = name.removesuffix(".schema.json")
        validator = Draft202012Validator(schema, registry=registry)
        for kind, expect_ok in (("valid", True), ("invalid", False)):
            directory = EXAMPLES / base / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                instance = json.loads(path.read_text(encoding="utf-8"))
                ok = validator.is_valid(instance)
                if ok != expect_ok:
                    raise AssertionError(
                        f"{path}: expected {'valid' if expect_ok else 'invalid'}, "
                        f"got {'valid' if ok else 'invalid'}"
                    )
                if expect_ok:
                    checked_valid += 1
                else:
                    checked_invalid += 1
    return checked_valid, checked_invalid


def run() -> tuple[int, int]:
    schemas = load_schemas()
    check_schemas(schemas)
    registry = build_registry(schemas)
    valid, invalid = check_fixtures(schemas, registry)

    # A floor, because a fixture suite cannot tell you about the fixtures it
    # never saw. `.gitignore` once matched `result-*` unanchored and excluded
    # three fixtures named `result-*.json`; the check passed, reported a smaller
    # count, and nothing knew the difference — including the one guarding the
    # dispatch stream's sequence gap-checking (N41). The numbers only ratchet
    # up, so a drop means something stopped being visible.
    if valid < MINIMUM_VALID or invalid < MINIMUM_INVALID:
        raise AssertionError(
            f"fixture count fell: {valid} valid (expected >= {MINIMUM_VALID}), "
            f"{invalid} invalid (expected >= {MINIMUM_INVALID}). "
            "A fixture that vanished is more likely excluded than deleted — "
            "check `git status --porcelain --ignored`."
        )

    print(
        f"contracts: {len(schemas)} schemas, "
        f"{valid} valid fixtures, {invalid} invalid fixtures — all pass"
    )
    return valid, invalid
