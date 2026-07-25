# Where else does dataflow win? — all 16 problems, ranked

`matmul` was unscoreable and is now 96×96 / 1.18bn, because it stopped being a
program running on a CPU and became **three rings and an adder** (`lm1/stream.py`,
`ARCH.md` §4.1). This document asks the obvious follow-up — *which of the other
fifteen problems does that trade unlock or improve, and by how much* — and answers
it with measured numbers rather than enthusiasm.

The short version, and it is not the answer the question implies:

> **Dataflow has already won almost everywhere it can win, and nobody wrote it
> down.** Four problems sit in `tests/test_lm1_programs.py`'s `BLOCKED` set.
> Three of them — `memory`, `reverse-a-list`, `sort-numbers` — are *already*
> solved by generator-emitted dataflow grids that beat every CPU-generated
> machine in the repo by **two to four orders of magnitude**. `BLOCKED` does not
> mean "unsolved"; it means "no `.asm` solves it", which is a statement about
> `lm1/machine.py`, not about the problem. The one genuinely unsolved problem is
> **`subset-sum`**, and §4 shows it *is* reachable — the wall the CPU hits is
> instruction issue, and issue is exactly what a bespoke grid deletes.

## 1. The three numbers that decide everything

**Cost per unit of work, measured on the engine.**

