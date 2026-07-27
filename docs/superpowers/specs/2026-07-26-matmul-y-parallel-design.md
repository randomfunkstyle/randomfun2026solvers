# matmul: a `Y`-parallel dataflow machine

**Status:** design approved 2026-07-26. Supersedes nothing — `matmul-5818b2cc.man`
and `matmul_cpu.man` both stay on disk as baselines.

## The number to beat

`matmul` is scored `max(w, h)² × avgTicks`. The two baselines:

| grid | footprint | local avg ticks | judged score |
|---|---|---|---|
| `tasks/solutions/matmul_cpu.man` | 85×86 = 7,396 | 102,865 | 1,137,402,365 |
| `tasks/solutions/matmul-5818b2cc.man` | 85×96 = **9,216** | **31,090** | **354M** |
| best team on the scoreboard | — | — | **8.32M** |

Both terms have to move. The leader is 42× ahead of us, which is not reachable by
tuning either one alone.

## Where the current best actually spends its time

Profiled on the 16×16×16 public case (130,644 ticks, `tools/heatmap.mjs`,
stride 4, capped at the settle tick):

* **Exactly one runner does work.** Runner 4 is 0% stalled; the other six are ring
  relay men, 83–99% stalled. The machine is one man walking.
* **37.6% of every tick is two grid rows**, 87 and 88 — the inner multiply-
  accumulate loop. Row 87 is an eastbound ~18-cell body, row 88 the westbound
  return walk that does nothing.
* That is **31.9 ticks per multiply-accumulate**, and roughly half of it is the
  return.

## The four measured facts the design rests on

Probed on the reference wasm (`littleman/lm.mjs`), grids in
`tests/test_matmul_y.py`:

1. **A man moves exactly one value per lap of his cycle.** 53.65 ticks/value on a
   54-cell cycle.
2. **P men sharing one cyclic `r`/`s` path give `cycle / P`**, linear to at least
   8 men: 1/2/3/4/6/8 men → 53.65 / 26.75 / 17.98 / 13.50 / 9.20 / 7.12
   ticks per value.
3. **FIFO order is never violated.** Men on a 1-D cycle cannot overtake each
   other, so they pass every `r` cell and every `s` cell in the same fixed
   rotational order; the sequence of reads and the sequence of writes are
   therefore the same permutation. Verified at every count above.
4. **Pipe length is not the limit.** 2-, 6- and 12-cell I/O pipes give
   6.90 / 6.95 / 7.03 ticks/value at 8 men.

Fact 3 is what makes the whole thing legal, and it is the reason no barrier is
needed between `t` iterations: men consume ring B strictly in cyclic order, so a
man re-entering the cycle behind the others picks up the first `b` of the next
row automatically.

## Architecture — 3 rooms plus I/O

```
   I ──in──▶ ┌──────┐ ──prod──▶ ┌───────┐ ──out──▶ O
             │ MAIN │ ──cmd───▶ │ ADDER │
             └──────┘           └───────┘
              │ ▲ │ ▲             │   ▲
        a_fwd │ │ │ │ b_fwd  cout │   │ cin
              ▼ │ ▼ │             ▼   │
             ┌──────────────────────────┐
             │ RELAY  (one man per ring)│
             └──────────────────────────┘
```

* **`MAIN`** — reads input, fills the rings, runs the hot multiply cycle, drives
  the ADDER. All control lives here.
* **`ADDER`** — owns the accumulator. Three counted phases per row of C, each
  preceded by one count word off the `cmd` pipe.
* **`RELAY`** — one room, several `Y`-seeded men stacked two rows apiece, each
  closing exactly one ring. Incoming pipes on the west wall, outgoing on the
  east, one pipe per row: an `r` on its own pipe's row is at distance 1 and every
  rival is at 1 + |Δrow| ≥ 2, so every glyph binds strictly nearest its own pipe.
  This is `ARCH.md` §8's recommended structural use of `Y`, generalising
  `stream.dual_relay_cells` from two rings to n.

Rings closed through `RELAY`: **A** (N·M ≤ 256 scalars), **B** (M·K ≤ 256),
**C** (K accumulators), and a handful of 1-value register rings for N, M, K and
the loop counters. There is no ROM, no tape and no decode trie — those are what
make the CPU build 85 wide, and the register file is what makes the hand build 96
tall.

## The hot cycle, and the three-register wall

A fused multiply-accumulate needs three live values — the scalar `a`, the operand
`b`, the running sum `c` — and a man has only A and B, since BP cannot be read.
Splitting the add into its own room is what dissolves it:

* **`MAIN`'s cycle is four glyphs:** `r`(b from ring B) `s`(b back to ring B)
  `*`(× a, which sits untouched in B) `s`(product to `prod`).
* **`ADDER`'s cycle is five:** `r`(prod) `M` `r`(cin) `+` `s`(cout).

Neither room ever holds three live values, and ring C never passes through
`MAIN`, so there is no separate forward pass — the accumulator recirculates
continuously. (The `STREAM` block pays a whole `FWD K` command per `MAC K`
precisely because its unit sits inside ring C.)

Laid as tight rectangles both cycles are ~8 cells. At P men in `MAIN` and Q in
`ADDER` the machine sustains `max(8/P, 8/Q)` ticks per multiply-accumulate:

| | 1 man | 2 | 4 | 8 |
|---|---|---|---|---|
| ticks/MAC | 8 | 4 | 2 | 1 |
| vs today's 31.9 | 4.0× | 8.0× | 16× | 32× |

## Why `Y` is load-bearing

Every man in `MAIN`'s cycle multiplies by the **same** `a = A[i][t]`. `Y` is the
only primitive that copies A/B/BP into another runner without a pipe, and a man
cannot read another man's registers. So the scalar reaches P workers one of two
ways, and both are built behind one build-time switch and swept, because which
wins depends on how much area `MAIN`'s code turns out to need:

* **(a) fan-out per `t`.** The man whose BP reaches 0 peels off, reads the next
  `a` from ring A, splits P−1 helpers that inherit it, and the previous helpers
  `H`. No extra storage; ~20–30 ticks per (i, t), so ~6k ticks on the 16³ case.
* **(b) ring A stores every scalar P times.** Man *m* reads every P-th entry, so
  all P men see the same `a` with no fan-out at all and men are seeded once. No
  per-`t` cost; N·M·(P−1) extra pipe cells.

**Count splitting.** `Y` children are identical, so each gets BP = K/P, and
K ∈ 2..16 is not generally divisible by P. Fixed at fill time: pad each row of B
with zeros up to a multiple of P and give ring C the matching extra
accumulators. A padded product is `a·0 = 0`, so the extra accumulators stay 0 and
are discarded at emit. The alternative — differentiating children's counts down
their separate corridors — is possible but branchy, and padding costs a handful
of pipe cells and a few ticks per row.

## `ADDER` phases

`MAIN` sends three count words per row of C. The ADDER's program is then fixed:

```
r(cmd) b ;  loop { r(prod) s(cout) }              # t = 0: seed ring C, no add
r(cmd) b ;  loop { r(prod) M r(cin) + s(cout) }   # t = 1..M-1: accumulate
r(cmd) b ;  loop { r(cin)  s(out) }               # emit row i of C
```

with the counts `K`, `(M−1)·K` and `K`. Seeding on the t=0 pass instead of
injecting zeros is what removes the ZEROC phase, and emitting from the ADDER is
what keeps `MAIN` off ring C entirely. Three sends per row, ≤48 per round —
negligible.

## Stretch: packed SIMD multiply

Entries are −99..99. Pack **3 columns of B into one 64-bit word** in 21-bit
fields. One `*` then computes three products and one `+` accumulates three sums,
because `|Σ_t a·b| ≤ 16 · 9801 = 156,816 < 2²⁰` and the packed accumulator
`|X| ≤ 156816 · (1 + 2²¹ + 2⁴²) ≈ 6.9e17 < 2⁶³`. Exact integer arithmetic
throughout: negatives borrow between fields and the borrows cancel, because the
word is literally `Σᵢ Cᵢ · 2²¹ᶦ`. Extraction at emit is one `%` and one `/` per
output value.

This divides **both** the MAC count and ring B's storage by 3 and composes with
the `Y` parallelism. It is stage 4 because it is the only part that changes the
numerics.

## Stages

Each stage is separately submittable, so a stall still ships an improvement.

1. **Skeleton, P = Q = 1.** 3 rooms, 4 rings, correct on all 7 public cases.
   Establishes the real footprint and the tick baseline.
2. **Parallel cycles.** Sweep P, Q ∈ {2,4,6,8} × scalar broadcast {a, b}.
3. **Footprint pack.** Sweep the ring serpentine shape against `MAIN`'s code box
   for minimum `max(w, h)`.
4. **Packed SIMD multiply.**

## Risks, each checkable before it is expensive

