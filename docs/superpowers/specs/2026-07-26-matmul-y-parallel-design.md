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
