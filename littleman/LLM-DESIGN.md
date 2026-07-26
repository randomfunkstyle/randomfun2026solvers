# `little-little-man` — machine design

**Status: judged, 28/28 cases.** `tasks/solutions/little-little-man_cpu.man`

    w x h = 203 x 204     area2 = 41,616     avgTicks = 22,258,080
    score = 926,292,239,445        14 public + 14 private cases, all passing

| piece | where | state |
|---|---|---|
| LLM reference interpreter | `llm_sim.py`, `tests/test_llm_sim.py` | all 14 public cases byte-exact |
| the interpreter program | `llm_lm1.py` -> `lm1/programs/little-little-man.asm` | 1,757 instructions, 3,377 ROM words |
| the assembler front end | `llm_asm.py` | slots, labels, indexed load/store |
| the machine | `lm1/machine.py` (`build`, `tape_n=427`, `display=(16,16)`) | 203x204, ROM folded to 84 rows |

## Why a CPU and not a dataflow ring

`little-little-little-man` — the same problem without rooms, pipes or a second man
— is solved on this repo's dataflow-ring pattern (`LLLM-DESIGN.md`): 63 blocks,
159x222, 1.46M ticks. LLM needs three to four times those blocks (a room finder, a
pipe walker, three men, two pipes), and that layout spends **~3.3 rows a block**,
so the same style would have landed near 600 rows — `area2` ~360,000 against this
machine's 41,616. A CPU pays for control flow in *ROM words*, which are dense
data, and LM-1 already generates the whole machine including the LM-75 panel.

The trade is ticks: a ring machine spends ~7,300 ticks an interpreted tick, this
one ~200,000. The product is what is scored, and the product came out ahead.

## The semantics, and where they were pinned

`llm_sim.py` is the oracle. Two rules the public cases do **not** decide, both
settled on the bundled wasm:

* **A pipe shifts as a train.** Resolving the shift from the destination end
  backwards means a solid run of values all advance on one tick; the alternative
  (simultaneous, pre-state, so only the front of a train moves) *also* reproduces
  all 14 public cases. `node lm.mjs tick` on a hand-built full pipe reports both
  values moving, so the train rule is the engine's.
* **Halting stops the pipes too.** When the last live man reaches an `H`, values
  still in flight do not keep moving. Unexercisable in the public data (every
  `H`-halting case has drained pipes by its last round), asserted anyway.

Three that *are* pinned, and each one fails a public case if broken: a wall freeze
completes the tick it happens in (`coin toss`, `pileup`, `cliffhanger`, `grand
tour`); nearest-pipe ties break by reading order (`switchboard`, `coin toss`); a
frozen program can leave a man standing on an unexecuted `H` (`cliffhanger`).

## What the machine stores

One slot a display cell, indexed by the **display address** `a = 16y + x`, so a
move is `+-1` / `+-16` and the same number addresses the panel. The slot holds
`colour * 32 + class`: the dispatch wants the class, a repaint wants the colour,
and one read gives both. That single decision deleted four inlined copies of an
eight-branch colour lookup.

Classes 0..9 are digits (the class *is* the value), 10 space, 11 `M`, 12 `+`,
13 `-`, 14 `X`, 15 `H`, 16..19 the four headings (`class - 16` **is** the
direction), 20 wall, 21 `s`, 22 `r`, 23 pipe body, 24..27 pipe arrowheads
(`class - 24` is the flow direction).

## Finding the rooms without a flood fill

A `+` … `-`* … `+` run in one row can only be a room's horizontal wall — pipe
bodies are bounded by arrowheads, never by `+`. So pass 1 collects those runs as
it streams the program in, and runs sharing an `(x0, x1)` **pair up in reading
order**: first is a top wall, next is its bottom. Stacked rooms of equal width
(`bounce house`) pair correctly because runs arrive in increasing `y`; a pair is
only accepted once the columns between them are checked to be `|`, which is what
stops an in-room `+--+` spelled out of arithmetic glyphs from faking a room.

