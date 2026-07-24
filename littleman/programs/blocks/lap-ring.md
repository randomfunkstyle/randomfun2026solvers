# `lap-ring.man` — a pass-through loop tested once per **lap**

`counted_loop` (8 t/v) and `counted_ring` (5 t/v) both spend a `d` and an `m` on
*every value*. This block spends them once per **lap** of a ring whose body is
`[r,s]` repeated K times, so loop control amortises over K values and the cost
approaches the inherent floor of 2 ticks/value (one `r`, one `s`).

The price is that BP now counts **laps**, not values, so the caller must split
its count: `n = K·q + rem`. One `/` does that in a single tick — with A=n and
B=K it leaves **A=q, B=rem** — then `q` laps of the big ring plus `rem` single
values through an ordinary `counted_loop`.

Verified against the reference engine: `12/12` in
[`lap-ring-cases.json`](lap-ring-cases.json), every `r`/`s` confirmed with
`route-check.mjs`.

```sh
node littleman/tools/run-cases.mjs littleman/programs/blocks/lap-ring.man \
     littleman/programs/blocks/lap-ring-cases.json 200000 1
```

## The shipped program (K=8)

Input is `n v1 … vn` (plus filler the block must not touch); output is
`v1 … vn -1`. The trailing `-1` is **test scaffolding, not part of the
primitive** — see "the sentinel" below.

```
+-+  +-----------------+  +-+
|I|>>|@rM8W/b>dWb>d1NsH|>>|O|
+-+  |       mr  mr    |  +-+
     |       ss   s    |
     |       rr  ^<    |
     |       ss        |
     |       rr        |
     |       ss        |
     |       rr        |
     |       ss        |
     |       r         |
     |       ^<        |
     +-----------------+
```

## Cell layout

Interior coordinates, `x` east, `y` south. `xr` is the ring's left column
(`xr = 7` here, just past the head); the ring is **2 wide × (K+3) tall**.

```
row 0:  @ r M 8 W / b   >  d    W  b   >  d   1 N s H
        \_ head _____/   \ring/  \__ remainder loop __/ \sentinel/
```

| cell | glyph | role |
|---|---|---|
| `(xr, 0)` | `>` | ring entry / top-left corner; the return leg arrives heading north and turns east |
| `(xr+1, 0)` | `d` | **the lap test**, top-right corner. BP>0 → CW = south = into the lap; BP==0 → straight east = exit |
| `(xr+1, 1 … K)` | `rsrs…` | right column walked **south**: K cells = K/2 values |
| `(xr+1, K+1)` | ` ` | the parity nop (see below) |
| `(xr+1, K+2)` | `<` | bottom-right corner |
| `(xr, K+2)` | `^` | bottom-left corner |
| `(xr, K+1 … 2)` | `rsrs…` | left column walked **north**: K cells = K/2 values |
| `(xr, 1)` | `m` | the lap decrement, last cell before the entry `>` |

Perimeter = `2(K+3) = 2K+6` cells = `2K+6` ticks per lap, `K` values per lap.

The remainder loop is `Circuit.counted_loop` verbatim (2 wide × 4 tall, `>`/`d`
on row 0, `r`,`s` down the right column, `<`,`^`, `m` on the return leg).

## Measured ticks/value

Marginal cost, measured as `(ticks(110K) − ticks(10K)) / (100K)` — i.e. pure lap
cost with the remainder path idle. Every K hits `(2K+6)/K` **exactly**: the ring
never blocks on a pipe, so there is no hidden penalty.

