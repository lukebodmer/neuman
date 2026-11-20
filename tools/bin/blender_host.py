"""The Blender package's host: start the user's Blender with Neuman's add-on.

The client runs this when a Blender tool is dispatched and nothing answers on the
package's socket (D101). It names the socket in `NEUMAN_HOST_SOCKET` and the
Blender it found in `NEUMAN_HOST_PROGRAM`, so this process only assembles the
command line and then becomes Blender: the client tracks Blender itself, and a
signal reaches it unrelayed.

`--disable-autoexec` is not optional. A `.blend` can carry a script that runs
when the file loads, and with Neuman a file can arrive from anywhere the model
was pointed — so a scene's own code must never run just because it was opened
(D100).
"""

from __future__ import annotations

import os
import sys

# Beside this module once installed, as every tool module is (`tools/default.nix`).
ADDON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_addon.py")


def command(program: str) -> list[str]:
    return [program, "--disable-autoexec", "--python", ADDON]


def main() -> int:
    program = os.environ.get("NEUMAN_HOST_PROGRAM")
    if not program or not os.environ.get("NEUMAN_HOST_SOCKET"):
        print(
            "neuman-blender-host: started without NEUMAN_HOST_PROGRAM and "
            "NEUMAN_HOST_SOCKET; the Neuman client starts this when a Blender tool needs it",
            file=sys.stderr,
        )
        return 2
    argv = command(program)
    os.execv(argv[0], argv)


if __name__ == "__main__":
    raise SystemExit(main())