* **Deadlock.** P men can each hold a `b` while waiting on ring B's source cell.
  Ring capacities need ≥ P slack over their contents. Checked by construction and
  by running the 16³ case.
* **Ring binding.** Four-plus rings resolving `r`/`s` in one `RELAY` room by
  Manhattan-nearest is the fiddliest part. The one-pipe-per-row stack makes it
  provable rather than searched, and `tools/route-check.mjs` verifies it against
  the engine. Fallback is per-ring mini-rooms, at the cost of the room budget.
* **Relay throughput.** A relay man forwards one value per lap of an 8-cell
  cycle, so ring B's and ring C's relays need their own `Y` men to keep up with
  P. Same mechanism as the hot cycle, same sweep.
* **`MAIN`'s code area is the number I am least sure of.** It is what sets the
  footprint in the hand-built machine today (its interior is 61×79). If `MAIN`
  will not pack, stage 3 is where the design earns or loses its footprint claim.

## What "done" looks like

Target ~40–45 a side (1,600–2,025) and ~4–8k local average ticks, i.e. a score
in the 20–60M range against today's 354M. Reaching the leader's 8.32M needs
stage 4 to land as well.

Tests assert behaviour, never a recorded score (`AGENTS.md`): outputs correct
round by round, every pipe binds, the checked-in `.man` matches its generator,
and an optimiser candidate beats the baseline from the same run.


---

## Correction (2026-07-27): pipes belong on the **top** wall, not the sides

This design put every incoming pipe on MAIN's west wall and every outgoing on its
east, so that "nearest" collapses to "nearest row" and a glyph binds correctly
wherever it sits horizontally. MAIN was built that way and its 34 pipe ops all
bind correctly. **It is still the wrong shape**, and working the assembly is what
showed it.

A ring has to return to the room it left. With the ports on the side walls, every
ring must **wrap around** the room. Costing the five rings MAIN needs — A, B and
three registers — with a nesting discipline that guarantees planarity (upper east
terminal → further east turn column → further south corridor → further west
riser) gives a bounding box of roughly **95x75**. That is worse than the 70x76
machine it was meant to replace, before a single instruction runs.

Every working matmul grid does the opposite. `matmul-c9920b5f.man` attaches
**all fourteen** of its pipes to the big room's top wall, and eight of them are
2–6 cells long because their relay rooms sit directly above:

    pipe  3 len   2  TOP at x=38        pipe  9 len 246  TOP at x=42
    pipe  7 len   2  TOP at x=31        pipe 11 len 212  TOP at x=57
    pipe  2 len   3  TOP at x=32        pipe 13 len 106  TOP at x=43
    ...                                 pipe 12 len  72  TOP at x=58

Only the four storage rings are long; the register rings cost almost nothing.

The consequence for layout is the mirror of the rule this design was built on.
With ports on the top wall the vertical term `(y - top)` is the same for every
glyph deep in the room, so **binding is by column**, and a loop body has to run
*horizontally* with blanks padding each glyph onto its own pipe's column —
`circuit.counted_loop_horizontal`, not `counted_loop`. That is also the reason the
existing machine's inner multiply loop has gaps in it, and why those gaps are
load-bearing: each `r`/`s` sits at the column its pipe attaches at.

So a rebuild wants: ports on the top wall, relay rooms in a band directly above,
horizontal counted loops with column-ordered bodies, and the two long rings
serpentined below. MAIN as committed in `matmul_main.py` keeps its program, its
register discipline and its marker test, but its geometry has to be transposed.

### The blueprint that follows from top-wall ports

With **every** port on one wall the vertical term `(y - top)` is identical for all
of them, so binding reduces to `|x - col|` alone — a pipe op binds whichever pipe
is nearest **in column**, at any row. Two consequences, and they pull opposite
ways, which is what the layout has to exploit:

* a **vertical** loop body keeps every glyph in one column, so all of them bind
  the *same* pipe — exactly right for repeated sends (`sWsWs` to `cmd`, the gauge
  fill), useless for anything touching two pipes;
* a **horizontal** body walks across columns, so it can touch several pipes — the
  only shape the MAC and the fills can take.

Pipe columns are therefore not free either; they have to be laid out so each
body's glyphs land on their own pipes. One assignment that satisfies all three
bodies at once, descending:

    b_ret 20   in 19   a_fwd 18   b_fwd 17   (*) 16   prod 15

    fill A   `rs`       r(in)@19  s(a_fwd)@18
    fill B   `r s`      r(in)@19  ...  s(b_fwd)@17
    the MAC  `r  s*s`   r(b_ret)@20 ... s(b_fwd)@17  *@16  s(prod)@15

