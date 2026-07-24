"""Hand-assembled programs built from the block abstractions.

These are the codegen *targets* — what the Python frontend will eventually emit —
and they double as end-to-end tests of the loop/ring/router stack.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Instr, N, Pipe, S, W
from .loopgen import linear_block, seq_block, while_loop
from .trail import TrailLayout

Op = Instr


def reverse_program() -> tuple[BlockGraph, TrailLayout]:
    """reverse_list, one round: read n, then n values; emit them reversed.

    A 4-pipe CPU (I, O, ring up/down). The outer emit-loop's counter `rem` lives
    in B (ring r/s only touch A, so B survives); the inner rotate-loop uses BP.
    Emits x[n-1], x[n-2], ..., x[0] via rotate-(rem-1)-then-extract.
    """
    push = while_loop(
        prologue=[Op("@"), Op("r", "in"), Op("M"), Op("b")],  # A=n, B=n(rem), BP=n
        test=[Op("d")],
        body=linear_block([Op("r", "in"), Op("s", "up"), Op("m")]),  # read, push, BP--
        epilogue=[],
    )
    rotate = while_loop(  # rotate BP(=rem-1) times; zero-trip
        prologue=[],
        test=[Op("d")],
        body=linear_block([Op("r", "down"), Op("s", "up"), Op("m")]),
        epilogue=[],
    )
    emit = while_loop(
        prologue=[],
        test=[Op("W"), Op("M"), Op("X")],  # A=rem, B=rem, continue while rem>0
        body=seq_block(
            [
                linear_block([Op("b"), Op("m")]),  # BP = rem-1
                rotate,
                linear_block([Op("r", "down"), Op("s", "out")]),  # extract head, emit
                linear_block([Op("W"), Op("M"), Op("1"), Op("-"), Op("N"), Op("M")]),  # rem--
            ]
        ),
        epilogue=[Op("H")],
    )
    program = seq_block([push, emit])

    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output", "BUF": "buf"}
    g.pipes = [
        Pipe("in", "I", E, "CPU", W),
        Pipe("out", "CPU", E, "O", W),
        Pipe("up", "CPU", N, "BUF", S),
        Pipe("down", "BUF", S, "CPU", N),
    ]
    return g, program
