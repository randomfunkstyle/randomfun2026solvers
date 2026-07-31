# Why rerouting a return path is worth zero, and what can still pay

Measured at `b115339` (men-v3 86,981,643 / 496x672 / 98.805 t/instr; taped
145,970,818 / 625x396).

## The principle

**CPU return paths here are monotone west-then-north and already Manhattan-exact.
Rerouting one is worth 0 by construction. Only ideas that *delete cells from the
path* or *shrink `lane_x0`* can pay.**

That single sentence explains three separate zeros below, and it is the filter to
apply to any future proposal before spending a build on it.

## Shared discard between the slabs — correct mechanism, zero value

Both drains are `drain.build_drain(0, unit_bits=3, even=True)` — one block, no
per-slab parameter. And **every slab's post-discard continuation is identical**:
`_slab` writes `^` at `exit_x`, rises to the collector, and the collector funnels
west to the fetch for all of them. So there is no per-slab continuation to
remember, and the position-encodes-identity trick the design needed is not needed
at all.

Counted cell by cell on the built grid — routing BRN's taken arm west along row
182 to column 20, north one row, west into BRZ's turn cell (18,181):

  +12 cells of walk, -1 riser cell (exit 189 not 190), -11 collector cells
  (rise at 14 not 25).  **Net 0.**

**Rows saved is 0, not 1**: `bottom = max(slab_at + slab_rows)` is set by *BRZ*
(177+19=196), not BRN, so dropping `slab_rows[BRN]` 19 -> 5 does not move the max.
Buying that row needs BRZ pushed down too, at +2 per taken BRZ.

**Ladder alignment is geometrically impossible**, not merely unmeasured:
`turn_row = s0 + 4` with entry rows stacked one apart, so BRN's ladder is
inherently one row below BRZ's. Aligning them needs BRN to turn west on row 181 —
which is its own `pos` arm (`>W^`), and a westbound man is turned **north** by
that `^`. Freeing it means moving an arm column at the east wall, i.e. the
`MEM_PAD_FOR` re-sweep that cost **+5.76%** earlier today.

## Two hypotheses killed by measurement

* **JMPF joining the drain loses on both tiers.** men-v3 `ops=BRN|BRZ|JMPF` ->
  87,506,145 (**+0.60%**), pad forced 2 -> 3, refuses to bind at pad 2. taped ->
  146,722,784 (**+0.52%**), 625x396 -> **628**x396, pad 1 -> 2. Sharing would not
  rescue it: the shared block exits at row 189 against JMPF's own at 176, ~+16
  cells per execution against a mean discard of only 9.75 words.
* **Sharing cannot buy a bigger unit.** Build-only sweep, men-v3:
  `drain=3,ops=BRZ` still pad 2; `drain=4,ops=BRZ` pad 16; `drain=4,ops=BRN|BRZ`
  pad 17; `drain=5,ops=BRZ` pad 27; `drain=6` refuses. Deleting BRN's eastern `r`
  cluster does not relax §7.1 at all.

For scale, the pool is real even though the sharing is not: **BRN's drain alone is
worth 1,130,455 ticks (1.30%)** — `drain=3,ops=BRZ` measures 88,112,098 at the
same box, pad and band depth.

## Transparent/liveness crossing — dead, for the same reason

The rule is correct: a glyph is safe to cross iff every register it writes is dead
there. On a return path B is live (it carries ACC), A and BP are dead, so the
fatal set is `M W /`, all turns, all pipe glyphs and `H`.

Scanned both tiers with it. Total recoverable across the whole drop band:
**men-v3 one column-step, taped two** — and every one stopped at *its own lane's
last operation*, never at a fatal glyph. `TUCKED_DROPS` already reads `lane_ops`
rather than `lane_end`, so the "`.` is not occupancy" insight was banked long ago.

## What is still alive: the fetch fold

The one idea that **deletes cells from the rigid body** rather than moving them.
See the next commit.
