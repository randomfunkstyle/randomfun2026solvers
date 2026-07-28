import sys
from randomfun2026solvers.lm1.machine import build_for, TAPE_SIZE

m = build_for("deadman-3d")
rows = [r.ljust(m.width) for r in m.rows]
print("TAPE_SIZE deadman-3d:", TAPE_SIZE.get("deadman-3d"))
y0, y1, x0, x1 = (int(a) for a in sys.argv[1:5])
hdr = "".join(str((x // 10) % 10) if x % 10 == 0 else " " for x in range(x0, x1))
print("    " + hdr)
print("    " + "".join(str(x % 10) for x in range(x0, x1)))
for y in range(y0, y1):
    print(f"{y:3d}|" + rows[y][x0:x1].replace(" ", "·"))
