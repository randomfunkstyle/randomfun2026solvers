#!/usr/bin/env python3
"""The floor-gap table: every block, its floor, its actual, and what the gap costs.

Three currencies, and keeping them apart is the whole point:

*cells/access* -- exact geometry, walked off the shipped grid.

*execs* -- measured at ``stride=1``, so it is a count and not a sample.

*tour ticks* -- what the gap is worth over the 21-round tour.  For a block on the
**critical path** (the CPU man, or the pre-send prefix of any servant the CPU is
blocked waiting for) that conversion is 1:1, because the CPU's blocked time *is*
the store's latency: 94.804 ticks of mean read latency times 324,600 reads is
30.8 M ticks, and the CPU's measured blocked time in the memory lanes is 31.0 M.
For a block's **post-send** walk in a room measured at 90-99 % idle, the rate
table says 0.000 %/tick and the gap is charged **zero** -- which is why a room
can be twelve cells above its lap floor and still be worth nothing to fix.

Numbers marked *modelled* use that conversion; numbers marked *measured* are
read straight off the profile.  Nothing here is estimated.

    python3 table.py
"""

from __future__ import annotations

from heat import TOTAL, load

READS = 324_600
INSTR = 880_332
SEEKS = 8_252
ACCESSES = 471_189
READ_GATE_HOPS = 931_531

#: ``(block, phase, floor, actual, execs, note)``
#:
#: ``phase`` is what decides whether the gap is charged: ``critical`` is the CPU
#: man or a pre-send prefix the CPU waits on (1:1 with tour ticks); ``idle`` is
#: post-send walk in a room measured 90-99 % idle (0.000 %/tick, charged zero);
#: ``blocked`` is a loop whose time is set by something else entirely.
ROWS = [
    # ── on the read's critical path ──────────────────────────────────────────
    ("bank gate v4 -- spine + arm, per hop", "critical", 7, 9, READ_GATE_HOPS,
     "UbW-X then N s; the shipped ^ > dogleg lifts the arm one row off the "
     "X's own exit. Routed floor reached by proposal 1."),
    ("bank worker -- request head r..S", "critical", 12, 15, READS,
     "r b ] - M | v . > | d W M b x r S; three fillers drop two rows into the "
     "ring loop. Needs a pad re-sweep (proposal 3)."),
    ("store answer collector -- R to s", "critical", 2, 4, READS,
     "a 6-cell lap puts its two straight cells diagonally opposite, so R and s "
     "are always half a lap apart. Proposal 2 trades lap 6->8 for latency 4->2."),
    ("adapter -- U * X s (read)", "critical", 4, 4, READS,
     "at the floor: four ops, four cells, the X paying for its own corner."),
    ("bank feed forwarder -- R to s", "critical", 2, 2, 471_189,
     "at the floor."),
    ("bank ring rotation (skip loop), per rotation", "critical", 8, 8, 2_483_000,
     "d r s m: 4 ops + 4 corners - 1 self-turning d = 7, even => 8. At the "
     "floor. The lever is fewer rotations, not fewer cells."),
    # ── the CPU man: every tick is the run ───────────────────────────────────
    ("cpu:seek:flush -- 6-cell lap", "critical", 6, 6, 803_822,
     "r cannot turn and a 4-cell lap is four corners; laps are even because "
     "the grid is bipartite. 6 is provable and shipped. AT FLOOR."),
    ("cpu:seek:discard -- 2x4 counted lap", "blocked", 8, 8, 158_304,
     "at the floor, and 43.6 % blocked on the ROM corridor: the lap is already "
     "shorter than the ROM's delivery period, so shortening it moves nothing."),
    ("cpu:discard:BRN/BRZ -- counted lap", "critical", 14, 14, 164_774,
     "a + m + 8 r = 10 ops, one self-turning => 13, even => 14. AT FLOOR."),
    ("cpu:discard -- the x binary peel", "critical", 3, 3, 64_194,
     "both of an x's exits detour: it has no straight exit. AT FLOOR, and the "
     "counting floor could not have said so."),
    ("cpu:trie -- decode ops per instruction", "critical", 7.94, 7.94, INSTR,
     "measured 8.58 M op-ticks in the box less the 1.59 M the `r b r` fetch "
     "prologue spends inside it, over 880,332 instructions. A uniform depth-5 "
     "trie would floor at 9 (5 tests + 4 shifts); this one comes in UNDER that "
     "because the shallow leaves are genuinely shallow. Nothing to take."),
    ("cpu dispatch+return -- travel per instruction", "critical", None, 25.0,
     INSTR, "fetch+trie+drops+collector+high+riser less their 12 ops/instr. "
     "This is row distance, not slack; prior work put it 0.66 % above the "
     "lane-assignment optimum and the framework has nothing to add."),
    ("cpu:seek:riser -- back to the collector", "critical", 27, 27, SEEKS,
     "27 rows of forced distance: the seek tail sits below the slab band."),
    ("cpu:seek:walk -- send to flush corridor", "critical", 23, 23, SEEKS,
     "hidden latency: the drum is seeking for all of it."),
    # ── off the critical path ────────────────────────────────────────────────
    ("bank ring relay (6-cell)", "idle", 6, 6, 1_321_457,
     "at the floor; and its r blocks the bank's skip loop only 0.1 % of the "
     "time, so it is not the constraint either."),
    ("bank ring relay (10-cell, 4-op)", "idle", 8, 10, 2_215_777,
     "two cells above the lap floor on seven relays. 89-99 % idle and off the "
     "read path: charged ZERO."),
    ("bank gate -- post-send return leg", "idle", 6, 25, READ_GATE_HOPS,
     "the descent, the floor's const reload and the riser. park_const buys "
     "them deliberately: 7 cells of inline literal move off the critical path."),
    ("bank worker -- post-send return leg", "idle", None, 93.7, ACCESSES,
     "row-160 west run + riser + the second skip loop. Bank 0's man walks "
     "24.8 % of the run, so this one is NOT free -- it is the queueing term."),
    ("adapter -- post-send lap", "idle", 6, 8, READS, "92.5 % idle."),
    ("ROM field unpacker >8M8*M2*Mr/b1NsWs", "idle", 15, 18, 4_604,
     "8M8*M2*M builds 128 in 8 cells where `128` is 5; row 2 carries no other "
     "backtick, so the literal would parse. Worth 13,812 ticks. Measured, and "
     "it does not matter."),
]


