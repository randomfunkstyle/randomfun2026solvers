# Littleman programs

Generated `.man` programs plus the blocks they're built from. Language reference:
[`../SPEC.md`](../SPEC.md) · scoring: [`../GRADING.md`](../GRADING.md) · problem
specs and public tests: [`../../tasks/problems/`](../../tasks/problems/).

| Program | Problem | Status |
|---|---|---|
| [`../../tasks/solutions/memory_tape.man`](../../tasks/solutions/memory_tape.man) | `memory` (Semester 1) | **the one to submit** — 27/27 local, 32×32, area² 1024, judge-scored **61.9M** |
| `memory.man` | `memory` | identical copy kept here for the tooling examples below |
| `memory-v2.man` | `memory` | previous build (single-value loops); judge-scored **92.0M** at 32×33 |
| `memory-v1-submitted.man` | `memory` | the 42×33 build that scored **158M**; earliest known-good fallback |
| `memory-n8.man` | — | N=8 build of the same generator; small enough to trace by hand |
| `register-cell.man` | — | the 1-value register block (store `1 v`, fetch `-1`) |
| [`blocks/`](blocks/) | — | mechanisms each verified in isolation: scratch register, packed words, lap-tested ring |

Generator: [`../../solvers/python/randomfun2026solvers/memory_tape.py`](../../solvers/python/randomfun2026solvers/memory_tape.py),
built on [`circuit.py`](../../solvers/python/randomfun2026solvers/circuit.py).

```sh
# regenerate
PYTHONPATH=solvers/python python3 -c "from randomfun2026solvers.memory_tape import build_v3; \
  open('tasks/solutions/memory_tape.man','w').write('\n'.join(build_v3(100))+'\n')"
# it must pass all four suites: memory-cases, memory-heavy-cases, memory-edge-cases,
# memory-fresh-cases (the last is a different seed, with read-heavy/write-heavy mixes)

# test (prints ticks and the contest score)
node littleman/tools/run-cases.mjs littleman/programs/memory.man littleman/programs/memory-cases.json 4000000 100

# check every pipe op targets the pipe you intended
node littleman/tools/route-check.mjs littleman/programs/memory.man

# watch a stall / trace tick by tick
node littleman/tools/watch.mjs littleman/programs/memory.man "0 0 0 1" 60000 4000
node littleman/tools/trace.mjs littleman/programs/memory-n8.man "1 7 42 0 7" 200
```

## `memory.man` — the rotating pipe tape

The 100 cells are **100 values circulating in a pipe ring**: worker → forward
pipe → relay room → return pipe → worker. Nothing is stored in a room; the pipes
*are* the memory, and a pipe holds as many values as it has cells.

The worker runs **exactly one full revolution per operation**, so the ring always
comes back to the same alignment and cell *k* is simply the *k*-th value out.
Addressing is therefore trivial and stateless — and unconditionally Θ(N): ~500 of
~641 ticks/op, the same whether you touch cell 3 or cell 97.

```
r(in)->op ; X                    op==0 -> straight (READ), 1 -> CW (WRITE)
r b M `100` - M                  BP=addr, B=+(100-addr)      [READ arm]
r b M `100` - N M                BP=addr, B=-(100-addr)      [WRITE arm]
P1 x addr:  {r(tape), s(tape)}   pass `addr` values through untouched
W ; X                            A=+-(100-addr); dispatch on its sign
  READ : b m                     BP = |A|-1 = 99-addr
         r(tape) ; S             cell[addr] -> output AND back onto the tape
  WRITE: N b m                   BP = 99-addr
         r(in)->value ; s(tape)  new value takes slot addr
         r(tape)                 consume + discard the old value
P2 x (99-addr): {r(tape), s(tape)}
-> back to MAIN
```

Things that took a while to get right, in case they bite again:

