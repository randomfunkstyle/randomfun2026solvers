# LM-1 — a general-purpose computer written in littleman

**Status: Semesters 1 and 2 are complete — 11 of the 16 graded problems are
solved**, every one passing all its public cases on the reference interpreter, and
`snake` opens Semester 4 by passing all **17** the judge actually runs. It is also the
first problem solved on a *third* tier — a coprocessor that holds the data structure
the program iterates and owns the display (§8.0, `lm1/snake_unit.py`), which took it
from 15.9bn to **3.37bn**.

| set | problem | grid | score | how |
|---|---|---|---|---|
| 1 | `triangle` | 8×8 | **960** | bespoke |
| 1 | `memory` | 31×31 | 55,105,622 server | bespoke (pipe tape) |
| 1 | `reverse-a-list` | 21×21 | 482,564 | bespoke (value ring) |
| 1 | `sort-numbers` | 25×25 | 2,083,304 | bespoke (value ring) |
| 2 | `history-lesson` | 97×90 | **9,409** | bespoke (base-128 ROM) |
| 2 | `brackets` | 98×75 | 308,880,647 | LM-1 |
| 2 | `tcp` | 112×77 | 1,195,367,936 | LM-1 |
| 2 | `plotter` | 112×116 | 2,749,462,237 | LM-1 + display |
| 3 | `sudoku-validity` | 98×91 | 12,712,904,437 | LM-1 |
| 3 | `gradebook` | 112×103 | 8,714,479,872 | LM-1 |
| 4 | `snake` | 121×136 | **3,369,020,288** | LM-1 + body-ring coprocessor (17/17, judged) |
| 4 | `pathfinder` | 180×184 | — (17/18, unscored) | LM-1 + display, bit-parallel BFS (§8.3) |
| — | `palette` | 98×98 | 1,451,615,788 | LM-1 + display (ungraded) |

`lm1/machine.py` takes an assembled program and emits the whole machine — looping
ROM + CPU (depth-`k` trie, one lane per used opcode, a structures band for jumps
and branches) + a request adapter + the tape + I/O, or an LM-75 panel instead of
an `O` room for the display problems. `lm1/cpugen.py` remains the earlier
hand-rolled 7-opcode instance (`triangle`, §2.6); `lm1/synth.py` is the
straight-line-only generator it grew into. `machine.py` supersedes both.

The bespoke grids beat their LM-1 equivalents by orders of magnitude —
`triangle` is 960 by hand against 471,744 generated — so LM-1 remains the safety
net rather than the plan of record (§1). `matmul` is now solved by adding the
rotate-only STREAM tier (§8 and `lm1/stream.py`); **`subset-sum`** remains open.

Visual walkthrough: [`arch.html`](arch.html) — the verified units, an animated
run of the whole machine driven by real interpreter snapshots, the tick budget
and the semester matrix.

Companion docs: [`SPEC.md`](SPEC.md) (the language) ·
[`GRADING.md`](GRADING.md) (scoring) · [`../tasks/problems/`](../tasks/problems/)
(the 20 problems).

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

### 2.9 Profiling: where the ticks actually go

`tools/heatmap.mjs` samples every runner's cell as the engine steps, and
`lm1/profile.py` attributes those cells to the regions `machine.py` records at
generation time (`Machine.regions`, rendered through the memory worker's
`man_debug` overlays). Two things had to be right before the numbers meant
anything:

- **Split by runner.** A *servant* blocked on its input — the adapter waiting for
  a request, the tape waiting for the adapter, the relay waiting for the ring — is
  **idle**, not a bottleneck, and pooling it with the CPU inverts the picture. The
  first run pooled them and pointed at the adapter's `r` as 19 % of all time; it
  is simply idle 89 % of its life. The tool now reports a stall fraction per
  runner and treats the least-stalled man as the critical path.
- **Name the cells.** A generated grid has ~10k cells and carries no comments, so a
  list of hot coordinates is unreadable.

`plotter`, sampled over one round (300k ticks), before any change:

| bucket | share | note |
|---|---|---|
| lanes | 47 % | but ~80 % of each *memory* lane is its blocked `r` |
| **return path** | **25 %** | pure walking — no work at all |
| slabs | 7.7 % | |
| trie | 6.1 % | |
| fetch | 1.4 % | |

**The return path was the first fix, and it was a placement mistake, not a
necessity.** Every instruction walks from the collector row up a riser to the
fetch row, and the collector had been placed *below* the structures band — so the
riser was 38 cells where the lane band alone needs 16. Moving the collector
directly under the lane band and making slab exits **rise** into it instead of
dropping past it took the return path to 17.9 % and bought:

| | before | after | |
|---|---|---|---|
| `brackets` | 62,930 | **55,986** | −11.0 % |
| `plotter` | 482,933 | **421,917** | −12.6 % |
| `tcp` | 96,923 | **92,383** | −4.7 % |
| `gradebook` | 694,713 | **681,441** | −1.9 % |

Two glyph rules fell out of it, both of which broke a program first:

- **Only a turn cell may be an arrow; the body of a drop or riser must be `.`.**
  A riser now crosses shallower slabs' westbound entry rows, and a `^` there sends
  that man north. `.` is the only glyph two men crossing one cell in different
  directions both survive.
- **A branch's loop exit cannot rise at `base + 2`** — that is exactly the column
  where each arm parks its `W`, so the returning man walked through a register
  swap. It rises at `base + 11`, east of every arm.

**Still on the table, measured.** The next two, with the numbers that justify them:

