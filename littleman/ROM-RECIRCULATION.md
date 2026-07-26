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
| `brackets` packed (3.46 cells/word) | 29 | 24,221 | — |
| `brackets` + buffer | 357 — 4.7× the whole program | 24,549 | **+1.4%** |
| `brackets` **unpacked** (7.00 cells/word) | 29 | 24,233 | — |
| `brackets` unpacked + buffer | 357 | 24,561 | **+1.4%** |
| `tcp` + buffer | 139 | 88,168 | **0.0%** |
| `gradebook` + buffer | 265 | 295,933 | **0.0%** |

The control is the third row. Doubling the ROM man's walk from 3.46 to 7.00 cells
a word leaves ticks unchanged, so **the ROM man is not the bottleneck at either
density** and there is no starvation for a buffer to absorb. The consumer is: the
discard loop walks `d → r → < → ^ → m → >`, six cells for one word, while the man
produces one every ~3.5. Enlarging the queue in front of a slower consumer buys
nothing and costs first-word latency, which is the +1.4%.

Caveat on the evidence: `brackets` and `tcp` are 4% and 5% discard, so those rows
prove very little. `gradebook` at ~15% is the only honest test, and it is flat.

**Pros of keeping it:** free when off (byte-identical), ~40 lines, and the band it
snakes through is dead space above the CPU — so on a width-bound machine it costs
zero footprint (`brackets`, `tcp` absorb a whole-program buffer at +0.0% area;
`gradebook` a quarter-program one). If a future change ever makes the ROM man the
bottleneck again, the mechanism is already there.

**Cons:** it is dead code with no caller, and it encodes an assumption that has
been measured false.

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
