import sys, re
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from common import setup, tour
d3, hires, M, prog = setup()
m = M.build_for("deadman-3d_hires", program=prog, store="taped")
inp, frames = tour(hires)
from randomfun2026solvers.fast_littleman import FastLittleman
res = FastLittleman("\n".join(m.rows)).run(inp, frames=frames, frame_tiles=(2, 2),
                                           max_ticks=40_000_000_000, profile=True)
p, rows = res.profile, m.rows
H = p.heat
k0 = next(iter(H)); print("heat key sample:", k0, type(k0))
def cell(x, y): return H.get((x, y), 0) or H.get(y * p.width + x, 0)
tot = 0; per = []
PATTERNS = [(r"drsmv", "^msrd", 5)]
for y in range(len(rows) - 1):
    for pat, below, w in PATTERNS:
        for mo in re.finditer(pat, rows[y]):
            x = mo.start()
            if rows[y+1][x:x+w] == below:
                s = sum(cell(x+i, y) + cell(x+i, y+1) for i in range(w))
                per.append((s, x, y)); tot += s
print(f"\n{len(per)} rings;  ticks inside rings = {tot:,}  = {100*tot/res.step:.2f}% of the {res.step:,} tick run")
print(f"  at 5 t/value that is ~{tot//5:,} values rotated")
for s, x, y in sorted(per, reverse=True): print(f"   ring ({x:>3},{y:>3})  {s:>12,}")
