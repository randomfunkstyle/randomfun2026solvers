"""Round-gated 116-round tour for a candidate OPCODE_SLOTS / ROM fold.

Builds the taped machine in memory (nothing in `littleman/examples` is touched)
and runs the checked-in tour on it, so a candidate is priced against the shipped
609,871,597 by the same instrument `tour_gate.py` uses.

    uv run python scratch/band_root_gate.py <candidate> [rom_rows]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine

from band_root_build import CANDIDATES, KEY

EX = Path(__file__).resolve().parents[1] / "littleman" / "examples"


def tour():
    chords = Path("/tmp/t.chords.txt").read_text().strip()
    cmds = [d3.keys("." if c == "," else c) for c in chords]
    case = d3.cases_json(cmds)["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    assert inp.replace(" / ", " ").split() == \
        EX.joinpath("deadman-3d_tour.input.txt").read_text().split(), "tour input drift"
    return inp, [r["frames"] for r in case["rounds"]]


def run(name: str, rom_rows: int | None) -> None:
    machine.OPCODE_SLOTS[KEY] = CANDIDATES[name]
    if rom_rows is not None:
        machine.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rom_rows}
    m = machine.build_for("deadman-3d", store="taped")
    src = "\n".join(m.rows)
    inp, frames = tour()
    t = time.time()
    res = FastLittleman(src).run(inp, frames=frames, max_ticks=6_000_000_000)
    base = 609_871_597
    print(f"{name} rom_rows={rom_rows}: {m.width}x{m.height} "
          f"ticks={res.step:,} passed={res.passed} fatal={res.fatal} "
          f"({100 * (res.step - base) / base:+.3f}% vs shipped) [{time.time() - t:.0f}s]")


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
