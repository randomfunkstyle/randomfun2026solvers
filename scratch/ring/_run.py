
import sys, json
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/solvers/python")
from pathlib import Path
WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers import deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble
assert "agent-ae0ef37bbc0eff687" in M.__file__, M.__file__
hires.install_wad(WAD)
M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
KEY = (SLUG, "taped")
snippet = json.loads(sys.argv[1])
out = {"tag": snippet["tag"]}
try:
    exec(snippet["code"], {"M": M, "d3": d3, "hires": hires, "SLUG": SLUG, "KEY": KEY})
    prog = assemble(hires.hires_source(), name=SLUG)
    m = M.build_for(SLUG, program=prog, store="taped")
    out["w"], out["h"] = m.width, m.height
    cmds = list(hires.WALK[:20])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    from randomfun2026solvers.fast_littleman import FastLittleman
    res = FastLittleman("\n".join(m.rows)).run(inp, frames=frames, frame_tiles=(2,2), max_ticks=400_000_000)
    out["step"] = res.step; out["passed"] = bool(res.passed); out["fatal"] = str(res.fatal)
except BaseException as e:
    import traceback; traceback.print_exc()
    out["err"] = f"{type(e).__name__}: {e}"[:400]
print("@@RESULT@@" + json.dumps(out))
