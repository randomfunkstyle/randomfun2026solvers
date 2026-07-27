#!/usr/bin/env python3
"""Parallel value-carrying-men prototype for ``reverse-a-list``.

The controller reads one value and splits.  One child continues reading while
the other keeps the value in A and delays on one of two eighteen-tick circuits.
The value's remaining-count rank is in BP.  Parity selects a circuit, then ``]``
halves the rank so a carrier waits only one lap per *pair* of positions.  A
nine-tick branch skew interleaves the two circuits in exact reverse order.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import E, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import hug_violations, reglyph

__all__ = ["build", "build_ast", "debug_map", "main"]

WORKER_ID = 0
INPUT_ID = 1
OUTPUT_ID = 2
IW = 8
IH = 13
CONTROLLER_PERIOD = 8
DELAY_PERIOD = 18
PARITY_SKEW = 9
MAX_LIST_LENGTH = 16


def _worker_cells() -> dict[tuple[int, int], str]:
    cells: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, glyph: str) -> None:
        old = cells.setdefault((x, y), glyph)
        if old != glyph:
            raise RuntimeError(f"collision at {(x, y)}: {old!r} != {glyph!r}")

    def loop(ox: int) -> None:
        # An eighteen-cell circuit in a 4x5 box.  A value carrier enters at the
        # bottom, decrements BP at the far corner, and `a` exits south at zero.
        for x, y, glyph in (
            (0, 0, "v"),
            (0, 4, "a"),
            (1, 4, "m"),
            (2, 4, ">"),
            (3, 4, "^"),
            (3, 3, "<"),
            (1, 3, "^"),
            (1, 2, ">"),
            (3, 2, "^"),
            (3, 1, "<"),
            (1, 1, "^"),
            (1, 0, "<"),
        ):
            put(ox + x, y, glyph)

    loop(0)
    loop(4)

    # Zero exits from the two delay circuits.
    for x, y, glyph in (
        (0, 5, ">"),
        (1, 5, "v"),
        (1, 6, "s"),
        (1, 7, "H"),
        (4, 5, ">"),
        (5, 5, "s"),
        (6, 5, "H"),
    ):
        put(x, y, glyph)

    # Storage child: BP parity selects a circuit.  The longer odd path is
    # exactly nine ticks behind the even path; with BP halved, that interleaves
    # ranks 1,2,3,... while same-parity carriers stay sixteen ticks apart.
    for x, y, glyph in (
        (4, 8, "x"),
        (3, 8, "]"),
        (2, 8, "^"),
        (2, 4, ">"),
        (5, 8, "]"),
        (6, 8, "v"),
        (6, 9, "<"),
        (5, 9, "v"),
        (5, 10, "v"),
        (5, 11, ">"),
        (7, 11, "^"),
        (7, 10, "^"),
        (7, 7, "<"),
        (6, 7, "^"),
        (6, 6, ">"),
        (7, 6, "^"),
        (7, 4, "^"),
    ):
        put(x, y, glyph)

    # Controller.  Y is entered eastward: its north child stores the value,
    # while its south child decrements the remaining count and reads the next.
    for x, y, glyph in (
        (4, 9, "Y"),
        (4, 10, "m"),
        (4, 11, "d"),
        (3, 11, "r"),
        (2, 11, "^"),
        (2, 9, ">"),
        (0, 9, ">"),
        (0, 12, "^"),
        (1, 12, "r"),
        (2, 12, "b"),
        (4, 12, "U"),
        (3, 12, "@"),
    ):
        put(x, y, glyph)
    return cells


def _children() -> list[Run | Joint]:
    joints = frozenset("<>^vVXdaxY")
    out: list[Run | Joint] = []
    for node_id, ((x, y), glyph) in enumerate(
        sorted(_worker_cells().items(), key=lambda item: (item[0][1], item[0][0]))
    ):
        gx, gy = 1 + x, 1 + y
        if glyph in joints:
            out.append(Joint(id=node_id, x=gx, y=gy, glyph=glyph))
        else:
            out.append(Run(id=node_id, x=gx, y=gy, glyphs=glyph, heading="E"))
    return out


def _pipe(
    node_id: int,
    path: list[tuple[int, int]],
    *,
    src: int,
    dst: int,
    direction: tuple[int, int],
) -> PipeNode:
    return PipeNode(
        id=node_id,
        x=min(x for x, _ in path),
        y=min(y for _, y in path),
        path=path,
        glyphs=reglyph(path, direction, direction),
        src=src,
        dst=dst,
        min_capacity=2,
        entry_dir=direction,
        exit_dir=direction,
    )


def build_ast() -> Ast:
    worker = RoomNode(
        id=WORKER_ID,
        x=0,
        y=0,
        kind="compute",
        w=IW,
        h=IH,
        children=_children(),
        ports=[(9, 13), (9, 6)],
    )
    input_room = RoomNode(
        id=INPUT_ID,
        x=12,
        y=12,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=13, y=13, glyphs="I")],
        ports=[(12, 13)],
    )
    output_room = RoomNode(
        id=OUTPUT_ID,
        x=12,
        y=5,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=13, y=6, glyphs="O")],
        ports=[(12, 6)],
    )
    input_pipe = _pipe(
        0,
        [(11, 13), (10, 13)],
        src=INPUT_ID,
        dst=WORKER_ID,
        direction=W,
    )
    output_pipe = _pipe(
        1,
        [(10, 6), (11, 6)],
        src=WORKER_ID,
        dst=OUTPUT_ID,
        direction=E,
    )
    ast = Ast(
        rooms=[worker, input_room, output_room],
        pipes=[input_pipe, output_pipe],
    )
    violations = hug_violations(ast)
    if violations:
        raise RuntimeError(violations[0])
    return ast


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("reverse-a-list — parallel value carriers")
    dbg.region(
        "worker",
        0,
        0,
        10,
        15,
        note="Controller and two collision-free value-carrier delay circuits.",
        color="#38bdf8",
        tags=["ast", "parallel"],
    )
    dbg.region(
        "even-rank delay",
        1,
        1,
        4,
        5,
        note="An 18-tick circuit; ] halves rank and m counts paired positions.",
        color="#22c55e",
        tags=["carrier", "delay"],
    )
    dbg.region(
        "odd-rank delay",
        5,
        1,
        4,
        5,
        note="Its nine-tick path skew interleaves odd ranks between even ranks.",
        color="#f59e0b",
        tags=["carrier", "delay"],
    )
    dbg.region(
        "parity dispatcher",
        3,
        5,
        6,
        5,
        note="x selects a circuit and ] halves the remaining-rank delay.",
        color="#a78bfa",
        tags=["split", "collision"],
    )
    dbg.region(
        "controller",
        1,
        10,
        7,
        4,
        note="Y retains one controller and creates one A-carrying worker per value.",
        color="#fb7185",
        tags=["split", "input"],
    )
    dbg.region(
        "input",
        12,
        12,
        3,
        3,
        note="Length-prefixed list input.",
        color="#60a5fa",
    )
    dbg.region(
        "output",
        12,
        5,
        3,
        3,
        note="The delayed carriers arrive in descending rank order.",
        color="#f472b6",
    )
    dbg.lane(
        "input pipe",
        [(11, 13), (10, 13)],
        kind="pipe",
        color="#60a5fa",
    )
    dbg.lane(
        "output pipe",
        [(10, 6), (11, 6)],
        kind="pipe",
        color="#f472b6",
    )
    dbg.scenario(
        "full sixteen-value reverse",
        "16 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15",
        0,
        200,
        watch=[
            "controller",
            "parity dispatcher",
            "even-rank delay",
            "odd-rank delay",
        ],
        note="Sixteen carriers remain collision-free and emit 15 down to 0.",
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
