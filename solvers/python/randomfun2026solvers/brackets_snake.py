#!/usr/bin/env python3
"""Snake-folded AST parser for ``brackets``.

This is the compact parser's same arithmetic classifier and base-3 stack, but
its long instruction paths are folded:

* the close arm travels east to its tag conversion, north, then west through
  the pop handler;
* the open arm travels west through its tag conversion, south, then east
  through the push handler;
* the output sits above the worker and the input below it, eliminating the
  dedicated five-column I/O strip;
* the register relay sits above the worker, so folded stack ``r``/``s`` cells
  remain nearer to the ring than to the input/output pipes.

The resulting program is narrow by construction rather than a clipped wide
layout.  It is represented entirely by structural ``manast`` nodes and rendered
only through ``manast.render``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import reglyph

__all__ = ["build_ast", "build", "debug_map", "main"]

IW, IH = 30, 19
WORKER_X, WORKER_Y = 0, 6
RELAY_X, RELAY_Y = 20, 0


def _worker() -> Circuit:  # noqa: PLR0915 - one collision-checked control graph
    c = Circuit(IW, IH, strict_corridors=True)

    # All terminal paths merge onto one output instruction near the left pipe.
    c.set(0, 1, ">")
    c.run(1, 1, "sH")

    # Classifier. The close arm continues east, then folds north and west.
    c.run(7, 12, "rM`32`W/W+N&")
    c.set(19, 12, "X")
    c.run(20, 12, "WM3W%")
    c.route((25, 12), E, [(28, 12), (28, 5)], (28, 5), W)
    c.run(27, 5, "MrWsWM3W/srsr~", d=W)
    c.set(13, 5, "X")
    c.set(12, 5, "r")
    c.set(11, 5, "X")

    # Wrong type: discard quotient, read position, then merge onto the roof.
    c.set(13, 4, ">")
    c.run(14, 4, "rr")
    c.route((16, 4), E, [(18, 4), (18, 0), (0, 0), (0, 1)], (0, 1), E)

    # Underflow reaches the same roof through the clear far-right column.
    c.set(10, 5, "r")
    c.route(
        (9, 5),
        W,
        [(9, 7), (29, 7), (29, 0), (0, 0), (0, 1)],
        (0, 1),
        E,
    )

    # Valid pop folds west and joins the bottom return loop.
    c.set(11, 4, "<")
    c.run(10, 4, "srM1+sm", d=W)
    c.route((3, 4), W, [(3, 17), (29, 17), (29, 16)], (28, 16), W)

    # Open arm: west through tag conversion, south, east through one push.
    c.route((19, 13), S, [(19, 14)], (19, 14), W)
    c.run(18, 14, "WM3W%", d=W)
    c.set(13, 14, "v")
    c.set(13, 15, ">")
    c.run(14, 15, "MrW+++srM1+sm")
    c.route((27, 15), E, [(29, 15), (29, 16)], (28, 16), W)

    # Main loop: BP>0 goes to the classifier; zero folds left to EOS.
    c.set(27, 16, "d")
    c.route((27, 15), N, [(27, 13), (6, 13), (6, 12)], (6, 12), E)
    c.route((26, 16), W, [(4, 16), (4, 9)], (4, 9), E)

    # EOS. Both balanced and unclosed paths use the far-right clear column to
    # reach the already shared output roof.
    c.run(5, 9, "RM1-")
    c.set(9, 9, "X")
    c.set(9, 8, "r")
    c.route((9, 7), N, [(29, 7), (29, 0), (0, 0), (0, 1)], (0, 1), E)
    c.set(10, 9, "0")
    c.route((11, 9), E, [(29, 9), (29, 0), (0, 0), (0, 1)], (0, 1), E)

    # INIT: n -> BP; ring = [sentinel stack 1, position 1].
    c.run(0, 18, "@rb")
    c.horizontal(18, 3, 19)
    c.run(20, 18, "1s1s")
    c.route((24, 18), E, [(29, 18), (29, 16)], (28, 16), W)
    return c


def _worker_children(circuit: Circuit) -> list[Run | Joint]:
    joints = frozenset("<>^vVXdax")
    children: list[Run | Joint] = []
    ordered = sorted(circuit.cell.items(), key=lambda item: (item[0][1], item[0][0]))
    for node_id, ((x, y), glyph) in enumerate(ordered):
        if glyph == " ":
            continue
        gx, gy = x + WORKER_X + 1, y + WORKER_Y + 1
        if glyph in joints:
            children.append(Joint(id=node_id, x=gx, y=gy, glyph=glyph))
        else:
            children.append(Run(id=node_id, x=gx, y=gy, glyphs=glyph, heading="E"))
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
    worker = RoomNode(
        id=1,
        x=WORKER_X,
        y=WORKER_Y,
        kind="compute",
        w=IW,
        h=IH,
        children=_worker_children(_worker()),
        ports=[(1, 6), (1, 26), (5, 6), (16, 6)],
    )
    input_room = RoomNode(
        id=0,
        x=0,
        y=29,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=30, glyphs="I")],
        ports=[(1, 29)],
    )
    output_room = RoomNode(
        id=2,
        x=0,
        y=1,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=2, glyphs="O")],
        ports=[(1, 3)],
    )
    relay = RoomNode(
        id=3,
        x=RELAY_X,
        y=RELAY_Y,
        kind="compute",
        w=5,
        h=4,
        children=[
            Joint(id=0, x=22, y=1, glyph=">"),
            Run(id=1, x=23, y=1, glyphs="r", heading="E"),
            Joint(id=2, x=25, y=1, glyph="v"),
            Run(id=3, x=21, y=2, glyphs="@", heading="E"),
            Joint(id=4, x=22, y=2, glyph="^"),
            Joint(id=5, x=25, y=4, glyph="<"),
            Run(id=6, x=24, y=4, glyphs="s", heading="E"),
            Joint(id=7, x=22, y=4, glyph="^"),
        ],
        ports=[(20, 1), (20, 4)],
    )

    forward = [(5, y) for y in range(5, 0, -1)]
    forward += [(x, 1) for x in range(6, 20)]
    backward = [(x, 4) for x in range(19, 15, -1)]
    backward.append((16, 5))
    return Ast(
        rooms=[input_room, worker, output_room, relay],
        pipes=[
            _pipe(0, [(1, 28), (1, 27)], src=0, dst=1, entry=N, exit_=N, capacity=1),
            _pipe(
                1,
                [(1, 5), (1, 4)],
                src=1,
                dst=2,
                entry=N,
                exit_=N,
                capacity=1,
            ),
            _pipe(2, forward, src=1, dst=3, entry=N, exit_=E, capacity=2),
            _pipe(3, backward, src=3, dst=1, entry=W, exit_=S, capacity=2),
        ],
    )


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("brackets — snake-folded arithmetic parser")
    dbg.region(
        "snake worker",
        WORKER_X,
        WORKER_Y,
        IW + 2,
        IH + 2,
        note="Folded classifier, close arm, open arm, EOS, and shared returns.",
        tags=["compute", "ast", "snake"],
    )
    dbg.region(
        "register-ring relay",
        RELAY_X,
        RELAY_Y,
        7,
        6,
        note="Packed stack and position circulate above the folded worker.",
        tags=["stack", "memory"],
    )
    dbg.lane(
        "classifier",
        [(8, 19), (21, 19)],
        note="Arithmetic bracket-family and opener/closer classification.",
        kind="control",
    )
    dbg.lane(
        "folded pop",
        [(29, 19), (29, 12), (15, 12)],
        note="East, north, then west through the shared pop handler.",
        kind="control",
    )
    dbg.lane(
        "folded push",
        [(20, 21), (14, 21), (14, 22), (28, 22)],
        note="West through tag conversion, south, then east through push.",
        kind="control",
    )
    dbg.lane("output pipe", [(1, 5), (1, 4)], kind="pipe")
    dbg.lane("input pipe", [(1, 28), (1, 27)], kind="pipe")
    dbg.lane("ring forward", [(5, 5), (5, 1), (19, 1)], kind="pipe")
    dbg.lane("ring return", [(19, 4), (16, 4), (16, 5)], kind="pipe")
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
