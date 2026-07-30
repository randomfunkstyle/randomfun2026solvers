#!/usr/bin/env python3
"""Run the CPU's **dispatch loop** on its own, without building the 594x630 machine.

Why this exists
---------------
``cpu:trie`` + ``cpu:drops`` + ``cpu:return:collector`` + ``cpu:return:riser`` +
``cpu:fetch`` is 51.1% of ``deadman-3d_hires`` men-v3, and all of it is one man
walking a closed loop: fetch at ``(1, centre)``, east through the decode trie to
``lane_x0``, east along the lane, south down the drop column to the collector
under the band, west along the collector, north up the riser at column 1, back
into the fetch. A full ``build_for`` + 21-round gate is ~3.5 minutes; every
question about that loop is a question about **movement glyphs**, which both
engines agree on exactly.

So this emits a self-contained ``.man`` holding

* the **real** decode trie — ``machine._uneven_trie`` is a pure function and is
  imported, not re-implemented, so the shape under test is the shipped shape;
* one row per lane, padded to the lane's measured ``lane_end`` and dropping at
  its measured ``drop_x``;
* the collector, the riser and the fetch, glyph for glyph; and
* a synthetic ROM that streams opcode numbers in the **measured frequency mix**,

and runs it on ``FastLittleman`` in well under a second.

The lever it was written for
----------------------------
``--corridor``: a second, *high* collector row placed immediately above the trie
root, with its own descent down column 1 into the fetch cell. The arithmetic says
it should be worth 13 ticks on every instruction whose lane sits above the root
row, because such a lane currently overshoots — it walks all the way down to the
collector under the band and then climbs back up the riser to a fetch row that
was above it the whole time. Formally the loop's vertical term is

    (collector - centre) + |centre - row| + (collector - row)

and for ``row < centre`` that is ``2*collector - 2*row``, carrying ``2*(collector
- centre)`` of pure overshoot. A corridor between the lane and the root turns it
into ``centre - row``, which is the Manhattan distance and cannot be beaten.

The one thing arithmetic cannot settle is whether the corridor **runs**: its
westward ``<`` sweep has to cross the trie's own columns, where an ancestor's
vertical leg parks a ``.``, and it has to hand the man to a ``v`` at column 1
that drops him onto the fetch's ``>``. That is what this bench actually proves.

    python dispatch_bench.py            # 0, 1 and 2 corridor rows
    python dispatch_bench.py --dump 1   # print the grid and stop

and what it said, against the shipped baseline of 78.976 t/instr:

    corridor=0  |  78.976 t/instr
    corridor=1  |  70.739 t/instr   -8.238   <- shipped
    corridor=2  |  72.231 t/instr   -6.746

The real machine moved -8.007 t/instr on the 21-round gate (126.649 -> 118.642),
so the bench is 2.8% optimistic and got the sign, the size and the ranking right.

The lane table below is builder geometry — rows, micro-program lengths and drop
columns, all of which follow from *which* opcodes the program uses and not from
any level — plus a 22-number opcode *mix*. The mix is an aggregate share per
opcode, strictly coarser than the per-slot traffic ``common.py`` deliberately did
not keep, and of the same kind as the bank counts already in
``machine.TAPED_BANK_ORDER``. Nothing here reconstructs a level.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# One directory deeper than the rest of ``scratch/deadman3d-opt`` — note the count.
WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT / "solvers" / "python"))

from randomfun2026solvers.lm1.machine import _uneven_gaps, _uneven_trie  # noqa: E402

#: The shipped band, read off ``build_for("deadman-3d_hires", store="men-v3")``:
#: ``(mnemonic, rank in the band, lane_end - lane_x0, drop_x - lane_x0, share)``.
#: ``share`` is the opcode's measured frequency (the drop ``v``'s heat, which can
#: never block, differentiated down each shared column).
LANES = [
    ("IN",   0,  1,  3, 0.0054), ("INCM", 1, 17, 18, 0.0052),
    ("MOVA", 2, 16, 17, 0.0035), ("DIV",  3, 14, 15, 0.0347),
    ("ST",   4, 13, 15, 0.1581), ("SUB",  5, 14, 15, 0.0301),
    ("ADD",  6, 13, 14, 0.0811), ("LDA",  7, 12, 14, 0.0277),
    ("MUL",  8, 12, 13, 0.0121), ("DIVI", 9,  2,  3, 0.0319),
    ("LD",  10, 11, 12, 0.1891), ("MODI", 11, 2,  3, 0.0366),
    ("NEG", 12,  2,  3, 0.0011), ("SUBI", 13, 2,  3, 0.0503),
    ("ADDI", 14, 1,  3, 0.0287), ("MULI", 15, 1,  3, 0.0516),
    ("LDI", 16,  0,  3, 0.0473), ("BRN", 17,  0, 22, 0.0644),
    ("BRZ", 18,  0, 11, 0.0641), ("JMPF", 19, 0,  5, 0.0324),
    ("JMPS", 20, -1, 4, 0.0098), ("SND", 21,  2,  3, 0.0393),
]
K = 5


class Sheet:
    def __init__(self) -> None:
        self.c: dict[tuple[int, int], str] = {}

    def put(self, x: int, y: int, ch: str) -> None:
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise AssertionError(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch

    def soft(self, x: int, y: int, ch: str) -> None:
        self.c.setdefault((x, y), ch)

    def run(self, x: int, y: int, s: str, dx: int = 1, dy: int = 0) -> None:
        for ch in s:
            self.put(x, y, ch)
            x, y = x + dx, y + dy

    def room(self, x0: int, y0: int, x1: int, y1: int) -> None:
        for x in range(x0 + 1, x1):
            self.put(x, y0, "-")
            self.put(x, y1, "-")
        for y in range(y0 + 1, y1):
            self.put(x0, y, "|")
            self.put(x1, y, "|")
        for c in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.put(*c, "+")

    def pipe(self, pts):
        glyph = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
        cells = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
            sx, sy = (bx > ax) - (bx < ax), (by > ay) - (by < ay)
            x, y = ax, ay
            while (x, y) != (bx, by):
                x, y = x + sx, y + sy
                cells.append((x, y))
        n = len(cells)
        for i, (x, y) in enumerate(cells):
            din = (x - cells[i - 1][0], y - cells[i - 1][1]) if i else None
            dout = (cells[i + 1][0] - x, cells[i + 1][1] - y) if i < n - 1 else None
            ch = glyph[dout] if (i in (0, n - 1) or din != dout) and dout else (
                glyph[din] if i == n - 1 else ("-" if dout[0] else "|"))
            self.put(x, y, ch)
        return n

    def rows(self):
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        return ["".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]


def _bitrev(v: int, k: int) -> int:
    return int(format(v, f"0{k}b")[::-1], 2)


def band_rows(k, n, corridor: int):
    """Lane rows at pitch 1 (``STRAIGHT_TRIE`` removes every gap), plus — when
    ``corridor`` — that many blank rows opened directly above the root's lane.

    One row is enough once ``_uneven_trie`` anchors an inline ``d`` to its up
    child's row; two are needed if it still insists on the gap above the down
    half, because then the node splitting the pair lands *in* the corridor."""
    slots = list(range(n))
    gaps = _uneven_gaps(k, slots, True)
    at, y0 = [1], 1
    for i in range(n - 1):
        at.append(at[-1] + (2 if i in gaps else 1))
    if corridor:
        # The root's ``x`` lands on lane ``mid-1``'s row, so the corridor goes
        # between lane ``mid-2`` and lane ``mid-1``: the lowest row that is still
        # above the fetch, hence the one that serves the most lanes.
        mid = 1 << (k - 1)
        at = [r + (corridor if i >= mid - 1 else 0) for i, r in enumerate(at)]
        return at, y0, at[mid - 1] - corridor
    return at, y0, None


def build(corridor: int, mix_len: int = 200):
    n = len(LANES)
    at, y0, corr = band_rows(K, n, corridor)
    slot_rows = {i: at[i] for i in range(n)}
    lane_x0 = 4 + 2 * K
    # ``inline_far`` is what keeps an ``x`` out of the corridor: without it the
    # node splitting the pair the row was opened between stands *in* the row and
    # turns the returning man out of it. Run ``--dump 1`` with it off to see the
    # machine deadlock after seven instructions.
    centre, trie = _uneven_trie(K, slot_rows, lane_x0, True, inline_far=bool(corridor))

    end = {at[i]: lane_x0 + e for _, i, e, _d, _w in LANES}
    drop = {at[i]: lane_x0 + d for _, i, _e, d, _w in LANES}
    collector = at[-1] + 1
    ret_x = max(max(drop.values()), lane_x0) + 1

    g = Sheet()
    for (x, yy), ch in trie.items():
        g.put(x, yy, ch)
    g.run(1, centre, ">rb.")
    # Heads first (hard), then the runs (soft) — two lanes may share a drop column
    # and a southbound man keeps his heading over another lane's `v`.
    for _mn, rank, _e, _d, _w in LANES:
        r = at[rank]
        for x in range(lane_x0, end[r] + 1):
            g.soft(x, r, ".")
    for _mn, rank, _e, _d, _w in LANES:
        g.put(drop[at[rank]], at[rank], "v")
    for _mn, rank, _e, _d, _w in LANES:
        r = at[rank]
        stop = corr if (corridor and r < corr) else collector
        for yy in range(r + 1, stop):
            g.soft(drop[r], yy, ".")
    for x in range(3, ret_x + 1):  # column 2 is the spawn cell, as in ``build_cpu``
        g.soft(x, collector, "<")
    g.put(1, collector, "^")
    for yy in range(centre + 1, collector):
        g.soft(1, yy, ".")
    if corridor:
        hi = max(drop[at[i]] for _mn, i, _e, _d, _w in LANES if at[i] < corr)
        for x in range(2, hi + 1):
            g.soft(x, corr, "<")
        g.put(1, corr, "v")
        for yy in range(corr + 1, centre):
            g.soft(1, yy, ".")
    g.put(2, collector, "@")

    W = max(x for x, _ in g.c)
    H = max(y for _, y in g.c)
    g.room(0, 0, W + 1, H + 1)

    # ── the ROM: a repeating opcode stream in the measured mix ───────────────
    order = sorted(LANES, key=lambda t: -t[4])
    seq, acc = [], {mn: 0.0 for mn, *_ in LANES}
    for _ in range(mix_len):
        for mn, rank, _e, _d, w in order:
            acc[mn] += w
        best = max(order, key=lambda t: acc[t[0]])
        acc[best[0]] -= 1.0
        seq.append(_bitrev(best[1], K))
    body = "".join(f"`{v}`s" if v > 9 else f"{v}s" for v in seq)
    dx, L = W + 6, len(body)
    g.room(dx, 0, dx + L + 4, 3)
    g.run(dx + 1, 1, ">@")
    g.run(dx + 3, 1, body)
    g.put(dx + L + 3, 1, "v")
    g.put(dx + L + 3, 2, "<")
    g.run(dx + 2, 2, "." * (L + 1))
    g.put(dx + 1, 2, "^")
    g.pipe([(dx - 1, 1), (W + 3, 1), (W + 3, centre), (W + 2, centre)])
    return g.rows(), centre, collector, corr, at


def measure(rows, centre, ticks=2_000_000):
    from randomfun2026solvers.fast_littleman import FastLittleman

    res = FastLittleman("\n".join(rows)).run("", max_ticks=ticks, profile=True, profile_stride=1)
    p = res.profile
    n = p.heat.get((3, centre), 0)  # the fetch's `b`: one visit an instruction, never blocks
    return res, n


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", type=int)
    ap.add_argument("--ticks", type=int, default=2_000_000)
    a = ap.parse_args(argv)
    if a.dump is not None:
        rows, *_ = build(a.dump)
        print("\n".join(rows))
        return 0

    base = None
    for corridor in (0, 1, 2):
        rows, centre, collector, corr, at = build(corridor)
        res, n = measure(rows, centre, a.ticks)
        if res.fatal or not n:
            print(f"corridor={corridor}: FATAL {res.fatal} instrs={n}")
            continue
        per = res.step / n
        tag = f"corridor={corridor}"
        if base is None:
            base = per
        print(f"  {tag} centre={centre:3d} collector={collector:3d} corr={corr} "
              f"| {n:6d} instrs, {per:7.3f} t/instr  ({per - base:+7.3f})")
    # what the arithmetic says
    at0, _y0, _c = band_rows(K, len(LANES), 0)
    centre0 = _uneven_trie(K, {i: at0[i] for i in range(len(LANES))}, 4 + 2 * K, True)[0]
    above = sum(w for _mn, i, _e, _d, w in LANES if at0[i] < centre0)
    print(f"\n  predicted: {above:.4f} of instructions sit above the root row; "
          f"each saves 13 ticks -> {-13 * above:+.3f} t/instr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
