"""Would moving the ROM touch row south recover `LANE_PITCH` on hires?  Yes, d>=5.

`LANE_PITCH[("deadman-3d_hires","taped")] = 1` is worth -4.351% and is currently
withdrawn (`c9c8f50`) because on a seek build the BRN slab's discard `r` at
(43,188) binds `mem_resp` instead of `rom`:

    'r' at (43, 188) must bind 'rom' but distances are
        [('mem_resp', 54), ('rom', 58), ('in', 88)]

`touches["rom"] = (CX - 1, CY + cpu.centre)` pins the ROM attachment to the
**fetch row** by construction — not by any obstruction.  Columns 0..7 below the
fetch row are blank for the whole remaining height of the CPU, so the corridor
could turn east lower down.  Moving it south helps every southern `r` and costs
the fetch almost nothing: the fetch `r` sits 3 cells from the touch and has ~57
cells of slack, while the deepest slab has one.

This probe answers the only question worth asking before implementing that:
**does the 12-constraint system have a solution at all?**  It shifts
`touches["rom"]` by `d` rows inside a `check_bindings` wrapper and lets the real
checker rule on every `r`.  Result:

    d= 0..3   fails, rom 58..55 against mem_resp 54
    d= 4      fails — rom 54 TIES mem_resp 54, and `check_bindings` fails ties
    d= 5..14  every one of the 12 `r` glyphs binds correctly

So `d = 5` is exactly the five cells the arithmetic predicted, and there are ten
rows of freedom above it rather than a knife-edge.

**What this does NOT do.** It lies to the checker; the pipe is still physically
routed to the fetch row.  The machine it "builds" is invalid and must not be
measured — the engine binds by real geometry, so the BRN discard would still
read a memory response.  Implementing this means routing the corridor to the
shifted row as well, which is a builder change.  This file exists to show the
change is worth making before anyone makes it.
"""
import sys
sys.path.insert(0, "solvers/python")
from pathlib import Path
from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers import deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble
SLUG, KEY = "deadman-3d_hires", ("deadman-3d_hires","taped")
hires.install_wad(Path.home()/"Downloads/doom1_0/DOOM1.WAD")
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
prog = assemble(hires.hires_source(), name=SLUG)
M.LANE_PITCH[KEY] = 1
M.MEM_PAD_FOR[KEY] = 15
real = M.check_bindings
SHIFT = {"d": 0}
seen = {}
def patched(glyphs, touches):
    t = dict(touches)
    if "rom" in t:
        x, y = t["rom"]; t["rom"] = (x, y + SHIFT["d"])
    seen["touches"] = t
    seen["nrom"] = sum(1 for _x, _y, g, b in glyphs if g == "r" and b == "rom")
    return real(glyphs, t)
M.check_bindings = patched
print("pitch 1, pad 15; shifting touches['rom'] south by d rows", flush=True)
for d in range(0, 15):
    SHIFT["d"] = d
    try:
        m = M.build_for(SLUG, program=prog, store="taped")
        print(f"  d={d:>2}  ALL BINDINGS OK  (build {m.width}x{m.height})", flush=True)
    except M.MachineError as e:
        s = str(e)
        if "must bind" in s:
            frag = s.split("must bind")[-1].strip()[:58]
            who = s.split("'r' at ")[-1].split(")")[0] + ")" if "'r' at " in s else "?"
            print(f"  d={d:>2}  binding fails at {who}: {frag}", flush=True)
        else:
            print(f"  d={d:>2}  other: {s[:66]}", flush=True)
    except Exception as e:
        print(f"  d={d:>2}  {type(e).__name__}: {str(e)[:60]}", flush=True)
M.check_bindings = real
print(f"\n('r' glyphs wanting rom in the last attempt: {seen.get('nrom')})")
