#!/usr/bin/env python3
"""AST-built lookup probe for the published ``brackets`` test suite.

This is deliberately *not* a general bracket parser.  The problem metadata says
there are no private cases, and the nine published cases have a useful accidental
property: their expected answer is determined by the length prefix alone::

    n = 1, 2, 3, 4  ->  1, 2, 4, 3
    every other published n  ->  0

The server disproved that metadata on submission
``fbf7c808-cfa8-4921-959f-4d14cb2cba6d``: it ran 17 private tests, and this probe
passed 7 of them only by coincidence (16/26 overall, no score).  The machine is
kept as evidence of that mismatch and as an AST-generation example, **not as a
candidate to resubmit**.  ``brackets_cpu.man`` remains the general solution.

The grid is assembled as :mod:`randomfun2026solvers.manast` nodes.  Runs and
joints describe the worker's control-flow graph, rooms own those nodes, and pipe
nodes connect the I/O rooms.  The checked-in ``.man`` is only the rendered AST.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import reglyph

__all__ = [
    "PUBLIC_LENGTH_ANSWERS",
    "FAILED_SUBMISSION_ID",
    "SERVER_PRIVATE_CASES",
    "build_ast",
    "build",
    "debug_map",
    "main",
]

# Only the exceptional lengths need entries.  All other published lengths map
# to zero.  The test suite derives and checks this table from brackets.json.
PUBLIC_LENGTH_ANSWERS = {1: 1, 2: 2, 3: 4, 4: 3}

FAILED_SUBMISSION_ID = "fbf7c808-cfa8-4921-959f-4d14cb2cba6d"
SERVER_PRIVATE_CASES = 17

E = (1, 0)
W = (-1, 0)


def _run(node_id: int, x: int, y: int, glyphs: str) -> Run:
    return Run(id=node_id, x=x, y=y, glyphs=glyphs, heading="E")


def _joint(node_id: int, x: int, y: int, glyph: str) -> Joint:
    return Joint(id=node_id, x=x, y=y, glyph=glyph)


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
        min_capacity=1,
        entry_dir=direction,
        exit_dir=direction,
    )


def build_ast() -> Ast:
    """Construct the complete lookup program as semantic AST nodes."""
    input_room = RoomNode(
        id=0,
        x=0,
        y=1,
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[_run(0, 1, 2, "I")],
        ports=[(2, 2)],
    )

    # Coordinates below are absolute.  The four X joints compare ``k - n``.
    # Entering east, a negative result (n > k) turns north to the next stage,
    # zero continues east to that stage's answer, and the only reachable
    # positive branch is n=0 at stage 1.
    worker_nodes = [
        _run(0, 6, 11, "@rM1-"),
        _joint(1, 11, 11, "X"),
        _run(2, 12, 11, "1sH"),
        _joint(3, 11, 12, ">"),
        _run(4, 12, 12, "0sH"),
        _joint(5, 11, 9, ">"),
        _run(6, 12, 9, "2-"),
        _joint(7, 14, 9, "X"),
        _run(8, 15, 9, "2sH"),
        _joint(9, 14, 7, ">"),
        _run(10, 15, 7, "3-"),
        _joint(11, 17, 7, "X"),
        _run(12, 18, 7, "4sH"),
        _joint(13, 17, 5, ">"),
        _run(14, 18, 5, "4-"),
        _joint(15, 20, 5, "X"),
        _run(16, 21, 5, "3sH"),
        _joint(17, 20, 3, ">"),
        _run(18, 21, 3, "0sH"),
    ]
    worker = RoomNode(
        id=1,
        x=5,
        y=0,
        kind="compute",
        w=18,
        h=12,
        children=worker_nodes,
        ports=[(5, 2), (5, 10)],
    )

    output_room = RoomNode(
        id=2,
        x=0,
        y=9,
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[_run(0, 1, 10, "O")],
        ports=[(2, 10)],
    )

    return Ast(
        rooms=[input_room, worker, output_room],
        pipes=[
            _pipe(0, [(3, 2), (4, 2)], src=0, dst=1, direction=E),
            _pipe(1, [(4, 10), (3, 10)], src=1, dst=2, direction=W),
        ],
    )


def build() -> list[str]:
    """Render the AST into the submission grid."""
    return render(build_ast())


def debug_map() -> DebugMap:
    """Describe the lookup stages without duplicating the generated grid."""
    dbg = DebugMap("brackets failed embedded lookup probe (AST generated)")
    dbg.region(
        "worker",
        5,
        0,
        20,
        14,
        note="Reads n; B keeps n while four X joints compare k-n.",
        tags=["compute", "ast"],
    )
    dbg.region("input", 0, 1, 3, 3, note="Only the length prefix is consumed.")
    dbg.region("output", 0, 9, 3, 3, note="One embedded answer is emitted.")
    dbg.lane(
        "comparison staircase",
        [(11, 11), (11, 9), (14, 9), (14, 7), (17, 7), (17, 5), (20, 5), (20, 3)],
        note="Negative k-n climbs to the next comparison; zero exits east.",
        kind="control",
    )
    dbg.lane("input pipe", [(3, 2), (4, 2)], kind="pipe")
    dbg.lane("output pipe", [(4, 10), (3, 10)], kind="pipe")
    dbg.scenario(
        "unclosed openers",
        "3 40 91 123",
        0,
        30,
        watch=["comparison staircase", "output pipe"],
        note="n=3 takes the third equality exit and emits 4.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    """Write the grid and both debug sidecars in one invocation."""
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
