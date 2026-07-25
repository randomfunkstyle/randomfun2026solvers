# `little-little-little-man` — machine design

Status: design pinned, machine under construction. Reference interpreter and its
test are in `solvers/python/randomfun2026solvers/lllm_sim.py` /
`tests/test_lllm_sim.py` (all 10 public cases byte-exact).

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

One packed word per 8 program cells, `byte = class` in 0..20, cell `i` at bits
`8*i`. All words are positive (max byte 20), so `END = -1` is the ring's only
negative value and every store scan finds its end with a bare `X` — no length
counter, no padding slots, and a 4x4 program costs 2 words where 16x16 costs 32.

`class` is one field, not a (colour, opcode) pair: the colour is a constant of
the dispatch lane the class lands on, so it costs lane glyphs rather than 4 bits
per cell in every word.

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

Bytes accumulate `word = word*256 + class` in raster order, so cell `i` of a word
sits at bits `8*(7-i)`; a short final word is left-shifted by the shortfall,
which is a single `{`.
