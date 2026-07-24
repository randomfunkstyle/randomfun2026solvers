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


def dispatch2(
    cond: list[Instr], then_block: TrailLayout, else_block: TrailLayout
) -> TrailLayout:
    """Two-way branch over whole *blocks* (unlike `if3`, whose arms are linear).

    `cond` runs first and must leave the test in BP (end it with `b`). Then `d` branches:
    BP>0 -> `then_block` (dips south, below else), BP==0 -> `else_block` (straight, row 0).
    Both arms run to a shared merge column, climb to row 0, and exit east -- so the block
    keeps the convention (enter (0,0) E, exit E row 0) and composes under `forever_loop`.

    Because each arm is a separate block that reads its own operands, nothing needs to be
    carried across the branch -- this is how memory's READ/WRITE dispatch avoids a register
    wall. Intended for men that route trivially (few pipes); it is pure control geometry.
    """
    cells: list[PlacedCell] = []

    def put(x, y, ch, pipe=None):
        cells.append(PlacedCell(x, y, ch, pipe))

    x = 0
    for ins in cond:
        put(x, 0, ins.char, ins.pipe)
        x += 1
    put(x, 0, ">")
    x += 1
    bcol = x
    put(x, 0, "d")           # BP>0 -> CW south (then) ; else straight east
    x += 1

    ex = x                   # else arm on row 0
    for c in else_block.cells:
        put(c.x + ex, c.y, c.char, c.pipe)
    else_exit = ex + else_block.width

    then_y = else_block.height + 1
    for y in range(1, then_y):          # drop from the branch down below the else arm
        put(bcol, y, "v")
    put(bcol, then_y, ">")
    tx = bcol + 1
    for c in then_block.cells:
        put(c.x + tx, c.y + then_y, c.char, c.pipe)
    then_exit = tx + then_block.width

    mx = max(else_exit, then_exit) + 1
    for xx in range(else_exit, mx):     # else -> east to MX on row 0
        put(xx, 0, ">")
    for xx in range(then_exit, mx):     # then -> east to MX on its row
        put(xx, then_y, ">")
    for y in range(1, then_y + 1):      # climb MX from then_y up to row 0
        put(mx, y, "^")
    put(mx, 0, ">")                     # merge -> exit east
    # height must cover the then_block's own dip (then_y .. then_y+then_h-1), not just then_y
    height = max(else_block.height, then_y + then_block.height)
    return TrailLayout(width=mx + 1, height=height, cells=cells, spawn=(0, 0))


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


def while_loop(
    prologue: list[Instr],
    test: list[Instr],
    body: TrailLayout,
    epilogue: list[Instr],
) -> TrailLayout:
    """Test-first loop that runs the body 0+ times (zero-trip).

        prologue > <test> epilogue        <- row 0
                 ^  > [BODY] v             <- body dips below; runs only if test passes
                 +----<------+

    `test` runs at the top of every iteration; its last op turns clockwise to
    continue (`d` on BP, `X` on A) -- clockwise dives south into the body, going
    straight exits east to the epilogue. The body must change the tested value
    (e.g. end with `m` for a BP loop) so the loop terminates.
    """
    cells: list[PlacedCell] = []
    x = 0
    for ins in prologue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    gt_col = x
    cells.append(PlacedCell(x, 0, ">", None))
    x += 1
    for ins in test:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    branch_col = x - 1  # last test op (d / X): CW -> body, straight -> exit
    row0_end = x
    for ins in epilogue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    row0_end = x

    # body dips below: enter at (branch_col, 1) heading south, turn east
    cells.append(PlacedCell(branch_col, 1, ">", None))
    bx0 = branch_col + 1
    for c in body.cells:
        cells.append(PlacedCell(c.x + bx0, c.y + 1, c.char, c.pipe))
    v_col = bx0 + body.width  # just past the body exit
    return_row = 1 + body.height
    cells.append(PlacedCell(v_col, 1, "v", None))  # turn south
    cells.append(PlacedCell(v_col, return_row, "<", None))  # turn west
    cells.append(PlacedCell(gt_col, return_row, "^", None))  # climb back to '>'

    width = max(row0_end, v_col + 1)
    height = return_row + 1
    return TrailLayout(width=width, height=height, cells=cells, spawn=(0, 0))


