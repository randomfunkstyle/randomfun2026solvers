# LM-1 — a general-purpose computer written in littleman

**Status: a generated CPU solves a graded problem end to end.** The ISA,
emulator and assembler exist (`lm1/`, 230 tests green) and all 7 memory-free
problems pass every public test case in the emulator. On the real interpreter,
`lm1/cpugen.py` emits a complete 7-opcode machine — ROM + code ring + CPU +
register cell + I/O — that passes 6/6 public cases of `triangle` at 446 ticks
(§2.6). Generalising that generator to the full ISA table is what remains
(§9 step 5).

Two problems are already solved *without* LM-1, as bespoke grids: `memory`
(accepted by the judge) and `history-lesson`. LM-1 is the safety net for the
rest, not the plan of record (§1).

Visual walkthrough: [`arch.html`](arch.html) — the verified units, an animated
run of the whole machine driven by real interpreter snapshots, the tick budget
and the semester matrix.

Companion docs: [`SPEC.md`](SPEC.md) (the language) ·
[`GRADING.md`](GRADING.md) (scoring) · [`../tasks/problems/`](../tasks/problems/)
(the 16 problems).

## 1. Goal and the trade we are consciously making

Build **one** machine — a fetch/decode/execute CPU with pipes for buses — and
then solve each contest problem by writing a *program* for that machine instead
of hand-drawing a bespoke `.man` grid. One machine, sixteen programs.

Score is `max(width, height)² × avgTicks`, and a CPU is both large and slow, so
**LM-1 will score badly on purpose**. That is fine, because points are:

```
test-case points = passing cases / total cases    (max 1)   <- LM-1 earns this
ranking points   = teams you beat on score        (max 1)   <- LM-1 earns ~0
```

A bad score still collects the **full test-case point on every problem it
solves**. 12 graded problems × 1 point is the prize; bespoke hand-written grids
can later reclaim the ranking half on individual problems. LM-1 is the safety
net, not the optimum.

## 2. Verified ground truth

Everything below was checked against the bundled reference interpreter, not
inferred from the spec. Re-run any of it with either front-end:

```sh
./lm.mjs run  prog.man [--input "1 2 3"] [--json] [--max-ticks N]
./lm.mjs tick prog.man 40 [--json]

# typed Python front-end over the same wasm (pydantic snapshots, same semantics)
uv run python -m randomfun2026solvers.littleman run  prog.man --input "42"
uv run python -m randomfun2026solvers.littleman tick prog.man 40 --json
```

Use the Python wrapper (`solvers/python/randomfun2026solvers/littleman.py`) for
anything programmatic — it parses snapshots into typed models, so the generator's
test suite can assert on runner positions, register contents and **per-pipe
values** rather than scraping text.

### 2.1 A two-room ring circulates values forever, in order

Probe: room A seeds `1 2 3` into a pipe, then loops `r`/`s`; room B loops
`r`/`s`; the two pipes close the loop.

```
+--------+   +----+
|@1s2s3sv|>->|@>rv|
|.....vr<|   |.^s<|
|.....>s^|   +----+
+--------+    v
     ^        |
     ^--------<
```

Result: values circulate indefinitely as `1 2 3 1 2 3 …`, **order preserved**.

- **Ring throughput is 6 ticks per word.** The bound is the *room*, not the pipe:
  a man needs a closed walking cycle, and the smallest cycle containing two
  operations is 3×2 = 6 cells (`<` `r` `v` / `>` `s` `^`) — the four corners must
  be turn glyphs, leaving two free cells.
- **Pipe length adds latency, not throughput.** A long return pipe delays the
  first lap; the steady-state rate is still one word per 6 ticks.
- **Consequence (load-bearing):** total ring capacity must be `P + slack`, where
  `P` is the program's word count. Capacity `< P` deadlocks; capacity `≫ P`
  starves the CPU while words make their way around. The generator knows `P`, so
  it sizes the ring pipes to `P + 2..4`.

### 2.2 The backpack is an instruction decoder

Probe: `@5bx` / `@4bx` / `@5b]x`, each with `H` on both branch targets, reading
the final heading out of the snapshot.

| BP | glyph | heading east becomes | meaning |
|---|---|---|---|
| 5 (low bit 1) | `x` | south — **clockwise** | bit = 1 |
| 4 (low bit 0) | `x` | north — **counter-clockwise** | bit = 0 |
| 5 → `]` → 2 | `x` | north | `]` is `BP >>= 1`, heading unchanged |