- **`S` does a READ in one instruction.** It sends A into *every* outgoing pipe, and the worker's outgoing pipes are exactly {output, tape-forward} — so one `S` both emits the value and puts it back on the tape.
- **`X` only goes straight when A == 0.** The op test can use `straight` (op is exactly 0 or 1), but the READ/WRITE dispatch cannot: it branches on ±(addr+1), which is never 0, so *both* arms must be turns (one CW, one CCW) and the two target tracks sit above and below the dispatch row.
- **Carrying `op` across P1.** P1 clobbers A (pass-through) and BP (loop counter), so `op` rides in the *sign of B*. B holds `±(100−addr)` rather than `±(addr+1)` specifically so the P2 count is `|B|−1`: each target arm then needs only `b m` (or `N b m`) instead of `M `100` - b`, which is what shrank the room enough to matter.
- **WRITE sends before it receives**, so slot `addr` gets the new value and the old one is then discarded. The ring momentarily holds N+1 values, which is why the tape needs ≥ N+1 slots.
- **Loops must test before the body.** `> body d` is a do-while and passes one value too many when `addr == 0`; the shape used here tests first. P1/P2 are `counted_ring`s: 2 values per lap, **5 ticks/value**.
- **Pipe choice is by distance from the instruction**, so the layout keeps every tape `r`/`s` at high columns and every input `r` at low columns. `route-check.mjs` verifies each one against the engine rather than trusting the arithmetic.
- **The return pipe enters the worker's *bottom* wall.** With both anchors on the right wall the return pipe had to cross the forward pipe's descent; going in underneath lets the whole ring fold into a band below the worker (96x24 → 42x33, area² 9216 → 1764).

## `register-cell.man` — 1-value register

```
     +----+
+-+  |vWs<|  +-+
|I|>>|v  W|>>|O|
+-+  |>@RX|  +-+
     |^  W|
     |^Wr<|
     +----+
```

A man circles `>@RX`; `R` blocks until a command arrives and `X` branches on its
sign:

- **`1 v` → store.** CW/south: `W` parks the command in B, `r` takes the *next* input value into A, `W` swaps it into B. The value lives in the off hand.
- **`-1` → fetch.** CCW/north: `W` brings B into A, `s` emits it, `W` puts it back — **non-destructive**, so `1 10 -1 -1` emits `10` twice.
- **`0` → straight into the wall, which kills the whole program.** Any bus that could carry a 0 command needs to bias it (e.g. use ±1 only).

9 ticks per store, 8 per fetch. Useful as a register *outside* A/B/BP — see the
first optimisation below.

## Measured on the real judge

The graded set is **4.4× heavier than the 7 public cases** (20.4k ticks) — the graded set contains materially heavier inputs even though the
API reports `privateTestCount: 0`. Optimise against `memory-heavy-cases.json`
(random 300–1000 token streams, generated locally with a Python oracle), not the
public set: a full 1000-token case is 411 ops / 392k ticks ≈ **955 ticks/op**, and
the judge's average works out to ~94 ops/case.

Where those 955 ticks/op go:

As shipped (v3, 5 t/v rings) that is ~641 ticks/op:

| | ticks/op | |
|---|---|---|
| tape pass-through | ~500 | 100 values × 5 ticks, every op regardless of address |
| corridor walking | ~120 | the man crosses the room between input and tape anchors |
| real work | ~20 | reads, arithmetic, the target access |

## Predicting the judge locally (calibrated)

Two submissions of the same logic, then a third of the compact build, pin the
relationship down: **judge avgTicks ≈ 0.328 × the local `memory-heavy-cases.json`
average**, good to ~1%.

| build | area² | local heavy avg | predicted | actual judge |
|---|---|---|---|---|
| pre-fold 96×24 | 9216 | — | — | 827M |
| folded 42×33 | 1764 | 273,560 | 158M | **158M** |
| compact 32×33 | 1089 | 257,528 | 92M | **92.0M** |
| rings 32×32 | 1024 | 183,808 | 62M | **61.9M** |

So `score ≈ area² × 0.328 × local_heavy_avgTicks` — measure locally, submit once.

## Where the score can still go (currently 61.9M)

**Complexity today is Θ(N) unconditionally** — every operation walks all 100
values, because the full lap is what restores the ring's alignment. Touching cell
3 costs exactly what cell 97 costs: ~500 of ~641 ticks/op.

