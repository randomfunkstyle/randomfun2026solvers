# LM-1 — a general-purpose computer written in littleman

**Status: design, not yet built.** This document freezes the architecture so the
assembler, the emulator and the `.man` generator can be built against one spec.

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
  than the §7.2 estimate, because this decoder is a single `X`; a depth-4 trie
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

### 4.1 STORE — memory, deliberately left abstract

Per decision: **no memory is baked into LM-1 v1.** `STORE` is a port contract
with a throwaway stub behind it, to be replaced later by someone else.

The wire protocol **is the `memory` problem's protocol**, verbatim:

| Request words (in) | Response words (out) |
|---|---|
| `0 addr` — READ | one word: the current value at `addr` |
| `1 addr value` — WRITE | none |

Ports: 1 in (`req`), 1 out (`resp`). Cells start at 0.

This is deliberate: **solving the `memory` problem produces the RAM block.** A
correct `memory` solution is a drop-in `STORE` with no protocol translation, and
LM-1 v1 wires in a tiny 8-slot stub of the same shape in the meantime. Anything
that needs real arrays (`sort-numbers`, `matmul`, `sudoku-validity`,
`subset-sum`, `gradebook`, `reverse-a-list`) is blocked on that block, and only
on that block.

A delay-line ring (N words circulating, addressed by counting them past a gate,
O(N) ticks per access) is the expected implementation, but LM-1 does not care.

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

ACC lives in `B` precisely because fetch destroys `A`. This costs a `W` or `M`
in most micro-programs and is much cheaper than spilling ACC to a pipe.

### 5.2 Word format

**No bit packing.** One integer per word; an instruction is an opcode word
followed by 0 or 1 operand words, as declared by the ISA table. Extracting a
packed operand would need `}` (which needs `B`, which holds ACC) — separate
words dodge that entirely, at the cost of a slightly longer ROM.

### 5.3 The PC is the ring's rotation phase

Words leave the ring in program order, so sequential execution needs no PC at
all: the next word *is* the next word. That makes the jump primitive:

```
JMPF n   — recirculate the next n words without executing them
```

The assembler resolves labels into `n`, so **source-level jumps are absolute**
while the hardware only ever skips forward. A backward jump to a target `L`
words back costs `n = P − L` skips.

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

## 7. What the generator has to get right

### 7.1 Nearest-pipe geometry is the hard part

`s` targets the nearest **outgoing** pipe and `r` the nearest **incoming** one —
Manhattan distance, ties by reading order, and *nearest*, not
nearest-that-can-proceed. The CPU room has up to 8 pipes, so **which pipe an
instruction talks to is decided by where the glyph sits.**

Discipline: bind each function to a wall and keep every site in that band.

| Wall | Pipes |
|---|---|
| west | ring-in, ring-out |
| east | STORE req / resp |
| north | input |
| south | output, display ports |

Micro-programs then shuttle between bands, paying travel ticks. Two useful
levers: **opcode numbering is a layout variable** (the trie's bit pattern fixes
which row each lane lands on, so put `IN` near the north wall and `OUT` near the
south), and if the geometry still will not close, a satellite mux room trades
area and ticks for a smaller CPU pipe count.

`layout.py` already validates that each pipe lands nearest its intended port —
that check is the generator's primary safety net, and it must run before every
`lm.mjs` invocation.

### 7.2 Tick and footprint budget

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

## 8. Build order

1. **ARCH.md** — this document. ← *you are here*
2. **Python ISA table + emulator + assembler**, and all task programs written
   and passing against the emulator. Proves the ISA is sufficient before any
   ASCII is drawn. *(delegated)*
3. ~~**Vertical slice in `.man`**~~ — **done** (§2.5): ROM + ring + CPU with a
   2-opcode ISA emits `7 8 9` on the real wasm at 20 ticks/instruction. The
   §7.1 nearest-pipe geometry closes, and headings turned out to be part of the
   port contract. Next increment: add `LDI`/`ADDI` and a real 2-bit trie so the
   slice exercises operand words and the ring write-back invariant (§5.3).
4. **Full 16-lane CPU generator**, driven by the ISA table.
5. **Drop in a real `STORE`** (i.e. the `memory` solution) and unlock the array
   problems.

## 9. Open questions

- **Trie vs `d`/`m` ladder** for decode — decide with measurements once the
  slice runs (§7.2).
- **ROM serpentine density** — reversed literals on alternating rows halve the
  ROM's height but add a whole class of load errors (§4.2). Ship the safe
  version first.
- **`Y` (split)** is unused so far. It is the only way to get a second man in
  one room, and a second man could run the ring's recirculation in parallel with
  execution — potentially cutting the 6-tick fetch. Worth a probe once the slice
  works.
- **Second "loop ring"** for hot bodies, if §5.4's jump cost dominates real
  programs.
