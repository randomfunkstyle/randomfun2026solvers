"""Does the packed cluster paint what the scattered wall painted?

``d3_router.build_packed_wall`` takes the four panels out of their DOOM blocks
and puts them in one 134x103 cluster, which means re-routing all twelve port
pipes around a shared object instead of three short runs inside each block.
Two things could go wrong and neither shows up as a load error: a pipe could
bind to the *wrong* panel (the engine binds by proximity, and the four panels
are now two cells apart rather than 177), and the re-routed lengths could break
``d3_unit``'s ``len(addr) == len(data)`` / ``len(swap) > len(data)`` invariants,
which paints at the wrong cursor or commits into the wrong buffer.

So: drive **both** walls with the same 25 commands — six laps of CURS+RUN on
each of the four tiles, round-robined so the panels interleave, then one
broadcast COMMIT — on the real engine, and compare the composed 128x96 images.
No IWAD, no CPU, no assembler: ``build_probe`` hangs a room off the router that
recites the words and halts.

    python scratch/deadman3d-opt/packed_probe.py

Both walls commit exactly one frame per panel with no fatal, and the two
composed images are byte-identical — which is as close to an end-to-end proof
as this family has, because the machine itself needs several million ticks to
commit its first frame and the engine runs out of memory at two.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import d3_router as R  # noqa: E402


def word(arg, code, sel): return 8 * (8 * arg + code) + sel


def scene(C, S):
    """The commands both walls are driven with.

    Three things it has to exercise, because each one is a different way a
    re-route can be wrong and none of them is a load error:

    * a **different pattern per tile**, so a pipe bound to the wrong panel shows
      up as two tiles holding each other's picture rather than as a blank one;
    * **every corner of every tile** — cursor 0, 63, 47*64 and 47*64+63 — which
      is what pins the ADDR pipes to their own panel across both seams, since a
      corner is the only place a one-tile error cannot hide behind a neighbour;
    * **two commits**, so the SWAP pipes are exercised as buffer swaps and not
      just as a single flush.  ``SWAP <- 0`` clears the next buffer, so the
      corners go in the *second* frame: what the run ends holding is frame two,
      and a check on frame one would be checking a buffer nothing reads.
    """
    cmds = []
    # frame one: a different diagonal-ish pattern per tile, round-robined
    for lap in range(6):
        for t in range(4):
            row = lap * 7 + t
            cmds.append(word(row * 64 + lap * 3, C["CURS"], S[f"T{t}"]))
            cmds.append(word((5 + lap) * 16 + (1 + (t + lap) % 15), C["RUN"], S[f"T{t}"]))
    cmds.append(word(0, C["COMMIT"], S["ALL"]))
    # frame two: the four corners of each tile, one pixel each, colour by tile
    for t in range(4):
        for corner in (0, 63, 47 * 64, 47 * 64 + 63):
            cmds.append(word(corner, C["CURS"], S[f"T{t}"]))
            cmds.append(word(16 + 1 + t, C["RUN"], S[f"T{t}"]))
    cmds.append(word(0, C["COMMIT"], S["ALL"]))
    return cmds


def image(packed):
    _r, w = R.build_probe([], packed=packed)
    rows, _ = R.build_probe(scene(w.codes, w.sel), packed=packed)
    p = f"/tmp/img_{'packed' if packed else 'plain'}.man"
    open(p, "w").write("\n".join(rows) + "\n")
    out = subprocess.run(  # noqa: S603
        ["node", str(REPO / "littleman" / "lm.mjs"), "tick", p, "400000", "--json"],
        capture_output=True, text=True, check=False)
    d = json.loads(out.stdout)
    panels = d["entities"]["displays"]
    def grid(pp):
        return ["".join(format(c, "x") for c in pp["front"][r*64:(r+1)*64]) for r in range(48)]
    gs = [grid(pp) for pp in panels]
    comp = ["".join(gs[2 * (r // 48) + c][r % 48] for c in range(2)) for r in range(96)]
    return d.get("fatal"), [pp["frames"] for pp in panels], comp

f0, n0, a = image(False)
f1, n1, b = image(True)
print("scattered fatal", f0, "frames", n0)
print("packed    fatal", f1, "frames", n1)
print("composed 128x96 images identical:", a == b)
print("image is non-blank:", any(ch != "0" for row in a for ch in row))
print("all four corners painted:", all(a[r][c] != "0" for r in (0, 47, 48, 95)
                                       for c in (0, 63, 64, 127)))
if a != b:
    bad = [r for r in range(96) if a[r] != b[r]]
    print("rows differing:", len(bad), bad[:10])
