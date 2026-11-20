"""The server's entry point: `python -m neuman_server`.

Deliberately thin. Everything it does is already decided elsewhere —
`Settings.from_env` validates the environment, `create_app` wires the parts,
and the lifespan in `composition/app.py` owns startup ordering. This file
exists so there is *one* way to start the process, and so the systemd unit
names a command rather than an incantation.

Two things it does own, because nothing else can:

* **Failing before uvicorn.** A bad environment must be a startup error with a
  message naming the variable, not a traceback from inside an ASGI server that
  has already bound a port. `Settings.from_env` raises `ConfigError`; this
  catches it and exits 78 (`EX_CONFIG`), which systemd surfaces as a
  configuration problem rather than a crash.

* **Starting both listeners.** The network port and the admin socket are
  two surfaces over one core (D9), and `composition/serve.py` runs them in
  one event loop so they share a pool, a registry, and one startup.

* **Quieting uvicorn's own logging.** It writes its access log and startup
  chatter in its own format, with ANSI colour, into the same stream as our
  canonical JSON records (`docs/decisions.md`). A log stream that is *mostly* JSON is
  worse than one that is not, because a parser will accept it until it does
  not.
"""

from __future__ import annotations

import asyncio
import sys

from .foundation import ConfigError, Settings, get_logger

# sysexits.h: the environment is wrong, as distinct from the program failing.
EX_CONFIG = 78

_log = get_logger("neuman.server")


def main(argv: list[str] | None = None) -> int:
    del argv  # no options: the environment is the interface (see config.py)

    try:
        settings = Settings.from_env()
    except ConfigError as error:
        # Before the logger is configured, so this goes to stderr plainly.
        print(f"neuman-server: {error}", file=sys.stderr)
        return EX_CONFIG

    from .composition.app import create_app

    try:
        app = create_app(settings)
    except (ConfigError, RuntimeError) as error:
        # `RuntimeError`: `build_services` raises it when the tool registry is
        # unusable. An appliance that cannot describe its tools must not come up
        # pretending it can work (D5).
        #
        # `ConfigError`: not every configuration error is found by
        # `Settings.from_env`. A gateway URL with an unreadable key file is only
        # detectable when the file is read, which happens here — and without
        # this clause it escaped as a traceback and exited 1, so systemd retried
        # five times and gave up with `start-limit-hit`. The message naming the
        # missing path was in the journal, buried under a stack trace and a
        # restart loop (N44).
        print(f"neuman-server: {error}", file=sys.stderr)
        return EX_CONFIG

    if not settings.tls_enabled:
        # Not fatal: the module may terminate TLS in front of us, and refusing
        # to start would make that configuration impossible. But it is worth
        # saying once, loudly, because the session surface is the untrusted
        # network surface (D1) and a cookie marked Secure is never sent over
        # plain HTTP — so the symptom is a login that silently never persists.
        _log.warning(
            "serving without TLS; session cookies are Secure and will not be "
            "sent over plain http",
            fields={"bind": settings.bind, "port": settings.port},
        )

    from .composition.serve import serve

    # Both listeners, one event loop, one lifespan: see `composition/serve.py`.
    asyncio.run(serve(app, settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
