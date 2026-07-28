# RAM-program LM-1 — Stage 1 (demand fetch), measured

Mission: replace the looping-drum instruction supply with a stored program in a
men-v3 RAM, boot-loaded from the ROM. Staging directive: raw demand fetch first
(no read-ahead, no flush protocol), measured, before any prefetching.

All numbers below are engine-measured (FastLittleman via `optimize.verify` for
whole machines, the reference `lm.mjs` for the toy and for spot parity checks).
No emulator-model numbers are used except where labelled.

## Toy A — the fetch pipeline standalone (`toy_fetch.py` / `toy_fetch.man`)

ROM drum (24 words) -> loader/fetcher man (one room, two phases) -> men-v3
store (n=25), commands from the input room standing in for the CPU
(0 = next, t > 0 = jump), answers straight to the output room. Reference
engine, input `1 0 0 15 0 3 23`:

- output exactly the 14 words at the commanded addresses — boot copy, phase
  switch by nearest-pipe geometry, sequential and jump fetch all correct.
- boot: ~19.7 t/word (loader ring is 16 cells; ROM feed hides behind it).
- demand round trip, idle store, short pipes: **~50 t** to the first word,
  ~60 t for both words of an instruction.
- pipelined issue (commands queued): **38-40 t/instruction** — the fetcher's
  38-cell loop is the limiter; the store itself sustains 10 t/word (one router
  block per read).
- store router tuning matters: ops=4/per_row=4 (walk-home every 4 ops) cost
  ~+90 t on the first fetch and stalled boot; ops=48 fixed it. The program
  store wants the router unrolled deep (streaming access pattern).

## Stage 1 machines (`lm1/ram_machine.py`)

`build_ram(program)`: ROM boot image (packed drum) -> fetcher (PC in B; phase 1
counted loader, phase 2 command loop) -> men-v3 program store (word j at addr
j+1; addr 0 unused) -> modified CPU (`build_ram_cpu`, a copy of
`machine.build_cpu`):

- fetch row `>0>s rbr`: send 0 ("next") or ride through with a jump target,
  then read opcode+operand off the answer pipe. One command outstanding at a
  time — nothing in flight past a taken branch, no flush protocol.
- jump/branch slabs send the **absolute target address** (ram_words resolves
  targets to `2t+1`) instead of discarding: taken path drops to a shared taken
  row, walks west, rises up a private riser (col 3) that rejoins the fetch row
  *after* the `0`, so the shared `s` at (4, centre) sends whatever A holds.
- the discard loop, the drain, and recirculation are gone.
- pipe rivalry (the snake_unit dragon): solved by making the cmd pipe's send
  site unique — all taken paths converge on the one `s` at (4, centre); the
  cmd pipe attaches on the west wall at centre-2 (above the answer's centre
  row), which also makes it the only outgoing pipe candidate for both senders
  by 9+ cells of margin. `check_bindings` enforces it; mem_pad search kept.

### brackets-ram vs baseline (engine, 9/9 public cases both)

| build | box | footprint | avg ticks | ratio |
|---|---|---|---|---|
| baseline drum (machine.build) | 89x60 | 7,921 | 23,207 | — |
| brackets-ram (stage 1) | 116x321 | 103,041 | 65,232 | **2.81x ticks, 13x footprint** |

Reference-engine parity: `lm.mjs` on the empty-string case emits `0` ✓.

Per-case linear fit (RAM ticks vs executed instructions, emulator counts):
`ticks ~= 820 + 374 * instrs`, residuals < 150 across 12..172-instruction
cases, and **words_skipped contributes nothing** — a taken jump costs the same
as a sequential fetch. That is the architectural point, measured.

- boot, measured directly on the reference engine (fetcher reaches phase 2):
  **1,347 ticks for 80 words** (~16.5 t/word; the 16-cell loader lap). The
  820 intercept vs 1,347 boot is lane-mix variance: the round trip is ~330 on
  the short cases' mix and ~374 on the loop mix (LD/ST lanes add their own
  tape round trip).
