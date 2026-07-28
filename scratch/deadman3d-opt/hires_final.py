"""The hires consolidation sweep: both new registries, then the fold.

`OPCODE_SLOTS` and `DOOM_LOOP_ROW` both *shrink* something the fold trades
against — the drum's data columns and the DOOM block's height — so the
`ROM_ROWS` crossing has to be re-derived after them, not before.  This builds
the machine (no PNGs, no debug sidecars) across the space and prints the box.

    ./.venv/bin/python scratch/deadman3d-opt/hires_final.py --wad ~/DOOM1.WAD \
        [--slots 0|1] [--loop-row N] [--rows a,b,c]
"""
import argparse
import hashlib
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers import deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble

KEY = ("deadman-3d_hires", "taped")

# `hires_slots.py`'s DP optimum against hires' own static histogram.
HIRES_SLOTS = {
    "IN": 0, "INCM": 1, "MOVA": 2, "DIV": 3, "ST": 4, "SUB": 5,
    "ADD": 8, "LDA": 9, "MUL": 10, "DIVI": 11, "LD": 12, "MODI": 13,
    "NEG": 14, "SUBI": 16, "ADDI": 17, "MULI": 18, "LDI": 20, "BRN": 21,
    "BRZ": 22, "JMPF": 24, "SND": 28,
}

ap = argparse.ArgumentParser()
ap.add_argument("--wad", type=Path, required=True)
ap.add_argument("--slots", type=int, default=None, help="0/1; default: sweep both")
ap.add_argument("--loop-row", dest="loop_row", default=None,
                help="int, 'none', or a comma list; default: sweep 10..27 coarsely")
ap.add_argument("--rows", default=None, help="comma list of rom_rows")
ap.add_argument("--answer-west", dest="answer_west", action="store_true",
                help="also put hires in STORE_ANSWER_WEST")
ap.add_argument("--store-offset", dest="store_offset", type=int, default=None,
                help="TIER_LAYOUT store_offset dx to try with --answer-west")
ap.add_argument("--slab-pitch", dest="slab_pitch", type=int, default=None)
args = ap.parse_args()

hires.install_wad(args.wad)
prog = assemble(hires.hires_source(), name="deadman-3d_hires")
M.TAPE_SIZE["deadman-3d_hires"] = max(d3.tape_slots(hires.GEOM).values()) + 1


def box(slots: bool, loop_row, rom_rows):
    M.OPCODE_SLOTS.pop(KEY, None)
    M.DOOM_LOOP_ROW.pop(KEY, None)
    if slots:
        M.OPCODE_SLOTS[KEY] = HIRES_SLOTS
    if loop_row is not None:
        M.DOOM_LOOP_ROW[KEY] = loop_row
    M.STORE_ANSWER_WEST.discard(KEY)
    M.SLAB_PITCH.pop("deadman-3d_hires", None)
    M.TIER_LAYOUT.pop(KEY, None)
    if args.answer_west:
        M.STORE_ANSWER_WEST.add(KEY)
    if args.store_offset is not None:
        M.TIER_LAYOUT[KEY] = {"store_offset": (args.store_offset, 0)}
    if args.slab_pitch is not None:
        M.SLAB_PITCH["deadman-3d_hires"] = args.slab_pitch
    old = M.ROM_ROWS["deadman-3d_hires"]
    if rom_rows is not None:
        M.ROM_ROWS["deadman-3d_hires"] = rom_rows
    try:
        m = M.build_for("deadman-3d_hires", program=prog, store="taped")
        h = hashlib.sha256("\n".join(m.rows).encode()).hexdigest()[:12]
        return m.width, m.height, h
    finally:
        M.ROM_ROWS["deadman-3d_hires"] = old


def parse_loop(s):
    if s is None:
        return [None] + list(range(10, 28))
    return [None if p.strip() in ("none", "None") else int(p) for p in s.split(",")]


slots_vals = [bool(args.slots)] if args.slots is not None else [False, True]
loop_vals = parse_loop(args.loop_row)
row_vals = ([int(r) for r in args.rows.split(",")] if args.rows else [None])

print(f"{'slots':>6} {'loop':>5} {'rows':>5}   box          max(w,h)  {'sha':<13} secs")
for s in slots_vals:
    for lr in loop_vals:
        for rr in row_vals:
            t = time.time()
            try:
                w, h, sha = box(s, lr, rr)
                out = f"{w}x{h}"
                mx = str(max(w, h))
            except Exception as exc:  # noqa: BLE001
                out, mx, sha = f"FAIL {type(exc).__name__}: {exc}"[:70], "-", "-"
            print(f"{int(s):>6} {str(lr):>5} {str(rr):>5}   {out:<12} {mx:>8}  "
                  f"{sha:<13} {time.time() - t:5.1f}", flush=True)
