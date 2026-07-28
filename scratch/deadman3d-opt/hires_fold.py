#!/usr/bin/env python3
"""Sweep ``deadman-3d_hires``'s ROM fold on the taped tier.

The hi-res family is named in neither :data:`machine.ROM_ROWS` nor
:data:`machine.SEEK_TIER_LAYOUT`, so its ROM takes ``_packed_fold``'s default —
a corridor 68 columns wide and **800 rows tall**, in a machine that is 573
columns wide and 1,155 rows tall.  The ROM is therefore 69% of the height and
12% of the width: the fold has simply never been swept against this program,
which grew from P=6,215 to P=8,895 in one session.

The trade is the one ``deadman-3d``'s own entry documents: one fold row buys
~6 ROM columns and costs exactly one row, so the optimum is where the ROM's
width meets the floor everything else sets, and the objective is
``max(w, h)`` (``Machine.area2``).

    python scratch/deadman3d-opt/hires_fold.py 120 160 200 217 240
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1.asm import assemble

    folds = [int(v) for v in argv] or [None]
    hires.install_wad(WAD)
    src = hires.hires_source()
    prog = assemble(src, name="deadman-3d_hires")
    machine.TAPE_SIZE["deadman-3d_hires"] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    print(f"P={prog.P} words")
    for fold in folds:
        # `SEEK_TIER_LAYOUT` is only read for slugs in `SEEK_DRUM`, and hires is
        # not one; `ROM_ROWS` is consulted unconditionally, so that is the knob.
        key = "deadman-3d_hires"
        old = machine.ROM_ROWS.get(key)
        if fold is not None:
            machine.ROM_ROWS[key] = fold
        try:
            m = machine.build_for("deadman-3d_hires", program=prog, store="taped")
            print(f"rom_rows={fold}: {m.width}x{m.height}  max={max(m.width, m.height)}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — a failed fold is an answer
            print(f"rom_rows={fold}: FAILED — {type(exc).__name__}: {exc}", flush=True)
        finally:
            if old is None:
                machine.ROM_ROWS.pop(key, None)
            else:
                machine.ROM_ROWS[key] = old
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
