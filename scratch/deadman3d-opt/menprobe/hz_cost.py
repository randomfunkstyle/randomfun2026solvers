"""The cost model, scored against real ticks, and what it gets wrong.

    hz_cost.py

:mod:`trie_shape` prices the decode/return loop per instruction and reproduces
the machine's ``cpu:trie`` region to the tick.  The question this answers is a
different one: **is that a tick model of the machine?**  A search needs the
second, and the first does not imply it.

The table below is every point measured through :mod:`hz_run` in this session:
the model's predicted change against the change the 3-round gate actually saw.
It is reported rather than fitted, because a fitted residual on five points
would be a number with no error bars pretending to be a calibration.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trie_shape as TS  # noqa: E402

INSTRS = 880_332
SCREEN3 = 11_500_259     # shipped, 3-round gate, this session
BASE21 = 111_492_961     # shipped, 21-round gate, this session

#: ``(tag, order, slots, measured 3-round ticks or None)`` — every candidate the
#: ladder built.  ``None`` means it never got a number: it died at load, which is
#: itself a datum about how far "binds" is from "runs".
CANDS = [
    ("search seed5 free", (
        "IN", "JMPF", "MOVA", "DIV", "NEG", "INCM", "MULI", "LDA", "JMPS", "ADDI",
        "LDI", "MODI", "SUB", "SUBI", "DIVI", "MUL", "ADD", "BRN", "BRZ", "ST",
        "LD", "SND"),
     dict(IN=0, JMPF=1, MOVA=2, DIV=3, NEG=4, INCM=5, MULI=6, LDA=7, JMPS=8,
          ADDI=9, LDI=10, MODI=11, SUB=12, SUBI=13, DIVI=14, MUL=15, ADD=18,
          BRN=19, BRZ=24, ST=28, LD=30, SND=31), None),
    ("search seed1 free", (
        "IN", "MODI", "MOVA", "DIV", "ADD", "SUB", "MULI", "LDA", "NEG", "MUL",
        "LDI", "INCM", "DIVI", "SUBI", "ADDI", "LD", "JMPF", "BRN", "ST", "BRZ",
        "JMPS", "SND"),
     dict(IN=0, MODI=1, MOVA=2, DIV=3, ADD=4, SUB=5, MULI=6, LDA=7, NEG=8, MUL=9,
          LDI=10, INCM=12, DIVI=13, SUBI=14, ADDI=15, LD=16, JMPF=17, BRN=19,
          ST=24, BRZ=28, JMPS=30, SND=31), 13_152_555),
    ("search seed2 struct-fixed", (
        "IN", "NEG", "MOVA", "DIV", "MUL", "SUB", "INCM", "LDA", "DIVI", "ST",
        "ADD", "MODI", "LDI", "SUBI", "LD", "ADDI", "MULI", "BRN", "BRZ", "JMPF",
        "JMPS", "SND"),
     dict(IN=0, NEG=1, MOVA=2, DIV=3, MUL=4, SUB=5, INCM=6, LDA=7, DIVI=8, ST=11,
          ADD=12, MODI=13, LDI=14, SUBI=15, LD=16, ADDI=17, MULI=20, BRN=24,
          BRZ=27, JMPF=28, JMPS=30, SND=31), None),
    ("search seed3 free", (
        "IN", "INCM", "MOVA", "JMPS", "DIVI", "SUB", "ADD", "MUL", "LDA", "ADDI",
        "LD", "MODI", "NEG", "JMPF", "ST", "MULI", "DIV", "SUBI", "BRZ", "BRN",
        "LDI", "SND"),
     dict(IN=0, INCM=1, MOVA=2, JMPS=3, DIVI=4, SUB=6, ADD=7, MUL=8, LDA=9,
          ADDI=10, LD=12, MODI=13, NEG=14, JMPF=15, ST=16, MULI=17, DIV=24,
          SUBI=25, BRZ=26, BRN=28, LDI=30, SND=31), 11_520_528),
    ("search seed0 struct-fixed", (
        "IN", "INCM", "MOVA", "LDA", "SUBI", "MUL", "DIV", "ADD", "SUB", "DIVI",
        "LD", "MODI", "NEG", "LDI", "ADDI", "MULI", "ST", "BRN", "BRZ", "JMPF",
        "JMPS", "SND"),
     dict(IN=0, INCM=1, MOVA=2, LDA=3, SUBI=4, MUL=5, DIV=6, ADD=7, SUB=8, DIVI=9,
          LD=10, MODI=12, NEG=13, LDI=14, ADDI=15, MULI=16, ST=17, BRN=24, BRZ=25,
          JMPF=28, JMPS=30, SND=31), 12_930_493),
]

#: ``(tag, measured 3-round ticks)`` for the vectors that move no lane at all —
#: the ``squash_band`` / ``store_dy`` line the level repair opened.  The trie
#: model predicts **exactly zero** for every one of them, because the band and
#: the collector move together and every ``collector - row`` is unchanged.
SQUASH = [("k=0 dy=17", 11_682_917), ("k=3 dy=14", 11_686_468),
          ("k=5 dy=12", 11_688_968), ("k=7 dy=10 (shipped)", 11_500_259),
          ("k=9 dy=8", 11_580_537), ("k=12 dy=5", 11_743_500),
          ("k=15 dy=2", 11_826_883)]


def main():
    base = TS.price(TS.DEFAULT_ORDER, TS.contiguous(TS.DEFAULT_ORDER))
    bc = TS.opcode_cells(TS.contiguous(TS.DEFAULT_ORDER))
    print(f"shipped: loop {base['loop']:.3f} t/instr, drum {bc:,} cells, "
          f"3-round {SCREEN3:,}")
    print(f"the model covers {100 * base['loop'] * INSTRS / BASE21:.1f}% of the "
          f"21-round run\n")

    print(f"{'candidate':>26} {'modelled':>9} {'measured':>9} {'residual':>9} "
          f"{'drum':>8}")
    rows = []
    for tag, order, slots, ticks in CANDS:
        r = TS.price(order, slots)
        pred = 100 * (r["loop"] - base["loop"]) * INSTRS / BASE21
        cells = TS.opcode_cells(slots)
        if ticks is None:
            print(f"{tag:>26} {pred:+8.3f}%  {'died at load':>9} "
                  f"{'-':>9} {cells:8,}")
            continue
        meas = 100 * (ticks - SCREEN3) / SCREEN3
        rows.append((pred, meas))
        print(f"{tag:>26} {pred:+8.3f}% {meas:+8.3f}% {meas - pred:+8.3f}pp "
              f"{cells:8,}")

    if rows:
        errs = [abs(m - p) for p, m in rows]
        print(f"\n  every candidate the model liked measured WORSE than shipped.")
        print(f"  residual: mean {sum(errs) / len(errs):.1f}pp, "
              f"max {max(errs):.1f}pp, sign correct 0 of {len(rows)}")
        print("  -> the loop model is not a tick model of this machine on the "
              "(LANE_ORDER, OPCODE_SLOTS) axis, and a search that trusts it "
              "proposes regressions with confidence.")

    print(f"\n{'squash/dy line':>26} {'modelled':>9} {'measured':>9} {'residual':>9}")
    for tag, ticks in SQUASH:
        meas = 100 * (ticks - SCREEN3) / SCREEN3
        print(f"{tag:>26} {0.0:+8.3f}% {meas:+8.3f}% {meas:+8.3f}pp")
    sq = [100 * (t - SCREEN3) / SCREEN3 for _, t in SQUASH]
    print(f"\n  the model says the squash is free; the machine charges up to "
          f"{max(sq):.2f}%. It is a **small** error and it has the right shape "
          f"(shipped is the minimum), so on this axis the model ranks correctly "
          f"even though it does not price correctly.")

    print(f"\n{'3-round screen':>26} {'21-round gate':>14} {'ratio':>7}")
    for tag, s3, g21 in PAIRS:
        a = 100 * (s3 - SCREEN3) / SCREEN3
        b = 100 * (g21 - BASE21) / BASE21
        print(f"{tag:>26} {a:+8.3f}% -> {b:+8.3f}% {a / b if b else 0:6.2f}x")
    rs = [(100 * (s - SCREEN3) / SCREEN3) / (100 * (g - BASE21) / BASE21)
          for _, s, g in PAIRS]
    print(f"\n  the 3-round screen over-reads a win by {sum(rs) / len(rs):.2f}x on "
          f"average (spread {min(rs):.2f}..{max(rs):.2f}). Boot is a fixed cost "
          f"diluted over 21 rounds and not over 3, so a screen is a **ranking**\n"
          f"  instrument and never a measurement — which is what AGENTS.md means "
          f"by '3 rounds is triage only'.")


#: ``(tag, 3-round screen, 21-round gate)`` for everything that got both numbers.
PAIRS = [
    ("dx=-2", 11_434_965, 110_786_159),
    ("13x70", 11_394_738, 110_887_138),
    ("16x57", 11_401_899, 110_929_063),
    ("dx=-2 13x70", 11_334_804, 110_210_580),
    ("dx=-2 12x76", 11_379_402, 110_472_122),
    ("dx=-2 13x70 rom118", 11_399_083, 110_521_505),
    ("dx=-2 14x65", 11_298_673, 110_000_589),
    ("dx=-2 15x61", 11_298_637, 110_000_337),
    ("dx=-2 13x70 drop6", 11_334_927, 110_209_973),
    ("dx=-2 13x70 k9 dy8", 11_417_298, 111_150_430),
]


if __name__ == "__main__":
    main()
