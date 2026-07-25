# Littleman programs

Generated `.man` programs plus the blocks they're built from. Language reference:
[`../SPEC.md`](../SPEC.md) · scoring: [`../GRADING.md`](../GRADING.md) · problem
specs and public tests: [`../../tasks/problems/`](../../tasks/problems/).

| Program | Problem | Status |
|---|---|---|
| `memory.man` | `memory` (Semester 1) | **22/22** local (7 public + 5 heavy + 10 edge), 32x33, area² 1089 — expect ≈ **92M** |
| `memory-v1-submitted.man` | `memory` | the 42x33 build that actually scored **158M** on the judge; kept as the known-good fallback |
| `memory-v3-external-init.man` | `memory` | **22/22** independent initializer: paired zero fill lives in the relay room; 34x37, heavy avg 176,200 |
| `memory-v3-upstream-init.man` | `memory` | **22/22** upstream initializer then ordinary relay; 34x37, heavy avg 177,200 |
| `memory-v3-one-shot-init.man` | `memory` | **22/22** sentinel-synchronized one-shot filler; 37x36, heavy avg **175,980** |
| `memory-n8.man` | — | N=8 build of the same generator; small enough to trace by hand |
| `register-cell.man` | — | the 1-value register block (store `1 v`, fetch `-1`) |
| `two-roms.man` | — | two looping ROMs feeding one room: code banks, see [`../ARCH.md`](../ARCH.md) §5.5 |

Generator: [`../../solvers/python/randomfun2026solvers/memory_tape.py`](../../solvers/python/randomfun2026solvers/memory_tape.py),
built on [`circuit.py`](../../solvers/python/randomfun2026solvers/circuit.py).

