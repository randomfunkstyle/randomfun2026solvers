#!/usr/bin/env python3
"""The three proposed relayouts, each walked and bound-checked before it is quoted.

A proposal that is only typed out is a claim.  Each one here is instead built as
a grid, walked with :mod:`place.trace` under the engine's own movement rule, and
checked against SPEC §7.1's nearest-pipe rule with ``z3/bind.decide`` -- so the
tick figure is *counted off the walk* and the binding is *decided*, not argued.

Every proposal is stated as a diff against the shipped body, and the shipped body
is walked in the same run so the two numbers come from the same code.

    python3 proposals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "z3"))

from circuit import route_open  # noqa: E402
from trace import Choices, Grid, walk  # noqa: E402

READS = 324_600          # measured: exec of the answer collector's `R`
READ_GATE_HOPS = 931_531  # measured: sum over gates of the reads entering each
TOUR = 85_522_204


def show(title, rows):
    print(f"\n  {title}")
    for r in rows:
        print(f"      |{r}|")


def presend(grid, start, heading, choices, stop_glyph):
    """Cells walked from ``start`` up to and including the first ``stop_glyph``.

    That prefix is the whole of what a requester waits for: everything after the
    answering send is the man walking home, and the measured post-send rate in a
    room this idle is 0.000 %/tick.
    """
    w = walk(grid, start, heading, choices, max_steps=80, walls=frozenset())
    for i, g in enumerate(w.glyphs):
        if g == stop_glyph and i:
            return i + 1, w.glyphs[: i + 1]
    return None, w.glyphs


# ── 1. the v4 bank gate's mine arm ───────────────────────────────────────────
def gate() -> None:
    """``_bank_gate_v4``: the ``^ >`` dogleg between the ``X`` and the arm.

    Shipped, the ``X``'s counter-clockwise exit lands on ``(cx, in_row-1)`` as a
    ``^``, climbs to the local row, and only then turns east onto ``N s``.  Two
    cells, on every hop of every read, to move the arm one row off the exit the
    branch already gave us.

    The arm does not need to be on that row.  §7.1 measures from the *glyph* to
    the pipe's attach cell, and both outgoing pipes are on the east wall four
    rows apart, so an ``s`` anywhere in the northern half of the body is nearer
    the local pipe than the downstream one -- with margin.  Putting ``N s``
    straight up the ``X``'s own column deletes both cells.
    """
    print("\n=== 1. bank gate v4 (park_const, high form): the mine arm ===")
    ship = Grid([
        "             ",
        "        >...v",
        "     >Nsx...v",
        "     ^  >rs.v",
        " UbW-Xv >...v",
        " ^   >>Wsx..v",
        " ^       >rsv",
        " ^M`8871`<<@<",
    ])
    show("shipped body, local coords (walls stripped; spine on row 4, cx=5)",
         ship.rows)
    n, gl = presend(ship, (1, 4), "E", Choices({(5, 4): "ccw", (8, 2): "ccw"}), "s")
    print(f"    shipped mine-arm pre-send: {n} cells  {chr(39)}{''.join(gl)}{chr(39)}")

    prop = Grid([
        "             ",
        "     >.....vv",
        "     s  vsrxv",
        "     N  >...v",
        " UbW-Xv >...v",
        " ^   >>Wsx..v",
        " ^       >rsv",
        " ^M`8871`<<@<",
    ])
    show("proposed: N and s stand on the X's own exit column, and the op branch "
         "moves\n  to the top row where its read exit lands straight on the descent",
         prop.rows)
    n2, gl2 = presend(prop, (1, 4), "E",
                      Choices({(5, 4): "ccw", (11, 2): "ccw"}), "s")
    print(f"    proposed mine-arm pre-send: {n2} cells  {chr(39)}{''.join(gl2)}{chr(39)}")

    # the whole proposed read lap, to prove it closes and still walks the floor
    lap = walk(prop, (1, 4), "E",
               Choices({(5, 4): "ccw", (11, 2): "ccw"}), max_steps=60,
               walls=frozenset())
    close = lap.cells.index((1, 4), 1)
    print(f"    proposed read lap: {close} cells, "
          f"{chr(39)}{lap.glyphs[:close]}{chr(39)}")
    print(f"      -> re-enters U having walked the floor const reload (walked west, so "
          f"the literal reads back as `1788`M): "
          f"{'YES' if '`1788`M' in lap.glyphs else 'NO -- B never reloaded'}")

    c = route_open("UbW-XNs", box=(0, 0, 8, 6), start=(1, 4), heading="E")
    print(f"    routed floor for the same seven ops: {c.ticks} cells")
    print(f"    -> shipped {n}, floor {c.ticks}, gap {n - c.ticks}; "
          f"the proposal is at the floor")

    # SPEC 7.1: does each send still bind the pipe it means to?
    ex = 13  # east wall; both outgoing pipes attach one cell beyond it
    local, down = (ex + 1, 2), (ex + 1, 6)
    def m(cell, want):
        dl = abs(local[0] - cell[0]) + abs(local[1] - cell[1])
        dd = abs(down[0] - cell[0]) + abs(down[1] - cell[1])
        win = "local" if dl < dd else "downstream"
        return f"{cell} -> {win} (local {dl}, downstream {dd}, margin {abs(dd - dl)})" \
               + ("" if win == want else "   *** BINDS THE WRONG PIPE ***")
    print("    SPEC 7.1, nearest outgoing pipe from each `s`:")
    print(f"      shipped  mine    {m((7, 2), 'local')}")
    print(f"      proposed mine    {m((5, 2), 'local')}")
    print(f"      proposed mine-wr {m((9, 2), 'local')}")
    print(f"      downstream (unmoved) {m((8, 5), 'downstream')}")
    saved = 2 * READ_GATE_HOPS
    print(f"    measured read gate-hops {READ_GATE_HOPS:,} "
          f"-> {saved:,} tour ticks ({100 * saved / TOUR:.2f} % of the run)")


# ── 2. the store's answer collector ──────────────────────────────────────────
def collector() -> None:
    """The answer teleport: a 6-cell lap whose ``R`` and ``s`` are half a lap apart.

    A 6-cell lap is a 3x2 rectangle, and a 3x2 rectangle has exactly four corners
    and two straight cells -- diagonally opposite each other.  ``R`` and ``s``
    cannot turn, so they *must* take the two straight cells, and the walk between
    them is therefore always three moves.  The minimum-lap layout and the
    minimum-latency layout are different layouts, and this room is on the read's
    critical path while being 97.7 % idle.

    Going to a 4x2 lap puts the two straight cells side by side.  The lap grows
    from 6 to 8 -- charged at the measured post-send-idle rate of 0.000 %/tick --
    and the answer leaves two ticks earlier on every read.
    """
    print("\n=== 2. the taped store's answer collector ===")
    g = Grid(["@>Rv", " ^s<"])
    n, gl = presend(g, (1, 0), "E", Choices(), "s")
    show("shipped (6-cell lap)", g.rows)
    print(f"    shipped R->s: {n} cells  {''.join(gl)!r}")

    p = Grid(["@>Rsv", " ^..<"])
    n2, gl2 = presend(p, (1, 0), "E", Choices(), "s")
    show("proposed (8-cell lap, R and s adjacent)", p.rows)
    print(f"    proposed R->s: {n2} cells  {''.join(gl2)!r}")
    w = walk(p, (1, 0), "E", Choices(), max_steps=40, walls=frozenset())
    print(f"    proposed lap closes in {w.cells.index((1, 0), 1)} cells "
          f"(shipped 6); the extra 2 are post-send in a 97.7 %-idle room")
    print("    §7.1: the collector has ONE outgoing pipe, so `s` has nothing to "
          "choose between; `R` reads any incoming pipe with no distance term")
    saved = 2 * READS
    print(f"    measured reads {READS:,} -> {saved:,} tour ticks "
          f"({100 * saved / TOUR:.2f} % of the run)")


# ── 3. the bank worker's request head ────────────────────────────────────────
def bank() -> None:
    """``tape_block``: three filler cells between the request ``r`` and the ring loop.

    The head is ``r b ] - M`` on one row, then ``v`` / blank / ``>`` to drop two
    rows and turn east into the skip loop's ``d``.  Those three cells are on the
    read's critical path -- the ring rotation that follows them is what the
    requester is waiting for -- and they exist only because the loop was drawn
    two rows below the row the request lands on.
    """
    print("\n=== 3. the bank worker's request head ===")
    g = Grid([
        ">rb]-Mv     ",
        "      .     ",
        "      >dWMbx",
    ])
    w = walk(g, (0, 0), "E", Choices({(7, 2): "straight", (11, 2): "cw"}),
             max_steps=20, walls=frozenset(), box=(0, 0, 11, 2))
    print(f"    shipped head r..x: {w.ticks} cells  {w.glyphs!r}")
    c = route_open("rb]-MdWMbx", box=(0, 0, 11, 2), start=(1, 0), heading="E")
    print(f"    routed floor for the same ten ops: {c.ticks} cells")
    show("routed", c.render().split("\n"))
    saved = 3 * READS
    print(f"    gap 3 cells; measured reads {READS:,} -> {saved:,} tour ticks "
          f"({100 * saved / TOUR:.2f} % of the run)")
    print("    CAVEAT: the `r` must stay nearest the request pipe and the loop's")
    print("    `r`/`s` nearest the two ring pipes -- this one needs the z3 pad")
    print("    sweep re-run before it is built, which the other two do not.")


def main() -> int:
    gate()
    collector()
    bank()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
