#!/usr/bin/env python3
"""Build the shipped taped hires machine and dump its grid to /tmp.

The grid is derived from DOOM1.WAD art, so it never leaves /tmp.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
OUT = Path("/tmp/d3hires-taped")


def build():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    m = M.build_for(SLUG, program=prog, store="taped")
    return m, prog


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    m, prog = build()
    print(f"built {m.width}x{m.height}", flush=True)
    rows = list(m.rows)
    (OUT / "grid.man").write_text("\n".join(rows) + "\n")
    meta = {"width": m.width, "height": m.height, "nrows": len(rows),
            "regions": {k: list(v) for k, v in m.regions.items()}}
    v = getattr(prog, "labels", None)
    if isinstance(v, dict):
        meta["labels"] = dict(v)
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print(f"wrote {OUT/'grid.man'} rows={len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
