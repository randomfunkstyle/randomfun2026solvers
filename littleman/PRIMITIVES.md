# Verified gadgets

Each block here has been run against the interpreter, not just reasoned about.
Coordinates are `(x, y)` with `y` growing downward, so "clockwise" means
east → south → west → north.

## The three laws these are built on

1. **`Y` places children by entry heading.** Entering `Y` heading *h*, the
   order-preserving child is born one cell to the **right** of *h* facing right,
   the newest child one cell to the **left** facing left. Entering east ⇒ south
   and north. Entering north ⇒ east and west.
2. **A closed walk on a grid has even length**, and a walk of *p* moves with net
   displacement *q* has `p ≡ q (mod 2)`. Every loop length below is even because
   it has to be, not because it looked tidy.
3. **`d`/`a` only work as a loop test at a corner.** Mid-edge, `d` with `BP > 0`
   turns the man *off* the path. At a corner the man has to turn anyway, so
   `d` means "turn = stay, straight = leave".

## Spawn ladder — n men, backpacks n−1 … 0, one every 4 ticks

```
d m > Y
Y < m d
```

Cell by cell, with the loop running clockwise from the top-left:

| cell | glyph | role |
|---|---|---|
| (0,0) | `d` | corner, entered north. `BP>0` → east (stay). `BP=0` → north (exit) |
| (1,0) | `m` | `BP −= 1` |
| (2,0) | `>` | **merge point** — a nop for the loop, and it re-aims anything arriving from outside |
| (3,0) | `Y` | entered east: keeper south (3,1), **worker born north at (3,−1) facing north** |
| (3,1) | `d` | corner, entered south. `BP>0` → west (stay). `BP=0` → south (exit) |
| (2,1) | `m` | `BP −= 1` |
| (1,1) | `<` | nop / spare merge point |
| (0,1) | `Y` | entered west: keeper north (0,0), **worker born south at (0,2) facing south** |

Enter at the `>` with `BP = n−1`; that is `r`, `b`, `m` and then walk in from
any direction. The men come out holding `n−1, n−2, … , 0` — the split inherits
the backpack, so **the countdown is a free side effect of the loop counter**.

* Period 8, two `Y`s four cells apart, so the **spawn interval is a uniform 4**.
* An `m` and a `d` alternate all the way round, so the test always fires on the
  value that was just handed out. It terminates on the spawn holding `BP = 0`
  for *either* parity of `n` — a 3×2 loop (period 6, interval 3) is one cell
  tighter but has only two non-corner cells, so it cannot hold both `m`s *and*
  a merge point, and it runs away on half of all `n`.
* Verified: `n = 1, 2, 3, 5, 8` each spawn exactly `n` men, no runaways.

### Why the `>` has to be there

Every predecessor of a loop cell is another loop cell, so an init man has
nowhere to join. The `>` is a cell whose glyph fixes the heading absolutely: it
is transparent to the loop man (already heading east) and rewrites the heading
of anyone dropping in from the north, south or west. Any tight loop that has to
be *entered* needs one.

## Spawned men read in spawn order

Give each `Y`'s worker its own `r` on an equal-length exit path and the men take
values off the single input pipe in birth order — a pipe hands its value to
whoever executes `r` first, and the uniform interval 4 keeps the two exit paths
from ever racing. Verified on `n = 1, 2, 4, 16`: output came back in input order.

This is what makes a burst design possible at all: **read order is free**, so
the reversal has to come from somewhere else — a per-man delay proportional to
the inherited backpack.

## Pair carrier — one man, two values, emitted reversed

```
r M r  …  s W s
```

`r` loads `v₀`, `M` parks it in B, `r` loads `v₁`; later `s` emits `v₁`, `W`
swaps, `s` emits `v₀`. A single man therefore carries **two** list values and
hands them back in reverse. Verified on `n = 4` and `n = 16`.

Pair the spawn count with `]` (`BP >>= 1`) and the machine needs `⌈n/2⌉` men
instead of `n` — `r`, `b`, `]` reads the length and halves it in three cells.
For odd `n` the leftover value is the *last* one read, so it belongs to the
last-spawned man, which is also the man that emits *first*; `x` (turn on BP's
low bit) can branch on the parity of `n` before the `]`.

## The parity wall — why the delay ring cannot be small

Two `Y`s sitting on one closed loop `k` cells apart spawn workers whose birth
*times* differ by `k` and whose birth *cells* differ in `x+y` parity by `k` as
well (the workers sit one cell off their `Y`, so both offsets flip together).
Their bipartite invariants `t + x + y` therefore differ by `2k` — **always
even**, whatever the loop's shape or size.

A man's phase on the ring, `index − time`, has a parity fixed by that invariant,
so *every burst-spawned worker lands in the same parity class*, and an `L`-cell
ring offers a single class only `L/2` slots. Carrying `n` values one-per-man
forces `L ≥ 2n = 32` — which is exactly the ring `reverse-a-list_carrier`
already runs, and no amount of re-routing shrinks it. Flight paths cannot help:
adding cells changes index and time together, leaving `index − time` parity
alone.

Only two things break the wall: **two independent spawn loops** phased an odd
number of ticks apart (the relation between separate loops is free), or the
**pair carrier** above, which halves the number of men and so halves `L`.

## Coupling law for any burst-then-delay machine

Man *i* is born at `T₀ + c·i` holding `BP = n − i`, and emits at

```
E_i = T₀ + c·i + F_i + s·(n − i) + Q_i
```

with `c` the spawn interval, `s` the ticks of delay per backpack unit, `F` the
flight in and `Q` the exit path. Emitting in reverse order needs `E_i > E_{i+1}`
for every *i*, which reduces to

```
s > c − ΔF − ΔQ
```

So **the delay ring can never be tighter than the spawn loop** unless the two
`Y`s get exit paths of different lengths. With the ladder above, `c = 4`, so
`s ≥ 5` with equal paths, or `s ≥ 4` if the two families' paths differ by one.
Total ticks are then `≈ s·n`, which is the whole budget.