The MAC's blanks at columns 19 and 18 are not padding to taste — they are what
puts `s(b_fwd)` on its own pipe's column while `r(b_ret)` sits on its own. That is
the same reason the existing machine's inner loop has gaps, arrived at from the
generator side.

Remaining ports (`a_ret`, the three register pairs, `cmd`) are used only in
straight-line code and in single-pipe vertical loops, so they can take any spare
columns. Relay rooms sit in a band directly above, keeping those pipes 2–6 cells
long, and the two long rings serpentine below.

### Sizing it — and a correction

My first pass put the big room at 52 columns and concluded the architecture floors
at ~22M. That was wrong, and wrong in the direction that matters: the room does not
need to be as wide as the code is long. It needs to be as wide as the **port
columns**, because that is the only thing a glyph's column has to reach. Thirteen
ports at consecutive columns plus the MAC body's six-glyph span and a corridor is
about **22 columns**, not 52 — the program's length goes into *rows*, not columns,
since a serpentine can hit several ports per row.

    big room     22 x 20  (interior; ~260 cells of path for ~80 ops)
    relay band   6 rooms at 6x4, two rows of three     ~10 rows
    ring band    rings serpentined at 22 wide

                          rings      ring rows   total h   footprint   judged   score
    no packing         531 cells        24          54       2,916     ~17,000   50M
    3-wide SIMD on B   360 cells        17          47       2,209      ~8,000   18M

So the architecture does reach under 20M, but only with the packing, and only if
the room stays near 22 columns. The lever on the remaining margin is the **port
count** — six of the thirteen are the three register rings, and replacing them with
`q` gauges costs one port each instead of two. That is where to look next, not at
the code.


---

## The row map is what makes the assembly planar (2026-07-27)

Four hand placements of ring A and ring B all collided, at a different cell each
time. They were not bad luck. With two rings whose serpentine bands sit on opposite
sides of MAIN, planarity is **forced** by the row map, and my map violated it.

Take two pipes leaving the east wall, one turning up and one turning down. Pipe i
has a horizontal leg at its own row spanning `45..C_i`, then a vertical run at
`C_i`. Crossing is avoided only if:

* the pipe with the **upper** terminal turns **up** and the lower one turns
  **down**. The other way round gives `C_b > C_a` and `C_a > C_b` at once — a
  contradiction, so no column assignment exists.
* and symmetrically for the returns: the ring whose band is **above** must have
  the **smaller** `ret` row, or its descent crosses the other's horizontal leg
  while the other's ascent crosses its own.

The old map had `a_ret=8` and `b_ret=4` with ring A's band above, which breaks the
second condition — so every column choice collided, which is exactly what happened.

A map that satisfies both, keeping every loop body legal:

    a_ret 2   in 3   a_fwd 4   b_ret 8   b_fwd 9   (*) 10   prod 11

    fill A   `rs`        r(in)@3  s(a_fwd)@4
    fill B   `r     s`   r(in)@3  ...  s(b_fwd)@9
    the MAC  `rs*s`      r(b_ret)@8  s(b_fwd)@9  *@10  s(prod)@11

`a_ret` is now above `b_ret`, so ring A's band goes above and ring B's below, and
`a_fwd` (row 4, upper) turns up while `b_fwd` (row 9, lower) turns down. Both
conditions hold and the columns are then free.

The MAC keeps its 12-cell cycle. Fill B grows from a 10-cell cycle to 18, because
`in` and `b_fwd` are now six rows apart — that costs ~2k ticks on the 16x16x16
case, and is the price of a routable machine.

### A third constraint: a ring must not cross *itself*

The two rules above stop ring A crossing ring B. They do not stop a ring crossing
itself, and that is what the next four failed routes were.

`a_fwd` leaves MAIN at its own row and rises to its band, so its **rise column
blocks every row between the two**. `a_ret` comes back to MAIN's wall along its own
row, and that approach has to cross the rise column — so `a_ret`'s row must lie
*outside* the span `(band, a_fwd)`. With the band above MAIN, "outside" means
**below** `a_fwd`.

Found the same way as the others: by searching candidate routes and reading the
collisions rather than deriving them. Twelve candidates for `a_ret`, and the first
three all failed at `collision at (56, 8): '-' vs '|'` — a_fwd's own leg along the
foot of its band.

