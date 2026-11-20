"""Look at the scene in the user's Blender: a picture the model sees (D28).

Drawn offscreen and handed back on stdout, so it writes no file and changes
nothing the person can see — which is why it is not mutating, where `browse`'s
`shot` writes a PNG and so asks.
"""

from __future__ import annotations

import sys

from blender_link import arguments, run


def main(argv: list[str]) -> int:
    args = arguments("blender_view", argv)
    if args is None:
        return 2
    request = {"op": "view"}
    for key in ("view", "object", "shading", "size"):
        if key in args:
            request[key] = args[key]
    return run("blender_view", request)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
