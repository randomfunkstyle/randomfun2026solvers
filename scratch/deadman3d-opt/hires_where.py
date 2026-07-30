#!/usr/bin/env python3
"""Which checkout is on sys.path — the pin that has cost agents whole runs."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

import randomfun2026solvers as pkg  # noqa: E402
from randomfun2026solvers.lm1 import d3_router  # noqa: E402

print(f"repo    = {REPO}")
print(f"package = {pkg.__file__}")
print(f"router  = {d3_router.__file__}")
