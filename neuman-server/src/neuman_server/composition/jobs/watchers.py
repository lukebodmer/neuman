"""File watchers: notification, never polling (docs/decisions.md D14).

The policy overlay is written by the admin surface and read by every dispatch.
Since policy is re-read per dispatch, a watcher is not needed for *correctness*
— it is needed so live clients can be told their policy changed without asking.

v1 polled an applied-state file's mtime every three seconds, which was both a
needless stat loop and up to three seconds of latency. D14 forbids
the pattern generally.

`inotify` is used directly through `ctypes` rather than pulling in a dependency:
the appliance is Linux-only by construction, and this is thirty lines.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...foundation import get_logger

__all__ = ["watch_directory"]

_log = get_logger("neuman.server.jobs.watchers")

# From <sys/inotify.h>.
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_DELETE = 0x00000200
_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_DELETE

_HEADER = struct.Struct("iIII")


async def watch_directory(
    directory: str | Path,
    on_change: Callable[[str], Awaitable[None]],
) -> None:
    """Call ``on_change(filename)`` whenever a file in ``directory`` changes.

    Runs until cancelled. A failure to set up the watch is logged and the
    coroutine returns: losing change notifications degrades responsiveness, it
    does not affect correctness, so it must never take the server down.
    """

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK)
    if fd < 0:
        _log.warning("inotify unavailable; overlay changes will not be announced")
        return

    watch = libc.inotify_add_watch(fd, str(path).encode(), _MASK)
    if watch < 0:
        _log.warning("cannot watch directory", fields={"path": str(path)})
        os.close(fd)
        return

    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    loop.add_reader(fd, ready.set)

    _log.info("watching for changes", fields={"path": str(path)})

    try:
        while True:
            await ready.wait()
            ready.clear()
            for name in _drain(fd):
                try:
                    await on_change(name)
                except Exception as exc:  # noqa: BLE001 - one bad handler, not the loop
                    _log.warning("change handler failed", fields={"error": str(exc)})
    except asyncio.CancelledError:
        raise
    finally:
        loop.remove_reader(fd)
        os.close(fd)


def _drain(fd: int) -> list[str]:
    """Read and parse every queued inotify event."""

    names: list[str] = []
    try:
        data = os.read(fd, 4096)
    except BlockingIOError:
        return names

    offset = 0
    while offset + _HEADER.size <= len(data):
        _wd, _mask, _cookie, length = _HEADER.unpack_from(data, offset)
        offset += _HEADER.size
        raw = data[offset : offset + length]
        offset += length
        name = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        if name:
            names.append(name)
    return names
