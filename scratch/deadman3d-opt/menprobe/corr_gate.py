"""Corridor-length sweep for hires/men-v3, with the two couplings held still.

The ROM->CPU corridor is a FIFO whose length is its capacity, and the seek flush
drains all of it, so ``rom_capacity`` is the flush's word count. Two things make
sweeping it not a one-knob job:

* ``fetch_y = CY + cpu.centre + rom_touch_drop`` and ``squash_band`` moves
  ``cpu.centre`` north, so **a squash of k is a negative drop of k**. ``k`` is a
  knife edge here (7 is the only value that keeps the adapter's request row level
  with the store's wall), so ``drop`` is the only half that can actually move.
* ``build_for`` searches ``mem_pad`` for the smallest *footprint*, and a shorter
  corridor makes the narrow pads infeasible — which is why the shipped
  ``ROM_TOUCH_DROP`` note's drop sweep changed width from row to row and is not a
  corridor measurement at all. Pin the pad and the confound goes away.

    python corr_gate.py 9 0 2 4 5 6 7 8 10 12 16 22
                        ^pad ^drops
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SLUG, setup  # noqa: E402

d3, hires, M, prog = setup()
KEY = (SLUG, "men-v3")

pad = int(sys.argv[1])
if pad >= 0:
    M.MEM_PAD_FOR[KEY] = pad
if (v := os.environ.get("DRAIN_SEEK")):
    M.SEEK_CLASSIC_DRAIN[KEY] = int(v)
if (v := os.environ.get("DRAIN_OPS")):
    M.SEEK_CLASSIC_DRAIN_OPS[KEY] = tuple(v.split(","))

for arg in sys.argv[2:]:
    drop = int(arg)
    M.ROM_TOUCH_DROP[KEY] = drop
    t0 = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except Exception as exc:  # noqa: BLE001
        print(f"  drop={drop:>3}: FAIL {exc} ({time.time()-t0:.0f}s)", flush=True)
        continue
    print(f"  drop={drop:>3}: {m.width}x{m.height} mem_pad={m.mem_pad} "
          f"rom_capacity={m.rom_capacity} ({time.time()-t0:.0f}s)", flush=True)
