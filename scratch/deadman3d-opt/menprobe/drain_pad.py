"""Why does :data:`machine.SEEK_CLASSIC_DRAIN` move the ``mem_pad`` floor?

The pad search reports only its *last* failure, which is pad 39's and tells you
nothing. Force each pad in turn and print its own message: a §7.1 message names
the glyph, the cell and the rival, which is the whole diagnosis.

    python drain_pad.py 2 9 11 13 15 17 19 21 23 25 27 29
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SLUG, setup  # noqa: E402

d3, hires, M, prog = setup()
KEY = (SLUG, "men-v3")
bits = int(sys.argv[1])
if bits:
    M.SEEK_CLASSIC_DRAIN[KEY] = bits

for arg in sys.argv[2:]:
    pad = int(arg)
    M.MEM_PAD_FOR[KEY] = pad
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except Exception as exc:  # noqa: BLE001
        print(f"  bits={bits} pad={pad:>3}: {type(exc).__name__}: {exc}", flush=True)
        continue
    print(f"  bits={bits} pad={pad:>3}: OK {m.width}x{m.height}", flush=True)
