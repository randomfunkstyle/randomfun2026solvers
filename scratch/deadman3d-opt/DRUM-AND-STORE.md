# What the CPU waits for: the drum is reducible, the store is not

Measured at `7b5d033` (men-v3 87,688,021 / 99.608 t/instr; taped 146,672,958).

## The conversion constant

A temporary `SEEK_NOTICE_PAD` hook (N idle cells between the drum reading the
request and emitting the sentinel) prices the notice path on the **real** machine.
`pad=0` reproduces 87,688,021 exactly.

| pad | ticks | Δ | Δ/pad |
|---|---|---|---|
| 50 | 87,978,621 | +290,600 | **5,812.0** |
| 100 | 88,269,221 | +581,200 | **5,812.0** |
| 200 | 88,850,421 | +1,162,400 | **5,812.0** |

**One tick off the drum's notice path is worth exactly 5,812 ticks of run
(0.0066%)** — dead linear to four significant figures. Any future drum change is
priced by one multiplication, with no need for the seek count.

## The drum: latency, and reducible

The drum is one man on a boustrophedon ROM, R=123 rows x data_w=421 columns,
10,592 words, 86.1 words/row. He notices only at a row-transition gadget (`q`
sets BP from the request pipe; `d` turns clockwise if BP>0), so notice
*granularity* is one row — but granularity is not the cost. The **walk from the
gadget to the station** is. Per taken seek (stride-1, 6 rounds, 2,744 seeks):

| part | t/seek | blocked |
|---|---|---|
| station + feeders | 372.8 | 96.5 |
| cascade collector row | 289.7 | 0 |
| seek riser | 123.0 | 0 |
| west + east cascade | 200.4 | 0 |
| west + east ladder | 93.1 | 0 |
| **notice path** | **1079.0** | **96.5** |

against CPU seek 1772.5 t/seek (927.4 blocked). **The drum's 1,079 ticks contain
essentially no blocked ticks — it is pure travel, and the CPU's 927 blocked ticks
are it waiting for that travel.** Latency, not throughput. That is why all four
CPU-side seek levers measured exactly 0.00%.

Two defects, each with direct per-cell evidence:

* The **seek riser reads exactly 2,744 on every one of its rows** — every taken
  seek walks all 123 cells unconditionally. It exists only because the cascade
  collects at the *bottom* (row R+2) while the station sits at the *top* (row -2):
  the man descends the whole ROM and climbs all the way back. **0.82%.**
* The **collector row reads 2,744 at col 1 but 1,819 at cols 100..430** — so
  **66.3% of seeks cross the full 431-cell row**, the east cascade walking the
  entire width to reach the single station in the west corner. **1.92%.** The
  station's odd-parity feeder then walks ~417 cells back east on ~half of seeks:
  a further **~1.38%**.

**Root cause in one line: a 421x123 room with exactly one station, in one corner,
that both the arrival and the departure path must reach.** It is also why
`rom_rows` is a flat, jagged curve — it trades the horizontal terms against the
vertical ones at roughly constant `data_w x R`.

Reducible: **~4.1% of the run identified**, ceiling **7.15%** if the notice path
went to zero. The fix is topological — a second station and riser at the east end,
or the station moved onto the collector with the ladders reversed to ascend — not
parametric.

## The store: the floor survives, but the old argument was wrong

84,065 reads, one every 257 ticks; the store is **idle 83% of the time**. Yet
**17.06% of the run** is the CPU blocked in store-read lanes (LD alone 8.51%),
and four straight-line lanes — LD, DIV, MOVA, ADD — agree **to the tick at 49.0
blocked per access**. Accesses are dependent (accumulator ISA), so there is no
memory-level parallelism and the full pipeline latency is exposed every time. The
"~11 ticks an access" in `STORE_TIER`'s docstring is a **throughput** number the
CPU never sees. Ratio 4.4x.

An earlier agent summed the stage laps and called each minimal. That is not the
argument. The correct one is that latency = stages x lap and **both factors are
forced**:

* *Every lap is at its ring floor.* A w x h ring has `2(w+h)-4` cells of which
  exactly 4 are corners that cannot do work, so k work glyphs need
  `2(w+h)-8 >= k`. Repeater: 6 glyphs (3 `r` + 3 `S`) -> `w+h >= 7` -> **lap 10**,
  as drawn. Decoder and cell: 8 glyphs -> **lap 12**, both 3x5. Collector and
  answer riser: 2 glyphs -> **lap 6**, 2x3. Nothing sits above its floor.
* *Stage count is forced.* Fan-out is 1 -> 14 repeaters -> 910 decoders, and a
  room may only own pipes attaching to its own walls, so one level cannot address
  910 targets. Two are required, and two symmetrically for fan-in. Decoder and
  cell cannot merge either: that needs two persistent registers (address, value)
  and A/B are the only two — **BP cannot substitute, because no glyph reads BP
  back into A** (`b`/`m`/`q`/`]` write it; `d`/`a`/`x` only branch on it), so a
  value parked in BP could never be sent.

The only remaining lever is overlap, which a single-man CPU cannot provide without
`Y`, and `Y` has no oracle on this machine.

`STORE_OPS = 1` re-derived on hires (the note admitted it was measured on
`deadman-3d`): ops=2 is a genuinely different router (23w against 13w) and gives
**byte-identical** ticks; ops=4 is +0.067%, ops=8 +0.586%. The walk home is
entirely off the critical path. The inherited value is right.

## Instrument caveat

`seek_bench.py`'s default `--word 6` is **not** its calibrated point — the
docstring pins `--word 12` — and even at 12 its discard reports ~960 words/seek
against the real machine's 38. Totals agree within 8%; the flush/discard split
does not. Trust it for totals only.
