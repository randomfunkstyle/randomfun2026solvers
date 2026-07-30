"""Where the ticks go, per tier: the CPU runner's own time budget.

`heat` is runner-samples; the CPU is one runner alive the whole run, so its
samples inside a region, divided by `profile.samples`, is the fraction of the
RUN the CPU spent there — and `wait` is the blocked subset of that.

usage: profile_tiers.py <rounds> [taped|men] [colsxrows]
"""
import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, SLUG
from randomfun2026solvers.fast_littleman import FastLittleman

d3, hires, M, prog = setup()
rounds = int(sys.argv[1])
which = sys.argv[2] if len(sys.argv) > 2 else "taped"
inp, frames = tour(hires, rounds)

if which == "taped":
    m = M.build_for(SLUG, program=prog, store="taped")
else:
    cols, rws = (int(v) for v in (sys.argv[3] if len(sys.argv) > 3 else "15x61").split("x"))
    M.STORE_SHAPE[SLUG] = (cols, rws)
    M.SEEK_TIER_LAYOUT[(SLUG, "men-v3")] = {"rom_rows": 119}
    m = M.build_for(SLUG, program=prog, store="men-v3")
print(f"{which}: {m.width}x{m.height}", flush=True)

t = time.time()
res = FastLittleman("\n".join(m.rows)).run(
    inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
    profile=True, profile_stride=17)
print(f"  step={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time()-t:.0f}s)",
      flush=True)
p = res.profile
S = p.samples
print(f"  samples={S:,} stride={p.stride}")

# ---- the CPU's own time budget -------------------------------------------
cpu = [(n, b) for n, b in m.regions.items() if n.startswith("cpu")]
rows = []
tot_h = tot_w = 0
for name, (x, y, w, h) in cpu:
    hh = sum(v for (cx, cy), v in p.heat.items() if x <= cx < x + w and y <= cy < y + h)
    ww = sum(v for (cx, cy), v in p.wait.items() if x <= cx < x + w and y <= cy < y + h)
    if hh:
        rows.append((name, hh, ww))
    tot_h += hh
    tot_w += ww
print(f"  CPU regions total: heat {100*tot_h/S:.2f}% of run, of which blocked "
      f"{100*tot_w/S:.2f}%")
print("  cpu region                          %run    %run blocked")
for name, hh, ww in sorted(rows, key=lambda r: -r[1])[:22]:
    print(f"   {name:34s} {100*hh/S:7.2f} {100*ww/S:12.2f}")

# ---- the store-facing pipes ----------------------------------------------
print("  top pipes by sampled wait (pipe_wait / samples):")
order = sorted(range(len(p.pipe_wait)), key=lambda i: -p.pipe_wait[i])[:12]
for i in order:
    print(f"   pipe {i:4d}: wait {100*p.pipe_wait[i]/S:7.2f}%  recv {p.recv[i]:>10,} "
          f"send {p.send[i]:>10,} recv_blocked {p.recv_blocked[i]:>9,} "
          f"send_blocked {p.send_blocked[i]:>9,}")
