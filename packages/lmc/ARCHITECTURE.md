# LMC processor architecture — CPU, DMA, looper

How we run real programs on the little-man machine: not one fat CPU, but a small
**system** of fixed hardware blocks joined by pipes. This doc is the map; each block is
a separately testable unit validated on `littleman.wasm`.

## Why a system, not one CPU

The engine routes every `s`/`r` to the **nearest** pipe (Manhattan, reading-order tie).
So a single man that touches several same-direction pipes (ROM + RAM + I/O) has **no
attachment placement** that makes each op reach its intended pipe — it goes UNSAT. This
killed every "fat CPU" attempt. Two facts break the wall:

1. **`R`/`S`/`U` are position-independent.** `R`/`U` read from *any* ready incoming pipe;
   `S` sends to *all* outgoing. They ignore the nearest rule (the router exempts them).
2. **Control glyphs are routing-neutral.** `> < ^ v X d a x` move the man but touch no
   pipe, so loop/branch wiring never competes for a pipe.

From these: give each man **≤ one pipe per direction that it selects by nearest**, push
everything else behind a bus or `R`-merge, and build loops/branches from control glyphs.

## The blocks

```
        input                              output
          │  (memory-mapped, owned by DMA)   ▲
          ▼                                  │
   ┌──────────────┐   req  (cmd, value)   ┌──────────────────┐
   │   CPU (man)  │ ───────────────────▶  │   DMA (man)      │
   │  A · B · BP  │                       │  RAM ring (South)│
   │  compute +   │ ◀───────────────────  │  + I/O ports     │
   │  bus ops     │   resp (data)         └──────────────────┘
   └──────────────┘
     one bus only                          fixed hardware,
     -> always routable                    control/data split
```

### DMA — memory controller (`dma.py`) ✅ built
A fixed hand-laid man: a **stack/RAM ring** (South) behind a `(cmd, value)` command
interface. Today: `cmd==0` PUSH value, `cmd>0` POP (emit head). The design rule is the
whole point:

- **Data path** (pipe-ops) is **spatially zoned** — `req` reads top-left (West), RAM ops
  low (near the South ring), `dout` far East — so nearest-routing is SAT.
- **Control path** (the loop back to re-read) is **pure control glyphs**, routing-neutral,
  so it never disturbs the data path.

Standalone-testable: `render_dma_standalone` drives `req` from an input room and observes
`dout` (`test_dma.py`: push/pop, interleaved, negatives, FIFO). The DMA hides the memory
*representation* (ring today; banked later) — swappable for scoring.

### Looper — control-only sequencer ✅ pattern established
The loop that cycles a man back to its start is built **only** from `> < ^ v` (see the
DMA's return lane: down the right column, west along the bottom, up the left column, into
the re-entry `>`). No pipe-ops, so it is routing-neutral. Generic `forever_loop` fails
here precisely because it re-places the data ops; a hand-laid looper leaves them alone.
Treat the looper as a hardware block, separate from the datapath it wraps.

### CPU — core (in progress)
The CPU talks **only over the bus**: `s req` is its single outgoing pipe (routes
trivially) and `resp` is read with `R` (exempt). So the CPU is routable **regardless of
program**. It computes in A/B/BP and issues bus commands for all memory/I/O; it never
touches a store directly. The two-man CPU+DMA bus is validated end-to-end (`soc.py`,
`test_soc.py`: echo through memory-mapped I/O).

Programs run on the CPU by **unrolling** into bus-command sequences (the CPU always
routes, so no per-program routing solve). A bytecode **interpreter** (program in a ROM
ring, fetch-decode-execute) is a later *footprint* optimization over the same command ISA.

## Instructions are testable blocks

Every capability is added as a **separate, oracle-validated block**, same as the ones
already here — never a monolithic rewrite. Proven blocks so far:

| block | file | tested by | what it proves |
|---|---|---|---|
| `if3` (3-way branch, merge) | `loopgen.py` | `test_loopgen.py` | compare/branch via `X` |
| South spill store | `router.py` | `test_router.py` | a 2nd store, opposite side |
| Ring/Cell/List/Array/Stack | `stores.py` | `test_stores.py` | store fragments |
| CPU+DMA bus (echo) | `soc.py` | `test_soc.py` | one-bus core + mmapped I/O |
| **DMA PUSH/POP** | `dma.py` | `test_dma.py` | memory controller + looper |
| footprint scoring | `score.py` | `test_score.py` | `max(w,h)²` measure |

**Adding an instruction** (the pattern to follow):
1. Add the op as a **new command arm in the DMA** (its own zoned pipe-ops) *or* a **CPU
   bus-op sequence** — a small block, not a rewrite.
2. Keep the data path zoned and the control path in control glyphs.
3. Write a **standalone test** that drives it in isolation on the oracle (`run_grid`),
   like `test_dma.py` — inputs → expected outputs, plus a render/route check.
4. Only then compose it into a program.

## Command ISA (target)
```
PUSH value   POP → data   ROTATE → head   ← stack + iteration (built)
LOAD_IDX     STORE_IDX    LEN             ← array a[i] (CPU rotates i then POP, next)
IN → data    OUT value                    ← memory-mapped I/O (echo built)
```
Random access `a[i]` needs no new DMA command: the CPU issues `ROTATE` i times, `POP`s
the head, then `ROTATE`s the rest to restore canonical rotation.
Compute (compare/ALU/branch) lives in the CPU; the DMA is the memory + I/O subsystem.

## Status
Built + validated + committed: the routing model, `if3`, stores, the CPU↔DMA bus, and the
DMA stack (PUSH/POP) with its looper. Next blocks: DMA `LOAD_IDX/STORE_IDX` and `IN/OUT`
command arms, then unroll `memory` and `sort` as CPU programs over the bus.
