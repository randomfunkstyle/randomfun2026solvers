"""Where the seek-build's classic slabs are, and which `r` the drain adds lowest.

Pure ``build_cpu`` — no placement, no simulation — so it answers "which slab ties
'rom' against 'mem_resp'" in a second rather than in a 20s build.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SLUG, setup  # noqa: E402

d3, hires, M, prog = setup()


def cpu(bits: int):
    program = M.seek_split(prog, threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS_FOR.get(SLUG, M.SEEK_OPS))
    order = M.LANE_ORDER.get(SLUG)
    if order is not None:
        order = list(order)
        used = {op.mnemonic for op in program.ops_used}
        at = min((order.index(c) for c in ("JMPF", "BRZ", "BRN") if c in order),
                 default=len(order))
        for new in ("JMPS", "BRZS", "BRNS"):
            if new in used and new not in order:
                order.insert(at, new)
                at += 1
    p = M.plan(program, middle_order=order, slots=M.OPCODE_SLOTS.get((SLUG, "men-v3")))
    return M.build_cpu(
        program, p, mem_pad=9, seek=True, drain_unit_bits=bits,
        slab_pitch=M.SEEK_SLAB_PITCH.get(SLUG, M._SLAB_PITCH),
        lane_pitch=M.LANE_PITCH.get((SLUG, "men-v3"), 2),
        squash_band=M.SQUASH_BAND.get((SLUG, "men-v3"), 0),
        straight_trie=(SLUG, "men-v3") in M.STRAIGHT_TRIE,
        tuck_drops=(SLUG, "men-v3") in M.TUCKED_DROPS,
        fold_lanes=(SLUG, "men-v3") in M.FOLDED_LANES,
        seek_taken_drop_east=(SLUG, "men-v3") in M.SEEK_TAKEN_DROP_EAST,
        tight_drops=(SLUG, "men-v3") in M.SEEK_TIGHT_STRUCT_DROPS,
        short_return=True,
        trim_dead=SLUG in M.TRIM_DEAD_LANES,
    )


for bits in (0, 2):
    c = cpu(bits)
    print(f"bits={bits}: {c.width}x{c.height} centre={c.centre}")
    for name, box in sorted(c.regions.items()):
        if ":discard:" in name or name.startswith("cpu:slab") or "slab:" in name:
            print(f"   {name:24s} {box}")
    rs = sorted((y, x) for (x, y), ch in c.cells.items() if ch == "r")
    print(f"   deepest 6 `r` cells (y, x): {rs[-6:]}")