- demand round trip decomposition (~375 t/instr):
  - **~213 t is pure pipe transit** in this layout: cmd 32 cells + request 69 +
    answer 112 (the store is a 3-rows-per-word single column far south, so
    every serial pipe is long). ARCH §7.4b's "every pipe cell is a tick" is
    the whole story of demand fetch.
  - ~30-45 t store service (router block 10/word + decoder/cell/collector).
  - ~15 t fetcher loop share.
  - ~110 t CPU decode + lane + return (shared with the baseline).

### gradebook-ram (second program, jumpier: 40% discard share at baseline)

| build | box | avg ticks | ratio |
|---|---|---|---|
| baseline drum (registry config) | 105x92 | 272,328 | — |
| gradebook-ram | 130x2653 | 450,348 | **1.65x ticks** (7/7) |

The ratio falls as the baseline's discard share rises (brackets 15.6% -> 2.81x;
gradebook 40% -> 1.65x): demand fetch charges every instruction the round trip
but charges jumps nothing extra.

## The DOOM projection (honest, from measured constants)

Current deadman-3d (V4, scratch/deadman3d-opt/METRICS.md): **770x1191,
footprint 1,418,481**, frame ~8.5M native ticks, jump discards ~1.2-1.5M/frame
(~150k skipped words at 8 t), **~220 taken jumps per frame** (149,545 words /
~674 avg skip), ~23-30k instructions/frame. Image = 2,368 words.

Stage 1 (demand fetch) applied to DOOM: even with the store answers teleported
(deadman already ships the L/U teleport rooms that cut a 59-cell response to
~6 effective cells — the exact cure for my 213-tick transit term), the round
trip is ~120-170 t/instr serialized on top of the ~200 t/instr the CPU already
spends. ~25k instrs x ~150 = **+3.5-4M/frame against -1.3M of discards saved:
demand fetch LOSES ~2.5M/frame (+30%) on ticks.** Confirms (and worsens) the
old +2.1M/-1.2M pricing note.

Stage 2 (read-ahead, the coordinator-confirmed mechanism) is where it flips:

- sequential supply is trivially hideable: the fetcher issues at 38-40
  t/instruction (toy-measured), the store sustains 10 t/word, and the DOOM CPU
  consumes at ~200 t/instruction — 5x headroom.
- only taken jumps pay flush + refill ~= one round trip ~ 200-400 t.
  **~220 taken jumps x ~400 t = ~0.09M vs 1.2-1.5M of discards deleted:
  a net -1.1..-1.4M per frame (~ -13-16% ticks).**
- squareness (the dominant metric): the program RAM is ~40-45 cells/word =
  ~96-107k cells for 2,402 words — reshapeable into ~10-16 banked columns
  (engineering: band_room is single-column today; the grid-store family shows
  the banking pattern). The 756-wide ROM fold and the 990-row single-column
  data store both dissolve into blocks. A ~450x450 machine (fp ~200k) vs
  770x1191 (fp 1.42M) is a **~7x footprint reduction**, with the ticks
  *better* than today under Stage 2, not worse.

Projection stated plainly: Stage-1 demand fetch on DOOM is a tick loss
(+~30%) and only pays under an extreme squareness weighting; Stage-2
read-ahead + teleported answers + a banked store is a simultaneous win on both
axes (-13-16% ticks, ~7x footprint) and is what should be built.

## Stage-2 design notes (from the measured constants)

1. Buffer sizing: refill latency ~ round trip (~150-400 t depending on
   layout); CPU consumes an instruction per ~200 t -> a corridor of 2-4
   instructions (4-8 words) never runs dry on straight-line code.
2. Flush protocol: count-based won't work (corridor occupancy unknown to
   either side); sentinel word (> max program word) emitted by the fetcher on
   accepting a jump, CPU discards until sentinel (r/-/X loop, needs B=SENT —
   B is ACC, so ACC must be parked in the store or the sentinel test done on
   A-only arithmetic; this is the one real register-pressure problem Stage 2
   must solve).
3. The fetcher's 38-t loop is fine (5x headroom vs DOOM's consumption); the
   transit terms should still be teleported to keep the jump refill short.