Checked against all fourteen public programs: 1..3 rooms each, every `@` inside
exactly one, every ambiguous glyph outside every rectangle covered by a pipe walk,
no stray glyph outside a room.

Then the perimeters are **stamped** class 20 rather than tested for, because on
this machine a store write is nearly free and a read is not.

## Pipes

The walk starts at the arrowhead whose *backward* neighbour is a wall — found by
the stamping loop, which is already walking every wall cell and only has to look
one step further out — and follows the flow, arrowheads re-aiming it, until the
forward neighbour is a wall. It records the cells in flow order, so index 0 is the
cell an `s` writes and `len - 1` the cell an `r` pops, and `OCC[i]` is 0 or
`value + 10`.

The shift walks the **occupied window** `LO..HI` from the top down, moving a value
whenever the cell ahead is free *after that cell has already moved* — one pass,
exactly the train rule, and a pipe with nothing in it costs one read. The window
is a conservative superset that every pass re-tightens, with a self-heal for the
case where it goes stale.

Which pipe an `s`/`r` binds to is a property of the **cell**, not the tick, so
each man caches it: a man blocked on an `r` retries the same cell every tick, and
without the cache each retry re-ran the whole nearest-pipe search.

## The tick

1. `STOP` — set when a man's move landed him on a wall, or when the last live man
   reached an `H` — ends everything.
2. Shift both pipes.
3. Each live man executes his class and moves, unless he halted or blocked.

A move reads the class it lands on, keeps it for the next tick's dispatch, and
compares it to the encoded wall class. That is the whole freeze mechanism: the
tick in which a man steps onto a wall completes, and the next one is aborted before
anything changes — no separate pass, and the dispatch gets its class for free.

## Frames are painted as they happen

`SWAP 1` preserves both buffers, so a frame is a delta and **no round repaints
anything**. A man's move paints two pixels (the cell he leaves, back to the colour
of the class under it; his new cell, 9); a value's move paints two (6 and 14).
Pass 1 paints every glyph its own colour as it arrives, the wall stamp repaints
its cells 4, the pipe walk repaints its cells 6 — which is exactly the set of
cells whose colour pass 1 could not know, so there is no raster sweep at all.

## The profile, and what it bought

Sampled with ``tools/heatmap.mjs`` and attributed by ``lm1/profile.py`` to the
machine's own named regions — ``littleman/examples/llm-machine.heat.html`` is the
overlay, ``llm-machine.debug.html`` the same map with every region named.

``critical_runner()`` had to be overridden: it takes the *least-stalled* man, and
on a machine whose tape ring never stops walking that is the tape's own man, which
reports "tape 100%" with an all-zero CPU rollup (ARCH.md §4.1 warns about exactly
this). Pinned to the CPU's man, on ``hello neighbor``:

| region | share | what it is |
|---|---|---|
| ``cpu:lane:LD`` | 46.0% | one cell — the memory-response ``r``, waiting for the store |
| ``cpu:slab:JMPF`` | 21.4% | the jump slab's discard loop, i.e. recirculating ROM |
| ``cpu:lane:SUB`` | 7.4% | another store round trip |
| ``cpu:lane:IN`` | 7.3% | |
| ``cpu:lane:MOVA`` | 4.3% | |
| ``cpu:lane:LDA`` | 3.4% | |

**~61% store round trips, ~23% ROM recirculation** — the two terms the tick model
predicts, confirmed by where the man physically stands.

