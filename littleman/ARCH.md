# LM-1 — a general-purpose computer written in littleman

**Status: the synthesiser solves the problems that need memory and control flow.**
`lm1/machine.py` takes an assembled program and emits a whole machine — looping
ROM + CPU (depth-`k` trie, one lane per used opcode, a structures band for jumps
and branches) + a request adapter + the 32×32 tape + I/O. On the reference
interpreter it passes **`brackets` 9/9** (98×95, 62,930 avg ticks) and **`tcp`
6/6** (112×78, 96,923 avg ticks), plus hand-built cases at both problems'
constraint limits — `brackets` at depth-32 nesting and n=64, `tcp` at n=48 with a
worst-case legal scramble (351k ticks against a 5M cap). §2.7 has the findings.

That makes **four** problems solved by generated or bespoke grids, and the first
two that need addressed memory *and* loops. `lm1/cpugen.py` remains the earlier
hand-rolled 7-opcode instance (`triangle`, §2.6); `lm1/synth.py` is the
straight-line-only generator it grew into. `machine.py` supersedes both.

Two problems are solved *without* LM-1, as bespoke grids: `memory` (accepted by
the judge) and `history-lesson`. LM-1 is the safety net for the rest, not the plan
of record (§1).

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

**A ring always needs two rooms — a self-loop pipe does not exist.** SPEC lists
"a pipe looping back to its own room" among the common load errors; what the
probe adds is *how* it fails. Two grids of identical geometry, differing only in
which room the far end re-enters:

```
A -> A  (ignored)          A -> B  (a pipe)
+------+                   +------+
|@1s  r|<-<                |  s  r|>-v
|      |  |                +------+  |
|  s  r|>-^                +------+  |
+------+                   |@r    |<-<
rooms=1 pipes=0            rooms=2 pipes=1  src=0 dst=1
```

Same glyphs, same arrows, same wall attachments. `analyze` reports the A→B pipe
and **silently drops** the A→A one — it surfaces no diagnostic at all, so the
grid looks fine and a `s`/`r` aimed at it simply binds to nothing (or to some
other pipe entirely). So every ring costs a turnaround room.

**But one room can turn around many rings.** Pipe binding is positional
(`two-roms.man`: `r` at col 11 → the west pipe, `r` at col 25 → the east one, no
branch and no decode), so a single relay may hold N `r`/`s` pairs, one per ring.
This is safe *only* when the rings are permanently full — a data ring holding its
payload never empties, so the relay's `r` never blocks. A relay serving rings
that can drain will deadlock on the first empty one.

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
38×31, **442 ticks**, score **638,248** after packing (§7.4 — it began at 46×31
/ 943,736). Every pipe instruction was confirmed against
`tools/route-check.mjs` to resolve to its intended pipe.

**This build has no code ring at all** — the ROM man walks a *closed loop* and
re-emits the program forever, so the fetch is `>rbr` with no write-back (§5.3).
Unlike the earlier straight-line-only version, this is correct for **looping
programs too**: a backward jump still discards `n` words, and the ROM keeps
supplying them. Confirmed at t=20,000 with no error — once the CPU halts the ROM
man simply blocks on a full pipe, which is the harmless steady state.

- The emulator predicted 407 ticks and the hardware measured 446, so **§7.3's
  tick model is good to ~10 %** — it can be trusted for planning.
- Spill is one `register-cell` on the east bus, which is §4.1's two-tier rule in
  practice: `triangle` needs exactly one live value besides ACC, because `A` dies
  on every fetch.
