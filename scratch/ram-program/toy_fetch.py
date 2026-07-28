#!/usr/bin/env python3
"""Toy A: the stored-program fetch pipeline, standalone.

ROM drum --(boot copy)--> men-v3 store; then a PC-holding fetcher demand-reads
instruction words. Commands arrive from the input room (standing in for the
CPU): 0 = "next" (pc += 2), t > 0 = "jump" (pc = t). Each command fetches the
two words at (pc, pc+1); the store's answers flow straight to the output room.

Program image: P words, word j stored at address j+1 (address 0 unused so a
jump target is always > 0 and the first command can be "jump to 1").
Value at address a is 200 + a, so the output stream is self-describing.

Loader and fetcher are ONE man in one room (LDR): phase 1 is a counted loop
copying ROM -> store (all writes strictly precede all reads: same man, same
pipe, no race); phase 2 is the fetch loop. Phase-1 `r` sits near the ROM pipe
(west wall row 2), phase-2 `r` near the CMD pipe (west wall row 5) --
nearest-pipe binding does the phase switch.

LDR interior (19 x 9), local coords:

  row0  @8M3*b1M..v          init: BP=24 (=8*3), B=1; descend east of ring
  row1  > >1sWs+Mv           (0,1) is the init rejoin turn
  row2   vd...msr<           (1,2) exit turn south; d loops north onto (2,1)
  row3  ^.........<          init return corridor (westbound), rise at col 0
  row4   .                   exit path continues south down col 1
  row5  >rX2+M>0sWsM0s1+sv   fetch loop main row
  row6  ^>>M..^          v   first-entry (1,6)>, jump arm (2..6), return v
  row7  ^<<<<<<<<<<<<<<<<<   return corridor
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers.memory_men_v3 import build_v3
from randomfun2026solvers.lm1.rom import build_packed_rom

P = 24  # program words; BP init below hardcodes 8*3
WORDS = [200 + (j + 1) for j in range(P)]  # word j -> stored at addr j+1


class Grid:
    def __init__(self):
        self.c = {}

    def put(self, x, y, ch):
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise SystemExit(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch

    def text(self, x, y, s):
        for i, ch in enumerate(s):
            if ch != " ":
                self.put(x + i, y, ch)

    def room(self, x0, y0, x1, y1):
        for x in range(x0 + 1, x1):
            self.put(x, y0, "-")
            self.put(x, y1, "-")
        for y in range(y0 + 1, y1):
            self.put(x0, y, "|")
            self.put(x1, y, "|")
        for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.put(cx, cy, "+")

    def pipe(self, path):
        arrow = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
        for i in range(len(path) - 1):
            (x, y), (nx, ny) = path[i], path[i + 1]
            step = (nx - x, ny - y)
            if step not in arrow:
                raise SystemExit(f"bad pipe step {step} at {path[i]}")
            self.put(x, y, arrow[step])

    def render(self):
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        return "\n".join(
            "".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)
        )


g = Grid()

# ── ROM drum: interior at (12, 1) ───────────────────────────────────────────
RX, RY = 12, 1
rom = build_packed_rom(WORDS, rows=2)
for (x, y), ch in rom.cells.items():
    if ch != " ":
        g.put(RX + x, RY + y, ch)
g.room(RX - 1, RY - 1, RX + rom.width, RY + rom.height)

# ── LDR room: interior 19 x 9 at (12, 10) ───────────────────────────────────
LX, LY = 12, 10

def L(x, y, s):
    g.text(LX + x, LY + y, s)

L(0, 0, "@8M3*b1M..v")        # init: BP=24, B=1; v at col 10
L(0, 1, ">")                   # init rejoin: arrive north->east into ring
L(2, 1, ">1sWs+Mv")            # phase-1 ring, top row, cols 2..9
L(1, 2, "vd...msr<")           # exit v at col1; ring bottom row cols 2..9
L(10, 3, "<")                  # init return corridor
for x in range(1, 10):
    L(x, 3, ".")
L(0, 3, "^")
L(0, 2, ".")
L(1, 4, ".")                   # exit path down col 1

L(0, 5, ">rX2+M>0sWsM0s1+sv")  # fetch main row, cols 0..17
L(0, 6, "^>>M..^")             # return riser, first-entry >, jump arm
L(17, 6, "v")
L(0, 7, "^" + "<" * 17)        # return corridor, cols 0..17

g.room(LX - 1, LY - 1, LX + 19, LY + 9)

# ── I room (commands) ───────────────────────────────────────────────────────
g.room(2, 14, 4, 16)
g.put(3, 15, "I")
g.pipe([(x, 15) for x in range(5, 12)])  # dest wall (11,15) = LDR west row 5

# ── ROM -> LDR pipe (into LDR west wall row 2 = abs (11,12)) ────────────────
g.pipe(
    [(RX - 2, RY), (RX - 3, RY)]
    + [(RX - 3, y) for y in range(RY + 1, 13)]
    + [(RX - 2, 12), (RX - 1, 12)]
)

# ── store: men-v3, addresses 0..P (0 unused) ────────────────────────────────
SX, SY = 12, 22
v3 = build_v3(P + 1, ops=48, per_row=24, per_row_auto=False, io=False)
for y, row in enumerate(v3.rows):
    for x, ch in enumerate(row):
        if ch != " ":
            g.put(SX + x, SY + y, ch)
assert v3.in_cell is not None and v3.out_cell is not None
in_x, in_y = v3.in_cell  # local (3, 6): '>' stub cells at local x 3,4; wall at 5

# LDR -> store request pipe: out of LDR south wall at abs x 13
g.pipe(
    [(13, y) for y in range(20, SY + in_y + 1)]
    + [(14, SY + in_y), (SX + in_x, SY + in_y)]
)

# ── answer -> O room ────────────────────────────────────────────────────────
ox, _oy = v3.out_cell
ox_abs = SX + ox
g.room(ox_abs - 1, 17, ox_abs + 1, 19)
g.put(ox_abs, 18, "O")
# stub arrows already run abs y SY+4 .. SY+1; extend SY..20 up, dest wall (19)
g.pipe([(ox_abs, y) for y in range(SY, 18, -1)])

print(g.render())
