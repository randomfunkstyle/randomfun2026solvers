"""How close can two LM-75 panels stand, and what sets the floor?

``deadman-3d_hires``'s four 64x48 panels sit in a 2x2 with **177** free columns
and **59** free rows between them.  Neither number is a panel property: each
panel is embedded in a 235x101 DOOM block whose logic fills the 169 columns west
of it, and ``d3_router.build_wall`` places whole blocks.  Before rebuilding that
wall it is worth knowing what the *panels themselves* allow, and that is a
question about ``SPEC.md``'s pipe rules:

    ADDR attaches on the top wall, DATA on the left, SWAP on the bottom;
    two pipes on one side, a pipe on the right side, or a pipe at a corner
    are all load errors, and pipes may not originate at a display.

So: panels driven by raw feeder rooms — no DOOM unit at all, because a panel is
controlled entirely by pipes — at a shrinking gap, handed to the real engine
(``lm.mjs``).  A gap counts as working when the grid **loads** (which is what
rules out a pipe binding to the wrong side, the wrong panel or a corner) and the
grid **runs** with every panel holding the colour that was fed to *it*, which is
what rules out a pipe not binding at all.

    python scratch/deadman3d-opt/panel_pack.py

What it answers
---------------

**Pairwise: one free cell on either axis, never zero.**  Side by side, the east
panel's DATA arrowhead has to sit immediately west of its left wall and point
east; at gap 0 that cell *is* the west panel's right wall, so gap 0 is not a load
error, it is not drawable.  At gap 1 the single corridor column works, but only
if the terminal is a **bend** — the pipe descends the corridor and turns east in
the very cell it ends on, because there is no cell west of it to arrive from.
Stacked, gap 0 leaves no row for either the upper SWAP's arrowhead or the lower
ADDR's; gap 1 works, with both terminals bends in the one shared row, entering
from opposite ends.

**A 2x2 is not the pairwise answers composed: it is 1 column x 2 rows.**  The
band between the two panel rows must carry four arrowheads — two SWAPs pointing
north, in its FIRST row, and two ADDRs pointing south, in its LAST.  A band row
can only be *entered* from its west or east end, because above the band is a
display and below it is a display, so one row carries at most two of them.  Four
terminals, two per row: the band is two rows, and no assignment of columns saves
``gy = 1`` (the sweep shows it as a collision, not a load error — the two
west-entering pipes want the same cells).  The corridor is the opposite story and
stays at one column: the east panels' DATA arrowheads sit at rows *inside* their
own panels, the upper one reached from above and the lower one from below, so
their row ranges are disjoint and one column serves both — and the band's two
horizontal runs cross that column at rows neither DATA pipe reaches.

    1 x 1  collision      1 x 2  loads, 4 displays, 133x102, runs
    2 x 1  collision      2 x 2  loads, 4 displays, 134x102, runs
    3 x 1  collision      1 x 3  loads, 4 displays, 133x103, runs

So the panel block's floor is **133x102** against ``build_wall``'s current
309x159 — 176 columns and 57 rows of pure spacing.  It is not free: the four
DOOM blocks' logic (169 columns west of each panel today) has to go somewhere
else, and the twelve pipes have to reach a cluster instead of four separate
neighbourhoods.  This file is the proof that the target is reachable, not the
rebuild.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1.machine import _Grid  # noqa: E402

PW, PH = 64, 48          # the panel interior; 66x50 with its walls
LM = REPO / "littleman" / "lm.mjs"
PX, PY = 40, 20          # panel 0's north-west wall corner


def feeder(g: _Grid, x: int, y: int, words: list[int]) -> tuple[int, int]:
    """A room that recites ``words`` into its one pipe and halts."""
    cells: dict[tuple[int, int], str] = {(1, 1): "@", (2, 1): "v"}
    row = 2
    for w in words:
        for ch in f"`{w}`s":
            cells[(2, row)] = ch
            row += 1
    cells[(2, row)] = "H"
    g.room(x, y, x + 3, y + row + 1)
    g.blit(x, y, cells)
    return x + 4, y + 1


def bend_end(g: _Grid, points: list[tuple[int, int]], glyph: str) -> None:
    """A pipe whose LAST cell is a bend, not a straight arrival.

    ``draw_pipe`` reads the terminal glyph off the direction it arrived from,
    which is right for a run into a wall and wrong for the one case this file
    exists to test: a DATA pipe that has to descend a **one-column** corridor
    and turn east in the very cell it ends on, because the column west of it is
    the neighbouring panel's own wall.
    """
    g.draw_pipe(points)
    g.c[points[-1]] = glyph


def side_by_side(gap: int, *, bend: bool) -> list[str]:
    """Two panels on one row, ``gap`` free columns between their facing walls."""
    g = _Grid()
    x0, y0 = PX, PY
    x1, y1 = PX + PW + 2 + gap, PY
    for cx, cy in ((x0, y0), (x1, y1)):
        g.room(cx, cy, cx + PW + 1, cy + PH + 1, h="=", v=":")

    # Three feeders stepped down-and-east so their exit rows are 1, 3, 5 and
    # none of their rooms is under another's run. The pipe that descends
    # furthest east turns on the SHALLOWEST row, which is what keeps the three
    # descents from crossing each other's horizontal runs.
    a1 = feeder(g, 0, 0, [0])          # exit (4, 1)  -> panel 1 ADDR, col x1+5
    d1 = feeder(g, 6, 2, [9])          # exit (10, 3) -> panel 1 DATA, corridor
    a0 = feeder(g, 12, 4, [0])         # exit (16, 5) -> panel 0 ADDR, col x0+3
    g.draw_pipe([a1, (x1 + 5, 1), (x1 + 5, y1 - 1)])
    g.draw_pipe([a0, (x0 + 3, 5), (x0 + 3, y0 - 1)])

    # Panel 0's DATA has open ground west of it: straight east into the wall.
    d0 = feeder(g, 0, 30, [5])         # exit (4, 31)
    g.draw_pipe([d0, (x0 - 1, 31)])

    # Panel 1's DATA is the horizontal gap's whole story. Its terminal cell must
    # sit immediately west of its left wall and point EAST, and that cell is in
    # the corridor; the only way in is down the corridor, because west of the
    # corridor is panel 0.
    corr = x1 - 1
    if bend:
        bend_end(g, [d1, (corr, 3), (corr, y1 + 8)], ">")
    else:
        g.draw_pipe([d1, (corr - 1, 3), (corr - 1, y1 + 8), (corr, y1 + 8)])

    # SWAP: under the panel and north into the bottom wall; the easterly one
    # runs on the deeper row for the same non-crossing reason.
    s0 = feeder(g, 0, 76, [0])         # exit (4, 77)
    s1 = feeder(g, 0, 86, [0])         # exit (4, 87)
    g.draw_pipe([s0, (x0 + 20, 77), (x0 + 20, y0 + PH + 2)])
    g.draw_pipe([s1, (x1 + 24, 87), (x1 + 24, y1 + PH + 2)])
    return g.rows()


def stacked(gap: int) -> list[str]:
    """Two panels in one column, ``gap`` free rows between their facing walls.

    The gap band has to hold **both** the upper panel's SWAP arrowhead (pointing
    north into its bottom wall) and the lower panel's ADDR arrowhead (pointing
    south into its top wall), *and* the run that feeds each of them — and every
    approach has to come along the band, because above it is one panel and below
    it is the other.  At gap 1 that is one row doing both jobs, possible only
    because the two can take disjoint column ranges of it: ADDR comes in from the
    west and turns south first, SWAP comes in from the east and turns north
    further on.  Both terminals are then *bends*, not arrivals.
    """
    g = _Grid()
    x0, y0 = PX, PY
    y1 = PY + PH + 2 + gap
    for cy in (y0, y1):
        g.room(x0, cy, x0 + PW + 1, cy + PH + 1, h="=", v=":")
    band = y0 + PH + 2                 # the first free row under panel 0
    a_col, s_col = x0 + 10, x0 + 40    # ADDR turns west of where SWAP rises

    a0 = feeder(g, 0, 0, [0])
    g.draw_pipe([a0, (x0 + 3, 1), (x0 + 3, y0 - 1)])
    # the DATA feeders sit east of ADDR's descent column so their runs into the
    # left wall never cross it
    d0 = feeder(g, 24, 30, [5])
    g.draw_pipe([d0, (x0 - 1, 31)])
    d1 = feeder(g, 24, y1 + 10, [9])
    g.draw_pipe([d1, (x0 - 1, y1 + 11)])

    # panel 1's ADDR: down a column west of the panels to the band, east along
    # it, then south into the top wall.  At gap 1 that last leg is zero cells
    # long and the terminal is the bend itself.
    a1 = feeder(g, 0, 10, [0])
    if y1 - 1 == band:
        bend_end(g, [a1, (20, 11), (20, band), (a_col, band)], "v")
    else:
        g.draw_pipe([a1, (20, 11), (20, band), (a_col, band), (a_col, y1 - 1)])
    # panel 0's SWAP: down a column EAST of the panels, west along the band's
    # last row, then north into the bottom wall — same bend at gap 1.
    s0 = feeder(g, x0 + PW + 20, 0, [0])
    down = s0[0] + 1                   # step east off the wall, then descend
    last = y1 - 2                      # the band's own last row
    if last <= band:                   # gap 1: one row, and it does both jobs
        bend_end(g, [s0, (down, s0[1]), (down, band), (s_col, band)], "^")
    else:
        bend_end(g, [s0, (down, s0[1]), (down, last), (s_col, last),
                     (s_col, band)], "^")
    s1 = feeder(g, 0, y1 + PH + 6, [0])
    g.draw_pipe([s1, (x0 + 20, y1 + PH + 7), (x0 + 20, y1 + PH + 2)])
    return g.rows()


def quad(gx: int, gy: int) -> list[str]:
    """The real question: a 2x2 with ``gx`` free columns and ``gy`` free rows.

    The pairwise answers do not compose, and the reason is a counting argument
    about the band between the two panel rows.  Everything that band has to
    carry terminates in it: the two upper panels' SWAP arrowheads must sit in
    the band's FIRST row (they point north into a bottom wall) and the two lower
    panels' ADDR arrowheads in its LAST row (they point south into a top wall).
    A pipe can only *enter* a band row from its west or east end, because above
    the band is a display and below it is a display — so one row carries at most
    two of these.  Four terminals, two per row: the band is two rows, and
    ``gy = 1`` cannot be made to work however the columns are assigned.

    The corridor is the opposite: the right-hand panels' DATA arrowheads sit in
    it at rows *inside* their own panels — the upper one reached from above, the
    lower one from below — so their row ranges are disjoint and **one** column
    serves both.  The band's two horizontal runs cross that column at rows the
    DATA pipes never reach.
    """
    g = _Grid()
    x0, y0 = PX, PY
    x1 = x0 + PW + 2 + gx
    y1 = y0 + PH + 2 + gy
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        g.room(cx, cy, cx + PW + 1, cy + PH + 1, h="=", v=":")
    band, band_end = y0 + PH + 2, y1 - 1     # the band's first and last rows
    corr = x1 - 1                            # the corridor's easternmost column
    west, east = 20, x1 + PW + 12            # the two vertical trunk columns
    bottom = y1 + PH + 8                     # clear of everything, to the south

    # ── ADDR for the two TOP panels, and DATA for the top-right one: three
    #    descents, and the one that reaches furthest east turns on the
    #    shallowest row, which is what keeps them from crossing ─────────────
    a1 = feeder(g, 0, 0, [0])                # exit row 1  -> col x1 + 5
    g.draw_pipe([a1, (x1 + 5, a1[1]), (x1 + 5, y0 - 1)])
    d1 = feeder(g, 6, 4, [9])                # exit row 5  -> the corridor
    bend_end(g, [d1, (corr, d1[1]), (corr, y0 + 8)], ">")
    a0 = feeder(g, 12, 8, [0])               # exit row 9  -> col x0 + 3
    g.draw_pipe([a0, (x0 + 3, a0[1]), (x0 + 3, y0 - 1)])

    # ── DATA for the two WEST panels: open ground, straight in ──────────────
    d0 = feeder(g, 26, 30, [5])
    g.draw_pipe([d0, (x0 - 1, 31)])
    d2 = feeder(g, 26, y1 + 20, [6])
    g.draw_pipe([d2, (x0 - 1, y1 + 21)])

    # ── the band: both SWAP arrowheads in its FIRST row and both ADDR
    #    arrowheads in its LAST, each pair entering from opposite ends,
    #    because a band row can only be entered from outside the panels ─────
    s0 = feeder(g, 0, 40, [0])               # west end -> top-left SWAP
    bend_end(g, [s0, (west, s0[1]), (west, band), (x0 + 12, band)], "^")
    s1 = feeder(g, east - 6, 20, [0])        # east end -> top-right SWAP
    bend_end(g, [s1, (east, s1[1]), (east, band), (x1 + 12, band)], "^")
    a2 = feeder(g, 0, 48, [0])               # west end -> bottom-left ADDR
    bend_end(g, [a2, (west - 2, a2[1]), (west - 2, band_end), (x0 + 40, band_end)],
             "v")
    a3 = feeder(g, east - 6, 12, [0])        # east end -> bottom-right ADDR
    bend_end(g, [a3, (east + 2, a3[1]), (east + 2, band_end), (x1 + 40, band_end)],
             "v")

    # ── from below: the two bottom SWAPs and the bottom-right DATA, which
    #    comes UP the corridor — the same column the top-right DATA came DOWN,
    #    at rows it never reaches ──────────────────────────────────────────
    s2 = feeder(g, 0, bottom + 4, [0])       # westernmost riser, shallowest row
    g.draw_pipe([s2, (x0 + 20, bottom + 5), (x0 + 20, y1 + PH + 2)])
    d3 = feeder(g, 0, bottom + 16, [10])
    bend_end(g, [d3, (corr, bottom + 17), (corr, y1 + 8)], ">")
    s3 = feeder(g, 0, bottom + 28, [0])
    g.draw_pipe([s3, (x1 + 24, bottom + 29), (x1 + 24, y1 + PH + 2)])
    return g.rows()


def analyze(rows: list[str]) -> dict:
    tmp = Path("/tmp/panel_pack_probe.man")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = subprocess.run(  # noqa: S603
        ["node", str(LM), "analyze", str(tmp)],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return {"error": ((out.stderr or out.stdout).strip().splitlines() or [""])[-1]}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"error": out.stdout.strip()[:200]}


def painted(rows: list[str]) -> str:
    """Run it, and say which colour each panel is actually holding.

    A clean run already proves every pipe bound (an unbound ``s`` is a ``no-pipe``
    fatal), and the colours prove each one bound to **its own** panel.  Front or
    back buffer is not the point: the corridor DATA pipes are longer than the
    SWAP pipes that commit them, so a panel fed down the corridor commits an
    empty frame first and holds its colour in the next one.  That is pipe
    *latency*, which a real driver interleaves away, not a binding question.
    """
    tmp = Path("/tmp/panel_pack_probe.man")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = subprocess.run(  # noqa: S603
        ["node", str(LM), "tick", str(tmp), "20000", "--json"],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        return f"RUN ERROR {err[-1:]}"
    got = []
    for dp in json.loads(out.stdout)["entities"]["displays"]:
        seen: set[int] = set()
        for buf in ("front", "back"):
            for row in dp.get(buf) or []:
                seen |= {c for c in (row if isinstance(row, list) else [row]) if c}
        got.append(sorted(seen))
    return f"holds {got}"


def sweep() -> None:
    print("── side by side: free COLUMNS between the facing walls ──")
    for gap in range(0, 5):
        for bend in (False, True):
            tag = f"  gap {gap}{' corridor-bend' if bend else ''}"
            try:
                rows = side_by_side(gap, bend=bend)
            except Exception as exc:  # noqa: BLE001 — a collision IS an answer
                print(f"{tag}: grid collision — {exc}")
                continue
            info = analyze(rows)
            if "error" in info:
                print(f"{tag}: LOAD ERROR — {info['error']}")
                continue
            print(f"{tag}: loads, {len(info.get('displays') or [])} displays, "
                  f"{max(len(r) for r in rows)}x{len(rows)}; {painted(rows)}")

    print("── stacked: free ROWS between the facing walls ──")
    for gap in range(0, 5):
        tag = f"  gap {gap}"
        try:
            rows = stacked(gap)
        except Exception as exc:  # noqa: BLE001
            print(f"{tag}: grid collision — {exc}")
            continue
        info = analyze(rows)
        if "error" in info:
            print(f"{tag}: LOAD ERROR — {info['error']}")
            continue
        print(f"{tag}: loads, {len(info.get('displays') or [])} displays, "
              f"{max(len(r) for r in rows)}x{len(rows)}; {painted(rows)}")


def sweep_quad() -> None:
    print("── the 2x2: free COLUMNS x free ROWS ──")
    for gy in range(1, 4):
        for gx in range(1, 4):
            tag = f"  {gx} x {gy}"
            try:
                rows = quad(gx, gy)
            except Exception as exc:  # noqa: BLE001
                print(f"{tag}: grid collision — {exc}")
                continue
            info = analyze(rows)
            if "error" in info:
                print(f"{tag}: LOAD ERROR — {info['error']}")
                continue
            block = (PW + 2) * 2 + gx, (PH + 2) * 2 + gy
            print(f"{tag}: loads, {len(info.get('displays') or [])} displays, "
                  f"panel block {block[0]}x{block[1]}; {painted(rows)}")


if __name__ == "__main__":
    sweep()
    sweep_quad()
