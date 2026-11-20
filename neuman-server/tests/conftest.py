"""Test configuration.

`fakes.py` sits beside the tests and is imported by name, so the tests read as
`from fakes import FakeAudit` rather than through a package path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
