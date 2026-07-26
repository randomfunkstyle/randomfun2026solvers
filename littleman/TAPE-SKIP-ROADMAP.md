# Four-way tape skips: measured design and implementation handoff

Status: implemented and task-benchmarked on 2026-07-26. Batch 4 is available
through the LM-1 STORE API; Pathfinder and Little-Little-Man use it by default.

## Current baseline

The production choices are now:

- `tape_skip_batch=1`: the compact one-value counted loop, 8 worker ticks per
  skipped word.
- `tape_skip_batch=2`: `memory_tape.worker_v2_jump`, a two-sided counted ring,
  5 worker ticks per skipped word. Its default 4×3 alternating relay has the
  same exterior dimensions as the old relay and removes the old six-tick cap.
- `tape_skip_batch=4`: `memory_tape.worker_v2_jump4`, exact two-bit cleanup plus
  a four-word bulk ring.
- `tape_skip_batch=None`: choose batch 2 at or above
  `tape_jump_threshold`, otherwise batch 1.

The size-200 boundary/parity probe writes and reads addresses
`1, 2, 99, 100, 198, 199`. Re-measured after merging `origin/main`:

| batch | grid | ticks | output |
|---:|---:|---:|---|
| 1 | 84×61 | 22,211 | `11 22 33 44 55 66` |
| 2, old relay | 96×61 | 16,392 | `11 22 33 44 55 66` |
| 2, 4×3 relay | 96×61 | 15,617 | `11 22 33 44 55 66` |
| 4, 6×4 relay | 111×61 | 12,403 | `11 22 33 44 55 66` |
| 4, 8×6 relay | 111×61 | 11,431 | `11 22 33 44 55 66` |

The exact debug bundle for batch 4 is
`littleman/examples/memory-tape-jump4-200.{man,html,json}`.

## What one `r`/`s` transfer costs

A successful `r` and `s` each consume one instruction tick. A counted worker
also pays for corners, its BP test, and its decrement. A turnaround relay pays
for its own walk. Sustained ring throughput is therefore the slower of the two
rooms:

```
worker(m) = 2 + 3/m
relay(w,h) = (2(w+h) - 4) / relay_words(w,h)
ring = max(worker, relay)
```

Here `m` is the number of `rs` pairs on each half of
`Circuit.counted_ring("rs" * m)`. One BP unit moves `m` words; a complete lap
moves `2m` words and decrements BP twice.

Reference-interpreter measurements in `tests/test_dataflow_relay.py` pin these
points:

| `m` | relay interior | limiting cost |
|---:|---:|---:|
| 1 | 4×3 | 5.00 |
| 2 | 6×4 | 3.50 |
| 3 | 6×4 | 3.20 |
| 3 | 8×6 | 3.00 |
| 4 | 8×6 | **2.75** |

The old minimal relay is a six-cell, one-word cycle, so it caps every worker at
6.0. A wider worker behind that relay cannot improve sustained throughput.

## Implemented design: power-of-two batch 4

Batch 3 looks slightly smaller, but arbitrary counts require
`q, r = divmod(count, 3)`. The `/` instruction puts the quotient in A and the
remainder in B. That collides with P1's live B value:
`+(N-addr)` means READ and `-(N-addr)` means WRITE. Every `r` overwrites A, BP
cannot be read back, and B is the only register that survives a tape pass.

Batch 4 avoids that collision. Counts are non-negative, and LM-1 already has
backpack bit operations:

1. `x` branches on BP bit 0; the set arm performs one `rs`.
2. Rejoin and execute `]`, making the old bit 1 the low bit.
3. `x` branches again; the set arm performs two `rs` pairs.
4. Rejoin and execute `]`; BP is now `floor(count / 4)`.
5. Enter `counted_ring("rs" * 4)`; each BP unit moves four words.

The two peeled arms move `count mod 4` words before the bulk loop. The total is
therefore exact:

```
(count & 1) + 2*((count >> 1) & 1) + 4*(count >> 2) = count
```

`x`, `]`, `d`, `m`, `r`, and `s` do not modify B. P1 can preserve the signed
remaining-distance/operation tag exactly as `worker_v2_jump` does today. After
the target branch, P2 no longer needs that tag and can use the same bit-peel
plus bulk block directly.

This is preferable to a divide/remainder cleanup because it resolves the
register-liveness problem structurally. The cleanup moves at most three words,
so its routing cost is a fixed intercept rather than a per-slot slope.

## Relay candidates and parameters

Both relay candidates are parameterized and measured:

| batch | relay interior | modelled/measured slope | reason to keep |
|---:|---:|---:|---|
| 4 | 6×4 | 3.20 | smaller footprint, relay-bound |
| 4 | 8×6 | 2.75 | fastest engine-pinned candidate, worker-bound |

An 8×6 relay walks 24 perimeter cells and transfers 9 words per lap
(2.667 ticks/word), so the four-way worker is the 2.75 bottleneck. A 6×4 relay
walks 16 cells and transfers 5 words, so it caps the same worker at 3.2. Because
score squares the longest grid side, the smaller 6×4 candidate may still win.

The API is:

```
tape_skip_batch: 1 | 2 | 4 | None
tape_relay_size: tuple[int, int] | None
tape_jump_threshold: int
```

