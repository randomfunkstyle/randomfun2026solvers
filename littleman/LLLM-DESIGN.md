# `little-little-little-man` — machine design

**Status: shipped and passing.** `tasks/solutions/little-little-little-man_ring.man`

    w x h = 144 x 202     area2 = 40,804     avg_ticks = 181,238.30
    score = 7.40e9        10 / 10 public cases on the engine, worst 404,556 ticks

Previously 159x202 at 1,373,035.30 ticks and **5.60e10** — the same charged side,
**7.6x** the speed. Everything below the first section is that change; the sections
after "CPU or ring?" are the earlier machine's record and are kept because their
measurements still bound what to try next.

| piece | where | state |
|---|---|---|
| LLLM reference interpreter | `lllm_sim.py`, `tests/test_lllm_sim.py` | all 10 public cases byte-exact |
| painter + LM-75 panel | `lllm_panel.py`, `tests/test_lllm_panel.py` | 22x26, area2 676; all 10 cases' frames replayed byte-identically on **both** validators |
| decode tables | `lllm_tables.py` | perfect hash over the glyphs *and* the wall-biased `+`/`-`, plus two 64-bit nibble magics |
| the machine's program | `lllm_ring.py` (`WORKER`, `simulate_worker`) | 68 blocks; reproduces every frame of all 10 public cases at token level |
| the worker's room layout | `lllm_layout.py` | one block to a row band, 28 routing channels; emits `--man/--html/--json` |

## The 7.6x, in the order it was found

Ticks on this machine are **block transitions**, not glyphs: 421k tokens against
13.7M ticks, so a block visit cost ~67 ticks of lane walking and a token cost 1.
Every change below is either "take a block visit out of a loop" or "make the walk
shorter", and the profile — `visits x (body + lane)` per CFG edge — is what chose
between them.

1. **The store lap was 92% of all block visits.** A tick rotated `16H + 1` words
   to reach one cell. Packing eight classes to a 64-bit word makes that ring 32
   words; holding the current word in the file and leaving a hole in the ring
   makes the *rotation* relative, so it is 6.6 word-moves a tick, not 184.
   The store loop is now **2%** of ticks.
2. **Rows are the charged side, and the panel was standing in them.** The 16x16
   panel and its painter are 26 rows; they sat in a 30-row band above the worker.
   Moved **east** of the worker — where the room's columns run out 60 short of its
   rows — the band is 8 rows. -22 rows for nothing.
3. **The bands were 21 columns apart.** The store's pipes were deliberately spread
   because a 257-word ring had to snake the whole band and its two pipes crossed.
   A 32-word ring is a short loop, so the pairs sit together and the file band
   starts 12 columns from the entry instead of 21 — nine ticks off *every* block
   visit.
4. **The setup decode was then 56% of ticks**, at eight block visits per program
   cell. It is five now: the wall-row flag is **added to the byte** before it is
   hashed rather than branched on (`WALL_BIAS`, and the hash is injective over
   the union), which deleted a branch, a block and the whole `WALL_CELL` path;
   the class lookup, the colour lookup and the `@` test merged into one block;
   and the word-boundary test became `ACC - 2^40` instead of `ACC >> 40`, because
   the *difference* is what both lanes want — add `2^40` back and you have the
   accumulator, keep it and you have the finished word — so neither lane has to
   fetch `ACC` a second time.

Two things that looked right and measured wrong, kept so they are not retried:

* **Handing the backtick columns out widest-literal-first.** A literal steps past
  every column already holding a backtick, and those blanks are walked; the decode
  block, which carries a 19-digit and a 15-digit magic, was spending ~120 of its
  204 walked cells on skipping. Planning it first does cut that (-1.8% ticks) and
  reshapes every other block's rows — the room came out four rows taller, +3.9% on
  a squared footprint. 7.85e9 against 7.69e9. Not done.
* **Widening the room past the point where wraps stop.** Free columns make the
  skipping cheaper in principle; measured across `IW` from +90 to +170 the ticks
  move by 0.05% and the height not at all, so the only effect is width — which
  starts costing the moment it passes the height.

