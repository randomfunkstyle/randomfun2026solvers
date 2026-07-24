# Plan: `memory` under 24M

Current: **61.9M** (`tasks/solutions/memory_tape.man`, 32×32, area² 1024, 27/27 local).
Target: **< 24M**. Score is `max(w,h)² × avgTicks`, lower better.

Predict the judge locally — calibrated over three submissions, good to ~1%:

```
judge_score ≈ area² × 0.328 × avg_ticks(memory-heavy-cases.json)
```

| step | ticks/op | area² | predicted |
|---|---|---|---|
| now (v3) | 641 | 1024 | 61.9M |
| **T1** relative rotation | ~330 | ≤1100 | ≈31M |
| **T2** + lap-tested ring | ~241 | ≤1100 | ≈23M |

Ticks are 92% of the problem. v3 walks **all 100 values every operation** — Θ(N)
unconditionally, because a full lap is what restores the ring's alignment.

---

## Why T1 works

Track the ring's alignment in a scratch register ("phase" = index of the next
value to emerge) and rotate only `(addr − phase) mod 100`. Expected gap is
**49.5** (uniform; one-way pipes rule out taking the shorter way round, so this is
~2×, not 3×). It also **deletes P2 entirely** — no restore lap — which halves the
corridors, since MAIN and the dance can sit next to each other and the ring is
visited once.

## Why T2 works on top

The pass-through ring currently tests BP before *every* value: 5 ticks/value. A
ring tested once per **lap** costs `2 + 6/K`; K=8 measures **2.75**. One `/`
yields lap count and remainder together. Verified standalone:
`blocks/lap-ring.man`, 12/12 including n=0,1,7,8,9,15,16,17,99,100,997,1000.
Whole-loop average is ~1.5× (not 2.2×) because the remainder still runs at 8 t/v.

---

## Tools — use these, do not hand-place anything

| tool | what it guarantees |
|---|---|
| `asmlayout.Serpentine` | an instruction lands only where its **declared pipe genuinely wins**; pads with blanks otherwise |
| `asmlayout.Assembler` | stacks bands (straight code / branch with cw-ccw-straight arms), derives band height from constraints, wires bands via a gutter |
| `circuit.Circuit` | refuses collisions: code on a corridor, a corridor crossing a glyph that would re-steer the man |
| `circuit.counted_loop` | 1 value/lap, 8 t/v, tests before body |
| `circuit.counted_ring` | 2 values/lap, 5 t/v, **two exits** (BP can hit 0 at either test) |
| `littleman/tools/route-check.mjs` | what pipe each `r`/`s`/`S` *actually* targets, per the engine |
| `littleman/tools/run-cases.mjs` | runs a suite, prints ticks and score |
| `littleman/tools/trace.mjs`, `watch.mjs` | tick-by-tick trace / stall finder |
| `randomfun2026solvers.scoring` | exact ticks by binary search |

Run python as `PYTHONPATH=solvers/python python3 …`; tests as
`PYTHONPATH=solvers/python uv run python -m pytest tests/ -q`.

---

## The anchor plan (settled — do not redesign)

Five pipes on the **bottom wall**, separated by column; tape-return on the right.
All twelve op classes discriminate with nearest-pipe margin ≥ 4.

| pipe | where | direction |
|---|---|---|
| `IN` | bottom wall, col 4 | incoming |
| `REGF` | bottom wall, col 7 | outgoing (park) |
| `REGR` | bottom wall, col 10 | incoming (fetch) |
| `OUT` | bottom wall, col 13 | outgoing |
| `TAPEFWD` | bottom wall, col 20 | outgoing |
| `TAPERET` | right wall, row ≈ H/2 | incoming |

Input and register anchors **must stay adjacent** — the dance interleaves them,
and on opposite walls every register op costs ~16 rows of walking, which routes
fine and silently spends the whole tick saving. Two earlier plans failed: `REGF`
sharing a column with `OUT` (deep `s(reg)` resolved to output), and input/register
on opposite walls.

Verify any change with a distance table **before** placing code.

---

## T1 instruction sequence (verified arithmetic; park `t` before the flag)

```
r(reg)->phase ; M ; 1 ; +     A = t = phase+1     (B = phase; r/s/X leave B alone)
s(reg) park t                                     ring: [t]
r(in)->op ; X: 0 -> `1` N (A=-1) | 1 -> `1` (A=+1) ; arms merge
s(reg) park flag                                  ring: [t, flag]
r(in)->addr ; -               A = addr-phase = delta_raw   (B still phase)
  X: <0 -> M ; `100` ; +      A = delta in 0..99            (B free to clobber)
M                             B = delta
r(reg)->t ; +                 A = t+delta = addr+1 = new phase   (t is at the head)
s(reg) park phase                                 ring: [flag, phase]
r(reg)->flag                                      ring: [phase]  <- invariant back
W ; b                         BP = delta, B = flag
<pass through delta values>   counted_ring (T1) / lap ring (T2)
W ; X                         dispatch on flag's sign
  READ  (flag<0, CCW): r(tape)->value ; s(tape) put it back ; s(out) emit
  WRITE (flag>0, CW):  r(in)->value ; s(tape) ; r(tape) discard old
-> back to MAIN
```

Invariants that must hold or the tape silently misaligns:

