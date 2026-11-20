"""The one canonical log record.

The decision core emits one JSON object per line on stderr, in the shape of
``contracts/schemas/log-record.schema.json``::

    {"ts": "...", "level": "info", "target": "neuman.server",
     "message": "...", "request_id": "...", "fields": {...}}

The fields are named once, in the schema, with golden fixtures beside it, so a
change to the record is a change to a contract rather than to a format string.
The client's daemon is deliberately not held to it: its lines go to the user's
own journal, where the person who ran it is the reader.

Two rules this module enforces rather than documents:

**Structured context goes in ``fields``, never in ``extra=``.**
``logging.makeRecord`` raises ``KeyError`` when an ``extra=`` key collides with
a ``LogRecord`` attribute (``created``, ``name``, ``module``, ``args``, …), and
it raises it *at emit time* — which crashed v1's server startup repeatedly
. v1 answered with renamed fields and an AST test that scanned
every call site. Here the collision is made impossible instead: callers pass
``fields=``, which is packed into a single reserved key, so no caller-chosen
name is ever a ``LogRecord`` attribute.

**Fields are scalars.** The schema says so: nested structure belongs in the
audit chain, not the log. Non-scalars are coerced to a string rather than
dropped, so a mistake is visible instead of silent.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import sys
from collections.abc import Iterator, Mapping
from typing import IO, Any

__all__ = [
    "JsonFormatter",
    "bind",
    "configure_logging",
    "get_logger",
    "log_context",
]

# The one key this module puts on a LogRecord. Chosen to be namespaced so it
# cannot collide with a stdlib attribute, and never settable by a caller.
_FIELDS_KEY = "neuman_fields"

# Default is None rather than {}: a mutable default is shared across every
# context that never set it, so one mutation would leak everywhere.
_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "neuman_log_context", default=None
)


def _context() -> dict[str, Any]:
    return _CONTEXT.get() or {}


# Fields promoted to top level by the schema rather than nested under `fields`.
_PROMOTED = ("request_id",)

# Presentation-only extras from third-party loggers. uvicorn attaches an
# ANSI-coloured copy of its message, which would corrupt structured output
# .
_DROP = frozenset({"color_message"})

_LEVELS = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
    # The schema has no `critical`; fold it into `error` rather than emit an
    # invalid record.
    "CRITICAL": "error",
}


def _scalar(value: Any) -> str | int | float | bool | None:
    """Coerce to a schema-permitted scalar, stringifying anything else."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one schema-valid JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = dt.datetime.fromtimestamp(record.created, tz=dt.UTC)
        payload: dict[str, Any] = {
            "ts": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": _LEVELS.get(record.levelname, "info"),
            "target": record.name,
            "message": record.getMessage(),
        }

        # Bound context first so per-call fields win on collision.
        fields: dict[str, Any] = dict(_context())
        fields.update(getattr(record, _FIELDS_KEY, None) or {})

        # Third-party loggers (uvicorn) still use `extra=`; accept what they
        # give us rather than losing it, minus the presentation-only keys.
        for key, value in record.__dict__.items():
            if key in _DROP or key in _RESERVED:
                continue
            fields.setdefault(key, value)

        for name in _PROMOTED:
            if name in fields:
                payload[name] = str(fields.pop(name))

        if record.exc_info:
            fields["exception"] = self.formatException(record.exc_info)

        if fields:
            payload["fields"] = {key: _scalar(value) for key, value in fields.items()}

        return json.dumps(payload, separators=(",", ":"), default=str)


# Attributes present on every LogRecord by default. Anything else in
# record.__dict__ arrived via `extra=` from code we do not own.
_RESERVED: frozenset[str] = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "taskName",
    _FIELDS_KEY,
}


class _Logger:
    """A logger that takes ``fields=`` instead of ``extra=``.

    The whole point is that ``fields`` can never crash the emit: its contents
    are packed under one reserved key rather than splatted onto the record.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @property
    def name(self) -> str:
        return self._logger.name

    def _emit(
        self,
        level: int,
        message: str,
        fields: Mapping[str, Any] | None,
        exc_info: bool,
        extra_fields: dict[str, Any],
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        merged: dict[str, Any] = dict(fields or {})
        merged.update(extra_fields)
        self._logger.log(
            level,
            message,
            extra={_FIELDS_KEY: merged},
            exc_info=exc_info,
            stacklevel=3,
        )

    def debug(self, message: str, fields: Mapping[str, Any] | None = None, **kw: Any) -> None:
        self._emit(logging.DEBUG, message, fields, False, kw)

    def info(self, message: str, fields: Mapping[str, Any] | None = None, **kw: Any) -> None:
        self._emit(logging.INFO, message, fields, False, kw)

    def warning(self, message: str, fields: Mapping[str, Any] | None = None, **kw: Any) -> None:
        self._emit(logging.WARNING, message, fields, False, kw)

    def error(
        self,
        message: str,
        fields: Mapping[str, Any] | None = None,
        *,
        exc_info: bool = False,
        **kw: Any,
    ) -> None:
        self._emit(logging.ERROR, message, fields, exc_info, kw)

    def exception(
        self, message: str, fields: Mapping[str, Any] | None = None, **kw: Any
    ) -> None:
        self._emit(logging.ERROR, message, fields, True, kw)


def get_logger(name: str) -> _Logger:
    """Return the logger for a dotted component name (convention: ``neuman.*``)."""

    return _Logger(logging.getLogger(name))


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind fields to every record emitted inside the block.

    Stored in a :mod:`contextvars` variable, so the binding follows into
    :mod:`asyncio` tasks created inside the block. Nested blocks merge with,
    and are restored around, the enclosing context.
    """

    token = _CONTEXT.set({**_context(), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def bind(**fields: Any) -> None:
    """Bind fields for the remainder of the current context.

    For entry points that own their whole task (a request handler, a job run)
    and have no enclosing block to scope.
    """

    _CONTEXT.set({**_context(), **fields})


def configure_logging(
    level: int | str = logging.INFO,
    stream: IO[str] | None = None,
) -> None:
    """Install the JSON handler on the root logger.

    Idempotent: replaces previously installed handlers, so it is safe from
    tests and after a fork.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers and duplicates access logs through a
    # second logger; route everything through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
