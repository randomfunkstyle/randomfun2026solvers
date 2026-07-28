#!/usr/bin/env python3
"""Toy: the seek-drum standalone. Requests from I, emitted words to O.

Verifies on the reference engine: sequential emission (wrap included), and the
jump protocol — on a request ``row*K+rem`` the stream shows ``-1, rem`` and
resumes at the target row's first word.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers.lm1.seekrom import build_seek_rom, seek_target

WORDS = [200 + j for j in range(24)]


class Grid:
    def __init__(self):
        self.c = {}

    def put(self, x, y, ch):
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise SystemExit(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch

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
            self.put(x, y, arrow[(nx - x, ny - y)])

    def render(self):
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        return "\n".join(
            "".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)
        )


rom = build_seek_rom(WORDS, rows=4)

g = Grid()
RX, RY = 8, 4  # ROM interior origin (room walls at RX-1 / RY-1)
for (x, y), ch in rom.cells.items():
    if ch != " ":
        g.put(RX + x, RY + y, ch)
g.room(RX - 1, RY - 1, RX + rom.width, RY + rom.height)

# request pipe: I room (west) -> ROM west wall at the station row
iy = RY + 1  # interior row -2 shifted by top_pad 3 -> local row 1
g.room(0, iy - 1, 2, iy + 1)
g.put(1, iy, "I")
g.pipe([(x, iy) for x in range(3, RX)])  # dest wall (RX-1, iy)

# output: ROM east wall -> O room (down and east, dest south wall of nothing --
# simple: east wall exit at some row, straight east into O)
oy = RY + 6
OX = RX + rom.width + 6
g.pipe([(x, oy) for x in range(RX + rom.width + 1, OX)])  # from east wall out
g.room(OX - 1, oy - 1, OX + 1, oy + 1)
g.put(OX, oy, "O")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "targets":
        # print seek operands for a few word indexes
        for wi in (0, 2, 8, 12, 20):
            print(wi, seek_target(rom, wi), rom.word_pos[wi])
    else:
        print(g.render())
