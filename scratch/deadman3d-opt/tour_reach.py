"""Tour the taped machine with each half of the gate-reach change, and padded.

Four builds on the checked-in 116-round tour, round-gated on the native engine:
the two knobs separately, together, and together with ``chain_pad`` so the
chain link's own tick derivative comes out of a division rather than a pipe
length. Prints ticks and the box for each.

usage: tour_reach.py [name ...]     (default: all)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, "solvers/python")

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
KEY = ("deadman-3d", "taped")

#: name -> (chain reach, request reach, request teleport, chain pad)
CASES = {
    "base": (False, False, True, 0),
    "chain": (True, False, True, 0),
    "roof": (False, True, False, 0),
    "both": (True, True, False, 0),
    "both+pad15": (True, True, False, 15),
    "both+pad5": (True, True, False, 5),
}


def build(chain, roof, teleport, pad):
    for reg, on in (
        (machine.TAPED_CHAIN_REACH, chain),
        (machine.STORE_REQUEST_REACH, roof),
        (machine.STORE_REQUEST_TELEPORT, teleport),
    ):
        reg.add(KEY) if on else reg.discard(KEY)
    try:
        return machine.build_for("deadman-3d", store="taped", store_chain_pad=pad)
    finally:
        machine.TAPED_CHAIN_REACH.add(KEY)
        machine.STORE_REQUEST_REACH.add(KEY)
        machine.STORE_REQUEST_TELEPORT.discard(KEY)


words = [int(w) for w in EX.joinpath("deadman-3d_tour.input.txt").read_text().split()]
cmds = words[len(d3.preamble_words() + d3.title_words()) :]
case = d3.cases_json(cmds)["publicTestData"][0]
inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
frames = [r["frames"] for r in case["rounds"]]

for name in sys.argv[1:] or list(CASES):
    m = build(*CASES[name])
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(inp, frames=frames, max_ticks=6_000_000_000)
    print(
        f"{name:11s} {m.width}x{m.height} adapter->store={m.route_lengths['adapter->store']:3d}"
        f"  ticks={res.step:,} passed={res.passed} fatal={res.fatal} ({time.time() - t:.0f}s)",
        flush=True,
    )