4. Store router: ops high (96 used here), per_row low keeps it narrow.

## Correctness ladder walked

- toy on the reference engine: outputs exactly as commanded (incl. jumps).
- brackets-ram: 9/9 public cases (fast engine) + reference-engine spot check.
- gradebook-ram: 7/7 public cases.
- tests: `tests/test_ram_machine.py` (digit_factors, absolute-target
  resolution, build smoke, and a slow-marked 9/9 engine test) — all pass.

## Files

- `solvers/python/randomfun2026solvers/lm1/ram_machine.py` — the generator
  (self-contained; `build_cpu` copied+modified, machine.py untouched).
- `tests/test_ram_machine.py`
- `scratch/ram-program/toy_fetch.py`, `toy_fetch.man` — the toy + probes
  (`trace_router.py`, `probe_store8.man`, `build_d3.py`).
- `scratch/ram-program/brackets_ram.man` — the verified Stage-1 machine.

## Known limits / risks

- Stage 1 supports IN/OUT/MEM/ALU programs only (no display/stream bands
  routed yet — DOOM needs the STREAM band added to the RAM CPU's band map;
  the lanes themselves are copied verbatim so this is routing work, not CPU
  work).
- The program store is an unbanked single column: footprint on small programs
  is terrible (13x brackets). Banking is required before any real submission.
- `build_ram_cpu` is a modified copy of `build_cpu` (drift risk, documented in
  the module docstring). Registry-driven integration deferred until Stage 2
  proves out.
- Programs addressing STORE slot 0 (hand-built triangle) are out of scope,
  same as the baseline generator.

# ═══ Stage 2 — the prefetching fetcher (2026-07-28, post-merge abd331d) ═══

Re-baselined against current main first: deadman-3d is now **307x307
(fp 94,249)** via `build_for` (STORE_SHAPE (8,42) men-v3 grid, TRIM_DEAD_LANES,
rom 42); frame 1 = 37,912 instructions, 227,760 skipped variable-width words
(~200 taken jumps at ~1,134 avg skip), boot+frame gate 9,769,747. Brackets
baseline unchanged (89x60, 23,207; fitted **122.1/instr + 5.12/skipped-word**).
The banked men-v3 grid measures **~104 cells/word** (8x42 = 336 words in
232x150) vs the drum's ~3.3 — the density fact that decides the DOOM verdict.

## The mechanism (lm1/ram_machine.py stage-2 half + lm1/ram_machine2.py)

* CPU fetch row reverts to the drum's plain `>rbr` — sequential instructions
  pay zero protocol overhead. Jump/branch taken paths converge on a taken row,
  send the **absolute target** out the east wall (`cmd` pipe), drop one row and
  walk into a 4x3 **flush block**: `r`/`X` — negative exits (rises col 4 to the
  collector, home via the normal riser), zero/positive loops. ACC stays in B
  untouched; no literal, no counter.
* The fetcher free-runs `(pc, pc+1)` READ pairs, `pc += 2`, polling the cmd
  pipe with `q` between pairs (BP = values in the pipe, non-blocking — and it
  counts in-transit values, so the fetcher parks on the service `r` early
  instead of streaming words that would be flushed). Service: `r` target, `M`,
  issue `0 0` — a **read of address 0, boot-loaded with -1** — then resume at
  the target. The sentinel therefore rides the normal answer path in request
  order. Boot tail writes the -1 (`1s0s1Ns`) and inits pc=1 (`1M`).
* Throttle = pipe backpressure (no credit protocol).

## Engine results (brackets, 9/9 both variants; all cases, exact fits)

| build | box | avg ticks | boot | per instr | per taken jump |
|---|---|---|---|---|---|
| baseline drum | 89x60 | 23,207 | 0 | 122.1 | ~105 (5.12 x 20.6-word avg skip) |
| Stage 1 (demand) | 116x321 | 65,232 | 1,347 | ~374 | +0 |
| Stage 2, grid (2,41) store | 165x170 | 32,152 | 2,603 | 137.1 | 288.6 |
| Stage 2, single-column store | 354x275 | 34,369 | 1,446 | 137.2 | 452.5 |

