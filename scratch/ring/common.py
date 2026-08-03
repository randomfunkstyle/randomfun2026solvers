"""Harness for the ring-rotation experiment (this worktree)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

WT = Path('/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687')
sys.path.insert(0, str(WT / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"


def setup():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    return d3, hires, M, prog


def tour(hires, n=21):
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    return inp, frames


def run(m, inp, frames, tag="", profile=False):
    from randomfun2026solvers.fast_littleman import FastLittleman
    t0 = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000, profile=profile)
    dt = time.time() - t0
    print(f"  {tag}: {m.width}x{m.height} fatal={res.fatal} passed={res.passed} "
          f"step={res.step:,} last_frame={res.frame_ticks[-1]:,} ({dt:.0f}s)",
          flush=True)
    return res
