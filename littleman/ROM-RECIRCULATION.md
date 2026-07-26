# ROM recirculation — what a taken branch costs, and what does not fix it

A generated LM-1 has no program counter. The ROM man walks a closed circuit and
re-emits every word forever (`rom.py`), the CPU fetches `>rbr` off that pipe, and
the *only* way to reach an instruction is to let the ones before it go past. So a
jump is a **discard loop**, and `rom_words` resolves its count as

```python
operand = 2 * ((index_of_word[target_word] - k - 1) % n)
```

**mod n** — always forward. A backward edge therefore walks almost the whole
program: a loop whose body is `body` instructions long costs `P - body` words of
discarding *per iteration*, and `LLM-DESIGN.md` §"The profile" prices that at
**12 ticks per recirculated ROM word**.

This document records what that costs per program, one idea that did not work and
why, and the ideas that are still open.

## What it costs

Words discarded per public case, counted on the emulator over the ROM image
(`image_program`), against each machine's measured average ticks:

| program | words/case | ≈ share of ticks | note |
|---|---|---|---|
| `sudoku-validity` | 18,817 | ~26% | **zero jumps** — all 44 branches |
| `snake` | 5,258 | ~26% | |
| `little-little-man` | 541,753 | **21.4%, measured** | `cpu:slab:JMPF` in the heat map |
| `gradebook` | 7,571 | ~15% | 29 jumps, 45 branches |
| `tcp` | 754 | ~5% | one jump in the whole program |
| `brackets` | 149 | ~4% | three jumps |
| `palette` | 31 | ~0.5% | |
| `snake-ring` | 0 | — | never takes a branch on the public cases |

Only `little-little-man`'s number is a real profile — it is `LLM-DESIGN.md:131`,
sampled with `tools/heatmap.mjs`. The rest are word counts times an assumed 6–9
ticks a word and should be read as an ordering, not as percentages. The emulator
ran the first three cases of each problem, which is not necessarily
representative.

Two things worth keeping in mind before optimising for this:

- **`sudoku-validity` has no jumps at all.** A taken *branch* discards through the
  same loop, so "does this program jump" is the wrong question; "does it loop" is
  the right one.
- **`little-little-man` is where the money is.** 21.4% of a 926,292,239,445 score
  is larger than the entire score of most other tasks. `tcp` and `brackets`, by
  contrast, are rounding error — measuring anything on them proves nothing.

## The negative: buffering the ROM corridor ("ROM-PLUS")

`SPEC.md` makes a pipe "a FIFO whose capacity equals its length", so the corridor
from the ROM to the CPU's fetch row is a queue of words already in flight. It is
~29 cells by accident of routing. The idea was to make it hold a whole program:
the CPU spends ~47% of its time in the memory lanes, the ROM man refills the queue
throughout, and a backward jump would then drain a *pre-filled* buffer instead of
pacing the man.

It is implemented, opt-in, behind `machine.ROM_BUFFER` (empty by default; with no
entry every machine is byte-identical to before). **It does not work.**

| build | corridor | avg ticks | Δ |
|---|---|---|---|
| `brackets` + buffer | 357 — 4.7× the whole program | 24,549 | **+1.4%** |
| `tcp` + buffer | 139 | 88,168 | **0.0%** |
| `gradebook` + buffer | 265 | 295,933 | **0.0%** |

### The control, and the cost model it gives

The first attempt at a control ran packed-vs-unpacked ROM on `brackets`, which is
4% discard and could not have shown anything. Re-run where discarding actually
costs — every row `optimize.verify` on the real public cases, all passing:

| program | ROM density | avg ticks | Δ |
|---|---|---|---|
| `sudoku-validity` | packed, 3.36 cells/word | 429,673 | — |
| `sudoku-validity` | unpacked, 7.00 | 444,882 | **+3.5%** |
| `gradebook` | packed, 3.41 | 295,803 | — |
| `gradebook` | unpacked, 7.00 | 301,801 | **+2.0%** |

Halving the ROM man's speed costs 2–3.5%, and the numbers fit one model:

```
discard cost per word = max( 6 ticks CPU loop , ROM emission ticks/word )

packed   : max(6, 3.36) = 6.00   CPU-bound
unpacked : max(6, 7.00) = 7.00   ROM-bound, +16.7% on the discard
                                 x 26% of sudoku's ticks = +4.3% predicted
                                 measured +3.5%
```

So the ROM man is genuinely hidden behind the loop **at today's loop speed**, and
that is why the buffer is flat. It is also why a *repeater* — a cycled ring
re-emitting the program at ~2 ticks a word instead of the man's 3.4 — would be
equally flat: `max(6, 2)` is still 6. Both ideas are blocked by the same nozzle.

**The ordering that falls out of this.** The repeater is not wrong, it is
*second*:

| loop | ticks/word | vs ROM at 3.36 | binding constraint |
|---|---|---|---|
| today, 1 read a lap | 6.0 | | the CPU loop |
| k=2 unroll | 4.0 | | the CPU loop, only just |
| k=4 | 3.0 | 3.36 | **the ROM man** |
| several men (`Y`) | →2.0 | 3.36 | **the ROM man** |

