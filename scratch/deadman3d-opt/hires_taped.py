#!/usr/bin/env python3
"""What each taped-tier registry is worth on ``deadman-3d_hires``, one at a time.

The optimisations landed on ``deadman-3d`` are all keyed per ``(slug, tier)``
and hires was in none of them.  They are *not* assumed to transfer: the answer
collapse and the compact gate are store-block geometry (so they should), the
bank order is traffic (so it had to be re-measured — see ``hires_banks.py``),
and the slab pitch is CPU geometry whose win depends on which side of the box
is binding.  This builds the machine with each in turn and prints the box.

    python scratch/deadman3d-opt/hires_taped.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
KEY = ("deadman-3d_hires", "taped")


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")
    M.TAPE_SIZE["deadman-3d_hires"] = max(d3.tape_slots(d3.GEOM128).values()) + 1

    def build(*, answer_west: bool, gate: bool, order: bool, pitch: int | None,
              fold: int | None = None) -> str:
        saved = (set(M.STORE_ANSWER_WEST), set(M.TAPED_COMPACT_GATE),
                 dict(M.TAPED_BANK_ORDER), dict(M.SLAB_PITCH), dict(M.ROM_ROWS))
        try:
            M.STORE_ANSWER_WEST.discard(KEY)
            M.TAPED_COMPACT_GATE.discard(KEY)
            M.TAPED_BANK_ORDER.pop(KEY, None)
            M.SLAB_PITCH.pop("deadman-3d_hires", None)
            if answer_west:
                M.STORE_ANSWER_WEST.add(KEY)
            if gate:
                M.TAPED_COMPACT_GATE.add(KEY)
            if order:
                M.TAPED_BANK_ORDER[KEY] = (3, 0, 1, 2)
            if pitch is not None:
                M.SLAB_PITCH["deadman-3d_hires"] = pitch
            if fold is not None:
                M.ROM_ROWS["deadman-3d_hires"] = fold
            m = M.build_for("deadman-3d_hires", program=prog, store="taped")
            return f"{m.width}x{m.height}  max={max(m.width, m.height)}"
        except Exception as exc:  # noqa: BLE001
            return f"FAILED — {type(exc).__name__}: {exc}"
        finally:
            (M.STORE_ANSWER_WEST, M.TAPED_COMPACT_GATE, M.TAPED_BANK_ORDER,
             M.SLAB_PITCH, M.ROM_ROWS) = None, None, None, None, None
            M.STORE_ANSWER_WEST, M.TAPED_COMPACT_GATE = saved[0], saved[1]
            M.TAPED_BANK_ORDER, M.SLAB_PITCH, M.ROM_ROWS = saved[2], saved[3], saved[4]

    print(f"P={prog.P}, tape={M.TAPE_SIZE['deadman-3d_hires']}")
    cases = [
        ("none (merge baseline)", dict(answer_west=False, gate=False, order=False, pitch=None)),
        ("+answer west        ", dict(answer_west=True, gate=False, order=False, pitch=None)),
        ("+compact gate       ", dict(answer_west=False, gate=True, order=False, pitch=None)),
        ("+bank order (3,0,1,2)", dict(answer_west=False, gate=False, order=True, pitch=None)),
        ("+slab pitch 11      ", dict(answer_west=False, gate=False, order=False, pitch=11)),
        ("all four            ", dict(answer_west=True, gate=True, order=True, pitch=11)),
        ("all but pitch       ", dict(answer_west=True, gate=True, order=True, pitch=None)),
    ]
    for name, kw in cases:
        print(f"  {name}: {build(**kw)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
