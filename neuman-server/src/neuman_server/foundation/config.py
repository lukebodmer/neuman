"""Environment → :class:`Settings`.

Configuration comes from the environment, which systemd sets from the NixOS
module. Keeping it in the environment rather than a config file means there is
exactly one place it is computed — the module — and the server never invents a
default that disagrees with it.

Foundation holds no opinion about what the values *mean*; it only parses and
validates shape. Policy defaults live in `decision/policy`, not here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ConfigError", "Settings"]


class ConfigError(ValueError):
    """The environment does not describe a runnable server."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved server settings.

    Paths are absolute; the module guarantees it and :meth:`from_env` checks.
    """

    # Storage. One database on the appliance (docs/decisions.md D18), reached over the local
    # Unix socket with peer authentication — so there is no password to hold.
    database_url: str = "postgresql:///neuman?host=/run/postgresql"
    pool_min_size: int = 2
    pool_max_size: int = 16

    # File-backed state that deliberately does not live in the database (D18).
    state_dir: Path = Path("/var/lib/neuman/state")
    audit_mirror_path: Path = Path("/var/log/neuman/audit.jsonl")

    # Local sockets.
    admin_socket: Path = Path("/run/neuman/admin/admin.sock")

    # The tool registry: <registry_dir>/<name>/manifest.toml.
    registry_dir: Path = Path("/etc/neuman/registry")

    # HTTP surface.
    bind: str = "127.0.0.1"
    port: int = 8443
    tls_cert: Path | None = None
    tls_key: Path | None = None
    allowed_origins: tuple[str, ...] = ()

    # The units beside the server that touch weights (D78, D79). The runtime
    # host holds the GPU and answers on `runtime_socket`; the server tells it
    # what to run by writing `runtime_config`, and reaches its chat router at
    # `runtime_chat_url`. The fetcher holds the network and `models_dir`.
    # Absent sockets are an appliance without them, which the panel reports
    # rather than fails on.
    runtime_socket: Path = Path("/run/neuman/runtime/control.sock")
    runtime_config: Path = Path("/var/lib/neuman/state/runtime.json")
    runtime_chat_url: str = "http://127.0.0.1:8081/v1"
    fetch_socket: Path = Path("/run/neuman/fetch/control.sock")

    # A GPU sample from the only unit with device access, for the model panel.
    gpu_sample_path: Path = Path("/run/neuman/gpu/state.json")
    # Where a local model's weights are. Read here only to say whether a preset's
    # files are present; the server never writes them.
    models_dir: Path = Path("/var/lib/neuman/models")

    # Which build is running, stamped into each turn's log. On NixOS the module
    # passes the server's own store path, which is a content hash — so two turns
    # with the same value provably ran the same code. Empty when unset, and then
    # no stamp is written.
    build_id: str = ""

    # Logging.
    log_level: str = "INFO"

    # The longest a model's tool call may block its turn. Caps the wait the
    # harness sends, never a background job's lifetime (the manifest's) and
    # never a task's run step, whose wait is the owner's `timeout_s` (D96, D98).
    tool_wait_ms: int = 600_000

    # The most a model may generate in one step of a turn.
    #
    # Not a safety limit — the token *budget* is `policy.tokens_daily` and the
    # context ceiling is the model's own. This is how much room one reply gets,
    # and setting it too low is how a model ends up cut off mid-thought: a
    # reasoning model routinely spends thousands of tokens before its first
    # visible word, and at 4096 that was five truncations in six hours on a
    # local 27B (N90). Truncation is now recovered from rather than fatal, so
    # this is a question of how often that recovery is needed.
    #
    # Shares the context window with the prompt, so raising it reduces how much
    # history fits — `Harness._prompt_budget` reserves this much plus a margin
    # and trims against the remainder. On a 32k window, 8192 here leaves ~21k
    # for history, which is the trade an operator should be able to make
    # knowingly rather than discover.
    max_tokens: int = 4096

    # Model gateway (D20: our own thin client, no proxy framework).
    #
    # No URL and no key here. Both are per provider and declared in the
    # baseline, because one appliance reaches several providers and a single
    # key held centrally was sent to all of them. The timeout stays: it is a
    # property of this client rather than of any provider.
    gateway_timeout_s: float = 300.0

    # How long an approval request waits in the inbox before failing closed.
    approval_timeout_s: float = 300.0

    # Retention, in days. 0 disables the purge for that class.
    session_retention_days: int = 0
    audit_retention_days: int = 0

    @property
    def tls_enabled(self) -> bool:
        return self.tls_cert is not None and self.tls_key is not None

    @property
    def overlay_dir(self) -> Path:
        """Where per-user runtime policy overlays live."""

        return self.state_dir / "policies"

    @property
    def secrets_dir(self) -> Path:
        """Where provider keys entered in the administration page are kept (D77).

        Under the state directory so the unit's existing write access covers it,
        and beside the overlays rather than in them, because a backup copies the
        overlays and must not copy this.
        """

        return self.state_dir / "secrets"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        def path(name: str, default: str) -> Path:
            return Path(source.get(name) or default)

        def opt_path(name: str) -> Path | None:
            value = source.get(name)
            return Path(value) if value else None

        def integer(name: str, default: int) -> int:
            raw = source.get(name)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} is not an integer: {raw!r}") from exc

        def number(name: str, default: float) -> float:
            raw = source.get(name)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} is not a number: {raw!r}") from exc

        def items(name: str, default: str) -> tuple[str, ...]:
            raw = source.get(name)
            text = default if raw is None else raw
            return tuple(item for item in text.split(":") if item)

        settings = cls(
            database_url=source.get("NEUMAN_DATABASE_URL")
            or "postgresql:///neuman?host=/run/postgresql",
            pool_min_size=integer("NEUMAN_POOL_MIN", 2),
            pool_max_size=integer("NEUMAN_POOL_MAX", 16),
            state_dir=path("NEUMAN_STATE_DIR", "/var/lib/neuman/state"),
            audit_mirror_path=path("NEUMAN_AUDIT_MIRROR", "/var/log/neuman/audit.jsonl"),
            admin_socket=path("NEUMAN_ADMIN_SOCKET", "/run/neuman/admin/admin.sock"),
            registry_dir=path("NEUMAN_REGISTRY_DIR", "/etc/neuman/registry"),
            bind=source.get("NEUMAN_BIND") or "127.0.0.1",
            port=integer("NEUMAN_PORT", 8443),
            tls_cert=opt_path("NEUMAN_TLS_CERT"),
            tls_key=opt_path("NEUMAN_TLS_KEY"),
            allowed_origins=items("NEUMAN_ALLOWED_ORIGINS", ""),
            log_level=source.get("NEUMAN_LOG_LEVEL") or "INFO",
            runtime_socket=path("NEUMAN_RUNTIME_SOCKET", "/run/neuman/runtime/control.sock"),
            runtime_config=path("NEUMAN_RUNTIME_CONFIG", "/var/lib/neuman/state/runtime.json"),
            runtime_chat_url=source.get("NEUMAN_RUNTIME_CHAT_URL")
            or "http://127.0.0.1:8081/v1",
            fetch_socket=path("NEUMAN_FETCH_SOCKET", "/run/neuman/fetch/control.sock"),
            build_id=source.get("NEUMAN_BUILD_ID") or "",
            gpu_sample_path=path("NEUMAN_GPU_SAMPLE", "/run/neuman/gpu/state.json"),
            models_dir=path("NEUMAN_MODELS_DIR", "/var/lib/neuman/models"),
            tool_wait_ms=integer("NEUMAN_TOOL_WAIT_MS", 600_000),
            max_tokens=integer("NEUMAN_MAX_TOKENS", 4096),
            gateway_timeout_s=number("NEUMAN_GATEWAY_TIMEOUT_S", 300.0),
            approval_timeout_s=number("NEUMAN_APPROVAL_TIMEOUT_S", 300.0),
            session_retention_days=integer("NEUMAN_SESSION_RETENTION_DAYS", 0),
            audit_retention_days=integer("NEUMAN_AUDIT_RETENTION_DAYS", 0),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Raise :class:`ConfigError` unless the settings describe a runnable server."""

        if (self.tls_cert is None) != (self.tls_key is None):
            raise ConfigError(
                "NEUMAN_TLS_CERT and NEUMAN_TLS_KEY must be set together or not at all"
            )
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"NEUMAN_PORT out of range: {self.port}")
        if self.pool_min_size < 1 or self.pool_max_size < self.pool_min_size:
            raise ConfigError(
                f"invalid pool bounds: min={self.pool_min_size} max={self.pool_max_size}"
            )
        if self.max_tokens < 1:
            raise ConfigError(f"NEUMAN_MAX_TOKENS must be positive: {self.max_tokens}")
        if self.tool_wait_ms < 1:
            raise ConfigError(f"NEUMAN_TOOL_WAIT_MS must be positive: {self.tool_wait_ms}")
        for name, value in (
            ("state_dir", self.state_dir),
            ("registry_dir", self.registry_dir),
        ):
            if not value.is_absolute():
                raise ConfigError(f"{name} must be an absolute path: {value}")

    def ensure_dirs(self) -> None:
        """Create the state directories, with their modes, at every start.

        The unit's `StateDirectory` makes the parent and nothing beneath it, so
        these are the server's to make — which is what lets an operator start
        over by deleting them and starting the unit again (N167). Idempotent.
        """

        self.state_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.overlay_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        # `mode` applies only when the directory is created, and the umask can
        # narrow it further but never widen it; the chmod makes an existing one
        # right too, since a key directory anyone can list is half a leak.
        self.secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.secrets_dir.chmod(0o700)
        self.audit_mirror_path.parent.mkdir(parents=True, exist_ok=True)
