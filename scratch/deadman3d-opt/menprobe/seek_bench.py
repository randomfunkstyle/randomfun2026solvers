#!/usr/bin/env python3
"""Run the CPU's **seek tail** on its own, without building the 595x630 machine.

Why this exists
---------------
A full ``build_for`` + gate cycle on ``deadman-3d_hires`` is ~70s, and every
question about the seek tail — "is the walk back from the ``s`` on the critical
path?", "what does the flush cost per word?", "would a resident reader help?" —
is a question about **nine rows and two pipes**. Three agents in one day
re-derived the same geometry because there was no way to poke at it. This is that
way: it emits a complete, self-contained ``.man`` holding

* the seek tail exactly as ``machine.build_cpu`` draws it (send row, westbound
  corridor, the ``r``/``X`` flush loop, the sentinel exit, the 2x4 counted
  discard, the riser) with the **walk length as a parameter**; and
* a synthetic drum: a ``q``/``a`` gadget, a filler stream, and a request path
  that waits ``latency`` ticks and then answers with the ``-1`` sentinel and the
  remainder,

and runs it on ``FastLittleman`` in a fraction of a second. Nothing here is
WAD-derived, so it is safe to keep.

What it is for
--------------
The question it was written for is the **Y split at the send**: the user's
proposal is one man sending at the ``s`` while another waits at the flush ``r``,
so the 46-cell walk between them stops being paid. The bench answers it *without
``Y``*, which matters — ``AGENTS.md`` pins that ``FastLittleman`` understates
ticks once ``Y`` is used and the reference engine OOMs on this machine, so a
``Y`` result would have no oracle. The trick is that a resident reader cannot
possibly beat a **walk of zero**, so sweeping ``--walk 0`` against ``--walk 46``
bounds the split from above using nothing but movement glyphs, which both engines
agree on.

    python seek_bench.py                       # the shipped geometry
    python seek_bench.py --walk 0 46 92        # sweep the walk
    python seek_bench.py --latency 0 200 600   # sweep the drum

Fidelity, and the answer it gave
-------------------------------
Calibrated against a 6-round profile of the shipped machine (2,712 taken seeks in
28,324,139 ticks, ``cpu:seek:*`` regions):

| per taken seek     | real machine | bench (``--word 12 --latency 300``) |
|--------------------|--------------|-------------------------------------|
| walk               | 46.5, 0 blk  | 46.0, 0 blk                         |
| flush, words       | 85 @ 6 ticks | 85 @ 6 ticks                        |
| discard, words     | 38 @ 4 ticks | 38 @ 4 ticks                        |
| sentinel / riser   | 7.1 / 18.2   | 7 / 18                              |

and the sweep that settles the split, at that calibration::

    walk    4 |  1904.8 t/seek | walk   4.0 | flush blocked 687.6
    walk   24 |  1904.8 t/seek | walk  24.0 | flush blocked 647.6
    walk   46 |  1904.8 t/seek | walk  46.0 | flush blocked 603.6   <- shipped
    walk  200 |  1904.8 t/seek | walk 200.0 | flush blocked 295.6
    walk  400 |  1904.8 t/seek | walk 400.0 | flush blocked   0.1
    walk  600 |  2531.6 t/seek | walk 600.0 | flush blocked   0.0

Every walked cell comes **exactly** off the blocked count, tick for tick, until
the slack runs out somewhere past 400 cells. The total does not move. A resident
reader at the ``r`` is the ``walk 4`` row, so the Y split's ceiling is zero — and
the same experiment on the real grid agrees: padding the walk by 44 cells costs
+0.0016%, by 132 costs +0.09%, by 220 costs +0.25%.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT / "solvers" / "python"))


class Sheet:
    """A sparse character canvas. Same discipline as ``machine._Grid``: a second
    glyph on an occupied cell is a bug, not an overwrite."""

    def __init__(self) -> None:
        self.c: dict[tuple[int, int], str] = {}

    def put(self, x: int, y: int, ch: str) -> None:
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise AssertionError(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch

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

    def pipe(self, points: list[tuple[int, int]]) -> int:
        """A rectilinear pipe through ``points``; returns its length in cells."""
        glyph = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
        cells = [points[0]]
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            sx, sy = (bx > ax) - (bx < ax), (by > ay) - (by < ay)
            x, y = ax, ay
            while (x, y) != (bx, by):
                x, y = x + sx, y + sy
                cells.append((x, y))
        n = len(cells)
        for i, (x, y) in enumerate(cells):
            din = (x - cells[i - 1][0], y - cells[i - 1][1]) if i else None
            dout = (cells[i + 1][0] - x, cells[i + 1][1] - y) if i < n - 1 else None
            if i == 0 or i == n - 1 or din != dout:
                ch = glyph[dout] if dout else glyph[din]
            else:
                ch = "-" if dout[0] else "|"
            self.put(x, y, ch)
        return n

    def rows(self) -> list[str]:
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        return ["".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]


def literal(n: int) -> str:
    """The glyphs that leave ``n`` in A. Digits only where possible."""
    if 0 <= n <= 9:
        return str(n)
    if n < 0:
        return literal(-n) + "N"
    return "`" + str(n) + "`"


def build(
    walk: int = 46, latency: int = 0, corridor: int = 49, rem: int = 38, word: int = 6
) -> list[str]:
    """The seek tail plus a synthetic drum, as a loadable grid.

    ``walk`` is the number of cells the man traverses westbound between the ``s``
    and the flush loop — 46 on the shipped machine. ``corridor`` is the length of
    the drum -> CPU pipe, hence its capacity in words (49 on the shipped
    machine). ``latency`` is how long the drum dawdles after taking the request
    before it answers with the sentinel. ``rem`` is the in-row offset the CPU
    then counts off in the 2x4 discard.

    ``word`` is the drum's cost per emitted word, and it is the knob that decides
    which of two regimes the bench is in — the thing that took longest to get
    right, and the reason a first version of this bench disagreed with the real
    machine:

    * ``word`` below the flush loop's 6 ticks a word: the drum out-produces the
      CPU, the corridor never empties mid-flush, and the only wait is the final
      one for the sentinel — which the drum cannot even *start* until the CPU has
      drained enough for it to unblock and reach its ``q``. The whole loop is
      CPU-paced, so every walked cell costs a tick.
    * ``word`` above 6: the CPU out-consumes the drum, empties the corridor part
      way through the flush and blocks on nearly every remaining word. The walk
      now runs while the corridor is still full, so it is **absorbed** up to the
      buffer's worth of slack.

    The real machine is squarely in the second regime — it flushes 85 words out
    of a 49-word corridor per seek, which is only possible if it is consuming
    faster than the drum supplies.
    """
    if walk < 2:
        raise ValueError("the walk has to hold the turn and the flush entry")
    g = Sheet()

    # ── the CPU's seek tail, glyph for glyph as ``build_cpu`` draws it ────────
    # interior rows 1..9; row 1 is the taken row, 2 the westbound corridor,
    # 3..5 the flush loop and its sentinel exit, 6..9 the counted discard.
    s_x = walk + 2                       # the `s`; its turn `v` is one east
    cw = max(s_x + 1, 7)                 # interior width; the sentinel exit needs 6
    g.room(0, 0, cw + 1, 10)
    g.run(1, 1, ">@")                    # the riser's turn, and the spawn
    g.run(3, 1, "." * (s_x - 3))
    g.run(s_x, 1, "sv")                  # send, then turn south
    g.put(s_x + 1, 2, "<")               # the westbound walk
    g.run(4, 2, "." * (s_x - 3))
    g.run(2, 2, ">v")                    # the flush loop's return, and its top
    g.run(2, 3, "^r")                    # the flush read
    g.run(2, 4, "^Xrbv")                 # sign test; A<0 goes east to the remainder
    g.run(2, 5, "^<")
    g.put(6, 5, ".")
    g.run(1, 6, "^a<..<")                # the discard loop's entry
    g.run(2, 7, "rm")
    g.run(2, 8, "rm")
    g.run(2, 9, ">^")
    g.run(1, 2, "." * 4, 0, 1)           # the riser back to the taken row

    # ── the drum: notice, delay, sentinel, remainder, and a filler stream ─────
    # Row 2 is the idle lap: `q` counts the request pipe and `a` turns north out
    # of it when one is pending. The filler `s`s block once the corridor is full,
    # which is exactly why the real drum cannot notice a request promptly — it is
    # stalled at a send, not at a gadget.
    answer = "r" + "." * latency + literal(-1) + "s" + literal(rem) + "s"
    if word < 2:
        raise ValueError("a word costs at least the digit and the send")
    unit = "2" + "." * (word - 2) + "s"
    nfill = max(len(answer), 4 * word)
    nfill += -nfill % word               # whole units
    # The corridor's length is set below by moving the *request* pipe's room one
    # column east for an even capacity, so ``dx`` carries the parity.
    dx = cw + 4 + (corridor % 2 == 0)
    dw = 5 + nfill
    g.room(dx, 0, dx + dw + 1, 4)
    g.run(dx + 1, 2, ">@qa")             # BP>0 -> north into the answer path
    g.run(dx + 5, 2, unit * (nfill // word))
    g.put(dx + 5 + nfill, 2, "v")
    g.put(dx + 4, 1, ">")
    g.run(dx + 5, 1, answer)
    g.run(dx + 5 + len(answer), 1, "." * (nfill - len(answer)))
    g.put(dx + 5 + nfill, 1, "v")
    g.put(dx + 5 + nfill, 3, "<")
    g.run(dx + 2, 3, "." * (nfill + 3))
    g.put(dx + 1, 3, "^")

    # ── the two pipes ────────────────────────────────────────────────────────
    # request: CPU east wall -> drum west wall, on the send's own row.
    g.pipe([(cw + 2, 1), (dx - 1, 1)])
    # corridor: drum -> CPU, snaked south and back west to exactly ``corridor``
    # cells, because a pipe's capacity is its length and the flush's cost is the
    # number of words in flight when the request goes out.
    deep = (corridor + 13 - dx + cw) // 2
    if deep < 11:
        raise ValueError(f"corridor {corridor} is too short for this layout")
    n = g.pipe([(dx + 1, 5), (dx + 1, deep), (cw + 3, deep), (cw + 3, 8), (cw + 2, 8)])
    assert n == corridor, (n, corridor, deep)
    return g.rows()


def measure(rows: list[str], ticks: int = 400_000) -> dict:
    """Ticks per seek, and per part, from an exact (stride 1) heat map."""
    from randomfun2026solvers.fast_littleman import FastLittleman

    res = FastLittleman("\n".join(rows)).run("", max_ticks=ticks, profile=True, profile_stride=1)
    p = res.profile
    heat, wait = p.heat, p.wait
    # The CPU's `s` is the westmost one; the drum's fillers are all further east,
    # and it fires exactly once per taken seek — so its heat *is* the seek count.
    send = min(c for c in heat if rows[c[1]][c[0]] == "s")
    n = heat[send]
    box = lambda pred: (  # noqa: E731
        sum(v for c, v in heat.items() if pred(c)),
        sum(v for c, v in wait.items() if pred(c)),
    )
    cw = rows[0].index("+", 1)          # the CPU room's east wall, not the drum's
    cpu = lambda c: c[0] <= cw  # noqa: E731
    parts = {
        "walk": lambda c: cpu(c) and c[1] == 2 and c[0] >= 4,
        "flush": lambda c: cpu(c) and 2 <= c[0] <= 3 and 2 <= c[1] <= 5,
        "sentinel": lambda c: cpu(c) and 4 <= c[0] <= 6 and 4 <= c[1] <= 6,
        "discard": lambda c: cpu(c) and 2 <= c[0] <= 3 and 6 <= c[1] <= 9,
        "riser": lambda c: cpu(c) and c[0] == 1,
        "taken": lambda c: cpu(c) and c[1] == 1 and c[0] >= 2,
    }
    out = {"seeks": n, "ticks": res.step, "per_seek": res.step / max(1, n), "fatal": res.fatal}
    for name, pred in parts.items():
        h, w = box(pred)
        out[name] = (h / max(1, n), w / max(1, n))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--walk", type=int, nargs="+", default=[46])
    ap.add_argument("--latency", type=int, nargs="+", default=[0])
    ap.add_argument("--corridor", type=int, nargs="+", default=[49])
    ap.add_argument("--word", type=int, nargs="+", default=[6])
    ap.add_argument("--rem", type=int, default=38)
    ap.add_argument("--ticks", type=int, default=400_000)
    ap.add_argument("--dump", action="store_true", help="print the grid and stop")
    a = ap.parse_args(argv)

    if a.dump:
        print("\n".join(build(a.walk[0], a.latency[0], a.corridor[0], a.rem, a.word[0])))
        return 0

    print(f"{'walk':>5} {'lat':>5} {'corr':>5} {'word':>5} | {'t/seek':>8} {'walk':>7} "
          f"{'flush':>8} {'(blk)':>8} {'disc':>7} {'(blk)':>7}")
    base = None
    for word in a.word:
        for corridor in a.corridor:
            for latency in a.latency:
                for walk in a.walk:
                    r = measure(build(walk, latency, corridor, a.rem, word), a.ticks)
                    head = f"{walk:>5} {latency:>5} {corridor:>5} {word:>5} |"
                    if r["fatal"]:
                        print(f"{head} FATAL {r['fatal']}")
                        continue
                    if base is None:
                        base = r["per_seek"]
                    print(
                        f"{head} {r['per_seek']:>8.1f} "
                        f"{r['walk'][0]:>7.1f} {r['flush'][0]:>8.1f} {r['flush'][1]:>8.1f} "
                        f"{r['discard'][0]:>7.1f} {r['discard'][1]:>7.1f}"
                        f"   {100 * (r['per_seek'] - base) / base:+7.2f}%"
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
