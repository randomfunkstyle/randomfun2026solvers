#!/usr/bin/env python3
"""AST-authored 19x19 compaction of the packed multi-man TCP queue.

The imported 22x21 machine has a six-by-four splitter room, a larger packet
worker, and one phase-sensitive ring made from pipes 0, 3, and 4.  Its original
43-cell east serpentine makes the width 22.  This generator preserves the
machine's logic while changing its placement:

* shift the splitter and input rooms one column west;
* fold pipe 0 into the four-column east band;
* lengthen the two short ring legs just enough to preserve the tested phase;
* fold the worker's four-row tail into two rows.

The result is rendered from a refined :mod:`manast` tree, not edited ASCII.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, Refine, Run, parse_ast, render
from randomfun2026solvers.manmoves import hug_violations, reglyph
from randomfun2026solvers.manparse import parse_program

__all__ = ["build", "build_ast", "debug_map", "main"]

SPLITTER_ID = 0
OUTPUT_ID = 1
INPUT_ID = 2
WORKER_ID = 3

RING_PIPES = (0, 3, 4)
MIN_EAST_LEG = 29
MIN_RING_CAPACITY = 37

BASE_ROWS = (
    "        +------+>v>v>v",
    "        |v<    | |||||",
    "       >|@Usrsv| |||||",
    "+-+ +-+||s^   <| |||||",
    "|O| |I|||>  rs^| |||||",
    "+-+ +-+|+------+ |||||",
    " ^  v  |     ^   >^>^|",
    " ^  v  ^     ^ v-----<",
    "+-----------------+",
    "| @rbv>rsv  >v    |",
    "|   vY^sr<  rs    |",
    "|     ^     s     |",
    "|  >1+W      >r-Xv|",
    "|  s        ^+<v<r|",
    "|             Y<  |",
    "|   >        ^    |",
    "|      H          |",
    "|Hs N1<X-`61`W<   |",
    "|     ^<          |",
    "|  ^             <|",
    "+-----------------+",
)

EAST_RING_PATH = [
    (15, 0),
    (16, 0),
    (17, 0),
    (18, 0),
    (18, 1),
    (17, 1),
    (16, 1),
    (15, 1),
    (15, 2),
    (15, 3),
    (15, 4),
    (15, 5),
    (15, 6),
    (16, 6),
    (16, 5),
    (16, 4),
    (16, 3),
    (16, 2),
    (17, 2),
    (18, 2),
    (18, 3),
    (17, 3),
    (17, 4),
    (18, 4),
    (18, 5),
    (17, 5),
    (17, 6),
    (18, 6),
    (18, 7),
    (17, 7),
    (16, 7),
    (15, 7),
]


def _base_ast() -> Ast:
    source = "\n".join(BASE_ROWS) + "\n"
    return parse_ast(parse_program(source, bind=False), refine=Refine.BLOCKS)


def _set_pipe(ast: Ast, pipe_id: int, path: list[tuple[int, int]]) -> None:
    pipe = next(pipe for pipe in ast.pipes if pipe.id == pipe_id)
    pipe.path = list(path)
    pipe.glyphs = reglyph(pipe.path, pipe.entry_dir, pipe.exit_dir)
    pipe.x = min(x for x, _ in pipe.path)
    pipe.y = min(y for _, y in pipe.path)


def _fold_worker_tail(ast: Ast) -> None:
    """Fold four sparse worker rows into two while preserving its logic graph."""
    worker = next(room for room in ast.rooms if room.id == WORKER_ID)
    paint = {cell: glyph for child in worker.children for cell, glyph in child.paint().items()}
    moves = {
        (4, 15): (4, 14),
        (13, 15): (13, 14),
        (7, 16): (7, 13),
    }
    moves.update({(x, 17): (x, 15) for x in range(1, 18) if (x, 17) in paint})
    moves.update({(x, 18): (x, 16) for x in range(1, 18) if (x, 18) in paint})
    moves.update({(x, 19): (x, 17) for x in range(1, 18) if (x, 19) in paint})
    folded = {moves.get(cell, cell): glyph for cell, glyph in paint.items()}
    if len(folded) != len(paint):
        raise RuntimeError("worker-tail fold collided")

    joints = frozenset("<>^vVXdax")
    worker.children = [
        (
            Joint(id=node_id, x=x, y=y, glyph=glyph)
            if glyph in joints
            else Run(id=node_id, x=x, y=y, glyphs=glyph, heading="E")
        )
        for node_id, ((x, y), glyph) in enumerate(
            sorted(folded.items(), key=lambda item: (item[0][1], item[0][0]))
        )
    ]
    worker.h = 9


def build_ast() -> Ast:
    """Return the compact refined AST."""
    ast = _base_ast()
    rooms = {room.id: room for room in ast.rooms}

    rooms[SPLITTER_ID].translate(-1, 0)
    rooms[INPUT_ID].translate(-1, 0)

    _set_pipe(ast, 0, EAST_RING_PATH)
    _set_pipe(ast, 1, [(3, 6), (3, 7)])
    _set_pipe(ast, 3, [(7, 7), (7, 6), (6, 6), (6, 5), (6, 4), (6, 3), (6, 2)])
    _set_pipe(ast, 4, [(13, 7), (13, 6), (12, 6)])

    worker = rooms[WORKER_ID]
    worker.ports = [(15, 8), (3, 8), (1, 8), (7, 8), (13, 8)]
    _fold_worker_tail(ast)

    east = next(pipe for pipe in ast.pipes if pipe.id == 0)
    ring_capacity = sum(pipe.capacity for pipe in ast.pipes if pipe.id in RING_PIPES)
    if east.capacity < MIN_EAST_LEG or ring_capacity < MIN_RING_CAPACITY:
        raise RuntimeError(f"ring capacity regressed: east={east.capacity}, total={ring_capacity}")
    violations = hug_violations(ast)
    if violations:
        raise RuntimeError(violations[0])
    return ast


def build() -> list[str]:
    """Render the compact AST."""
    return render(build_ast())


def debug_map() -> DebugMap:
    """Describe the rooms, pipes, and compacted worker tail."""
    ast = build_ast()
    rooms = {room.id: room for room in ast.rooms}
    pipes = {pipe.id: pipe for pipe in ast.pipes}
    dbg = DebugMap("tcp — dense packed queue, AST 19-square")

    for room_id, name, color, note in (
        (
            SPLITTER_ID,
            "packet splitter",
            "#22c55e",
            "U splits the packed packet stream across the ring workers.",
        ),
        (INPUT_ID, "input", "#60a5fa", "Round-gated packet input."),
        (OUTPUT_ID, "output", "#a78bfa", "Ordered output stream."),
        (
            WORKER_ID,
            "packed queue worker",
            "#38bdf8",
            "Packed seq/value processing and curr-index validation.",
        ),
    ):
        room = rooms[room_id]
        dbg.region(
            name,
            room.x,
            room.y,
            *room.size,
            note=note,
            color=color,
            tags=["ast", "tcp"],
        )

    for pipe_id, name, color in (
        (0, "east queue leg", "#f59e0b"),
        (1, "packet input", "#60a5fa"),
        (2, "ordered output", "#a78bfa"),
        (3, "west queue return", "#f97316"),
        (4, "queue control return", "#facc15"),
    ):
        dbg.lane(name, pipes[pipe_id].path, kind="pipe", color=color)

    dbg.region(
        "folded validation tail",
        1,
        13,
        17,
        5,
        note="Delay check, loss output, and feedback loop folded from four tail rows to two.",
        color="#14b8a6",
        tags=["compact", "control"],
    )
    dbg.scenario(
        "block-reversed maximum queue",
        "16 15 115 / 14 114 / 13 113 / 12 112 / 11 111 / 10 110 / "
        "9 109 / 8 108 / 7 107 / 6 106 / 5 105 / 4 104 / 3 103 / "
        "2 102 / 1 101 / 0 100",
        0,
        20_000,
        watch=["east queue leg", "west queue return", "packed queue worker"],
        note="Fills the legal 15-packet window before packet zero drains it.",
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
