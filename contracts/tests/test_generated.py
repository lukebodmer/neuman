"""The generated package accepts every valid fixture and rejects every invalid one."""

import json
import sys
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTRACTS / "gen" / "python"))

import neuman_contracts as nc  # noqa: E402

MODULES = {
    "baseline": nc.baseline,
    "dispatch-rpc": nc.dispatch_rpc,
    "log-record": nc.log_record,
    "task": nc.task,
    "task-run": nc.task_run,
    "tool-manifest": nc.tool_manifest,
    "ws": nc.ws,
}


def test_generated_modules_exist() -> None:
    module_names = {base.replace("-", "_") for base in MODULES}
    assert module_names <= set(nc.__all__)


def test_fixtures_against_generated() -> None:
    for base, module in MODULES.items():
        for path in sorted((CONTRACTS / "examples" / base / "valid").glob("*.json")):
            module.validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((CONTRACTS / "examples" / base / "invalid").glob("*.json")):
            instance = json.loads(path.read_text(encoding="utf-8"))
            assert not module.is_valid(instance), f"{path} was accepted"