Costs, all measured on the engine rather than modelled (`ARCH.md` §4.1 is explicit
that the emulator's flat store cost is useless here):

| unit | cost |
|---|---|
| a store read | `8.0 * N` ticks, `N` = tape slots — 3,416 at `N = 427` |
| a store write | ~free, fire and forget |
| an instruction | ~143 ticks at decode depth 5 (~100 at depth 4) |
| **a taken branch** | **12 ticks per ROM word it recirculates** |

The first cut ran 40.6M ticks average and 58.5M worst — over the 50M cap, i.e. a
*failing* machine. What fixed it, in order of what it was worth:

| change | why | avg ticks |
|---|---|---|
| — | first working version | 40.6M |
| painting moved into pass 1, wall stamp and pipe walk repaint their own cells | deletes a 256-iteration raster sweep | 27.1M |
| a cell holds `colour * 32 + class` | one read serves dispatch and repaint | 24.8M |
| the row-end padding loop deleted | the panel starts black, so only the cursor has to move; the loop was 4.4M ticks a case doing nothing | 22.5M |
| one stop flag; each man caches his pipe binding | a blocked man re-ran the pipe search every tick | 21.4M |
| dispatch is one read and a cumulative subtract chain | `BRZ`/`BRN` leave ACC alone, so re-loading the class ten times was pure loss | 20.8M |

**A loop iteration costs `12 * (P - body)` ticks** — the ROM is a ring and a taken
branch recirculates the rest of it — which is why the profile's top line was a
padding loop that did nothing, and why the men and the pipes are *unrolled*: three
men and two pipes rolled would be five loop closures a tick.

Two measured negatives, both kept as evidence:

* **Packing four cells to a 64-bit word loses.** It takes the tape from 427 slots
  to 235 and a read from 3,416 to 1,880 ticks, but the read-modify-write on every
  stamped wall costs more than that saves — 23.6M against 21.4M — and it grows the
  ROM. Available behind `build_asm(packed_cells=True)`.
* **Rolling the three men into a loop is roughly score-neutral**: `P` drops ~830
  words (`area2` ~32,400, −22%) and the ~300 new loop closures a case cost ~+2.8M
  ticks. Modelled at 7.9e11 against the shipped 9.26e11 — inside the model's error
  bars, and it spends the tick-cap margin that the private cases were passed on.

## The tape had to be extended first

`machine.tape_block` could not size the store past **107 slots**: its ring was a
fixed 108 cells at every `n`, drawn as two L-shaped pipes. A 256-cell grid plus
the interpreter's own state is 427 slots, so the store came first — a serpentine
ring for anything the folded layouts cannot hold. 33x48 at `n = 427`, **zero width
cost**, 8.0 ticks a slot a read, up to 1,975 slots, and byte-identical output for
every `n <= 107`.

## The store backends, measured on this program

`machine.build` takes `store=` and there are three tiers.  Asked directly, at this
program's 427 slots:

| `store=` | result | area2 |
|---|---|---|
| `"tape"` | **203x204** | **41,616** |
| `"men"` | `ValueError: a line STORE block wants 1..24 cells, not 427` | — |
| `"men-y"` | 1381x204 | 1,907,161 |

So the man-memory is a **latency win and a width loss**: `men-y` costs
`358 + 3.07 * N` a read against the tape's `8.0 * N` — about half, on a machine
where the heat map says 61% of the CPU's time is store round trips — but its block
is `83 + 3 * N` columns wide, so the box goes 46x and the score with it.  There is
no `N` where that trade comes out ahead here, and the repo's own sweep found the
same on six other programs (`OPTIMIZATION.md`): `tcp` was the only one whose ticks
improved at all, by 7.4%, and its 253-column layout still lost 399% on the
objective.

`memory_men_grid` — the newest man-memory — is a different geometry, and the one
where **the shape is a free parameter**: `M` columns of `N` cells, no bit
alignment and no stride, so `M` and `N` are independent.  Measured on the builder:

    width  = 27 * M   exactly   (2 cols -> 54, 4 -> 108, 8 -> 216)
    height = 32 + 3 * N         (10 cells -> 62, 25 -> 107, 27 -> 113)

That still cannot hold this program's store, and the reason is per-word area
rather than aspect ratio.  Best shape for 427 words is 7x61 -> 189x215, `area2`
**46,225** — larger than the entire machine the tape sits inside (41,616) — because
a grid spends **81 cells a word** where a folded pipe tape spends **3.7**.

What it is actually for is the *second tier* `ARCH.md` §4.1 calls not-optional: a
man cell answers in `22 + 14 * depth` ticks against `8.0 * N` for the whole tape,
so a **small, hot** working set belongs in one.  This program's hot scalars — the
three men, the two pipes, the loop cursors — are ~40 words and ~2,000 of the 3,350
reads a case.  A 3x14 tier is 81x74 and fits the dead space east of the CPU band;
moving those reads off a 3,416-tick tape onto a ~220-tick tier is ~6M ticks, i.e.
~30% of the machine, and the biggest single number left anywhere in this document.

The builder's shape limit is now **fixed** (a column base past 99 needed a ninth
column for its third digit), so the block builds and answers on the engine at any
size this needs — 7x61 = 427 cells round-trips its highest address.  And its cost
is measured, by `judge` at one read against nine:

