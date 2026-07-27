#!/usr/bin/env python3
"""`subset-sum` as a **two-room** meet-in-the-middle machine.

:mod:`randomfun2026solvers.subset_sum_grid` is the same algorithm in one room,
`92x128`.  The room is more than twice as tall as it is wide and the score
charges `max(w, h)^2`, so every row of it is paid for twice over and half its
width is void.  Cutting the stack in two and standing the halves side by side
trades rows for columns at exactly the rate the score wants.

## The seam

**Phase 3 and emit need ring V and the output pipe.  They never touch ring B.**
Everything that reads ring B — the doubling pass in `_rightvals`, and `_scan` —
is in init and phase 2.  So:

    room A   init + phase 2                46 rows of code, needs all three rings
    room B   phase 3 + emit + no-solution  67 rows of code, needs ring V and O

Room A stays wide (41) because the scan band and ring B's anchors live in it;
room B is 33, because it has no `b` band at all.  **Room B goes on the right:**
ring B must serpentine on room A's side, and swapping them drags it across the
whole grid.

## Ring V threads both rooms, and the relay room disappears

A pipe may not loop back into its own room, which is the only reason the
one-room build needs a turnaround for ring V.  With two rooms `A -> B -> A` is a
legal ring of two pipes and the relay is deleted.  Whichever room is not the
active phase forwards words for the other, out of a two-row loop.

**Forwarding is free, and that is measured rather than modelled.**  The design
this build was executed from priced an idle room at ~6 ticks a word against the
relay's 3.2 and charged the split ~5%.  Swapping ring V's relay on the one-room
grid for `relay(3,3)` (8.00 ticks a word), `relay(4,3)` (5.00) and `relay(8,6)`
(2.67) moves the seven-case average by **exactly zero ticks** in all four builds:
ring V's far side is never on the critical path.  It carries 28 words against a
~1,500-tick lap and the pipe is 97 cells, so the buffer absorbs the worker's
bursts and the far room has the whole lap to catch up.  Both idle loops here run
at ~3 ticks a word, comfortably inside that.

## The baton is a third pipe, polled with `q`

A marker word in ring V would cost ~10 ticks a word instead of 6 — every word
would need `M 2 + X W` to be compared without being destroyed — and it would have
to dodge `MB = -1` and `MT = -(t+1)`.  Instead room A sends **one word down a
pipe of its own** and room B's idle loop polls it: `q` sets `BP` from the nearest
incoming pipe and `a` turns out of the loop when `BP > 0`.  Four glyphs, no
compare, and no ring word is ever inspected.

The baton carries a value, which is what moves the no-solution answer out of the
deepest row in the grid: `0` means phase 2 found a residual, `1` means it ran
out of masks, and room B's `X` on that word is the whole of `_nosol`.

## Room B has to find the head of the ring, and the markers are already there

Room A's phase-2 lap reads all 28 words and re-sends them, so when it stops the
ring is aligned at `C` **as room A sees it** — but the words are spread across
two pipes and room B's idle loop, and room B has no idea where in the cycle it
is.  So it looks: `_rot` stops on the first negative word, `M 1 +` is `0` for
`MB = -1` and negative for `MT = -(t+1)`, and one `X` either retries the `_rot`
or falls through.  Past `MT` comes `RR`, and past `RR` comes `C`.  Six rows,
once a case.

## After the baton, room A never stops

Room B's phase 3 and emit rotate ring V, and a ring only turns if every room on
it is turning it, so room A forwards for ever.  That is sound because the judge
stops counting at the final correct output value and **the program need not
halt** (`littleman/GRADING.md`).
"""

from __future__ import annotations

from randomfun2026solvers import subset_sum_grid as ssg
from randomfun2026solvers.circuit import E, N, Circuit
from randomfun2026solvers.subset_sum_grid import _link, _op, vr, vs
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["build", "main", "room_a", "room_b"]

