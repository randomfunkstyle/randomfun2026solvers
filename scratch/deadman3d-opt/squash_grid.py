#!/usr/bin/env python3
"""The ``(squash rows, rom_touch_drop)`` feasibility grid, geometry only.

``squash_h_probe.py`` establishes that a *partial* squash coexists with
``SEEK_TELEPORT`` for k=1..7, and that k=8 is refused not by room H — whose band
comes out clear — but by a §7.1 **tie**::

    'r' at (10, 158) must bind 'rom' but distances are
        [('rom', 25), ('in', 25), ('mem_resp', 65)]

A tie is a distance, and ``ROM_TOUCH_DROP`` is the registry that moves one. So the
question is whether the drop that unlocks the deepest squash exists, which is a
two-dimensional feasible region rather than the one-dimensional one
``rom_touch_probe.py`` swept.

    python scratch/deadman3d-opt/squash_grid.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(REPO))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")


def classify(err: str) -> str:
    """Collapse a MachineError to which constraint refused the build."""
    if err is None:
        return "ok"
    if "room H" in err:
        return "H"      # room H's band is not clear
    if "must bind" in err:
        return "B"      # a §7.1 binding/tie failure
    if "collision" in err:
        return "X"      # a hard geometric collision
    return "?"


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    ks = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    drops = [5, 10, 14, 18, 22, 26, 28, 30]
    print("rows = squash k, cols = rom_touch_drop; ok / H=room H / B=binding / X=collision",
          flush=True)
    print("     k |" + "".join(f"{d:>6}" for d in drops), flush=True)
    detail: list[str] = []
    for k in ks:
        cells = []
        for d in drops:
            sq: bool | int = False if k == 0 else k
            try:
                m = M.build_for(SLUG, program=prog, store="taped",
                                squash_band=sq, rom_touch_drop=d)
                cells.append(f"{m.height:>6}")
            except Exception as exc:  # noqa: BLE001
                tag = classify(f"{exc}")
                cells.append(f"{tag:>6}")
                if tag == "B" and k >= 7:
                    detail.append(f"  k={k} drop={d}: {exc}")
        print(f"  {k:>4} |" + "".join(cells), flush=True)
    print("\n(cell = machine height when it builds)", flush=True)
    for line in detail[:12]:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
