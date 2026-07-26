#!/usr/bin/env python3
"""Compact AST machine for ``reverse-a-list``.

The list is stored in a FIFO pipe ring.  Loading preserves input order; to emit
the last value, the worker rotates the first ``k - 1`` values and removes the
new head.  The important state simplification over the original value-ring
machine is that the remaining count stays in ``B``:

``INIT``
    Read ``n`` into ``B``/``BP`` and load ``n`` values into the ring.
``FIRST``
    Compute ``n - 1`` once, putting it in both ``B`` and ``BP``.
``ROTATE``
    Rotate ``BP`` values through the ring, then read and emit the head.
``NEXT``
    Recover the previous rotation count from ``B``.  Zero means the last value
    was just emitted; positive counts are decremented and copied to ``BP``/``B``
    before returning to ``ROTATE``.

The update is folded immediately after the output send, so it needs no separate
setup lane.  The worker is collision-routed with :class:`Circuit`, but every
emitted object is a structural :mod:`manast` node and the final grid is produced
only by :func:`manast.render`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import reglyph

__all__ = [
    "MAX_LIST_LENGTH",
    "RING_CAPACITY_NEEDED",
    "build_ast",
    "build",
    "debug_map",
    "main",
]

MAX_LIST_LENGTH = 16
# Sixteen resident values plus one empty slot so the first value can advance.
RING_CAPACITY_NEEDED = MAX_LIST_LENGTH + 1

IW, IH = 10, 11
WORKER_Y = 3
WORKER_ID, INPUT_ID, OUTPUT_ID, RELAY_ID = 1, 0, 2, 3


def _worker() -> Circuit:
    """Place the compact control graph inside a 10x11 worker interior."""
    c = Circuit(IW, IH, strict_corridors=True)

    # INIT entry is the `>` at (1,0), so the same code works for the initial
    # spawn and for the next round.
    c.run(0, 0, "@>rbM")
    c.route((5, 0), E, [(9, 0), (9, 1)], (9, 1), S)

    # LOAD: BP=n; the horizontal counted loop executes r(input), s(ring).
    load_exit = c.counted_loop_horizontal(6, 1, "rs")
    assert load_exit == (9, 3)

    # FIRST: B=n -> A=B=BP=n-1.  It folds west below LOAD, then reaches
    # ROTATE through the common entry column.
    c.set(9, 3, "<")
    c.run(8, 3, "1-NbM", d=W)
    c.route((3, 3), W, [(3, 6)], (9, 6), S)

    # ROTATE: BP times { r(ring); s(ring) }.  The zero exit drops onto EMIT.
    rotate_exit = c.counted_loop_horizontal(6, 7, "rs")
    assert rotate_exit == (9, 9)

    # EMIT: read the last value near the ring-return pipe, walk west, then send
    # it near the output pipe.  The distance is intentional pipe affinity.
    c.set(9, 9, "<")
    c.set(8, 9, "r")
    c.horizontal(9, 8, 4)
    c.set(4, 9, "s")

    # NEXT: B is the previous rotation count.  Branch before decrementing:
    #   positive (`X` turns north while walking west) -> SETUP -> ROTATE
    #   zero (straight west)                          -> INIT
    c.run(3, 9, "WMX", d=W)
    c.set(0, 9, "v")
    c.set(0, 10, ">")
    c.set(5, 10, "^")
    c.route((5, 9), N, [(5, 4), (2, 4), (2, 1)], (1, 1), N)

    # SETUP for another value: c -> c-1 in BP and B.  It folds down the west
    # edge and merges into the same ROTATE-entry row as FIRST.
    c.run(1, 8, "1-NbM", d=N)
    c.set(1, 3, "<")
    c.set(0, 3, "v")
    c.set(0, 6, ">")
    # Crossing the setup's `N` only changes dead A; BP/B already own c-1.
    c.route((2, 6), E, [], (9, 6), S)
    return c


def _worker_children(circuit: Circuit) -> list[Run | Joint]:
    """Convert the collision-checked placement into editable AST leaves."""
    joints = frozenset("<>^vVXdax")
    children: list[Run | Joint] = []
    for node_id, ((x, y), glyph) in enumerate(
        sorted(circuit.cell.items(), key=lambda item: (item[0][1], item[0][0]))
    ):
        if glyph == " ":
            continue
        gx, gy = x + 1, y + WORKER_Y + 1
        if glyph in joints:
            children.append(Joint(id=node_id, x=gx, y=gy, glyph=glyph))
        else:
            children.append(
                Run(id=node_id, x=gx, y=gy, glyphs=glyph, heading="E")
            )
    return children


def _pipe(
    node_id: int,
    path: list[tuple[int, int]],
    *,
    src: int,
    dst: int,
    entry: tuple[int, int],
    exit_: tuple[int, int],
    capacity: int,
) -> PipeNode:
    return PipeNode(
        id=node_id,
        x=min(x for x, _ in path),
        y=min(y for _, y in path),
        path=path,
        glyphs=reglyph(path, entry, exit_),
        src=src,
        dst=dst,
        min_capacity=capacity,
        entry_dir=entry,
        exit_dir=exit_,
    )


def build_ast() -> Ast:
    """Build the 18-square worker, I/O rooms, relay, and value ring."""
    worker = RoomNode(
        id=WORKER_ID,
        x=0,
        y=WORKER_Y,
        kind="compute",
        w=IW,
        h=IH,
        children=_worker_children(_worker()),
        ports=[(11, 3), (11, 11), (11, 13), (11, 14)],
    )
    input_room = RoomNode(
        id=INPUT_ID,
        x=14,
        y=0,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=15, y=1, glyphs="I")],
        ports=[(14, 2)],
    )
    output_room = RoomNode(
        id=OUTPUT_ID,
        x=12,
        y=15,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=13, y=16, glyphs="O")],
        ports=[(13, 15)],
    )

    # The incoming pipe attaches at the bottom, so `U` turns north through `s`;
    # the right column closes the loop.  The outgoing pipe leaves the east wall
    # rather than the adjacent bottom cell, because the loader would merge two
    # side-by-side pipe starts.  The last row is only the spawn tail: `@^<`
    # enters `U` once without widening the two-column steady-state loop.
    relay = RoomNode(
        id=RELAY_ID,
        x=12,
        y=4,
        kind="compute",
        w=2,
        h=4,
        children=[
            Joint(id=0, x=13, y=5, glyph=">"),
            Joint(id=1, x=14, y=5, glyph="v"),
            Run(id=2, x=13, y=6, glyphs="s"),
            Run(id=3, x=13, y=7, glyphs="U"),
            Joint(id=4, x=14, y=7, glyph="<"),
            Run(id=5, x=13, y=8, glyphs="@"),
            Joint(id=6, x=14, y=8, glyph="^"),
        ],
        ports=[(13, 9), (15, 6)],
    )

    # Input is close to INIT/LOAD and far from the ring-return pipe.
    input_pipe = _pipe(
        0,
        [(13, 2), (12, 2), (12, 3)],
        src=INPUT_ID,
        dst=WORKER_ID,
        entry=W,
        exit_=W,
        capacity=2,
    )
    output_pipe = _pipe(
        1,
        [(12, 14), (13, 14)],
        src=WORKER_ID,
        dst=OUTPUT_ID,
        entry=E,
        exit_=S,
        capacity=2,
    )

    # The narrow relay receives from below, so the old west-entry `>^<^` hook is
    # gone.  Only one east step is needed to align the worker with its bottom
    # input port.
    forward_path = [
        (12, 11),
        (13, 11),
        (13, 10),
    ]
    # The return first continues east (so the loader sees its room attachment),
    # drops down one outer column, and heads straight back into the worker.
    # Forward + return remains exactly the 17-cell capacity bound.
    return_path = [
        (16, 6),
        (17, 6),
        (17, 7),
        (17, 8),
        (17, 9),
        (17, 10),
        (17, 11),
        (17, 12),
        (17, 13),
        (16, 13),
        (15, 13),
        (14, 13),
        (13, 13),
        (12, 13),
    ]
    forward_pipe = _pipe(
        2,
        forward_path,
        src=WORKER_ID,
        dst=RELAY_ID,
        entry=E,
        exit_=N,
        capacity=2,
    )
    return_pipe = _pipe(
        3,
        return_path,
        src=RELAY_ID,
        dst=WORKER_ID,
        entry=E,
        exit_=W,
        capacity=2,
    )
    if len(forward_path) + len(return_path) < RING_CAPACITY_NEEDED:
        raise AssertionError("value ring is too short for a full-size list")

    return Ast(
        rooms=[worker, input_room, output_room, relay],
        pipes=[input_pipe, output_pipe, forward_pipe, return_pipe],
    )


def build() -> list[str]:
    """Render the authored AST, clipping no cells by hand."""
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("reverse-a-list — compact AST value ring")
    dbg.region(
        "worker",
        0,
        WORKER_Y,
        IW + 2,
        IH + 2,
        note="INIT, LOAD, one-time k-1 setup, ROTATE, EMIT, and NEXT.",
        tags=["compute", "ast"],
    )
    dbg.region(
        "U relay",
        12,
        4,
        4,
        6,
        note="Two-wide bottom-entry U loop; the return leaves from its east wall.",
        tags=["stack", "ring", "r-to-U"],
    )
    dbg.lane("input pipe", [(13, 2), (12, 2), (12, 3)], kind="pipe")
    dbg.lane(
        "output pipe",
        [(12, 14), (13, 14)],
        note="Two-cell minimum from worker east wall into the output top wall.",
        kind="pipe",
    )
    dbg.lane(
        "ring forward",
        [(12, 11), (13, 11), (13, 10)],
        note="One alignment step, then straight north into the relay bottom.",
        kind="pipe",
    )
    dbg.lane(
        "ring return",
        [
            (16, 6),
            (17, 6),
            (17, 13),
            (16, 13),
            (14, 13),
            (12, 13),
        ],
        note="Fourteen cells; with the three-cell forward leg, exactly the 17-cell bound.",
        kind="pipe",
    )
    dbg.lane(
        "first k-1 setup",
        [(10, 7), (4, 7), (4, 10), (10, 10)],
        kind="control",
    )
    dbg.lane(
        "next-value feedback",
        [(2, 12), (2, 7), (1, 7), (1, 10), (10, 10), (10, 11)],
        note="Positive count is decremented and returns to ROTATE.",
        kind="control",
    )
    dbg.scenario(
        "full length with extremes",
        "16 "
        + " ".join(
            map(
                str,
                [
                    -1_000_000,
                    1_000_000,
                    0,
                    1,
                    -1,
                    999_999,
                    -999_999,
                    7,
                    7,
                    42,
                    -42,
                    3,
                    2,
                    1,
                    0,
                    -1_000_000,
                ],
            )
        ),
        0,
        8_000,
        watch=["worker", "U relay", "ring return"],
        note="Worst legal list length, duplicates, and both value bounds.",
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
    raise SystemExit(main())