# ── room A: init and phase 2 ─────────────────────────────────────────────────
#
# Anchors are worker-interior columns on the **north** wall, so "nearest pipe"
# collapses to "nearest column" at every row — the one-dimensional rule that a
# block moved down a row cannot silently break.
#
#   incoming   I 3          V-ret 20      B-ret 30      midpoints 11.5, 25
#   outgoing   baton 9      V-fwd 21      B-fwd 31      midpoints 15,   26
#
# Room A loses the `O` pipe, which only *widens* the western band: the baton is
# the one thing it sends there.  So the `v` and `b` bands are the one-room
# machine's unchanged, and every block that was verified against them still is.
#
# The baton anchor is **9 and not 1** because of the band above, not the binding
# below: the `I` room sits over column 4 and its riser fills that column all the
# way to the wall, so a baton leaving further west could never get east past it.
# Nine is as far west as it can go and still leave `vs` at column 16 — the
# westernmost ring-V send in the room — five from `V-fwd` and seven from it.
A_IW, A_IH = 41, 60
A_BATON_COL = 9
A_ANCHORS = {
    "in": {"io": ssg.IN_COL, "v": ssg.VRET_COL, "b": ssg.BRET_COL},
    "out": {"io": A_BATON_COL, "v": ssg.VFWD_COL, "b": ssg.BFWD_COL},
}
A_EAST = ssg.EAST_COL

#: The ring-V columns the forwarding loop may use.  Derived, not chosen: with the
#: baton out at 9 and ring V out at 21 the midpoint is **15**, so a send at 15 is
#: a tie and a send at 14 goes down the baton.  The first draft laid the loop's
#: pairs from 14 and every ring-V word room A forwarded after handing over went
#: into the baton pipe instead — the grid loads, the shape of the answer is
#: right, and only the values are wrong.  `_op` now derives this rather than
#: trusting a band, and it is what caught it.
A_LOOP_LO, A_LOOP_HI = 16, 24

#: Room A's two exits from phase 2, and where the baton goes out.  `HIT` is the
#: row `_scan`'s found lanes converge on; `MISS` is the row `_phase2`'s `C == 0`
#: test leaves heading east.
A_HIT_ROW, A_MISS_ROW = 55, 56
A_LOOP_TOP = 58


def _baton(c: Circuit, x: int, y: int) -> None:
    """Send `A` down the baton pipe.  Its own band, because it is its own pipe."""
    _op(c, x, y, "s", "io")


def _idle(c: Circuit, y: int, x0: int, x1: int, lo: int, hi: int,
          *, poll: int | None = None) -> None:
    """A two-row forwarding loop: east along `y`, west along `y + 1`.

    Every `r` is followed immediately by its own `s`, so the walk never reaches a
    send it has no word for and leaving the loop between a pair is impossible.
    `poll` places the `q` that reads the baton pipe's depth on the outbound row
    and the `a` that leaves the loop on the return row; without it the loop is a
    bare forwarder and never leaves, which is what room A does after handing over.

    `lo`/`hi` are the columns where an `r` and an `s` both bind to ring V, and
    they are **not** symmetric: room A's baton sits west of ring V's send anchor
    and pulls the safe range east.  The poll goes far east, where `q` can only
    reach the baton.
    """
    pairs = [x for x in range(lo, hi) if (x - lo) % 2 == 0]
    c.set(x0, y, ">")
    c.set(x1, y, "v")
    c.set(x1, y + 1, "<")
    c.set(x0, y + 1, "^")
    for x in pairs:                             # outbound: r then s, eastward
        vr(c, x, y)
        vs(c, x + 1, y)
    for x in pairs:                             # return: the same, walked west
        vr(c, x + 1, y + 1)
        vs(c, x, y + 1)
    c.horizontal(y, pairs[-1] + 1, x1)
    c.horizontal(y + 1, x1, pairs[-1] + 1)
    c.horizontal(y, x0, pairs[0])
    c.horizontal(y + 1, pairs[0], x0)
    if poll is not None:
        _op(c, poll, y, "q", "bat")             # BP = words waiting on the baton
        c.set(poll - 2, y + 1, "a")             # BP > 0 -> ccw, south, out