Below ~3.4 ticks a word the ROM man becomes the bottleneck and a repeater is what
unblocks it. Anything faster than k=2 needs the producer fixed first.

**Pros of keeping the corridor:** free when off (`ROM_BUFFER` empty, all machines
byte-identical), ~40 lines, and on a **width**-bound machine the band it snakes
through is dead space — `brackets` takes a 357-word buffer at 90x70 -> 90x78 with
the footprint unchanged at 8,100.

**Cons:** dead code with no caller; it encodes an assumption measured false; and
on a **height**-bound machine it is not free at all. `little-little-man` is
203x204, so every corridor row goes straight into the box: **+6.0%** at 400 words,
**+16.3%** at 1,069, **+55%** at a full program. On the one machine where
recirculation is worth 21.4% of ticks, this feature is pure loss.

## The measurement that settles the ordering (2026-07-26)

The drain was built (`lm1/drain.py`), wired into `_slab` behind
`machine.DRAIN_UNIT_BITS`, and measured on `little-little-man`. **It is a net
loss, and the reason is the number this document guessed at.**

| build | box | area² | avg ticks | score |
|---|---|---|---|---|
| today, counted loop | 192x194 | 37,636 | 7,594,099 | 285,811,507,276 |
| `unit_bits=2` drain | 192x200 | 40,000 | 7,580,362 | 303,214,497,143 |

14/14 public cases pass either way. The drain went from 4.0 to 2.51 ticks a word
and bought **0.18% of ticks** — so the discard was never paying 4.0.

Measured directly instead of assumed: this program's ROM is 3,498 words in a
17,480-cell packed lap, which is **5.0 ticks a word**. Against the loop's 4.0,
`max(4.0, 5.0) = 5.0` — the CPU loop has been *idle* on `r` for a fifth of every
discard, and making it faster cannot move a number it does not set. The earlier
"6 ticks CPU loop vs 3.36 ROM" model had both figures from other programs;
`little-little-man`'s operands run to 3,470, so its tokens average 4.29 cells
against `gradebook`'s 3.46, and the fold's connectors add the rest.

### How big the producer is: two methods, agreeing

**Method 1 — engine A/B on the ROM lap.** Rebuild the same machine with the
unpacked ROM and re-verify all 14 public cases. Only the lap changes, so the
slope is the number of laps the program waits out per case:

| ROM | lap | cells/word | avg ticks |
|---|---|---|---|
| packed (shipped) | 17,480 | 5.00 | 7,594,099 |
| unpacked | 28,800 | 8.23 | 10,322,651 |

    (10,322,651 - 7,594,099) / (28,800 - 17,480) = 241 laps a case
    241 x 17,480 = 4.21M of 7.59M ticks -- 55.5% ROM-paced

**Method 2 — count the words.** Every discarding instruction's operand, summed
on the emulator over `image_program`, across **all 14** cases: a mean of 908,850
words a case, at 5.00 cells a word = 4.54M ticks, **59.8%**.

The two agree to 8% (241 laps is 842,900 words against 908,850 counted), from
completely different instruments. **So a little over half of `little-little-man`
is the CPU standing on an `r` waiting for the ROM man to walk round.**

A ring repeater stores one cell per word against the ROM's five and re-emits at
~2 ticks a word (`r`,`s` in a relay), so it is smaller *and* faster. At 241 laps
a case:

| ROM lap | ROM-paced ticks | total | vs today |
|---|---|---|---|
| 17,480 (today) | 4.21M | 7.59M | — |
| 7,000 (repeater at 2 cells/word) | 1.69M | 5.07M | **-33%** |
| 3,498 (ring at 1 cell/word) | 0.84M | 4.22M | **-44%** |

The drain is therefore *second*, and it is already built and tested: turn it on
by naming the program in `DRAIN_UNIT_BITS` once the producer is under 4.0 cells a
word, which is where `max(drain, ROM)` starts selecting the drain again. Note its
footprint bill on this machine is +6 rows (+6.28% area²), because the CPU room
sits **above** the display and a deeper slab pushes the display down — the slab
band's own 5-row gap at rows 169..174 is the room's south wall, not slack.

### A correction, and the mistake worth not repeating

An earlier version of this section put the producer at 42% and the repeater at
21%. Both were too low, from two errors made together: the word count averaged
**three** cases (642,113) and was then compared against a **fourteen**-case tick
average; and the unpacked lap was read off `rom.build_rom(words)` without the
`rows=` argument `machine.build` actually passes, giving 55,980 cells instead of
the real 28,800. The two errors pulled in opposite directions and the wrongness
did not show up as an inconsistency until both methods were run over the same
case set.

`heatmap.mjs` cannot referee this: its wasm OOMs above a few million ticks and a
case needs ~12M, and the `--rounds` flag in its usage banner is not implemented,
so a round-based program exhausts its input and then profiles its own stall — a
run of it here attributed 56% to the `IN` lane, which is that artefact and not a
finding.

## The repeater: what it can reach, measured (2026-07-26)

