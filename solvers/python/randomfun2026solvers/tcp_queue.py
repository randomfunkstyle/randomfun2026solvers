#!/usr/bin/env python3
"""`tcp` (packet reassembly) as a **two-tape queue** walked by a marker word.

A second machine for `tcp`, alongside :mod:`tcp_ring`.  Where the ring machine
indexes a fixed 16-slot window *by phase* (slot ``d = seq - want``), this one
keeps a genuine **variable-length queue** of the packets that arrived early and
searches it associatively.  The two tapes are pipe rings rotated in lockstep:

    S tape   MARK  s_1  s_2 ... s_k        the sequence numbers still held
    V tape   junk  v_1  v_2 ... v_k        their values, one for one

``MARK = -1`` is the rotation anchor.  It is the whole reason no counter is
needed: ``seq`` is never negative, so ``word - want`` is negative **exactly** on
the marker, zero exactly on the packet we are waiting for, and positive on
everything else.  One ``X`` therefore splits the loop three ways with a single
subtraction, and the queue's length never has to be represented at all --
which matters because the little man has only ``A``, ``B`` and a backpack he
cannot read.

That frees the registers to hold what actually matters::

    B    want -- the sequence number we are waiting for. Never spilled.
    BP   "a clean pass is still owed".  Set on every arrival and every emit,
         cleared when a pass starts, tested by `d` when the marker comes round.
    A    scratch.

**Every packet is enqueued, even the one that is an exact match.** That is not
laziness, it is what deletes a whole lane: an arrival with ``d == 0`` pushes
itself onto the tapes like any other and the drain loop finds it on the pass
that is about to run anyway.  ``MAIN`` shrinks to "is this the halt sentinel,
is it out of window, otherwise enqueue".

The scan is a pass that repeats while the flag is set::

    r(S) -              A = word - want
    X       < 0  MARKER  push MARK back, rotate V, `d`: flag ? new pass : exit
            = 0  MATCH   drop it, r(V), emit, want++, flag
            > 0  NOMATCH `+` restores the word, push it back, rotate V

Entering the scan with the flag *set* is what makes it correct rather than
merely plausible: an arrival pushes at the tail, so the first marker the walk
meets is mid-pass, and the forced restart guarantees one full pass from the
anchor before the loop is allowed to exit.  After an emit the flag is set
again, because ``want`` has changed and slots already walked past may now match.

**Layout.** All six pipes anchor on the worker's north wall, so the Manhattan
distance from any cell is ``|x - col| + y + 1``: the ``y`` term is common to all
six and "nearest pipe" collapses to *nearest column* at every row. Outgoing and
incoming pipes are ranked independently, which is what lets the four tape
anchors interleave with I/O::

    outgoing        OUT 3        SF 8         VF 14
    incoming   IN 0        SR 10        VR 16

    s(OUT) x<=5    r(input) x<=4    S tape: s 6..10, r 6..12    V tape: s>=12, r>=14

:func:`_out`, :func:`_in`, :func:`_stape` and :func:`_vtape` refuse a cell on
the wrong side of a boundary outright, because a mis-bound ``r`` reads a
plausible number rather than faulting.

Input is the **westmost** anchor on purpose. The driver room sits west of the
worker and there is exactly one free row above the north wall, so any other
ordering would have the driver's pipe cross the output pipe.

The gap between the S and V bands is one column, which is what keeps the hot
loop to **18 ticks per queue slot**: the walk is a rectangle -- north through
``+``/``s`` in column 10, east across the top through ``r``/``s`` in columns
14-15, south down column 16, west back to ``r``.

Ring length is a two-sided constraint, and the *lower* bound is the one that is
easy to miss. Each ring must hold :data:`TAPE_WORDS` words and still have a free
cell to turn on -- under that it deadlocks silently. But a ring much *longer*
than that is just as bad: with only the marker resident the worker pops it and
immediately wants it back, so every spare cell is a tick the scan stalls on an
almost-empty queue.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = [
    "IW",
    "IH",
    "TAPE_WORDS",
    "build",
    "worker",
    "driver",
    "debug_map",
    "main",
]

# ── worker geometry ───────────────────────────────────────────────────────────
#: Interior of the worker room.
IW, IH = 18, 20

#: North-wall anchor columns, worker-interior relative.  Input is the *westmost*
#: anchor on purpose: the driver sits west of the worker, so anywhere else its
#: pipe would have to cross the output pipe on the one free row above the wall.
#: Adjacent anchors are two columns apart -- two pipe cells side by side are
#: merged by the loader, so parallel runs must never touch.
IN_COL, OUT_COL, SF_COL, SR_COL, VF_COL, VR_COL = 0, 3, 8, 10, 14, 16

#: Words resident on each tape at the worst moment: the marker, the fifteen
#: packets a 16-wide window can hold, and the arrival that is about to match.
TAPE_WORDS = 17

#: Cells each tape ring must be able to hold, so that a full tape can still turn.
RING_CELLS = TAPE_WORDS + 1

# ── driver geometry ───────────────────────────────────────────────────────────
#: Interior of the driver room: read `n`, forward `n` `seq val` pairs, then send
#: the halt sentinel.
DW, DH = 4, 8


def _out(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place an output-side send.  OUT beats SF for x <= 5 (they tie on 5.5)."""
    if x > 5:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} does not bind to the output")
    c.set(x, y, ch)