| mechanism | ticks | ratio |
|---|---|---|
| one glyph the man walks over (bespoke grid) | **1** | 1× |
| one rotation of a pipe ring (`r`+`s` in a counted loop) | **~3.2** | 3× |
| one relay lap (`memory.man`'s 4×3 room, 6-cell cycle) | **~6** | 6× |
| one issued LM-1 instruction (fetch + trie + execute + return) | **46** | 46× |
| one tape access at N=97 | **~780** (`67 + 7.35·N`) | 780× |

The planning model stays `ticks = 46·I + A·(67 + 7.35·N) + 7·W`; it over-predicts
the engine by +0.5%..+6.4%, against +4.9%..+10.9% for `62 + 8.0·N`. (The additive
form is physically wrong — a read unblocks the CPU when the answer is *sent*, not
when the tape finishes its lap, so the truth is a `max(…)` — but it is the best
*calibrated* predictor we have, so use it and do not believe the mechanism.)

**Where each shipped machine's ticks actually go.** This is the table that decides
which rewrite is worth doing, because a ring deletes the *tape* column and a
station deletes the *issue* column:

| program | tape N | issue | tape | ROM recirc | what dataflow can take |
|---|---|---|---|---|---|
| `palette` | 3 | **92%** | 7% | 1% | ~0% from rings; **all of it** from deleting issue |
| `brackets` | 8 | 37% | 46% | 18% | ~46% from a ring stack |
| `plotter` | 11 | 28% | 57% | 15% | ~57% |
| `sudoku-validity` | 31 | 22% | 53% | 25% | ~53% + most of the 25% |
| `tcp` | 52 | 16% | **80%** | 5% | **~80%** |
| `gradebook` | 94 | 7% | 62% | **30%** | ~62% + the 30% |
| `subset-sum` | 97 | 8% | 72% | 21% | 72%, and that is still not enough — §4 |

Read `palette` carefully: it is the one machine a ring cannot help at all. Its
1,024 pixel writes are pure instruction issue. That is the general rule — **a ring
is a memory optimisation, not a compute one.** The compute optimisation is
deleting the CPU, and that is a different and much larger job.

**Footprint, which is squared and therefore usually decides.** A dataflow grid
deletes the ROM, the decode trie, the lane band, the adapter and the tape, and
every generated machine is ~112 columns because of the adapter plus the 32-wide
tape. So the bespoke grids come out at 8×8 … 32×32 where the generated ones come
out at 98×75 … 112×116. That is a **12×–200× factor on `max(w,h)²` alone**, before
a single tick is saved. It is why the five bespoke grids beat their LM-1
equivalents by orders of magnitude and why `triangle` is 960 by hand against
471,744 generated.

## 2. The ranking

Fit: **A** = fixed schedule, sequential, state fits as pipe length — build it.
**B** = data-dependent control but the *structure* is a ring — buildable, needs a
station with an `X` or `d` branch in it. **C** = needs random indexed access; a
ring only helps if the addressing can be turned into rotation. **D** = no memory
traffic to delete; the cost is issue.

| # | problem | fit | resident state | now | projected | unblocks or improves | confidence |
|---|---|---|---|---|---|---|---|
| 1 | **`subset-sum`** | B | 21 packed cells | **unscoreable** (6/7, one case 34× over) | ~50×50 × ~1.4M = **~3.4e9** | **UNBLOCKS** the last unsolved graded problem | med-high (§4) |
| 2 | `tcp` | A | 18 (16-slot window + 2) | 112×77 = **1.195e9** | ~30×30 × ~15k = **~1.4e7** | improves **~85×** | high |
| 3 | `sudoku-validity` | B | 27 masks | 98×91 = **12.71e9** | ~34×34 × ~120k = **~1.4e8** | improves **~90×** | high |
| 4 | `brackets` | A | 35 (32-deep stack) | 98×75 = **0.309e9** | ~26×26 × ~4k = **~2.7e6** | improves **~110×** | high |
| 5 | `gradebook` | C | 80 (16 packed) | 112×103 = **8.71e9** | ~40×40 × ~250k = **~4e8** | improves ~20× | medium |
| 6 | `max-element` | A | 2 | not built | ~14×14 × ~4k = **~8e5** | new solve (practice-tier) | high |
| 7 | `atoi` | A | 2 | not built | ~16×16 × ~1k = **~2.6e5** | new solve (practice-tier) | high |
| 8 | `plotter` | D/C | ~7 scalars | 112×116 = **2.75e9** | ~40×40 × ~400k = **~6.4e8** | improves ~4× | low-med |
| 9 | `palette` | **D** | 1 | 98×98 = **1.45e9** | ~12×12 × ~20k = **~2.9e6** | improves, but **not via rings** | medium |
| 10 | `matmul` | A | 272 | 96×96 = **1.18e9** | — | **done** — do not touch | — |
| 11 | `memory` | C | 103 | 31×32 = **14.3e6** | ~2× from a relative-rotation tape | **already dataflow**; only sizing left | — |
| 12 | `sort-numbers` | B | 17 | 25×25 = **2.08e6** | ~1.5× from `counted_ring` | **already dataflow** | — |
| 13 | `reverse-a-list` | B | 17 | 21×21 = **0.48e6** | little | **already dataflow** | — |
| 14 | `triangle` | D | 1 | 8×8 = **960** | — | done, bespoke, closed form | — |
| 15 | `history-lesson` | D | 0 | 97×90 = **9,409** | — | `footprint`-scored; ticks are free | — |
| 16 | `hello-world` | D | 0 | not built | ~11×5 | trivial either way | — |

The "now" column is a moving target in one direction only: a pending change that
removes four dead columns from every generated machine is worth **−7% to −8%** on
six of the seven CPU builds (`brackets` 98×95 → 94×76, `tcp` 112×78 → 108×78,
`subset-sum` 112×97 → 108×99), and it invalidates every ROM fold optimum. Re-read
the baselines after that merges. None of it moves the *ratios* above by more than a
few percent, because a 12×–200× footprint factor does not care about four columns.

Two structural facts fall out of that table and are worth stating plainly.

**Unblocking is nearly exhausted.** Rows 11–13 are already ring machines
(`memory_tape.build_v2`, `value_ring.build_reverse`, `value_ring.build_sort`), all
generator-emitted, all verified round-by-round on the engine. `build_v2(100)`
reproduces `littleman/programs/memory.man` byte-for-byte. So the entire remaining
unblocking value in the problem set is **one problem, `subset-sum`**, which is why
it is ranked first despite being the hardest build here.

**Optimising is not exhausted at all, and the numbers are absurd.** Rows 2–4 are
each ~85–110× improvements on problems that are already solved. `tcp` at 1.195bn
is the third-largest score we carry; its window is 16 slots, its base advances
monotonically, its drain is sequential from the base — it is a **shift register
that we implemented as a rotating tape addressed through an adapter**. On the
score, `tcp` alone is worth more than everything in rows 5–9 combined.

## 3. Per-problem justification, with the arithmetic

### `tcp` — the fat target (rank 2)
80% of its ticks are tape. Access pattern: an arrival at `seq` goes to window
offset `seq − awaited ∈ 0..15` (anything ≥16 is a hard `-1` failure, which is what
bounds the state); the drain then walks the window from the base while occupied.
So the *only* random access is "write at offset ≤ 15 from the head", and a ring
gives that for `offset` rotations at 3.2 ticks each — 48 ticks against ~700.
Resident state 18, or **16** using `val = 0` as the empty sentinel (legal, since
`1 ≤ val ≤ 999`). Worst load: n=48 → 48 rounds, ≤48 window writes and ≤48 drains,
say ~2,000 ring ops → **~6.4k ticks**, plus per-round control. Against a footprint
that should land near `sort-numbers`' 25×25, the projection is ~1.4e7 — an **85×**
improvement, and the highest-confidence win in the document.

### `sudoku-validity` — the add-based test-and-set is literally a station (rank 3)
27 nine-bit masks, and the membership test *is* an adder: `mask + 2^v` carries out
of bit `v` exactly when bit `v` was already set. So the hot inner operation is
`r; +; s` with a branch on the carry — a station of ~6 glyphs. The obstacle is
addressing: three of the 27 masks per round, at indices `r`, `c`, `3·(r/3)+(c/3)`,
which are *not* sequential across rounds. Three rotations of a 27-cell ring to
reach three arbitrary positions averages 3×13.5 = 40 rotations = ~130 ticks per
round, against the ~53% of ~61,700 ticks/round the tape currently costs. 81 rounds
→ ~11k ticks of ring traffic plus control. Even at 10× that estimate it is a ~90×
win on a 12.7bn score, the worst we carry.

### `brackets` — a 32-deep stack is a pipe (rank 4)
The only structure is a LIFO of type tags, depth ≤32, and 46% of its ticks are the
tape implementing it. But note the trap that shapes the whole design space: **a
pipe is strictly FIFO — `s` enters the source end and `r` leaves the destination
end — so a single pipe cannot be a stack.** Two pipes between two rooms give a
ring, not a stack, and popping the newest entry from a ring of depth `k` costs
`2(k−1)+1` ring ops. For `brackets` that is fine, because the *matching* structure
does not need pop-newest: a closer must match the most recent opener, and the
classic trick is to keep the depth in `BP` and the tag stack as a **base-4 integer
in `B`** (`mask = mask·4 + tag` to push, `mask/4` with the remainder in `B` to
pop — `/` leaves the remainder in `B`, which is exactly a pop). Depth 32 × 2 bits
= 64 bits, so it just fits and the stack costs **zero** pipe traffic. 64
characters × ~40 glyphs → ~2.6k ticks, ~26×26 grid.

### `gradebook` — the one where addressing really is random (rank 5)
Lookup is by sparse unsorted `id ∈ 1000..9999`, so the id→row map is a search, not
an index. But the search is a *scan of 16 cells* — which is one lap of a 16-cell
ring, ~51 ticks, against 16 tape probes at ~700. And AVG/TOP are full column
scans, i.e. laps, which is the ideal ring shape. The 30% ROM-recirculation column
dies with the ROM. Confidence is only medium because the round structure (roster
round then up to 10 batch rounds, ops of 2–4 tokens dispatched on a leading
opcode) is a *parser*, and parsers are where bespoke grids get big — and footprint
is squared.

### `max-element`, `atoi` — free wins, not yet built (ranks 6–7)
Resident state 2, one streaming pass, no structure at all. `max-element` is
`r; -; X` with two lanes and a `BP` counter; `atoi` is `r; M; …; *; +`. Both are
`triangle`-scale bespoke grids (`triangle` is 8×8/960). They are practice-tier so
they buy no contest points, but they are also ~an hour each and they would make
the ring/station idiom routine.

### `palette` — the counter-example, and it matters (rank 9)
92% issue, 7% tape. There is no memory traffic to delete, so a ring buys
**nothing**. What buys `palette` is that its 1,024 pixel writes are 1,024 × ~46
ticks of *issue*, and a bespoke grid writes a pixel in ~6 glyphs: 1,024 × 6 ≈ 6k
ticks against 151k, on a ~12×12 grid instead of 98×98 — a ~500× score improvement
that has nothing to do with dataflow. **Keep the two levers separate when costing
anything: "put it in a ring" and "delete the CPU" are different optimisations with
different targets.** Everything in rows 2–9 above gets both; `palette` gets only
the second.

### `plotter` (rank 8) and the display problems generally
`plotter` already packs `err` and `addr` into one word and is down to 4 tape
accesses per pixel; the remaining 57% tape column is register spill, not an array,
so the right fix is `register-cell` blocks (~20 ticks) rather than a ring. Its
footprint is set by the 32×24 panel plus the ROM, and the panel does not shrink.
Low confidence and a poor ratio of work to score: skip until rows 2–4 are done.

### Where a ring is the wrong tool
`triangle`, `hello-world`, `history-lesson` have no resident data at all.
`history-lesson` is `footprint`-scored — ticks are literally free, so the optimum
is maximum compression at arbitrary decode cost, which is what `rom_baseN.py`
already does. None of these move.

## 4. `subset-sum`: yes, it is unlockable — the control-flow analysis

This is the question the survey was commissioned to settle, so here is the whole
argument.

### 4.1 What the CPU build proves
`programs/subset-sum.asm` (112×97, footprint 12,544) is **correct** and answers
6 of 7 public cases in 1.20M–8.66M ticks against the 15M cap. The seventh,
`near-total-sum, 20 values`, needs **41,487 oracle iterations** at ~19
instructions and ~11 tape accesses each — **506M ticks, ~34× the cap**.

The wall is **issue, not memory**, and the arithmetic is unambiguous: 41,487 ×
19 = 788,253 instructions, and 15M / 46 = **326,086** instructions is all the cap
buys *even with a free tape*. So deleting the tape — the thing rings are for —
gets a 3.6× reduction on a 34× gap and leaves you 2.4× over. **A ring alone does
not save `subset-sum`.** Anyone quoting the 72% tape column at it is answering the
wrong question.

What closes a 34× gap is the other lever: **46 ticks per instruction becomes ~1
tick per glyph.** The hot loop is ~19 instructions ≈ ~40 glyphs of bespoke grid.
That is the 34×, and it is why this problem is a *bespoke grid* job rather than a
*ring* job — the ring is a supporting part.

### 4.2 The honest difficulty, confronted
`matmul`'s schedule is fixed and data-independent; `subset-sum` is a backtracking
search, so the take/skip/backtrack decision has to live somewhere. It can:
**`X` turns by `sign(A)`**, which after one `-` is a three-way comparator branch,
and `d`/`a` branch on `BP`. `sort-numbers_ring.man` already runs a three-lane
compare-exchange station this way (`r-X` into `Ws+M` / `+s` / `+s`). Data-dependent
control inside a station is *solved practice in this repo*, not an open question.

The real difficulty is not the branch. It is that **the search needs a stack, and
a pipe cannot be a stack** (§3, `brackets`). Three ways out, priced:

1. **Explicit stack ring**, push = 1 send, pop at depth `k` = `2(k−1)+1` ring ops.
   Measured over the public set: the stack costs **803,521 ring ops against the
   value ring's 804,803** — it *doubles* the traffic. Worst case at 3.2 ticks/op
   and 30 ticks/iteration: 11.4M, **0.76× the cap**. Works, no margin.
2. **Marks in the ring, no stack**: find the deepest taken position by walking a
   full lap. Costs `L` extra rotations per backtrack (21 at n=20) on 38,323
   backtracks — worse than (1).
3. **A linked list threaded through the ring cells.** This is the answer. Keep the
   deepest-taken position `q` in the state word; when position `p` is taken, write
   the *previous* `q` into `p`'s own ring cell. On backtrack, jump from `p` to
   `q+1` — a rotation of `(q+1−p) mod L` — and **the last cell that rotation reads
   is `q`'s**, so recovering `old_q` and `v[q]` (for the residual restore, since
   `r_before = r_after + v[q]`) is free. The stack costs **zero extra ring
   traffic**, because the traversal that reaches `q+1` already passes `q`.