| shape | cells | first read | marginal read |
|---|---|---|---|
| 3x14 | 42 | **118** | 15 |
| 4x25 | 100 | 173 | 15 |
| 7x61 | 427 | 355 | 15 |

The marginal cost is **15 ticks whatever the size** — it pipelines — but a CPU read
*blocks*, so what it would pay is the first-read latency: ~118 ticks against the
tape's **3,416**.  Which gives two designs, and only one of them is worth building:

| | reads a case | avg ticks | footprint | score |
|---|---|---|---|---|
| today, 427-slot tape | 10.9M | 20.3M | 41,616 | 8.44e11 |
| **A: 3x14 hot tier + tape** | 1.6M | **11.0M** | **41,616** (81x74 fits the free space east of the CPU) | **4.6e11** |
| B: whole store as 7x61 | 1.3M | 10.7M | ~73,984 (the block is 196x215) | 7.9e11 |

B replaces the tape and pays for it in width; **A keeps the tape for the cold
256-cell grid and puts only the ~40 hot scalars in men**, which is free on
footprint because the machine already has a 93x111 hole east of the CPU band.

What A needs is the one thing the CPU does not have: **a second store seam**.  The
adapter routes on the *sign* of the request word today (`+a` read, `-a` write); a
second tier means routing on its *range* as well, plus `machine.py` placing the
block and binding four more pipes.  That is the whole remaining job, and it is
worth ~45% of the score.

The version of the idea that *would* pay is `snake-ring`'s: not swapping the
store, but taking the big structure *out* of it.  The 256-cell grid in a
coprocessor behind `SND`/`RCV` leaves ~170 scalars in the tape, and a read falls
from 3,416 to ~1,360 ticks with no change to the box — which is how `snake` went
from a 66-slot tape at 523 ticks a read to 9 slots at ~180.

## The two-tier store: 2.36x faster in ticks, rejected on wall clock

Built, engine-verified, submitted, **refused**: `4/28`, the runner reporting
`10 time-cap` on the public cases and `14 time-cap` on the private ones. Worth
recording in full, because everything about it looked right.

The change: the cold 256-word program grid stays on the tape, the 52 hottest slots
(90.6% of all reads) move to a man-memory tier reached through a range-routing
adapter and a response merger. Measured across all 14 public cases:

| | footprint | avg ticks | max ticks | score |
|---|---:|---:|---:|---:|
| one tape | 41,616 | 20,275,186 | 31,809,643 | 8.44e11 |
| + tier | 41,616 | **8,605,207** | 13,676,774 | **3.58e11** |

It is not wrong: on the reference wasm, under engine round-gating, `first steps`
matches 4/4 frames at 1,492,654 ticks — the same number the native validator
gives. The frames are right and the ticks are real.

**What kills it is that a stored word is a live man.**

