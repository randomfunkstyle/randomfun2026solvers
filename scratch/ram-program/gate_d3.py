"""The deadman-3d boot+frame-1 native gate, for baseline and seek builds."""

import sys

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

seek = "--seek" in sys.argv
if seek:
    M.SEEK_DRUM.add("deadman-3d")
m = M.build_for("deadman-3d", trim_dead=True)
print(("seek" if seek else "base"), "machine:", m.width, "x", m.height,
      "fp", max(m.width, m.height) ** 2)
if "--man" in sys.argv:
    out = f"scratch/ram-program/deadman_{'seek' if seek else 'base'}.man"
    with open(out, "w") as f:
        f.write("\n".join(m.rows) + "\n")
    print("wrote", out)

cases = d3.cases_json(d3.WALK[:1])
rounds = cases["publicTestData"][0]["rounds"]
inp = "/".join(" ".join(r["in"]) for r in rounds)
frames = [r["frames"] for r in rounds]
fl = FastLittleman("\n".join(m.rows))
res = fl.run(inp, frames=frames, max_ticks=30_000_000)
print("passed:", res.fatal is None and res.ok, "ticks:", res.step,
      "fatal:", res.fatal)