def _in(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place an input receive.  IN beats SR for x <= 4 (they tie on 5)."""
    if x > 4:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} does not bind to the input")
    c.set(x, y, ch)


def _stape(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place an S-tape op.  `s` binds on 6..10, `r` on 6..12."""
    lo, hi = (6, 12) if ch == "r" else (6, 10)
    if not lo <= x <= hi:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} does not bind to the S tape")
    c.set(x, y, ch)


def _vtape(c: Circuit, x: int, y: int, ch: str) -> None:
    """Place a V-tape op.  `s` binds from 12 east, `r` from 14 east."""
    lo = 14 if ch == "r" else 12
    if x < lo:
        raise Collision(f"{ch!r} at ({x},{y}): column {x} does not bind to the V tape")
    c.set(x, y, ch)


def worker() -> Circuit:  # noqa: PLR0915 - one grid, laid out row by row
    """The worker room: INIT, ENQUEUE, the scan loop, MAIN, FAIL and MATCH."""
    c = Circuit(IW, IH)

    # ══ INIT: seed both tapes with the anchor pair ═══════════════════════════
    # S gets MARK, V gets a junk word that keeps the two tapes the same length.
    # `want` and the flag are already 0, and the walk falls straight into the
    # scan, whose first lap reads MARK, restores it and leaves with the flag
    # clear -- so INIT needs no jump of its own.
    c.run(0, 0, "@1N")
    _stape(c, 9, 0, "s")
    c.run(10, 0, "0")
    _vtape(c, 13, 0, "s")
    c.set(17, 0, "v")

    # ══ ENQUEUE: A = d on entry, climbing column 1 out of MAIN ═══════════════
    #   +        A = d + want = seq        (executed on the climb, at (1,7))
    #   s(S)     push the sequence number
    #   r(input) A = val
    #   s(V)     push the value, one for one with the S push
    #   1 b      flag: a full clean pass is owed
    c.set(1, 1, ">")
    _stape(c, 9, 1, "s")
    c.set(10, 1, "v")
    c.set(10, 2, "<")
    _in(c, 4, 2, "r")
    c.set(3, 2, "v")
    c.set(3, 3, ">")
    _vtape(c, 13, 3, "s")
    c.run(14, 3, "1b")
    c.set(17, 3, "v")

    # ══ SCAN ═════════════════════════════════════════════════════════════════
    # The loop top is walked WEST so that `r(S)`, the subtraction and the branch
    # all land in the S band and the NOMATCH lane can climb column 10 through
    # `+`/`s`.  That is what makes the cycle a rectangle rather than a detour:
    # 18 ticks per queue slot instead of 25.
    #
    # ── NOMATCH lane (word > want), read bottom-up ───────────────────────────
    c.set(10, 6, "+")                               # A = the word again
    _stape(c, 10, 5, "s")                           # push it back
    c.set(10, 4, ">")
    _vtape(c, 14, 4, "r")                           # rotate the V tape in step
    _vtape(c, 15, 4, "s")
    c.set(16, 4, "v")

    # ── loop top, walked west ────────────────────────────────────────────────
    c.set(17, 7, "<")                               # entry from INIT / ENQUEUE
    c.set(16, 7, "<")
    c.set(13, 7, "<")                               # restart / MATCH re-entry
    _stape(c, 12, 7, "r")                           # A = the next queued word
    c.run(11, 7, "-")                               # A = word - want
    c.set(10, 7, "X")

    # ── MARKER lane (word < want, i.e. the anchor) ───────────────────────────
    c.set(10, 8, "+")                               # A = MARK again
    _stape(c, 10, 9, "s")                           # push the anchor back
    c.set(10, 10, ">")
    _vtape(c, 14, 10, "r")                          # keep the tapes aligned
    _vtape(c, 15, 10, "s")
    c.set(16, 10, "d")                              # flag set -> another pass
    c.set(17, 10, "v")                              # flag clear -> MAIN

    # ── restart: clear the flag, climb column 12, re-enter the loop ──────────
    c.run(16, 11, "0b", d=(0, 1))
    c.set(16, 13, "<")
    c.set(12, 13, "^")
    c.set(12, 8, ">")
    c.set(13, 8, "^")

    # ══ MAIN: one packet ═════════════════════════════════════════════════════
    #   r(input) -   A = seq - want = d
    #   X            d < 0 -> HALT (the driver's sentinel); 0 and d > 0 fold
    #                north onto the same column
    #   b ]]]] d     BP = d >> 4; non-zero => d >= 16 => FAIL
    c.set(17, 15, "<")
    _in(c, 4, 15, "r")
    c.run(3, 15, "-")
    c.set(2, 15, "X")
    c.set(2, 16, "H")                               # d < 0: the stream is over
    c.set(1, 15, "^")                               # d == 0 folds north...
    c.set(2, 14, "<")                               # ...and so does d > 0
    c.set(1, 14, "^")
    c.run(1, 13, "b]]]]", d=(0, -1))
    c.set(1, 8, "d")

    # ── FAIL: a packet 16 or more past `want` loses the stream ───────────────
    c.run(2, 8, "1N")
    _out(c, 4, 8, "s")
    c.set(5, 8, "H")

    # ── otherwise climb into ENQUEUE, turning d into seq on the way ──────────
    c.set(1, 7, "+")

    # ══ MATCH lane: the packet we were waiting for ═══════════════════════════
    #   r(V) s(output)   emit the value paired with the word just dropped
    #   1 + M b          want += 1, and flag another pass: the new want may
    #                    already sit in a slot this pass has walked past
    c.set(9, 7, "v")
    c.set(9, 17, ">")
    _vtape(c, 14, 17, "r")
    c.set(15, 17, "v")
    c.set(15, 18, "<")
    c.set(0, 18, "v")
    c.set(0, 19, ">")
    _out(c, 1, 19, "s")
    c.run(2, 19, "1+Mb")
    c.set(12, 19, "^")
    return c


