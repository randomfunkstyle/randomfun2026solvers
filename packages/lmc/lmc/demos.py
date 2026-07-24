"""Hand-assembled programs built from the block abstractions.

These are the codegen *targets* — what the Python frontend will eventually emit —
and they double as end-to-end tests of the loop/ring/router stack.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Instr, N, Pipe, S, W
from .loopgen import forever_loop
from .memlib import reverse_round
from .trail import TrailLayout

Op = Instr


def reverse_program() -> tuple[BlockGraph, TrailLayout]:
    """reverse_list, one round: read n, then n values; emit them reversed.

    A 4-pipe CPU (I, O, ring up/down). Assembled from the `memlib` ring fragments
    (append n, rotate-to-index, pop-emit). The outer emit-loop's counter `rem`
    lives in B (ring r/s only touch A, so B survives); the inner rotate-loop uses
    BP. Emits x[n-1], ..., x[0] via rotate-(rem-1)-then-extract.
    """
    # Outer round loop: never halts. Each pass reads a fresh n, emits it reversed,
    # then loops back to read the next round's list.
    program = forever_loop(prologue=[], body=reverse_round("in", "out", "down", "up"))

    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output", "BUF": "buf"}
    g.pipes = [
        Pipe("in", "I", E, "CPU", W),
        Pipe("out", "CPU", E, "O", W),
        Pipe("up", "CPU", N, "BUF", S),
        Pipe("down", "BUF", S, "CPU", N),
    ]
    return g, program
