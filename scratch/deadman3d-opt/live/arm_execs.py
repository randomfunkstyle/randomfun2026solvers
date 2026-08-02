#!/usr/bin/env python3
"""Per-*arm* execution counts, which is the granularity a reroute is priced in.

``opcode_execs.py`` says BRN runs 57,416 times.  That is the wrong number for the
riser reroute: a branch slab is an ``X`` fan-out on ``sign(ACC)`` and the three
arms leave by three different columns, so a move on the ``zero`` riser is worth
``2k`` ticks times the number of BRN executions **with ACC == 0**, not times
57,416.  The measured 2,340-tick win on the 3-column BRN move implies ~390
executions of that one arm; this script is where that number comes from instead
of being inferred backwards from a tour.

Counts only; nothing IWAD-derived leaves this script.

    uv run python scratch/deadman3d-opt/live/arm_execs.py [rounds]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    n = int(argv[0]) if argv else 21
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")

    em = Emulator(prog)
    real = em.step
    hits: collections.Counter = collections.Counter()

    def step():  # noqa: ANN202
        acc = em.b          # the emulated ACC lives in B (SPEC/ARCH §5.1)
        op = real()
        if op.mnemonic in ("BRZ", "BRN", "BRZS", "BRNS"):
            arm = "neg" if acc < 0 else ("zero" if acc == 0 else "pos")
            hits[f"{op.mnemonic}.{arm}"] += 1
        else:
            hits[op.mnemonic] += 1
        return op

    em.step = step
    walk = list(hires.WALK[: n - 1])
    res = em.run([Round(input=tuple(hires.input_words(walk)))],
                 max_instructions=200_000_000)
    print(f"reason={res.reason} instructions={res.instructions:,} "
          f"frames={len(walk) + 1}", flush=True)
    for name, c in hits.most_common():
        print(f"  {name:>10}: {c:>12,}", flush=True)
    out = Path("/tmp/d3hires-taped/arm_execs.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"instructions": res.instructions, "frames": len(walk) + 1,
         "counts": dict(hits)}, indent=1))
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