1. **The CPU is 13–18 columns wider than it needs to be**, purely so a memory `r`
   binds to the tape's response pipe (east) rather than the ROM pipe (west) — that
   is what `mem_pad` is. The width is paid *twice per instruction* (east to the
   drop column, west along the collector). Moving the response pipe to the **north**
   wall, directly above the memory lanes, would let `mem_pad` be 0: it lengthens
   one pipe (a per-*read* cost) to shorten every instruction's walk (a per-
   *instruction* cost), and instructions outnumber reads ~2.7:1, so break-even is
   ~70 extra pipe cells against ~26 saved per instruction.
2. **Response-pipe latency is 5–9 %.** The pipe is 47–49 cells and a read is
   strictly serial — `s` then `r`, one outstanding request — so the whole length is
   paid on every read (§7.4b). It is that long only because the adapter sits
   between the CPU and the tape, forcing a detour over the top.

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

**The rule holds but this formula understates it badly, and the mechanism is the
ring's phase.** Measured end-to-end on generated machines, one extra slot costs
**~999 ticks per case on `tcp`** and ~114 on `brackets` (`tcp` at 52/70/90 slots:
99,456 / 116,894 / 137,410) — an order of magnitude above `8.3·N`, because a
request does not pay a fixed latency, it *waits for its slot to come round*. Two
consequences the formula hides:

- **A tape-bound program's instruction count barely matters.** A `tcp` rewrite
  that removed 158 executed instructions with an **identical** memory access
  sequence scored **14.8% worse** (1,215,797,931 → 1,395,950,677), and a
  byte-identical grid reached by a different route cost 2.6% more ticks. Removing
  work re-times requests onto worse ring phases.
- So **any tape-bound program must be judged on the engine, never modelled.** The
  emulator's flat 6 ticks per store word and §7.3's budget are both fine for
  comparing compute-bound programs and useless here. `brackets` is
  instruction-bound (fetch+decode+return is 41% of its ticks) and `tcp` is
  tape-bound; the same change can help one and hurt the other.

Note the ~105-tick fixed overhead per operation (read op, read addr, arithmetic,
dispatch) — it does not amortise away, which is why even an N=4 tape costs 138
ticks and loses to a `register-cell` by 7× for scratch. The tiers are distinct
mechanisms, not two sizes of the same one.

#### A read costs 523 ticks and a write costs 19 — the asymmetry is the whole game

Measured per *cell* on `snake` (`tools/heatmap.mjs` + `lm1/profile.py`, gated so the
run ends at the scored tick), and it re-prices every program on this page:

| unit | cost | how it was measured |
|---|---|---|
| one tape **read** | **523 ticks** (512–637 across five cases) | 100.0% of the tape's marginal cost lands on one cell — the mem-response `r` in the CPU's memory lanes. Rebuilding at N+16 moved *only* that cell. |
| one tape **write** | **~19 ticks** | the whole `ST` lane is 5,375 ticks for 276 writes: a write is fire-and-forget, the CPU never waits for it |
| one **instruction** | **162 ticks** | fetch + trie + lane walk + return, summed over the CPU's own regions |
| one **taken branch** | **155 + 5.88 per discarded word** | solved from the JMPF and BRZ slabs (253 jumps/45,325 words and 161/8,672) |
| one tape **slot** | **8.06 ticks per read** | N=66 → 90 on a fixed program: 2,169,980 → 2,617,836 ticks |

Four consequences, all of them design rules rather than trivia:

- **Spend writes freely and hunt reads.** `ST` is nearly free, so parking a value in a
  scratch slot costs nothing and re-reading it costs 523. Prefer any encoding that
  reads once and writes twice over one that reads twice.
- **A read-modify-write opcode is cheap and a re-read is not** — which is what makes
  the `INCM`/`DECM` family (§6.1) worth its lanes: `DECM n` is one read where
  `LD n; SUBI 1; ST n` is two.
- **Each word of `P` costs ~2,433 ticks per case** on a program taking ~414 branches,
  so ROM size is a tick cost and not only an area cost. And the skip is `P − L`
  wherever the loop sits — the ring is circular, so "put the hot loop last" is really
  "make the loop body long and `P` short".
- **Sizing the tape is worth multiples, not percent, once a structure is in it.** A
  50-cell array taxes every unrelated scalar read by 8.06 × 50 = 403 ticks. That is the
  argument for the STREAM tier below, and for `snake` it is the difference between a
  523-tick read and a ~196-tick one.

Two traps in the profiler itself, both hit on `snake`:

- `profile.py`'s `critical_runner()` takes the **least-stalled** runner, and on a
  machine whose tape ring never stops walking that is the *tape's* man — reported as
  `tape 100%` with an all-zero rollup. Pin the CPU's runner by hand.
- `heatmap.mjs` passes no expected frames, so **input is never gated** and the CPU
  parks on the `IN` lane's `r` forever once the input runs out. At `--cap 3000000` that
  fabricated 720,069 ticks — 24% of the profile — as a lane that does no work in the
  scored run. Gate the run, or cap it at the final commit's tick.

**In a generated machine that fixed part is ~316 ticks, not ~105, and it dwarfs the
slope.** Solving for per-unit costs against `plotter`'s engine total (§8.1) gives
~316 ticks per access at N=11 while rebuilding the *same* program at N=11/21/31 moves
it by only ~1.9 ticks per access per slot. So the two figures above answer different
questions and both are needed: `8.3·N`-style slope arguments decide **how big** to make
a tape, and the ~316 fixed cost decides **how often to touch it**. Sizing is worth a
few percent; access count is worth multiples. The planning rule that falls out —
**one tape access ≈ 7 instructions ≈ 40 recirculated ROM words** — is what turned
`plotter` from 6 % over the step cap to 61 % under it.

