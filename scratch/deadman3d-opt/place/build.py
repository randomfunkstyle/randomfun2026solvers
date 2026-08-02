#!/usr/bin/env python3
"""Build the shipped ``deadman-3d_hires`` (taped) machine once and cache it.

Everything here that says "the shipped layout" means the grid this module
produces.  Building costs ~a minute, so it is cached under ``/tmp/compactor``
and reused; nothing WAD-derived is written into the repo.

Artefacts, all in ``/tmp/compactor``:

``grid.man``
    the rows, exactly as they ship.

``regions.json``
    ``{name: [x, y, w, h]}`` from ``Machine.regions`` -- the generator's own
    record of what every cell means.  Boxes are a *consequence* of the drawing
    (``_Grid._claim``), so they cannot drift from the glyphs the way a hand-typed
    box does.  This is what makes "walk **every** structure" a finite job: the
    region list *is* the block list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = Path("/Users/ptaykalo/.claude-personal/jobs/06214102/tmp/h")
OUT = Path("/tmp/compactor")
GRID = OUT / "grid.man"
REGIONS = OUT / "regions.json"


def ensure() -> tuple[list[str], dict[str, tuple[int, int, int, int]]]:
    """``(rows, regions)``.  Raises if the cache is cold rather than rebuilding."""
    if not (GRID.exists() and REGIONS.exists()):
        raise SystemExit("cache cold: run `python3 build.py` first (~1 min)")
    rows = GRID.read_text().split("\n")
    regions = {k: tuple(v) for k, v in json.loads(REGIONS.read_text()).items()}
    return rows, regions


def main() -> int:
    OUT.mkdir(exist_ok=True)
    sys.path.insert(0, str(HOME))
    import common

    d3, hires, M, prog = common.setup()
    import randomfun2026solvers.lm1.machine as mach

    assert "worktrees/compactor" in mach.__file__, mach.__file__
    print(f"machine: {mach.__file__}", flush=True)
    m = M.build_for("deadman-3d_hires", program=prog, store=hires.STORE_TIER)
    print(f"built {m.width}x{m.height}, {len(m.regions)} regions", flush=True)
    GRID.write_text("\n".join(m.rows))
    REGIONS.write_text(json.dumps({k: list(v) for k, v in m.regions.items()}, indent=0))
    print(f"wrote {GRID} and {REGIONS}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
