#!/usr/bin/env python3
"""`tcp` (packet reassembly) as a dataflow ring machine -- no CPU, no tape.

The CPU build (`tasks/solutions/tcp_cpu.man`, 111x78, 1.138e9) spends ~80% of its
ticks in the LM-1 tape implementing a 52-cell buffer addressed by `seq`.  The
problem does not need addressed memory:

* `tcp.json`'s rule is "``seq >= want + 16`` -> emit ``-1`` and stop", so an
  arrival always lands at ``d = seq - want`` in ``0..15``.  **Sixteen slots
  suffice**, indexed by *phase* rather than by address -- and a phase index is a
  rotation, which is what a pipe ring does natively.
* ``1 <= val <= 999``, so **``val = 0`` is a free empty-slot sentinel** and no
  occupancy bitmap is needed.

So the machine is a 17-word ring walked by one man:

    ring = [ HDR, w[0], w[1], ..., w[15] ]        HDR = -want

``w[j]`` holds the value awaiting sequence number ``want + j`` (0 = still
missing), and the head of the ring is ``HDR``.  Every packet consumes **exactly
17 reads and 17 pushes**, so the header comes back to the head aligned -- the
same "count in the ring as a header word" trick ``value_ring.build_sort`` uses
for its selection-sort counter, except here the header carries ``want``.

Storing the header *negated* is what makes the arithmetic fit in two registers:

    r(ring) -> A = -want ; M -> B = -want ; r(input) -> A = seq ; + -> A = d

one glyph per step, and ``B`` (the only register that survives a receive) still
holds the header for the update.  The over-window test is ``b`` + ``]]]]`` +
``d``: ``BP = d >> 4`` is non-zero exactly when ``d >= 16``, and ``]``/``d``
touch neither hand, so ``want`` is never spilled.

Three lanes leave the head block:

    d >= 16   FAIL   emit -1, halt
    d == 0    DRAIN  emit val, then drain the window while it is non-empty
    d >= 1    STORE  rotate d, overwrite slot d with val, rotate 15-d back

``STORE`` rotates ``d`` then ``15-d``, so the ring always turns 16 slots plus the
header read.  The two counts have to survive the first rotation loop and only
``B`` does, so ``val`` and ``15-d`` ride together as ``val + (15-d)<<10`` --
built with ``{`` and split with ``/`` (whose remainder lands in ``B``, which is
the whole reason packed fields are affordable here).  That also means the *input*
read happens once, in the store head, next to the input pipe.

``DRAIN`` holds the header out of the ring while it walks, so the drained slots
can be zeroed in place; when it finds an empty slot it pushes the header back
followed by that slot, which re-anchors the header in front of the **new**
``want``, and 15 further rotations restore the head.  Moving the header is
therefore free: it is the same push either way, just deferred.

Pipe binding is why all four pipes attach to the **north** wall.  For a north
anchor the Manhattan distance from any cell is ``|x - col| + y + 1``, so the
``y`` term is common to all four and "nearest pipe" reduces to *nearest column* --
a one-dimensional rule that holds at every row, instead of the 2-D case where a
block that moves down a row can silently rebind.  With input/output at columns
3/7 and ring-forward/return at 18/22, both midpoints land on 12.5, so

    columns 0..12 -> input / output        columns 13..36 -> the ring

and :func:`_io` / :func:`_ring` refuse to place a pipe op on the wrong side of
that line.  ``route-check.mjs`` and :meth:`Littleman.route` verify the result
against the engine.

The layout itself is a **serpentine** (:class:`Snake`): a straight-line glyph run
is poured along a row, wraps at the interior edge and continues westward on the
next row, and ``Snake.at_col`` walks to whatever column the next pipe op needs.
Nothing here is hand-placed except the branch lanes, which is what keeps the
column discipline above checkable rather than aspirational.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision, E, N, S, W
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["IH", "IW", "RING_WORDS", "build", "worker"]

# ── worker geometry ───────────────────────────────────────────────────────────
#: Interior of the one worker room.
IW, IH = 37, 32

#: North-wall anchor columns.  Both midpoints are 12.5, so the binding rule is
#: "columns <= 12 are I/O, columns >= 13 are the ring" at *every* row.
IN_COL, OUT_COL, FWD_COL, RET_COL = 3, 7, 18, 22

#: Cells at or west of this talk to input/output; cells east of it to the ring.
SPLIT = 12

#: Interior of the turnaround room.  5 words/lap is more than an exact-count
#: ``counted_ring("rs")`` worker can consume (5.0 ticks/rotation), so the ring is
#: worker-bound and the relay never binds -- see :mod:`dataflow_relay`.
RELAY_W, RELAY_H = 6, 3

#: Words resident in the ring: 16 window slots plus the header.
RING_WORDS = 17


def _io(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place an I/O-side pipe op, asserting it lands west of :data:`SPLIT`."""
    if x > SPLIT:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} binds to the ring, not I/O")
    c.set(x, y, ch)