def driver() -> Circuit:
    """The packet sender: read `n`, forward `n` pairs, then the halt sentinel.

    It exists so that the *worker's* backpack is free to be the scan flag. The
    count lives here instead, in a room with exactly one incoming and one
    outgoing pipe, where `b`/`m`/`d` have nothing to compete with -- and the
    worker never sees `n` at all, only `seq val` pairs.

    The sentinel is `-1`. Every real `seq` is at least `want`, so a negative one
    is unambiguous, and it lands on the branch MAIN already has to make.
    """
    c = Circuit(DW, DH)
    c.run(0, 0, "@rb")                              # A = n; BP = n
    c.set(3, 0, "v")
    c.set(3, 1, "<")
    c.set(0, 1, "v")
    c.set(0, 2, ">")
    exit_ = c.counted_loop(1, 2, "rsrs")            # n x { seq, val } forwarded
    assert exit_ == (3, 2), exit_
    c.set(3, 2, "v")
    c.run(3, 3, "1Ns", d=(0, 1))                    # A = -1: the halt sentinel
    c.set(3, 6, "H")
    return c


# ── whole-grid placement ──────────────────────────────────────────────────────
#: Worker room walls span WX..WX+IW+1 and WY..WY+IH+1.
WX, WY = 7, 15
#: Interior origin: an interior column `c` sits at absolute `OX + c`.
OX, OY = WX + 1, WY + 1

