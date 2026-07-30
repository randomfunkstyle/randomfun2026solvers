"""Shared harness for the hires men-vs-taped probe.

Kept because four levers were never swept on the men geometry — ``SQUASH_BAND``,
``TUCKED_DROPS`` and ``SEEK_TAKEN_DROP_EAST`` went unmeasured when machine load
made them uneconomic, and a men-native ``OPCODE_SLOTS``, a men
``STORE_ANSWER_WEST``, the ``store_offset`` dy pipe collision at (93, 146) and a
bind-aware ``MEM_PAD`` sweep are all still open. Re-deriving this harness to get
at them would be the expensive part; running it is not.

``WT`` was an absolute path into the throwaway isolation worktree, which is why
this needed rescuing at all. It now resolves from the file's own location, so the
scripts work from any checkout or worktree. Note the parent count: this lives one
directory deeper than the rest of ``scratch/deadman3d-opt``.

Nothing here is WAD-derived. The companion ``traffic21.json`` was deliberately
**not** kept — per-slot access counts are level data (``littleman/DEADMAN-3D.md``)
and ``traffic.py`` regenerates it locally in seconds.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
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


def run(m, inp, frames, tag=""):
    from randomfun2026solvers.fast_littleman import FastLittleman
    t0 = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
    dt = time.time() - t0
    print(f"  {tag}: {m.width}x{m.height} fatal={res.fatal} passed={res.passed} "
          f"step={res.step:,} last_frame={res.frame_ticks[-1]:,} ({dt:.0f}s)",
          flush=True)
    return res
