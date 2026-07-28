"""Multi-frame native A/B: boot, boot+1, boot+3 for base and seek builds."""

import sys

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs

SLUG = "deadman-3d"
seek = "--seek" in sys.argv
pad = None
for a in sys.argv:
    if a.startswith("--pad="):
        pad = int(a.split("=", 1)[1])

place = M.MEM_PLACE.get(SLUG, ((0, 0), (0, 0)))
kw = dict(
    tape_n=M.TAPE_SIZE[SLUG],
    rom_rows=M.ROM_ROWS.get(SLUG),
    # the seek build's lane band differs, so its pad is re-searched
    mem_pad=pad if seek else M.MEM_PAD.get(SLUG),
    display=M.display_for(SLUG),
    stream=M.STREAM_SIZE.get(SLUG),
    store=M.STORE_TIER.get(SLUG, "tape"),
    middle_order=M.LANE_ORDER.get(SLUG),
    mem_offset=place[0],
    store_offset=place[1],
    in_north=SLUG in M.INPUT_NORTH,
    store_teleport=SLUG in M.STORE_TELEPORT,
    trim_dead=True,
    store_shape=M.STORE_SHAPE.get(SLUG),
    seek=seek,
)
if seek:
    ops = [a.split("=",1)[1] for a in sys.argv if a.startswith("--ops=")]
    if ops:
        kw["seek_ops"] = tuple(ops[0].split(","))
m = M.build(programs.load(SLUG), **kw)
tag = "seek" if seek else "base"
print(f"{tag}: {m.width}x{m.height} fp {max(m.width, m.height) ** 2} mem_pad={m.mem_pad}")
if "--man" in sys.argv:
    out = f"scratch/ram-program/deadman_{tag}.man"
    with open(out, "w") as f:
        f.write("\n".join(m.rows) + "\n")
    print("wrote", out)

fl = FastLittleman("\n".join(m.rows))
prev = 0
for n in (0, 1, 3):
    cases = d3.cases_json(d3.WALK[:n])
    rounds = cases["publicTestData"][0]["rounds"]
    inp = "/".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    res = fl.run(inp, frames=frames, max_ticks=60_000_000)
    ok = res.fatal is None and res.ok
    delta = f" (+{res.step - prev:,})" if n else ""
    print(f"  boot+{n} frames: passed={ok} ticks={res.step:,}{delta}")
    prev = res.step
