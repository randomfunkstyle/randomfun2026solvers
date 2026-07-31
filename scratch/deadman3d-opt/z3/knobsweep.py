"""Z3 says pad 1 binds if `rom` or `mem_resp` moves. Find a knob that gets there.

For each knob value we capture the geometry with `check_bindings` stubbed out,
then decide it in Z3 rather than trusting the build -- so one build answers the
binding question at every pad at once.
"""
import json, os, sys
from pathlib import Path
REPO = Path("/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/compactor")
sys.path.insert(0, str(REPO / "solvers" / "python"))
from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers import deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble

SLUG = "deadman-3d_hires"
KEY = (SLUG, "men-v3")
hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
prog = assemble(hires.hires_source(), name=SLUG)
out = Path(os.environ["CLAUDE_JOB_DIR"]) / "tmp"
BASE_DROP = M.ROM_TOUCH_DROP.get(KEY)
print(f"baseline ROM_TOUCH_DROP={BASE_DROP}", flush=True)

recs = []
for drop in range(BASE_DROP - 4, BASE_DROP + 9):
    for pad in (1, 2):
        M.ROM_TOUCH_DROP[KEY] = drop
        M.MEM_PAD_FOR[KEY] = pad
        seen, real = [], M.check_bindings
        M.check_bindings = lambda g, t: seen.append((list(g), dict(t)))
        try:
            m = M.build_for(SLUG, program=prog, store="men-v3")
        except Exception as e:
            M.check_bindings = real
            print(f"  drop={drop} pad={pad}: BUILD FAILED {type(e).__name__}: {e}", flush=True)
            continue
        finally:
            M.check_bindings = real
        g, t = seen[0]
        recs.append({"drop": drop, "pad": pad, "w": m.width, "h": m.height,
                     "glyphs": [[x, y, gl, str(b)] for x, y, gl, b in g],
                     "touches": {str(k): list(v) for k, v in t.items()}})
        print(f"  drop={drop} pad={pad}: {m.width}x{m.height} rom={t.get('rom')} "
              f"mem_resp={t.get('mem_resp')}", flush=True)
(out / "knobs.json").write_text(json.dumps(recs))