| | live men | ticks a case | runner-ticks |
|---|---:|---:|---:|
| one 427-slot tape | 5 | 20,275,186 | 0.10bn |
| + 52-slot tier | **114** | 8,605,207 | **0.98bn** |

Score counts ticks; the grader spends wall clock, and wall clock goes as
`runners x ticks`. The tier added 109 men — 52 cells, their decoders, repeaters,
router, collector, relay — and the simulator steps every one of them on every tick
for the whole run, read or not. Ticks fell 2.36x, simulator work rose 9.7x.

A pipe tape stores 427 words in **zero** runners, because values move without a man.
That asymmetry is the whole result, and it is now in `ARCH.md` §4.1 as a third
axis on the tier table.

The machine kept here is therefore the single-tape one. `build_machine(hot=HOT)`
still builds the tier version for anyone who wants to measure it, and
`tests/test_lm1_two_tier.py` still proves the seam works.

### Re-measured on the compacted CPU, and refused again

Re-placing the seam against main's compacted routing made it **free in area** — the
tier had cost 203x204, and at 26 slots it now fits inside the same 200x199 box. The
answer lanes were the whole problem: they were hard-coded to `cy - 1` / `cy - 3`, the
band *above* the CPU, which the ROM's fold now occupies (`collision at (166, 88)` on
every pad pair). Moved below the CPU's top row into the merger's own rows they need
no climb at all, and `_ANS_BAND` is what buys them.

That bought real ticks and changed nothing about the verdict:

| tier | live men | avg ticks | worst runner-ticks | judged |
|---|---:|---:|---:|---|
| none | 5 | 19,354,082 | 0.151bn | **28/28** |
| 10 slots | 30 | 16,743,610 | 0.768bn | **11/28**, time-cap |
| 26 slots | 62 | 10,001,880 | 0.946bn | not sent |

So the earlier reading of the ceiling — "0.73bn passed, 0.87bn refused" — is **wrong**,
and it was wrong because it inferred a per-case bound from sorting one build's costs.
0.768bn is refused. The bound is one-sided and much tighter: **0.151bn passes,
0.768bn does not.**

## Banking the *tape* instead: 2.12x, judged 28/28

`men x ticks` is the constraint, and the man-memory loses because it pays men *per
slot*. A pipe tape does not: it has **four little men at n=52 and four at n=427**,
constant in size, because a stored word is a value in a rotating ring. Its cost is
`8.0 * N` — so splitting the store into a small hot ring and the full cold one buys
the latency win on the axis that scores and barely touches the axis that refuses.

The seam already had the parts. `two_tier_adapter` routes by address range and the
`R` merger takes from any incoming pipe; only the block in the hot slot changed
(`TIER_PIPE_BANK`, `_PipeBank`). `memory_banked_machine.py` is the same protocol,
validated earlier on the `memory` problem.

| store | live men | avg ticks | area2 | worst runner-ticks | judged |
|---|---:|---:|---:|---:|---|
| one 427-slot tape | 5 | 19,354,082 | 40,000 | 0.151bn | 28/28, **848,506,331,429** |
| hot 52 + cold 427 | 8 | 7,156,214 | 40,804 | 0.083bn | `fatal: wall` x4 locally |
| **hot 104 + cold 427** | **8** | **8,788,539** | 41,616 | **0.110bn** | 28/28, **400,740,741,396** |
| hot 208 + cold 427 | 8 | 11,528,142 | 41,616 | 0.143bn | not sent |

**2.12x, and it was never a gamble**: 0.110bn is *below* the 0.151bn of the machine
that was already passing 28/28, so the shipped grid does strictly less simulator work
than its predecessor while running 2.2x fewer ticks. The 52-slot bank is the
`TAPE_SIZE` trap — a ring sized to exactly its top address stalls rather than faults.

The overlay is `examples/llm-banked-tape.debug.html`, rendered from the submitted grid.

### The local -> judge tick factor is 1.096

