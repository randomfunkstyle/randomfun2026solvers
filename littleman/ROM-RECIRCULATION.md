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
entry every machine is byte-identical to before). **It does not work** — flat on
the three programs first tried, and on the one machine where it finally does
something, that machine has a seek drum and the sign is inverted. See "DOOM" below.

| build | corridor | avg ticks | Δ |
|---|---|---|---|
| `brackets` + buffer | 357 — 4.7× the whole program | 24,549 | **+1.4%** |
| `tcp` + buffer | 139 | 88,168 | **0.0%** |
| `gradebook` + buffer | 265 | 295,933 | **0.0%** |

### DOOM, and the one case where it is not flat but *backwards* (2026-07-28)

The table above had no row for `deadman-3d`, which is the largest program here and
the only slug with a `SEEK_DRUM`. It turns out to be the interesting case, in both
directions.

All numbers are the **native/fast engine** (`fast_littleman`), taped tier, the same
fixed 57-command `WALK` on every arm, all `passed=True`. The reference engine cannot
referee any of this: it OOMs its 4 GB Go/wasm heap on DOOM.

| build | seek drum | box | ticks | Δ |
|---|---|---|---|---|
| corridor 29 (routing accident) | on | 295x269 | 591,485,564 | — |
| corridor 500 | on | 295x278 | 611,878,828 | **+3.4%** |
| corridor 1,677 | on | 295x296 | 675,651,202 | **+14.2%** |
| corridor 29 | **off** | 279x258 | 653,734,716 | — |
| corridor 1,677 | **off** | 279x291 | 629,578,991 | **-3.7%** |

Canonical at corridor 1,677 is **+30.3%** on the same change.

**On the classic drum the buffer works.** −3.7% is small but it is the first
positive reading this feature has ever produced, and it is on the one program big
enough for its discard bill to matter. The three flat rows above were measured on
programs whose whole image is smaller than DOOM's corridor.

**On the seek drum the same corridor costs +14.2%, and the mechanism is in
`seekrom.py`'s own protocol.** A seek is not free of the corridor — the docstring
says it plainly: a taken long jump costs "notice (< one row) + seek (~3 t/row) +
**the corridor flush**". The station emits a `-1` sentinel and the CPU *discards
every word already in flight* until it sees it. So the corridor's length is paid in
full by every long jump. A buffer is precisely a longer corridor, and it therefore
prices each seek at its own capacity: the seek drum's entire purpose is to stop
paying for words in front of the target, and the buffer puts 1,677 of them back.

The regression is monotone in corridor length in both tiers, which is the flush
model rather than noise. It also does not compose: adding `BRZ` to `SEEK_OPS` on
top is **super**-additive (+17.3% together, against +13.8% predicted from the two
alone), because each extra split family makes more seeks and every seek flushes
again.

**So `ROM_BUFFER` and `SEEK_DRUM` are antagonistic by construction**, and the two
features should never be named for the same slug. `ROM_BUFFER` stays empty.

This was proposed on the reasoning that DOOM is the biggest discard bill in the
repo and so the best case for a buffer, which was a good hypothesis — it was
tested rather than argued down, and the answer is that the seek drum got there
first. The drum already removes ~68% of the frame's discard, so the buffer is left
hiding the third the drum declined to take, and the flush it adds to every one of
the drum's 186 long jumps swamps that remainder several times over. The bigger the
program, the worse the trade, which is the opposite of the intuition.

### The other half of the same experiment: `BRZ` in `SEEK_OPS`

Measured at the same time because the two changes attack overlapping work. `BRZ`
delivers exactly the discard bill it was predicted to and still does not pay:

| build | box | ticks | Δ | share of frame-1 discard split |
|---|---|---|---|---|
| `JMPF` (shipped) | 295x269 | 591,485,564 | — | 263,260 / 387,532 = 67.9% |
| `JMPF`+`BRZ` | **309x271** | 588,983,630 | **-0.42%** | 327,429 / 387,532 = **84.5%** |

+16.6 points of the bill for four tenths of a percent of ticks. The extra 13-column
slab lifts the `mem_pad` floor 22 → 29, and that pad charges every memory
instruction the extra walk twice — DOOM's taped tier is memory-bound, so the pad
gives back nearly the whole discard win. The 14 columns are not recoverable by
re-folding: `rom_rows` 80, 84, 88, 92, 96, 100, 104 and 110 all land on width 309,
because the *store* binds the taped width, not the drum. 309 breaks the taped
machine's checked-in 300 ceiling. Not shipped.

`SEEK_THRESHOLD` does not want re-tuning once `BRZ` is in: the sweep is the same
plateau it is for `JMPF` alone (thr 64 → 84.7%, 128 → 84.7%, 192 → 84.6%, 256 →
84.5%, 384 → 82.9%), so 256 is still the corner.

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