Semantics:

- explicit batch values select the algorithm, never a heuristic;
- `tape_relay_size=None` selects the smallest relay registered for that batch;
- an explicit relay size must be validated against the generated worker;
- `None` auto-selection remains conservative at batch 1/2;
- `build_for(..., tape_skip_batch="task")` uses measured per-task choices.

The generator/report/debug sidecars must print the resolved batch and relay
dimensions. Do not overload `jump_threshold` with relay selection.

## Why the proposed `Y` pipeline is not the next step

A chain of workers spawned with `Y`, each doing `r` then `s`, has no independent
work before its read succeeds. All children contend for the same ordered input
and output FIFOs, so backpressure serializes the useful operations.

The compact two-lane prototype also exposed a correctness hazard: the right
child retains the parent's creation-order slot while newly spawned workers are
newer. The dispatcher can act before an older value has been stored, allowing
later values to overtake it. One observed failure wrote address 1's value into
address 4.

`Y` remains useful when it creates genuinely independent banks, as in the
banked STORE design in `ARCH.md`. It does not lower the one-FIFO relay's
two-instruction data dependency. The safe single-ring relay is one runner
alternating `r,s,r,s,...`; it preserves order by construction.

## Binding hazard found while measuring batch 4

The original `dataflow_relay` probe attached its request pipe at worker column
8. Increasing its vertical counted ring from `m=3` to `m=4` lowered the return
attachment by two rows. The top ring read then became 13 cells from the request
pipe and 14 from the return pipe, so nearest-pipe binding silently switched to
the request and the probe emitted nothing.

Moving the request attachment to column 6 restores the intended binding:
LOAD remains closest to request; all `m=1..4` rotation reads are closest to the
ring return. With that correction the reference engine measures batch 4 plus
an 8×6 relay at exactly 2.75 ticks/word.

This is also a production warning: do not infer bindings from visual proximity.
Assert every pipe-op binding after placing each worker/relay candidate.

## Whole-task results

All scores below are local `max(width,height)² × public avg ticks`; every listed
candidate passed every public output/frame with the independent native validator.

| task | legacy/previous | batch 2 + 4×3 relay | best batch 4 | selected |
|---|---:|---:|---:|---|
| Pathfinder | 147,140,354,273 | 128,783,668,267 | **127,813,359,906** | batch 4, 6×4 |
| TCP | **568,891,733** | 640,800,976 | 866,425,973 | batch 1 |
| Snake (tape reference) | 8,693,241,406 | **7,332,602,688** | 7,864,634,700 | batch 2, 4×3 |
| Gradebook | **2,476,096,263** | 2,966,282,550 | not routable in current placement | batch 1 |
| Sudoku Validity | **2,343,268,274** | 2,854,317,828 | 4,083,535,733 | batch 1 |
| Little-Little-Man | 281,603,173,417 | 281,603,173,417 | **259,324,223,311** | batch 4, 8×6 |

For Little-Little-Man, the “previous” column is already the improved same-box
batch-2 relay. Batch 4 reduces public average ticks from 7,482,282 to 6,890,324
at the identical 192×194 footprint. Pathfinder remains 177×176 in every mode;
6×4 and 8×6 have identical task ticks, so the smaller relay is selected.

TCP, Gradebook, and Sudoku demonstrate why batch 4 is not the global default:
their tick reductions cannot repay a wider squared footprint. Snake is the
middle case—batch 4 is faster, but batch 2 has the better score and remains
inside the same box. This selects the tape reference machine; the submitted
`snake-ring` coprocessor does not use this tape.

## Implemented file map

- `memory_tape.py`: `_bit_tail_horizontal`, `worker_v2_jump4`.
- `lm1/machine.py`: batch/relay resolution, task configuration and layout.
- `dataflow_relay.py`: scalable FIFO relay art and measured cost model.
- `tape_jump_debug.py`: synchronized size-200 debug bundle.
- `tests/test_memory_tape_jump.py`: exact boundary/remainder behavior.
- `tests/test_dataflow_relay.py`: reference-engine slope measurements.

The first behavioral cases must cover counts
`0, 1, 2, 3, 4, 5, N-2, N-1` on both P1 and P2, for READ and WRITE. Include
sizes around routing seams (`107/108`) and selection thresholds
(`127/128`, `199/200`). Assert outputs, alignment after every operation,
capacity, bindings, and a relative improvement over a same-run baseline.
Do not pin a footprint, score, or exact application tick count in tests.

## Further work

- Compact the 49×24 worker before reconsidering TCP/Sudoku; it needs to recover
  at least the footprint columns shown in the table, not merely save more ticks.
- Give two-tier stores separate hot/cold relay parameters. Little-Little-Man
  currently uses the same 8×6 relay for both; a smaller hot-bank relay might
  preserve ticks while simplifying its internal floorplan.
- Sweep Pathfinder’s ROM fold jointly with batch 4. Its current 177×176 crossing
  already hides STORE, so any win must come from a new global fold, not tape
  compaction alone.

The independent `manatom.unrolled(v)` gadget costs `4 + 4/v` and requires a
divisible count. It is not the same as the two-sided `counted_ring`, whose cost
is `2 + 3/m`; reusing its remainder-peeling assumptions would select the wrong
geometry.
