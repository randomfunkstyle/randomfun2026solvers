#!/usr/bin/env python3
"""Compact direct ``brackets`` parser with one arithmetic classifier.

The problem guarantees that every character is one of ``()[]{} `` (without the
space).  Their ASCII values have a useful structure:

``char / 32``
    is 1 for parentheses, 2 for square brackets, and 3 for braces.

``(-(char % 32 + char / 32)) & (char / 32)``
    is positive for each opener and zero for each closer.

The quotient modulo three is therefore also the base-3 stack tag.  This replaces
the earlier six-comparison staircase with one straight classifier and two
adjacent shared handlers.  Persistent state remains ``[packed_stack, position]``
in the tiny relay ring; the backpack holds the remaining character count.

Everything placed in the final grid is a structural ``manast`` node.  The
``Circuit`` layer is used only to collision-check the paths before converting
each glyph to editable ``Run``/``Joint`` leaves.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import reglyph

__all__ = [
    "MAX_PACKED_STACK",
    "RING_CAPACITY_NEEDED",
    "build_ast",
    "build",
    "debug_map",
    "main",
]

MAX_PACKED_STACK = 2 * 3**32 - 1
RING_CAPACITY_NEEDED = 3

IW, IH = 49, 17
WORKER_X = 6
RETURN_X = 48


def _worker() -> Circuit:
    c = Circuit(IW, IH, strict_corridors=True)

    # LOOP: entered heading west. BP>0 turns north to the classifier; BP==0
    # continues west to the end-of-input handler.
    c.set(4, 9, "d")
    c.route((4, 8), N, [(4, 3)], (4, 3), E)

    # Read one character. Division by 32 leaves its family in A and remainder
    # in B; W+N& computes the opener/closer signal while retaining the family.
    c.run(5, 3, "rM`32`W/W+N&")
    c.set(17, 3, "X")

    # Closer (zero branch): family%3 is the tag, then compare it with stack%3.
    c.run(18, 3, "WM3W%")
    c.run(23, 3, "MrWsWM3W/srsr~")
    c.set(37, 3, "X")
    c.set(38, 3, "r")
    c.set(39, 3, "X")

    # Wrong type: discard quotient, read position, emit it on the left.
    c.set(37, 4, "<")
    c.run(36, 4, "rr", d=W)
    c.horizontal(4, 34, 2)
    c.run(1, 4, "sH", d=W)

    # Matching tag with quotient zero means the sentinel was popped: underflow.
    c.set(40, 3, "r")
    c.route((41, 3), E, [(45, 3), (45, 0)], (2, 0), W)
    c.run(1, 0, "sH", d=W)

    # Opener (positive branch): the same family-to-tag conversion, one shared
    # push handler, then the common return lane.
    c.route((17, 4), S, [(17, 6)], (17, 6), E)
    c.run(18, 6, "WM3W%")
    c.run(23, 6, "MrW+++srM1+sm")
    c.route((36, 6), E, [(RETURN_X, 6), (RETURN_X, 9)], (5, 9), W)

    # Valid closer: put quotient back, advance position/decrement BP, and merge
    # into the same return lane. Build this second so its southbound path may
    # safely walk through the opener path's existing south arrow.
    c.set(39, 4, ">")
    c.run(40, 4, "srM1+sm")
    c.route((47, 4), E, [(RETURN_X, 4), (RETURN_X, 9)], (5, 9), W)

    # EOS: stack==sentinel emits 0; otherwise read and emit position (n+1).
    c.route((3, 9), W, [(1, 9), (1, 12)], (32, 12), E)
    c.run(33, 12, "rM1-")
    c.set(37, 12, "X")
    c.set(37, 11, "r")
    c.route((37, 10), N, [(37, 8)], (2, 8), W)
    c.run(1, 8, "sH", d=W)
    c.set(38, 12, "0")
    c.route((39, 12), E, [(43, 12), (43, 14)], (2, 14), W)
    c.run(1, 14, "sH", d=W)

    # INIT: n -> BP; ring = [sentinel stack 1, position 1].
    c.run(0, 16, "@rb")
    c.horizontal(16, 3, 37)
    c.run(38, 16, "1s1s")
    c.route((42, 16), E, [(RETURN_X, 16), (RETURN_X, 9)], (5, 9), W)

    return c


def _worker_children(circuit: Circuit) -> list[Run | Joint]:
    joints = frozenset("<>^vVXdax")
    ordered_cells = sorted(
        circuit.cell.items(),
        key=lambda item: (item[0][1], item[0][0]),
    )
    children: list[Run | Joint] = []
    for node_id, ((x, y), glyph) in enumerate(ordered_cells):
        if glyph == " ":
            continue
        if glyph in joints:
            children.append(
                Joint(id=node_id, x=x + WORKER_X + 1, y=y + 1, glyph=glyph)
            )
        else:
            children.append(
                Run(
                    id=node_id,
                    x=x + WORKER_X + 1,
                    y=y + 1,
                    glyphs=glyph,
                    heading="E",
                )
            )
    return children


def _pipe(
    node_id: int,
    path: list[tuple[int, int]],
    *,
    src: int,
    dst: int,
    direction: tuple[int, int],
    capacity: int,
) -> PipeNode:
    return PipeNode(
        id=node_id,
        x=min(x for x, _ in path),
        y=min(y for _, y in path),
        path=path,
        glyphs=reglyph(path, direction, direction),
        src=src,
        dst=dst,
        min_capacity=capacity,
        entry_dir=direction,
        exit_dir=direction,
    )


def build_ast() -> Ast:
    worker_circuit = _worker()
    worker = RoomNode(
        id=1,
        x=WORKER_X,
        y=0,
        kind="compute",
        w=IW,
        h=IH,
        children=_worker_children(worker_circuit),
        ports=[(6, 17), (6, 9), (56, 8), (56, 11)],
    )
    input_room = RoomNode(
        id=0,
        x=0,
        y=16,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=17, glyphs="I")],
        ports=[(2, 17)],
    )
    output_room = RoomNode(
        id=2,
        x=0,
        y=8,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=9, glyphs="O")],
        ports=[(2, 9)],
    )
    relay = RoomNode(
        id=3,
        x=59,
        y=7,
        kind="compute",
        w=5,
        h=4,
        children=[
            Joint(id=0, x=61, y=8, glyph=">"),
            Run(id=1, x=62, y=8, glyphs="r", heading="E"),
            Joint(id=2, x=64, y=8, glyph="v"),
            Run(id=3, x=60, y=9, glyphs="@", heading="E"),
            Joint(id=4, x=61, y=9, glyph="^"),
            Joint(id=5, x=64, y=11, glyph="<"),
            Run(id=6, x=63, y=11, glyphs="s", heading="E"),
            Joint(id=7, x=61, y=11, glyph="^"),
        ],
        ports=[(59, 8), (59, 11)],
    )

    return Ast(
        rooms=[input_room, worker, output_room, relay],
        pipes=[
            _pipe(0, [(3, 17), (4, 17), (5, 17)], src=0, dst=1, direction=E, capacity=1),
            _pipe(1, [(5, 9), (4, 9), (3, 9)], src=1, dst=2, direction=W, capacity=1),
            _pipe(2, [(57, 8), (58, 8)], src=1, dst=3, direction=E, capacity=2),
            _pipe(3, [(58, 11), (57, 11)], src=3, dst=1, direction=W, capacity=2),
        ],
    )


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("brackets — compact arithmetic classifier and packed stack")
    dbg.region(
        "worker",
        6,
        0,
        IW + 2,
        IH + 2,
        note="Direct parser with one classifier and adjacent shared push/pop handlers.",
        tags=["compute", "ast"],
    )
    dbg.region(
        "register-ring relay",
        59,
        7,
        7,
        6,
        note="Persistent [packed stack, position], with at most three transient values.",
        tags=["stack", "memory"],
    )
    dbg.lane(
        "arithmetic classifier",
        [(11, 4), (24, 4)],
        note="char/32 selects family; remainder expression selects opener versus closer.",
        kind="control",
    )
    dbg.lane("shared pop", [(25, 4), (47, 4)], kind="control")
    dbg.lane("shared push", [(25, 7), (43, 7)], kind="control")
    dbg.lane("ring forward", [(57, 8), (58, 8)], kind="pipe")
    dbg.lane("ring return", [(58, 11), (57, 11)], kind="pipe")
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
