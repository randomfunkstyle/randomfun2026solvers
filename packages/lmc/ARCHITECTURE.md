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

## Memory is behind a swappable interface (a scoring lever)
The command ISA **is** the memory abstraction boundary. A consumer (a program, or the CPU)
issues `LOAD_IDX/STORE_IDX/PUSH/POP` and never sees how they are realized. The
*representation* behind them is a pluggable `Memory` implementation:

- **`RotateRingMemory`** (impl #1) — one ring, address by rotating to the index. O(addr)
  per access (slow, small footprint). Built first.
- **direct-access** (impl #2, later) — a faster structure (e.g. banked rings, or a
  display-backed store) that lowers ticks. O(1)-ish.

Because `max(w,h)² × avg_ticks` trades area against speed, **the representation is chosen
per problem for score** — swap the impl, not the program. Interface (Python-side fragment
factories, so it works whether memory is a single man or lives behind the DMA bus):
```
class Memory(Protocol):
    def rooms_pipes(self, owner) -> (rooms, pipes)   # what the router draws
    def read(self)  -> fragments   # pre: index in A -> post: value in A / emitted
    def write(self) -> fragments   # pre: index, value staged -> stored
    def sizing(self, n_cells) -> hints
```
Programs (`memory`, `sort`, `sudoku`, `grade_book`, `matrix`) are written against
`Memory`; the impl is injected. This is the same data-abstraction discipline as
`stores.py` (Ring/Cell/List/Array/Stack), one level up.

**Implementation must be hand-laid, not composed.** A `RotateRingMemory` built from
generic `forever_loop`/`while_loop`/`if3` goes UNSAT — the composers scatter the input
reads / ring ops / output sends and the nearest rule can't place them (verified). Every
`Memory` impl is therefore a **fixed hand-laid man** following the DMA method: zoned data
path (input reads West, ring ops near the ring, output East) + a control-only looper +
control-glyph addressing loops (rotate-to-index is routing-neutral). `dma.py` is the
worked template; `memory` is a hand-laid man of the same shape with an internal
rotate-to-addr loop and a READ/WRITE dispatch. Swapping to direct-access = a different
hand-laid man behind the same `Memory` interface.
Compute (compare/ALU/branch) lives in the CPU; the DMA is the memory + I/O subsystem.

## Memory as a chain of sub-blocks (the buildable plan)

A single memory man (addressing loops + dispatch + I/O + ring) has too many pipes/loops to
route and a register wall. **Split it into a pipeline of two few-pipe men** — each routes,
each is standalone-testable, and the register wall vanishes:

```
problem input ─▶ [ Driver ] ─cmd stream─▶ [ DMA ] ─▶ problem output
                 translate               ring exec
```

- **DMA-mem** (block 1) — hand-laid ring man (the `dma.py` template). Commands:
  `ADVANCE` (rotate ring by 1, no emit), `PEEK` (emit head), `REPLACE value` (head:=value).
  Standalone test: drive the command stream from input, observe output.
- **Driver** (block 2) — reads `op, addr, [value]`; emits commands. `READ(addr)` =
  ADVANCE×addr, PEEK, ADVANCE×(N−addr). `WRITE(addr,value)` = ADVANCE×addr, REPLACE value,
  ADVANCE×(N−addr). **No register wall:** dispatch `op` first; `addr` stays in B as the
  ADVANCE counter; `value` is read late and forwarded immediately (never held across the
  restore). Pipes: input(W) + command-out(E) → routes. Standalone test: drive an op-stream,
  observe the command stream.
- **Chain** (block 3) — wire Driver→DMA (proven by `chain.man`) → `memory`.

`sort` follows the same shape: a Driver doing the selection/compare logic over a DMA that
holds the list. Each block is added and tested on the oracle before composing.

## Status
Built + validated + committed: the routing model, `if3`, stores, the CPU↔DMA bus, and the
DMA stack (PUSH/POP/ROTATE) with its looper.

**Routing feasibility of the full memory subsystem is proven.** A 6-pipe integrated DMA —
`req`/`resp` (North bus), RAM (South), input (West), output (East) — is SAT under the
zoning rule (RAM ops dip South near the ring; result-sends route North to `resp`; `IN`
reads West; `OUT` sends East). So every routing/architecture unknown for semester-1 is
resolved; what remains is assembly, not discovery.

Remaining for **sort + memory** (semester-1): hand-lay the integrated DMA *logic* (the
5-command datapath: PUSH/POP/ROTATE + IN/OUT, with the winding S→N result paths + looper),
then write the two CPU programs over the bus (the CPU spills its transient values to the
DMA stack, so it has no register wall). Substantial hand-layout, but no unknowns.
