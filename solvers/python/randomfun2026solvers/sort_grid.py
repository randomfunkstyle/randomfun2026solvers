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

    PASS:  k = r(ring); k == 0 ends the round (and re-reads for the next one)
    BODY:  A = k-1 (B is 1, parked there by EMIT); BP = k-1; header := k-1;
           carry = r(ring)
    COMP:  BP times { A = r(ring); A -= carry; push the loser; keep the min }
    EMIT:  the min -> output; then A = 1, and PASS's `M` parks it in B

Parking a 1 in **B** is what shrinks BODY from ``M1-Nbsr`` to ``-bsr``: B is
dead at EMIT -- `W` has just moved the pass minimum into A -- so the decrement
every pass needs costs one glyph instead of four.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import Circuit, E, N, S, W

__all__ = ["comparator_worker", "WORKER_IW", "WORKER_IH", "build", "boxes"]

# Worker interior.  Every pipe binds by Manhattan distance to the pipe segment
# touching the room, so the anchors -- not the code -- decide which pipe each
# `s` talks to.  Asserted cell by cell in `tests/test_sort_network.py`.
WORKER_IW, WORKER_IH = 9, 7


def _comparator_block(c: Circuit, dx: int, dy: int) -> None:
    """One compare-exchange against the carry, as a 7x3 cycle around ``d``.

    ``d`` is entered heading **south**, so ``BP > 0`` turns clockwise into the
    westward body and ``BP == 0`` falls straight through to the emit lane.  The
    three ``X`` lanes are all correct for a tie, so the ``A == 0`` exit is free
    to merge into the ``A > 0`` one instead of needing a lane of its own.
    """
    c.set(dx, dy, "d")
    c.run(dx - 1, dy, "rm-X", d=W)  # A = vi - carry, B = carry
    # A > 0 (vi > carry): push vi, carry unchanged -- the 10-tick path.
    c.set(dx - 4, dy - 1, ">")
    c.run(dx - 3, dy - 1, "+s")
    c.set(dx - 1, dy - 1, " ")
    c.set(dx, dy - 1, "v")
    # A == 0: straight west, up and back along the A > 0 lane.
    c.set(dx - 5, dy, "^")
    c.set(dx - 5, dy - 1, ">")
    # A < 0 (vi < carry): push carry, carry = vi; returns round the east side.
    c.set(dx - 4, dy + 1, ">")
    c.run(dx - 3, dy + 1, "+Ws")
    c.set(dx, dy + 1, " ")
    c.set(dx + 1, dy + 1, "^")
    c.set(dx + 1, dy, " ")
    c.set(dx + 1, dy - 1, "<")


def comparator_worker() -> Circuit:
    """The comparator bank: one room, one man, the ring cycling through him.

    Registers through a pass: **B** carries the running minimum, **A** is
    scratch, **BP** is the pass counter.  Rows, top to bottom: the three
    comparator rows, EMIT, PASS, the round-over return, BODY.
    """
    c = Circuit(WORKER_IW, WORKER_IH)

    # ── the comparator loop, cols 2..8 of rows 0..2 ─────────────────────────
    _comparator_block(c, 7, 1)

    # ── EMIT (row 3): B holds the pass minimum; ship it, then A = 1 ────────
    c.set(7, 3, "<")
    c.blanks(6, 3, 3, d=W)
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
    # k == 1: no header, just take the survivor as the carry and fall through
    # the comparator zero times.  BP is already 0 -- the previous pass counted
    # it down, and it starts there.
    c.set(5, 4, " ")
    c.run(6, 4, "rM")
    c.set(8, 4, "^")
    c.set(8, 3, " ")

    # ── BODY (row 6): BP = k-1, push the header, load the carry ────────────
    c.set(4, 5, "v")
    c.set(4, 6, ">")
    c.run(5, 6, "bsr")  # BP = k-1, header := k-1, A = v1
    c.set(8, 6, "^")
    c.run(8, 5, "M", d=N)  # carry = v1, on the way back up to the loop

    # ── spawn: the man starts north-west and drops into PASS ───────────────
    # `1` here is what PASS's `M` parks in B on the very first pass; every
    # later pass gets it from EMIT instead.
    c.set(0, 0, "@")
    c.set(1, 0, "v")
    c.set(1, 1, "1")
    c.set(1, 2, "<")
    c.set(0, 2, "v")
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
