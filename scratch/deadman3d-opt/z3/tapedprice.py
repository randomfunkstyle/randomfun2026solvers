"""What a taped column and a taped corridor row are actually worth, in ticks.

The optimiser needs prices, and men-v3's (2.436 t/instr per ``mem_x`` column,
0.649 per corridor row) are men-v3's -- a different box, a different store and a
different access mix. This measures taped's own by building the variant and
running the **round-gated native emulator** over a short prefix of the tour, not
the tour: only the *difference* between two builds is wanted, and a difference
per round scales.

``frame_ticks`` gives the tick each logical frame committed on, so round 1 (boot
plus first frame) is dropped and the price is taken over the steady-state rounds
only. That is what makes a 4-round run stand in for a 21-round one: the boot is
a constant both variants pay, and every later round pays the same geometry.

    python tapedprice.py <rounds> '<json list of knob dicts>'
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("LM1_REPO") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taped import KEY, SLUG, TIER, apply_knobs, restore  # noqa: E402

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
#: The 21-round tour's instruction count, from the shipped measurement
#: (140,379,566 ticks / 159.46 t/instr). Ticks move with the layout; the
#: instruction stream does not, so this is the constant a tick delta is divided
#: by to become a t/instr delta.
TOUR_INSTRS = 880_332
TOUR_ROUNDS = 21


def main():
    rounds = int(sys.argv[1])
    trials = json.loads(sys.argv[2])

    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    cmds = list(hires.WALK[: rounds - 1])
    rr = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rr)
    frames = [r["frames"] for r in rr]
    print(f"gate {len(rr)} rounds, tape={M.TAPE_SIZE[SLUG]}, P={prog.P}", flush=True)

    out = []
    for i, kn in enumerate(trials):
        saved = apply_knobs(M, kn)
        kw = {}
        if "drop" in kn:
            kw["rom_touch_drop"] = kn["drop"]
        if "squash" in kn:
            kw["squash_band"] = kn["squash"]
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store=TIER, **kw)
        except Exception as e:  # noqa: BLE001
            print(f"[{i + 1}/{len(trials)}] {kn} BUILD FAILED {type(e).__name__}: {e}",
                  flush=True)
            restore(M, saved)
            continue
        finally:
            restore(M, saved)
        tb = time.time() - t0
        t0 = time.time()
        # hires paints 128x96 as four tiled LM-75s, so the display judge needs
        # the 2x2 tiling told to it (``hires_bankrun.py`` does the same).
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        ft = list(res.frame_ticks)
        # steady state: everything after the first accepted frame
        steady = (ft[-1] - ft[0]) if len(ft) > 1 else None
        rec = {
            "knobs": kn, "w": m.width, "h": m.height, "mem_pad": m.mem_pad,
            "ticks": res.step, "passed": res.passed, "fatal": res.fatal,
            "frames": len(ft), "steady": steady,
            "per_round": (steady / (len(ft) - 1)) if steady else None,
            "routes": dict(m.route_lengths), "rom_capacity": m.rom_capacity,
            "build_s": round(tb, 1), "run_s": round(time.time() - t0, 1),
        }
        out.append(rec)
        print(f"[{i + 1}/{len(trials)}] {kn} {m.width}x{m.height} pad={m.mem_pad} "
              f"ticks={res.step:,} passed={res.passed} fatal={res.fatal} "
              f"steady={steady} per_round={rec['per_round']} "
              f"(build {rec['build_s']}s run {rec['run_s']}s)", flush=True)
    Path("/tmp/z3work/price.jsonl").open("a").write(
        "".join(json.dumps(r) + "\n" for r in out))


if __name__ == "__main__":
    main()
