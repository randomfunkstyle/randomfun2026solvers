#!/usr/bin/env python3
"""`sort-numbers` as a comparator bank on a feedback ring, fed *through* the ring.

The algorithm is :func:`randomfun2026solvers.sort_network.selection_network` --
every comparator is between the man's **carry register** and the ring head, so
like odd-even transposition it needs zero rotations, and unlike it each
comparator costs one pop and one push instead of two.

**What changed against the 17x17 incumbent.**  The input pipe no longer reaches
the worker at all: it feeds the *relay*, which merges it into the ring with `R`.
`R` takes any ready pipe and breaks ties by reading order, so wiring input to
read first makes the relay drain the round's ``n + 1`` words before it forwards
one of the worker's, and the ring comes out in exactly the order the worker's
pass invariant wants: header, then values.  That deletes MAIN *and* LOAD --
four of the worker's ten rows -- because the ring's initial contents (``n`` then
``v1..vn``) already *are* "header plus n values", the state every later pass
produces.  The machine is now one loop::

    PASS:  B = 1; k = r(ring); k -= 1
      k > 0:  BODY:  BP = k-1; header := k-1; carry = r(ring)
              COMP:  BP times { A = r(ring); A -= carry; push the loser }
              EMIT:  the min -> output; A = 1; round again
      k == 0: the ring holds one value.  Read it, emit it, and leave the ring
              **empty** -- no header, so the round boundary is a blocking `r`
              and nothing else.

Parking a 1 in **B** is what shrinks BODY's decrement from ``M1-N`` to ``-``: B
is dead at EMIT -- `W` has just moved the pass minimum into A -- so the constant
every pass needs costs one glyph instead of four.

The comparator itself is symmetric: **10 ticks whichever way the compare goes**,
because each lane carries its own loop test (`d` clockwise, `a` counter-
clockwise) instead of both walking round to a single shared one.  See
:func:`_comparator_block`.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import Circuit, E, N, S, W

__all__ = ["comparator_worker", "WORKER_IW", "WORKER_IH", "build", "boxes"]

# Worker interior.  Every pipe binds by Manhattan distance to the pipe segment
# touching the room, so the anchors -- not the code -- decide which pipe each
# `s` talks to.  Asserted against the engine's own oracle in `test_sort_grid.py`.
WORKER_IW, WORKER_IH = 9, 7


def _comparator_block(c: Circuit, bx: int, by: int) -> None:
    """One compare-exchange against the carry: a 7x3 block, **10 ticks either way**.

    ``(bx, by)`` is the block's top-left interior cell.  The body runs *west*
    along the middle row and each lane runs back *east* along its own row, so
    the block is a flattened figure-of-eight rather than a loop with a tail::

        >  >  +  s     d  v      A > 0 lane, then its own loop test
        ^  X  -  m  r  <         body, walked west out of the entry turn
           >  +  W  s  a  v      A < 0 lane, then its own loop test

    **The two loop tests are the whole trick.**  A single `d` can only be
    entered on one heading, so one lane always has to walk round to reach it --
    that is the 14-tick return round the east side this replaces, and it cost
    ~250 ticks a case.  But `d` turns clockwise on ``BP > 0`` and `a` turns
    counter-clockwise, so a lane arriving from the north gets `d` and a lane
    arriving from the south gets `a`, and both land on the same entry cell
    facing the same way.  Two glyphs, one per lane, in cells the old block was
    spending on transit anyway: the footprint is unchanged at 7x3.

    Both tests fall through **east** on ``BP == 0``, into a shared column that
    carries either one down to EMIT.

    The caller enters at the body's turn cell, one cell east of `r`, which skips
    the test -- sound only because the last pass of a round (``k == 1``, ``BP ==
    0``) never comes here at all: PASS routes it straight to EMIT.
    """
    ex = bx + 5  # the entry turn: where both tests deliver the man
    # ── body (middle row), walked west out of the entry turn ────────────────
    c.set(ex, by + 1, "<")
    c.run(ex - 1, by + 1, "rm-X", d=W)  # A = vi - carry, B = carry
    # ── A > 0 (vi > carry): push vi, carry unchanged.  `X` walked into heading
    #    **west** turns clockwise to the *north*, so this is the top row.
    c.set(bx + 1, by, ">")
    c.run(bx + 2, by, "+s")
    c.set(bx + 4, by, " ")
    c.set(ex, by, "d")  # entered heading east: BP > 0 turns CW, south, to `ex`
    # ── A < 0 (vi < carry): push carry, carry = vi.  Counter-clockwise, south.
    c.set(bx + 1, by + 2, ">")
    c.run(bx + 2, by + 2, "+Ws")
    c.set(ex, by + 2, "a")  # BP > 0 turns CCW, north, to `ex`
    # ── A == 0: a tie is correct on either lane, so it costs a turn, not a
    #    lane -- west out of X, north, and east into the A > 0 lane's head.
    c.set(bx, by + 1, "^")
    c.set(bx, by, ">")
    # ── BP == 0: both tests fall through east into the shared exit column ───
    c.set(ex + 1, by, "v")
    c.set(ex + 1, by + 1, " ")  # also BODY's westward return: a blank crossing
    c.set(ex + 1, by + 2, "v")


def comparator_worker() -> Circuit:
    """The comparator bank: one room, one man, the ring cycling through him.

    Registers through a pass: **B** carries the running minimum, **A** is
    scratch, **BP** is the pass counter.  Rows, top to bottom: the three
    comparator rows, EMIT, PASS, the last-pass lane, BODY.
    """
    c = Circuit(WORKER_IW, WORKER_IH)

    # ── the comparator loop: cols 0..6 of rows 0..2 ─────────────────────────
    _comparator_block(c, 0, 0)

    # ── EMIT (row 3, walked west): ship the pass minimum, then A = 1 ───────
    # `s` sits at column 2 because that is what binds it to the *output* pipe.
    # Nearest-pipe is Manhattan distance to the segment touching the room, and
    # from column 4 the ring's forward pipe would be nearer -- the emit would
    # go back into the ring, silently. `test_sort_grid` asks the engine's own
    # `route` oracle rather than trusting that arithmetic.
    c.set(6, 3, "<")
    c.blanks(5, 3, 2, d=W)
    c.run(3, 3, "Ws1", d=W)  # A = min, out, then A = 1 for BODY's `-`
    c.set(0, 3, "v")

    # ── PASS (row 4): B := 1, read the header, branch on k-1 ───────────────
    # The last pass of a round has k == 1 and must push **no** new header: a
    # header left in the ring is a word the next round has to step over, and at
    # a round boundary the relay is already refilling from input, so that word
    # arrives *behind* the new list and desynchronises everything after it.
    # Ending with the ring genuinely empty is what makes the round boundary a
    # blocking `r` and nothing else -- there is no round-over branch at all.
    c.set(0, 4, ">")
    c.run(1, 4, "Mr-", d=E)  # B = 1, A = k, A = k-1
    c.set(4, 4, "X")  # k-1 > 0 -> south (a pass); k == 1 -> east (the last one)

    # ── k == 1 (row 5, walked west): emit the survivor, then go round again ─
    # It bypasses the comparator entirely rather than falling through it zero
    # times, which is what lets the comparator's entry skip its loop test.
    c.set(5, 4, "v")
    c.set(5, 5, "<")
    c.set(4, 5, " ")  # BODY drops south through this cell: a blank crossing
    c.run(3, 5, "rs1", d=W)  # A = survivor, out, A = 1 for the next PASS
    c.set(0, 5, "^")

    # ── BODY (row 6): BP = k-1, push the header, load the carry ────────────
    c.set(4, 6, ">")
    c.run(5, 6, "bsr")  # BP = k-1, header := k-1, A = v1
    c.set(8, 6, "^")
    c.run(8, 5, "M", d=N)  # carry = v1, on the way back up to the loop
    c.blanks(8, 4, 3, d=N)
    c.set(8, 1, "<")
    c.blanks(7, 1, 1, d=W)  # west into the comparator's entry turn at (5, 1)

    # ── spawn: park a 1 in A, then double back into PASS ───────────────────
    # `@` is a nop, so the man leaves it on the heading he arrived with and the
    # walk has to turn him round; `1` is direction-neutral, so it is free to
    # stand on the one cell he crosses twice.
    c.set(1, 6, "@")
    c.set(2, 6, "1")
    c.set(3, 6, "<")
    c.set(0, 6, "^")
    return c


# The relay closes the ring *and* merges the input stream into it.  `R` takes a
# value from any incoming pipe that has one ready and breaks ties by reading
# order, so both incoming pipes land on the **west** wall and the input's
# segment sits two rows above the worker's: input wins every tick it has a
# value, which is every tick until the round's `n + 1` words are gone.
#
# `@` is a nop, so the man leaves it on the heading he arrived with -- it can
# only stand on a cell the walk passes straight through, which is why the loop
# needs a 4x3 interior rather than the 3x2 a `U`-relay gets away with.  The
# third row is empty; it exists so the west wall has a *third* attach cell, one
# clear of the corner, for the forward pipe.
RELAY = ["+----+", "|vs <|", "|>@R^|", "|    |", "+----+"]

WX, WY = 1, 6  # worker interior origin
RING_CAPACITY_NEEDED = 18  # 17 words can be alive at once; one spare slot means
# "fwd full AND ret full" needs 18 words to exist, so `s` can never deadlock


def build() -> list[str]:
    """The whole machine: worker + relay + I/O rooms + four pipes."""
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    g = Circuit(14, 14)

    # worker: outer cols 0..10, rows 5..13
    stamp(g, WX, WY, comparator_worker().rows())
    walls(g, WX, WY, WORKER_IW, WORKER_IH)

    # O room (cols 0..2), I room (cols 3..5), relay (cols 8..13) in the band
    stamp(g, 0, 0, ["+-+", "|O|", "+-+"])
    stamp(g, 3, 0, ["+-+", "|I|", "+-+"])
    stamp(g, 8, 0, RELAY)

    draw_pipe(g, [(1, 4), (1, 3)])                 # out: worker -> O
    draw_pipe(g, [(6, 1), (7, 1)])                 # input: I -> relay, row 1
    n_fwd = draw_pipe(g, [(6, 4), (6, 3), (7, 3)])  # fwd: worker -> relay, row 3
    # ret: out of the relay's south wall and the long way round the east edge,
    # into the worker's east wall.  Column 11 carries only the terminal `<`:
    # any arrowhead there would sit with the worker's east wall behind it and
    # mint a phantom *outgoing* pipe, which loads clean and computes nonsense.
    n_ret = draw_pipe(g, [(12, 5), (12, 6), (13, 6), (13, 13), (12, 13),
                          (12, 8), (11, 8)])
    if n_fwd + n_ret < RING_CAPACITY_NEEDED:
        raise ValueError(f"ring holds {n_fwd + n_ret}, need {RING_CAPACITY_NEEDED}")
    return [r.rstrip() for r in g.rows()]


def boxes() -> list[tuple[int, int, int, int]]:
    """Every room as (x, y, w, h) -- what `check_no_phantom_pipes` needs."""
    return [(0, 0, 3, 3), (3, 0, 3, 3), (8, 0, 6, 5),
            (WX - 1, WY - 1, WORKER_IW + 2, WORKER_IH + 2)]


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="sort_grid", description=__doc__)
    ap.add_argument("--man", type=Path, help="write the grid")
    args = ap.parse_args(argv)

    rows = build()
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    else:
        print("\n".join(rows))
    width = max(map(len, rows))
    print(f"{width}x{len(rows)}  area2={max(width, len(rows)) ** 2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