Further variants, in rough order of payoff (the first two are described in
`programs/README.md` and not yet built):

- **Relative rotation** instead of a full revolution — rotate `(addr − next) mod
  N` and keep the index in a `register-cell`: ~2.6× on the slope.
- **A larger pass-through loop ring** — the 2×4 loop costs 8 ticks/value, but a
  6×6 hollow square amortises the fixed corner/test/decrement cost over 7 values
  in flight, reaching ~2.9: another ~2.2×.
- **Banking** — k rings, address = (bank, offset), ~N/k per access at k× the
  pipe area. Superseded for `matmul` by its sequential-access STREAM block, but
  still relevant to random-access workloads.
- **`register-cell` arrays** — for a handful of named scalars, k cells beat any
  tape outright.

`layout.py`'s `Container.variants` is the mechanism for handing the solver
several of these and letting it pick one that routes.

#### Two 50-cell banks with `Y`

The concrete two-bank design no longer needs an input broadcaster or a join.
The operation reader consumes `op, addr`, keeps `op` in B and `addr` in A/BP,
then splits once:

```
                         Y
                       /   \
low child:  addr      /     \  high child: addr - 50
            ring[0]  /       \ ring[1]
```

Both children execute the same full-lap 50-cell algorithm concurrently. The
low child is active only when `addr - 50 < 0`; the high child is active only
when `addr - 50 >= 0`. Exactly one active child performs the target side effect.
For a WRITE, that child alone receives the value token *after* selection; for a
READ, it alone sends to output. The inactive child makes a no-op revolution, so
both rings return to cell zero. One designated child walks back to the input
loop after a padded fixed-length path and the other halts—there is no join.

This fits the three registers. Before `Y`, A/BP hold `addr` and B holds `op`.
Each child first branches on `op`; once that branch is known, the short sequence
`WM` `` `50` `` `W-` replaces the no-longer-needed op with the bank predicate
without losing the address. The high worker uses the result directly; the low
worker adds 50 back on its active path.

The model and the executable selector are checked in
`memory_ast.banked_spec`, `memory_banked.build_bucket_probe`, and
`tests/test_memory_banked.py`. The probe runs all 100 addresses on the native
validator and the four cut boundaries on the reference wasm. It proves actual
`Y` birth directions and mutually exclusive output, not merely integer
division in Python.

The budget is unusually favorable:

| property | one ring | two banks |
|---|---:|---:|
| values per critical-path revolution | 100 | 50 |
| modeled rotation ticks | 800 | 400 |
| minimum total pipe cells | 101 | 102 |
| independent relay loops | 1 | 2 |

Storage capacity therefore barely grows—the extra cost is duplicated relay and
worker control, not another 100 cells. Against the current 31×31 grid, a
rotation-only 2× speedup can tolerate a longest side of at most 43 cells
(`31√2`, strict score inequality). This is an optimistic geometry gate, not a
score prediction: fixed decode paths reduce the real speedup. A complete
candidate should be abandoned as soon as it exceeds 43, and must beat the
checked-in machine on all seven public cases before replacing it.

The complete acknowledged two-bank reference is now generated by
`memory_banked_machine.py` (with synchronized `.man`, debug HTML, and debug
JSON sidecars). Both 50-cell rings use the exact 51 pipe slots required to
avoid a transient WRITE deadlock, and the machine passes all seven public
cases, including consecutive overwrites and both boundary banks. Its measured
public average is 15,118.57 ticks at 113×72 after cropping. This is a protocol
proof, not a submission candidate: the accepted 31×31 machine averages 13,248
ticks locally.

The measurement also invalidates the rotation-only 2× estimate as a placement
budget. On a 100-operation low-bank workload, the accepted N=100 machine takes
61,342 ticks, while the same compact worker at N=50 still takes 50,293; fixed
setup, relay cadence, and pipe phase dominate enough that halving cells saves
only 18%. The one-shot-initialized N=50 experiment reaches 36,577 ticks (40%),
but would require a longest side below roughly 40 cells merely to tie the
accepted score. Two independent workers plus routing do not meet that bound.
Banking remains useful when the banks already exist, but it must not replace
the submitted `memory` solution without a shared-room worker or a materially
faster pass loop.

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
error, so the generator omits the `O` room in that configuration — and refuses
outright if a program uses both, since the `O` room stands where the port pipes
turn. The `I` room goes the same way: `palette` reads no input, and an unused
incoming pipe is not merely dead weight, it still competes for every `r` in the CPU
(§7.1 is nearest, not nearest-useful).

**Built, and the geometry is forced twice over** (`machine._display`). The panel
goes in the free space south of the CPU, and all three pipes leave the *south* wall
so that each `s` binds its own port: the pipe drops out of the wall **in the same
column as its own `s`**, which makes its distance strictly smaller than any
sibling's by exactly the columns between them (`_DSP_PITCH` guards the margin —
column separation has to beat row separation). ADDR must arrive from the north and
SWAP from the south while the CPU is on one side, so exactly one detour is
unavoidable, and that fixes two orders:

- the three lanes' columns run **DATA < ADDR < SWAP** west to east, so DATA's
  westward leg starts west of every column a later lane uses and SWAP's eastward
  leg starts east of every column an earlier one uses;
- ADDR drops *straight* into the top wall, DATA turns west and comes back into the
  left wall, SWAP turns east, runs beneath the panel and comes back up into the
  bottom wall.

Swap either order and two pipes share a cell, or a port silently binds its
neighbour's pipe — a program that addresses where it meant to paint.

