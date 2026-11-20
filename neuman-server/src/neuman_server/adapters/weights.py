"""Whether a preset's weights are in `modelsDir`, read without opening them.

The administration page draws a local model's tile as absent, partly fetched,
present, verified or wrong, and it asks on every catalog read, so the answer
must not read 16 GB. A file is *present* when its name and size match the pin,
and *verified* when `neuman-fetch` has also hashed it and left a record of that
in `.verified/<name>.json` that still describes the file — same hash as the pin,
same size, same modification time. Hashing is the fetcher's job, done once when
a file arrives or is adopted, not the panel's on every look. A size that
disagrees is called out rather than treated as absent: it is somebody's
download of something else under the name a runtime will open.

A `.part` beside a missing file is a download in progress or interrupted, the
name the fetcher writes to before it verifies and renames.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["weights_state"]


def weights_state(models_dir: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    """The state of each pinned file, and of the set.

    `state` is `verified` when every file is, `present` when every file is at
    least present, `absent` when none is begun, `wrong` when any file has
    another size, `unreadable` when the directory cannot be read, and `partial`
    otherwise.
    """

    states = []
    for file in files:
        path = models_dir / file["name"]
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            part = path.with_name(path.name + ".part")
            states.append("partial" if part.exists() else "absent")
            continue
        except OSError:
            states.append("unreadable")
            continue
        if size != file["size"]:
            states.append("wrong")
        else:
            states.append("verified" if _verified(models_dir, path, file) else "present")

    if "unreadable" in states:
        state = "unreadable"
    elif "wrong" in states:
        state = "wrong"
    elif all(item == "verified" for item in states):
        state = "verified"
    elif all(item in ("present", "verified") for item in states):
        state = "present"
    elif all(item == "absent" for item in states):
        state = "absent"
    else:
        state = "partial"

    return {
        "state": state,
        "bytes": sum(file["size"] for file in files),
        "files": [
            {"name": file["name"], "size": file["size"], "state": item}
            for file, item in zip(files, states, strict=True)
        ],
    }


def _verified(models_dir: Path, path: Path, file: dict[str, Any]) -> bool:
    """Whether the fetcher's record still describes this file and this pin.

    The record's path is `neuman-fetch`'s convention (`runtime/fetch.py`), read
    here rather than imported: the runtime is a separate process with no place
    in the server's layering.
    """

    try:
        record = json.loads(
            (models_dir / ".verified" / f"{file['name']}.json").read_text(encoding="utf-8")
        )
        stat = path.stat()
    except (OSError, ValueError):
        return False
    return (
        record.get("sha256") == file.get("sha256")
        and record.get("size") == stat.st_size
        and record.get("mtime_ns") == stat.st_mtime_ns
    )
