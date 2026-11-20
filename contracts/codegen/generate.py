"""Emit the generated Python contracts package from schemas/.

Deterministic by construction: same schemas in, same bytes out. The
committed artifacts under gen/python are never hand-edited;
``--check`` fails when they would change.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parent / "schemas"
GEN = HERE.parent / "gen" / "python" / "neuman_contracts"

_HEADER = '"""Generated from {source} by contracts/codegen — do not edit."""'


def module_name(schema_file: str) -> str:
    """log-record.schema.json -> log_record"""
    return schema_file.removesuffix(".schema.json").replace("-", "_")


def load_schemas() -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))
    return schemas


def _embedded(obj: dict) -> str:
    """A JSON document embedded safely inside a triple-quoted raw string."""
    text = json.dumps(obj, indent=1, sort_keys=True)
    assert '"""' not in text
    return text


def _registry_module(schemas: dict[str, dict]) -> str:
    parts = [
        _HEADER.format(source="all schemas"),
        "import json",
        "",
        "RESOURCES: dict[str, dict] = {}",
        "",
    ]
    for name in sorted(schemas):
        parts.append(f'RESOURCES["{name}"] = json.loads(r"""')
        parts.append(_embedded(schemas[name]))
        parts.append('""")')
    return "\n".join(parts) + "\n"


def _contract_module(schema_file: str, schema: dict) -> str:
    return "\n".join(
        [
            _HEADER.format(source=f"schemas/{schema_file}"),
            "",
            "import json",
            "",
            "from jsonschema import Draft202012Validator",
            "from referencing import Registry, Resource",
            "",
            "from ._registry import RESOURCES",
            "",
            'SCHEMA = json.loads(r"""',
            _embedded(schema),
            '""")',
            "",
            "",
            "def _build() -> Draft202012Validator:",
            "    registry: Registry = Registry()",
            "    for name, contents in RESOURCES.items():",
            "        registry = registry.with_resource(name, Resource.from_contents(contents))",
            "    return Draft202012Validator(SCHEMA, registry=registry)",
            "",
            "",
            "_VALIDATOR = _build()",
            "",
            "",
            "def validate(instance: object) -> None:",
            '    """Raise jsonschema.ValidationError unless instance satisfies the schema."""',
            "    _VALIDATOR.validate(instance)",
            "",
            "",
            "def is_valid(instance: object) -> bool:",
            '    """Return True when instance satisfies the schema."""',
            "    return _VALIDATOR.is_valid(instance)",
            "",
        ]
    )


def _init_module(modules: list[str]) -> str:
    mods = sorted(modules)
    lines = [
        _HEADER.format(source="all schemas"),
        "",
        "from . import " + ", ".join(mods),
        "",
        "__all__ = [" + ", ".join(f'"{m}"' for m in mods) + "]",
        "",
    ]
    return "\n".join(lines)


def generate() -> dict[str, str]:
    """Return {relative filename: file content} for the whole package."""
    schemas = load_schemas()
    files = {
        "__init__.py": _init_module([module_name(name) for name in schemas]),
        "_registry.py": _registry_module(schemas),
    }
    for name, schema in schemas.items():
        files[f"{module_name(name)}.py"] = _contract_module(name, schema)
    return files


def write() -> int:
    files = generate()
    GEN.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (GEN / rel).write_text(content, encoding="utf-8")
    return len(files)


def check() -> list[str]:
    """Return the generated files that would change (empty list = fresh)."""
    files = generate()
    drift = [
        rel
        for rel, content in sorted(files.items())
        if not (GEN / rel).exists()
        or (GEN / rel).read_text(encoding="utf-8") != content
    ]
    existing = {p.name for p in GEN.glob("*.py")}
    drift.extend(sorted(existing - set(files)))
    return drift
