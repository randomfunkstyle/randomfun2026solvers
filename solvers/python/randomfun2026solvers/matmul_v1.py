#!/usr/bin/env python3
"""MAIN v2: every port on the top wall, so binding is by column.

With all ports on one wall the vertical term `(y - top)` is identical for every
one of them, so a pipe op binds whichever port is nearest **in column**, at any
row. Two consequences that pull opposite ways:

* a **vertical** body keeps every glyph in one column, so all of them bind the
  same port — right for repeated sends (`sWsWs` into `cmd`);
* a **horizontal** body walks across columns, so it can touch several ports — the
  only shape the MAC and the two fills can take.

`counted_loop_horizontal(x, y, body)` lays the body at row y+1 running **west**
from column `x+k`, so body[0] is the easternmost glyph. Solving the three bodies
for one consistent set of columns:

    MAC     "r  s*s"  x=15 -> r@21  _@20 _@19  s@18  *@17  s@16
    fill A  "rs"      x=18 -> r@20  s@19
    fill B  "r s"     x=17 -> r@20  _@19  s@18

which forces b_ret@21, in@20, a_fwd@19, b_fwd@18, prod@16 — and the MAC's two
blanks at 20/19 are exactly what puts `s(b_fwd)` on its own column while
`r(b_ret)` sits on its own. That is the same reason the hand-built machine's inner
loop has gaps, derived from the generator side instead of read off a grid.

`rk` sits at 24/25 rather than beside the other registers, because it is the one
register the drive loop touches on every (i, t): keeping it next to the MAC's
columns turns ~30 columns of travel per scalar into ~10, which is ~5k ticks on the
16x16x16 case.
"""

from __future__ import annotations

from .circuit import Circuit, E, N, S

__all__ = ["IN_PORTS", "OUT_PORTS", "Snake", "main_room"]

# ── port columns on the top wall ─────────────────────────────────────────────
RN_FWD, RN_RET, RM_FWD, RM_RET = 2, 3, 4, 5
PROD, STAR, B_FWD, A_FWD, IN, B_RET, A_RET, CMD, RK_RET, RK_FWD = (
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25)

OUT_PORTS = {"rn_fwd": RN_FWD, "rm_fwd": RM_FWD, "prod": PROD, "b_fwd": B_FWD,
             "a_fwd": A_FWD, "cmd": CMD, "rk_fwd": RK_FWD}
IN_PORTS = {"rn_ret": RN_RET, "rm_ret": RM_RET, "in": IN,
            "b_ret": B_RET, "a_ret": A_RET, "rk_ret": RK_RET}

IW, IH = 30, 46
MARKER_BUILD = "2M******"        # A = 128, no backtick literal


class Snake:
    """Walk a row east or west, dropping ops on named columns.

    Rows alternate direction, so within a row the columns an op needs must be
    monotonic in the walk direction; anything out of order costs a new row. Ops
    with no column ride the next cell along, which is what makes the arithmetic
    between pipe operations free.
    """

    def __init__(self, c: Circuit, x: int, y: int, left: int, right: int) -> None:
        self.c, self.x, self.y = c, x, y
        self.left, self.right = left, right
        self.d = 1

    def _turn(self) -> None:
        edge = self.right if self.d > 0 else self.left
        for fill in range(self.x + self.d, edge, self.d):
            self.c.set(fill, self.y, " ")
        self.c.set(edge, self.y, "v")
        self.y += 1
        self.d = -self.d
        self.c.turn(edge, self.y, E if self.d > 0 else (-1, 0))
        self.x = edge

    def op(self, glyph: str, col: int | None = None) -> None:
        if col is None:
            nxt = self.x + self.d
            if not self.left < nxt < self.right:
                self._turn()
                nxt = self.x + self.d
            self.c.set(nxt, self.y, glyph)
            self.x = nxt
            return
        if (col - self.x) * self.d <= 0:
            self._turn()
        while self.x + self.d != col:
            nxt = self.x + self.d
            if not self.left < nxt < self.right:
                self._turn()
                continue
            self.c.set(nxt, self.y, " ")
            self.x = nxt
        self.c.set(col, self.y, glyph)
        self.x = col

    def ops(self, glyphs: str) -> None:
        for g in glyphs:
            self.op(g)

    def to_row(self, y: int, col: int) -> None:
        """Drop straight down column `col` to row `y`, ending headed east."""
        self.op(" ", col)
        for fill in range(self.y + 1, y):
            self.c.set(col, fill, " ")
        self.c.set(col, self.y, "v")
        self.c.turn(col, y, E)
        self.x, self.y, self.d = col, y, 1


