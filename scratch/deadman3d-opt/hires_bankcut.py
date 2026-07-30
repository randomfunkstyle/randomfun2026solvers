#!/usr/bin/env python3
"""``TAPED_BANKS`` for ``deadman-3d_hires``: the per-address cut, not just the order.

``hires_banks.py`` measured this family's traffic over the **uniform quarters**
``taped_plan`` hands it when the registry has no entry, and used the answer to
pick :data:`machine.TAPED_BANK_ORDER`.  It never used it to pick the *sizes* —
so hires still runs 90.79% of its reads through a 223-slot ring while
``deadman-3d``, on the same code path, cut its hot bank to 69.

This traces the same abstract wire per **address** (``hires_banks.Tracing``),
differences a gameplay run against boot so the histogram is per gameplay frame,
and then runs ``bankdp``'s cost model over every contiguous split and every
chain order ``memory_taped.gate_chain`` can actually build.

    python scratch/deadman3d-opt/hires_bankcut.py [frames] [nbanks ...]

Writes ``hires_traffic.json`` beside itself so the DP can be re-run without
paying for the trace again.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

HERE = Path(__file__).resolve().parent
TRAFFIC = HERE / "hires_traffic.json"
WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"

#: ``ARCH.md`` §4.1: the ring tax is ~8 ticks a slot per access at
#: ``skip_batch=1``, which is what hires runs (``TAPED_SKIP_BATCH`` has no entry
#: for it).  ``HOP`` is a pass-through gate ahead of the bank, from ``bankdp``.
RING = 8.0
HOP = 21.0


def trace(frames: int) -> dict:
    """Per-address reads/writes a gameplay frame, boot differenced out."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round
    from randomfun2026solvers.lm1.store import DictStore

    sys.path.insert(0, str(HERE))
    from hires_banks import Tracing  # the same wire the order was read off

    hires.install_wad(WAD)
    prog = assemble(hires.hires_source(), name=SLUG)
    tape_n = max(d3.tape_slots(d3.GEOM128).values()) + 1

    def run(cmds: list[int]) -> list[tuple[int, int]]:
        tr = Tracing(DictStore())
        Emulator(prog, store=tr).run(
            [Round(input=tuple(hires.input_words(cmds)))], max_instructions=400_000_000)
        return tr.log

    print(f"tape {tape_n} slots; tracing boot...", flush=True)
    boot = run([])
    print(f"  boot {len(boot):,} accesses; tracing {frames} frames...", flush=True)
    play = run(list(hires.WALK[:frames]))
    print(f"  play {len(play):,} accesses", flush=True)

    def tally(log, key):
        out: dict[int, int] = {}
        for op, addr in log:
            if op == key:
                out[addr] = out.get(addr, 0) + 1
        return out

    data = {"tape_n": tape_n, "frames": frames, "reads": {}, "writes": {}}
    for name, key in (("reads", 0), ("writes", 1)):
        b, p = tally(boot, key), tally(play, key)
        for addr in set(b) | set(p):
            per = (p.get(addr, 0) - b.get(addr, 0)) / frames
            if per > 0:
                data[name][str(addr)] = per
    return data


def dp(acc: list[float], top: int, nb: int) -> tuple[float, tuple[int, ...]]:
    """Cheapest contiguous split into ``nb`` banks, ring term only.

    ``chain_position`` depends on the *order*, which is chosen after the split
    (:func:`best_order`), so the DP minimises the ring term alone and the hop
    term is added per candidate.  The ring term dominates by ~10x here — a
    223-slot bank is 1,792 ticks an access against a gate's 21.
    """
    pre = [0.0] * (top + 2)
    for a in range(1, top + 1):
        pre[a] = pre[a - 1] + acc[a]

    def A(lo, hi):
        return pre[hi] - pre[lo - 1]

    # best[k][a] = cost of covering 1..a with k banks; cut[k][a] = previous edge
    INF = float("inf")
    best = [[INF] * (top + 1) for _ in range(nb + 1)]
    cut = [[0] * (top + 1) for _ in range(nb + 1)]
    best[0][0] = 0.0
    for k in range(1, nb + 1):
        for a in range(k, top + 1):
            row, lo = best[k - 1], k - 1
            for prev in range(lo, a):
                if row[prev] == INF:
                    continue
                c = row[prev] + A(prev + 1, a) * RING * (a - prev + 1)
                if c < best[k][a]:
                    best[k][a], cut[k][a] = c, prev
    sizes, a = [], top
    for k in range(nb, 0, -1):
        prev = cut[k][a]
        sizes.append(a - prev)
        a = prev
    return best[nb][top], tuple(reversed(sizes))