ADDR is the one pipe with no corridor row to turn on, so the panel must **span
ADDR's column**. A 32×24 panel under a 48-wide CPU does that where it sits
(`plotter`); `palette`'s 8×8 one does not, and the panel slides east until it does
(`machine._panel_x`).

#### The panel is a persistent framebuffer — measured, and it changes the cost model

`plotter` and `palette` both commit with `SWAP ← 0`, which clears `next`, so both
repaint every pixel they want in every frame. That is not the only mode. Probed on
the engine (4×4 and 8×8 panels, single-stepped, frames dumped at every commit):

- **`SWAP ← 1` keeps `next` *and* the cursor.** Paint one pixel, commit 1, paint a
  second, commit 1: frame 2 holds **both**. So a round-based display problem can treat
  the panel as a framebuffer that survives commits and write only the pixels that
  *changed* — `snake`'s tick is 2 pixels rather than 256, which is the whole reason its
  ticks are dominated by the tape and not by the panel.
- **`next` starts all-zero**, so the first frame may paint a few pixels and commit.
- **`ADDR ← row*w+col`, then `DATA` paints there and advances by one**; at the last
  cell it wraps to 0 with no error. Row-major, verified on a non-square panel.
- **Every `SWAP` emits exactly one frame, even an unchanged one**, and one `SWAP`
  never emits two. There is no way to commit "silently", which is what makes a
  no-frame round (`snake`'s direction rounds) a *hardware* obligation rather than an
  optimisation: an extra commit desynchronises every later frame.
- **The panel costs 0 ticks beyond pipe transit.** A value sent on tick *T* is
  consumed during tick `T + (L-1)` for a pipe of `L` cells; all three ports can be made
  to land on the same tick, and the same-tick order is ADDR → DATA → SWAP, so the pixel
  written on a tick *is* in the frame committed on that tick. Unequal pipe lengths
  reorder writes — plan latency, not send order. Values still in flight are processed
  after the last man halts.
- **Range faults are fatal on the arrival tick**, each with its own reason:
  `display-value` for `DATA` outside 0–15, `display-addr` for `ADDR` outside
  `0 … w*h-1`, `display-swap` for anything but 0/1.

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

### 7.6 The lane-order shortcut is already spent — measured, not argued

A lane that touches no memory does **not** walk out to the shared `mem_x`
column: `_flat_lane` only pads a lane that contains a `Band.MEM` glyph, so short
lanes already turn south early. That shortcut is in.

The obvious refinement — order lanes by their *true* east extent rather than by
micro-program length — **loses**. The two differ because a memory lane reaches
`max_prefix + (len(micro) − first_mem) − 1` while a memory-free lane stops at
`len(micro) − 1`, and the existing order is genuinely not monotone in the true
extent (`ST` extent 4 sat above `SUB` extent 5). Reordering to fix that was
measured on all three generated solutions:

| | micro-length order | true-extent order |
|---|---|---|
| `brackets` | **363,687,473** | 364,229,566 |
| `tcp` | **1,215,797,931** | 1,217,620,992 |
| `sudoku-validity` | **12,712,904,437** | 12,739,766,825 |

Uniformly ~0.2% worse, so it was reverted. The reason is that **the drop
staircase is not what sets the width**: for `brackets` the width decomposes as
`9 + 33 (cpu) + 4 + 13 (adapter) + 6 + 33 (tape) = 98` and is floored by the
structures band (`struct_east = 28` against a maximum `lane_end` of 22), so lane
order changes only *which* row each opcode gets, never the box.

What that leaves as the real per-row lever is **vertical** travel, and its shape
is worth knowing before anyone tries again. A lane costs
`|row − centre|` to reach plus `collector − row` to drop. For `row > centre`
those sum to `collector − centre`, a **constant**; for `row < centre` they sum to
`collector + centre − 2·row`, which *decreases* as the row moves down. So rows
above the trie centre are strictly worse than rows below it, and the only
profitable ordering rule is to keep hot opcodes at or below centre — which needs
dynamic opcode frequencies, not static lane geometry. Untested.

Two other footprint facts fell out of the same measurement:

- `rows_for_budget(..., max(40, W))` caps the ROM at 40 columns even when the
  grid is 98 wide. Folding it to the true width would drop ~10 rows of height at
  no footprint cost — and height slack is exactly what a depth-5 trie (+~32 rows)
  would need to be affordable.
- The tape is a fixed 33 columns at **any** `N` (checked at `tape_n` 5, 6, 8 —
  all give 98×79 for `brackets`), so shrinking `tape_n` buys nothing.

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
| Sem 2 | `plotter` | display ADDR/DATA/SWAP + line arithmetic | 10 | 6/6 · P=243 · 74k | **✔ solved by the synthesiser** (112×116, 204,330 ticks avg, score 2.75bn) |
| Sem 3 | `gradebook` | ids + N×K grades, search by id; needs `LDA`/`MOVA`/`DIVI` | 93 | 7/7 · P=460 · 318k | **✔ solved by the synthesiser** (112×103, 694,713 ticks, score 8.71bn) |
| Sem 3 | `matmul` | rotate-only A/B/C streams; fused hardware MAC | 14 scalars + STREAM | 7/7 · P=126 · 120k | **✔ solved by STREAM** (96×96, score 1.11bn) |
| Sem 3 | `subset-sum` | 20 values + subset search | 24 | — | C |
| Sem 3 | `sudoku-validity` | 81 cells + 27 set checks | 81 | — | C |
| Practice | `hello-world` · `max-element` · `atoi` | 0–1 slots | ≤1 | 1/1 · 10/10 · 2/2 | A |
| Sem 4 | `snake` | persistent framebuffer + a body FIFO; the tape version needs `LDA`/`MOVA`/`INCM`/`DECM`/`DIV`, the coprocessor version needs none of them | 8 scalars (was 11 + 50) | 5/5 · P=183 · 122k | **✔ solved twice**: `snake.asm` on the tape alone (123×129, score 15.9bn) and `snake-ring.asm` on the SNAKE unit (121×136, avg 182,149, **score 3.37bn**, 17/17) |
| Practice | `palette` | display ADDR/DATA/SWAP, 16 solid frames | 1 | 1/1 · P=89 · 59k | **✔ solved by the synthesiser** (98×98, 151,147 ticks, score 1.45bn) |

Stages: **A** = CPU + SPILL only · **B** = SPILL + the tape · **C** = tape +
display ports.

Both display machines are generated by `lm1/machine.py` and verified *frame for
frame on the wasm* — twice over, in fact: `lm.mjs judge --frames` reads back the
engine's own `frameJudge` verdict, and `tools/display-frames.mjs` steps with
`stopOnFrame` and compares the snapshots in Python. Neither is the old "no fatal, no
output" check, which proved nothing on a problem that emits no output.

### 8.0 `snake`: what a coprocessor is worth, measured twice

`snake` is the only problem solved both ways on the same ISA, so it prices the tiers
against each other on one program. Both pass all 17 cases the judge runs.

| | `snake.asm` | `snake-ring.asm` |
|---|---|---|
| body of ≤50 cells lives in | the tape, a 50-slot ring | the unit's value ring |
| self-collision test | a scan: 5 reads **and a ROM lap** per cell | one `STEP` command |
| the panel is driven by | the CPU (3 lanes, 3 pipes) | the unit |
| opcodes | 23 (depth-5 trie) | **16 (depth-4)** |
| tape | N=66, ~653 ticks a read | N=9, ~180 |
| grid | 123×129 = 16,641 | 121×136 = 18,496 |
| judge avg ticks | 954,945 | **182,149** |
| **judge score** | 15,891,242,682 | **3,369,020,288** |

Three results worth keeping:

- **A `STEP` on a six-cell body costs 218 engine ticks — about 1.35 CPU
  instructions** — where the tape scan it replaces cost ~5,300. Rotating a ring is a
  pipe's cost; a tape read is 523 ticks and a ROM lap ~1,000 more.
- **The box got bigger and the score fell 4.7×.** 18,496 against 16,641: a
  coprocessor is not a footprint optimisation, and `max(w,h)²` is worth paying when
  the tick factor moves by 5×.
- **The 17th opcode costs a trie level *plus* its lane rows.** Dropping `NEG` (build
  it as `LDI 0` / `SUBI n`) and the final `HALT` (a blocking `IN` instead — the case
  has ended, so no input ever comes) took exactly 17 opcodes to 16 and the machine
  from **158×167 to 121×136, and the average from 151,544 ticks to 122,264** — the
  footprint *and* every instruction's decode. Sixteen is the number to design to.

Where the ticks now are (gated profile, longest public case): lanes 33.6 %, jump/branch
slabs **25.7 %**, return path 20.5 %, trie 9.2 %, fetch 2.0 %. The tape has stopped
being the story; `P` = 183 words and the return path are what is left.

Two rules the build produced, both of which generalise beyond `snake`:

- **A coprocessor should not answer back.** §7.1 makes an incoming pipe a rival for
  every `r` in the CPU, including the jump slab's ROM read — so a *replying* unit
  cannot be placed on a machine that has jumps at all. Measured: all 4,800 (fold,
  `mem_pad`, `stream_pad`) combinations fail, always on that binding, and `matmul`
  escapes it only by containing no `JMPF`. Give the unit enough authority to act on
  what it finds — `STEP` either moves the snake or ends the game — and the machine
  places at once.
- **A unit that owns the display costs the CPU nothing.** The three port lanes and
  their pipes disappear from the CPU, the panel is placed inside the block where its
  three pipe *lengths* can be asserted against each other (`addr` = `data`, `swap` ≥
  `data`, or a commit overtakes the pixels it commits), and the drawing fuses into the
  ring commands — `GROW` appends *and* paints *and* commits.

### 8.3 `pathfinder`: bitwise ops change the algorithm, and the profile says what is left

The language has `&`, `|`, `~`, `{`, `}` (`SPEC.md`), which none of the earlier problems
needed. That turns a 16x16 board from a 256-cell array into **four 64-bit words**, so one
BFS level is ~16 word operations instead of a queue over 256 tape cells. `MULI`/`DIVI` by
powers of two serve as the shifts, which is sound only because of one invariant:

> **Bit 63 of every bitset is clear.** Bit 63 of word `w` is cell `64w` — row `4w`,
> column 0 — and the spec guarantees every border cell is a wall. So every word is
> non-negative and a floor-divide really is a *logical* shift. `MULI` is a wrapping
> multiply and needs no such caveat.

Bit order is **reversed** (bit `b` is cell `64w + (63 - b)`), which is not cosmetic: it is
what lets setup fold the input stream with `acc = 2*acc + v`. The natural order needs a
`1 << 63` literal, which the ROM cannot encode as a positive word, and it would drive the
accumulator negative and break the invariant above.

**The tie-break costs nothing.** Take the four directions as up, right, down, left and
*consume* the unreached set as each is taken; a cell reachable two ways at one level keeps
only the first. That is exactly the spec's preference order — no priority masks, no
complements. Confirmed against the contest's own expected frames, which `pathfinder.json`
ships per round and `pathfinder_sim` reproduces byte for byte on all seven public cases.

| | measured |
|---|---|
| grid | 180x184 = 33,856 |
| public avg ticks (engine) | 5,000,658 |
| judge | **17/18** — a full pass is required, so it scores nothing |
| cost model (engine, 7 cases) | `1,680,572 + 61,159 x moves`, max residual 248k |
| the 15M cap is reached at | **~218 total moves** |

The spec bounds each path at 64 moves but places **no bound on the number of rounds**, so
~10 long rounds exceed the cap. The 18th case is a *ticks* failure, not a wrong answer.

Where the ticks go (gated to the scored tick, CPU runner pinned by hand — `critical_runner`
picks the tape's man on this machine, §4.1's first profiler trap, and reports `tape 100%`):

| bucket | share | note |
|---|---|---|
| **lanes** | **43.7 %** | `LD` alone is 24.1 % and `ADD` 6.1 %, and the hottest cell of each is the mem-response `r` — this is blocked tape latency, not work |
| slabs | 23.5 % | `JMPF` 17.9 %, `BRZ` 5.6 % — the ROM lap a backward jump pays |
| return | 13.6 % | pure walking |
| trie | 8.8 % | depth **5**, because the CPU paints the panel itself |
| fetch | 1.0 % | |

Three things that follow, and the third is the one that matters:

- **More unrolling is nearly spent.** Per move the jump cost is
  `(P - LEVELS*b_level)/LEVELS + (P - WALKS*b_walk)/WALKS` words, and going from
  (2,4) to (4,8) to (8,16) copies moves that from ~1,174 to ~807 to ~638 words, i.e.
  ~5 % of the per-move cost for a P that nearly triples. The 23.5 % slab share is mostly
  the *setup* loop, which is fixed cost and does not touch the slope.
- **Sixteen opcodes is still free money, but it is not the cap.** The three display
  opcodes are what force depth 5; a write-only coprocessor spends one `SND` instead
  (`lm1/path_unit.py`, `pathfinder-unit.asm`). That attacks the trie's 8.8 % and part of
  the return path's 13.6 %, and `snake`'s identical 17->16 step measured -19 % ticks and
  -41 % footprint. Worth taking; ~-19 % where the cap needs ~-55 %.
- **The slope is tape reads and nothing else.** ~89 reads a move at ~415 ticks is ~58 % of
  the 61,159. Shrinking the tape helps at ~1.9 ticks per slot per access (§8.1), i.e. a few
  percent. Halving the *count* needs the level step itself in hardware — `snake` measured
  24x on exactly that move (§8.0) — and a unit that computes levels must also walk and
  paint, because it cannot answer back.

One measured negative worth keeping, because it looked obvious: **guarding a word's block
on an empty frontier loses.** A word can only be skipped when its own frontier *and both
neighbours'* are empty, since the cross-word terms feed it, and the frontier occupies ~1.9
of 4 words *contiguously* — so the guard almost never fires. 4.99M -> 5.06M, i.e. worse.

### 8.1 `plotter` was 6 % over the step cap, and the fix was tape accesses per pixel

The margin used to be **negative** at the constraints' limit: 20 rounds of the worst
legal segment cost **5,311,321** ticks against a 5,000,000 cap, and the only reason
that shipped is that the number nobody had measured on the engine was the one everyone
quoted. All figures below are `lm.mjs judge --frames`, never the emulator.

| Load | before | after |
|---|---|---|
| worst **graded** case (`octant fan`, 8 rounds) | 857k | **378k** |
| 20 rounds of the worst legal segment | **5.31M — 1.06× the cap** | **1.94M — 0.39×** |
| score (`max(w,h)² × avg ticks`) | 12,544 × 483k = 6.06bn | 13,456 × 204k = **2.75bn** |

**Where the ticks were, decomposed against the engine's total** (20 rounds,
12,560 tape accesses, 19,220 instructions, 59,600 recirculated words): solving for the
per-unit costs gives **~316 ticks per tape access**, ~45 per instruction and the known
8 per skipped word — so the tape was **75 %** of the bill, instructions 16 % and jumps
9 %.

