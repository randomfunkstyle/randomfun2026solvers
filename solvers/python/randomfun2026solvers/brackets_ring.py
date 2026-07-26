#!/usr/bin/env python3
"""General ``brackets`` parser: packed stack in a two-word register ring.

This machine is not an LM-1 CPU and does not embed test answers.  One worker
implements the parser directly.  Its persistent state is a tiny pipe ring::

    [stack, position]

``stack`` is a base-3 integer with sentinel 1 and tags 0/1/2.  At the legal
depth bound its largest value is ``2*3**32-1``, comfortably signed-64.  The
backpack holds the number of input characters still to process.  ``position``
starts at 1 and is incremented after each successful character, so it is already
the required answer for both a mismatch and unclosed openers at end-of-input.

The worker grid is laid out with :class:`~randomfun2026solvers.circuit.Circuit`
for collision-checked routing, then represented entirely as semantic
:mod:`randomfun2026solvers.manast` ``Run``/``Joint``/``RoomNode``/``PipeNode``
nodes.  The submission grid is rendered only through ``manast.render``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, W
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

IW, IH = 80, 58
HANDLER_X = 50
RETURN_X = 76
EOS_HANDLER_X = 65


def _worker() -> Circuit:  # noqa: PLR0915 - one explicitly routed state machine
    c = Circuit(IW, IH, strict_corridors=True)

    # INIT: n -> BP; ring = [sentinel stack 1, position 1].
    c.run(3, 56, "@rb")
    c.horizontal(56, 5, HANDLER_X)
    c.run(HANDLER_X, 56, "1s1s")
    c.route((54, 56), E, [(RETURN_X, 56)], (RETURN_X, 52), W)

    # LOOP test, entered heading west. Positive BP turns north into classification;
    # zero continues west and is routed to end-of-input handling.
    c.set(7, 52, "d")
    c.route((7, 51), N, [(7, 49), (3, 49)], (3, 46), E)
    c.route((6, 52), W, [(6, 54)], (EOS_HANDLER_X, 54), E)

    # Ordered exact-character classifier. At each X, equality continues east to
    # that character's handler; a larger ASCII value turns north to the next row.
    c.run(4, 46, "rM")  # A=B=current character; B survives the comparison staircase
    stages = [
        # (row, literal start, X column, ASCII, tag, opener?)
        (46, 6, 11, 40, 0, True),
        (40, 12, 17, 41, 0, False),
        (34, 18, 23, 91, 1, True),
        (28, 24, 29, 93, 1, False),
        (22, 30, 36, 123, 2, True),
        (16, 37, 43, 125, 2, False),
    ]
    for index, (y, start, branch_x, ascii_value, tag, opener) in enumerate(stages):
        c.run(start, y, f"`{ascii_value}`-")
        c.set(branch_x, y, "X")
        c.set(branch_x + 1, y, str(tag))
        c.horizontal(y, branch_x + 1, HANDLER_X)
        if opener:
            _push_handler(c, y)
        else:
            _pop_handler(c, y)
        if index + 1 < len(stages):
            next_y, next_start, *_ = stages[index + 1]
            c.route((branch_x, y - 1), N, [(branch_x, next_y)], (next_start - 1, next_y), E)

    # EOS: stack == sentinel -> 0; otherwise the ring's position is n+1.
    c.run(EOS_HANDLER_X, 54, "rM1-")
    eos_x = EOS_HANDLER_X + 4
    c.set(eos_x, 54, "X")
    # unclosed: negative branch north, read position, then emit it to the west
    c.set(eos_x, 53, "r")
    c.route((eos_x, 52), N, [(eos_x, 51)], (5, 51), W)
    c.run(5, 51, "sH", d=W)
    # balanced: zero branch east, materialize 0 and emit on its own row
    c.run(eos_x + 1, 54, "0")
    c.route((eos_x + 2, 54), E, [(eos_x + 2, 55)], (5, 55), W)
    c.run(5, 55, "sH", d=W)

    return c


def _push_handler(c: Circuit, y: int) -> None:
    """A=tag; stack := stack*3+tag; position++; BP--; return."""
    c.run(HANDLER_X, y, "MrW+++srM1+sm")
    end = HANDLER_X + len("MrW+++srM1+sm")
    c.route((end, y), E, [(RETURN_X, y)], (RETURN_X, 52), W)


def _pop_handler(c: Circuit, y: int) -> None:
    """A=tag; compare to stack%3, pop on match, or emit current position."""
    prefix = "MrWsWM3W/srsr~"
    c.run(HANDLER_X, y, prefix)
    match_x = HANDLER_X + len(prefix)
    c.set(match_x, y, "X")

    # Mismatch: ring=[quotient, position]. Drop quotient, emit position.
    c.set(match_x, y + 1, "<")
    c.run(match_x - 1, y + 1, "rr", d=W)
    c.horizontal(y + 1, match_x - 2, 5)
    c.run(5, y + 1, "sH", d=W)

    # Match: read quotient and reject quotient=0 (the sentinel was popped).
    c.set(match_x + 1, y, "r")
    quotient_x = match_x + 2
    c.set(quotient_x, y, "X")

    # Underflow: ring=[position].
    c.set(quotient_x + 1, y, "r")
    c.route(
        (quotient_x + 2, y),
        E,
        [(quotient_x + 2, y - 2)],
        (5, y - 2),
        W,
    )
    c.run(5, y - 2, "sH", d=W)

    # Valid pop: put quotient back, increment position, decrement BP, return.
    c.set(quotient_x, y + 1, ">")
    c.run(quotient_x + 1, y + 1, "srM1+sm")
    end = quotient_x + 1 + len("srM1+sm")
    c.route((end, y + 1), E, [(RETURN_X, y + 1)], (RETURN_X, 52), W)


def _worker_children(circuit: Circuit) -> list[Run | Joint]:
    """Turn collision-checked placed glyphs into editable AST leaves."""
    joints = frozenset("<>^vVXdax")
    children: list[Run | Joint] = []
    ordered_cells = sorted(
        circuit.cell.items(),
        key=lambda item: (item[0][1], item[0][0]),
    )
    for node_id, ((x, y), glyph) in enumerate(ordered_cells):
        if glyph == " ":
            continue
        if glyph in joints:
            children.append(Joint(id=node_id, x=x + 7, y=y + 1, glyph=glyph))
        else:
            children.append(Run(id=node_id, x=x + 7, y=y + 1, glyphs=glyph, heading="E"))
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
    """Build worker, I/O, relay, and the three-word-capacity register ring."""
    worker_circuit = _worker()
    worker = RoomNode(
        id=1,
        x=6,
        y=0,
        kind="compute",
        w=IW,
        h=IH,
        children=_worker_children(worker_circuit),
        ports=[(6, 57), (6, 31), (87, 21), (87, 24)],
    )
    input_room = RoomNode(
        id=0,
        x=0,
        y=56,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=57, glyphs="I")],
        ports=[(2, 57)],
    )
    output_room = RoomNode(
        id=2,
        x=0,
        y=30,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=1, y=31, glyphs="O")],
        ports=[(2, 31)],
    )
    relay = RoomNode(
        id=3,
        x=92,
        y=20,
        kind="compute",
        w=5,
        h=4,
        children=[
            Joint(id=0, x=94, y=21, glyph=">"),
            Run(id=1, x=95, y=21, glyphs="r", heading="E"),
            Joint(id=2, x=97, y=21, glyph="v"),
            Run(id=3, x=93, y=22, glyphs="@", heading="E"),
            Joint(id=4, x=94, y=22, glyph="^"),
            Joint(id=5, x=97, y=24, glyph="<"),
            Run(id=6, x=96, y=24, glyphs="s", heading="E"),
            Joint(id=7, x=94, y=24, glyph="^"),
        ],
        ports=[(92, 21), (92, 24)],
    )

    return Ast(
        rooms=[input_room, worker, output_room, relay],
        pipes=[
            _pipe(0, [(3, 57), (4, 57), (5, 57)], src=0, dst=1, direction=E, capacity=1),
            _pipe(1, [(5, 31), (4, 31), (3, 31)], src=1, dst=2, direction=W, capacity=1),
            _pipe(
                2,
                [(88, 21), (89, 21), (90, 21), (91, 21)],
                src=1,
                dst=3,
                direction=E,
                capacity=2,
            ),
            _pipe(
                3,
                [(91, 24), (90, 24), (89, 24), (88, 24)],
                src=3,
                dst=1,
                direction=W,
                capacity=2,
            ),
        ],
    )


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("brackets — general packed-stack parser with register ring")
    dbg.region(
        "worker",
        6,
        0,
        IW + 2,
        IH + 2,
        note="Direct parser; BP is remaining n, ring is [packed stack, position].",
        tags=["compute", "ast"],
    )
    dbg.region(
        "register-ring relay",
        92,
        20,
        7,
        6,
        note="One-value-at-a-time turnaround; never creates or duplicates payload.",
        tags=["stack", "memory"],
    )
    dbg.lane(
        "character classifier",
        [
            (18, 47),
            (18, 41),
            (24, 41),
            (24, 35),
            (30, 35),
            (30, 29),
            (36, 29),
            (36, 23),
            (43, 23),
            (43, 17),
            (50, 17),
        ],
        note="Ordered comparisons for ASCII 40,41,91,93,123,125.",
        kind="control",
    )
    dbg.lane("ring forward", [(88, 21), (91, 21)], kind="pipe")
    dbg.lane("ring return", [(91, 24), (88, 24)], kind="pipe")
    dbg.scenario(
        "last-position mismatch",
        "64 " + " ".join(map(str, [40] * 31 + [41] * 31 + [91, 41])),
        0,
        12_000,
        watch=["character classifier", "register-ring relay"],
        note="The final ')' mismatches '[' and emits 64.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    """Write the rendered AST and both debug sidecars together."""
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
