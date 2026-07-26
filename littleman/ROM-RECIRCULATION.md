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

**Re-measured over *every* public case, at a measured tick rate.** The table below
replaces an earlier one that sampled three cases a problem and multiplied by an
assumed 6–9 ticks a word. Both of those inputs were wrong, in opposite directions,
and the conclusion got stronger rather than weaker.

The rate is **4.8 ticks a recirculated word**, not 6–9. `_discard_loop` retires two
words per lap of an eight-cell cycle — 4.0 in the steady state — and the profile
implies 4.8 once each branch's entry and exit are amortised in: `switchboard`
discards 1,305,966 words and spends ~6,268,661 ticks in `cpu:slab:JMPF`.

| program | words/case | discard ticks | total ticks | share |
|---|---|---|---|---|
| `little-little-man` | 764,936 | 3,671,693 | 7,009,707 | **52.4%** (engine) |
| `pathfinder` | 148,058 | 710,680 | 1,575,791 | 45.1% |
| `snake` | 15,180 | 72,865 | 169,651 | 42.9% |
| `gradebook` | 9,905 | 47,545 | 118,795 | 40.0% |
| `matmul` | 4,868 | 23,366 | 62,796 | 37.2% |
| `sudoku-validity` | 16,304 | 78,260 | 218,224 | 35.9% |
| `plotter` | 2,429 | 11,658 | 66,998 | 17.4% |
| `brackets` | 445 | 2,135 | 13,694 | 15.6% |
| `tcp` | 638 | 3,062 | 24,035 | 12.7% |
| `palette` | 77 | 370 | 59,102 | 0.6% |

`little-little-man`'s row is engine-measured (`optimize.verify` for the total,
`tools/heatmap.mjs` for the split). The rest are emulator totals with the
emulator's own `skip_word = 8` backed out and 4.8 substituted, so they are
comparable to each other and to the engine row on the discard term.

**Recirculation is 36–52% of six different machines.** It is not an
`little-little-man` peculiarity; it is what a CPU-generated machine spends its time
on, and anything that moves the rate moves all of them at once.

Two things worth keeping in mind before optimising for this:

- **`sudoku-validity` has no jumps at all.** A taken *branch* discards through the
  same loop, so "does this program jump" is the wrong question; "does it loop" is
  the right one.
- **This is not one problem's tax.** The first version of this note read as though
  `little-little-man` were the only machine worth the trouble. Measured across every
  public case, six machines spend 36–52% of their ticks recirculating, so the rate
  is a *generator-wide* constant and a change to `_discard_loop` is worth ~11% on
  `little-little-man` and 7–10% on `pathfinder`, `snake`, `gradebook`, `matmul` and
  `sudoku-validity` simultaneously. `tcp`, `brackets` and `palette` remain rounding
  error — measuring anything on them still proves nothing.
- **The share rose as the rest of the machine got faster.** `little-little-man` was
  21.4% when a store read cost 3,416 ticks; the banked store cut the store term to
  ~14% and recirculation became 52% of what is left, without a single extra word
  being discarded. Shares move when *other* terms move, so re-measure before
  choosing a target rather than trusting a share recorded against an older machine.

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

## Still open

**1. Unroll the discard loop two words to a lap. — DONE, and the residual idea is
impossible.** `b016681` built it: `_discard_loop` is now a 2x4 burst retiring two
words per lap, which took the rate from 6 ticks a word to 4.

The `d, r, r, <, ^, m, >` variant proposed here — 7 ticks for 2 words, by having
`BP` count *instructions* so the lap decrements once instead of twice — **cannot be
built, and would buy nothing if it could.** A grid graph is bipartite, so every
closed walk on it has even length: there is no 7-cell cycle. What the loop costs is
the number of cells the man *walks*, not the number of glyphs that do work, so
dropping one `m` to a plain corridor cell leaves the 2x4 cycle eight cells long and
eight ticks a lap. Counting instructions rather than words is free of benefit here.

The rate only improves by making the cycle *deeper*: a 2x(k+2) block retires `k`
words in `2k + 4` cells, so k=2 is 4.0 ticks a word, k=3 is 3.33 and k=4 is 3.0,
tending to 2.0. The obstacle is exactness. `rom_words` guarantees every count is
*even*, which is what makes k=2 total; k=4 needs every count divisible by four, and
k=3 needs a remainder arm for counts that are not multiples of three. Two ways
round it, neither built:

* **Align the targets.** Pad the image so every branch target sits at an even
  instruction index, and every count becomes a multiple of four. Costs a few `NOP`s
  of `P` — which lengthens every discard — against 4.8 -> ~3.8 ticks a word.
* **Two exits in one lap.** Let `BP` count pairs and give the 12-cell cycle two `a`
  tests, one per pair, so an odd remainder leaves through the first. No padding, but
  two escape corridors out of one slab.

At 4.8 -> 3.8 that is ~21% of the discard term: **~11% of `little-little-man`'s
ticks, and 7-10% of five other machines'** — see the table above.
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