That 316 is the load-bearing number and it is *not* §4.1's `105 + 8.3N`. §4.1's slope
is real but small: rebuilding `plotter` at N = 11/21/31 moves the total by only
~1.9 ticks per access per slot, so almost all of the 316 is fixed cost per access —
adapter round trip plus the tape's own dispatch — and it does **not** amortise. The
practical rule: **an access costs ~7 instructions. Count accesses, not instructions.**

Three transformations got the inner loop from ~20 accesses per pixel to 4
(`programs/plotter.asm` documents each; all three are verified against the spec's
pseudocode over all 589,824 endpoint pairs):

- **carry `addr = 32*y + x` instead of `(x, y)`** — the map is injective on the panel,
  so the stop test `x==x1 and y==y1` is exactly `addr == addr1`;
- **split on the major axis**, which makes one of Bresenham's two error tests
  identically true, leaving two arms whose entire effect is *one addition of a
  per-round constant* to (err, addr);
- **pack err and addr into one word** at radix 1024, so that addition is one `ADD`.
  The surviving error test becomes `sign(q)` by folding the threshold into the packed
  value, which works because the threshold is a whole multiple of the radix and so
  cannot disturb the low field. `MODI 1024` recovers `addr` with no access at all.

The fourth lever is **unrolling, and it is worth stating on its own** because it is
counter-intuitive: a backward jump recirculates `P − body` words at 8 ticks each, so
**every iteration pays for the whole program's non-loop code whether it runs or not**
(§5.4). `P − body` is just the setup, so `u` copies of the body divide that tax by `u`
at a cost in ROM cells only — measured 2,485,405 / 2,075,485 / 1,894,525 / 1,846,233
ticks at u = 1/2/4/6. Four is where it flattens and where `footprint × ticks` bottoms
out; note the *footprint* cost is real here, because `plotter` is height-bound once the
ROM is folded to 112 columns.