All three conditions together, and a map satisfying them:

    a_ret below a_fwd          (a ring must not cross itself)
    a_ret above b_ret          (band-above ring takes the smaller ret row)
    b_ret above b_fwd          (upper terminal turns up, lower turns down)

    (fill loop top) 2   in 3   a_fwd 4   a_ret 6   b_ret 8   b_fwd 9   (*) 10   prod 11

MAIN stays 43x28 with all 34 pipe ops bound. `a_ret` is read only in the drive loop,
never in a counted body, so moving it costs nothing.

### The escape order is global: up-pipes above down-pipes

`prod` would not route, and the collision was the same cell every time —
`(46, 49)`, which is `b_fwd`'s horizontal leg. That is not a column-choice problem,
it is a **river-routing** constraint over all thirteen of MAIN's wall pipes at once.

Split them by which way they leave the wall:

* **up** — into the band above: `a_fwd`, `a_ret`, and (as placed) `prod`, `cmd`
* **down** — into the band below: `b_fwd`, `b_ret`
* the three register rings are two cells long and have no vertical run at all

Within one direction the rule is the familiar one: of two up-pipes the one with the
**lower** terminal turns further east; of two down-pipes, the **upper** one does.
Across directions there is a second condition, and it is the one that bit: an
up-pipe's horizontal leg runs from the wall out to its turn column, crossing every
down-pipe's column, and a down-pipe's vertical spans from its own row downwards. So
**every up-pipe's row must lie above every down-pipe's row.**

With the ADDER above MAIN that fails: `prod`@11 and `cmd`@26 go up, but `b_ret`@8
and `b_fwd`@9 go down and sit above them. No column assignment exists, which is
exactly what 110 candidates reported.

**Put the ADDER below MAIN**, alongside ring B's band. Then

    up   = a_fwd 4, a_ret 6
    down = b_ret 8, b_fwd 9, prod 11, cmd 26

and every up row is above every down row. The condition holds, the columns are
free again, and ring C's relay and the O room go in the same lower band as the
ADDER — which also keeps `out` short.

### `prod` and `cmd` cannot both be simple L-routes: mirror the ADDER

Moving the ADDER below MAIN satisfies the up/down separation, but `prod` and `cmd`
still cannot both reach it. Both run from MAIN's east wall (rows 51 and 66) down to
the ADDER's **west** wall (rows 92 and 96), so both are L-routes going east, south,
then west, and:

* `cmd`'s vertical crosses `prod`'s westward leg unless `C_cmd > C_prod`;
* `cmd`'s **first** leg, at row 66, crosses `prod`'s vertical (spanning 51..92)
  unless `C_cmd < C_prod`.

Contradiction, and adding a jog to either one reproduces it one column over — the
two conditions are on opposite sides of the same inequality however the path is
folded.

The cause is that both pipes come from the north-east and the ADDER presents its
ports on the **west**. So **mirror the ADDER**: reflect its interior horizontally,
swapping `<` and `>`, so `cin`, `prod` and `cmd` land on its east wall and `cout`
and `out` on its west. Then both feeds approach from the side they are already on
and descend without crossing, and `out` runs west to the O room instead of east.

**That does not work, and the reason is structural: a horizontal mirror is not a
symmetry of this language.** Tested on the engine — the mirrored ADDER probe emits
nothing, on every shape.

Two independent reasons, either fatal:

* **Flipping an arrowhead reverses its pipe's flow.** `>` becomes `<`, so a pipe
  that carried values *into* the ADDER now carries them out. A mirror does not
  reflect the dataflow graph, it inverts it.
* **`@` always spawns facing east** (SPEC), and no transformation of the grid
  changes that, so every man in a mirrored room starts walking the wrong way.
  Patching a `<` into the cell east of each spawn does not rescue it either — the
  pipe inversion remains.

So the ADDER has to be **re-derived** with its ports on the east wall, not
transformed. Its three counted phases and their row order (which is what makes the
accumulate body read `prod` before `cin`) have to be laid out afresh against
east-facing ports.

Also ruled out: `layout.py`'s `AStarRouter`, which was the obvious tool for the
remaining pipes. It has no notion of pipe capacity and `score()` *minimises* total
pipe length — but a pipe's capacity **is** its length, and ring A and ring B need
257 cells each. A router that shortens pipes optimises against the one hard
requirement, so it would produce a grid that loads, binds correctly, and deadlocks.
Capacity-aware routing is the unbuilt piece `task.md` calls for.
