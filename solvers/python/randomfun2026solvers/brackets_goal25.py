#!/usr/bin/env python3
"""Goal-sized 25x25 AST parser for ``brackets``.

The dense parser's classifier moves four columns left, its pop update folds
down the west wall, and its shared east highway moves to x=22.  Five safe
worker-row squashes and one relay-row squash then leave a 23x14 worker inside a
25x25 complete machine.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.brackets_snake import _worker_children
from randomfun2026solvers.brackets_snake import build_ast as build_snake_ast
from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, Run, render
from randomfun2026solvers.manmoves import reglyph, try_squash

__all__ = ["build_ast", "build", "debug_map", "main"]

IW, IH = 23, 19
WORKER_ID = 1
INPUT_ID = 0
OUTPUT_ID = 2
RELAY_ID = 3


def _worker() -> Circuit:  # noqa: PLR0915 - one collision-checked control graph
    c = Circuit(IW, IH, strict_corridors=True)

    c.set(0, 1, ">")
    c.run(1, 1, "sH")

    # Classifier, shifted four columns left.
    c.run(3, 12, "rM`32`W/W+N&")
    c.set(15, 12, "X")
    c.run(16, 12, "WM3W%")
    c.route((21, 12), E, [(21, 5)], (21, 5), W)
    c.run(20, 5, "MrWsWM3W/srsr~", d=W)
    c.set(6, 5, "X")
    c.set(5, 5, "r")
    c.set(4, 5, "X")

    # Wrong type still has a short horizontal handler.
    c.set(6, 4, ">")
    c.run(7, 4, "rr")
    c.route((9, 4), E, [(14, 4), (14, 0), (0, 0), (0, 1)], (0, 1), E)

    # Underflow joins the common x=22 highway at the existing EOS merge.
    c.set(3, 5, "r")
    c.set(9, 7, ">")
    c.route(
        (2, 5),
        W,
        [(2, 7), (22, 7), (22, 0), (0, 0), (0, 1)],
        (0, 1),
        E,
    )

    # Valid pop: execute srM westward and 1+ downward, then place the stack send
    # directly under the ring attachment before returning along row 9.
    c.set(4, 4, "<")
    c.run(3, 4, "srM", d=W)
    c.set(0, 4, "v")
    c.run(0, 5, "1+", d=S)
    c.route((0, 7), S, [(0, 8)], (3, 8), E)
    c.run(3, 8, "sm")
    c.route(
        (5, 8),
        E,
        [(5, 9), (0, 9), (0, 17), (22, 17), (22, 16)],
        (22, 16),
        W,
    )

    # Open arm and push, both shifted into the 23-column worker.
    c.route((15, 13), S, [(15, 14), (13, 14)], (13, 14), W)
    c.run(12, 14, "WM3W%", d=W)
    c.set(7, 14, "v")
    c.set(7, 15, ">")
    c.run(8, 15, "MrW+++srM1+sm")
    c.route((21, 15), E, [(22, 15), (22, 16)], (22, 16), W)

    # Main loop returns to the classifier's new x=3 entry.
    c.set(21, 16, "d")
    c.route((21, 15), N, [(21, 13), (2, 13), (2, 12)], (2, 12), E)
    c.route((20, 16), W, [(1, 16), (1, 10), (4, 10)], (4, 10), E)

    # EOS, one row lower to leave row 9 to the folded-pop return.
    c.run(5, 10, "RM1-")
    c.set(9, 10, "X")
    c.set(9, 9, "r")
    c.route(
        (9, 8),
        N,
        [(9, 7), (22, 7), (22, 0), (0, 0), (0, 1)],
        (0, 1),
        E,
    )
    c.set(10, 10, "0")
    c.route((11, 10), E, [(22, 10), (22, 0), (0, 0), (0, 1)], (0, 1), E)

    # INIT.
    c.run(0, 18, "@rb")
    c.horizontal(18, 3, 12)
    c.run(13, 18, "1s1s")
    c.route((17, 18), E, [(22, 18), (22, 16)], (22, 16), W)
    return c


def _set_pipe(pipe, path: list[tuple[int, int]]) -> None:
    pipe.path = path
    pipe.glyphs = reglyph(path, pipe.entry_dir, pipe.exit_dir)
    pipe.x = min(x for x, _ in path)
    pipe.y = min(y for _, y in path)


def _squash_worker_rows(ast: Ast) -> Ast:
    # Original local rows 11, 3, and 2, always removed bottom-up.  Row 10 now
    # hosts EOS, while the side-fed input lets the worker remain one row taller.
    for global_y in (18, 10, 9):
        squashed, report = try_squash(
            ast,
            WORKER_ID,
            "row",
            global_y,
            capacity={(2, 3): 2},
            reroute=False,
        )
        if squashed is None:
            raise RuntimeError(f"cannot squash worker row {global_y}: {report}")
        ast = squashed
    return ast


def build_ast() -> Ast:
    ast = build_snake_ast()
    worker = next(room for room in ast.rooms if room.id == WORKER_ID)
    worker.w = IW
    worker.children = _worker_children(_worker())

    ast = _squash_worker_rows(ast)
    worker = next(room for room in ast.rooms if room.id == WORKER_ID)
    worker.translate(0, -2)
    worker.ports = [(3, 4), (1, 21), (4, 4), (10, 4)]

    # One-incoming-pipe relay: U combines receive + "turn away from the left
    # pipe", replacing the old r/turn pair and fitting the repeater in 4x2.
    relay = next(room for room in ast.rooms if room.id == RELAY_ID)
    relay.x = 19
    relay.y = 0
    relay.w = 4
    relay.h = 2
    relay.children = [
        Run(id=0, x=21, y=1, glyphs="U"),
        Joint(id=1, x=23, y=1, glyph="v"),
        Run(id=2, x=20, y=2, glyphs="@"),
        Joint(id=3, x=21, y=2, glyph="^"),
        Run(id=4, x=22, y=2, glyphs="s"),
        Joint(id=5, x=23, y=2, glyph="<"),
    ]
    relay.ports = [(19, 1), (19, 2)]

    output_room = next(room for room in ast.rooms if room.id == OUTPUT_ID)
    output_room.y = 0
    output_room.children = [Run(id=0, x=1, y=1, glyphs="O")]
    output_room.ports = [(2, 2)]

    input_room = next(room for room in ast.rooms if room.id == INPUT_ID)
    input_room.x = 5
    input_room.y = 22
    input_room.children = [Run(id=0, x=6, y=23, glyphs="I")]
    input_room.ports = [(5, 23)]

    by_id = {pipe.id: pipe for pipe in ast.pipes}
    by_id[0].entry_dir = W
    by_id[0].exit_dir = N
    _set_pipe(
        by_id[0],
        [
            (4, 23),
            (3, 23),
            (3, 22),
            (2, 22),
            (1, 22),
        ],
    )
    by_id[1].entry_dir = N
    by_id[1].exit_dir = W
    _set_pipe(by_id[1], [(3, 3), (3, 2)])
    _set_pipe(
        by_id[2],
        [(4, y) for y in range(3, 0, -1)]
        + [(x, 1) for x in range(5, 19)],
    )
    _set_pipe(
        by_id[3],
        [(x, 2) for x in range(18, 9, -1)] + [(10, 3)],
    )
    return ast


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("brackets — goal 25x25 arithmetic parser")
    dbg.region(
        "goal-sized worker",
        0,
        4,
        25,
        18,
        note="23x16 interior with west-folded pop and three AST row squashes.",
        tags=["compute", "ast", "goal25"],
    )
    dbg.region(
        "compressed relay",
        19,
        0,
        6,
        4,
        note="4x2 U-based packed-stack repeater.",
        tags=["stack", "memory"],
    )
    dbg.lane(
        "classifier",
        [(4, 11), (17, 11)],
        note="Shifted ASCII family/opener classifier.",
        kind="control",
    )
    dbg.lane(
        "west-folded pop",
        [(22, 8), (5, 8), (1, 8), (1, 9), (23, 9)],
        note="Compare westward; finish the pop update down the west wall.",
        kind="control",
    )
    dbg.lane(
        "folded push",
        [(16, 13), (8, 13), (8, 14), (22, 14), (22, 15)],
        note="Compact tag conversion and packed-stack push.",
        kind="control",
    )
    dbg.lane("output pipe", [(3, 3), (3, 2)], kind="pipe")
    dbg.lane(
        "input pipe",
        [(4, 23), (3, 23), (3, 22), (1, 22)],
        kind="pipe",
    )
    dbg.lane("ring forward", [(4, 3), (4, 1), (18, 1)], kind="pipe")
    dbg.lane("ring return", [(18, 2), (10, 2), (10, 3)], kind="pipe")
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