def if3(neg: list[Instr], zero: list[Instr], pos: list[Instr]) -> TrailLayout:
    """Three-way branch on the sign of A, arms merging back to one continuation.

        (0,0) v            <- entry: drop off row 0 so the north arm has room
        (0,1) > X <zero> > ^   <- X turns by sign(A); zero arm runs straight (row 1)
              > <neg> v            <- A<0: CCW north (row 0), then v back down
              > <pos> ^            <- A>0: CW south (row 2), then ^ back up

    The man enters at (0,0) heading east and a `v` drops him to the middle row where
    `X` sits (so the CCW/north arm lands on row 0, not off-grid). `X` reads A (sign):
    A<0 -> CCW (north, row 0), A>0 -> CW (south, row 2), A==0 -> straight (row 1).
    Each arm runs its linear body, then turns onto a shared `>` merge cell on row 1;
    a `>` reorients any entrant to east regardless of which side he came from. A short
    `^`/`>` tail lifts the merged path back to row 0, so the block keeps the standard
    convention: entered at (0,0) heading east, exits east on row 0.

    `X` does not change A and never touches B/BP, so a value parked in B (e.g. a
    running min) and any BP counter survive the branch -- arms may read/rewrite them.
    Caller stages the test value in A before the block. Give two arms the same body
    to collapse to a two-way branch (e.g. neg==zero for "strictly less" vs "keep").
    """
    cells: list[PlacedCell] = []
    cells.append(PlacedCell(0, 0, "v", None))  # entry drops to middle row
    cells.append(PlacedCell(0, 1, ">", None))
    cells.append(PlacedCell(1, 1, "X", None))  # branch on sign(A)

    body_len = max(len(neg), len(zero), len(pos))
    merge_x = 2 + body_len  # arms turn back to row 1 here

    cells.append(PlacedCell(1, 0, ">", None))  # neg arm (north, row 0)
    for i, ins in enumerate(neg):
        cells.append(PlacedCell(2 + i, 0, ins.char, ins.pipe))
    cells.append(PlacedCell(merge_x, 0, "v", None))  # drop to merge row

    cells.append(PlacedCell(1, 2, ">", None))  # pos arm (south, row 2)
    for i, ins in enumerate(pos):
        cells.append(PlacedCell(2 + i, 2, ins.char, ins.pipe))
    cells.append(PlacedCell(merge_x, 2, "^", None))  # climb to merge row

    for i, ins in enumerate(zero):  # zero arm (straight, row 1)
        cells.append(PlacedCell(2 + i, 1, ins.char, ins.pipe))

    cells.append(PlacedCell(merge_x, 1, ">", None))  # merge: any entrant -> east
    cells.append(PlacedCell(merge_x + 1, 1, "^", None))  # tail lifts back to row 0
    cells.append(PlacedCell(merge_x + 1, 0, ">", None))

    return TrailLayout(width=merge_x + 2, height=3, cells=cells, spawn=(0, 0))


def forever_loop(prologue: list[Instr], body: TrailLayout) -> TrailLayout:
    """`while True:` -- run the body forever (no test, no exit). Used for the
    outer round loop of stream programs, which never halt (they pass on output).

        prologue > [BODY] v
                  ^ ...... <
    """
    cells: list[PlacedCell] = []
    x = 0
    for ins in prologue:
        cells.append(PlacedCell(x, 0, ins.char, ins.pipe))
        x += 1
    gt_col = x
    cells.append(PlacedCell(x, 0, ">", None))
    x += 1
    bx0 = x
    for c in body.cells:
        cells.append(PlacedCell(c.x + bx0, c.y, c.char, c.pipe))
    v_col = bx0 + body.width
    return_row = body.height
    cells.append(PlacedCell(v_col, 0, "v", None))  # turn south after body
    cells.append(PlacedCell(v_col, return_row, "<", None))  # turn west
    cells.append(PlacedCell(gt_col, return_row, "^", None))  # climb back to '>'
    return TrailLayout(width=v_col + 1, height=return_row + 1, cells=cells, spawn=(0, 0))


# convenience wrappers ------------------------------------------------------

def counted_loop_trail(
    prologue: list[Instr], body: list[Instr], epilogue: list[Instr]
) -> TrailLayout:
    """A single BP counted loop with a linear body. Zero-trip (while semantics):
    the body runs 0 times if BP starts at 0. `prologue` must set BP."""
    return while_loop(prologue, [Instr("d")], linear_block([*body, Instr("m")]), epilogue)
