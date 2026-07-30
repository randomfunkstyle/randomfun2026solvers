"""The joint search: enumerate, price with the tick model, prune with the binding
model, and emit only what survives as specs for :mod:`hz_run`.

    hz_search.py [iters] [--free|--mem]

The user's proposal, at the only granularity where it is tractable.  Cell-level
enumeration is not — the CPU is 51x46 over a ~20-glyph alphabet — but the
*parameter* space is, and it is what has been searched one axis at a time all
day.

**What is new here is not the search, it is what the search is allowed to move.**
Every previous screen over ``LANE_ORDER`` / ``OPCODE_SLOTS`` had to hold
``mem_out_row`` — the median MEM lane row — because moving it unlevels the
store's request leg and the build refuses.  That constraint threw away most of
the space; the frequency-shaping run kept 14 candidates and bound 0.
:func:`hz_geom.repair_dy` removes it: ``store_dy`` moves the store's wall to
wherever the adapter ended up, exactly, for free.  So the search runs
unconstrained and repairs afterwards.

The tick cap is the pruning the proposal asks for.  Priced candidates are ranked
by modelled loop ticks per instruction; anything not better than the shipped
vector by more than the model's own measured residual is dropped before a
builder is ever started.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trie_shape as TS  # noqa: E402

#: Instructions executed over the 21-round tour, from ``exec_hist.py``.  The
#: model prices *per instruction*, so this is what turns it into ticks.
INSTRS = 880_332
BASE_TICKS = 111_492_961


def spec_of(order, slots) -> str:
    """A candidate as a :mod:`hz_run` spec — the two registries plus the repair."""
    return (f"lane_order={'|'.join(order)}"
            f",opcode_slots={','.join(f'{m}:{s}' for m, s in sorted(slots.items(), key=lambda kv: kv[1]))}"
            f",dy=auto")


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 60_000
    mode = "mem" if "--mem" in sys.argv else None
    base_slots = TS.contiguous(TS.DEFAULT_ORDER)
    base = TS.price(TS.DEFAULT_ORDER, base_slots)
    print(f"shipped: loop {base['loop']:.3f} t/instr  mem_out {base['mem_out']}  "
          f"root {base['entry']}  band {base['band']}", flush=True)
    print(f"         = {base['loop'] * INSTRS:,.0f} modelled ticks of "
          f"{BASE_TICKS:,} ({100 * base['loop'] * INSTRS / BASE_TICKS:.1f}% of the run)\n",
          flush=True)

    seen, cands = set(), []
    for seed in range(6):
        for fixed in (True, False):
            order, slots, s = TS.joint_search(seed=seed, iters=iters,
                                              structured_fixed=fixed,
                                              constraint=mode)
            r = TS.price(order, slots)
            key = (order, tuple(sorted(slots.items())))
            if key in seen:
                continue
            seen.add(key)
            d = r["loop"] - base["loop"]
            cands.append((r["loop"], order, slots, r))
            print(f"  seed {seed} {'struct-fixed' if fixed else 'free       '}: "
                  f"loop {r['loop']:7.3f} ({d:+7.3f} t/instr = "
                  f"{d * INSTRS:+12,.0f} ticks, {100 * d * INSTRS / BASE_TICKS:+6.3f}%)"
                  f"  mem_out {r['mem_out']:3d} (shipped {base['mem_out']}, "
                  f"dy repair {r['mem_out'] - base['mem_out']:+d})"
                  f"  root {r['entry']}  cells {TS.opcode_cells(slots):,}", flush=True)

    cands.sort()
    print("\n--- specs for hz_run, best first ---", flush=True)
    for loop, order, slots, r in cands[:5]:
        d = loop - base["loop"]
        print(f"\n# modelled {100 * d * INSTRS / BASE_TICKS:+.3f}%  "
              f"mem_out {r['mem_out']}  root {r['entry']}")
        print(spec_of(order, slots), flush=True)


if __name__ == "__main__":
    main()
