#!/usr/bin/env python3
"""Where the ``JMPS`` lane lands in ``deadman-3d_hires``' slot map.

A seek build splits long jumps into a ``JMPS`` family (:func:`machine.seek_split`),
so the CPU grows a 22nd lane — and :data:`machine.OPCODE_SLOTS`' hires entry names
only 21, which is why ``build_for(..., seek=True)`` fails outright with "opcode
slot map does not name the used opcodes ['JMPS']".

``_relabel_slots`` is explicit that one registered map serves both builds ("a map
may name opcodes this build does not use, and only those"), so the fix is to name
``JMPS`` — inert for the classic build, which filters the name straight back out.
The only question is *which* slot, and the constraint is rank preservation: the
map must sort the used lanes into the same north-to-south order the default does.

This prints the seek plan's ranks and default slots, the free slots in each gap,
and the drum-cell DP (``hires_slots.py``'s, re-run over the seek histogram) both
unconstrained and with the 21 shipped assignments pinned.

    python scratch/deadman3d-opt/hires_seek_slots.py
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"


def main() -> int:
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    prog = assemble(hires.hires_source(), name=SLUG)
    split = M.seek_split(prog, ops=M.SEEK_OPS)
    shipped = M.OPCODE_SLOTS[(SLUG, "taped")]

    for label, pr in (("classic", prog), ("seek", split)):
        p = M.plan(pr, middle_order=M.LANE_ORDER.get(SLUG))
        by_rank = sorted(p.number, key=lambda m: p.row[m])
        slots = [p.row[m] // 2 for m in by_rank]
        hist = collections.Counter(i.mnemonic for i in pr.instrs)
        print(f"{label}: P={pr.P} lanes={len(by_rank)} k={p.k} slots={1 << p.k}",
              flush=True)
        for r, m in enumerate(by_rank):
            mark = "" if m in shipped else "   <- unnamed by the shipped map"
            print(f"  rank {r:2d}  {m:6s} default slot {slots[r]:2d} "
                  f"shipped {shipped.get(m, '-'):>3}  n={hist[m]:5d}{mark}")
        if label == "seek":
            seek_ranks = by_rank
            seek_cnt = [hist[m] for m in by_rank]
            K = p.k
            LANES = 1 << p.k

    # Which slots are free, and which of them keep the shipped order.
    used = sorted(shipped.values())
    print(f"\nshipped slots {used}")
    print(f"free slots    {[s for s in range(LANES) if s not in set(used)]}")
    idx = seek_ranks.index("JMPS")
    lo = shipped[seek_ranks[idx - 1]] + 1 if idx else 0
    hi = shipped[seek_ranks[idx + 1]] - 1 if idx + 1 < len(seek_ranks) else LANES - 1
    legal = [s for s in range(lo, hi + 1) if s not in set(used)]
    print(f"JMPS sits at rank {idx}, between {seek_ranks[idx - 1]} "
          f"(slot {shipped[seek_ranks[idx - 1]]}) and {seek_ranks[idx + 1]} "
          f"(slot {shipped[seek_ranks[idx + 1]]})")
    print(f"legal JMPS slots (rank-preserving, unused): {legal}")
    for s in legal:
        print(f"   slot {s:2d} -> opcode {M._bitrev(s, K):2d} "
              f"= {2 if M._bitrev(s, K) < 10 else 5} cells x {seek_cnt[idx]} "
              f"= {(2 if M._bitrev(s, K) < 10 else 5) * seek_cnt[idx]:,} cells")

    # And the unconstrained DP over the seek histogram, for reference only.
    N = len(seek_ranks)
    best: dict[tuple[int, int], float] = {}
    INF = float("inf")

    def cells(code: int) -> int:
        return 2 if code < 10 else 5

    def solve(rank: int, slot: int) -> float:
        if rank == N:
            return 0
        if (rank, slot) in best:
            return best[(rank, slot)]
        if LANES - slot < N - rank:
            return INF
        take = seek_cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1)
        best[(rank, slot)] = min(take, solve(rank, slot + 1))
        return best[(rank, slot)]

    sys.setrecursionlimit(10000)
    print(f"\nunconstrained DP over the seek histogram: {solve(0, 0):,} opcode cells")
    sol, rank, slot = [], 0, 0
    while rank < N:
        if seek_cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1) == solve(
            rank, slot
        ):
            sol.append(slot)
            rank += 1
        slot += 1
    print("  " + repr(dict(zip(seek_ranks, sol))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