### 4.3 The algorithm, and why the greedy+oracle shape is not the one to build
Two formulations, both cross-checked exhaustively against brute force (300 random
cases, all 4- and 5-value lists over `1..6` at every target) and both reproducing
all seven published answers:

| | worst public case | total public | shape |
|---|---|---|---|
| greedy over index + descending oracle (the `.asm`) | **41,516** iters | 41,945 | n oracle calls, insertion sort, candidate deletion, suffix rebuild |
| **direct lex-order DFS** (take-before-skip on the original index) | **112,018** iters | 120,915 | **one loop**, the stack *is* the answer |

The direct DFS is 2.7× more iterations and *far* less machine: no sort, no
deletion, no suffix rebuild, no greedy, and no separate output construction. Since
all `v ≥ 1`, no proper superset of a solution is a solution, so take-before-skip
in index order visits index sets in exactly lexicographic order and the first hit
is the answer. With `suf[n] = 0` the test `r > suf[p]` subsumes `p == n`, so the
loop has no bounds check:

```
loop:  if r == 0                 -> SUCCESS, the marked cells are the answer
       if r > suf[p]             -> backtrack (covers p == n, since suf[n] == 0)
       if v[p] <= r              -> take:  mark p, link p -> q, q = p, r -= v[p]
       p = p + 1
back:  if nothing taken          -> FAIL, emit 0
       jump to q+1, reading q's cell on the way: r += v[q], q = link(q), unmark
```

