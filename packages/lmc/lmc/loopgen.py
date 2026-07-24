"""Counted-loop layout: build a 2D walkable trail for a BP counter loop.

Shape (validated on the reference as /tmp/cloop.man):

    prologue > body m d epilogue      <- row 0: sets BP, then loop body, test, exit
             ^ ....... <              <- row 1: return path back to the '>' entry

`d` turns clockwise (continue the loop) while BP > 0, else goes straight (east)
into the epilogue. This is do-while semantics: the body runs at least once.
"""

from __future__ import annotations

from .blockspec import Instr
from .trail import PlacedCell, TrailLayout


def counted_loop_trail(
    prologue: list[Instr], body: list[Instr], epilogue: list[Instr]
) -> TrailLayout:
    cells: list[PlacedCell] = []
    x = 0

    for ins in prologue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1

    gt_col = x
    cells.append(PlacedCell(x, 0, ">", None))  # loop entry (forces east heading)
    x += 1

    for ins in body:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1

    cells.append(PlacedCell(x, 0, "m", None))  # BP -= 1
    x += 1
    d_col = x
    cells.append(PlacedCell(x, 0, "d", None))  # test: BP>0 -> continue, else exit
    x += 1

    for ins in epilogue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1

    width = x

    # return row (y=1): '^' under the entry, '<' under d, dots between
    cells.append(PlacedCell(gt_col, 1, "^", None))
    for cx in range(gt_col + 1, d_col):
        cells.append(PlacedCell(cx, 1, ".", None))
    cells.append(PlacedCell(d_col, 1, "<", None))

    return TrailLayout(width=width, height=2, cells=cells, spawn=(0, 0))
