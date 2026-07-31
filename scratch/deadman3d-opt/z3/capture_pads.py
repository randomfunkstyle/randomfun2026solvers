"""Capture the real glyph geometry + touch table at several `mem_pad` values.

A doomed pad still *builds* — it is `check_bindings` that refuses it — so we
stub the hook out and read the geometry the builder would have produced.
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
hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
prog = assemble(hires.hires_source(), name=SLUG)
out = Path(os.environ["CLAUDE_JOB_DIR"]) / "tmp"

def grab(pad, in_west):
    M.MEM_PAD_FOR[(SLUG, "men-v3")] = pad
    M.INPUT_NORTH_WEST.pop((SLUG, "men-v3"), None)
    if in_west is not None:
        M.INPUT_NORTH_WEST[(SLUG, "men-v3")] = in_west
    seen, real = [], M.check_bindings
    M.check_bindings = lambda g, t: seen.append((list(g), dict(t)))
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    finally:
        M.check_bindings = real
    g, t = seen[0]
    return {"pad": pad, "in_west": in_west, "w": m.width, "h": m.height,
            "mem_pad": m.mem_pad,
            "glyphs": [[x, y, gl, str(b)] for x, y, gl, b in g],
            "touches": {str(k): list(v) for k, v in t.items()}}

recs = []
for pad in (0, 1, 2, 3):
    for iw in (9, None):
        r = grab(pad, iw)
        recs.append(r)
        print(f"pad={pad} in_west={iw}: {r['w']}x{r['h']} mem_pad={r['mem_pad']} "
              f"{len(r['glyphs'])} glyphs", flush=True)
(out / "pads.json").write_text(json.dumps(recs))