The producer is ~56% of `little-little-man` (above), so this is the piece worth
building. Three facts, each measured rather than argued, fix its design.

**1. A relay is 2.00 ticks a word.** A man re-emitting a stream does `r` then
`s`; there is no cheaper cycle. Measured on the reference interpreter with a
corridor of alternating `r`/`s` — 12 words out in 26 steps, the extra 2 being
spawn and halt:

```
+-+  +--------------------------+
|I|>>| @rsrsrsrsrsrsrsrsrsrsrsrsH|
+-+  +--------------------------+
```

That alone is **5.00 -> 2.00** on the producer. Against today's counted discard
loop `max(4.0, 2.0) = 4.0`, so a repeater on its own is worth 20% of the
producer's share; with the drain behind it (`unit_bits=2`, 2.51) it is 50%.

**2. One room cannot beat 2.00 — it is grid parity, not effort.** The obvious
answer is two men on the corridor in opposite phases, one reading while the other
sends. A room may hold at most one `@`, so the second man comes from `Y`, and
`Y`'s two children are born at `(x, y-1)` and `(x, y+1)`. Any path from a birth
cell to row `y` column `c` has length parity `|c - x| + 1`, so two children
standing on row `y` at the same tick are **always an even number of cells apart**
— always the same phase on a period-2 `rsrs` corridor. They would read on the
same tick, the input pipe delivers one value a tick, one blocks, the other steps
onto him and both stop. No arrangement of detours escapes it.

**3. Two banks reach 1.00 a word, and the ROM image already provides the split.**
`r` binds to the *nearest incoming* pipe (`SPEC.md`), so two `r` cells in one room
can read two different rings purely by where they stand. Verified with
`lm.route` on a probe — a cell at (16,2) binds the 3-cell pipe, a cell at (17,6)
the 14-cell one, same room:

    r r          <- one word from bank A, one from bank B: 2 words in 2 ticks
    ^ ^
    |  \____ nearest the south pipe
     \______ nearest the west pipe

Each bank sustains one word per two ticks, and the consumer takes one from each
per two ticks, so the pair sustains **1 word a tick with no merger man** — the
merger is exactly what would have put a 2-tick cycle back in the path. The image
is already fixed-width `(opcode, operand)` pairs (`rom_words`), so bank A holds
the even words and bank B the odd ones; the CPU's `>rbr` fetch reads one of each,
and every discard count is even (§`even` in `drain.py`), so a drain lap consuming
one from each bank is exactly correct with no remainder arm.

### Seeding, which is the part that is actually hard

A ring's pipes start empty, so something must fill them once, and a ring room that
takes both the ROM's pipe and its own return has no way to order the two — `r`
picks the nearest and would deadlock on an empty ring, `R` picks either and
corrupts the order the moment both are ready.

The way out is that the choice is not per-word, it is once: the relay man runs a
**counted seeding phase** first — `b` from a literal, then `r` from the cell
nearest the ROM, `s` into the ring, `m`, loop — and falls into the steady loop
(`r` from the cell nearest the return, `S` to the ring *and* the CPU) when `BP`
hits zero. Same nearest-pipe positioning trick as (3). One-time cost is 3 ticks a
word for one program length; after that the ROM man simply blocks on a full ring
forever and is never heard from again.

Ring capacity must be an exact multiple of the program length, or the window that
recirculates is not a whole image and the CPU sees a rotation that never
resynchronises.

## Still open

**1. Unroll the discard loop two words to a lap.** Every discard count is even —
`rom_words` emits `2 * (...)` because instructions are two words wide — so a loop
that discards two words a lap with `BP` counting *instructions* is exactly correct
for every jump, with no remainder arm. `d, r, r, <, ^, m, >` is 7 ticks for 2
words against the present 6 for 1: **~1.7×** on 21% of `little-little-man`.
*Pro:* exact, small, no new glyph, no new room. *Con:* one more cell of slab
width, and `_SLAB_PITCH`/binding has to still hold.

**2. Several men in one loop (`Y`).** `Y` splits a man into two children with
identical `A`/`B`/`BP` (`SPEC.md` §Y). Six men staggered around the six-cell loop
would retire one word per tick — the 6× rather than the 1.7×. *Pro:* the whole
prize. *Con:* `BP` is per-man, so each child counts its own laps and the
termination condition is no longer "one man's `BP` hit zero"; and a child born on
a live man kills both silently.

**3. Make the loop body bigger rather than the loop faster.** The cost is
`12 * (P - body)`, so it falls as the hot loop's *span* grows. Cold code placed
between a hot loop's entry and its back edge is discarded either way and is
therefore free; the same code placed outside the loop costs that loop one discard
per word per iteration. This is a pure instruction-placement problem with an
existing frequency model (the one that ordered the lanes), and it needs no
hardware change at all. *Pro:* free in area and glyphs. *Con:* the assembler has
no notion of block placement yet.

**4. Do not recirculate at all for short forward hops.** A forward jump of a few
instructions discards a few words; a backward one discards `P - body`. Only the
backward edges matter, and there are far fewer of them.
