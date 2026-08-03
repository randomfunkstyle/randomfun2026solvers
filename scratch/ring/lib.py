"""Parallel knob sweep for deadman-3d_hires / taped, in this worktree."""
from __future__ import annotations
import json, subprocess, os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WT = Path('/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687')
BASE = 76_610_982

RUNNER = r'''
import sys, json
sys.path.insert(0, "%WT%/solvers/python")
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
'''.replace("%WT%", str(WT))

_rp = WT / "scratch" / "ring" / "_run.py"


def sweep(variants, jobs=6, quiet=False, timeout=900):
    _rp.write_text(RUNNER)
    def one(v):
        tag, code = v
        try:
            p = subprocess.run(
                ["uv", "run", "python", str(_rp), json.dumps({"tag": tag, "code": code})],
                cwd=str(WT), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"tag": tag, "err": "TIMEOUT (machine does not terminate)"}
        for line in p.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                r = json.loads(line[10:])
                if "err" in r:
                    r["tb"] = p.stderr[-2000:]
                return r
        return {"tag": tag, "err": (p.stderr or p.stdout)[-300:]}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        res = list(ex.map(one, variants))
    for r in res:
        if "step" in r:
            r["d"] = r["step"] - BASE
            r["pct"] = 100.0 * r["d"] / BASE
    if not quiet:
        report(res)
    return res


def report(res):
    ok = [r for r in res if "step" in r]
    bad = [r for r in res if "step" not in r]
    for r in sorted(ok, key=lambda r: r["step"]):
        flag = "" if r["passed"] and r["fatal"] == "None" else "  !!FAIL"
        print(f'{r["step"]:>13,}  {r["d"]:>+11,}  {r["pct"]:>+7.3f}%  {r["w"]}x{r["h"]}  {r["tag"]}{flag}')
    for r in bad:
        print(f'{"ERR":>13}  {r["tag"]}: {r.get("err","?")[:300]}')