#: Driver room walls, west of the worker.
DX, DY = 0, 16

#: Relay room walls.  Each relay spans its own ring's two anchor columns, so both
#: of that ring's pipes run straight up and down and cross nothing.
S_RELAY_X, V_RELAY_X, RELAY_Y = 15, 21, 0


def _anchor(col: int) -> int:
    """Absolute column of a worker north-wall anchor."""
    return OX + col


def build() -> list[str]:
    """The whole machine: driver, worker, I/O rooms, two relays and two rings."""
    g = Circuit(27, 37)

    stamp(g, OX, OY, worker().rows())
    walls(g, OX, OY, IW, IH)
    stamp(g, DX + 1, DY + 1, driver().rows())
    walls(g, DX + 1, DY + 1, DW, DH)

    # Input room -> driver -> worker.  The input anchor is the westmost on the
    # wall, which is what keeps this chain clear of the output pipe: there is
    # only one free row above the worker, and both would want it.
    stamp(g, 0, 11, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(1, 14), (1, 15)])
    draw_pipe(g, [(4, 15), (4, 13), (8, 13), (8, 14)])

    # Output room, hanging directly over its anchor.
    stamp(g, _anchor(OUT_COL) - 1, 10, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(_anchor(OUT_COL), WY - 1), (_anchor(OUT_COL), 13)])

    # The two tape rings.  A ring has to hold RING_CELLS words *and keep
    # turning* -- and it must not be much longer than that either: with only the
    # marker resident the worker pops it and immediately wants it back, so every
    # spare cell is a tick the scan stalls on an almost-empty queue.
    for relay_x, fwd, ret in (
        (S_RELAY_X, SF_COL, SR_COL),
        (V_RELAY_X, VF_COL, VR_COL),
    ):
        stamp(g, relay_x, RELAY_Y, relay(3, 3))
        n_fwd = draw_pipe(g, [(_anchor(fwd), WY - 1), (_anchor(fwd), RELAY_Y + 5)])
        n_ret = draw_pipe(g, [(_anchor(ret), RELAY_Y + 5), (_anchor(ret), WY - 1)])
        if n_fwd + n_ret < RING_CELLS:
            raise Collision(f"ring holds {n_fwd + n_ret} cells, need >= {RING_CELLS}")

    return [r.rstrip() for r in g.rows()]


