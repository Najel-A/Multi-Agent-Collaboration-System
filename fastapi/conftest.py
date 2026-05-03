"""Pytest configuration for the fastapi/ sanity service.

Inserts this directory at the front of sys.path so tests can do
`from app import app` and `from schemas.requests import ...` while
running pytest from the repo root or from inside fastapi/.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