## What has to happen

Round 1: `W H` then `W*H` ASCII codes of an LLLM program (row-major). Commit one
frame of the start state. Every later round: one `k` in 1..64 — step the
interpreted program `k` ticks or until it halts, commit one frame. <= 30 rounds,
<= 200 interpreted ticks per case, and the case ends after the round in which the
program halts.

## Measured facts that shaped the design

- `verify` on the native backend stops the tick clock at **the tick the final
  expected frame matched** (`fast_littleman_native.cpp:267`), which is exactly
  the contest rule. A machine that blocks forever after its last frame therefore
  costs nothing, and does not have to halt.
- That first reading of the leaderboard — "best LLLM score 1.3e12, so correctness
  first, footprint second, ticks a distant third" — was of an empty board. The
  finished one runs from 1.18e9, and at 5.60e10 this machine was 51x off it and
  5.1x off tenth. **Ticks turned out to be the whole game**, which is what the
  first section is about.
- Public cases: `W,H` from 4x4 to 16x16, <= 26 rounds, <= 182 interpreted ticks,
  `k` up to 64. Every case halts, always on its final round. The room fills the
  whole `W x H` grid in all 10.

## Display path — taken, not designed

`lllm_panel.py` ports the engine-proven painter + LM-75 panel from the unmerged
`snake-finish` branch (22x26, area2 676, 129 snake frames byte-identical). One
incoming pipe, protocol `n, (addr, colour) x n`, then the painter commits with
`SWAP 1` — which preserves both buffers and the cursor, so **a frame is a delta**.
Five geometry rules (ADDR=top, DATA=left, SWAP=bottom; the two-row band under a
south wall; ADDR <= DATA <= SWAP arrival order; distinct send columns because `s`
binds by Manhattan distance; the spawn placed so the man's first act is `r`) are
carried over verbatim with their assertion.

Consequence for the interpreter: **each interpreted tick repaints exactly two
pixels** — the vacated cell back to its stored colour, and the new cell to 9.
Only round 1 paints the program, and it paints exactly `W*H` pixels (every
program cell, with the man's cell as 9 instead of its colour), so `n = W*H` is
known before the first cell is read and the setup frame streams straight out of
the input with no buffering.

Per later round `n = 2k`, also known up front. If the program halts early the
remainder is padded with `(POS, 9)` pairs, which is idempotent — 6 glyphs
(`W s W s` under a `b`-counted loop) rather than a second painter protocol.

## Program store

Rows are padded to sixteen so that **a cell's store index is its display
address** — which is why the man's position is one number and a move is `+-1` /
`+-16` with no multiply anywhere in the tick loop. Eight of those cells are
packed into each ring word at five bits apiece, so the ring is a fixed 32 words
whatever `H` is, `POS / 8` is one glyph and yields both the word index and the
bit offset, and the padding rows past `H` are pushed as zero *words* rather than
decoded as cells.

`class` is one field, not a (colour, opcode) pair: the colour is a constant of
the dispatch lane the class lands on. Only the *setup* pass needs a colour for
an arbitrary glyph, and it gets it from the decode table.

The stored value is the class **biased so no third register is ever needed**:
non-digits store `j = class - 10` in 0..11, digits store `d + 12` in 12..21. The
ranges are disjoint and both positive, so one sign test on `v - 12` splits them,
and the decoder can push `j` to the store *before* looking the colour up from
it.

| class | meaning | colour |
|---|---|---|
| 0..9 | digit `d`: `AI = d` | 8 |
| 10 | space / vacated `@` | 0 |
| 11 | `M`: `BI = AI` | 12 |
| 12 | `+`: `AI += BI` | 10 |
| 13 | `-`: `AI -= BI` | 10 |
| 14 | `X`: rotate by sign(AI) | 3 |
| 15 | `H`: halt | 3 |
| 16..19 | heading N/E/S/W (`class - 16` **is** the direction index) | 3 |
| 20 | wall: halt | 4 |

Digits need no dispatch at all (`AI = class`, colour 8 for all ten), and the four
headings need none either (`DIR = class - 16`). That is 14 of the 21 classes
collapsing into two lanes.

Cell `n`'s word index and bit offset come from **one glyph**: `A = n, B = 8, /`
leaves the quotient in A and the remainder in B. Store index is `n = y*W + x`
(tight, no row padding); the display address is a separate `a = 16y + x`. Both
are updated by constants per move — E/W add +-1 to both, S/N add +-W to `n` and
+-16 to `a` — so no multiply runs inside the tick loop.

## Ring

    STORE  31 words in the pipe, the 32nd held in the file
    FILE   [ K, HALT, BI, AI, DIR, POS, CUR, WORD ]

`K` = ticks left this round, `HALT` = whether the interpreted man has stopped,
`AI`/`BI` = the interpreted registers, `DIR` = 0..3, `POS` = the man's display
address, `CUR` = which store word the file is holding, `WORD` = that word.
`TICK_LIVE` transiently holds a ninth slot, so the file's pipe pair must hold
>= 11.

One interpreted tick: `POS / 8` gives the word index `j` and the bit offset;
`j - CUR` decides whether the held word is already the right one (53% of ticks,
and then the store is not touched at all) or the ring has to turn
`(j - CUR - 1) mod 32`; the byte comes out with one `}` and one `&`.

## Setup decode

Rows 0 and H-1 are wall rows; in every other row `|` is unambiguous and anything
else strictly inside the room is an operation. So the only positional knowledge
the decoder needs is *"is this a wall row"*, tested once per row against the row
counter, not once per cell. `lllm_sim.py` derives the room rectangle from the
`|` columns instead, which is the strictly safer superset if a private case ever
puts a smaller room inside a padded grid; the machine assumes the room fills the
grid, and that assumption is recorded here as the single semantic risk.

## What the store actually holds — packing, and why it took two goes

The first design packed eight cells to a 64-bit word and **was abandoned**, for a
reason worth keeping because it was right about the obstacle and wrong about the
way round it: *a little man has two hands and a write-only backpack, and packing
needs three live values.* The setup loop must hold an accumulator, a display
address and a loop count while the decoder itself wants both hands, and
`acc*256 + class` cannot be formed while `class` occupies `B`.

What unblocks it is that the **register file is the third hand**, and two tricks
that cost nothing:

* the accumulator rides the file **pre-shifted**, so a cell's whole contribution
  is one `+` — no constant needs to be in `B` at the moment the class is;
* the word boundary is found by a **carry**, not a counter. `ACC` starts at
  `1 << 5` and shifts five bits a cell, so its sentinel reaches bit 40 on exactly
  the eighth cell. That matters because the backpack is already holding the row's
  cell count and there is no second counter to be had.

Five bits a class rather than eight is what keeps the sentinel inside a positive
64-bit literal: classes run 0..21, `8 x 5` is a 40-bit payload, and `ACC << 5`
at its widest is `2^45`.

Unpacked, the ring was `16H + 1` words and a tick turned all of them. Packed it is
32 words — and once it is 32 words the *relative* rotation below becomes possible,
which is the change that actually paid.

## The read that does not advance the ring

A `rr` takes a word out of the ring and an `sr` puts it back at the tail, so a
read **rotates the ring by one**. That is invisible on a full lap and fatal on a
relative one: the commonest step of all is "the same word again" — 53% of
interpreted ticks — and it would cost a whole 32-word lap to get back.

So the word does not go back. It stays in the file (`WORD`), `CUR` names it, and
the ring holds 31 words with a hole where it came from. A hit touches the store
not at all. A miss pushes the held word back — the hole is at the tail, which in
a cyclic order is exactly where it belongs — and rotates `(j - CUR - 1) mod 32`,
which is 0 for a step east across a word boundary and 1 for a step south.

Measured over the public cases, per interpreted tick:

| store | word-moves a tick |
|---|---|
| unpacked, full lap | 184 |
| packed 8/word, full lap | 23.0 |
| packed, relative, word returned | 22.4 — *the read's own rotation eats it all* |
| packed, relative, word **held** | **6.6** |

The middle row is the trap: relative rotation on its own buys nothing.

## CPU or ring? — measured, and it is a wash

The ring is charged from its **height**: 159x222, and `max(w,h)^2` bills only the
larger side, so the 63 columns of slack to the west are free and narrowing the
machine is worth exactly zero. All of the score is in rows.

Where the rows go, measured on `lllm_layout.build_room`:

    63 blocks -> 189 interior rows =  80 glyph rows + 109 lane-overhead rows
    53 of the 63 blocks have exactly ONE glyph row, and still cost 2 (non-branching)
    or 4 (branching) rows apiece

58% of the charged dimension is lane plumbing. It is structural, not sloppy: the
module docstring says it "lays one block to a row band and spends rows freely",
which was right when correctness was the risk.

`LLM-DESIGN.md` ran this comparison the other way and rejected the ring for the
sibling task, because **a CPU pays for control flow in ROM words, which are dense
data, not in rows**. So the question was worth asking here. It was answered with
`lm1.machine.build`, which synthesises a machine from a `Program` and reports its
dimensions — a CPU can therefore be priced against a synthetic program of the
right size and opcode mix *without writing the interpreter*.

**Footprint says yes.** Sweeping the ROM fold for the square optimum (ARCH 7.3b),
`tape_n=280`, `display=(16,16)`:

| interpreter | best `rom_rows` | w x h | `area2` |
|---|---|---|---|
| 300 instrs | 20 | 95x91 | 9,025 |
| 400 | 26 | 98x97 | 9,604 |
| 600 | 36 | 104x107 | 11,449 |
| 900 | 48 | 117x119 | 14,161 |

The fold matters as much as the program: 600 instructions is 19,600 at the default
fold and 11,449 swept, because the sweep lands the machine square at 104x107.

**Ticks say no, and ticks decide it.** The first pass of this estimate omitted ROM
recirculation and put a CPU at ~2.5M ticks. That was wrong. `LLM-DESIGN.md` prices
a taken branch at **12 ticks per ROM word it recirculates**, and at `P = 1,022`
words that term dominates everything else. Applying the full engine-measured model
against the *measured* workload — 58.5 interpreted ticks and 11.6 rounds a case,
not the 182 the round budgets suggest:

    setup, 256 program cells    4,017,664     <- 3.08M of it is recirculation alone
    the interpreted ticks        1,458,639     (24,934 a tick)
    rounds and frames              443,677
    TOTAL                        5,919,980     (cap 15,000,000)

| | `area2` | ticks | score |
|---|---|---|---|
| **the ring, measured** | 49,284 | 1,460,882 | **7.20e10** |
| CPU, 300 instrs | 9,025 | 5.92M | 5.34e10 |
| CPU, 600 instrs | 11,449 | 5.92M | **6.78e10** |
| CPU, 900 instrs | 14,161 | 5.92M | 8.38e10 — *worse* |

Break-even at `area2` 11,449 is 6.29M ticks against an estimated 5.92M, which is
inside the model's error bars. **A 4.3x smaller box buys 6%**, because the CPU is
4.1x slower, and it goes negative if the interpreter passes ~700 instructions.

The two machines cost almost exactly the same per interpreted tick — **24,934 for
the CPU against the ring's measured 24,972** — which is the cross-task finding in
miniature: the fetch-decode-return tax gives back what dense control flow wins.
A block machine avoids that tax by construction, and the ring already is one.

**Where the CPU's cost actually sits**, and why this is not a closed door: 68% of
it is the setup loop that loads 256 program cells, and 3.08M of that 4.02M is a
loop closure recirculating `P - body` words 256 times. This is exactly the
pathology `LLM-DESIGN.md` records costing the sibling 4.4M ticks a case doing
nothing. Code banks (ARCH 5.5, one looping ROM per subprogram) make a closure
nearly free and would take the CPU to ~2.9M ticks and **~3.3e10** — a real 2.2x.
But code banks are unbuilt (`ROM-RECIRCULATION.md` lists them still-open), so that
number prices a machine that does not exist.

### The branch that trades nothing away

Packing the ring is the only option that attacks the charged side without giving
up the block machine's tick profile. The overhead is 109 of 189 rows and 53 blocks
carry a single glyph row, so the headroom is large; halving it is 222 -> 168 rows,
`area2` 28,224 and **4.12e10 at unchanged ticks — 1.75x, and no new hardware.**

Cheapest slice of that, already located: 9 lane rows are *provably* dead. A `d`
branch declares only `pos`/`zero`, so its north free row can never be taken (3
blocks), and an `x` branch has no straight lane at all (`_straight_key` returns
`None`, 6 blocks) — yet `build_room` allocates all three overhead rows for every
branching block. Reclaiming them is 222 -> 213, `area2` 45,369, **-7.9%**.

Not done here: none of the three is built. The CPU bracket is measured on
synthetic programs of the right size, not on LLLM's code, and the tick figures are
the engine-measured cost model applied to a budgeted instruction mix rather than a
running interpreter. What the numbers do settle is the *ordering* — pack the ring
first, and only revisit the CPU if code banks land.

### Wrap elimination: measured, and it does not pay

After the lane-row slice the room is 181 interior rows — 80 glyph rows and 101 of
overhead. The glyph rows carry 17 rows of *wrapping* (63 blocks, 80 rows), so
removing wraps looked like the next 17 rows. It is not, and the reason is worth
recording because the number that suggested it was mine.

Attributing every wrap to the `_Pen` call that caused it:

    seek   14     the block revisits a pipe band it has already passed
    ensure  1     the row genuinely ran out of columns

Only the `ensure` wrap is a width problem, so **widening buys one row, not 17**.
The other 14 are the zone-order rule doing its job: a block whose tokens need
`ST` after `IO` must wrap, and that is a property of the token sequence in
`lllm_ring.WORKER`, not of the column geometry.

The zone *order* is a free parameter though, so all six permutations were swept,
moving `ZONE_COLS` and `PIPE_COL` together (moving either alone fails
`check_binding` — `'sq' at column 93 binds IO, wanted FI`) and sweeping the gap
between bands, since width is free while the machine is charged from its height:

| order | gap | glyph rows | interior h | `IW` |
|---|---|---|---|---|
| `FI->ST->IO` | 22 | **76** | **177** | 216 |
| `ST->FI->IO` (shipped) | 6 | 80 | 181 | 168 |

`FI->ST->IO` really does cut four rows. It also needs `IW` 216, which makes the
machine ~217x209 and `area2` **47,089 against the shipped 45,369** — spending
width past the height loses, and no smaller gap binds for that order. The other
four permutations do not bind at any gap.

Two further reasons not to force it: the shipped order puts `ST` first
deliberately, because `SEEK`/`REST` are the only hot loops and want to be one
column from the entry, so reordering is a tick risk on top of a footprint loss;
and the remaining 101 overhead rows are the one-block-per-row-band scheme itself,
which is the redesign this document already declines.

### And a wrap removed is not a row saved

The 14 `seek` wraps are program-shaped, so the next attempt was in
`lllm_ring.WORKER` rather than the layout: reorder a block's pipe ops so it stops
revisiting a band it has passed.

Which ops may move is not a free choice. `sq`/`rq` ride the **register file** and
`sr` the **store**, and both are rotating rings — the ring advances per operation,
so the order of those ops *is* which slot they touch, and they are pinned. Only
`sp` (painter) and `ri` (input) are separate channels that can be re-interleaved,
and then only when no value flows between them.

`WALL_CELL` is the clean case: `["ri", "L4", "sp", "L10", "sr", "m"]`, zones
IO, IO, ST — the worst possible order under `ST -> FI -> IO`. Both payloads are
constants loaded immediately before their send, so nothing flows from the input
read to either, and hoisting the store op to the front preserves every
per-channel sequence. Reordered to `["L10", "sr", "ri", "L4", "sp", "m"]` it is
**semantically clean — all 10 public cases still reproduce frame for frame at
token level.**

It also makes the machine *bigger*: interior 181 -> **182 rows**, 159x214,
`area2` 45,796 against the shipped 45,369. Reverted.

The reason is the finding worth keeping: **the row budget is coupled through
fall-through.** A block chains into its successor for free only when the
successor's first glyph row lands immediately below its straight lane, which
depends on its own glyph-row count. Removing a wrap changes that count, and a
chain that stops being adjacent becomes a routed lane — which costs a channel and
can cost more rows than the wrap saved.

So the 14 wraps are **not worth 14 rows**. Each has to be evaluated end to end on
the built room, not counted, and the first one tried came out negative. That does
not prove the other 13 all do, but it does mean the lead is worth much less than
its row count suggests, and each candidate costs a build to price.

## A fall-through that lands east costs nothing

The straight-lane row was 57 of the room's 101 overhead rows, and it exists for one
reason: to walk the man back **west** to the target's entry at `NC`. A west leg
reserves the row from the channel bank out to wherever he stopped, which is why
those rows can never be shared — every horizontal run in this room touches the west
bank, so one row carries one leg.

But the detour is only needed to *reach* the entry. When the straight successor is
the next block in order **and** its first glyph stands east of where the predecessor
stopped, the man does not need the entry at all: he drops one row at his own column
and keeps walking east into the glyphs. The row is never claimed, so the successor
moves up into it.

Both conditions carry weight. "Next in order" is what makes the target adjacent once
the row is skipped; "east of" is what lets him arrive by continuing rather than
doubling back over glyphs he has already run. 12 of the 40 unconditional edges
qualify, and 9 of those target `MOVE`, so adjacency decides which ones actually pay.

    159x213, 45,369, 1,425,943.50   ->   159x204, 41,616, 1,387,994.30
    6.47e10 -> 5.78e10, -10.7%

Ticks improved 2.7%, the same way the dead-lane slice did: a row that is not
allocated is also a row the returning man does not walk. Live-man count is
unchanged, so `men x ticks` stays out of play; worst case 4,932,048 against the 15M
cap. Cumulatively against the shipped 159x222 the two slices are **-19.8%**.

`_droppable` is deliberately a separate pass over `(order, plans)` rather than a
condition inside the allocator: whether a drop is possible depends on the *target's*
column, which the allocator does not know while it is still deciding rows.

## A block may start at its own band — but it may not be *entered* there

Starting every block at `CODE0` costs twice: the man walks dead columns to reach
his first band, and the block's first glyph sits at the far west, which is what
decides whether an incoming fall-through can drop east into it.

The zones run `ST -> FI -> IO` west to east, so a block may begin at its own first
band provided it never needs a band further west later — i.e. provided it holds no
`ST` op. 52 of 63 blocks qualify (27 begin at `FI`, 2 at `IO`, 23 have no pipe op
at all and keep `CODE0` because they are short enough that moving them buys no drop
their own length would not already allow).

    159x204, 41,616, 1,387,994.30   ->   159x202, 40,804, 1,373,035.30
    5.78e10 -> 5.60e10, -3.0%

**What does not work, measured:** moving each block's *entry* east to match its
start. It looks like the natural other half of the change and it fails, because the
channel bank is west: an arriving man turns east at his channel and runs to the
entry, so pushing the entry east lengthens that run across the very region the
lanes occupy — `entry run to L_DIGIT blocked at (35,41)`. Entries cannot move east
while channels stay west, and moving the channel bank east is a different machine.

That is the same wall the earlier attempts hit, stated in its general form: **every
horizontal run in this room touches the west bank, so one row carries one run.**
Rows are shareable only for traffic that never goes west, which is exactly what the
east-falling drop is and nothing else in the current scheme is.