def best_order(sizes, accs) -> tuple[tuple[int, ...], float]:
    """Cheapest chain order ``gate_chain`` accepts, by interval DP.

    A gate peels an **end** of what it is handed, so the banks still unplaced
    are always a contiguous interval and the next position is fixed by its
    width — which makes the reachable set exactly ``2**(nb-1)`` and the choice
    an ``O(nb**2)`` recursion rather than an enumeration of it. Enumerating
    was fine at four banks and is two billion orders at thirty-two.
    """
    nb = len(sizes)
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def f(lo: int, hi: int) -> tuple[float, tuple[int, ...]]:
        pos = nb - (hi - lo + 1)
        if lo == hi:
            return accs[lo] * HOP * pos, (lo,)
        a, ta = f(lo + 1, hi)
        b, tb = f(lo, hi - 1)
        ca, cb = accs[lo] * HOP * pos + a, accs[hi] * HOP * pos + b
        return (ca, (lo,) + ta) if ca <= cb else (cb, (hi,) + tb)

    c, order = f(0, nb - 1)
    return order, c


def cost(sizes, accs) -> tuple[float, tuple[int, ...]]:
    ring = sum(a * RING * (m + 1) for a, m in zip(accs, sizes, strict=True))
    order, hop = best_order(sizes, accs)
    return ring + hop, order


def bank_accs(acc: list[float], sizes) -> list[float]:
    out, lo = [], 1
    for m in sizes:
        out.append(sum(acc[lo: lo + m]))
        lo += m
    return out


def main(argv: list[str]) -> int:
    frames = int(argv[0]) if argv else 4
    nbs = [int(x) for x in argv[1:]] or [4, 5, 6, 8]

    if TRAFFIC.exists():
        data = json.loads(TRAFFIC.read_text())
        print(f"reusing {TRAFFIC.name} ({data['frames']} frames)")
    else:
        data = trace(frames)
        TRAFFIC.write_text(json.dumps(data))
        print(f"wrote {TRAFFIC.name}")

    top = data["tape_n"] - 1
    acc = [0.0] * (top + 2)
    reads = writes = 0.0
    for k, v in data["reads"].items():
        if int(k) <= top:
            acc[int(k)] += v
            reads += v
    for k, v in data["writes"].items():
        if int(k) <= top:
            acc[int(k)] += v
            writes += v
    print(f"top address {top}; {reads:,.0f} reads + {writes:,.0f} writes a frame\n")

    from randomfun2026solvers import memory_taped as mt

    def report(label, sizes):
        accs = bank_accs(acc, sizes)
        c, order = cost(sizes, accs)
        try:
            mt.gate_chain(list(sizes), order=list(order))
            ok = "ok"
        except Exception as exc:  # noqa: BLE001
            ok = f"REJECTED {exc}"
        share = " ".join(f"{100 * a / (reads + writes):5.1f}%" for a in accs)
        print(f"{label:24} {str(sizes):34} order {str(order):16} "
              f"cost {c:12,.0f}  [{share}]  {ok}")
        return c

    base = mt.taped_plan(data["tape_n"], 4)
    ref = report("uniform quarters (now)", tuple(base))
    for nb in nbs:
        _, sizes = dp(acc, top, nb)
        c = report(f"DP {nb} banks", sizes)
        print(f"{'':24} vs uniform: {100 * (c - ref) / ref:+.1f}% of modelled access ticks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