def room_a(stage: str = "full") -> Circuit:
    """Init, the doubling pass, phase 2, the baton, and then forward for ever."""
    c = Circuit(A_IW, A_IH)
    c.anchors, c.east_col = A_ANCHORS, A_EAST

    exit_ = ssg._init(c)
    _link(c, exit_, E, 3, (14, 4))
    exit_ = ssg._leftvals(c, 4)
    _link(c, exit_, E, 8, (14, 9))
    exit_ = ssg._boundary(c, 9)
    _link(c, exit_, E, 10, (14, 11))
    exit_ = ssg._rightvals(c, 11)
    _link(c, exit_, E, 17, (14, 19))
    exit_ = ssg._tail(c, 19)

    if stage == "loadv":                        # hand straight over, ring loaded
        _link(c, exit_, E, 22, (14, 24))
        c.run(14, 24, "0")
        c.set(15, 24, "v")
        c.set(15, 25, "<")
        c.horizontal(25, 15, 9)
        _baton(c, 8, 25)
        c.horizontal(25, 8, 0)
        c.set(0, 25, "v")
        c.vertical(0, 25, A_LOOP_TOP)
        c.set(0, A_LOOP_TOP, ">")
        c.horizontal(A_LOOP_TOP, 0, 13)
        _idle(c, A_LOOP_TOP, 13, 26, A_LOOP_LO, A_LOOP_HI)
        return c

    _link(c, exit_, E, 22, (14, ssg.P2_HEAD))
    ssg._phase2(c)

    # ── found: `_scan`'s hit lanes end heading east one row below phase 2 ─────
    c.set(13, ssg.P3_HEAD, "v")
    c.set(13, A_HIT_ROW, "<")
    c.horizontal(A_HIT_ROW, 13, 9)
    c.run(9, A_HIT_ROW, "0", d=(-1, 0))
    _baton(c, 8, A_HIT_ROW)                     # 0 = phase 2 found a residual
    c.horizontal(A_HIT_ROW, 8, 0)
    c.set(0, A_HIT_ROW, "v")

    # ── exhausted: `C == 0` leaves the phase-2 head heading east ─────────────
    c.horizontal(ssg.P2_HEAD, 17, 40)
    c.set(40, ssg.P2_HEAD, "v")
    c.vertical(40, ssg.P2_HEAD, A_MISS_ROW)
    c.set(40, A_MISS_ROW, "<")
    c.horizontal(A_MISS_ROW, 40, 9)
    c.run(9, A_MISS_ROW, "1", d=(-1, 0))
    _baton(c, 8, A_MISS_ROW)                    # 1 = every left mask was tried
    c.horizontal(A_MISS_ROW, 8, 0)
    c.set(0, A_MISS_ROW, "v")

    # ── both lanes fall into the forwarding loop, which never ends ───────────
    c.vertical(0, A_MISS_ROW, A_LOOP_TOP)
    c.set(0, A_LOOP_TOP, ">")
    c.horizontal(A_LOOP_TOP, 0, 13)
    _idle(c, A_LOOP_TOP, 13, 26, A_LOOP_LO, A_LOOP_HI)
    return c


# ── room B: phase 3, emit, and the no-solution answer ────────────────────────
#
#   incoming   V-in 21      baton 31                    midpoint 26
#   outgoing   O 6          V-out 20                    midpoint 13
#
# The `v` band and the `io` band come out the same as room A's, which is why
# every block moves across unaltered; the baton gets a band of its own in the
# east, where `q` and the one `r` that reads it cannot reach ring V.
B_IW, B_IH = 33, 76
B_ANCHORS = {
    "in": {"v": ssg.VRET_COL, "bat": 31},
    "out": {"io": ssg.OUT_COL, "v": ssg.VFWD_COL},
}
B_EAST = 32
B_POLL_COL = 31

#: Room B's ring-V columns.  `O` out at 6 against ring V out at 21 puts the
#: send midpoint at 13.5, and the baton in at 31 against ring V in at 20 puts the
#: receive midpoint at 25.5 — so 14..25 is safe both ways, symmetric where room
#: A's is not.
B_LOOP_LO, B_LOOP_HI = 14, 25

