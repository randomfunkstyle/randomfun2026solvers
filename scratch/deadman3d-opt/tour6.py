"""Native round-gated run of the checked-in 115-frame tour, under overrides.

The command list is recovered from ``deadman-3d_tour.input.txt`` itself (the
file is preamble + title RLE + one word per command), so no chords file is
needed and the tour cannot drift.

usage: tour6.py <rom_rows|-> <banks csv|-> <order csv|-|none>
"""
import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

rr = sys.argv[1] if len(sys.argv) > 1 else "-"
banks = sys.argv[2] if len(sys.argv) > 2 else "-"
order = sys.argv[3] if len(sys.argv) > 3 else "-"
if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr)}
if banks != "-":
    M.TAPED_BANKS["deadman-3d"] = tuple(int(x) for x in banks.split(","))
if order == "none":
    M.TAPED_BANK_ORDER.pop(("deadman-3d", "taped"), None)
elif order != "-":
    M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = tuple(int(x) for x in order.split(","))

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
words = [int(w) for w in EX.joinpath("deadman-3d_tour.input.txt").read_text().split()]
boot = d3.preamble_words() + d3.title_words()
assert words[: len(boot)] == boot, "tour input does not start with the boot round"
cmds = words[len(boot) :]
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
assert inp.replace(" / ", " ").split() == [str(w) for w in words], "tour input drift"
frames = [r["frames"] for r in case["rounds"]]

t0 = time.time()
m = M.build_for("deadman-3d", store="taped")
src = "\n".join(m.rows)
print(
    f"rom_rows={M.SEEK_TIER_LAYOUT[('deadman-3d','taped')]} "
    f"banks={M.TAPED_BANKS['deadman-3d']} "
    f"order={M.TAPED_BANK_ORDER.get(('deadman-3d','taped'))}"
)
print(
    f"  {m.width}x{m.height} max={max(m.width, m.height)}  rounds={len(frames)}  "
    f"cmds={len(cmds)}  build {time.time()-t0:.0f}s",
    flush=True,
)
t0 = time.time()
res = FastLittleman(src).run(inp, frames=frames, max_ticks=6_000_000_000)
print(
    f"  ticks={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time()-t0:.0f}s)",
    flush=True,
)