def main() -> int:
    load()  # fail early if the profile cache is cold
    print(f"{'block':46s} {'phase':>9s} {'floor':>6s} {'actual':>7s} "
          f"{'gap':>6s} {'execs':>11s} {'tour ticks':>12s} {'%run':>7s}")
    out = []
    for name, phase, floor, actual, execs, note in ROWS:
        gap = None if floor is None else actual - floor
        cost = 0.0 if (gap is None or phase != "critical") else gap * execs
        out.append((cost, name, phase, floor, actual, gap, execs, note))
    for cost, name, phase, floor, actual, gap, execs, note in sorted(
            out, key=lambda r: -r[0]):
        f = "--" if floor is None else f"{floor:g}"
        g = "--" if gap is None else f"{gap:g}"
        print(f"{name:46s} {phase:>9s} {f:>6s} {actual:7g} {g:>6s} "
              f"{execs:11,} {cost:12,.0f} {100 * cost / TOTAL:7.2f}")
    live = sum(r[0] for r in out)
    print(f"\n  charged gap, all blocks: {live:,.0f} tour ticks "
          f"({100 * live / TOTAL:.2f} % of {TOTAL:,}) -- modelled")
    print("  every floor/actual is MEASURED off the shipped grid; every exec "
          "count is MEASURED at stride 1;")
    print("  the tick conversion is MODELLED (1 pre-send tick = 1 tour tick "
          "per read, from the CPU's own blocked time).")
    for _c, name, _p, _f, _a, _g, _e, note in out:
        print(f"\n  {name}\n      {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
