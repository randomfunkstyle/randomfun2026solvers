"""Profile the taped DOOM ROM: word stats, token-width distribution, cells/word."""
import collections

from randomfun2026solvers.lm1 import machine as M

m = M.build_for("deadman-3d", store="taped")
print(f"machine {m.width}x{m.height}")
for name, (x, y, w, h) in sorted(m.regions.items(), key=lambda kv: -(kv[1][2] * kv[1][3])):
    print(f"  {name:24s} x={x:4d} y={y:4d} w={w:4d} h={h:4d}  east={x+w:4d} south={y+h:4d} area={w*h:,}")