Two smaller findings from the same rewrite:

- **An `N`-slot tape addresses 1..N−1.** Ten live values fit an N=11 tape only after
  aliasing four pairs whose live ranges do not overlap. Overrunning by one slot is not
  a wrong answer, it is `fatal: wall` inside the tape room — the same failure `tcp`
  hit at 51 slots.
- **16 opcodes is free, 17 is not.** `k = ceil(log2 |used|)`, so the sixteenth opcode
  (`MODI`, here) costs nothing while a seventeenth adds a trie level to *every*
  instruction plus ~32 lane rows. That ceiling is what ruled out `DIVI`/a shift opcode
  for the branch-free `sx = 2*floor((dx−1)/32) + 1` trick, which would otherwise have
  shortened the setup.

`privateTestCount: 0` is therefore no longer what makes `plotter` submittable — it says
0 for every problem here, and it said 0 for `gradebook` too, which the judge then
served a private case anyway. Measure the constraint limit on the engine and assert it:
`test_lm1_display.py::test_the_worst_legal_20_round_load_fits_the_step_cap_on_the_engine`.

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
- **The ordinary tape makes `matmul` infeasible, but STREAM solves it.** The
  ~8,450-access estimate correctly rejects random-access STORE. The final loop
  order only rotates A, B, and the current C row, so `lm1/stream.py` replaces
  those accesses with three FIFO rings and performs 4,096 worst-case MACs in a
  local counted loop. The generated 96×96 grid passes 7/7 at 120,460 average
  ticks and 550,774 worst-case ticks.

