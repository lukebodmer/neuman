"""Describe the scene in the user's Blender, as text. Changes nothing."""

from __future__ import annotations

import sys

from blender_link import arguments, run


def main(argv: list[str]) -> int:
    args = arguments("blender_scene", argv)
    if args is None:
        return 2
    request = {"op": "scene"}
    for key in ("object", "limit"):
        if key in args:
            request[key] = args[key]
    return run("blender_scene", request)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
