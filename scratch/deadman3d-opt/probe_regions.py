import sys, time
from randomfun2026solvers.lm1.machine import build_for

for store in ("men-v3", "taped"):
    t = time.time()
    m = build_for("deadman-3d", store=store)
    print(f"=== {store}: {m.width} x {m.height}   ({time.time()-t:.1f}s)")
    for name, (x, y, w, h) in sorted(m.regions.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        print(f"  {name:28s} x={x:4d} y={y:4d} w={w:4d} h={h:4d}  (x2={x+w-1}, y2={y+h-1})")
    text = m.render() if hasattr(m, "render") else str(m)
    lines = text.split("\n")
    print("  rendered lines:", len(lines), "max len:", max(len(l) for l in lines))
