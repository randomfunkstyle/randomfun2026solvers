#!/usr/bin/env python3
"""How many times does each opcode actually execute, over a short hi-res tour?

A drop column saved is worth ``2 * columns * executions`` ticks — the eastward
walk out to the turn and the westward walk back along the collector.  Which
makes the *frequency* the whole question: a 20-column saving on an opcode that
runs 21 times is 840 ticks against 191 million.

Counts only; nothing IWAD-derived leaves this script.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

KNOBS = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True, lap_via_jump=True)


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    n = int(argv[0]) if argv else 3
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")

    em = Emulator(prog)
    real = em.step
    hits: collections.Counter = collections.Counter()

    def step():  # noqa: ANN202
        op = real()
        hits[op.mnemonic] += 1
        return op

    em.step = step
    walk = list(hires.WALK[: n - 1])
    res = em.run(
        [Round(input=tuple(hires.input_words(walk)))], max_instructions=200_000_000
    )
    from randomfun2026solvers.lm1 import display
    fr = display.tiled_frames_from_writes(res.wall_writes)
    print(f"reason={res.reason} instructions={res.instructions:,} "
          f"wanted_frames={len(walk) + 1} got_frames={len(fr)} "
          f"writes={len(res.wall_writes):,}")
    for name, c in hits.most_common():
        print(f"  {name:>5}: {c:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
