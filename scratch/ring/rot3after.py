"""Ring heat with the batched P2 on.

The shipped `drsmv`/`^msrd` scan only sees a one-word ring, so the batched P2 is
matched on its own signature (`d` + `rs`*k + `m` + a turn, twice) and the two bit
tails are charged to it as well — otherwise turning the ring into something the
scanner cannot see would look like a win by itself.
"""
import sys, re
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from common import setup, tour
d3, hires, M, prog = setup()
import randomfun2026solvers.memory_tape as mt
mt.JUMP_V4_P2_BATCH = 4
m = M.build_for("deadman-3d_hires", program=prog, store="taped")
inp, frames = tour(hires)
from randomfun2026solvers.fast_littleman import FastLittleman
res = FastLittleman("\n".join(m.rows)).run(inp, frames=frames, frame_tiles=(2, 2),
                                           max_ticks=40_000_000_000, profile=True)
p, rows = res.profile, m.rows
H = p.heat
def cell(x, y): return H.get((x, y), 0)

tot = 0; per = []
# 1) the shipped one-word rings that remain (all seven P1)
for y in range(len(rows) - 1):
    for mo in re.finditer(r"drsmv", rows[y]):
        x = mo.start()
        if rows[y+1][x:x+5] == "^msrd":
            s = sum(cell(x+i, y) + cell(x+i, y+1) for i in range(5))
            per.append((s, x, y, "P1 1-word ring")); tot += s
# 2) the batched P2: its ring (11 wide) plus the two bit tails above it
for y in range(len(rows) - 1):
    for mo in re.finditer(r"drsrsrsrsmv", rows[y]):
        x = mo.start()
        if rows[y+1][x:x+11] == "^msrsrsrsrd":
            s = sum(cell(x+i, y) + cell(x+i, y+1) for i in range(11))
            # the tails sit three rows above the ring's top row, columns x-2..x+10
            t = sum(cell(xx, yy) for yy in (y-5, y-4, y-3) for xx in range(x - 2, x + 11))
            per.append((s + t, x, y, f"P2 batched (ring {s:,} + tails {t:,})"))
            tot += s + t
print(f"\n{len(per)} rings;  ticks inside rings = {tot:,}  = {100*tot/res.step:.2f}% "
      f"of the {res.step:,} tick run")
for s, x, y, tag in sorted(per, reverse=True):
    print(f"   ({x:>3},{y:>3})  {s:>12,}  {tag}")
