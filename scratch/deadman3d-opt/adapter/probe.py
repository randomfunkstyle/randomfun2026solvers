"""Build deadman-3d_hires/taped with the folded adapter on and off, and compare.

Usage: probe.py [base|fold|tuck] [--run]

``base``  both registries emptied  — the tree as it stands
``fold``  ADAPTER_COMPACT only     — 10x3 adapter, drop unchanged in length
``tuck``  ADAPTER_COMPACT + STORE_REQUEST_TUCK — and the drop down to two cells

Nothing here is WAD-derived except the tour input, which stays in /tmp.
"""
from __future__ import annotations

import json, subprocess, sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT / "solvers" / "python"))
sys.path.insert(0, str(WT / "scratch" / "deadman3d-opt" / "menprobe"))
from common import setup, tour, run, SLUG  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "base"
DO_RUN = "--run" in sys.argv
DY = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--dy=")), None)
KEY = (SLUG, "taped")

d3, hires, M, prog = setup()
if MODE == "base":
    M.ADAPTER_COMPACT = set()
    M.STORE_REQUEST_TUCK = set()
elif MODE == "fold":
    M.ADAPTER_COMPACT = {KEY}
    M.STORE_REQUEST_TUCK = set()
elif MODE == "tuck":
    M.ADAPTER_COMPACT = {KEY}
    M.STORE_REQUEST_TUCK = {KEY}
else:
    raise SystemExit(f"unknown mode {MODE}")

if DY is not None:
    dx, _ = M.TIER_LAYOUT[KEY]["store_offset"]
    M.TIER_LAYOUT[KEY] = dict(M.TIER_LAYOUT[KEY], store_offset=(dx, DY))

print(f"[{MODE}] building...", flush=True)
t0 = time.time()
m = M.build_for(SLUG, program=prog, store="taped")
print(f"[{MODE}] {m.width}x{m.height} in {time.time()-t0:.0f}s", flush=True)

grid = Path(f"/tmp/adapter-{MODE}.man")
grid.write_text("\n".join(m.rows) + "\n")
ax, ay, aw, ah = m.regions["adapter"]
print(f"[{MODE}] adapter region ({ax},{ay}) {aw}x{ah}  floor_y={ay+ah-1}", flush=True)
print(f"[{MODE}] store_offset={M.TIER_LAYOUT[KEY]['store_offset']}  "
      f"routes={json.dumps(m.route_lengths)}", flush=True)
for y in range(ay - 1, ay + ah + 6):
    print(f"[{MODE}] {y:4d} |{m.rows[y][ax-2:ax+aw+20]}|", flush=True)

out = subprocess.run([str(WT / "littleman" / "lm.mjs"), "analyze", str(grid), "--json"],
                     capture_output=True, text=True, check=False)
if out.returncode != 0:
    print(f"[{MODE}] LOAD ERROR: {(out.stderr or out.stdout)[:600]}", flush=True)
    raise SystemExit(1)
an = json.loads(out.stdout)
rooms, pipes = an.get("rooms") or [], an.get("pipes") or []
print(f"[{MODE}] analyze OK: {len(rooms)} rooms, {len(pipes)} pipes", flush=True)

# how many pipes touch the adapter room, per the reference analyser's own tally
adapter_room = None
for i, r in enumerate(rooms):
    if tuple(r["min"]) == (ax, ay) and tuple(r["max"]) == (ax + aw - 1, ay + ah - 1):
        adapter_room = i
assert adapter_room is not None, f"no room at ({ax},{ay})-({ax+aw-1},{ay+ah-1})"
inc = sum(1 for q in pipes if q["dst"] == adapter_room)
outg = sum(1 for q in pipes if q["src"] == adapter_room)
print(f"[{MODE}] adapter pipes: {inc} incoming, {outg} outgoing", flush=True)

if DO_RUN:
    inp, frames = tour(hires, 21)
    res = run(m, inp, frames, MODE)
    print(f"[{MODE}] RESULT passed={res.passed} fatal={res.fatal} ticks={res.step:,} "
          f"box={m.width}x{m.height}", flush=True)