| K | ring box | ticks/lap | ticks/value | vs `counted_ring` (5.0) |
|---|---|---|---|---|
| 2 | 2×5 | 10 | **5.000** | 1.00× (this *is* `counted_ring`'s geometry) |
| 4 | 2×7 | 14 | **3.500** | 1.43× |
| 6 | 2×9 | 18 | **3.000** | 1.67× |
| 8 | 2×11 | 22 | **2.750** | 1.82× |
| 12 | 2×15 | 30 | **2.500** | 2.00× |
| 16 | 2×19 | 38 | **2.375** | 2.11× |
| 24 | 2×27 | 54 | **2.250** | 2.22× |
| 32 | 2×35 | 70 | **2.188** | 2.29× |

The shipped K=8 program's total tick count is exactly

```
ticks(n) = 17 + 22·⌊n/8⌋ + 8·(n mod 8)
```

confirmed at n = 0, 1, 7, 8, 9, 15, 16, 17, 99, 100, 997, 1000 (17, 25, 73, 39,
47, 95, 61, 69, 305, 313, 2785, 2767). Of the 17 fixed ticks, 9 are the loop
scaffolding the caller pays (5 to split, 1 for the big `d` exit, 2 for `W b`, 1
for the small `d` exit); the rest is the head `r` and the sentinel.

**Whole-loop cost is not `2+6/K`** — the remainder runs at 8 t/v, so for a count
uniform on 0…99 (the `memory` gap distribution) the average is
`9 + (2+6/K)·(n − rem) + 8·rem` with `E[rem] = (K−1)/2`:

| K | 8 | 10 | 12 | 16 | 24 |
|---|---|---|---|---|---|
| E[ticks] for n~U(0,99) | 164 | 162 | 163 | 169 | 187 |

i.e. **~1.5× over `counted_ring`'s 250, and flat from K=8 to K=12** — past that
the remainder loop eats the win. Two ways to push further, both landing at ≈146
(1.7×), which is where this plateaus (arithmetic only, *not* built or verified
here):

- Make the remainder loop a `counted_ring` (5 t/v): K=16 → ≈146. Costs a merge,
  because `counted_ring` has two exits.
- Cascade splits with single-exit pieces only: `n = 16q₁+r₁`, `r₁ = 4q₂+r₂`, then
  `r₂` singles → ≈146 too. The inner stage is just this same generator at K=4,
  which is a verified single-exit ring, so this is the lower-risk of the two.
  Each extra level costs one more `M K W /` (4 ticks); a third level (…→2→1)
  buys nothing.

## Caller-side sequence (exact register contents)

`K` is baked into the geometry; the literal on row 0 must match it (single digit,
or `` `16` `` for K ≥ 10).

| tick | glyph | A | B | BP |
|---|---|---|---|---|
| | | *count* | ? | ? |
| 1 | `M` | count | count | ? |
| 2 | `8` | K | count | ? |
| 3 | `W` | count | K | ? |
| 4 | `/` | **q** | **rem** | ? |
| 5 | `b` | q | rem | **q** |
| | *big ring, q laps* | clobbered | rem (untouched) | → 0 |
| 6 | `W` | **rem** | q | 0 |
| 7 | `b` | rem | q | **rem** |
| | *remainder loop, rem values* | clobbered | q | → 0 |

`r`/`s` touch only A, so **B carries `rem` across the big ring for free** and BP
is the only other live slot. Running the remainder loop *first* also works —
`@rM8W/Wb` → small loop → `Wb` → big ring — verified at the same 2.75 t/v and 5
ticks more overall; its only advantage is that B is free (garbage) *during* the
big ring instead of during the short loop.

### The integration trap for `memory`'s worker

**All three of A, B and BP are live at split time, and the split destroys B.**
The v3 worker's pass-through relies on `B = ±(N−addr)` surviving P1 — carrying
the `op` flag in the sign and the P2 count in the magnitude. A lap-ring needs
B=K for one tick to divide, which wipes that. So a drop-in replacement needs a
third slot; the cheapest options are

- park `addr` (or the flag) in [`scratch-register.man`](scratch-register.man) for
  the duration — ~10-tick round trip, one send at the top, one receive after
  (see [`README.md`](README.md) for its distance-window caveat);
- or recompute: after the loop, rebuild `addr` from what survives and split
  again for P2. A split is only 5 ticks, so doing it twice is cheap; the problem
  is purely that nothing survives to rebuild `addr` *from* once B is gone.

Either way the `±(rem+1)` sign trick composes with the existing one: dispatch on
`X`, and each arm does `b m` / `N b m` exactly as the v3 arms already do.

## Traps hit while building this

1. **The entry must land on the cell immediately before the `d`, or the ring is
   a do-while.** The first draft used a 6×7 hollow square (same perimeter 22,
   same K=8) entered at the top-left corner. The man then walks the top row's
   `r s r s` *before* reaching the test at the top-right corner, so `n=0`
   emitted one value: `want [-1] got [777]`. It measured a perfect 2.75 t/v
   while being wrong — the boundary case is the only thing that caught it.

2. **Consequence: minimum-overhead rings are 2 wide (or 2 tall), not square.**
   For a clockwise convex ring the CW turn on a *straight* segment always points
   into the hole, so `d` can only sit on a **corner**. And the entry, arriving
   from outside, can only land on a corner. "Entry corner immediately followed by
   the test corner" therefore forces two adjacent corners, which in a 4-corner
   rectangle means one side is 2 cells long. Squarer boxes need extra corners.

3. **Extra corners cost exactly one value per lap per pair.** With `c` corners,
   `2K = P − c − 1(m) − 1(nop)`. A ring can be *folded* to fit a box — verified
   with an L-shaped 2-wide ring, K=6 in a 6×6 box, `8/8` cases and exactly
   `20/6 = 3.333` t/v, against `3.000` for the unfolded 2×9. Useful when K+3 rows
   do not fit: at K=32 the straight ring is 35 rows tall.

4. **The perimeter of any rectilinear closed loop is even**, but `2K + 5` is odd,
   so there is always **exactly one leftover nop** — hence `2+6/K` rather than
   the `2+4/K` a naive count suggests. (A blank on a straight run is also the one
   cell a perpendicular corridor could legally cross, since a blank steers
   nobody. Untested, but it is the only door into a hollow ring's interior.)

5. **`m` goes last, just before the entry corner.** With the decrement at the
   *end* of the lap, BP > 0 holds at every corner during the lap, so any corner
   could later be promoted to a second `d`. Putting `m` early forbids that.

6. **`run-cases.mjs` cannot see an over-moving loop on its own.** It stops at the
   first tick where output *length* matches, so a ring that moves n+1 values
   passes silently — the extra value is emitted after the check. Hence the
   `1 N s H` sentinel: the block emits `-1` after the loops, the cases feed three
   extra input values (`777 888 999`), and any over-move shows up as
   `wrong at n: got 777 want -1`. Negative controls confirm the suite bites:
   dividing by 4 while the ring still moves 8 per lap fails from n=7 onward, and
   turning the big ring into a do-while (`d` moved one cell down) fails at n=0.

7. **K must be even** for this 2-column layout, since each column holds K/2
   whole `rs` pairs and a pair may not straddle a corner.
