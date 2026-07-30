"""Where inside build_for does check_bindings sit?

If the CPU's binding check happens early, aborting right after it is an *exact*
binding oracle far cheaper than a full build -- which is the difference between
a 21s candidate and a sub-second one.
"""
import sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3, deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble

hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
SLUG = "deadman-3d_hires"
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
prog = assemble(hires.hires_source(), name=SLUG)

real = M.check_bindings
T0 = [0.0]
log = []


def patched(glyphs, touches):
    log.append((time.time() - T0[0], len(glyphs), sorted(touches)))
    return real(glyphs, touches)


M.check_bindings = patched
T0[0] = time.time()
m = M.build_for(SLUG, program=prog, store="men-v3")
total = time.time() - T0[0]
M.check_bindings = real
print(f"build_for total {total:.1f}s, check_bindings called {len(log)}x")
for t, n, ts in log:
    print(f"  t={t:6.2f}s  glyphs={n:4d}  touches={ts}")


# --- and: how cheap is an abort right after the first (CPU) call? ---
class Abort(Exception):
    pass


cap = {}


def aborting(glyphs, touches):
    cap["glyphs"] = list(glyphs)
    cap["touches"] = dict(touches)
    real(glyphs, touches)  # let the real checker rule
    raise Abort


M.check_bindings = aborting
t0 = time.time()
try:
    M.build_for(SLUG, program=prog, store="men-v3")
except Abort:
    print(f"abort-at-first-check: {time.time()-t0:.2f}s  "
          f"glyphs={len(cap['glyphs'])} touches={sorted(cap['touches'])}")
except Exception as exc:
    print(f"abort run raised {type(exc).__name__}: {exc}  ({time.time()-t0:.2f}s)")
M.check_bindings = real
