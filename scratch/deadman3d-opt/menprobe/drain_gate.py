"""Build-only gate for :data:`machine.SEEK_CLASSIC_DRAIN` on hires/men-v3.

Does the ladder+loop even *place* in a seek band, and what does it cost in box?
Ticks are a separate question and a 50x more expensive one, so answer this first.

    python drain_gate.py 0 2 3 4 5
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SLUG, setup  # noqa: E402

d3, hires, M, prog = setup()
KEY = (SLUG, "men-v3")

for arg in sys.argv[1:] or ["0", "2", "3", "4", "5"]:
    # "<bits>[:OP,OP]" — the mnemonics the drain is restricted to.
    bits, _, ops = arg.partition(":")
    t = int(bits)
    M.SEEK_CLASSIC_DRAIN.pop(KEY, None)
    M.SEEK_CLASSIC_DRAIN_OPS.pop(KEY, None)
    if t:
        M.SEEK_CLASSIC_DRAIN[KEY] = t
    if ops:
        M.SEEK_CLASSIC_DRAIN_OPS[KEY] = tuple(ops.split(","))
    t0 = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except Exception as exc:  # noqa: BLE001 — the point is which failure
        print(f"  {arg:>16}: FAIL {type(exc).__name__}: {exc} ({time.time()-t0:.0f}s)",
              flush=True)
        continue
    print(f"  {arg:>16}: {m.width}x{m.height} mem_pad={m.mem_pad} "
          f"rom_capacity={m.rom_capacity} ({time.time()-t0:.0f}s)", flush=True)