def _ring(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place a ring-side pipe op, asserting it lands east of :data:`SPLIT`."""
    if x <= SPLIT:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} binds to I/O, not the ring")
    c.set(x, y, ch)



def worker() -> Circuit:  # noqa: PLR0915  - one grid, laid out row by row
    """The single worker room: INIT, HEAD, FAIL, STORE, DRAIN and two ring loops.

    Rows are allocated so that each block owns a band and the branch lanes never
    have to cross one another; the two ``counted_ring`` rotation loops sit in
    columns 13-14 (ring side) and everything that talks to input or output is
    pulled west of :data:`SPLIT` on its own row.
    """
    c = Circuit(IW, IH, strict_corridors=True)

    # ══ INIT: eat n, then fill the ring with 17 zeros ═════════════════════════
    # HDR = -want = 0 at want = 0 and every slot starts empty, so the whole ring
    # is one repeated `s` -- no distinguished header value is ever needed, since
    # the header is found by position, not by sign.
    c.set(0, 0, ">")
    c.run(1, 0, "@")
    c.horizontal(0, 1, IN_COL)
    _io(c, IN_COL, 0, "r")                          # A = n, discarded
    c.run(IN_COL + 1, 0, "`17`b0")                  # A = 17; BP = 17; A = 0
    c.set(10, 0, "v")
    c.set(10, 1, ">")
    c.horizontal(1, 10, 13)
    c.set(13, 1, "v")
    fill_exit, _ = c.counted_loop(13, 2, "s")       # 17 x { s(ring) }
    _ring(c, 14, 3, "s")
    c.route((fill_exit, 2), E, [(16, 2), (16, 5), (12, 5)], (12, 6), E)

    # ══ HEAD ═════════════════════════════════════════════════════════════════
    #   r(ring) M          B = HDR = -want
    #   r(input) +         A = seq - want = d
    #   b ]]]] d           BP = d >> 4; non-zero  => d >= 16 => FAIL
    #   X                  d == 0 -> east = DRAIN, d >= 1 -> south = STORE
    c.set(12, 6, ">")                               # every return path merges here
    _ring(c, 13, 6, "r")
    c.run(14, 6, "M")
    c.set(15, 6, "v")
    c.set(15, 7, "<")
    c.horizontal(7, 15, 2)
    c.set(2, 7, "v")
    c.set(2, 8, ">")
    _io(c, IN_COL, 8, "r")
    c.run(IN_COL + 1, 8, "+b]]]]")
    c.set(10, 8, "d")
    c.set(11, 8, "X")

    # ── FAIL: emit -1 and halt ───────────────────────────────────────────────
    c.set(10, 9, "<")
    c.run(9, 9, "1N", d=W)
    _io(c, OUT_COL, 9, "s")
    c.set(OUT_COL - 1, 9, "H")

    # ══ STORE (d >= 1) ═══════════════════════════════════════════════════════
    c.set(11, 9, ">")
    c.horizontal(9, 11, 13)
    c.run(13, 9, "W")
    _ring(c, 14, 9, "s")                            # push HDR back at push #1
    c.run(15, 9, "WbM`15`-M`10`W{M")                # BP = d; B = (15-d) << 10
    c.set(31, 9, "v")
    c.set(31, 10, "<")
    c.horizontal(10, 31, IN_COL)
    _io(c, IN_COL, 10, "r")                         # A = val
    c.run(IN_COL - 1, 10, "+M", d=W)                # B = P = val + ((15-d)<<10)
    c.set(0, 10, "v")
    c.set(0, 11, ">")
    c.horizontal(11, 0, 12)
    c.set(12, 11, "v")
    c.set(12, 12, ">")
    rot1 = c.counted_ring(13, 12, "rs")             # rotate d slots
    for x, y in ((14, 13), (14, 14), (13, 14), (13, 15)):
        _ring(c, x, y, c.get(x, y))
    c.route(rot1[0], E, [(16, 12)], (16, 17), W)
    c.set(16, 17, "<")
    c.horizontal(17, 16, 11)
    c.set(11, 17, "v")
    c.route(rot1[1], W, [(11, 16)], (11, 17), S)
    c.set(11, 18, ">")
    c.horizontal(18, 11, 13)
    _ring(c, 13, 18, "r")                           # drop the empty slot d
    c.run(14, 18, "WM`1024`W/W")                    # A = val, B = 15-d
    _ring(c, 25, 18, "s")                           # write val into slot d
    c.run(26, 18, "Wb")                             # BP = 15 - d
    c.set(28, 18, "v")
    c.set(28, 19, "<")
    c.horizontal(19, 28, 12)
    c.set(12, 19, "v")
    c.set(12, 20, ">")

    # ══ LOOP2: the shared phase restore, reached from STORE and from DRAIN ════
    rot2 = c.counted_ring(13, 20, "rs")
    for x, y in ((14, 21), (14, 22), (13, 22), (13, 23)):
        _ring(c, x, y, c.get(x, y))
    c.route(rot2[0], E, [(33, 20)], (33, 5), W)
    c.set(33, 5, "<")
    c.horizontal(5, 33, 16)
    c.route(rot2[1], W, [(11, 24), (11, 25), (33, 25)], (33, 24), N)
    c.vertical(33, 24, 20)

    # ══ DRAIN (d == 0) ═══════════════════════════════════════════════════════
    #   r(input) s(output)   emit val -- it is exactly the awaited packet
    #   0 + 1 W - M          B = HDR - 1, i.e. want += 1
    #   r(ring) 0 s(ring)    consume slot 0 and leave it empty
    c.horizontal(8, 11, 35)
    c.set(35, 8, "v")
    c.vertical(35, 8, 26)
    c.set(35, 26, "<")
    c.horizontal(26, 35, IN_COL)
    _io(c, IN_COL, 26, "r")                         # A = val
    c.set(2, 26, "v")
    c.set(2, 27, ">")
    c.horizontal(27, 2, OUT_COL)
    _io(c, OUT_COL, 27, "s")                        # emit val
    c.run(OUT_COL + 1, 27, "0+1W-M")                # B = HDR - 1
    _ring(c, 14, 27, "r")                           # consume slot 0
    c.run(15, 27, "0")
    _ring(c, 16, 27, "s")                           # ...and leave it empty
    c.set(17, 27, "v")

    # ── DRAINLOOP: v > 0 -> south = EMIT, v == 0 -> east = EXIT ──────────────
    c.set(17, 28, ">")
    _ring(c, 18, 28, "r")
    c.set(19, 28, "X")

    # ── EMIT lane ────────────────────────────────────────────────────────────
    c.set(19, 29, "<")
    c.horizontal(29, 19, OUT_COL)
    _io(c, OUT_COL, 29, "s")                        # emit the drained value
    c.run(OUT_COL - 1, 29, "0+1W-M", d=W)           # B = HDR - 1
    c.set(0, 29, "v")
    c.set(0, 30, ">")
    c.horizontal(30, 0, 13)
    c.run(13, 30, "0")
    _ring(c, 14, 30, "s")                           # push the slot back empty
    c.route((15, 30), E, [(15, 31), (17, 31)], (17, 29), N)
    c.vertical(17, 29, 28)

    # ── EXIT lane: re-anchor the header in front of the new want ─────────────
    c.run(20, 28, "W")
    _ring(c, 21, 28, "s")                           # push HDR'
    c.run(22, 28, "0")
    _ring(c, 23, 28, "s")                           # ...then the emptied slot
    # The literal is padded east so that no two backticks share a column: they
    # pair vertically as well as horizontally, and a live glyph caught between a
    # vertical pair is a *load* error rather than a wrong answer.
    c.horizontal(28, 23, 26)
    c.run(26, 28, "`15`b")                          # BP = 15: restore the head
    c.set(31, 28, "^")
    c.vertical(31, 28, 19)
    c.set(31, 19, "<")                              # merge into STORE's route to LOOP2
    c.horizontal(19, 31, 28)
    return c


def build() -> list[str]:
    """The whole machine: worker + I/O rooms + relay + the ring's two pipes."""
    w = worker()
    g = Circuit(IW + 12, IH + 10)
    wx, wy = 1, 6
    stamp(g, wx, wy, w.rows())
    walls(g, wx, wy, IW, IH)

    for col, label, into in ((IN_COL, "I", True), (OUT_COL, "O", False)):
        x = wx + col
        stamp(g, x - 1, 0, ["+-+", f"|{label}|", "+-+"])
        draw_pipe(g, [(x, 3), (x, wy - 2)] if into else [(x, wy - 2), (x, 3)])

    # The relay sits in the north band east of the I/O rooms, and both ring pipes
    # take the long way round on purpose: the ring must hold RING_WORDS + 1 words
    # or it deadlocks *silently*, and a short pipe pair cannot.
    rx = wx + 29
    stamp(g, rx, 0, relay(RELAY_W, RELAY_H))
    fwd, ret = wx + FWD_COL, wx + RET_COL
    n_fwd = draw_pipe(g, [(fwd, wy - 2), (fwd, 1), (rx - 1, 1)])
    n_ret = draw_pipe(g, [(rx - 1, 2), (ret, 2), (ret, wy - 2)])
    if n_fwd + n_ret < RING_WORDS + 1:
        raise Collision(f"ring holds {n_fwd + n_ret} words, need >= {RING_WORDS + 1}")
    return [r.rstrip() for r in g.rows() if r.strip()]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "grid"
    print(worker().ruler() if which == "worker" else "\n".join(build()))