def debug_map() -> DebugMap:
    """Name every block, lane and pipe, so the HTML overlay reads as the algorithm."""
    dbg = DebugMap("tcp - two-tape queue walked by a marker word")

    dbg.region(
        "driver",
        DX,
        DY,
        DW + 2,
        DH + 2,
        note=(
            "Reads n, forwards n `seq val` pairs, then sends -1 as the halt "
            "sentinel. The count lives here so the worker's backpack is free."
        ),
        tags=["compute", "counter"],
    )
    dbg.region(
        "worker",
        WX,
        WY,
        IW + 2,
        IH + 2,
        note="INIT, MAIN, FAIL, ENQUEUE, the scan loop and MATCH.",
        tags=["compute", "queue"],
    )
    dbg.region(
        "S relay",
        S_RELAY_X,
        RELAY_Y,
        5,
        5,
        note="Turnaround for the sequence-number tape; one word per 6-tick lap.",
        tags=["ring"],
    )
    dbg.region(
        "V relay",
        V_RELAY_X,
        RELAY_Y,
        5,
        5,
        note="Turnaround for the value tape, rotated in lockstep with the S tape.",
        tags=["ring"],
    )

    # ── the worker's blocks, in execution order ──────────────────────────────
    for name, x, y, w, h, note in (
        (
            "INIT",
            OX,
            OY,
            IW,
            1,
            "Seed the tapes: MARK onto S, a junk word onto V. Falls into SCAN, "
            "whose first lap reads MARK back and leaves with the flag clear.",
        ),
        (
            "ENQUEUE",
            OX,
            OY + 1,
            IW,
            3,
            "A = seq (d + want); push it to S, read val, push it to V, set the "
            "flag. Every packet takes this path, the exact match included.",
        ),
        (
            "SCAN",
            OX + 9,
            OY + 4,
            9,
            7,
            "The hot loop, 18 ticks a slot: r(S), subtract want, and one X "
            "splits marker / match / no-match.",
        ),
        (
            "MAIN",
            OX,
            OY + 13,
            IW,
            4,
            "r(input), A = seq - want. Negative is the driver's sentinel and "
            "halts; the rest folds north into `b ]]]] d`.",
        ),
        (
            "FAIL",
            OX + 1,
            OY + 8,
            5,
            1,
            "d >= 16: emit -1 and stop. The stream is lost.",
        ),
        (
            "MATCH",
            OX,
            OY + 17,
            IW,
            3,
            "r(V), emit, want += 1, flag another pass -- the new want may sit "
            "in a slot this pass has already walked past.",
        ),
    ):
        dbg.region(name, x, y, w, h, note=note, tags=["block"])

    dbg.lane("input pipe", [(1, 14), (1, 15)], kind="pipe")
    dbg.lane(
        "driver -> worker",
        [(4, 15), (4, 13), (8, 13), (8, 14)],
        note="Enters at the westmost anchor, clear of the output pipe.",
        kind="pipe",
    )
    dbg.lane(
        "output pipe",
        [(_anchor(OUT_COL), WY - 1), (_anchor(OUT_COL), 13)],
        kind="pipe",
    )
    for name, fwd, ret in (("S tape", SF_COL, SR_COL), ("V tape", VF_COL, VR_COL)):
        dbg.lane(
            f"{name} forward",
            [(_anchor(fwd), WY - 1), (_anchor(fwd), RELAY_Y + 5)],
            note=f"Both legs are 10 cells, so the ring is 20 -- at least the "
            f"{RING_CELLS} that {TAPE_WORDS} resident words plus one free cell "
            "to turn on demand, and not so many more that an almost-empty "
            "queue stalls a whole lap waiting for the marker to come round.",
            kind="pipe",
        )
        dbg.lane(
            f"{name} return",
            [(_anchor(ret), RELAY_Y + 5), (_anchor(ret), WY - 1)],
            kind="pipe",
        )

    dbg.lane(
        "no-match cycle",
        [
            (OX + 12, OY + 7),
            (OX + 10, OY + 7),
            (OX + 10, OY + 4),
            (OX + 16, OY + 4),
            (OX + 16, OY + 7),
            (OX + 12, OY + 7),
        ],
        note="The rectangle the scan walks per queue slot: `+`/s(S) up column "
        "10, r(V)/s(V) across the top, back down column 16.",
        kind="control",
    )
    dbg.lane(
        "pass restart",
        [(OX + 16, OY + 13), (OX + 12, OY + 13), (OX + 12, OY + 8), (OX + 13, OY + 8)],
        note="Marker reached with the flag set: clear it and walk one more "
        "full pass from the anchor.",
        kind="control",
    )

    dbg.scenario(
        "drain burst",
        "16 15 215 / 14 214 / 13 213 / 12 212 / 11 211 / 10 210 / 9 209 / "
        "8 208 / 7 207 / 6 206 / 5 205 / 4 204 / 3 203 / 2 202 / 1 201 / 0 200",
        0,
        9_000,
        watch=["worker", "SCAN", "S relay", "V relay"],
        note="Fifteen packets queued, then packet 0 unlocks all sixteen at "
        "once -- the deepest the tapes ever get.",
    )
    dbg.scenario(
        "loss case",
        "20 1 301 / 2 302 / 3 303 / 4 304 / 5 305 / 6 306 / 7 307 / 8 308 / "
        "9 309 / 10 310 / 11 311 / 12 312 / 13 313 / 14 314 / 15 315 / 16 316",
        0,
        7_000,
        watch=["worker", "MAIN", "FAIL"],
        note="seq 16 arrives while want is still 0: `b ]]]] d` fires and the "
        "machine emits -1 and stops.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = build()
    dbg = debug_map()
    for path in (args.man, args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["worker"]:
        print(worker().ruler())
    elif sys.argv[1:2] == ["driver"]:
        print(driver().ruler())
    elif sys.argv[1:2] == ["grid"]:
        print("\n".join(build()))
    else:
        raise SystemExit(main())
