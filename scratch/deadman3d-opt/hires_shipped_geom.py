#!/usr/bin/env python3
"""The box and the screens' row of the machine the registries actually build."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers import deadman3d_hires as hires  # noqa: E402
from randomfun2026solvers.lm1 import machine as M  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402

hires.install_wad(WAD)
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
m = M.build_for(SLUG, program=assemble(hires.hires_source(), name=SLUG), store="taped")
print(f"box {m.width}x{m.height}")
print(f"lift={M.DOOM_CLUSTER_LIFT[(SLUG, 'taped')]} "
      f"north_up={M.DOOM_PACK_NORTH_UP[(SLUG, 'taped')]}")
for name in ("stream:cluster", "stream:t0:unit", "stream:t2:unit"):
    x, y, w, h = m.regions[name]
    print(f"  {name:<16} y{y}..{y + h - 1}  x{x}..{x + w - 1}")