### 4.4 The measured cost, and the projection
Ring traffic for the direct DFS with the §4.2(3) linked stack, over the public set
(`L = n+1` cells, jump cost `(q+1−p) mod L`):

| case | iterations | ring rotations | rot/iter |
|---|---|---|---|
| `tiny warm up` | 93 | 285 | 3.06 |
| `multiple solutions, lex pin` | 3 | 2 | 0.67 |
| `no solution` | 2,653 | 12,293 | 4.63 |
| `single-element subset` | 2 | 1 | 0.50 |
| `last-index-required` | 6,143 | 26,623 | 4.33 |
| `duplicate values` | 3 | 2 | 0.67 |
| **`near-total-sum, 20 values`** | **112,018** | **804,803** | **7.18** |
| total | 120,915 | 844,009 | 6.98 |

At `a` ticks per iteration of station logic and `b` ticks per rotation, plus two
ring ops per iteration for the state header word:

| a | b | worst case | vs 15M cap | avg over 7 |
|---|---|---|---|---|
| 30 | 3.2 | 6.1M | **0.41×** | 0.93M |
| 40 | 3.2 | 7.2M | 0.48× | 1.10M |
| 50 | 3.2 | 8.3M | 0.55× | 1.27M |
| 50 | 5 | 10.6M | 0.71× | 1.61M |