#: Room B's rows.  The tail — phase 3 and emit — keeps the one-room build's
#: constants and is drawn through a :class:`~subset_sum_grid._Shifted` canvas, so
#: six blocks and nine lanes go on agreeing about `P3_HEAD`, `E1_HEAD` and the
#: rest without a second set of numbers to drift against.
#: Room B's prologue, row by row.  Every lane gets a row of its own: the `_rot`'s
#: entry corridor is the one thing three paths all want to reach, and a lane that
#: drops onto it anywhere but its own merge cell is re-steered in silence.
#:
#:     0,1  the forwarding loop, with the `q` poll and the `a` that leaves it
#:     2,3  read the baton and branch: `1` walks west into the whole of `_nosol`
#:     4    found — walk west and drop down column 12
#:     5    which marker was it?  `M 1 + N` then one `X`
#:     6,7  the alignment `_rot`, entered along row 6 from column 12
#:     8    it was `MT`: take `RR` off the ring and drop to phase 3
#:     9    it was `MB`: climb column 13 and go round the `_rot` again
B_LOOP_TOP, B_TAKE, B_FOUND, B_TEST, B_ALIGN, B_MT, B_MB = 0, 2, 4, 5, 6, 8, 9
B_SHIFT = -44                                   # P3_HEAD 54 -> row 10


def room_b(stage: str = "full") -> Circuit:
    """Forward ring V until the baton lands, find the ring's head, then answer."""
    c = Circuit(B_IW, B_IH)
    c.anchors, c.east_col = B_ANCHORS, B_EAST

    c.set(12, B_LOOP_TOP, "@")                  # spawn: a nop, heading east
    _idle(c, B_LOOP_TOP, 13, B_EAST, B_LOOP_LO, B_LOOP_HI, poll=B_POLL_COL)

    # ── the baton: `0` found, `1` exhausted ──────────────────────────────────
    take = B_POLL_COL - 2                        # the column `a` drops him onto
    _op(c, take, B_TAKE, "r", "bat")
    c.set(take, B_TAKE + 1, "X")                # 0 -> south; 1 -> clockwise, west
    c.horizontal(B_TAKE + 1, take, 7)
    c.run(7, B_TAKE + 1, "0", d=(-1, 0))
    ssg.out(c, 6, B_TAKE + 1)                   # the whole of `_nosol`
    c.set(5, B_TAKE + 1, "H")

    # ── found: walk west and drop into the alignment `_rot` ──────────────────
    c.set(take, B_FOUND, "<")
    c.horizontal(B_FOUND, take, 12)
    c.set(12, B_FOUND, "v")
    c.vertical(12, B_FOUND, B_ALIGN)
    c.set(12, B_ALIGN, ">")
    c.horizontal(B_ALIGN, 12, ssg.VRET_COL - 1)
    c.set(13, B_ALIGN, ">")                     # where the `MB` retry rejoins
    ssg._rot(c, ssg.VRET_COL, B_ALIGN)          # on to the first negative word

    # `_rot`'s `X` has a **third** exit and this is the one room where it can be
    # taken: straight, on a word of exactly zero.  In the one-room machine the
    # ring is always aligned and `_rot` only ever meets values and markers, but
    # room B starts wherever the idle loop left it and ring V holds two words
    # that can be zero — `RR` before phase 2 has written it, and `C` when the
    # last left mask is the winning one.  The word has already been re-sent by
    # the time the `X` fires, so the fix is one turn back into the rotation.
    c.set(ssg.VRET_COL + 3, B_ALIGN, "v")
    c.set(ssg.VRET_COL + 3, B_ALIGN + 1, "<")

    # ── which marker was it?  `MB` is -1 and `MT` is -(t+1), so `M 1 + N` is
    # zero for `MB` and *positive* for `MT` — and a positive `A` turns an `X`
    # clockwise, which is south here.  Negating is one glyph and it buys a lane
    # that leaves downward instead of north through the whole prologue.
    c.set(22, B_TEST, ">")
    c.run(23, B_TEST, "M1+N")
    c.set(27, B_TEST, "X")
    c.set(28, B_TEST, "v")                      # A == 0: that was `MB`
    c.vertical(28, B_TEST, B_MB)
    c.set(28, B_MB, "<")
    c.horizontal(B_MB, 28, 13)
    c.set(13, B_MB, "^")                        # climb back into the `_rot` entry
    c.vertical(13, B_MB, B_ALIGN)

    c.vertical(27, B_TEST, B_MT)                # A > 0: that was `MT`
    c.set(27, B_MT, "<")
    c.horizontal(B_MT, 27, ssg.VFWD_COL)
    vr(c, ssg.VFWD_COL, B_MT)                   # `RR`, walked westward
    vs(c, ssg.VRET_COL, B_MT)                   # and now the ring's head is `C`
    c.horizontal(B_MT, ssg.VRET_COL, 12)
    c.set(12, B_MT, "v")

    if stage == "loadv":                        # pour ring V out, head first
        y = B_MT + 2
        c.vertical(12, B_MT, y)
        c.set(12, y, ">")
        c.horizontal(y, 12, 14)
        vr(c, 14, y)
        c.set(15, y, "v")
        c.set(15, y + 1, "<")
        c.horizontal(y + 1, 15, 6)
        ssg.out(c, 6, y + 1)
        c.set(5, y + 1, "v")
        c.set(5, y + 2, ">")
        c.horizontal(y + 2, 5, 12)
        c.set(12, y + 2, "^")
        c.vertical(12, y + 2, y)
        return c

    s = ssg._Shifted(c, B_SHIFT)
    c.vertical(12, B_MT, ssg.P3_HEAD + B_SHIFT)
    c.set(12, ssg.P3_HEAD + B_SHIFT, ">")       # merge onto phase 3's own lane
    ssg._phase3(s)
    ssg._emit(s)
    return c


