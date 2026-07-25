# `little-little-little-man` — machine design

**Status: no grid yet.** Three of the four pieces are done and measured; the
fourth, laying the worker's control-flow graph out as a room, is not started.

| piece | where | state |
|---|---|---|
| LLLM reference interpreter | `lllm_sim.py`, `tests/test_lllm_sim.py` | all 10 public cases byte-exact |
| painter + LM-75 panel | `lllm_panel.py`, `tests/test_lllm_panel.py` | 22x26, area2 676; all 10 cases' frames replayed byte-identically on **both** validators |
| decode tables | `lllm_tables.py` | perfect hash + two 64-bit nibble magics, verified |
| the machine's program | `lllm_ring.py` (`WORKER`, `simulate_worker`) | 63 blocks, 614 glyph cells; reproduces every frame of all 10 public cases at token level |
| **the worker's room layout** | — | **not built** — see "What is left" |

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
- The leaderboard's best LLLM score is 1.3e12 against a 15M tick cap, i.e. a
  footprint of >= 295 on a side. Any working compact machine wins by orders of
  magnitude, so **correctness first, footprint second, ticks a distant third**.
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

One ring word per program cell, rows padded to sixteen so that **a cell's store
index is its display address** — which is why the man's position is one number
and a move is `+-1` / `+-16` with no multiply anywhere in the tick loop.
`16H + 1` words, so 65 for a 4x4 program and 257 for a 16x16 one. Every stored
value is non-negative, so `END = -1` is the ring's only negative value and the
scan that returns the store finds its end with a bare `X` — no length counter.

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

    [ K, N, A, DIR, AI, BI, W, W0 .. W(m-1), END = -1 ]

`K` = ticks left this round, `N`/`A` = store index / display address of the man,
`DIR` = 0..3, `AI`/`BI` = the interpreted registers, `W` = program width, then
`m = ceil(W*H/8)` store words, then the sentinel. Max resident 39 words, so the
ring pipe pair must hold >= 40.

One interpreted tick is one lap: read the head words, `/` the address into
(quotient, remainder), rotate `quotient` store words under a `b`-counted loop
(B carries `8*remainder` across it, since only A is clobbered by `r`), read and
immediately push back the target word, extract the class, then a sentinel-
terminated `r`/`X`/`s` loop returns the rest of the store and the head words are
rewritten behind it.

## Setup decode

Rows 0 and H-1 are wall rows; in every other row `|` is unambiguous and anything
else strictly inside the room is an operation. So the only positional knowledge
the decoder needs is *"is this a wall row"*, tested once per row against the row
counter, not once per cell. `lllm_sim.py` derives the room rectangle from the
`|` columns instead, which is the strictly safer superset if a private case ever
puts a smaller room inside a padded grid; the machine assumes the room fills the
grid, and that assumption is recorded here as the single semantic risk.

## What the store actually holds — packing was tried and dropped

The first design packed eight cells to a 64-bit word (32 words for a 16x16
program, a ring of ~35 pipe cells). It was abandoned, and the reason is worth
keeping: **a little man has two hands and a write-only backpack, and packing
needs three live values.** The inner setup loop must hold an accumulator, a
display address and a loop count while the decoder itself wants both hands;
`acc*256 + class` cannot be formed while `class` occupies `B`, and every escape
(`{`, `*`, `/`) needs its own constant in `B`. Every variant cost either a
second spill FIFO or a rotating-register discipline over five slots touched once
per cell.

Unpacked — **one class per ring word** — the setup cell body holds *nothing*
across the decode, because `s` leaves `A` alone: push the class, then look the
colour up from it. That is the single decision that made the program small
enough to write down. It costs a 257-cell ring instead of a 35-cell one and
about 8x the ticks, both of which are cheap against a leaderboard at 1.3e12.

The same reasoning fixed the decoder: the class table is indexed by a perfect
hash of the byte and the colour table by the *class offset the first lookup
produced*, because `&` wants `B = 15` and destroys the shift amount. Chained,
not parallel.

## What is left, and the two things that will shape it

The worker is 63 blocks / 614 glyph cells of straight runs plus 87 routed edges.
Two constraints measured on this CFG:

1. **Pipe binding is a column discipline, and it is nearly free here.** Six pipes
   (input, painter, store fwd/ret, file fwd/ret) all attach to one wall, so
   "nearest" reduces to nearest column at every row — `tcp_ring`'s rule. Of 285
   pipe ops, **246 are register-file ops, 26 are input/painter and 13 are
   store**, and there are only **22 intra-block zone switches**. So the bulk of
   the code sits in one column band and only 22 places need a walk. This was the
   risk I expected to dominate and it does not.
2. **Rows are what will cost.** A naive band layout — one block to a band of four
   rows (north lane, glyphs, south lane, fall-through lane) with a channel bank
   for the 87 edges — is ~245 rows by ~60 columns, `area2` ~60,000. At the
   ~40,000 avg ticks the token program implies that is ~2.4e9: 540x better than
   the leaderboard's 1.3e12, but 40x worse than the 22x26 panel floor deserves.
   Getting to a ~120-row build (two stacks, or lanes shared between adjacent
   bands) is worth roughly 4x, and real 2-D packing of 614 glyphs into a
   ~45x45 room would be worth ~30x.

Bytes accumulate `word = word*256 + class` in raster order in the packed variant,
so cell `i` of a word sits at bits `8*(7-i)`; a short final word is left-shifted
by the shortfall, which is a single `{`. Kept here only because the packed store
is the obvious footprint optimisation once the layout exists.