Worth stating because it removes the guesswork: local `optimize.verify` averages the
**14 public** cases and the judge averages **28**, and the private half runs ~19%
heavier, so `judge_avg = 1.096 x local_avg` — confirmed on four submissions and used
to predict this one before sending it (estimated 9,632k, judge measured 9,629,487).

### Two dead ends, measured

* **`packed_cells=True`** — eight display cells to a word takes the tape 427 -> 239
  and a read 3,416 -> 1,912, at **no extra men**, which is exactly the right shape.
  It is still a loss: the unpack code takes `P` 3,377 -> 3,919, so the best fold is
  207x213 (45,369, swept) and avg ticks go *up* to 20,889,724. Score 9.48e11 against
  7.74e11. The shorter tape does not pay for the instructions that shorten it.
* **`grid_side_block` for the tier** — the side-ported man-memory puts both stubs on
  one wall, which is the right idea for a seam whose merger is west of the block.
  It does not place: `collision at (109, 93)`, because `_two_tier` still routes the
  answer as if it left the east side. Parked behind `TIER_SIDE_PORTS = False`.
## Where the box actually goes: half of it is empty

Measured on the 195x196 machine — 38,220 cells in the bounding box, **18,467 used
(48%)**:

| band | columns | rows | cells | used |
|---|---|---|---:|---:|
| ROM, west | 0..104 | 0..91 | 9,660 | 88% |
| ROM, east | 105..194 | 0..91 | 8,280 | 89% |
| CPU stack, west | 0..104 | 92..195 | 10,920 | 23% |
| **CPU stack, east** | **105..194** | **92..195** | **9,360** | **0%** |

**A quarter of the bounding box is one dead rectangle**, because the layout is a
*stack*: the ROM spans the full width on top, the CPU stack is only 105 columns
wide underneath, and nothing was ever placed to the right of it.

That also explains why compacting the CPU's *columns* buys nothing today while
compacting its *rows* pays: width is set by the ROM's fold (195) against a stack
that ends at x=104, so only rows convert into score. It is a property of the pose,
not of the CPU.

The ROM is the element that can fill the hole — it is one folded pipe snake with
no natural shape, so it can be **L-shaped**: a top band plus a leg down the east
side beside the CPU. That makes the fold a two-variable problem, and the optimum
moves a long way:

| ROM width | east leg | top rows | height | box | area2 |
|---|---|---:|---:|---|---:|
| 195 (today) | — | 88 | 196 | 195x196 | 38,416 |
| 160 | 55x104 | 76 | 180 | 180x180 | 32,400 |
| **170** | **65x104** | **66** | **170** | **170x170** | **28,900** |
| 175 | 70x104 | 61 | 165 | 175x175 | 30,625 |

**~25% at the optimum**, and the floor is lower still: 18,467 used cells is a
136x136 square, so even 70% packing efficiency would be ~163 a side.

Two things make it real work rather than a parameter change: the ROM is one
looping ring, so an L-shaped fold has to keep the boustrophedon continuous around
the corner (`rom.build_packed_rom` folds into a plain rectangle and carries a
per-column backtick-parity invariant), and the fetch pipe into the CPU still has
to bind by nearest column.

## What is left on the table

* **Code banks (ARCH.md §5.5).** Every one of the ~470 taken branches a case
  recirculates ~1,900 ROM words. Two ROMs — one for setup, one for the tick loop —
  would roughly halve that (~9.2M ticks of the 22.2M), and `two-roms.man` already
  proves the hardware. It needs a second fetch site in `build_cpu`.
* **Sixteen opcodes.** At 19 the decode trie is depth 5, which costs 32 CPU rows
  and ~43% on every instruction's issue cost. Folding `DSPA`/`DSPD`/`DSPS` into
  one `SND` to a paint-only coprocessor, and dropping `MULI`, would land exactly
  on 16.
* **Setup is ~1,500 of the 3,395 ROM words**, and every one of them is paid by
  every taken branch in the tick loop.