| | per-access | ticks/op | score |
|---|---|---|---|
| now (v3) | Θ(N), 5 t/v | 641 | 61.9M |
| relative rotation (v4) | **Θ(gap), E=N/2** — deletes the P2 lap | ~330 | ≈31M |
| + lap-tested ring | Θ(gap) at 2.75 t/v + an 8 t/v remainder | ~241 | ≈23M |
| banks B=10 + bit decode | Θ(N/B + log B) | ~206 | 24–31M |
| tree of 100 registers | Θ(log N) ticks, **Θ(N) area** | — | area² eats it |

True O(1) RAM is not reachable: addressing is *positional* (`r`/`s` pick the
nearest pipe by the instruction's own location), so "go to address k" means
physically walking a man there. The one addressable primitive, the LM-75 display,
is write-only and 4-bit.

**The wall is fixed cost.** At ~200 ticks/op roughly 80 is corridors plus setup
arithmetic. Past ~2× the lever stops being complexity and becomes worker
tightness, which is also why banking buys less than its Θ suggests — area² is
squared and ten duplicated access machineries grow the box.

Ranked by payoff. Note compaction helps *both* terms — the corridors are ticks:

**Done (v2, now `memory.man`):** compacted the worker — area² 1764 → 1089 and
ticks ~6% lower, i.e. **1.72× total ⇒ ≈ 92M expected**. `worker_v2`/`build_v2`
in the generator; `worker`/`assemble` still build the 158M version. The win came
from dropping both target literals (B now carries `±(N−addr)`, so each arm needs
only `b m` / `N b m`) which let the room shrink 32×22 → 22×18, plus packing the
dispatch and targets adjacent to the loops. Corridor ticks fell less than hoped
(~150 → ~120 of ~840/op), so the footprint was the real gain.

1. ~~**Compact the worker — ~1.9× total, and the safest change**~~ (layout only, logic and tests untouched). The 34×24 interior is ~90% blank: 22 rows exist only because each loop is 4 tall and each branch owns its own row. Packing toward 24×24 takes area² 1764 → ~1024 (1.7×) *and* shortens the per-op corridors from ~150 to ~60 ticks (1.1×). → done: 158M → ≈ 92M.
2. **Rotate relatively instead of a full revolution — designed, part-built, ≈31M.**
   Track the alignment and rotate `(addr − phase) mod 100`. **The gap is uniform on
   0…99, so E[gap] = 49.5, not N/3** — one-way pipes mean we cannot take the
   shorter way round, so this is ~2× on the pass-through, not 3×. It also *deletes
   P2*, which halves the corridors (MAIN + the dance at the top, one ring at the
   bottom: two vertical traversals instead of four).

   Verified pieces: [`blocks/scratch-register.man`](blocks/scratch-register.man)
   (~10-tick round trip, survives A and B being clobbered) and the mod arithmetic
   (`%` is floored, so `−95 mod 100 == 5`).

   The dance is **shared, not duplicated per arm**, because `op` becomes a ±1 flag
   two cells after it is read. Put the register's two anchors on adjacent top-wall
   columns so `r(reg)`/`s(reg)` are neighbours instead of a shuttle apart:

   ```
   r(in)->op ; X:  0 -> `1` N (A=-1)   1 -> `1` (A=+1) ; merge    flag = ±1
   s(reg) park flag                              ring: [phase, flag]
   r(reg)->phase ; M ; 1 ; +           A = phase+1 = t
   s(reg) park t                                 ring: [flag, t]
   r(in)->addr ; -                     A = addr-phase = delta_raw  (B = phase)
     X: <0 -> M ; `100` ; +            A = delta in 0..99          (B free now)
   M                                   B = delta
   r(reg)->flag ; s(reg)               re-park flag                ring: [t, flag]
   r(reg)->t ; +                       A = t+delta = addr+1 = new phase
   s(reg) park phase                             ring: [flag, phase]
   r(reg)->flag                                  ring: [phase]  <- invariant back
   W ; b                               BP = delta, B = flag
   <pass through delta values>
   W ; X                               dispatch on flag's sign
   ```

   Two traps found while building it:

   - **`S` can no longer implement a READ.** It sends to *every* outgoing pipe, so
     it would inject the read value into the register ring too. Carry the value in
     A up the return path and emit at the top — that walk happens anyway, so it
     costs nothing.
   - The phase needs no `mod`: `addr+1` may reach 100, and `delta_raw` then lands
     in [−100, 99], which the single `+100` correction still maps into [0, 99].

   What remains is purely layout: the correction's three arms (CCW with the
   `+100`, straight, CW) must merge without crossing each other's cells, and every
   register op must stay inside its distance window. `circuit.py` rejects the bad
   versions, so this is fiddly rather than risky.