# ── the grid ─────────────────────────────────────────────────────────────────
#
# Room A on the left with the void beneath it, room B on the right, a two-column
# gap between them for ring B to climb down into that void.
#
# The band above is nine rows and every one of them is spoken for::
#
#     0  baton      A col 10 -> B col 77      the longest run, so the highest
#     1  ring V fwd A col 22 -> B col 67
#     2  ring V ret B col 66 -> A col 21
#     0..2 the I room, over column 4, west of every run
#     3..5 the O room, over column 52 — **low**, so its two-cell riser cannot
#          block the three runs above it.  This is the whole reason the band is
#          nine and not six: an `O` room at rows 0..2 fills column 52 down to the
#          wall and nothing can get from room A to room B past it.
#     6  ring B ret  void col 44 -> A col 31
#     7  ring B fwd  A col 32 -> void col 43
#
# Runs nest by span: a run may cross a column only if that column's own riser
# turns *below* it.  Ring B's two never leave columns 31..44, so they take the
# two rows against the wall and cross nothing at all.
BAND = 9
AX, BX = 1, 46
GAP_FWD, GAP_RET = 43, 44
GW, GH = 80, BAND + B_IH + 1

#: Words each ring must hold, plus a free cell: a ring exactly as long as its
#: contents blocks a send behind its own backlog and deadlocks in silence.
V_CAP, B_CAP = ssg.V_CAP, ssg.B_CAP

RELAY_X, RELAY_Y = 36, 74                       # ring B's turnaround, in the void


def _boxes() -> list[tuple[int, int, int, int]]:
    """Every room's outer rectangle, from the floor plan and not from the glyphs.

    `|` and `-` are a pipe's body as well as a wall and the finished grid cannot
    tell them apart, which is exactly what lets a phantom pipe hide.
    """
    return [
        (AX - 1, BAND - 1, A_IW + 2, A_IH + 2),
        (BX - 1, BAND - 1, B_IW + 2, B_IH + 2),
        (AX + ssg.IN_COL - 1, 0, 3, 3),
        (BX + ssg.OUT_COL - 1, 3, 3, 3),
        (RELAY_X, RELAY_Y, 8, 6),
    ]