- Opcode numbering was chosen so the lanes land next to the walls they need
  (§2.4's bit-reversal): `IN` = 0 → top row, beside the north input pipe;
  `OUT` = 7 → bottom row, beside the south output pipe.

**The bespoke grid for the same problem is 24×3, 12 ticks, score 6,912 — 92×
better.** Both pass 6/6. That is the §1 trade, measured rather than assumed: the
CPU costs two orders of magnitude and buys the ability to solve a problem by
writing seven lines instead of deriving a grid.

### 2.7 The synthesiser, with memory and control flow

`lm1/machine.py` is §7.5's per-program synthesiser carried out far enough to run
the graded problems. Measured on the reference interpreter:

| | `brackets` | `tcp` |
|---|---|---|
| public cases | **9/9** | **6/6** |
| size | 98×95 | 112×78 |
| avg ticks | 62,930 | 96,923 |
| score | 604,383,988 | 1,215,797,931 |
| opcodes used → trie depth | 15 → 4 | 14 → 4 |
| program | P=154 words, 33 ROM rows | P=45 words, 8 ROM rows |
| tape | N=8 | N=52 |

Also verified at the problems' *constraint* limits, which the public data does not
reach: `brackets` at depth-32 nesting and every n=64 shape, `tcp` at n=48 with a
worst-case legal scramble (351k ticks against the 5M cap). Tape size is picked
from the constraints, not the public cases — `tcp` needs N=52 because n=48 puts
the top slot at BUF+47, though no public case goes past 35.

**The emulator's tick model held.** Adding §4.1's real tape latency
(`105 + 8.3N` per access) to the emulator's estimate predicted 55,005 and 114,794
ticks; hardware measured 62,930 and 96,923 — within ~15 % in both directions. §7.3
can be trusted for planning, *provided* store accesses are billed at tape cost
rather than the emulator's flat 6 ticks per word, which is out by a factor of ~30.

Four findings, each of which cost a debugging cycle and none of which is visible
in the ASCII:

- **The fetch is fixed-width, so the ROM image must be too.** The assembler emits
  ARCH's abstract *variable*-width form — one word for a zero-operand opcode — and
  `>rbr` unconditionally takes two. `LDI 42 / OUT / HALT` then pairs up as
  `(LDI, 42), (OUT, HALT)` and emits 42 forever without ever halting. The
  generator pads to two words **and rescales every skip count**, since the
  assembler resolved them in variable-width positions: instruction *k* lives at
  word 2*k*, so a jump to *t* discards `2·((t − k − 1) mod n)` words.
- **A drop column may only be `v` at its head.** Filling it with `v` all the way
  down looks harmless and is not: where it crosses a structures-band slab's
  westbound entry row it turns that man *south*, into the middle of the drop.
  `.` is the only glyph that lets a southbound and a westbound man cross the same
  cell, since each keeps his heading.
- **Nearest-pipe ties are real, and lose silently.** A branch's discard-loop `r`
  landed exactly 28 cells from both the ROM pipe (west) and the tape's response
  pipe (east); the engine broke the tie by reading order, so every taken jump
  blocked forever on an empty pipe. The loop now runs back to the slab's west edge
  before reading. §7.1's binding assertion is worth running on *every* pipe glyph,
  including the ones inside generated sub-structures — that one was missed because
  only lane glyphs were registered.
- **A pipe leg alongside a room's corner becomes a second pipe.** §7.4b's "a pipe
  may attach at a room's corner" cuts both ways: the response pipe's final
  westward leg ran along the adapter's top-wall row, one cell west of its corner,
  and the engine read those two cells as an extra adapter→CPU pipe. The CPU's
  memory `r` bound to it and read the adapter's op words instead of the tape's
  answers. Counting the pipes the engine finds against the number drawn catches
  the whole family in one line.

**The `0`/`1` request literal is why memory needed a new idea.** The STORE
protocol wants an opcode word first, but fixed-width instructions mean `A` already
holds the operand when a lane starts, and the literal destroys it — §6.1's hole in
`STP`, now hit by plain `ST` as well. The fix is to put the operation **in the
sign of a single request word** (`+a` read, `−a` write): no literal, no spill slot,
no lane reading the ring. A 13×4 adapter room expands that back into the tape's
real protocol, so the verified tape is untouched. Address 0 is sign-ambiguous, so
hardware addresses start at 1.

The same trick retires `LDP`/`STP`: `LDA` (`ACC = store[ACC]`) and `MOVA`
(`store[ACC] = store[addr]`, source read *first*) keep the address in ACC and never
need a pointer and a value live together. **`tcp` therefore needs no SPILL block at
all** — one fewer room, two fewer pipes, and every memory lane stays on the east
bus, which is what keeps §7.1 tractable.

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

**Invariant (ring only) — every ring read is immediately followed by a ring
write-back.** Opcode words *and* operand words *and* skipped words all go back
into the ring, or the program erases itself on the first lap. A pipe read is
destructive, so this is the price of using pipes as the program store.

#### The looping ROM makes the ring — and the write-back — unnecessary

The ring has exactly one job: present the program's words to the CPU over and
over, in order, forever. It does that by *storing* them. A **ROM man walking a
closed loop** does the same thing by *regenerating* them, and the CPU cannot tell
the difference — same order, same "PC = phase" property, same jump mechanism
(discard `n` words).

Verified standalone — emits `9 7 8 9 7 8 …` indefinitely at 4 ticks/word:

```
+------+  +-+
|>7s8sv|>>|O|
|^..s9<|  +-+
|@....^|
+------+
```

| | code ring | looping ROM |
|---|---|---|
| rooms | ROM + `LOOP` | ROM |
| pipe capacity | **≥ `P`** or deadlock | none — words are regenerated |
| fetch | `>rsbrsx` | **`>rbr`, for every program** |
| throughput | 6 ticks/word | 4 here; ~2.3 for a 14-word loop |
| packing | constrained by minimum pipe length | unconstrained |

The looping ROM wins on every row, so **it is the design of record** (§2.6). Two
consequences worth stating plainly:

- The §7.4 packing obstacle "the ring needs a *minimum* pipe length" **is gone**.
  Only the `max(w,h)²` objective remains before `layout_graph` can place rooms.
- The write-back invariant above applies only if you choose the ring. The one
  thing the ring still buys is *mutable* program words — self-modifying code, or
  spill slots living in the ring. LM-1 uses neither (spill is the register cell),
  so nothing is given up.

Generator constraint: the spawn path must join the loop immediately before word
0, or execution starts mid-program. The first probe above emitted `9 7 8` rather
than `7 8 9` for exactly that reason — `lm1/cpugen.py` puts words 0..6 on the
westbound row and joins at its head.

### 5.4 Jump cost, and the rule that follows from it

Skipping a word costs ~8 ticks (a 4-op cycle: `r`, `s`, `m`, `d`). A backward
jump in a `P = 60` program is therefore ~480 ticks — the machine's dominant
cost.

The mitigation falls out of the cost model: **the skip is `P − L`, so make the
hot loop most of the program.** A program that is one tight loop pays almost
nothing per iteration; a tight loop buried in a long program pays for the whole
rotation. Keep `P` small and put loops last. **The better fix is §5.5** — give
each block its own looping ROM, and branching stops costing word discards
entirely.

### 5.5 Code banks — one looping ROM per subprogram

A program does not have to live in **one** ROM. Give each subprogram its own
looping ROM with its own pipe into the CPU, and "call subprogram *k*" becomes
"route the man to fetch-site *k*".

Verified in `programs/two-roms.man` — two looping ROMs feeding one room, one
emitting `2,1` and the other `8,7`; the consumer reads two words at its west end
and two at its east end and emits `2 1 8 7` repeating indefinitely:

```
+----+   +--------------------+   +----+
|>1sv|>->|>rsrs..........rsrsv|<-<|>7sv|
|^s2<|   |^..................<|   |^s8<|
|@..^|   |@..................^|   |@..^|
+----+   +--------------------+   +----+
```

Two properties, the second not obvious in advance:

- **Which ROM a fetch reads from is decided purely by where the `r` glyph sits**
  (§7.1's nearest-pipe rule). No opcode, no multiplexer, no bank register.
- **Alignment survives switching.** Every visit to a bank starts at its word 0 —
  *provided the call consumes exactly one whole lap of that ROM*. Single entry,
  single exit, whole body.

That changes the cost of control flow:

| | one ROM | one ROM per subprogram |
|---|---|---|
| call / branch | discard `n` words — **O(P) ticks** | route the man to fetch-site `k` — **a turn, ~free** |
| shared code | duplicated, or reached by a jump | stored **once** |
| lap latency | the whole program | just that block |

This is the real fix for §5.4's jump cost, which the emulator measured at 13–28 %
of total ticks — worst on `brackets`, whose dispatch chain forces jumps across a
154-word ring. It also cuts ROM footprint whenever a routine is used twice.

The resulting shape: **instruction supply stays ROM-driven and compact, while
branching between blocks becomes geometric** — a turn inside the CPU room. That
is the hybrid of the two control-flow options weighed at the start of this
document, and it beats either alone.

Limits to design around:

- **No return address.** "Call" is really "switch fetch source", and the return
  point is wherever the man's path goes next — so these are *inlined call sites*,
  not a call stack. Recursion needs extra machinery (a return-address cell and a
  dispatch trie on it).
- **Cost per bank** is a room, a pipe and a fetch site, and the nearest-pipe
  geometry tightens as the count grows: banks need spreading along different
  walls, or the fetch sites clustered so each is unambiguously nearest its own.
- **Partial laps desynchronise a bank.** Any path that abandons a bank mid-body
  leaves it misaligned for the next call. If a block can exit early, it must
  still drain its remaining words.

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

### 7.4b Pipe length is a tick cost, on both sides

Measured on `triangle` (9×9, same 11 glyphs, only the pipe lengths varied):

| input pipe | output pipe | ticks |
|---|---|---|
| 2 | 2 | **13** |
| 4 | 2 | 15 |
| 4 | 4 | 17 |

**Every extra pipe cell costs one tick.** The output side is obvious — a value
takes one tick per cell to reach the end of the output pipe, and ticks are counted
until the last correct output *arrives*, not until the man finishes. The input
side is less obvious: input is free only if the `r` fires after the value has
already traversed. In a short program `r` runs at t1 and *blocks*, so the input
pipe's length lands on the critical path too.

Consequences for layout:

- **Both pipes want to be the 2-cell minimum.** This directly opposes §7.4's
  packing pressure, because wrapping a pipe around a room to save a row makes it
  longer.
- Everything *after* `s` is genuinely free — the trailing `H` costs nothing. Keep
  it anyway: an error before the output pipe drains loses the value in flight.
- A long input pipe is free *only* for programs that do real work before their
  first `r`, which is rare.

**But wrapping does not have to lengthen a pipe** — and that is how `triangle`
reached 8×8. Shift the compute room one column right and the freed left edge
carries the input pipe *up the outside* at the 2-cell minimum, while the output
pipe drops down the right edge, also 2 cells. Both I/O rooms then abut below with
no gap column:

```
 +-----+
 |@rM*v|
 |v2M+<|
>|>W/sH|
^+-----+
+-++-+v
|I||O|<
+-++-+
```

8×8 = 64 footprint, 15 ticks, **score 960** — beating the stacked 9×9's 1,053
despite two extra ticks, because a 3-row serpentine costs 4 turn cells instead of
2. Verified 6/6 public cases plus n=0,1,2,999,1000, both pipes 2 cells, both ops
binding as intended.

Two structural facts this depends on, neither of them obvious and both now
verified:

- **A pipe may attach at a room's corner.** The input pipe's backward cell is I's
  top-left `+`. Displays forbid corner attachment; plain rooms do not. Without
  this the pipe has nowhere to go, since I's top wall is flush against the compute
  room's bottom-left corner.
- **Two rooms may abut with no gap column** (`+-++-+`). I and O share a boundary,
  which is what fits 3+3 rooms into an 8-wide box.

7×7 is out of reach: 4 interior columns × 3 rows leaves only 9 instruction cells
after `@` and 4 turns, and 4 interior rows costs 6 turns and pushes the height to
9.

### 7.4 Packing: why `layout.py` cannot own this unaided

Footprint is `max(w, h)²`, so **only the larger dimension is billed** and slack in
the smaller one is free. The first working CPU (§2.6) was 46×31 — width-bound,
with 15 rows of height doing nothing. Hand-trading width into that slack (fold
the ROM onto two rows, serpentine the ring pipes vertically in a narrow west
band) reached 40×31, and dropping the ring reached 38×31: `2116 → 1600 → 1444`,
a 32 % score improvement with no change to the logic.

None of that went through `layout_graph`. Two reasons it cannot simply be handed
over:

- **The code ring needs a *minimum* pipe length** (§2.1: capacity ≥ `P` or the
  machine deadlocks). Every router optimises for *short* pipes, so a naive
  packing pass silently breaks a looping program. Those two edges need a
  `min_length` constraint the model does not currently express.
- **The objective is `max(w, h)²`, not area or wire length.** Packing should
  deliberately grow the smaller dimension to shrink the larger — the opposite of
  what a conventional placer does. `score()` is the hook, but it has to encode
  this, or packing will chase the wrong target.

Until both are expressed, the generator places rooms itself and uses
`layout.py`'s port validation (§7.1) and `tools/route-check.mjs` as the safety
net rather than as the placer.

### 7.5 Synthesise a per-program CPU, not one universal CPU

**Decision: the generator takes a program and emits the smallest machine that
runs it.** There is no "the LM-1 CPU" — there is a CPU *synthesiser*, and each
task gets its own instance. The §2.6 build is already one (7 opcodes chosen for
`triangle`); the work is to parameterise it.

This is not a micro-optimisation. The decode trie's depth is
`ceil(log2(#opcodes used))` and its leaves spread geometrically, so the opcode
count sets the CPU room's height:

| opcodes used | depth | lanes | CPU rows |
|---|---|---|---|
| 7 (`triangle`) | 3 | 8 | ~19 |
| 16 (ISA v1) | 4 | 16 | ~27 |
| 24 (v1 + §6.1) | 5 | 32 | ~35 |

Footprint is squared, so synthesising for 7 opcodes instead of 24 is most of the
score. The same argument applies to every block: instantiate a `register-cell`
per spill slot actually used, size the tape to the slots actually addressed
(§4.1's `105 + 8.3N`), and omit the display ports, the `O` room or the `I` room
when the program never touches them.

The pipeline:

1. **Assemble** the `.asm` → word list `P`, plus the *set* of opcodes it uses.
2. **Number the opcodes.** `k = ceil(log2 |used|)`. The trie sorts leaves
   bit-reversed (§2.4), so the number *chooses the row*: put `IN` near the north
   wall, `OUT` near the south, memory ops near the east. This is the step where
   ISA-as-data pays off — numbering is an output of layout, not a constant.
3. **Emit the trie** at depth `k`, and **only the used lanes**.
4. **Instantiate only the blocks needed**, sized per §4.1.
5. **ROM**: looping (§5.3), folded onto enough rows to balance `w` against `h`.
6. **Pack** for `max(w, h)²` (§7.4), growing the smaller dimension deliberately.
7. **Verify**: `tools/route-check.mjs` for port resolution, then
   `tools/run-cases.mjs` against the problem's public data.

Per task, the only bespoke artefacts are then **the `.asm` program and its block
configuration** — data, not code.

Two known hard parts: step 2 ↔ step 6 are coupled (numbering fixes lane rows,
which fixes where pipes must attach, which constrains packing), and step 6 needs
the `max(w,h)²` objective taught to `score()` (§7.4 — the other blocker there,
minimum ring length, is already gone since §5.3).

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
| Sem 2 | `brackets` | typed stack depth 32; needs `MODI`/`DIVI` | 6 scalars | 9/9 · P=154 · 30k | **✔ solved by the synthesiser** (98×95, 62,930 ticks, score 604M) |
| Sem 2 | `tcp` | indexed by `seq`; needs `LDA`/`MOVA` | 51 | 6/6 · P=45 · 30k | **✔ solved by the synthesiser** (112×78, 96,923 ticks, score 1.22bn) |
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
- **`brackets` and `tcp` need arrays too — and both are now solved** (§2.7).
  `brackets` turned out not to need indexing at all: its base-3 packed stack is six
  scalars, so an N=8 tape serves it. `tcp`'s 48-slot reorder buffer does need
  indexing, but through `LDA`/`MOVA` rather than `LDP`/`STP`, so it needs no SPILL
  block either.
- **`matmul` looks infeasible.** ~8,450 accesses × ~750 ticks ≈ **6.3M against a
  5M cap** — and that is with the *constant-cost* tape, so the earlier delay-line
  estimate was pessimistic about the mechanism but right about the verdict. It
  needs banked memory or a bespoke solution.

`history-lesson` proved the point about scoring rather than capability: LM-1 can
emit it (P=8431, ~40k ROM cells), but on a `footprint`-only problem that is
hopeless, so it shipped as a **bespoke 144×148 grid** instead
(`tasks/solutions/history-lesson_rom.man`, generated by `rom_snake.py`). Two problems
are now solved *without* the CPU — a reminder that LM-1 is the safety net, not
the plan of record.

## 9. Build order

1. ~~**ARCH.md**~~ — **done**, and revised twice since: §7.1 (ports are declared,
   not hand-solved) and §4.1/§5.1/§6.1 (the two-tier memory verdict).
2. ~~**Python ISA table + emulator + assembler**~~ — **done**
   (`lm1/`, and 294 tests green across the package). All 7 memory-free problems pass
   every public case.
   The findings it produced are folded into §4.1, §5.1, §5.3 and §6.1.
3. ~~**Vertical slice in `.man`**~~ — **done** (§2.5): ROM + ring + CPU with a
   2-opcode ISA emits `7 8 9` on the real wasm at 20 ticks/instruction. §7.1's
   geometry closes; headings turned out to be part of the port contract.
4. ~~**Grow the slice**~~ — **done**, and then some: `lm1/machine.py` runs
   arbitrary operand words, a depth-4 trie, control flow and the tape.
5. ~~**Per-program CPU synthesiser**~~ (§7.5) — **done** for the opcode set and
   the block list: `lm1/machine.py` is driven by `isa.py`, emits a depth-`k` trie
   with only the used lanes, and sizes the ROM and the tape per program (§2.7).
   *Packing* is still hand-shaped — see §7.4 and the note below.
6. ~~**Wire in the tape**~~ — **done** (§2.7), via the sign-biased request word and
   a small adapter room, leaving the verified tape untouched.
7. **Pack the generated machine.** Both builds are footprint-dominated and neither
   dimension is being traded against the other: `brackets` is 98×95 (9,604) where
   the CPU is 33 wide and the ROM 67, and `tcp` is 112×78 (12,544) where the tape
   band sets the width. §7.4's `max(w,h)²` objective is the lever, and it is now
   the *only* thing between these scores and roughly a 3–4× improvement. ← *next*
8. **Reuse the machinery for the remaining array problems** — `sort-numbers`,
   `reverse-a-list`, `subset-sum`, `gradebook`, `sudoku-validity`. Each is now an
   `.asm` file plus a tape size; `matmul` is still out of reach on ticks (§8).

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
