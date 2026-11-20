"""Contracts self-tests: schemas are valid, fixtures behave, gen is fresh."""

import sys
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTRACTS))

from codegen import generate, validate  # noqa: E402


def test_schemas_pass_meta_validation() -> None:
    validate.check_schemas(validate.load_schemas())


def test_fixtures_behave() -> None:
    schemas = validate.load_schemas()
    registry = validate.build_registry(schemas)
    valid, invalid = validate.check_fixtures(schemas, registry)
    assert valid >= 10, f"only {valid} valid fixtures"
    assert invalid >= 10, f"only {invalid} invalid fixtures"


def test_generated_artifacts_are_fresh() -> None:
    assert generate.check() == []