Max fit residual < 70 ticks across 12..1,036-instruction cases. **The
prefetcher closes the sequential gap: 374 -> 137 t/instr, i.e. +12% over the
drum's 122 (supply hidden behind execution, as designed); a taken jump costs
one bounded refill (289-452, dominated by the cmd pipe's routed length) instead
of Stage 1's round-trip-per-instruction.** Stage-1 2.81x -> **1.39-1.48x**.

## The negative that matters: banked stores break the flush protocol

gradebook (840 words, jump-heavy): Stage 2 over the **(6,141) grid store: 0/7,
wrong output** — traced on the Python fast engine to the store's answer merge:

1. The fetcher runs ~130 words ahead (the grid's *equal-length* answer pipes
   are ~170-cell buffers per column — six of them — so backpressure engages
   far too late).
2. The collector merges the six column pipes with `R`, whose tie-break is
   **reading order, not arrival order**. Under any queueing, a westerly
   column's answer overtakes an easterly one; the addr-0 sentinel (westmost
   column) cuts ahead of the queued stale words, the flush exits early, and
   the CPU decodes stale words as the target stream (observed: `op=15` where
   the target's `LD` should be; flush log shows the scrambled interleave and
   `0,0` where `-1` was expected).
3. Sequential streams survive by luck: address order == column reading order.
   Backward jumps are exactly the case that breaks. brackets' (2,41) 9/9 is
   therefore *lucky, not sound* — kept in the table only as a measurement of
   the prefetch mechanics.

Fix shipped: `build_ram2` refuses multi-column stores; the single-column block
(one answer pipe — order-preserving **by construction**) is required. That
makes brackets sound (9/9, 34,369) and gradebook *correct but geometry-bound*:
1/7 with the first case fully passing (153,103 ticks) and the rest only
tick-capped — the 2,530-row store forces a ~1,700-cell cmd route, so each of
its frequent jumps pays ~2,000 ticks of notice latency. The tension is now a
theorem about this store family:

    merge-free (correct)  =>  single column  =>  3 rows/word tall
    banked (compact)      =>  R-merge        =>  flush protocol unsound

An order-preserving banked store would need a request-driven answer
serializer (a decoder-tree unit of its own) — noted, not built.

## DOOM verdict (re-baselined, final): DO NOT BUILD

* Correct (single-column) program store for 2,402 words = ~7,200 rows: the
  machine would be ~24x taller than today's 307. Disqualified on squareness.
* The compact banked store is disqualified on correctness (above).
* Even granting a store: ticks net -0.2..-1.1M/frame (-3..-13%) — discards
  deleted (~1.1-1.8M) minus +15/instr supply overhead (~0.57M) minus ~200
  jumps x 450-2,000 refill — against a >=2.7x footprint loss in the best
  impossible case. Under the squareness-dominant doctrine this loses on every
  branch.
* **The tick win lives in the jump mechanism, not the store.** Every protocol
  element Stage 2 proved on the engine — the CPU-side r/X sentinel flush, the
  jump-target cmd pipe and its bindings, q-polled request service on the
  supply side — transfers verbatim to a **seek-drum**: keep the 3.3-cells/word
  looping drum as the program store, add a jump-request pipe + per-row q-check
  + a skip lane, and a taken jump becomes notice (<= 1 row) + seek walk
  (~1-2 t/row) + sentinel flush (corridor) + remainder discard, est. 400-700
  ticks vs today's ~5,400 avg — roughly the same -1M/frame, at +~2 cells/row
  of footprint. That is the recommended follow-up, not the RAM.

## Stage-2 artifacts

- `lm1/ram_machine.py` (stage-2 fetcher + CPU), `lm1/ram_machine2.py`
  (assembler), `tests/test_ram_machine.py` (6 tests, all pass).
