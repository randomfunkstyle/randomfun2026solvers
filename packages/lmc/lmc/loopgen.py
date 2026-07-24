"""Block-structured 2D layout: linear blocks, horizontal sequencing, and loops.

A *block* is a TrailLayout the man enters at (0,0) heading east and leaves at the
right edge of row 0 heading east. Blocks compose:

- `linear_block(instrs)`  — one row of instructions.
- `seq_block([...])`      — blocks side by side; the man flows left to right.
- `loop_wrap(...)`        — wrap a body block in a loop with a return lane.

Loop shape (validated as /tmp/cloop.man for the counted case):

    prologue > [BODY BLOCK] <test> epilogue     <- row 0
             ^ ............. <                   <- return row, below the body

`<test>` ends in a clockwise-on-continue op: `d` (continue while BP>0) for counted
loops, or `X` (continue while A>0) for value loops whose counter lives in B. The
return lane drops down the `test` column (right of the body), runs west below the
body, and climbs the entry column (left of the body) back to `>`. Both lanes are
outside the body's columns, so nesting never collides -- and because a value loop
uses B+X while a counted loop uses BP, an outer value-loop can nest a counted
inner-loop without a counter conflict.
"""

from __future__ import annotations

from .blockspec import Instr
from .trail import PlacedCell, TrailLayout


def linear_block(instrs: list[Instr]) -> TrailLayout:
    cells = [PlacedCell(i, 0, ins.char, ins.pipe) for i, ins in enumerate(instrs)]
    return TrailLayout(width=len(instrs), height=1, cells=cells, spawn=(0, 0))


def seq_block(blocks: list[TrailLayout]) -> TrailLayout:
    cells: list[PlacedCell] = []
    x = 0
    height = 1
    for b in blocks:
        for c in b.cells:
            cells.append(PlacedCell(c.x + x, c.y, c.char, c.pipe))
        x += b.width
        height = max(height, b.height)
    return TrailLayout(width=x, height=height, cells=cells, spawn=(0, 0))


def loop_wrap(
    prologue: list[Instr],
    body: TrailLayout,
    test: list[Instr],
    epilogue: list[Instr],
) -> TrailLayout:
    """Wrap `body` in a loop. `test` runs after the body each iteration and its
    last op must turn clockwise to continue (d for BP, X for a value in A)."""
    cells: list[PlacedCell] = []
    x = 0

    for ins in prologue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1

    gt_col = x
    cells.append(PlacedCell(x, 0, ">", None))  # loop entry
    x += 1

    body_x0 = x
    for c in body.cells:
        cells.append(PlacedCell(c.x + body_x0, c.y, c.char, c.pipe))
    x = body_x0 + body.width

    for ins in test:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    d_col = x - 1  # the clockwise-continue op

    for ins in epilogue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    width = x

    return_row = body.height  # one row below the body
    cells.append(PlacedCell(gt_col, return_row, "^", None))  # climb back up
    cells.append(PlacedCell(d_col, return_row, "<", None))  # turn west
    height = return_row + 1
    return TrailLayout(width=width, height=height, cells=cells, spawn=(0, 0))


# convenience wrappers ------------------------------------------------------

def counted_loop_trail(
    prologue: list[Instr], body: list[Instr], epilogue: list[Instr]
) -> TrailLayout:
    """A single BP counted loop with a linear body (do-while, body runs >=1)."""
    return loop_wrap(prologue, linear_block(body), [Instr("m"), Instr("d")], epilogue)