So `b` + repeated `]`/`x` walks a **binary trie over the opcode's bits**. That
trie *is* the decoder — no comparison chain, no jump table. Depth *k* dispatches
2^*k* opcodes and each leaf is a distinct cell, which is exactly where that
opcode's micro-program starts.

### 2.3 ROM unit — literals walked once

```
+-------------------+  +-+
|@`1`s`12`s`345`sH  |>>|O|
+-------------------+  +-+
```

Emits `1 12 345`. Backticks pair left-to-right within the row (1st with 2nd, 3rd
with 4th, …), and each closing backtick loads the whole number into `A`, which
`s` then ships. One row, one word per `` `NNN`s `` group.

### 2.4 DECODE unit — a depth-2 trie dispatching 4 opcodes

Entry is the man arriving **heading east** with `BP` = opcode; each lane here
just emits a marker so the mapping is observable.

```
+-----------+
|....>4sH...|
|...>x......|
|...]>6sH...|   +-+
|@0bx.......|>->|O|
|...]>5sH...|   +-+
|...>x......|
|....>7sH...|
+-----------+
```

Measured, substituting `0`/`1`/`2`/`3` for the opcode digit:

| opcode | emits | lane row |
|---|---|---|
| 0 | 4 | 1 |
| 1 | 5 | 5 |
| 2 | 6 | 3 |
| 3 | 7 | 7 |

12 ticks total including the send and halt, so decode itself is ~6 ticks at
depth 2. Note the lane rows run `0, 2, 1, 3` top-to-bottom — the trie sorts
leaves in **bit-reversed** order, which is the concrete reason §7.1 can treat
opcode numbering as a free layout variable.

The lanes deliberately use bare digits rather than `` `NN` `` literals: four
stacked literal rows would put backticks in the same **column**, forming
unintended vertical pairs with `.` between them — a load error, exactly the
hazard in §4.2. It bites immediately in practice.

### 2.5 The whole machine, end to end

ROM + code ring + CPU + output, with a two-opcode ISA: word `0` = `HALT`, any
positive word = `OUT` that value. Program: `7 8 9 0`.

```
+------------------+
|@7s8s9s0sH........|
+------------------+
  v
  v
+----+   +---------+
|@>Rv|>->|@v.......|
|.^s<|<-<|.>rsXH...|   +-+
+----+   |.^..>..sv|>->|O|
         |.^......<|   +-+
         +---------+
```

Emits **`7 8 9`** and the CPU man halts on the `0` word. Left room is `LOOP`
(`R` accepts from both ROM and CPU, `s` feeds the CPU). Right room is the CPU:
`>` re-orients, `r` fetches, `s` recirculates into the ring, `X` decodes on
sign, `H` halts, and the lower lanes carry the `OUT` micro-program and the
return path back to `r`.

- **Measured: 20 ticks per instruction** (outputs land at t=20, 40, 60). Better
  than the §7.3 estimate, because this decoder is a single `X`; a depth-4 trie
  adds ~15.
- The LOOP man never halts, so the run only ends at the tick cap. Harmless —
  grading stops counting at the last correct output and does not require halting.
- Nearest-pipe resolution came out as designed: the `s` at `(13,7)` is 5 away
  from the ring-out source and 8 from the output source, so it recirculates;
  the `s` at `(17,8)` is 3 from output and 10 from ring-out, so it emits.

**Heading is part of a unit's port contract.** The first version of this slice
hung with no output: the fetch `r` was entered heading *south*, so after
receiving, the man stepped south onto the return lane's `^`, which sent him
straight back onto `r` — a two-cell loop that refetched forever while the
decoder was never reached. Cell contents were all correct. Every unit's entry
port must therefore declare **(cell, heading)**, and the generator has to assert
on it; `flow(rows)` from the wasm (per-cell reachable headings) is the tool for
that.

### 2.6 A complete CPU running a real program

`lm1/cpugen.py` generates a whole machine — ROM + code ring + CPU + register cell
+ I/O — that solves `triangle` **as a program**, on a 7-opcode ISA
(`IN STR ADDI MULR DIVI OUT HALT`) dispatched by a depth-3 backpack trie.

