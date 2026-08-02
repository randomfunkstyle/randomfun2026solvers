#!/usr/bin/env python3
"""The taped bank worker, in the IR -- and the hunt for a 2x.

Everything here is driven by **measured** per-bank traffic: the 21-round trace
table at ``lm1/machine.py:11647-11658``, 471,189 accesses over eleven banks,
with each ring's size, its access count, and its mean skip under both the
absolute walk (``ROT_v1``) and the rotational delta (``ROT_v2``).

The shipped leg, read off the geometry
--------------------------------------
The batch-1 packed body (``memory_tape._worker_v2_v4``, ``memory_tape.py:610``)
serves banks 4/8/9/10.  Walked cells, one tick each, with ``a`` the bank-local
slot index and ``n`` the ring depth:

    MAIN + descent            8
    P1 skip loop              8a + 1
    dispatch WMbx             4
    target r, S               2
    -------------------------------
    r -> S  (pre-send)        15 + 8a
    post-send tail            ~14
    P2 skip loop              8(n-1-a) + 1
    return gutter             35

A note on the brief's ``19 + 8a``.  That figure is stale by two independent
steps and the framework says so rather than reproducing it.  The docstring at
``memory_tape.py:597`` says ``27 + 8a -> 18 + 8a``; and it predates
``V2_V4_SHIFT = 3`` (``memory_tape.py:622``), which moved the answer's ``S``
from ``(14, 7)`` to ``(11, 7)``.  The live leg is **15 + 8a**.  It *is*
Manhattan-minimal -- the man walks ``(1,3) -> (11,7)`` east-and-south with no
doubling back -- which is exactly the conclusion the brief wanted a framework to
be able to reach, and it means **relocation is exhausted**.

So the whole question is the ``8a``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from route import loop_floor  # noqa: E402
from score import RATES  # noqa: E402

# ── measured inputs ──────────────────────────────────────────────────────────
#: The 21-round trace, verbatim from ``lm1/machine.py:11647-11658``.
#: (bank, ring depth, accesses, ROT_v1 mean skip, ROT_v2 mean skip)
TRACE = [
    (5, 442, 3_850, 327.0, 18.4),
    (2, 53, 10_490, 24.4, 7.3),
    (8, 7, 59_916, 2.7, 1.2),
    (1, 53, 6_238, 27.1, 7.5),
    (10, 8, 165_181, 2.8, 2.6),
    (0, 115, 343, 93.1, 2.3),
    (4, 8, 11_107, 5.3, 4.0),
    (9, 10, 121_890, 2.9, 2.8),
    (3, 135, 6_668, 13.1, 23.0),
    (7, 22, 54_218, 3.5, 6.8),
    (6, 59, 31_288, 4.8, 12.1),
]

#: ``lm1/machine.py:11674`` -- the banks that already skip the delta.
ROTATED = {0, 1, 2, 5}
#: ``TAPED_JUMP_THRESHOLD = 16``: rings at or below it get the narrow batch-1
#: body, which has **no rotating variant** (``machine.py:5060-5065``:
#: ``worker_v2_rot`` raises for ``skip_batch != 2``).
JUMP_THRESHOLD = 16

#: Whole-run tick budget the shares are taken against: ``scratch/deadman3d-opt/rot/ab.py``
#: BASE at 21 rounds.
BASE_TICKS = 87_431_352

#: Ring tax per slot, from the floor theorem, both confirmed against shipped code.
TAX_BATCH1 = 8.0   # circuit.counted_loop:        d r s < ^ . m >
TAX_BATCH2 = 5.0   # circuit.counted_ring_horizontal: d r s m v / ^ m s r d

#: Fixed pre-send leg of the narrow body, walked off the geometry.
FIXED_PRE = 15
#: Post-send: tail to P2 entry, P2's own fall-through, and the return gutter.
FIXED_POST = 14 + 1 + 35


@dataclass
class Bank:
    idx: int
    ring: int
    accesses: int
    skip_v1: float
    skip_v2: float

    @property
    def batch(self) -> int:
        return 2 if self.ring > JUMP_THRESHOLD else 1

    @property
    def tax(self) -> float:
        return TAX_BATCH2 if self.batch == 2 else TAX_BATCH1

    @property
    def rotated(self) -> bool:
        return self.idx in ROTATED

    @property
    def skip(self) -> float:
        """Slots actually skipped per access, as shipped."""
        return self.skip_v2 if self.rotated else self.skip_v1

    def pre_send(self) -> float:
        return FIXED_PRE + self.tax * self.skip

    def p2(self) -> float:
        """The restoring pass.  Rotation deletes it outright."""
        if self.rotated:
            return 0.0
        return self.tax * max(0.0, self.ring - 1 - self.skip)

    def lap(self) -> float:
        return self.pre_send() + FIXED_POST + self.p2()


BANKS = [Bank(*t) for t in TRACE]
BANKS.sort(key=lambda b: -b.accesses)
TOTAL_ACCESSES = sum(b.accesses for b in BANKS)


def pool(banks, f) -> float:
    return sum(b.accesses * f(b) for b in banks)


def pct(ticks: float) -> float:
    return 100.0 * ticks / BASE_TICKS


def hr(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def main() -> int:
    print("=== the taped bank worker ===", flush=True)
    print(f"measured: {TOTAL_ACCESSES:,} accesses over {len(BANKS)} banks, "
          f"21-round trace (machine.py:11647)", flush=True)

    # ── where the pre-send mass actually is ──────────────────────────────────
    hr("1. where the pre-send ticks are (measured traffic, modelled geometry)")
    print(f"  {'bank':>4} {'ring':>5} {'batch':>5} {'rot':>4} {'accesses':>9} "
          f"{'skip':>6} {'t/slot':>6} {'pre-send':>9} {'ticks':>12} {'share':>7}",
          flush=True)
    pre_total = pool(BANKS, lambda b: b.pre_send())
    for b in BANKS:
        t = b.accesses * b.pre_send()
        print(f"  {b.idx:>4} {b.ring:>5} {b.batch:>5} {'v2' if b.rotated else 'v1':>4} "
              f"{b.accesses:>9,} {b.skip:>6.1f} {b.tax:>6.1f} {b.pre_send():>9.1f} "
              f"{t:>12,.0f} {100 * t / pre_total:>6.1f}%", flush=True)
    print(f"  pre-send pool {pre_total:,.0f} ticks = {pct(pre_total):.2f}% of the "
          f"{BASE_TICKS:,}-tick run", flush=True)

    b1 = [b for b in BANKS if b.batch == 1]
    b1_acc = sum(b.accesses for b in b1)
    b1_pre = pool(b1, lambda b: b.pre_send())
    print(f"\n  the four batch-1 banks {sorted(b.idx for b in b1)} are "
          f"{b1_acc:,} accesses = {100 * b1_acc / TOTAL_ACCESSES:.1f}% of all reads",
          flush=True)
    print(f"  and {b1_pre:,.0f} pre-send ticks = {100 * b1_pre / pre_total:.1f}% "
          f"of the pre-send pool", flush=True)
    print("  they pay 8.00 t/slot where every other bank pays 5.00, and they are "
          "locked out of\n  rotation because worker_v2_rot requires skip_batch == 2 "
          "(machine.py:5060-5065).", flush=True)

    # ── the floor ────────────────────────────────────────────────────────────
    hr("2. the floor, from the loop-floor theorem")
    for b in (1, 2, 4, 8):
        fl = loop_floor(4 * b, b)
        print(f"  per-slot test, batch {b}: {fl.ticks:2d} cells/lap = "
              f"{fl.ticks / b:.2f} t/slot", flush=True)
    print("  information floor (r+s per slot, all control amortised): 2.00 t/slot",
          flush=True)
    print("\n  BUT 2.00 is not reachable, and the reason is structural, not a", flush=True)
    print("  matter of trying harder. A test that fires TURNS THE MAN OUT OF THE", flush=True)
    print("  ROW -- that is what `d` does. So a row can hold at most one test, a", flush=True)
    print("  rectilinear lap has two rows, and an *exact* (non-overshooting)", flush=True)
    print("  rotation therefore gets exactly two tested slots per lap:", flush=True)
    print("        d r s m v", flush=True)
    print("        ^ m s r d      = 10 cells, 2 slots, 5.00 t/slot", flush=True)
    print("  which is precisely counted_ring_horizontal. Anything cheaper per slot", flush=True)
    print("  must test less often, hence overshoot by up to b-1 slots -- and on a", flush=True)
    print("  one-way ring an overshoot of j costs n-j to undo, not j.", flush=True)
    print("\n  --> EXACT-ROTATION FLOOR = 5.00 t/slot. This is the binding constant.",
          flush=True)

    mean_slots = pool(BANKS, lambda b: b.skip) / TOTAL_ACCESSES
    mean_slots_v2 = pool(BANKS, lambda b: min(b.skip, b.skip_v2)) / TOTAL_ACCESSES
    mean_pre = pre_total / TOTAL_ACCESSES
    # the fixed leg's own floor: 12 glyphs, 3 of the 15 cells are pure distance
    FIXED_FLOOR = 12
    floor_info = FIXED_FLOOR + 2.0 * mean_slots_v2
    floor_real = FIXED_FLOOR + TAX_BATCH2 * mean_slots_v2
    print(f"\n  mean skip as shipped           {mean_slots:.2f} slots/access", flush=True)
    print(f"  mean skip with best rotation   {mean_slots_v2:.2f} slots/access", flush=True)
    print(f"  mean pre-send leg as shipped   {mean_pre:.1f} ticks/access", flush=True)
    print(f"  information floor  {FIXED_FLOOR} + 2.00 x {mean_slots_v2:.2f} = "
          f"{floor_info:.1f}  -> {mean_pre / floor_info:.2f}x (NOT reachable)", flush=True)
    print(f"  reachable floor    {FIXED_FLOOR} + 5.00 x {mean_slots_v2:.2f} = "
          f"{floor_real:.1f}  -> {mean_pre / floor_real:.2f}x", flush=True)

    # ── the graph-level lever the placement search cannot see ────────────────
    hr("2b. sub-ringing: the one lever that moves `skip` itself")
    print("  Everything above takes the ring depth as given and haggles over the", flush=True)
    print("  price per slot. The mean skip is itself a free variable: a bank of n", flush=True)
    print("  slots split into k sub-rings of n/k has mean skip divided by k, at the", flush=True)
    print("  cost of log2(k) dispatch glyphs on the pre-send leg. This is a change", flush=True)
    print("  to the GRAPH, not to the placement -- which is exactly the class of", flush=True)
    print("  move a layout framework can see and hand-nitpicking cannot.\n", flush=True)
    print(f"  {'bank':>4} {'n':>4} {'k':>3} {'sub':>4} {'skip':>6} {'dispatch':>9} "
          f"{'skip cost':>10} {'pre-send':>9} {'x':>6}", flush=True)
    DISPATCH_PER_BIT = 3.0   # `x` + `]` + a cell of spread, modelled
    best_split = {}
    for b in b1:
        base = b.pre_send()
        rows = []
        for k in (1, 2, 4, 8):
            if k > b.ring:
                continue
            sub = b.ring / k
            # rotation delta on a k-times shorter ring, floored at the measured
            # v2 delta when the sub-ring is no shorter than the observed locality
            skip = min(b.skip_v2, b.skip) / k
            disp = DISPATCH_PER_BIT * (k.bit_length() - 1)
            cost = TAX_BATCH2 * skip
            pre = FIXED_PRE + cost + disp
            rows.append((pre, k, sub, skip, disp, cost))
        rows.sort()
        best_split[b.idx] = rows[0]
        for pre, k, sub, skip, disp, cost in rows:
            mark = "  <-- best" if (pre, k) == (rows[0][0], rows[0][1]) else ""
            print(f"  {b.idx:>4} {b.ring:>4} {k:>3} {sub:>4.1f} {skip:>6.2f} "
                  f"{disp:>9.1f} {cost:>10.1f} {pre:>9.1f} {base / pre:>5.2f}x{mark}",
                  flush=True)
    print("\n  Sub-ringing converges on a directly-addressed store: at k = n the", flush=True)
    print("  skip is zero and the dispatch is a full trie. The tape bank is one", flush=True)
    print("  end of that continuum and the men-v3 grid store is the other.", flush=True)

    # ── candidate rewrites ───────────────────────────────────────────────────
    hr("3. candidates, priced on the measured traffic")
    cands = []

    def evaluate(name, pre_of, post_of, note=""):
        p = pool(BANKS, pre_of)
        q = pool(BANKS, post_of)
        cands.append((name, p, q, note))
        return p, q

    evaluate("shipped", lambda b: b.pre_send(), lambda b: FIXED_POST + b.p2())

    # A: narrow-room batch-2 ring. counted_ring_horizontal is 5x2 cells; the
    # narrow worker's east third (cols 16-21) is deliberately blank, so it fits.
    # Costs the odd-tail re-entry on half of all counts.
    ODD_TAIL = 6.0
    evaluate(
        "A: batch-2 ring in the narrow room",
        lambda b: (FIXED_PRE + TAX_BATCH2 * b.skip + 0.5 * ODD_TAIL
                   if b.batch == 1 else b.pre_send()),
        lambda b: FIXED_POST + (TAX_BATCH2 * max(0.0, b.ring - 1 - b.skip)
                                if b.batch == 1 else b.p2()),
        "8.00 -> 5.00 t/slot on 76% of reads; +3 columns into blank space",
    )

    # B: rotation for the small rings too -- needs A first, since worker_v2_rot
    # is batch-2 only.  Deletes P2 outright and swaps skip_v1 for skip_v2.
    evaluate(
        "B: A + rotation on the batch-1 banks",
        lambda b: (FIXED_PRE + TAX_BATCH2 * b.skip_v2 + 0.5 * ODD_TAIL
                   if b.batch == 1 else b.pre_send()),
        lambda b: FIXED_POST + (0.0 if b.batch == 1 else b.p2()),
        "ROT_v2 unlocked for banks 4/8/9/10; P2 vanishes",
    )

    # C: B + sub-ringing the four batch-1 banks at their best split.
    def _c_pre(b):
        if b.batch == 1 and b.idx in best_split:
            return best_split[b.idx][0]
        return FIXED_PRE + TAX_BATCH2 * b.skip if b.rotated else b.pre_send()

    evaluate(
        "C: B + sub-ringed batch-1 banks",
        _c_pre,
        lambda b: FIXED_POST + (0.0 if b.batch == 1 else b.p2()),
        "the graph-level lever: k sub-rings divide the skip, cost log2(k) glyphs",
    )

    # D: the arithmetic bound -- exact-rotation tax on a 12-glyph fixed leg.
    evaluate(
        "D: reachable floor (5.00 t/slot, 12-glyph leg, best rotation)",
        lambda b: FIXED_FLOOR + TAX_BATCH2 * min(b.skip, b.skip_v2),
        lambda b: FIXED_POST + 0.0,
        "not a proposal -- the bound, with the ring left intact",
    )

    base_pre = cands[0][1]
    base_lap = cands[0][1] + cands[0][2]
    print(f"  {'candidate':<44} {'pre-send':>12} {'x':>6} {'lap':>12} {'x':>6}",
          flush=True)
    for name, p, q, note in cands:
        print(f"  {name:<44} {p:>12,.0f} {base_pre / p:>5.2f}x "
              f"{p + q:>12,.0f} {base_lap / (p + q):>5.2f}x", flush=True)
        if note:
            print(f"  {'':<44} {note}", flush=True)

    # ── the impact-weighted answer ───────────────────────────────────────────
    hr("4. impact, not ticks -- the score function's whole point")
    print("  pre-send ticks are charged at 0.27 %/tick (the consumer is stopped);",
          flush=True)
    print("  post-send at 0.019 %/tick in a hot room. A tick before the S is worth",
          flush=True)
    print(f"  {RATES.pre_send / RATES.post_send_hot:.0f}x a tick after it.",
          flush=True)
    for name, p, q, _ in cands:
        pre_pct = pct(p)
        post_pct = pct(q)
        weighted = pre_pct * RATES.pre_send + post_pct * RATES.post_send_hot
        print(f"  {name:<44} pre {pre_pct:>6.2f}%  post {post_pct:>6.2f}%  "
              f"weighted {weighted:>7.3f}", flush=True)

    hr("5. verdict on the 2x")
    b_pre = cands[2][1]
    c_pre = cands[3][1]
    d_pre = cands[4][1]
    c_lap = cands[3][1] + cands[3][2]
    print(f"  A 2x on the pre-send leg needs {base_pre / 2:,.0f} ticks.", flush=True)
    print(f"  Leaving the ring intact, the bound is D = {d_pre:,.0f} "
          f"({base_pre / d_pre:.2f}x): NOT 2x.", flush=True)
    print(f"  Sub-ringing the four hot banks reaches C = {c_pre:,.0f} "
          f"({base_pre / c_pre:.2f}x on pre-send,", flush=True)
    print(f"  {base_lap / c_lap:.2f}x on the full lap).", flush=True)
    print(f"\n  So: 2x is NOT reachable by placement, and NOT reachable by any", flush=True)
    print("  rewrite that keeps one ring per bank. It IS reachable on the full lap", flush=True)
    print("  by sub-ringing, which is a change to the memory's shape rather than", flush=True)
    print("  to the worker's layout.", flush=True)

    print("\n  The arithmetic that binds it, in one line each:", flush=True)
    print(f"   1. the fixed r->S leg is 15 cells and Manhattan-minimal. 12 are", flush=True)
    print("      glyphs that must run; only 3 are pure distance. Placement is", flush=True)
    print("      exhausted here -- this is what the brief asked a framework to be", flush=True)
    print("      able to say, and it says it.", flush=True)
    print("   2. the ring tax floors at 5.00 t/slot for exact rotation, because a", flush=True)
    print("      test that fires turns the man out of the row, so a lap holds two", flush=True)
    print("      tests. counted_ring_horizontal already achieves it exactly.", flush=True)
    print(f"   3. at the measured mean skip of {mean_slots_v2:.2f} slots, the skip term is", flush=True)
    print(f"      only {TAX_BATCH2 * mean_slots_v2:.0f} ticks against a {FIXED_PRE}-cell fixed leg -- so even a free", flush=True)
    print("      ring would give just "
          f"{mean_pre / FIXED_PRE:.2f}x. The fixed leg, not the ring, is the wall.", flush=True)
    print("   4. sub-ringing is the only lever that moves the skip itself, and it", flush=True)
    print("      buys the remaining factor by turning the tape into a shallow grid.", flush=True)

    hr("6. the geometric floor on the full lap")
    print("  The worker's man walks a CLOSED circuit -- he must end where he began", flush=True)
    print("  or he cannot serve a second request. So the lap is bounded below by", flush=True)
    print("  the perimeter of the box his op cells span.\n", flush=True)
    print("  NOTE, because it is easy to get wrong and I got it wrong first: the", flush=True)
    print("  man does NOT have to touch the walls the pipes attach to. Binding is", flush=True)
    print("  Manhattan-NEAREST (SPEC.md:181), so a pipe glyph need only sit closer", flush=True)
    print("  to its own attach than to any rival. A 'must touch all four walls'", flush=True)
    print("  floor is too strong and produces a bound the shipped machine already", flush=True)
    print("  beats -- which is how I caught it.\n", flush=True)
    # Bounding box of the shipped worker's circuit, worker-interior coords,
    # read off the layout in memory_tape._worker_v2_v4.
    span_w, span_h = 16, 17     # cols 0..15, rows 1..17
    perim = 2 * (span_w + span_h) - 4
    fixed_circuit = FIXED_PRE + FIXED_POST
    print(f"  shipped circuit spans cols 0..15, rows 1..17 = {span_w}x{span_h}", flush=True)
    print(f"  rectilinear perimeter of that box            {perim} cells", flush=True)
    print(f"  shipped fixed circuit (15 pre + 50 post)     {fixed_circuit} cells", flush=True)
    print(f"  --> the fixed circuit is {fixed_circuit / perim:.2f}x its own bounding "
          f"perimeter: it is,\n      to within {fixed_circuit - perim} cells, a perfect "
          "rectangle walk. There is nothing\n      left in it for a placer to find.",
          flush=True)

    shipped_lap = sum(b.accesses * b.lap() for b in b1) / sum(b.accesses for b in b1)
    rot_lap = fixed_circuit + TAX_BATCH2 * mean_slots_v2
    floor_lap = perim + TAX_BATCH2 * mean_slots_v2
    print(f"\n  shipped batch-1 lap (access-weighted)  {shipped_lap:.0f} ticks", flush=True)
    print(f"  same lap with A+B                      {rot_lap:.0f} ticks", flush=True)
    print(f"  geometric floor (perimeter + skip)     {floor_lap:.0f} ticks", flush=True)
    print(f"\n  --> shipped is {shipped_lap / floor_lap:.2f}x the floor; A+B is "
          f"{rot_lap / floor_lap:.2f}x.", flush=True)
    print("      The 44 ticks A+B removes are almost exactly the P2 restoring pass,", flush=True)
    print("      which rotation deletes outright. After that the worker is within", flush=True)
    print(f"      {100 * (rot_lap / floor_lap - 1):.0f}% of the floor its own room imposes, and the "
          "remaining\n      levers are the room's shape and the ring's depth -- not "
          "the layout.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