`a = 40`–`50` is the right expectation: the loop unpacks a state word and a cell
word with three `/`s (each `M`, literal, `W`, `/` — `/` leaves the remainder in
`B`, which is what makes packed fields cheap), compares twice, and repacks. So the
projection is **~1.1–1.3M average ticks, worst case ~0.5× the cap**, and at a
50×50 grid that is a score of **~2.8–3.2e9** where today there is no score at all.

### 4.5 The prune is load-bearing — do not drop it to simplify ingest
Storing `suf[j]` per cell is the expensive part of ingest, so the obvious
simplification is to prune only on `p == n` and let `v[p] > r` do the rest.
Measured over the public set:

| variant | worst iterations | worst rotations | worst ticks @ a=40, b=3.2 | vs cap |
|---|---|---|---|---|
| with the `r > suf[p]` prune | 112,018 | 804,803 | 7.77M | **0.52×** |
| without it (`p == n` only) | 189,702 | 1,022,594 | 12.07M | **0.80×** |

0.80× is not a margin worth having on a problem whose model constants are
estimates, so the prune stays.

### 4.6 The packing, and how to avoid needing a third register
A man has `A`, `B` and a **write-only** `BP` (`b` sets it, `m` decrements it,
`d`/`a`/`x` branch on it — nothing reads it). So there are *two* readable
registers, and that is the binding constraint on the whole design, not the tick
budget. Every binary op takes its right operand from `B`, so computing
`pre·10^5 + v` from `A = v, B = pre` needs a third slot and there isn't one. Three
ways round it, and the third is why this design closes:

1. **A scratch ring** (`register-cell.man`'s idiom, ~20 ticks round trip) — costs a
   third incoming and a third outgoing pipe on the worker, and every one of those
   makes nearest-pipe binding harder. Works; `stream.py` carries eleven pipes.
2. **Multiplication by a small constant is repeated `+`**, because `B` survives it:
   with `B = mask`, `A = tag`, the sequence `++++M` computes `mask·4 + tag` into `B`
   using no third register at all. This is the trick that makes §3's `brackets`
   stack free, and it generalises to any radix small enough to unroll.
3. **Don't store `suf` at all — carry it in the state word and update it
   incrementally.** `suf[p+1] = suf[p] − v[p]`, and `v[p]` is read every iteration
   anyway, so the forward step is one `-`. The wrap is the only special case:
   at `p = n`, `suf` must reset to `Total`. Give the sentinel cell the value field
   **`−Total`** and the uniform rule `suf ← suf − v_j` is then correct *everywhere*,
   because `suf[n] = 0` and `0 − (−Total) = Total`. The sentinel can never be
   selected: `r == 0` is tested first and `r > suf[n] = 0` prunes every other case,
   so its value field is never used as a value.

With (3) the ring cell collapses to `(v_j·L + link_j)·2 + mark_j` — under 4.2e6,
no suffix field, and **ingest becomes `n × {r, s}`, exactly `sort-numbers`' load
loop**. The state word is `((suf·10^6 + r)·L + q)·L + p`: `suf ≤ 1,999,980`,
`r < 10^6`, `L = 21`, so `< 8.8e11`. Two `/L`s recover `p` then `q`, a `/10^6`
splits `suf` from `r`, and `/` leaving its remainder in `B` is what makes each
unpack two glyphs rather than five.

Backtracking updates `suf` for free: the jump from `p` to `q+1` reads every cell it
passes, so accumulating `suf ← suf − v` in the rotate loop body is one extra glyph,
and the pass necessarily crosses the sentinel — which is exactly where `Total` gets
reinstated. Split the jump into `rotate (k−1) times` plus one special final step so
that recovering `link[q]` and `v[q]` (for `r ← r + v[q]`) is straight-line code
rather than a branch inside a counted loop.

`mark` exists only so the answer can be emitted in *increasing* index order by two
laps at the end (lap 1 counts the marks and emits `k`, lap 2 emits the marked
values) — following the `link` chain would give them in decreasing order.

### 4.7 What is left to do, and the one place it can still go wrong
The design above is complete and its arithmetic is measured, but it is a
multi-session build: INIT (read `n`, load the ring, accumulate `Total`, write the
sentinel), the DFS loop head with three lanes, the two-part backtrack, and two
emit laps — call it six blocks in one worker room of roughly 45×40, plus one ring
and one relay. The risk is not the ticks and not the algorithm; it is that
**a two-in/two-out station can bind both `r` glyphs to the same pipe with no load
error and no stall, and simply read the wrong data** (the failure `stream.py`'s
docstring records). So build it against `tools/route-check.mjs` from the first
block, not at the end.

### 4.8 The one thing that is *not* fixed by this
The direct DFS is 112,018 iterations on the worst **public** case, and
`privateTestCount: 0` means the public set *is* the graded set. But it is not
robust: a random-search sweep of 400 inputs drawn strictly inside the constraints
(`n = 20`, `1 ≤ v ≤ 99999`, `t` at 10–60% of the sum) found inputs needing
**714,549** iterations for the direct DFS and **537,471** for the greedy+oracle —
6.4× and 12.9× their worst public cases. At `a = 50, b = 3.2` that is ~40M, well
over the cap. So the honest claim is: **this design passes the graded set with a
2× margin and is not a general subset-sum solver.** If private cases ever appear
on this problem, the fallback is the greedy+oracle walk in the same hardware
(41,516 iterations worst public, and its extra machinery — insertion sort,
candidate deletion, suffix rebuild — is all `n`-scale cold code, so it costs
footprint rather than ticks).

## 5. What to build, in order

1. **`subset-sum`** as a bespoke grid on the §4 design. It is the only remaining
   unblock in the problem set and the only one worth 2 contest points that we do
   not already have.
2. **`tcp`** as a 16-cell window ring. Highest confidence, ~85×, and the shape is
   `value_ring.py`'s, so the work is small.
3. **`sudoku-validity`** as 27 masks in a ring with an add-based test-and-set
   station. ~90× on the worst score we carry.
4. **`brackets`** as a base-4 stack in `B` with no ring at all. ~110×.
5. Pin what already exists: `memory_tape.build_v2(100)` is a generator-emitted
   solution to `memory` and **nothing tests it against the `memory` problem's
   public cases**. `value_ring` is properly pinned; `memory` is not.

Do **not** start with `plotter`, `gradebook` or `palette`: the first two are
parser-shaped (footprint risk, and footprint is squared) and the third has no
memory traffic to delete.

## 6. Rules the survey confirmed, for whoever builds next

* **Payload in pipes, control in the man.** Capacity is length; a ring needs
  `payload + 1` cells minimum (`memory.man`'s WRITE briefly holds N+1), and
  under-capacity **deadlocks silently**.
* **A pipe cannot be a stack.** `s` enters the source end, `r` leaves the
  destination end. Pop-newest costs `O(depth)` — so either thread the stack
  through the data cells (§4.2), or pack it into an integer with `/` (§3
  `brackets`), or pick an algorithm that only ever pops the front.
* **Every ring costs a turnaround room**, minimum 6 cells and therefore ~6
  ticks/word — a pipe may not loop back to its own room, and the engine drops such
  a pipe *without an error*.
* **A count that must survive `r`/`s` lives in `B`; if `B` is busy, put it in the
  ring as a header word** and make each pass consume exactly one lap so it comes
  back aligned. `sort-numbers` does this; `0` doubles as "ring now empty".
* **`/` leaves the remainder in `B`.** That single fact is what makes packed
  multi-field words affordable, and it is load-bearing in §4.5.
* **Re-run `tools/route-check.mjs` after every layout move.** Nearest-pipe binding
  is nearest, not nearest-ready; the failure mode is a silent wrong read.
  `value_ring.py` documents a real case where a Manhattan tie emitted the count as
  program output.
* **Do not spend rows to shorten pipes.** A pipe cell is worth 0.13–0.17 ticks at
  a large tape; a row of the binding dimension is squared in the score.