```sh
# regenerate
PYTHONPATH=solvers/python python3 -c "from randomfun2026solvers.memory_tape import build_v2; \
  open('littleman/programs/memory.man','w').write('\n'.join(build_v2(100))+'\n')"
# ...and the three suites it must pass: memory-cases / memory-heavy-cases / memory-edge-cases

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
That makes addressing trivial and costs a constant ~800 ticks/op.

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
- **Loops must test before the body.** `> body d` is a do-while and passes one value too many when `addr == 0`; the shape used here (`d` first, body in the column below, `m` on the return leg) tests first. 8 ticks per value.
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

Two submissions of the *same logic* pinned both terms down:

| Submitted | area² | score | ⇒ judge avgTicks |
|---|---|---|---|
| pre-fold 96×24 | 9216 | 827M | 89,735 |
| folded 42×33 | 1764 | **158M** | 89,569 |

The fold delivered exactly the predicted 5.2× and nothing else moved, so the
judge's `avgTicks ≈ 89.6k` is solid. Note it is **4.4× the 20.4k these 7 public
cases cost** — the graded set contains materially heavier inputs even though the
API reports `privateTestCount: 0`. Optimise against `memory-heavy-cases.json`
(random 300–1000 token streams, generated locally with a Python oracle), not the
public set: a full 1000-token case is 411 ops / 392k ticks ≈ **955 ticks/op**, and
the judge's average works out to ~94 ops/case.

Where those 955 ticks/op go:

| | ticks/op | |
|---|---|---|
| tape pass-through | ~800 | 100 values × 8 ticks, every op regardless of address |
| corridor walking | ~150 | ~40 cells P1→dispatch, ~62 cells P2→MAIN, every op |
| real work | ~10 | reads, arithmetic, the target access |

## Predicting the judge locally (calibrated)

Two submissions of the same logic, then a third of the compact build, pin the
relationship down: **judge avgTicks ≈ 0.328 × the local `memory-heavy-cases.json`
average**, good to ~1%.

| build | area² | local heavy avg | predicted | actual judge |
|---|---|---|---|---|
| pre-fold 96×24 | 9216 | — | — | 827M |
| folded 42×33 | 1764 | 273,560 | 158M | **158M** |
| compact 32×33 | 1089 | 257,528 | 92M | **92.0M** |

So `score ≈ area² × 0.328 × local_heavy_avgTicks` — measure locally, submit once.

## Where the score can still go (currently 92.0M)

Ranked by payoff. Note compaction helps *both* terms — the corridors are ticks:

**Done (v2, now `memory.man`):** compacted the worker — area² 1764 → 1089 and
ticks ~6% lower, i.e. **1.72× total ⇒ ≈ 92M expected**. `worker_v2`/`build_v2`
in the generator; `worker`/`assemble` still build the 158M version. The win came
from dropping both target literals (B now carries `±(N−addr)`, so each arm needs
only `b m` / `N b m`) which let the room shrink 32×22 → 22×18, plus packing the
dispatch and targets adjacent to the loops. Corridor ticks fell less than hoped
(~150 → ~120 of ~840/op), so the footprint was the real gain.

1. ~~**Compact the worker — ~1.9× total, and the safest change**~~ (layout only, logic and tests untouched). The 34×24 interior is ~90% blank: 22 rows exist only because each loop is 4 tall and each branch owns its own row. Packing toward 24×24 takes area² 1764 → ~1024 (1.7×) *and* shortens the per-op corridors from ~150 to ~60 ticks (1.1×). → done: 158M → ≈ 92M.
2. **Rotate relatively instead of a full revolution (~2.6× ticks).** Keep the index of the next value to emerge and rotate `(addr − next) mod 100` — average 33 passes instead of 100. Two wrinkles: the mod correction needs a compare-and-add branch, and the index must live outside A/B/BP (all three are busy: A is clobbered by pass-through, BP is the loop counter, B carries `op`'s sign) — that's the job for `register-cell.man`, storing `index+1` so the value is never the fatal `0` command. → ≈ 36M with #1 done.
3. **Enlarge the pass-through loop ring (~2.2×) — bigger than first thought.** 8 ticks/value is only the floor for a *minimal* 2-wide loop: the four corners, the `d` test and the `m` decrement are fixed cost, so a larger ring amortises them over many values. Each value inherently costs just `r`+`s` = 2 ticks, so the floor is ~2, not 8:

   | loop | perimeter | values/lap | ticks/value |
   |---|---|---|---|
   | 2×4 (current) | 8 | 1 | 8.0 |
   | 2×6 | 12 | 3 | 4.0 |
   | 6×6 hollow square | 20 | 7 | 2.9 |
   | 8×8 | 28 | 11 | 2.5 |

   Needs `addr = K·q + r` — one `/` yields quotient *and* remainder — then `q` big laps plus up to `K−1` single passes in a small clean-up loop. No new pipes, no persisted state, so it is lower risk than #2 and roughly the same payoff. → ≈ 42M alone.

Banks (e.g. 10 rings of 10 cells, one lap each) look tempting at ~80 ticks/op but
need 10 relays, 10 pipe rings and a 10-way demux; since footprint is *squared*,
the area growth cancels the tick win. Not worth it before #1–#3.

Head-room check: the step cap is 5,000,000 and the worst legal input (1000
tokens, 411 ops) costs 392k ticks, so there is 12× slack — every remaining win is
score, not correctness.

## Independent relay initialization

`memory-v3-external-init.man` removes zero filling from the worker. Its relay
room starts with a paired counted ring that sends exactly 100 zeroes, then the
known-even exit falls directly into the steady `r`/`s` relay loop. The worker
starts command decode immediately; a tape `r` blocks if initialization has not
produced enough values, so synchronization is entirely through dataflow.

The handoff must enter at `r`, not `s`: entering at `s` emits stale `A=0` once
more, creates 101 initial values, and shifts every write. The balanced 42/72-slot
pipe layout passes all 22 cases and improves ticks over paired v3:

| suite | paired v3 | independent fill |
|---|---:|---:|
| public | 13,519 | **12,871** |
| heavy | 177,876 | **176,200** |
| edge | 10,360 | **9,640** |

The proof layout is 34x37, so its larger `37²` size factor still loses to the
31x31 submitted best. The next useful move is placement: preserve this protocol
and the balanced pipe capacities while fitting the fill/relay room into the
worker's vacated initialization space.

`memory-v3-upstream-init.man` tests the fully separated topology:

```text
worker -> initializer/pass-through -> ordinary relay -> worker
```

The initializer emits 100 zeroes and then loops forever on `r`/`s`; it does not
become idle or change pipe ownership. A two-cell middle pipe feeds the unchanged
compact relay. The three tape pipes hold 42 + 2 + 68 = 112 values, and every
route is dataflow-synchronized.

This version also passes all 22 cases, but the extra stage raises the heavy
average from 176,200 to 177,200 ticks without reducing the 34x37 bounds. Keep it
as the marked architecture experiment; the combined fill/relay room remains the
better scoring candidate.

The corrected one-shot topology is `memory-v3-one-shot-init.man`. The filler
does not relay CPU values: it sends 100 zeroes, then a `+1` sentinel, and halts
on `H`. The phase relay drains only the filler pipe during startup. The sentinel
turns its man into a separate six-tick steady loop which drains only the CPU
pipe. This is dataflow synchronization; correctness does not depend on which
room happens to run faster.

The compact placement is 37x36. The filler room is 12x6 and the phase relay is
8x6. Its persistent ring has 78 + 43 = 121 slots; the 13-cell startup pipe is
not counted after its producer halts. A 36x36 route was explored, but its two
right-side pipes merge into a loop at the relay boundary and the loader rejects
it.

| suite | combined fill/relay | one-shot filler |
|---|---:|---:|
| public | **12,871** | 13,100 |
| heavy | 176,200 | **175,980** |
| edge | **9,640** | 9,840 |
