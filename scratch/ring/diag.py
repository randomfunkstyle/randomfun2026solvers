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
H, W = p.heat, p.wait
def cell(D, x, y): return D.get((x, y), 0)

tot = totw = 0; per = []
for y in range(len(rows) - 1):
    for mo in re.finditer(r"drsmv", rows[y]):
        x = mo.start()
        if rows[y+1][x:x+5] == "^msrd":
            s = sum(cell(H, x+i, y) + cell(H, x+i, y+1) for i in range(5))
            w = sum(cell(W, x+i, y) + cell(W, x+i, y+1) for i in range(5))
            per.append((s, w, x, y)); tot += s; totw += w
print(f"{len(per)} rings; heat {tot:,} = {100*tot/res.step:.2f}% of {res.step:,}; wait {totw:,} ({100*totw/max(1,tot):.1f}% blocked)")
for s, w, x, y in sorted(per, reverse=True):
    print(f"   ring ({x:>3},{y:>3})  heat {s:>12,}  wait {w:>12,} ({100*w/max(1,s):.0f}%)")

# relay rooms: the batch-2 art '|>@rv|', batch-1 legacy '|@ >v|'
print()
for tag, art0 in (("batch2 relay(4,3)", "|>@rv|"), ("batch1 RELAY", "|@ >v|")):
    for y in range(len(rows)):
        for mo in re.finditer(re.escape(art0), rows[y]):
            x = mo.start()
            hh = ww = 0
            det = []
            for dy in range(0, 4):
                for dx in range(1, len(art0) - 1):
                    ch = rows[y+dy][x+dx]
                    a, b = cell(H, x+dx, y+dy), cell(W, x+dx, y+dy)
                    hh += a; ww += b
                    if ch.strip() and ch not in "+-|":
                        det.append(f"{ch}@{dx},{dy}:{a:,}/{b:,}")
            print(f"  {tag} at ({x},{y}) heat {hh:,} wait {ww:,} ({100*ww/max(1,hh):.0f}%)  " + " ".join(det))
