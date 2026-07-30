#!/usr/bin/env python3
"""The router's outlet columns, against ``BLOCK_X0``.

Whether a leg may descend past a block row at all is decided here: a leg drops
straight down its outlet column before it turns east, so an outlet at or east of
:data:`d3_router.BLOCK_X0` would run through any block placed in the west logic
column above its target.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import d3_router as R  # noqa: E402

print(f"LEAF0={R.LEAF0} LEAF_PITCH={R.LEAF_PITCH} DEST_LEAF={R.DEST_LEAF}")
print(f"outlets by tile = {R.outlet_cols()}")
print(f"RX={R.RX} IW={R.IW} (router spans cols {R.RX}..{R.RX + R.IW + 1})")
print(f"BLOCK_X0={R.BLOCK_X0}  -> west logic column starts at {R.RX + R.BLOCK_X0}")
print(f"all outlets west of the block column? "
      f"{all(R.RX + c < R.RX + R.BLOCK_X0 for c in R.outlet_cols())}")
