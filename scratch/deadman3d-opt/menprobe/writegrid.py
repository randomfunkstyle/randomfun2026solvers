"""Write a built grid to a temp path so the reference WASM oracle can route it.

WAD licence: the grid is IWAD-derived, so it goes to a temp dir and is never
committed (``littleman/DEADMAN-3D.md``).

env: RELAX=1, INWEST=9  -- same knobs as tiegate.py
usage: writegrid.py <store> <out-path>
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402
from tiegate import relax  # noqa: E402


def main():
    store, out = sys.argv[1], sys.argv[2]
    d3, hires, M, prog = setup()
    if os.environ.get("RELAX"):
        relax(M)
    if os.environ.get("INWEST"):
        M.INPUT_NORTH_WEST[(SLUG, "men-v3")] = int(os.environ["INWEST"])
    m = M.build_for(SLUG, program=prog, store=store)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(m.rows) + "\n")
    print(f"{store} {m.width}x{m.height} mem_pad={m.mem_pad} -> {out}")
    # the cells worth asking the oracle about: every MEM-band r/s in the CPU
    R = m.regions
    tx, ty, tw, th = R["cpu:trie"]
    print(f"lane_x0={tx+tw}")


if __name__ == "__main__":
    main()