`history-lesson` proved the point about scoring rather than capability: LM-1 can
emit it (P=8431, ~40k ROM cells), but on a `footprint`-only problem that is
hopeless, so it shipped as a **bespoke 144×148 grid** instead
(`tasks/solutions/history-lesson_rom.man`, generated by `rom_snake.py`). Two problems
are now solved *without* the CPU — a reminder that LM-1 is the safety net, not
the plan of record.

### 8.2 Split (`Y`) audit

The supplemental [official split reference](https://icfpcontest2026.com/split)
changes the architecture search space in one very specific way: `Y` duplicates
the current A, B, and BP into two runners **without adding a room or a pipe**.
It does not duplicate pipe values or tape state and provides no join
instruction. Consequently, the profitable shape is:

1. build shared state in registers;
2. split into disjoint corridors;
3. let both children perform independent side effects, usually sends to
   different devices or deterministic writes to one output pipe;
4. halt the children separately.

The right child retains the parent's creation-order position and the left child
runs last. That gives deterministic ordering when both reach side effects on the
same tick; it is useful, but it is not mutual exclusion. A child blocked on a
pipe remains live, and runners which meet, swap cells, or arrive at the same cell
kill one another. Split layouts therefore need disjoint lanes except where a
collision is deliberately the result.

The complete released-task audit is:

| Task | `Y` value | Assessment |
|---|---|---|
| `triangle` | none | The closed form is already a short dependency chain. Forking multiplication/addition adds setup and a join, so the 24×3 bespoke grid wins. |
| `reverse-a-list` | low | Input and output order are both serial and the current ring is the storage. A feeder could alternate values between workers, but merging them in reverse order needs the same count/state and adds pipes. |
| `sort-numbers` | low | Parallel compare/search needs either duplicated arrays or a reduction. The single ring/tape remains the bottleneck; split workers cannot safely mutate it independently. |
| `memory` | **high; bank selector implemented** | Split each operation across two independent 50-cell rings. The low worker uses `addr`, the high worker uses `addr - 50`, and exactly one performs the target side effect. `memory_banked.build_bucket_probe` proves the selector on real engines; the remaining work is physical placement of the two rings under the 43-cell go/no-go bound. The older `memory_blocks.east_fork` remains a smaller read-side-effect gadget. |
| `history-lesson` | none for score | Two emitters can produce the fixed text faster, but scoring ignores ticks. They duplicate or complicate the compressed stream and cannot reduce the 144×148 footprint bound. |
| `brackets` | low | Stack state is a prefix recurrence. Splitting chunks requires exporting each chunk's unmatched prefix/suffix and joining summaries; at length 32 that machinery is larger than the packed-stack loop. |
| `tcp` | low | Packet arrival, reorder-buffer mutation, and earliest-possible ordered output share one moving frontier. Split lookup/output servants can overlap locally, but both still serialize on the same tape and output pipe. |
| `plotter` | low | The four unrolled pixel bodies look identical but each consumes the previous pixel's packed `(err,addr)`. Separate rounds are withheld until the frame commits, and display writes/commit are side effects on one device. |
| `gradebook` | medium in a banked rewrite | Student scans and per-subject aggregates are independent and can be split by roster half. The current build has one tape adapter, so two CPU runners merely queue the same expensive accesses; a win requires two memory banks plus a small min/max/sum join. GET/SET remain serial operations. |
| `matmul` | low for the solved critical path | Main now uses three rotate-only rings and a four-glyph hardware MAC, not one tape. Splitting the MAC's B-return and product sends does not shorten its three-tick critical path and needs termination corridors. A better `Y` use is structural: seed the two persistent A/B relay loops in one shared room; `stream.dual_relay_cells` is the validated placement scaffold. |
| `subset-sum` | **conditional** | Include/exclude search maps directly to `Y`, copied registers give both branches the same prefix state, and BP can hold the 20-bit choice mask. But the values are dynamic input: every live branch still needs the next value, and neither a tape read nor an input receive broadcasts inside a room. A full depth-20 tree also exceeds the 65,536-live-runner cap. Split is useful only after solving value distribution (staged broadcast or replicated banks), and lexicographic output still needs an ordered winner protocol. |
| `sudoku-validity` | **high local shape** | Row, column, and box are three identical test-and-set operations. Two splits can make three workers and combine failure as a side effect (any duplicate writes a sticky invalid flag). The current single tape serializes all three, so the practical rewrite needs three independent 9-word banks or three bitset servants. This is the clearest small-room prototype. |
| `hello-world` | none | Independent constant emitters immediately serialize on the one output pipe; deterministic creation order is possible but the fork corridors cost more cells than the tiny literal stream. |
| `atoi` | none | `acc = 10*acc + digit` is a strict recurrence and rounds withhold the next input. Chunked parsing needs powers and a join, which is larger for at most ten digits. |
| `max-element` | low | Two reducers plus one final max are possible, but distributing a length-16 stream and joining costs more than the register-only single reducer. |
| `palette` | low | Fill and commit for colors 0…15 are externally ordered. Workers may prepare constants, but all display side effects still serialize and the practice task is not worth a larger split fabric. |

This yields three implementation rules:

- **Small rooms:** use `Y` as a one-cell register fan-out when each child can
  terminate in a different side effect. The memory read-target fork is the
  reference gadget; Sudoku's row/column/box update is the next candidate.
- **Large rooms:** do not split the CPU while leaving one tape. Split or
  specialize the data plane too—STREAM for `matmul`, and a bounded search tree
  plus winner collector for `subset-sum`.
- **Identical subfunctions are not sufficient.** They must have independent
  inputs/state or commute through a side effect. Plotter's repeated bodies and
  gradebook's scans are syntactically alike, but their current data paths remain
  serial.

Concrete probes and budgets keep this audit falsifiable:

- **Creation order / side effects:** `tests/test_split_instruction.py` has a
  two-child output probe. Both children reach the same pipe on the same tick;
  the right child sends `2`, the left blocks and later sends `1`, producing
  exactly `[2, 1]` on the wasm, Python, and native validators. This proves a
  deterministic side-effect fan-out, not just two runners in a snapshot.
- **Memory:** `littleman/examples/memory-onepass-v2-tight.man` still passes 7/7
  public cases after the corrected split/collision implementation, averaging
  15,879 ticks. At 78×47 its local footprint-tick product is roughly 96.6M,
  versus roughly 12.7M for the submitted 31×31 ring at its recorded 13,248
  local ticks. The split gadget stays a routing building block; this machine is
  not a submission candidate.
- **Banked-memory gate:** `memory_ast.banked_spec(100, 2)` proves every address
  selects exactly one bank and that the high child's local address is
  `addr - 50`. Two rings need 102 pipe cells in total versus 101 today, while
  the modeled rotation path falls from 800 to 400 ticks. The executable probe
  verifies outputs `addr % 50` for all 100 addresses. A placed solution must
  remain at most 43 cells on its longest side to have any idealized score
  margin over 31×31.
- **Sudoku go/no-go budget:** the current CPU performs **17 tape transactions
  per cell**: five to materialize addresses and four for each row/column/box
  test-and-set. Three split workers with three independent 9-mask banks reduce
  the storage critical path to four transactions and run the identical unit
  arithmetic concurrently. Using the measured 47% tape / 37% execute / 16% ROM
  decomposition, the optimistic time ratio is
  `0.47×4/17 + 0.37/3 + 0.16 ≈ 0.394`, or **2.54× faster**. It can beat the
  current 89×94 grid only if the tiled three-bank machine stays below about
  `sqrt(2.54)×94 ≈ 150` cells on its longest side. That is a useful geometry
  acceptance test before implementing the full generator.
- **Matmul result:** main's single STREAM worker is already better than the
  proposed 16-worker bank: it needs three rings, 14 scalar tape slots, and 0.59
  CPU instructions per MAC. The first `Y` prototype therefore targets relay
  room consolidation, not more MAC workers.
- **Subset-sum stop condition:** do not draw the exponential fork tree until a
  value-distribution probe can deliver one dynamic `v_i` to every live branch
  cheaper than those branches can read it serially from the tape. At depth 16
  there are already 65,536 runners—the entire live limit—and blindly giving
  each one 20 tape reads is worse than a sequential meet-in-the-middle solver.

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
   `reverse-a-list`, `subset-sum`, `sudoku-validity`. Each is now an `.asm` file plus
   a tape size (and a `ROM_ROWS` fold if the default is not the footprint optimum),
   as `gradebook` demonstrated. `matmul` now uses the separate STREAM tier
   because its access order is FIFO rather than random (§8).
   `gradebook` also measured where the ticks of an array problem *go*, which the
   scalar programs could not: with 93 tape slots an access is ~885 ticks and every
   variable is a slot, so **memory traffic is ~68% of the bill**, the ROM lap a
   backward jump pays is ~18%, and execute is the remaining ~14%. Two consequences
   for the programs that follow: bound loops by pointer equality rather than by a
   counter (one access a lap, not two), and treat the *total* instruction count as
   part of the cost of every inner loop, since a loop iteration recirculates
   `2*(n - body)` ROM words whatever it does.

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
- **`Y` (split) beyond the proven memory fork.** Place and benchmark the tested
  dual-relay scaffold in STREAM, then prototype Sudoku's three independent
  bitset servants. For `subset-sum`, solve dynamic value distribution before
  drawing a bounded split tree (§8.2).
- **Second "loop ring"** for hot bodies, if §5.4's jump cost dominates real
  programs.
