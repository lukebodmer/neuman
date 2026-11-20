"""A task is a standing approval, not a principal.

See `model.py` for the recipe, the envelope and the schedule arithmetic, and
`docs/decisions.md` D92–D97 for why the object exists in this shape.
"""

from .model import (
    CATCH_UP,
    RECORD_TOOL,
    RUN_DIR,
    RUN_RECORD,
    SHELL,
    AgentStep,
    Recipe,
    RunStep,
    TaskEnvelope,
    TaskInput,
    TaskSchedule,
    TaskValidationError,
    command_line,
    intersect,
    resolve_inputs,
    run_dir_for,
    run_status,
    should_run,
    step_environment,
    validate,
    within_write,
)

__all__ = [
    "CATCH_UP",
    "RECORD_TOOL",
    "RUN_DIR",
    "RUN_RECORD",
    "SHELL",
    "AgentStep",
    "Recipe",
    "RunStep",
    "TaskEnvelope",
    "TaskInput",
    "TaskSchedule",
    "TaskValidationError",
    "command_line",
    "intersect",
    "resolve_inputs",
    "run_dir_for",
    "run_status",
    "should_run",
    "step_environment",
    "validate",
    "within_write",
]
