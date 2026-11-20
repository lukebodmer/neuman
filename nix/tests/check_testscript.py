"""Extract a nixosTest's testScript and check it parses as Python.

A syntax error in the script fails the *driver* derivation with a message that
names `testScriptWithTypes` and a line number in generated code — which is a
long way from the file that caused it. Checking locally costs a second instead
of a VM build.
"""

import ast
import re
import sys
import textwrap
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()

match = re.search(r"testScript = ''\n(.*?)\n  '';", text, re.DOTALL)
if not match:
    print(f"{path}: no testScript block found")
    raise SystemExit(1)

# Nix strips the common leading indentation from a '' string; the extracted
# text still carries it.
script = textwrap.dedent(match.group(1))

# Nix's '' strings escape an interpolation as ''${...} and a literal '' as '''.
# Neither appears in these scripts; ${...} interpolations do, and they become
# store paths at build time. Substitute a placeholder so the Python parses.
script = re.sub(r"\$\{[^}]*\}", "/nix/store/placeholder", script)

try:
    ast.parse(script)
except SyntaxError as error:
    print(f"{path}: testScript is not valid Python")
    print(f"  line {error.lineno}: {error.text.rstrip() if error.text else ''}")
    print(f"  {error.msg}")
    raise SystemExit(1) from None

print(f"{path}: testScript parses")