- `scratch/ram-program/brackets_ram2.man` (grid variant), `brackets_ram2sc.man`
  (sound single-column variant), `gradebook_ram2.man` / `gradebook_ram2sc.man`,
  `tri3.asm`/`tri3_ram2.man` (3-slab smoke), debug harnesses `dbg_*.py`,
  `trace_cpu.mjs`, `trace2.mjs`.

# ═══ Stage 3 — the SEEK-DRUM (2026-07-28, post-merge b757fd6) ═══

The Stage-2 conclusion was that the tick win lives in the *jump mechanism*, not
in replacing the store. This builds exactly that: the packed drum keeps its
~3.3 cells a word for sequential supply, and gains random access for jumps.

## The mechanism (`lm1/seekrom.py` + `machine.py`'s opt-in `seek`)

* Every data-row transition passes a **gadget** — ``q`` then ``d``/``a``. With
  no request pending the drum man walks straight into his row (2 cells a row,
  the whole sequential tax). With one pending he is diverted into a **cascade**
  that zigzags down the gadget cells to a bottom collector, west to a riser, and
  up to the **station**.
* The station builds ``K = 128`` in ``B`` from digits (no backtick may enter
  these columns), receives the request, ``/`` splits it into ``row`` (A) and
  ``offset`` (B), emits the **sentinel ``-1``** then the offset into the fetch
  corridor, and splits on ``row``'s parity with ``x`` — even rows enter down the
  west ladder, odd down the east. ``]`` halves BP into the rung count.
* A ladder rung is pitch-2, three columns, ``d`` entered heading east: BP == 0
  exits through the row's own gadget (whose ``q`` now reads 0) into the row;
  BP > 0 detours through ``m`` and re-enters one row-pair lower.
* CPU side, all three elements engine-proven by the Stage-2 RAM work: the taken
  path sends ``row*K+offset`` out an east-wall request pipe, then **flushes the
  corridor to the sentinel** with a two-glyph ``r``/``X`` sign loop (words are
  non-negative, ARCH §4.2, so the sign is the whole test and **ACC is never
  touched**), reads the offset, and runs the stock 2x4 counted discard for it.
  The drum packs every row to an **even** word count, which is that loop's
  invariant.

Verified standalone on the reference engine (`scratch/ram-program/toy_seek.*`):
sequential emission byte-identical to the packed drum including the wrap, and
requests answered correctly for both row parities and mid-stream.

## The hybrid, which is the whole result

A seek is a flat few hundred ticks; a discarded word is ~4.5. So the seek is
only worth it for **long** jumps — and the distribution decides everything.
Measured on a `deadman-3d` frame (emulator, per taken jump):

| skip distance | jumps | words | share of the discard bill |
|---|---|---|---|
| 0-64 | 2,417 | 58,933 | 15.4% |
| 64-256 | 6 | 1,121 | 0.3% |
| 256-1024 | 58 | 34,613 | 9.0% |
| 1024+ | 128 | 288,143 | **75.3%** |

**186 jumps of 2,609 carry 84% of the words.** So `machine.seek_split` rewrites
only those instructions to new build-time opcodes (`JMPS`/`BRZS`/`BRNS`,
`SEEK_THRESHOLD = 256`); everything else keeps the counted discard it is already
good at. Source, listings and the emulator still say `JMPF`.

## Measured: brackets

Fitted over all 9 public cases (all builds 9/9 on the fast engine, exact fits):

| build | box | start | per instr | per jump |
|---|---|---|---|---|
| drum (baseline) | 89x60 | -102 | 122.1 | 5.12/skipped word (~105) |
| all-seek (threshold 0) | 93x73 | -150 | 133.9 | 229.4 |