def main_room() -> tuple[Circuit, dict]:
    c = Circuit(IW + 2, IH + 2)
    c.set(1, 1, "@")
    s = Snake(c, 1, 1, 0, IW)

    # ── read N, M, K; B carries N across the second read so neither needs a slot
    s.op("r", IN); s.op("M")
    s.op("r", IN)
    s.op("s", RM_FWD)                      # M away
    s.op("W")                              # A=N, B=M
    s.op("s", RN_FWD)                      # N away
    s.op("r", IN); s.op("s", RK_FWD)       # K away

    # ── the ADDER's count words: BP=N, A=K, B=K1=(M-1)*K ─────────────────────
    s.op("r", RN_RET); s.op("b"); s.op("s", RN_FWD)
    s.ops("1M")
    s.op("r", RM_RET); s.op("s", RM_FWD); s.ops("-M")
    s.op("r", RK_RET); s.op("s", RK_FWD); s.ops("*M")
    s.op("r", RK_RET); s.op("s", RK_FWD)
    # vertical body: every glyph in column CMD, so all five bind `cmd`
    s.op(" ", CMD - 1)
    cx, cy = c.counted_loop(CMD - 1, s.y + 1, "sWsWs")
    c.vertical(CMD - 1, s.y, s.y + 1)
    s.x, s.y, s.d = cx, cy, 1
    c.turn(cx, cy, E)

    # ── fill ring A with N*M scalars, then its 128 end marker ────────────────
    s.op("r", RN_RET); s.op("M")
    s.op("r", RM_RET); s.op("s", RM_FWD); s.ops("*b")
    s.op(" ", A_FWD - 1)
    ax, ay = c.counted_loop_horizontal(A_FWD - 1, s.y + 1, "rs")
    c.vertical(A_FWD - 1, s.y, s.y + 1)
    s.x, s.y, s.d = ax, ay, 1
    c.turn(ax, ay, E)
    s.ops(MARKER_BUILD)
    s.op("s", A_FWD)

    # ── fill ring B with M*K values ──────────────────────────────────────────
    s.op("r", RM_RET); s.op("M")
    s.op("r", RK_RET); s.op("s", RK_FWD); s.ops("*b")
    s.op(" ", B_FWD - 1)
    bx, by = c.counted_loop_horizontal(B_FWD - 1, s.y + 1, "r s")
    c.vertical(B_FWD - 1, s.y, s.y + 1)

    # ── the drive loop ───────────────────────────────────────────────────────
    # The marker test may touch only A and BP: B is holding the scalar the MAC is
    # about to multiply by. `b` copies A into BP and seven `]` shift it, so
    # 128>>7 is 1 while 99>>7 is 0 and -99>>7 is -1.
    top = by + 2
    c.turn(bx, by, S)
    c.vertical(bx, by, top)
    c.turn(bx, top, (-1, 0))
    for x in range(1, bx):
        c.set(x, top, " ")
    c.set(1, top, "v")                        # the back-edge lands here too
    c.turn(1, top + 1, E)
    for x in range(2, A_RET):
        c.set(x, top + 1, " ")
    c.set(A_RET, top + 1, "r")                # the scalar, off ring A
    c.set(A_RET + 1, top + 1, "M")            # ... parked in B
    for x in range(A_RET + 2, IW):
        c.set(x, top + 1, " ")
    c.set(IW, top + 1, "v")
    c.turn(IW, top + 2, (-1, 0))
    for x in range(6, IW):
        c.set(x, top + 2, " ")
    c.set(5, top + 2, "v")
    c.turn(5, top + 3, E)
    c.set(6, top + 3, "b")                    # BP = scalar
    c.run(7, top + 3, "]]]]]]]", d=E)
    c.set(14, top + 3, "d")                   # marker -> south into H
    c.set(14, top + 4, "H")
    for x in range(15, RK_RET):
        c.set(x, top + 3, " ")
    c.set(RK_RET, top + 3, "r")               # BP = K, K straight back
    c.set(RK_FWD, top + 3, "s")
    c.set(RK_FWD + 1, top + 3, "b")
    for x in range(RK_FWD + 2, IW):
        c.set(x, top + 3, " ")
    c.set(IW, top + 3, "v")
    c.turn(IW, top + 4, (-1, 0))
    for x in range(16, IW):
        c.set(x, top + 4, " ")
    c.set(15, top + 4, "v")
    c.turn(15, top + 5, E)
    mx, my = c.counted_loop_horizontal(15, top + 5, "r  s*s")
    # back-edge: down, west along its own row, and up column 1 to the fetch
    c.turn(mx, my, S)
    c.turn(mx, my + 1, (-1, 0))
    for x in range(2, mx):
        c.set(x, my + 1, " ")
    c.set(1, my + 1, "^")
    for y in range(top + 1, my + 1):
        if c.get(1, y) == " ":
            c.set(1, y, " ")
    return c, {"marker_row": top + 3, "mac_row": top + 6}