def _mouths(rows: list[str], *, expect: int = 7) -> None:
    """Count pipe mouths the way the **runtime** does, and refuse a surprise.

    An arrowhead whose backward cell is a room border starts a pipe whether or
    not one was meant, and both `route-check.mjs` and `lm.mjs analyze` fold a
    stray one into its neighbour rather than reporting it.  Seven is the whole
    design: I, O, two per ring, and the baton.
    """
    from randomfun2026solvers.brackets_men import pipe_mouths, wall_cells

    found = pipe_mouths([r.rstrip() for r in rows], wall_cells(_boxes()))
    if len(found) != expect:
        listed = ", ".join(f"{ch!r}@{c}" for c, ch in sorted(found))
        raise ValueError(f"{len(found)} pipe mouths, wanted {expect}: {listed}")


def build(stage: str = "full") -> list[str]:
    """Both rooms, the I/O rooms, ring B's relay, and all seven pipes."""
    from randomfun2026solvers.dataflow_relay import relay

    g = Circuit(GW, GH)
    stamp(g, AX, BAND, room_a(stage).rows())
    walls(g, AX, BAND, A_IW, A_IH)
    stamp(g, BX, BAND, room_b(stage).rows())
    walls(g, BX, BAND, B_IW, B_IH)

    wall = BAND - 2                             # the band row against the walls
    a_in, a_bat = AX + ssg.IN_COL, AX + A_BATON_COL
    a_vr, a_vf = AX + ssg.VRET_COL, AX + ssg.VFWD_COL
    a_br, a_bf = AX + ssg.BRET_COL, AX + ssg.BFWD_COL
    # Room B's two ring-V anchors are the mirror of room A's: the pipes cross
    # logically, so the *incoming* one has to be the western of the pair or the
    # forward run and the return riser fight over a column in the band.  Nothing
    # inside room B can tell — an `r` there has one incoming ring pipe to pick
    # from and an `s` one outgoing, whichever column each sits on.
    b_out, b_in_v, b_out_v = BX + ssg.OUT_COL, BX + ssg.VRET_COL, BX + ssg.VFWD_COL
    b_bat = BX + B_POLL_COL

    stamp(g, a_in - 1, 0, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(a_in, 3), (a_in, wall)])
    stamp(g, b_out - 1, 3, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(b_out, wall), (b_out, 6)])

    # ── the baton, room A -> room B, one word a case ─────────────────────────
    draw_pipe(g, [(a_bat, wall), (a_bat, 0), (b_bat, 0), (b_bat, wall)])

    # ── ring V, threaded A -> B -> A; no relay room, because it needs none ───
    n = draw_pipe(g, [(b_out_v, wall), (b_out_v, 1), (a_vr, 1), (a_vr, wall)])
    n += draw_pipe(g, [(a_vf, wall), (a_vf, 2), (b_in_v, 2), (b_in_v, wall)])
    if n < V_CAP:
        raise ValueError(f"ring V holds {n} words, need >= {V_CAP}")

    # ── ring B: down the gap, round a relay, and serpentined under room A ────
    stamp(g, RELAY_X, RELAY_Y, relay(6, 4))
    m = draw_pipe(g, [(a_bf, wall), (a_bf, 6), (GAP_FWD, 6), (GAP_FWD, RELAY_Y - 2),
                      (RELAY_X + 3, RELAY_Y - 2), (RELAY_X + 3, RELAY_Y - 1)])
    ret = [(RELAY_X - 1, RELAY_Y + 2), (1, RELAY_Y + 2), (1, 81)]
    ret += ssg._serpentine(1, GAP_RET - 1, [81, 83, 85])[1:]
    ret += [(GAP_RET, 85), (GAP_RET, 5), (a_br, 5), (a_br, wall)]
    m += draw_pipe(g, ret)
    if m < B_CAP:
        raise ValueError(f"ring B holds {m} words, need >= {B_CAP}")

    _mouths(g.rows())
    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--stage", default="full", choices=("full", "loadv"))
    args = parser.parse_args(argv)
    args.man.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(build(args.stage)) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