**Done (v3, now `memory.man`):** both pass-through loops are `counted_ring`s —
2 values per lap, 5 ticks/value instead of 8. Heavy-suite ticks 257,528 →
183,808 (**1.40×**) with area² unchanged at 1024, so ≈ **62M** expected. The
rings cost 2 rows each, paid for by moving the output room from above the worker
to the left beside the input room. Note area² is *width*-bound at 32, so that
move buys rows, not footprint. `worker_v3`/`build_v3`.

3. ~~**Enlarge the pass-through loop ring (~2.2×) — bigger than first thought.**~~ 8 ticks/value is only the floor for a *minimal* 2-wide loop: the four corners, the `d` test and the `m` decrement are fixed cost, so a larger ring amortises them over many values. Each value inherently costs just `r`+`s` = 2 ticks, so the floor is ~2, not 8:

   Built and measured — see [`blocks/lap-ring.md`](blocks/lap-ring.md). The cost is
   **`2 + 6/K`, not `2 + 4/K`**: per-lap overhead is 4 corners + `m` = 5 cells, and
   a rectilinear closed loop always has even perimeter while `2K+5` is odd, so one
   nop is unavoidable.

   | K | ring box | ticks/value |
   |---|---|---|
   | 2 | 2×5 | 5.00 |
   | 4 | 2×7 | 3.50 |
   | 8 | 2×11 | **2.75** (shipped) |
   | 16 | 2×19 | 2.38 |
   | 32 | 2×35 | 2.19 |

   **Rings for this must be 2 cells wide, never square.** An earlier table here
   listed a "6×6 hollow square, 2.9 t/v" — that shape is *wrong*: its top row runs
   `r s r s` before the corner test, so a count of 0 still moves a value. It
   measures the right ticks/value and is silently incorrect; only the n=0 boundary
   case catches it. On a straight segment of a CW ring the CW turn points into the
   hole, so `d` only works on a corner, and entry from outside also only lands on a
   corner — two adjacent corners forces one side to be 2 cells.

   **The realistic gain is ~1.5×, not 2.2×**, because the remainder loop still runs
   at 8 t/v: for a count uniform on 0…99 the whole split averages ~163 ticks against
   250 for `counted_ring`, and it is flat from K=8 to K=12.

   Caller side, verified: `M` `8` `W` `/` `b` leaves **A=q, B=rem, BP=q**; the big
   ring runs q laps (its `r`/`s` touch only A, so **B carries `rem` across it for
   free**); then `W` `b` gives BP=rem for a small `counted_loop`. 9 ticks of
   scaffolding per pass-through.

   **Integration warning:** the split needs `B=K` for one tick, which destroys
   whatever B was carrying (today `±(N−addr)`). With A, B and BP all live, a
   drop-in needs a third slot — park it in the scratch register. The `±(rem+1)`
   sign trick does compose with the existing `b m` / `N b m` arms.

   → with #2, ≈ 23M; alone against v3's double lap, only ≈ 48M (two splits, two
   remainder loops).

Banks (e.g. 10 rings of 10 cells, one lap each) look tempting at ~80 ticks/op but
need 10 relays, 10 pipe rings and a 10-way demux; since footprint is *squared*,
the area growth cancels the tick win. Not worth it before #1–#3.

Head-room check: the step cap is 5,000,000 and the worst legal input (1000
tokens, 411 ops) costs 392k ticks, so there is 12× slack — every remaining win is
score, not correctness.
