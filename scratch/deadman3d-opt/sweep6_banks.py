"""Tick sweep over taped bank plans (native engine).

usage: sweep6_banks.py <n_commands|full> [rom_rows|-] <plan:order> [plan:order ...]
       plan = comma-separated sizes, order = comma-separated bank indices or "-"
"""
import sys
import time

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine as M

ncmd = sys.argv[1]
rr = sys.argv[2]
specs = sys.argv[3:]

if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr)}

walk = d3.WALK if ncmd == "full" else d3.WALK[: int(ncmd)]
case = d3.cases_json(walk)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]
print(f"cmds={len(walk)} rounds={len(frames)} rom_rows={rr}", flush=True)

base = None
for spec in specs:
    plan_s, _, order_s = spec.partition(":")
    if plan_s.startswith("B"):  # inclusive top addresses of every bank but the last
        b = [int(x) for x in plan_s[1:].split(",")]
        plan = tuple([b[0]] + [b[i] - b[i - 1] for i in range(1, len(b))] + [600 - b[-1]])
    else:
        plan = tuple(int(x) for x in plan_s.split(","))
    order = None if order_s in ("", "-") else tuple(int(x) for x in order_s.split(","))
    M.TAPED_BANKS["deadman-3d"] = plan
    if order is None:
        M.TAPED_BANK_ORDER.pop(("deadman-3d", "taped"), None)
    else:
        M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = order
    t0 = time.time()
    try:
        m = M.build_for("deadman-3d", store="taped")
        src = "\n".join(m.rows)
        res = FastLittleman(src).run(inp, frames=frames, max_ticks=8_000_000_000)
    except Exception as exc:  # noqa: BLE001
        print(f"  {plan} o={order}  FAIL {type(exc).__name__}: {str(exc)[:90]}", flush=True)
        continue
    if base is None:
        base = res.step
    print(
        f"  {str(plan):24s} o={str(order):14s} {m.width}x{m.height} "
        f"max={max(m.width,m.height):3d}  ticks={res.step:>13,}  "
        f"{100*(res.step-base)/base:+7.2f}%  passed={res.passed} fatal={res.fatal}  "
        f"({time.time()-t0:.0f}s)",
        flush=True,
    )