```
program:  IN · STR · ADDI 1 · MULR · DIVI 2 · OUT · HALT
ROM:      0 0  6 0  2 1  1 0  5 2  7 0  4 0        (14 words, fixed 2-word form)
```

**6/6 public cases on the reference interpreter**, plus n=999 and n=1000.
46×31, **446 ticks**, score **943,736**. Every pipe instruction was confirmed
against `tools/route-check.mjs` to resolve to its intended pipe.

- The emulator predicted 407 ticks and the hardware measured 446, so **§7.3's
  tick model is good to ~10 %** — it can be trusted for planning.
- Spill is one `register-cell` on the east bus, which is §4.1's two-tier rule in
  practice: `triangle` needs exactly one live value besides ACC, because `A` dies
  on every fetch.
- Opcode numbering was chosen so the lanes land next to the walls they need
  (§2.4's bit-reversal): `IN` = 0 → top row, beside the north input pipe;
  `OUT` = 7 → bottom row, beside the south output pipe.

**The bespoke grid for the same problem is 24×3, 12 ticks, score 6,912 — 137×
better.** Both pass 6/6. That is the §1 trade, measured rather than assumed: the
CPU costs two orders of magnitude and buys the ability to solve a problem by
writing seven lines instead of deriving a grid.

## 3. Block diagram

Every box is a `layout.py` `Container` with fixed ports; every arrow is a pipe
that `layout_graph` routes and validates. Blocks are swappable as long as they
honour the port contracts in §4.

```
        +-------+
        |  ROM  |  program as `NNN` literals, walked once at boot
        +---+---+
            | words
            v
        +-------+  code ring (capacity P+slack)
        | LOOP  |<-------------------------+
        +---+---+                          |
            | ring-in                      | ring-out
            v                              |
   +-----------------------------------+---+
   |               CPU                 |
   |  one man:  A = word/scratch       |
   |            B = ACC                |
   |            BP = decode & counters |
   |                                   |
   |  [fetch] -> [trie] -> [16 lanes]  |
   +--+-----+------+------+--------+---+
      ^     |      |      ^        |
   in |  out|   req|  resp|     3x |
      |     v      v      |        v
   +--+-+ +-+-+ +--+------+--+ +---+-----+
   | I  | | O | |   STORE    | | LM-75   |
   +----+ +---+ +------------+ +---------+
                  (abstract)     (optional)
```

**The ring runs *through* the CPU.** The CPU is one of the ring's two rooms: it
receives each word and immediately sends it back. This removes a whole room of
latency, and — more importantly — makes fetch strictly lock-step. There is no
prefetch, so there is no branch-flush hazard to reason about.

## 4. Block contracts

### 4.1 Memory — two tiers, and the second one is not optional

`memory` is **solved** (`programs/memory.man`, accepted by the judge), so this is
no longer a stub. Both blocks below are measured on the reference interpreter:

| Block | Size | Cost per access | Protocol |
|---|---|---|---|
| `memory.man` — rotating pipe tape, 100 cells | 32×32 | **~750 ticks, constant** | `0 addr` / `1 addr value` |
| `register-cell.man` — one value | 6×7 | **~20 ticks** round trip, non-destructive read | `1 v` store · `-1` fetch |

The tape is a **drop-in `STORE`**: its wire protocol is the `memory` problem's,
which is exactly the contract this document specified, so no translation is
needed. It runs exactly one revolution per operation, so cost is constant in the
*address* — better than the delay line originally assumed here.

#### `STORE` is a family, not a block

`memory_tape.build_v2(n)` is parameterized, so there is a whole family of
variants and the generator should pick one per problem. Measured sweep (10
writes + 10 reads across the address space, reference interpreter):

| N | 4 | 8 | 16 | 32 | 48 | 64 | 100 |
|---|---|---|---|---|---|---|---|
| ticks/op | 138 | 164 | 229 | 364 | 496 | 630 | **936** |
| footprint | 1024 | 1024 | 1024 | 1024 | 1024 | 1024 | 1024 |

So `ticks/op ≈ 105 + 8.3·N`, and **footprint is 32×32 independent of N**.

That gives an unusually clean generator rule: **size N to the problem's actual
slot count. It is free on footprint and linear on ticks — there is no trade-off
to weigh.** `tcp` at N=48 is 1.9× cheaper per access than the N=100 build,
`brackets` at N=32 is 2.6×, `sort-numbers` at N=16 is 4.1×.

Note the ~105-tick fixed overhead per operation (read op, read addr, arithmetic,
dispatch) — it does not amortise away, which is why even an N=4 tape costs 138
ticks and loses to a `register-cell` by 7× for scratch. The tiers are distinct
mechanisms, not two sizes of the same one.

Further variants, in rough order of payoff (the first two are described in
`programs/README.md` and not yet built):

- **Relative rotation** instead of a full revolution — rotate `(addr − next) mod
  N` and keep the index in a `register-cell`: ~2.6× on the slope.
- **A larger pass-through loop ring** — the 2×4 loop costs 8 ticks/value, but a
  6×6 hollow square amortises the fixed corner/test/decrement cost over 7 values
  in flight, reaching ~2.9: another ~2.2×.
- **Banking** — k rings, address = (bank, offset), ~N/k per access at k× the
  pipe area. The only route to `matmul`'s 768 slots, and still a stretch.
- **`register-cell` arrays** — for a handful of named scalars, k cells beat any
  tape outright.

`layout.py`'s `Container.variants` is the mechanism for handing the solver
several of these and letting it pick one that routes.

**The two tiers are a precondition, not an optimisation.** The emulator (§9
step 2) measured that **41–53 % of executed instructions are `LD`/`ST`**, and
almost none of that is arrays — it is register spill, because `A` dies on every
fetch and a loop needs 2–3 live values. Route spill through a 750-tick tape and
the machine dies on ticks: `triangle` alone is ~10k accesses ≈ **7.5M ticks
against a 5M cap**. Route it through register cells at ~20 ticks and the same
program is comfortable.

So the memory hierarchy is:

- **SPILL** — 2–4 `register-cell` blocks for loop counters and temporaries,
  reached by `PUSH`/`POP` or `LDR`/`STR` (§6.1). Also the only way to implement
  indirect addressing at all (§6.1).
- **STORE** — the tape, for arrays *only*: `sort-numbers`, `matmul`,
  `sudoku-validity`, `subset-sum`, `gradebook`, `reverse-a-list`, plus — not
  previously on that list — `brackets` (32-deep typed stack) and `tcp` (48-slot
  reorder buffer).

Hazard inherited from `register-cell`: the command `0` walks its man into a wall
and kills the whole program. Any bus that can carry a `0` must bias it to ±1.

### 4.2 ROM — the program store

One man walks a serpentine of numeric literals, `s`ending each word into the
ring, then halts. Ports: 1 out (`words`). Footprint ≈ 1 cell per digit.

Two codegen hazards, both real:

- **Digit reversal.** `` `123` `` walked right-to-left loads **321**. Serpentine
  rows alternate direction, so odd rows must be emitted with digits reversed.
  (The alternative — every row left-to-right with a return lane — costs an extra
  row per word row but removes the class of bug entirely. Start there, optimise
  later.)
- **Accidental vertical literals.** Backticks pair on rows *and columns
  independently* (`SPEC.md` §Fine print). Stacked literal rows with aligned
  backticks form vertical pairs you did not intend, and a non-digit between such
  a pair is a **load error**. The generator must stagger literals so backtick
  columns never align, and assert on it.

### 4.3 CPU

One room, one man, four regions: fetch site, decode trie, 16 opcode lanes, and a
return path back to fetch.

### 4.4 I/O and display

`I` and `O` are 3×3 rooms with exactly one pipe each. The LM-75 needs three
pipes from the CPU (top = ADDR, left = DATA, bottom = SWAP) and is only wired in
for `plotter` / `palette`; emitting program output on a display problem is an
error, so the generator omits the `O` room in that configuration.

## 5. Registers, words, and the code ring

### 5.1 Register model

A little man carries `A`, `B`, `BP`, and that is the entire register file.

| Register | Role | Why |
|---|---|---|
| `A` | instruction word / scratch | clobbered by every `r`, so it cannot hold state |
| `B` | **the accumulator (ACC)** | survives fetch and decode untouched |
| `BP` | decode bits, loop counters | write-only (`b`, `m`, `]`), branch-only (`d`, `a`, `x`) |

ACC lives in `B` precisely because fetch destroys `A`. That costs a `W` or `M` in
most micro-programs, which is cheap.

What is *not* cheap is that three registers are not enough. **A loop needs 2–3
live values and ACC holds one**, so every loop spills — measured at 41–53 % of
all executed instructions (§4.1). The original claim here, that this beats
"spilling ACC to a pipe", compared the wrong two options: the real choice is
whether spill pays *tape* latency or *register-cell* latency, and that is a 40×
difference. Hence the SPILL tier in §4.1; it is load-bearing, not a nicety.

### 5.2 Word format

**No bit packing.** One integer per word; an instruction is an opcode word
followed by 0 or 1 operand words, as declared by the ISA table. Extracting a
packed operand would need `}` (which needs `B`, which holds ACC) — separate
words dodge that entirely, at the cost of a slightly longer ROM.

**Prefer fixed-width 2-word instructions** (opcode + operand, operand ignored
where unused). This was the change that made the first working CPU close
geometrically (§2.6): if instructions are variable-width, an operand-taking lane
has to read the ring from *inside the lane*, and a lane sitting 4 rows from the
input pipe finds that pipe nearer than the ring — so the operand fetch silently
reads program input instead. Making every instruction 2 words moves all ring
access into the fetch stage, so **each lane needs only the one pipe its own
micro-program uses** and §7.1's constraint becomes trivial to satisfy. It costs
ring words and buys a CPU that works.

### 5.3 The PC is the ring's rotation phase

Words leave the ring in program order, so sequential execution needs no PC at
all: the next word *is* the next word. That makes the jump primitive:

```
JMPF n   — recirculate the next n words without executing them
```

The assembler resolves labels into `n`, so **source-level jumps are absolute**
while the hardware only ever skips forward. A backward jump to a target `L`
words back costs `n = P − L` skips.

Precisely: `n = (target − after) mod P`, where `after = pos + 1 + operands` — the
word *following* the jump, because the jump's own operand has already been
consumed by the time the skip starts. The generator must use the same convention
as the assembler; getting this off by one silently executes the wrong word.

Confirmed by the emulator: **forward-skip-only is not painful to compile
against.** The resolver is two lines and works uniformly for forward and backward
jumps; nothing ever wanted a real PC. Only the *cost* hurts — 13–28 % of total
ticks, worst on `brackets`, because §5.4's "put the hot loop last" advice fails
when a program has several hot loops competing for the tail.

**Invariant — every ring read is immediately followed by a ring write-back.**
Opcode words *and* operand words *and* skipped words all go back into the ring,
or the program erases itself on the first lap.

### 5.4 Jump cost, and the rule that follows from it

Skipping a word costs ~8 ticks (a 4-op cycle: `r`, `s`, `m`, `d`). A backward
jump in a `P = 60` program is therefore ~480 ticks — the machine's dominant
cost.

The mitigation falls out of the cost model: **the skip is `P − L`, so make the
hot loop most of the program.** A program that is one tight loop pays almost
nothing per iteration; a tight loop buried in a long program pays for the whole
rotation. Keep `P` small and put loops last. (v2 option if this bites: a second,
short "loop ring" so hot bodies never rotate the full program.)

## 6. ISA v1 — a table, not a hardwiring

Per decision, the accumulator ISA below is **one instantiation, not the
architecture**. It lives as data in Python; the decode trie, the lane layout and
the ROM encoder are all *generated* from it. Adding an opcode means adding a row
to the table, not redrawing ASCII.

Notation: `A` = scratch, `B` = ACC. `r↺` = read from ring **and write back**.
`s→mem`, `s→out` etc. name which pipe the `s` targets (see §7.1).

| # | Mnemonic | Operand | Effect | Micro-program |
|---|---|---|---|---|
| 0 | `NOP` | — | — | (fall through) |
| 1 | `LDI n` | word | ACC = n | `r↺` `M` |
| 2 | `IN` | — | ACC = next input | `r→in` `M` |
| 3 | `OUT` | — | emit ACC | `W` `s→out` `W` |
| 4 | `ADDI n` | word | ACC += n | `r↺` `+` `M` |
| 5 | `SUBI n` | word | ACC −= n | `r↺` `-` `N` `M` |
| 6 | `MULI n` | word | ACC *= n | `r↺` `*` `M` |
| 7 | `LD addr` | word | ACC = store[addr] | `0` `s→mem` `r↺` `s→mem` `r→mem` `M` |
| 8 | `ST addr` | word | store[addr] = ACC | `1` `s→mem` `r↺` `s→mem` `W` `s→mem` `W` |
| 9 | `ADD addr` | word | ACC += store[addr] | `LD` body then `+` `M` |
| 10 | `SUB addr` | word | ACC −= store[addr] | `LD` body then `-` `N` `M` |
| 11 | `JMPF n` | word | skip n words | `r↺` `b` then skip-cycle |
| 12 | `BRZ n` | word | skip n if ACC == 0 | `r↺` `W` `X` → 3 lanes |
| 13 | `BRN n` | word | skip n if ACC < 0 | same shape, lanes permuted |
| 14 | `DSP p` | word | send ACC to display port p | `W` `s→addr/data/swap` `W` |
| 15 | `HALT` | — | stop | `H` |

Two details that make this work:

- **`OUT` preserves ACC for free.** `W` `s` `W`: the first `W` brings ACC into
  `A`, `s` sends it without clobbering `A`, the second `W` puts it back in `B`.
- **`X` is a native three-way branch.** After `r↺` (`A` = n, `B` = ACC), `W`
  gives `A` = ACC and `X` turns by `sign(ACC)` — negative / zero / positive land
  on three different cells. A second `W` on *every* lane restores `A` = n and
  `B` = ACC, so taken and not-taken paths share the same fix-up. `BRZ` and `BRN`
  are the same hardware with the lanes relabelled.

The `LD`/`ST` micro-programs also show why the STORE protocol is `op, addr
[, value]` and not `addr, op`: emitting the opcode digit **first** means the
operand can be read from the ring straight into `A` and forwarded, so ACC never
needs a spill slot. That ordering happens to be exactly the `memory` problem's
wire format, which is why §4.1 costs nothing.

### 6.1 v2 — what the emulator proved the table is missing

All 7 memory-free problems pass on v1, so v1 is *adequate*. It is also badly
inefficient, and two rows are outright wrong. These are implemented as `LM1_EXT`
in `lm1/isa.py`; the numbering is deliberately unspecified here because
assignment is a layout decision (§7.1).

| Mnemonic | Operand | Why it earns a row |
|---|---|---|
| `MUL addr` | word | v1 has `MULI` but no memory multiply |
| `DIVI n` / `MODI n` | word | **no division or modulo existed in any form.** `triangle` must loop 1000× (654k ticks) where `n(n+1)/2` is 7 instructions and 407 ticks — a **1600× difference for two rows** whose micro-programs are 4 and 7 glyphs. `brackets` needs `MODI` for its packed base-3 stack |
| `NEG` | — | missing, while §4.2's ROM can only encode non-negative literals, so `-1` costs `LDI 0` + `SUBI 1`. The assembler now rejects negative operand words for this reason |
| `LDP` / `STP` | word | **indirect through a pointer cell.** v1 has immediate addressing only, so an array access means unrolling a 48-way `BRZ` ladder: ~1000 extra words and ~50 branches per access, i.e. ~50× on both footprint and ticks |
| `PUSH` / `POP` | — | fall out of the SPILL pipe for free once `LDP`/`STP` exist |

Two corrections to the v1 table itself:

- **`SUBI` is one glyph too long.** `r↺` `-` `N` `M` should be `r↺` `W` `-` `M`;
  same for `SUB addr`. `N` is never needed when `W` can reorder the operands.
- **Indirect store is impossible without a spill slot — a real hole in §5.1.** To
  honour the STORE protocol you must emit `1` *before* the address, but the `1`
  glyph writes `A`, and `B` holds ACC, so the fetched pointer has nowhere to
  live. There is no third register and `BP` cannot be read back. `LDP`/`STP`
  therefore park the pointer in the **SPILL pipe**. If you want arrays at all,
  that pipe is not optional — which is the same conclusion §4.1 reaches from the
  tick side.

## 7. What the generator has to get right

### 7.1 Pipe binding is declared, not hand-solved

`s` targets the nearest **outgoing** pipe and `r` the nearest **incoming** one —
Manhattan distance, ties by reading order, and *nearest*, not
nearest-that-can-proceed. The CPU room has up to 8 pipes, so which pipe an
instruction talks to is decided by where the glyph sits.

That is a constraint we **declare**, not a puzzle we solve by hand.
`layout.py`'s `Container` already models exactly this: `inputs` and `outputs` are
lists of **local cell coordinates**, and the list index *is* the port number. So
the CPU container names its `r` and `s` cells as ports; `Edge` wires port to
port; and the router does the rest:

- `_exit` projects each port onto its nearest border edge and derives the pipe's
  **touch cell**, breaking ties toward the other container;
- `_resolve_port` then asserts that the manhattan-nearest port to that touch cell
  really is the intended one, and raises `LayoutError` if a pipe would land on
  the wrong port.

So the generator's job is to place `r`/`s` glyphs near the wall they belong to
and let placement and routing be solved for it. `Container.variants` is the
escape hatch: offer several equivalent CPU layouts and let the solver pick one
that routes.

The useful discipline is still to bind each function to a wall — west for the
ring, east for `STORE`, north for input, south for output and display — so that
ports cluster and the solver has an easy job. Two levers make that cheap:
**opcode numbering is a layout variable** (§2.4: the trie sorts leaves in
bit-reversed order, so you choose which opcode lands next to which wall — put
`IN` near the north wall and `OUT` near the south), and if the geometry still
will not close, a satellite mux room trades area and ticks for a smaller CPU
pipe count.

Port validation must run before every `lm.mjs` invocation — it is the
generator's primary safety net, and it catches the class of bug that is otherwise
invisible until a program silently reads the wrong pipe.

### 7.2 Heading is part of every port contract

A port is **(cell, heading)**, never just a cell. The §2.5 slice hung with no
output because the fetch `r` was entered heading south instead of east: correct
glyphs, correct pipes, and an infinite two-cell refetch loop. Assert entry
headings with the wasm's `flow(rows)` (per-cell reachable headings) as part of
generation, not as a debugging step.

### 7.3 Tick and footprint budget

| Stage | Ticks |
|---|---|
| fetch (`r↺`), bounded by ring throughput | ~6 |
| decode (depth-4 trie, incl. vertical travel 8+4+2+1) | ~15–20 |
| execute | ~3–10 |
| return to fetch | ~10–20 |
| **per instruction** | **~40–60** (×2 with an operand word) |
| per skipped word on a taken branch | ~8 |

The trie's vertical travel dominates decode, because a depth-4 tree spreads its
first branch 8 rows. A `d`/`m` ladder is the alternative: 3 ticks per rung, so
opcode 0 costs 3 and opcode 15 costs 48 — better than the trie *if* opcode
frequency is skewed and hot opcodes sit first. Both are generated from the same
table; pick empirically.

Footprint, for a 60-word program: ROM ≈ 300 cells (~20×15), ring ≈ 62 cells,
CPU ≈ 40×24, STORE stub small. Bounding box ~70×50 → `footprint ≈ 4900`, so a
250k-tick run scores ~1.2e9. Ugly, and expected (§1).

## 8. Coverage by semester

Semesters do not map onto capability tiers — the tiers cut across them. But
grouping by semester is still informative: **Semester 3 is uniformly "big RAM
plus nested loops"**, which is exactly where a CPU beats hand-drawing, while
Semester 1 spans the whole range by itself.

Emulator column = passes every public test case under `lm1/emulator.py`, with the
word count `P` and average estimated ticks. "—" means not yet written.

| Set | Problem | Needs | Slots | Emulator | Stage |
|---|---|---|---|---|---|
| Sem 1 | `triangle` | 1 spill slot; needs `DIVI`/`MUL` for the closed form | 1 | 6/6 · P=27 · 118k *(closed: P=11 · 407)* | **✔ solved bespoke** (24×3, 12 ticks, score 6912) |
| Sem 1 | `reverse-a-list` | LIFO, 16 deep | 16 | — | B |
| Sem 1 | `sort-numbers` | addressed array + selection loop | 16 | — | B |
| Sem 1 | `memory` | — | — | **SOLVED as a bespoke grid** (`programs/memory.man`, accepted) | ✔ |
| Sem 2 | `history-lesson` | no input; pure ROM dump (`footprint`-only) | 0 | 1/1 · P=8431 · 273k | **✔ solved bespoke** |
| Sem 2 | `brackets` | typed stack depth 32; needs `MODI`/`DIVI` | 32 | 9/9 · P=154 · 30k | B |
| Sem 2 | `tcp` | indexed by `seq`; needs `LDP`/`STP` | 48 | 6/6 · P=48 · 30k | B |
| Sem 2 | `plotter` | display ADDR/DATA/SWAP + line arithmetic | 8 | — | C |
| Sem 3 | `gradebook` | ids + N×K grades, search by id | 80 | — | C |
| Sem 3 | `matmul` | three matrices, ~8450 accesses | 768 | — | **✕** |
| Sem 3 | `subset-sum` | 20 values + subset search | 24 | — | C |
| Sem 3 | `sudoku-validity` | 81 cells + 27 set checks | 81 | — | C |
| Practice | `hello-world` · `max-element` · `atoi` | 0–1 slots | ≤1 | 1/1 · 10/10 · 2/2 | A |

Stages: **A** = CPU + SPILL only · **B** = SPILL + the tape · **C** = tape +
display ports.

Three findings that change the plan:

- **Spill is the dominant cost, and it must not touch the tape** (§4.1). This
  supersedes the earlier "one spill slot is mandatory" note: it is not one slot,
  it is 2–4 slots *on the fast tier*, and getting that wrong costs `triangle` the
  whole tick budget.
- **`brackets` and `tcp` need arrays too.** Neither was on the blocked list, but a
  32-deep typed stack and a 48-slot reorder buffer are arrays. They pass in the
  emulator only because it has `LDP`/`STP` (§6.1).
- **`matmul` looks infeasible.** ~8,450 accesses × ~750 ticks ≈ **6.3M against a
  5M cap** — and that is with the *constant-cost* tape, so the earlier delay-line
  estimate was pessimistic about the mechanism but right about the verdict. It
  needs banked memory or a bespoke solution.

`history-lesson` proved the point about scoring rather than capability: LM-1 can
emit it (P=8431, ~40k ROM cells), but on a `footprint`-only problem that is
hopeless, so it shipped as a **bespoke 144×148 grid** instead
(`tasks/solutions/history-lesson.man`, generated by `rom_snake.py`). Two problems
are now solved *without* the CPU — a reminder that LM-1 is the safety net, not
the plan of record.

## 9. Build order

1. ~~**ARCH.md**~~ — **done**, and revised twice since: §7.1 (ports are declared,
   not hand-solved) and §4.1/§5.1/§6.1 (the two-tier memory verdict).
2. ~~**Python ISA table + emulator + assembler**~~ — **done**
   (`lm1/`, 230 tests green). All 7 memory-free problems pass every public case.
   The findings it produced are folded into §4.1, §5.1, §5.3 and §6.1.
3. ~~**Vertical slice in `.man`**~~ — **done** (§2.5): ROM + ring + CPU with a
   2-opcode ISA emits `7 8 9` on the real wasm at 20 ticks/instruction. §7.1's
   geometry closes; headings turned out to be part of the port contract.
4. **Grow the slice** to `LDI`/`ADDI` + a real 2-bit trie + one `register-cell`
   as SPILL. This is the first increment that exercises operand words, the ring
   write-back invariant (§5.3) and a second block over a bus. ← *next*
5. **Full CPU generator**, driven by the ISA table (v1 + the §6.1 extensions).
   **First instance done** (§2.6): `lm1/cpugen.py` generates a complete 7-opcode
   CPU that runs `triangle` as a program — 6/6 public cases on the real
   interpreter. It is hand-rolled for that ISA rather than driven by `isa.py`;
   generalising it is what remains.
6. **Wire in the tape** and unlock the array problems.

Useful tooling that now exists for steps 4–6: `tools/route-check.mjs` reports
which pipe every pipe instruction actually resolves to (the §7.1 safety net as a
command), `tools/run-cases.mjs` scores a grid against a case file, and
`tools/trace.mjs` / `watch.mjs` step or watch for stalls.

## 10. Open questions

- **Trie vs `d`/`m` ladder** for decode — decide with measurements once the
  slice runs (§7.3).
- **ROM serpentine density** — reversed literals on alternating rows halve the
  ROM's height but add a whole class of load errors (§4.2). Ship the safe
  version first.
- **`Y` (split)** is unused so far. It is the only way to get a second man in
  one room, and a second man could run the ring's recirculation in parallel with
  execution — potentially cutting the 6-tick fetch. Worth a probe once the slice
  works.
- **Second "loop ring"** for hot bodies, if §5.4's jump cost dominates real
  programs.
