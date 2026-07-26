#!/usr/bin/env python3
"""Dense 29-square AST parser for ``brackets``.

This keeps the snake parser's arithmetic and packed base-3 stack, but moves its
shared east-side control highway three columns inward.  Three independently
free worker rows are then removed with structural AST squashes.  Output remains
above the worker and input below it, so both dimensions finish at 29.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.brackets_snake import _worker_children
from randomfun2026solvers.brackets_snake import build_ast as build_snake_ast
from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Run, render
from randomfun2026solvers.manmoves import reglyph, try_squash

__all__ = ["build_ast", "build", "debug_map", "main"]

IW, IH = 27, 19
WORKER_ID = 1
INPUT_ID = 0
INPUT_PIPE_ID = 0


def _worker() -> Circuit:  # noqa: PLR0915 - one collision-checked control graph
    c = Circuit(IW, IH, strict_corridors=True)

    # Shared output roof.
    c.set(0, 1, ">")
    c.run(1, 1, "sH")

    # Classifier and close arm.  Its private ascent is x=25; the shared east
    # highway is x=26, preserving the original two-column separation.
    c.run(7, 12, "rM`32`W/W+N&")
    c.set(19, 12, "X")
    c.run(20, 12, "WM3W%")
    c.route((25, 12), E, [(25, 5)], (25, 5), W)
    c.run(24, 5, "MrWsWM3W/srsr~", d=W)
    c.set(10, 5, "X")
    c.set(9, 5, "r")
    c.set(8, 5, "X")

    # Wrong type: discard quotient, read position, and merge onto the roof.
    c.set(10, 4, ">")
    c.run(11, 4, "rr")
    c.route((13, 4), E, [(18, 4), (18, 0), (0, 0), (0, 1)], (0, 1), E)

    # Underflow uses the same east highway.
    c.set(7, 5, "r")
    c.set(9, 7, ">")  # merge point later reused by the EOS-unclosed path
    c.route(
        (6, 5),
        W,
        [(6, 7), (26, 7), (26, 0), (0, 0), (0, 1)],
        (0, 1),
        E,
    )

    # Valid pop folds down at x=1 and returns along the lower corridor.
    c.set(8, 4, "<")
    c.run(7, 4, "srM1+sm", d=W)
    c.route((0, 4), W, [(0, 17), (26, 17), (26, 16)], (25, 16), W)

    # Open arm: pull both its tag conversion and push three cells left.
    c.route((19, 13), S, [(19, 14), (16, 14)], (16, 14), W)
    c.run(15, 14, "WM3W%", d=W)
    c.set(10, 14, "v")
    c.set(10, 15, ">")
    c.run(11, 15, "MrW+++srM1+sm")
    c.route((24, 15), E, [(26, 15), (26, 16)], (25, 16), W)

    # Main loop.
    c.set(24, 16, "d")
    c.route((24, 15), N, [(24, 13), (6, 13), (6, 12)], (6, 12), E)
    c.route((23, 16), W, [(4, 16), (4, 9)], (4, 9), E)

    # EOS, sharing the compact east highway and output roof.
    c.run(5, 9, "RM1-")
    c.set(9, 9, "X")
    c.set(9, 8, "r")
    c.route((9, 7), N, [(26, 7), (26, 0), (0, 0), (0, 1)], (0, 1), E)
    c.set(10, 9, "0")
    c.route((11, 9), E, [(26, 9), (26, 0), (0, 0), (0, 1)], (0, 1), E)

    # INIT: n -> BP; ring = [sentinel stack 1, position 1].
    c.run(0, 18, "@rb")
    c.horizontal(18, 3, 16)
    c.run(17, 18, "1s1s")
    c.route((21, 18), E, [(26, 18), (26, 16)], (25, 16), W)
    return c


def _squash_worker_rows(ast: Ast) -> Ast:
    """Delete old local rows 11, 10, and 6, working bottom-up."""

    for global_y in (18, 17, 13):
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
    worker.ports = [
        (4, 6) if port == (5, 6) else port
        for port in worker.ports
    ]

    # The compact pop handler's first stack send is equidistant from the old
    # output and ring ports. Pull the ring source one column left to make its
    # intended binding strict while leaving the roof's output send unchanged.
    ring_forward = next(pipe for pipe in ast.pipes if pipe.id == 2)
    ring_forward.path = [(4, y) for y in range(5, 0, -1)]
    ring_forward.path += [(x, 1) for x in range(5, 20)]
    ring_forward.glyphs = reglyph(
        ring_forward.path,
        ring_forward.entry_dir,
        ring_forward.exit_dir,
    )
    ring_forward.x = 4
    ring_forward.y = 1

    ast = _squash_worker_rows(ast)

    # The worker's south wall moved from y=26 to y=23. Pull the input room and
    # its minimum-length pipe up by the same three rows.
    input_room = next(room for room in ast.rooms if room.id == INPUT_ID)
    input_room.y = 26
    input_room.children = [Run(id=0, x=1, y=27, glyphs="I")]
    input_room.ports = [(1, 26)]

    input_pipe = next(pipe for pipe in ast.pipes if pipe.id == INPUT_PIPE_ID)
    input_pipe.path = [(1, 25), (1, 24)]
    input_pipe.glyphs = ["^", "^"]
    input_pipe.x = 1
    input_pipe.y = 24
    return ast


def build() -> list[str]:
    return render(build_ast())


def debug_map() -> DebugMap:
    dbg = DebugMap("brackets — dense 29x29 arithmetic parser")
    dbg.region(
        "dense worker",
        0,
        6,
        29,
        18,
        note="Three-column highway fold plus three structural row squashes.",
        tags=["compute", "ast", "dense"],
    )
    dbg.region(
        "register-ring relay",
        20,
        0,
        7,
        6,
        note="Persistent packed stack and position.",
        tags=["stack", "memory"],
    )
    dbg.lane(
        "classifier",
        [(8, 16), (21, 16)],
        note="ASCII family and opener/closer classification.",
        kind="control",
    )
    dbg.lane(
        "folded pop",
        [(27, 16), (27, 12), (12, 12)],
        note="Compact east highway, then west through compare/pop.",
        kind="control",
    )
    dbg.lane(
        "folded push",
        [(20, 17), (20, 18), (11, 18), (25, 18), (25, 19)],
        note="Tag conversion and packed-stack push.",
        kind="control",
    )
    dbg.lane("output pipe", [(1, 5), (1, 4)], kind="pipe")
    dbg.lane("input pipe", [(1, 25), (1, 24)], kind="pipe")
    dbg.lane("ring forward", [(4, 5), (4, 1), (19, 1)], kind="pipe")
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