1. **Register ring holds exactly `[phase]` between operations.** Park order is
   what makes the FIFO hand back `t` before `flag`; parking the flag first forces a
   re-park (2 extra ops).
2. **`op` rides in the sign of B** across the pass-through — A is clobbered by
   `r`, BP is the counter. Convert `op` to ±1 immediately so the dispatch's `X`
   never sees 0 (`X` only goes straight at exactly 0).
3. **No `mod` on the phase.** `addr+1` may reach 100; `delta_raw` then lands in
   [−100, 99], which the single `+100` correction maps into [0, 99].
4. **`S` is unusable for a READ** once a register pipe exists — it writes to
   *every* outgoing pipe, so the value would land in the register. Use `s(tape)`
   then `s(out)`.
5. **WRITE sends before it receives** (new value in, old one discarded), so the
   ring momentarily holds N+1 → tape needs ≥ N+1 slots.
6. Every arm of a branch must **leave the merge cell with the same heading**, or
   the arms never actually join (routes cleanly, behaves wrongly).

---

## T2 caller sequence (measured)

```
M ; `8` ; W ; /      A=q, B=rem, BP=q   (one `/` gives both)
<big ring: q laps>   its r/s touch only A, so B carries rem across for free
W ; b                BP=rem
<counted_loop: rem>  8 t/v clean-up
```

**The `B=8` clobber is solved — no extra register traffic.** The dance's last
step (`r(reg)->flag`) simply *moves* to after the pass-through: leave the flag
sitting in the register ring while the loops run, and read it back for the
dispatch. The FIFO already has it in the right place:

```
dance ... s(reg) park phase        ring: [flag, phase]
M ; `8` ; W ; / ; b                A=q B=rem BP=q     (B was free, flag is parked)
<big ring: q laps>                 B carries rem across untouched
W ; b                              BP = rem
<remainder loop: rem values>
r(reg)->flag                       ring: [phase]  <- invariant restored
X                                  dispatch
```

**The remainder loop MUST be a `counted_ring` (5 t/v), not a `counted_loop`
(8 t/v).** This decides whether the target is met — modelled over delta uniform
on 0…99:

| pass-through | E[ticks] | ticks/op | predicted |
|---|---|---|---|
| K=8, remainder at 8 t/v | 154 | ~260 | **25.0M — misses** |
| K=8, remainder at 5 t/v | 144 | ~245 | **23.6M — clears** |
| K=12, remainder at 5 t/v | 137 | ~238 | 23.1M |

Margin is thin, so do not spend ticks anywhere else. `Serpentine` padding blanks
cost 1 tick each: keep bands narrow. If it still misses after W3, the next lever is
**packing 3 cells per 64-bit word** (21 bits each, biased by 10⁶ — verified in
`blocks/packed-field-unpack.man`), which cuts the tape from 100 values to 34 for
~40 ticks of arithmetic: pass-through 144 → ~55, total ~215 → ≈20.8M.

---

## Verification — required before claiming any score

A program is only "done" when all of these pass:

1. `route-check.mjs` — every `r`/`s`/`S` targets the intended pipe.
2. Four suites, all green: `memory-cases` (7 public), `memory-edge-cases` (10),
   `memory-heavy-cases` (5 judge-weight), `memory-fresh-cases` (5, different seed,
   read-heavy and write-heavy mixes).
3. **Extra-output check.** `run-cases.mjs` stops as soon as output *length*
   matches, so it cannot see a loop that emits one value too many. Either extend
   the runner to keep stepping and assert no further output, or add sentinel cases
   (see `blocks/lap-ring.md`).
4. Boundary addresses: 0 and 99; a stream ending mid-WRITE; repeated writes to one
   cell; reading all 100 cells.
5. Report `area² × 0.328 × heavy_avg` as the predicted judge score.

Bugs this problem has already produced, each caught only by a boundary case or the
collision checker — assume the next one is the same shape:

- `> body d` is a do-while: a count of 0 still moves one value.
- A square ring runs body cells *before* the corner test — measures the right
  ticks/value and is silently wrong at n=0.
- The phase seed placed on the per-op return path zeroed A before the emit.
- Three branch arms that each keep their own heading never merge.

---

## Work breakdown

**W1 — v4 worker (critical path).** Complete `worker_v4` in `memory_tape.py` using
`Assembler`: the bands above, the pass-through `counted_ring`, the dispatch branch
and both target arms. Deliver `build_v4(n)` emitting a full program.
*Acceptance:* all four suites green, route-check clean, area² ≤ 1100, heavy avg
≤ 210k (≈ 31M predicted).

**W2 — verification hardening (independent).** Extend `run-cases.mjs` (or add a
runner) to detect extra output beyond the expected stream, and add
`tests/test_memory_solution.py` asserting the shipped program passes all four
suites. Do not touch `memory_tape.py` or `asmlayout.py`.

**W3 — lap ring integration (after W1).** Swap the pass-through for the lap-tested
ring per the T2 sequence, solving the `B=8` clobber.
*Acceptance:* same as W1 plus heavy avg ≤ 150k (≈ 23M predicted).

**W4 — footprint trim (last).** Only if area² > 1024 after W3: fold the tape band
or narrow the worker. Width and height are near-equal today, so this is worth ≤6%
and must never trade ticks for it.