**Brackets cannot show the win and says so.** Its image is 72 words, so the
longest jump skips ~60 — far under the 256 crossover — and at the real
threshold `build(..., seek=True)` refuses to build ("no jump is long enough to
seek"). Forcing it (threshold 0) costs +11.8 ticks an instruction for a per-jump
cost *worse* than the discard it replaced. That is the mechanism behaving
exactly as designed on a program with nothing to seek; it is proof of
correctness (9/9, including a mixed classic+seek slab build at threshold 32),
not of value. The value is only visible where `8 x (P - L)` is large.

## Measured: deadman-3d (native, round-gated, every frame matched)

Re-baselined post-merge: P = 3,957 var-width words (2,132 instructions), frame 1
executes 20,821 instructions and takes 2,609 jumps skipping 382,810 words.

| build | box | fp | boot | per gameplay frame | Δ frame |
|---|---|---|---|---|---|
| drum (canonical) | 374x376 | 141,376 | 3,006,218 | 5,826,361 | — |
| **seek, JMPF only** | **379x382** | **145,924** | 3,089,784 | **4,916,381** | **-15.6%** |
| seek, JMPF+BRZ | 393x382 | 154,449 | 3,166,523 | 4,946,150 | -15.1% |
| seek, all three | 407x382 | 165,649 | 965,346 | 5,375,802 | -7.7% |
| seek, *no* split (all jumps seek) | 378x385 | 148,225 | 842,189 | 7,327,033 | **+25.8%** |

Two results worth keeping:

**1. Seeking every jump is a large loss (+25.8% a frame).** The all-seek build
looks like a win on the boot+frame-1 gate (7.57M vs 8.51M) purely because boot's
107 jumps skip ~3,900 words each and the seek crushes them; the steady-state
frame, whose jumps skip 147 on average, gets 25.8% *worse*. **A gate that
includes boot cannot referee a per-frame change** — the same trap as
"measure where the effect can show", one level up.

**2. Splitting more than `JMPF` is also a loss.** Each extra family costs a lane
and a 13-column slab; the slab band is what pushes the memory block east, and
the pad it forces (17 -> 22 -> 29 -> 36) charges every memory instruction that
walk twice over. JMPF alone carries 80% of the long-jump words for a third of
the width, so `SEEK_OPS` defaults to `("JMPF",)`.

One binding fact worth recording: the classic slabs must stay **west** of the
seek slabs. A classic discard's `r` has to be nearer the ROM pipe than the
STORE's response pipe (§7.1), a seek slab has no `r` at all, and slab order
follows the lanes' drop columns — so `build()` splices the new mnemonics in
*above* every classic structured lane. Placed the obvious way (beside their
originals) the sixth slab's `r` sat 70 cells from `mem_resp` and 95 from `rom`,
and nothing bound.

## Go / no-go against the two criteria

**(a) net ticks/frame improve by >= 5%: PASS — -15.6%** (5,826,361 ->
4,916,381 per gameplay frame, native, frames matched). Boot is +2.8%, one-time.
Over the coordinator's 54-round walk the projection is 305.6M -> ~258M.

**(b) footprint / squareness survives: PASS.** 374x376 -> 379x382: the machine
stays near-square (0.8% off, against the 10% doctrine) and grows 3.2% in area²,
from +6 rows of CPU tail and 5 columns of pad. Nothing about the fold changes.

**Verdict: GO**, and it is wired in behind the per-slug opt-in
(`machine.SEEK_DRUM`, empty by default with `SEEK_MEM_PAD["deadman-3d"] = 22`
recorded so a seek build is deterministic). I have **not** added `deadman-3d` to
the set or regenerated the canonical artifacts: two agents are live on
`deadman3d.py`/`d3_unit.py` for M7b, and flipping it changes every DOOM
artifact. `build_for("deadman-3d", seek=True)` reproduces the measured machine
exactly; the flip is one line when the coordinator wants it.

## Stage-3 artifacts

- `solvers/python/randomfun2026solvers/lm1/seekrom.py` — the drum.
- `machine.py` — `seek_split`, `seek_words`, `SEEK_*` registries, the seek slab
  and tail in `build_cpu`, the request pipe in `_assemble`. Flag-off is
  byte-identical (2,606-test suite green).
- `isa.py` — `JMPS`/`BRZS`/`BRNS` and their sems (build-time only).
- `tests/test_seekrom.py` — 7 fast + 2 slow.
- `scratch/ram-program/`: `toy_seek.py` (standalone drum), `gate_frames.py`
  (the A/B harness), `deadman_seek.man`, `brackets_seek.man`.
