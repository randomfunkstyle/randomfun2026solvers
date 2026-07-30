#!/usr/bin/env python3
"""The optimization catalogue — every idea we tried, grouped by what it attacks.

This module is **data**, not code. `make_optimizations.py` renders it to
`OPTIMIZATIONS.md` and `optimizations.html`, and `optimization_debug.py` runs a
debug pass over the artifacts each entry names and writes
`littleman/optimizations.debug.json`, which the HTML then embeds.

Every entry carries:

* ``date``      — when it landed / when it was measured (ISO, from the commit)
* ``group``     — which resource it attacks (see ``GROUPS``)
* ``block``     — the sub-system inside that group
* ``status``    — ``shipped`` · ``rejected`` (measured, kept as evidence) ·
                  ``parked`` (designed, never built) · ``superseded``
* ``era``       — ``contest`` or ``post-contest``
* ``before`` / ``after`` — ASCII, or ``after`` alone when there was no "before"
* ``numbers``   — the measurement that decided it
* ``alternatives`` — what else was on the table, and why it lost

Nothing here is a projection unless it says so: every number is either the
judge's, the reference wasm engine's, or the native validator's.
"""

# ruff: noqa: E501 -- this file is a data table; wrapping the ASCII figures and the
# measurement rows would make them unreadable.

from __future__ import annotations

# ── the top-level split: what resource does the idea attack? ─────────────────

GROUPS: list[dict] = [
    {
        "key": "footprint",
        "title": "Footprint — the squared bounding box",
        "blurb": "Score is `max(w,h)² × avgTicks`, so one dimension is billed and it is squared. Everything here moves the box, not the clock.",
        "law": "Only the longer side is charged. Narrowing an already-narrow machine is worth exactly zero; growing the short side to shrink the long one is the move.",
    },
    {
        "key": "memory",
        "title": "Memory — stores, tiers and what a stored word costs",
        "blurb": "Four mechanisms with wildly different costs: a pipe tape (dense, slow, zero runners), register cells (tiny, fast), man-memory (fast, huge, one live man per word) and rotate-only STREAM rings.",
        "law": "A pipe tape stores 427 words in **zero** runners; a man-memory stores one word per live man. Score counts ticks, the grader spends `runners × ticks`.",
    },
    {
        "key": "cpu",
        "title": "CPU — decode, lanes and instruction issue",
        "blurb": "One issued LM-1 instruction is 46 ticks against a walked glyph's 1. Everything here makes an instruction cheaper, or makes the machine that issues it smaller.",
        "law": "`k = ceil(log2 |opcodes used|)`. Sixteen opcodes is free; the seventeenth costs a whole trie level plus ~32 lane rows.",
    },
    {
        "key": "rom",
        "title": "ROM — program supply and the cost of a taken branch",
        "blurb": "There is no program counter. The ROM man walks a closed circuit and re-emits every word forever, so a jump is a discard loop and costs `P − body` words.",
        "law": "Recirculation is 36–52% of six different CPU machines. `discard cost/word = max(CPU loop ticks, ROM emission ticks/word)` — fix the producer before the consumer.",
    },
    {
        "key": "dataflow",
        "title": "Dataflow — deleting the CPU entirely",
        "blurb": "A hand-built grid pays 1 tick per walked glyph where the CPU pays 46 per instruction, and it deletes the ROM, the trie, the lane band, the adapter and the tape from the box as well.",
        "law": "A ring is a *memory* optimisation; deleting the CPU is a *compute* one. Keep the two levers separate when costing anything.",
    },
    {
        "key": "io",
        "title": "I/O, display and coprocessors",
        "blurb": "The LM-75 panel and the write-only coprocessor: the two ways work leaves the CPU without the CPU having to wait for it.",
        "law": "A coprocessor must not answer back — an incoming pipe is a rival for every `r` in the CPU, including the jump slab's ROM read.",
    },
    {
        "key": "parallel",
        "title": "Split (`Y`) and parallelism",
        "blurb": "`Y` duplicates a man's A/B/BP into two runners with no new room and no new pipe — and no join instruction, no shared pipe values, and a hard 65,536-runner cap.",
        "law": "A grid graph is bipartite, so two children of one loop are always an even number of cells apart — same phase, forever. Parity kills most pipelining ideas before effort does.",
    },
    {
        "key": "tooling",
        "title": "Measurement, verification and process",
        "blurb": "Three of the largest wins in the contest were corrections to numbers that had been assumed. These are the instruments that found them.",
        "law": "Re-measure every inherited constant. Build the verifier you can afford to run — a sweep you cannot run is a sweep you do not do.",
    },
]

STATUS_LABEL = {
    "shipped": "SHIPPED",
    "rejected": "MEASURED · NOT TAKEN",
    "parked": "DESIGNED · NOT BUILT",
    "superseded": "SHIPPED · LATER SUPERSEDED",
}

ENTRIES: list[dict] = []

# ═══════════════════════════════════════════════════════════════════════════
# FOOTPRINT
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "fp-squared-objective",
        "date": "2026-07-24",
        "group": "footprint",
        "block": "The objective itself",
        "title": "Optimise the product, and only the longer side is billed",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "Score is `max(width, height)² × averageTicks`. Slack in the *shorter* dimension is free, so packing should deliberately grow the small side to shrink the large one — the opposite of what a conventional placer does. This single rule is behind every fold sweep in this document.",
        "after": """
        w = 46                          w = 38
  +----------------+            +------------+
  |  CPU + lanes   | h=31       | CPU+lanes  | h=31
  |                |            | ROM folded |
  |  (15 rows of   |            | into the   |
  |   height idle) |            | dead rows  |
  +----------------+            +------------+
   max(46,31)^2 = 2116           max(38,31)^2 = 1444
   ^^^ 15 free rows paid for      ^^^ -32% for no logic change
""",
        "numbers": [("first CPU box", "46x31 = 2,116", "38x31 = 1,444", "-32%")],
        "alternatives": [
            {"name": "hand `layout_graph` the packing", "verdict": "blocked", "why": "the router optimises for *short* pipes, and the code ring needs a *minimum* pipe length — a naive packing pass silently deadlocks a looping program. Both blockers went away once the looping ROM replaced the ring."},
        ],
        "commits": ["42519e9", "7fdb4de"],
        "sources": ["littleman/ARCH.md §7.4", "littleman/GRADING.md"],
        "artifacts": [],
    },
    {
        "id": "fp-triangle-8x8",
        "date": "2026-07-25",
        "group": "footprint",
        "block": "Hand-drawn grids",
        "title": "`triangle` 9x9 -> 8x8: wrap the pipes outside the room",
        "status": "shipped",
        "era": "contest",
        "problems": ["triangle"],
        "what": "Shift the compute room one column right; the freed left edge carries the input pipe *up the outside* at the 2-cell minimum while the output pipe drops the right edge, also 2 cells. Both I/O rooms then abut below with no gap column. Two structural facts made it legal, neither obvious: a pipe may attach at a room's **corner**, and two rooms may abut with **no gap column**.",
        "before": """
  +-----+
  |@rM*v|      9x9, stacked I/O, 3-row serpentine
  |v2M+<|      score 1,053
  |>W/sH|
  +-----+
   +-+ +-+
   |I| |O|
   +-+ +-+
""",
        "after": """
  +-----+
  |@rM*v|
  |v2M+<|
 >|>W/sH|      8x8 = 64 footprint, 15 ticks
 ^+-----+      score 960  -- 2 ticks SLOWER and still better,
 +-++-+v       because a 3-row serpentine costs 4 turn cells
 |I||O|<       instead of 2
 +-++-+
""",
        "numbers": [
            ("score", "1,053 (9x9)", "960 (8x8)", "-8.8%"),
            ("earlier", "6,912 (24x3 hand-drawn)", "1,134 (9x9 fold)", "-84%"),
            ("vs the generated CPU", "471,744", "960", "**492x**"),
        ],
        "alternatives": [
            {"name": "7x7", "verdict": "impossible", "why": "4 interior columns x 3 rows leaves 9 instruction cells after `@` and 4 turns; 4 interior rows costs 6 turns and pushes the height back to 9."},
        ],
        "commits": ["ae5fe6c", "a073afb", "9c3dad0"],
        "sources": ["littleman/ARCH.md §7.4b"],
        "artifacts": ["solutions/triangle/000000000000960_triangle.man"],
    },
    {
        "id": "fp-rom-fold-square",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "The ROM fold",
        "title": "The optimum machine is square, and the fold is how you reach it",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man", "snake", "brackets", "gradebook", "matmul"],
        "what": "The ROM fold trades width for height monotonically — more `rom_rows` is narrower and taller — so its optimum is not 'minimum width', it is **wherever the two sides cross**. Swept per machine and pinned by a test, because any change to the CPU, ROM, store or tape invalidates it and nothing else in the suite notices when it drifts.",
        "after": """
  rom_rows   box        area2
     88     197x192    38,809      wider than tall
     89     196x193    38,416
  -> 90     192x194    37,636   <- the crossing: last fold where w > h
     91     190x195    38,025      now taller than wide, and losing
""",
        "numbers": [
            ("little-little-man box", "197x192 = 38,809", "192x194 = 37,636", "-3.0%"),
            ("snake-handmade", "121x100 = 1.74e9", "102x102 = 1.22e9", "-30%"),
        ],
        "alternatives": [
            {"name": "sweep the fold on `tcp` / `gradebook` / `sudoku-validity`", "verdict": "flatlined", "why": "width sat at 103 / 107 / 77 for the entire range — the 33-column store tape had already set it. **A flat sweep is not evidence of an optimum; it is evidence you are sweeping the wrong thing.** Read `Machine.regions` first."},
        ],
        "commits": ["53251eb", "67b9c3a", "6f55e64"],
        "sources": ["littleman/ARCH.md §7.3b"],
        "artifacts": [],
    },
    {
        "id": "fp-unstack-memory",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "Block placement",
        "title": "Move the memory subsystem off the east chain — the fold comes back to life",
        "status": "shipped",
        "era": "contest",
        "problems": ["tcp", "gradebook"],
        "what": "In all four CPU-generated machines the easternmost region was the same fixed 33x34 store tape, and `tape east == width` exactly. The ROM had been folding narrower and narrower **behind a wall the tape had already set**. `MEM_PLACE` / `mem_offset` moved it off that chain.",
        "before": """
  +--------- ROM (folding narrower, uselessly) ----+
  | CPU | adapter |  TAPE 33 cols  |   <- width is set HERE
  +------------------------------------------------+
""",
        "after": """
  +--- ROM (fold now binds) ---+
  | CPU | adapter |
  +--------------+
  |    TAPE      |     <- moved below, off the east chain
  +--------------+
""",
        "numbers": [
            ("tcp", "103x57 = 10,609", "80x80 = 6,400", "-40%"),
            ("gradebook", "107x85 = 11,449", "95x93 = 9,025", "-21%"),
            ("fold re-awoke", "tcp 3 rows", "tcp 2, gradebook 32 -> 40", ""),
        ],
        "alternatives": [
            {"name": "the same restructure on `matmul` / `sudoku-validity`", "verdict": "correctly left alone", "why": "`matmul` is charged from its *height* (the stream ring bottoms out at y=86), so narrowing width is worth zero; `sudoku-validity` is already square and the best reachable was 78x76, worse than the 77x77 it had. **A width-buying restructure only wins on a machine wider than it is tall.**"},
        ],
        "commits": ["74f4d20"],
        "sources": ["littleman/ARCH.md §7.3b"],
        "artifacts": [],
    },
    {
        "id": "fp-dead-lines",
        "date": "2026-07-25",
        "group": "footprint",
        "block": "Deterministic AST optimisation",
        "title": "`mancompact` — dead-line elimination, gated on bindings and cases",
        "status": "shipped",
        "era": "contest",
        "problems": ["memory", "plotter", "tcp", "sudoku-validity", "gradebook"],
        "what": "A pass that deletes a whole grid row/column when nothing occupies it — but only after re-checking that every `s`/`r` still binds to the same pipe and every public case still passes. A grid that still *loads* is not sufficient evidence: nearest-pipe binding is geometry, and a moved room reads the wrong pipe with no error at all.",
        "after": """
  before                        after
  +----------+                  +---------+
  |@..s....v |                  |@.s....v |
  |          |   <- blank row   |.^....s< |
  |.^....s<  |      deleted     +---------+
  +----------+                  one column and one row gone,
                                bindings re-asserted, cases re-run
""",
        "numbers": [("judge-verified", "5 wins", "415M points", "from deleting 10 blank lines")],
        "alternatives": [
            {"name": "infer a pipe's minimum from its visual length", "verdict": "forbidden", "why": "capacity is a contract. A conduit may be shortened; a display feed's terminal cell selects a *port*; a recirculating ring's **group total** is its capacity and may not fall below the declared requirement. Under-capacity deadlocks **silently**."},
        ],
        "commits": ["67c7036", "54f9948"],
        "sources": ["littleman/OPTIMIZATION.md"],
        "artifacts": [],
    },
    {
        "id": "fp-ast-moves",
        "date": "2026-07-25",
        "group": "footprint",
        "block": "Deterministic AST optimisation",
        "title": "`manmoves` / `manopt` / `manroute` — structural moves searched, not hand-poked",
        "status": "shipped",
        "era": "contest",
        "problems": ["memory", "gradebook", "snake", "tcp", "brackets"],
        "what": "Model the grid as a mutable AST (rooms, pipes, opaque bodies), then search over row/column cuts, room placement, declared-pipe rerouting and loop squashing. Every accepted move is validated against the *whole problem*, and the acceptance policy is one policy for every input: AST renders, topology and bindings preserved, declared group capacities preserved, all public cases pass, and the measured objective strictly improves.",
        "numbers": [
            ("memory", "61,904,085", "59,244,809", "-4.3%"),
            ("memory + ring buffer", "59,244,809", "55,105,622", "-7.5%"),
            ("gradebook AST10", "3,912,488,501", "3,907,925,049", "-0.12%"),
            ("brackets ROM+AST", "230,976,825", "225,289,528", "-2.41%"),
        ],
        "alternatives": [
            {"name": "per-case patches on public examples", "verdict": "banned by construction", "why": "public cases are an acceptance *gate* for a candidate, never a source of coordinate-level tuning. A failed optimisation is then a useful structural result rather than an invitation to patch one case."},
            {"name": "`--speed-for-space` (non-compacting moves)", "verdict": "kept separate", "why": "a much larger search; making every compaction run pay for it silently was the wrong default."},
        ],
        "commits": ["563cf8e", "24de377", "d8ed84f"],
        "sources": ["littleman/OPTIMIZATION.md"],
        "artifacts": [],
    },
    {
        "id": "fp-output-room-off-axis",
        "date": "2026-07-25",
        "group": "footprint",
        "block": "Block placement",
        "title": "Move the output room off the binding axis",
        "status": "shipped",
        "era": "contest",
        "problems": ["gradebook", "sudoku-validity"],
        "what": "The `O` room and its pipe were standing in the dimension that sets the score. Moving them into the free dimension is pure profit — the room is the same size, it is just no longer billed.",
        "numbers": [("gradebook", "", "-988M", ""), ("sudoku-validity", "", "-335M", "")],
        "alternatives": [],
        "commits": ["aaecb35"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "fp-lllm-lane-rows",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "Block-machine row plumbing",
        "title": "`little-little-little-man`: stop allocating lane rows no edge can leave on",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "58% of the charged dimension was lane plumbing, not glyphs — 63 blocks became 189 interior rows, of which 109 were overhead and 53 blocks carried exactly one glyph row. Nine of those rows were *provably* dead: a `d` branch declares only pos/zero so its north free row can never be taken, and an `x` branch has no straight lane at all, yet the allocator reserved all three overhead rows for every branching block.",
        "after": """
  one block, as allocated        as it actually needs
  +---------------------+        +--------------------+
  | north free row      |  dead  | glyph row          |
  | glyph row           |   ->   | straight lane      |
  | straight lane       |        +--------------------+
  | south free row      |
  +---------------------+
""",
        "numbers": [("interior rows", "222", "213", "-4%"), ("area2", "49,284", "45,369", "-7.9%")],
        "alternatives": [],
        "commits": ["9d117e8"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": ["tasks/solutions/little-little-little-man_ring.man"],
    },
    {
        "id": "fp-lllm-fallthrough-east",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "Block-machine row plumbing",
        "title": "A fall-through that lands east needs no lane row at all",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "The straight-lane row was 57 of 101 overhead rows, and it exists only to walk the man back **west** to the target's entry. When the straight successor is the next block in order *and* its first glyph stands east of where the predecessor stopped, the man drops one row at his own column and keeps walking east — the row is never claimed, so the successor moves up into it.",
        "after": """
  needs the lane row                 does not
  block A ...stops at col 40         block A ...stops at col 12
  <-------------------- west leg     block B      col 30 -> east, drop one row
  block B  entry at col 3            (12 of 40 unconditional edges qualify)
""",
        "numbers": [
            ("box", "159x213 = 45,369", "159x204 = 41,616", "-8.3%"),
            ("ticks", "1,425,943", "1,387,994", "-2.7% (an unallocated row is also a row not walked)"),
            ("score", "6.47e10", "5.78e10", "-10.7%"),
        ],
        "alternatives": [
            {"name": "move each block's *entry* east to match its start", "verdict": "fails", "why": "the channel bank is west, so an arriving man turns east at his channel and runs to the entry — pushing the entry east lengthens that run across the region the lanes occupy. `entry run to L_DIGIT blocked at (35,41)`."},
        ],
        "commits": ["ffd28d6"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": ["tasks/solutions/little-little-little-man_ring.man"],
    },
    {
        "id": "fp-lllm-own-band",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "Block-machine row plumbing",
        "title": "A block may start at its own band — but it may not be *entered* there",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "Zones run `ST -> FI -> IO` west to east, so a block may begin at its own first band provided it never needs a band further west later — i.e. provided it holds no `ST` op. 52 of 63 blocks qualify.",
        "numbers": [("box", "159x204 = 41,616", "159x202 = 40,804", "-2.0%"), ("score", "5.78e10", "5.60e10", "-3.0%")],
        "alternatives": [],
        "commits": ["df3dc7b"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": ["tasks/solutions/little-little-little-man_ring.man"],
    },
    {
        "id": "fp-lllm-wraps",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "Block-machine row plumbing",
        "title": "Wrap elimination, zone reorder, widest-literal-first — three that measured wrong",
        "status": "rejected",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "The glyph rows carried 17 rows of *wrapping*, so removing wraps looked like the next 17 rows. Attributing every wrap to the `_Pen` call that caused it: 14 were `seek` (the block revisits a pipe band it has already passed — a property of the token sequence, not the geometry) and exactly **one** was `ensure` (the row genuinely ran out of columns). So widening buys one row, not 17.",
        "numbers": [
            ("zone order `FI->ST->IO`", "159x202 = 45,369", "217x209 = 47,089", "**+3.8%** — needs IW 216, and width past height loses"),
            ("reorder `WALL_CELL`'s ops", "181 interior rows", "182", "+1 row: the budget is coupled through fall-through"),
            ("widest-literal-first backticks", "7.69e9", "7.85e9", "-1.8% ticks, +3.9% footprint"),
            ("widen the room past the wrap point", "IW +90", "IW +170", "ticks move 0.05%, height not at all"),
        ],
        "alternatives": [],
        "commits": ["b164ba9", "dfc55d1"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "fp-subset-sum-turnarounds",
        "date": "2026-07-27",
        "group": "footprint",
        "block": "Ring geometry",
        "title": "`subset-sum`: turn every ring rotation around",
        "status": "shipped",
        "era": "contest",
        "problems": ["subset-sum"],
        "what": "Rows turned out to be the scarce dimension, and each phase's ring was rotating the way that spent them. Turning the emit stack, then phases 2 and 3, then emit's two rotations, then the last two — five separate moves, each one measured.",
        "after": """
  81x169  ->  92x153  ->  92x142  ->  92x137  ->  92x132  ->  92x128
   both       emit       phases     emit's      the last
   rings      stack      2 and 3    two rots    two rots
   turned    (-14%)
   (-18% on score)
""",
        "numbers": [("box", "81x169", "92x128", "-42% on the charged side"), ("first move", "", "-18% on score", "")],
        "alternatives": [
            {"name": "the one-room ring at 81x253", "verdict": "superseded", "why": "the no-solution emit set the charged side (253 -> 220 -> 202 before the turn-arounds even started)."},
        ],
        "commits": ["90b393e", "c12558d", "1b11054", "f0457da", "5effab5"],
        "sources": [],
        "artifacts": ["solutions/subset-sum/000005218553037_subset-sum.man"],
    },
    {
        "id": "fp-subset-sum-two-rooms",
        "date": "2026-07-27",
        "group": "footprint",
        "block": "Ring geometry",
        "title": "`subset-sum`: two rooms on one ring",
        "status": "shipped",
        "era": "contest",
        "problems": ["subset-sum"],
        "what": "Split the worker into two rooms sharing one ring, so neither has to hold the whole program's glyphs in one row band. Room B's prologue then lost two more rows.",
        "numbers": [
            ("box", "92x128", "80x86", "-33% on the charged side"),
            ("local ticks", "4.37e9", "1.94e9", "-56%"),
            ("then", "80x86", "80x84", ""),
            ("judged score", "12,178,364,826", "5,218,553,038", "**2.33x**"),
        ],
        "alternatives": [],
        "commits": ["0b200c9", "ce9ffcc", "cb2fdb2"],
        "sources": [],
        "artifacts": ["solutions/subset-sum/000005218553037_subset-sum.man"],
    },
    {
        "id": "fp-llm-rom-refold",
        "date": "2026-07-27",
        "group": "footprint",
        "block": "The ROM fold",
        "title": "Re-fold the ROM against the machine's *real* bounding box — the last submission of the contest",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "The fold constant had been swept against a machine that had since gotten shorter. Re-folding 83 rows to 89 at 180 wide, with the corridor 12 -> 14 rows, changes only the ROM room and the corridor — the CPU tail is byte-identical.",
        "numbers": [
            ("box", "193x171 = 37,249", "180x179 = 32,400", "-13%"),
            ("local avg ticks", "4,631,961", "4,605,502", "-0.6%"),
            ("judged score", "188,494,404,573", "163,823,101,714", "-13.1%, 28/28"),
        ],
        "alternatives": [],
        "commits": ["d7f2399"],
        "sources": ["WRITEUP.md"],
        "artifacts": ["solutions/little-little-man/000163823101714_little-little-man.man"],
    },
    {
        "id": "fp-lshaped-rom",
        "date": "2026-07-26",
        "group": "footprint",
        "block": "The ROM fold",
        "title": "Half the bounding box is empty, and the ROM is the shape that can fill it",
        "status": "parked",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "Measured on the 195x196 machine: 38,220 cells in the box, **18,467 used (48%)**, and a quarter of the box is one dead rectangle east of the CPU stack. The ROM is one folded pipe snake with no natural shape, so it can be **L-shaped** — a top band plus a leg down the east side. That makes the fold a two-variable problem and the optimum moves a long way.",
        "after": """
  today                          L-shaped
  +---------- ROM 195 ---------+ +----- ROM 170 -----+---+
  |                            | |                   |ROM|
  +--------------+             | +-------------+     |leg|
  |  CPU stack   |   DEAD      | |  CPU stack  |     | 65|
  |   105 cols   |   9,360     | |             |     |x104
  +--------------+---- cells --+ +-------------+-----+---+
   195x196 = 38,416              170x170 = 28,900   (-25%)
""",
        "numbers": [
            ("box (projected)", "195x196 = 38,416", "170x170 = 28,900", "-25%"),
            ("packing floor", "18,467 used cells", "= a 136x136 square", "even 70% efficiency is ~163 a side"),
        ],
        "alternatives": [
            {"name": "compact the CPU's *columns*", "verdict": "worth zero here", "why": "width is set by the ROM's fold against a stack that ends at x=104. Only rows convert into score — a property of the pose, not of the CPU."},
        ],
        "commits": ["a18bee3", "79d10c3", "1ccb41a"],
        "sources": ["littleman/LLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "fp-deadman-square",
        "date": "2026-07-28",
        "group": "footprint",
        "block": "deadman-3d (DOOM)",
        "title": "756x1197 -> 307x307, an exact square, for +1.79% ticks",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The objective changed: the visualisers render the machine's full bounding rectangle, so `max(w,h)` had to come down hard and tick regressions were acceptable well past the usual ~5% line. Three levers: the men-v3 store became a placeable **multi-column block** with an answer riser (a one-column 330-cell store was 681x999 and set *both* dimensions of the old box); `STORE_OPS = 1` (measured at exactly **0 ticks** — the router walks home while the CPU idles); and raising the DOOM block's panel, which had been sitting 37 rows lower than any route needed.",
        "after": """
  1x330 store               8x42 store
  +---+                     +-----------------+
  |ROM|                     |      ROM        |
  +---+                     +-----------------+
  |CPU|                     |      CPU        |
  +---+                     +-----------------+
  | s |  681 x 999          |  store 232x150  |
  | t |  sets BOTH dims     +-----------------+
  | o |                     |   DOOM panel    |
  | r |                     +-----------------+
  | e |                      307 x 307, exact square
  +---+
   756x1197, bbox 904,932    bbox 94,249  (9.6x smaller)
""",
        "numbers": [
            ("box", "756x1197 (max 1197)", "307x307 (max 307)", "**3.9x** on the charged side"),
            ("bbox", "904,932", "94,249", "9.6x smaller"),
            ("rounds 0..1", "11,305,517", "11,508,389", "+1.79% — deliberately accepted"),
            ("delta split", "", "store shape +63k · ops=1 +0 · squareness placement +140k", ""),
        ],
        "alternatives": [
            {"name": "9x37 store at rom39", "verdict": "not chosen", "why": "335x296 — faster (-23k) but max 335 against 307."},
            {"name": "`STORE_OPS = 8` (the unrolled router strip)", "verdict": "worth nothing here", "why": "0 ticks difference. The CPU issues reads ~1k ticks apart; the unrolled strip only pays for back-to-back streams this machine never generates."},
        ],
        "commits": ["403fc85", "cf0835f", "709c62e"],
        "sources": ["scratch/deadman3d-opt/METRICS.md §SQ"],
        "artifacts": ["littleman/examples/deadman-3d.man"],
    },
    {
        "id": "fp-deadman-m7c",
        "date": "2026-07-28",
        "group": "footprint",
        "block": "deadman-3d (DOOM)",
        "title": "M7c — the box re-swept, and three 'obvious' cuts each worth exactly zero",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "Measure what binds *first*. On the canonical tier the width is the **ROM** and the height is `rom_rows + 3*store_rows + 112`; on the taped tier the width is the **store** (`48*banks + 32` columns, independent of the bank sizes) with 164 rows of slack. Two closed forms fall out — `rom_w ~ 22,740 / rom_rows`, so one more fold row buys ~6 columns and costs 1 — and they drive everything.",
        "numbers": [
            ("canonical", "379x376 (max 379)", "373x377 (max 377)", "-1.3% bbox"),
            ("taped", "395x231 (max 395)", "279x258 (max 279)", "**-21%**"),
            ("canonical, 58-round walk", "330,339,051", "327,860,446", "-0.75%"),
            ("taped, 116-round tour", "1,271,045,970", "1,253,152,404", "-1.41%"),
            ("merging the two COLD bank pairs", "6 banks 93,649,383", "4 banks 90,157,275", "-3.7% — and narrower"),
        ],
        "alternatives": [
            {"name": "put the input on the WEST wall", "verdict": "worth 0", "why": "feasible and **dimension-neutral** — byte-for-byte the same 379x376. The historical 'pad 39' rationale is dead: it is the STORE *teleport* room, not the input wall, that unbinds the memory `r` now. Dropping the teleport too gets 4 pixels for 9% of frame ticks. No."},
            {"name": "reclaim the empty rightmost CPU column", "verdict": "worth 0", "why": "real (interior column 53), but the ROM is 16 columns wider than the store chain, so pad 15..31 all build the *identical* box."},
            {"name": "reclaim the empty CPU bottom row", "verdict": "worth 0", "why": "the CPU box ends at row 123; the height is set 150 rows lower by the store block plus the DOOM stack."},
            {"name": "merging the HOT bank pair", "verdict": "rejected", "why": "+29% ticks — re-proves the traffic-shaping lesson."},
        ],
        "commits": ["3d80898"],
        "sources": ["scratch/deadman3d-opt/METRICS.md §M7c"],
        "artifacts": ["littleman/examples/deadman-3d.man"],
    },
    {
        "id": "fp-doom-unit-lift",
        "date": "2026-07-29",
        "group": "footprint",
        "block": "deadman-3d (DOOM)",
        "title": "M10 — the DOOM unit's loop corridor lifts eight rows; the unit's *width* is worth nothing",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The DOOM block hangs below everything, so the machine's last row is the block's, and the block's height is `R_ADDR + PANEL_H + 6` — **ADDR alone sets it**. Every band row is a fixed offset from the loop corridor, so the whole lower half translates as one rigid piece and all four pipe lengths are unchanged. Rows 19..26 held no cell at all; below that RUN's arm collided on a `W` until it was given COL's climb column, which unlocked nine more rows. Row 10 is a real floor — not a collision, a *binding*: COL's seed push at fixed row 20 must stay nearer the ring band than ADDR.",
        "numbers": [
            ("DOOM block", "235x101", "235x84", "-17 rows"),
            ("taped box", "287x271", "287x254", ""),
            ("116-round tour", "839,384,674", "839,158,874", "-0.03% (and slightly *cheaper*: shorter descents)"),
        ],
        "alternatives": [
            {"name": "variable-pitch trie / merge GUN+GUNF / merge COMMIT+CURS", "verdict": "worth exactly zero", "why": "the three arms carrying 98.9% of the words occupy 16 columns; the two sprite arms carry 0.55% and occupy 56. But the block's east edge is column 235 on a 287-wide machine — it is 50 columns clear. The block would have to **grow by 52 columns** before its width mattered at all."},
        ],
        "commits": ["7555da4", "31e4615"],
        "sources": ["scratch/deadman3d-opt/METRICS.md §M10"],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# MEMORY
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "mem-rotating-tape",
        "date": "2026-07-24",
        "group": "memory",
        "block": "STORE — the rotating pipe tape",
        "title": "Memory as a rotating pipe tape — the contest's first accepted solution",
        "status": "shipped",
        "era": "contest",
        "problems": ["memory"],
        "what": "A pipe *is* a FIFO whose capacity equals its length, so a folded corridor is a tape you have already paid for. One revolution per operation, so cost is constant in the *address*. It became the drop-in `STORE` for every generated machine, because its wire protocol is the `memory` problem's own.",
        "after": """
   worker            relay (turnaround room)
  +---------+       +----+
  | @ r s m |>----->|>r v|      capacity = pipe length
  | ^ . . v |       |^ s<|      one revolution per operation
  | < . . < |<-----<+----+      cost constant in the address
  +---------+
   values move with NO man -- 427 slots cost ZERO runners
""",
        "numbers": [
            ("N", "4 / 8 / 16 / 32 / 48 / 64 / 100", "138 / 164 / 229 / 364 / 496 / 630 / 936 ticks-per-op", "`ticks/op ~ 105 + 8.3N`"),
            ("footprint", "32x32", "32x32", "**independent of N**"),
            ("judged", "92.0M expected", "86,529,024 accepted", "first accepted solution, 22:04 day 1"),
        ],
        "alternatives": [
            {"name": "a delay line with address-dependent cost", "verdict": "wrong model", "why": "the tape runs exactly one revolution per operation, so cost is constant in the address — better than this document originally assumed."},
        ],
        "commits": ["c639bfb", "f8f9c23"],
        "sources": ["littleman/ARCH.md §4.1"],
        "artifacts": ["littleman/programs/memory.man"],
    },
    {
        "id": "mem-size-n-to-slots",
        "date": "2026-07-25",
        "group": "memory",
        "block": "STORE — the rotating pipe tape",
        "title": "Size `N` to the problem's actual slot count — free on footprint, linear on ticks",
        "status": "shipped",
        "era": "contest",
        "problems": ["tcp", "brackets", "sort-numbers", "plotter"],
        "what": "The tape is 32x32 at every `N`; the serpentine grows *rows* for capacity, not columns. So there is no trade-off to weigh at all: pick `N` from the problem's constraints (not from its public cases) and take the tick saving for nothing.",
        "numbers": [
            ("tcp at N=48 vs N=100", "", "1.9x cheaper per access", ""),
            ("brackets at N=32", "", "2.6x", ""),
            ("sort-numbers at N=16", "", "4.1x", ""),
        ],
        "alternatives": [
            {"name": "size `N` from the public cases", "verdict": "a crash", "why": "`tcp` needs N=52 because n=48 puts the top slot at BUF+47, though no public case goes past 35. An `N`-slot tape addresses 1..N-1, and overrunning by one is `fatal: wall` inside the tape room, not a wrong answer."},
        ],
        "commits": ["15a551f"],
        "sources": ["littleman/ARCH.md §4.1"],
        "artifacts": [],
    },
    {
        "id": "mem-per-access-not-per-slot",
        "date": "2026-07-25",
        "group": "memory",
        "block": "The cost model",
        "title": "The tape's cost is per-ACCESS, not per-slot — and it re-priced every program",
        "status": "shipped",
        "era": "contest",
        "problems": ["plotter", "gradebook"],
        "what": "The model in every plan had been `105 + 8.3N`, a *slope*. Solving for per-unit costs against the engine's own totals gives ~**316 ticks of fixed cost per access** in a generated machine, moving only ~1.9 ticks per access per slot. The two figures answer different questions and both are needed — the slope decides **how big** to make a tape, the fixed cost decides **how often to touch it** — but only one of them is worth multiples.",
        "after": """
  planning rule that fell out:
     1 tape access  ~=  7 instructions  ~=  40 recirculated ROM words
                    ^^^ count ACCESSES, not instructions
""",
        "numbers": [
            ("plotter", "6% OVER the step cap", "61% under it", "-2.8x on score"),
            ("a read", "", "523 ticks", "512-637 across five cases, 100% on one cell"),
            ("a write", "", "~19 ticks", "fire-and-forget; the CPU never waits"),
            ("an instruction", "", "162 ticks", ""),
            ("a taken branch", "", "155 + 5.88/discarded word", ""),
        ],
        "alternatives": [
            {"name": "trust the emulator's flat 6 ticks per store word", "verdict": "out by ~30x", "why": "fine for comparing compute-bound programs, useless for a tape-bound one. **Any tape-bound program must be judged on the engine, never modelled.**"},
        ],
        "commits": ["6c93333", "f7e2258"],
        "sources": ["littleman/ARCH.md §4.1", "littleman/ARCH.md §8.1"],
        "artifacts": [],
    },
    {
        "id": "mem-write-cheap-read-dear",
        "date": "2026-07-26",
        "group": "memory",
        "block": "The cost model",
        "title": "A read costs 523 ticks and a write costs 19 — the asymmetry is the whole game",
        "status": "shipped",
        "era": "contest",
        "problems": ["snake", "little-little-man", "gradebook"],
        "what": "Measured per *cell* with the heat-map profiler, gated so the run ends at the scored tick. Spend writes freely and hunt reads: parking a value in a scratch slot costs nothing and re-reading it costs 523. This is what makes the `INCM`/`DECM` read-modify-write family worth its lanes, and it is why `little-little-man` stamps room perimeters rather than testing for them.",
        "numbers": [
            ("one tape read", "", "523 ticks", ""),
            ("one tape write", "", "~19 ticks", "**27x cheaper**"),
            ("one tape slot", "", "8.06 ticks per read", "N=66 -> 90: 2,169,980 -> 2,617,836"),
            ("a 50-cell array", "", "taxes every unrelated scalar read by 403 ticks", ""),
        ],
        "alternatives": [],
        "commits": [],
        "sources": ["littleman/ARCH.md §4.1"],
        "artifacts": [],
    },
    {
        "id": "mem-sign-biased-request",
        "date": "2026-07-25",
        "group": "memory",
        "block": "The store seam",
        "title": "Put the operation in the **sign** of the request word",
        "status": "shipped",
        "era": "contest",
        "problems": ["tcp", "brackets", "gradebook", "matmul"],
        "what": "The STORE protocol wants an opcode word first, but fixed-width instructions mean `A` already holds the operand when a lane starts, and the `0`/`1` literal destroys it. Putting the op in the sign (`+a` read, `-a` write) needs no literal, no spill slot and no lane reading the ring; a 13x4 adapter room expands it back into the tape's real protocol, so the verified tape is untouched.",
        "after": """
  lane emits:   -----> [ +a ]  read       adapter --> `0 a`
                       [ -a ]  write               --> `1 a v`
  no literal, no spill slot, the tape never changes
  (address 0 is sign-ambiguous, so hardware addresses start at 1)
""",
        "numbers": [("tcp SPILL block", "1 room + 2 pipes", "**none at all**", "")],
        "alternatives": [
            {"name": "`LDP` / `STP` (indirect through a pointer cell)", "verdict": "retired", "why": "`LDA` (`ACC = store[ACC]`) and `MOVA` keep the address in ACC and never need a pointer and a value live together."},
        ],
        "commits": [],
        "sources": ["littleman/ARCH.md §2.7"],
        "artifacts": [],
    },
    {
        "id": "mem-serpentine-tape",
        "date": "2026-07-26",
        "group": "memory",
        "block": "STORE — the rotating pipe tape",
        "title": "The tape capped at 107 slots; the serpentine ring took it to 1,975 at zero width cost",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "`machine.tape_block`'s ring was a fixed 108 cells at every `n`, drawn as two L-shaped pipes. `little-little-man` needs 427 slots (a 256-cell program grid plus the interpreter's state), so the store had to come first. A serpentine ring is 33x48 at n=427, byte-identical output for every `n <= 107`.",
        "numbers": [("max slots", "107", "1,975", ""), ("width cost", "", "**zero**", ""), ("cost", "", "8.0 ticks a slot a read", "")],
        "alternatives": [],
        "commits": [],
        "sources": ["littleman/LLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "mem-man-memory",
        "date": "2026-07-25",
        "group": "memory",
        "block": "Man-memory — a stored word is a live man",
        "title": "16 cells of memory with no walk at all: `S` broadcasts, one-hot decodes, `R` teleports back",
        "status": "shipped",
        "era": "contest",
        "problems": ["memory"],
        "what": "A cell that is only a man and a loop, with the value living in the pipe. The router *broadcasts* the address and the one cell holding it in `B` replies — so nothing walks, and latency depends only on the column depth `N`, never on the column count `M` or the total size. Width is free; depth is what you pay for.",
        "after": """
  S broadcast ---> [cell 0] [cell 1] [cell 2] ... [cell M]
                      |                              |
                      +---- one-hot match ----+      |
                                              v      v
                              R teleport <--- collector
  latency = 53 + 8*rows,  INDEPENDENT of M
  vs the tape's 8.0 x N for the WHOLE tape
""",
        "numbers": [
            ("memory_men_addr at n=100, judged", "3,223 avgTicks", "", "**17.8x fewer** than the shipped ring"),
            ("cells of `.man` per stored word", "tape 3.7", "man-memory 81", "22x denser vs 22x faster"),
            ("shape", "`width = 27M`", "`height = 32 + 3N`", "M and N independent — no stride, no power-of-two"),
        ],
        "alternatives": [
            {"name": "the address as *code* (a decoded literal per cell)", "verdict": "superseded", "why": "`memory_men_addr` makes the address a number in a hand instead, so one block serves every address."},
        ],
        "commits": ["a2b5537", "07c8b85", "58df1db", "86c712a"],
        "sources": ["littleman/ARCH.md §4.1"],
        "artifacts": ["tasks/solutions/memory_men_addr.man", "tasks/solutions/memory_men_grid.man"],
    },
    {
        "id": "mem-man-memory-as-store",
        "date": "2026-07-26",
        "group": "memory",
        "block": "Man-memory — a stored word is a live man",
        "title": "The man-memory as a CPU `STORE`: correct on every program, and the wrong trade almost everywhere",
        "status": "rejected",
        "era": "contest",
        "problems": ["brackets", "snake", "matmul", "gradebook", "tcp"],
        "what": "Wired as a drop-in tier and correct on every program tried (`brackets` 9/9, `gradebook` 7/7, `tcp` 6/6, `matmul` 7/7, `snake-ring` 5/5). It still loses, and the reason is geometry: the block is 36 columns wide whatever `n` is and **`3n + 9` rows tall**, and the ROM already occupies every row above it, so those rows are additive.",
        "after": """
  program      n     footprint    ticks    score
  snake-ring   9     1.000x       0.970x   0.970x   <- break-even sits at n ~ 9
  brackets     5     1.064x       0.962x   1.024x
  matmul      16     1.335x       0.875x   1.169x
  gradebook   32     1.617x       0.777x   1.257x
  tcp         52     2.667x       0.563x   1.501x
                     ^^^^^^^ squared, and monotone in n -- it always wins
""",
        "numbers": [
            ("tcp ticks", "1.000x", "0.563x", "44% of its ticks *were* the tape — the largest single-change tick win measured"),
            ("tcp score", "", "1.501x", "and it still loses, on area"),
        ],
        "alternatives": [
            {"name": "multi-column grids", "verdict": "do not rescue it", "why": "`build_grid` puts the collector strip on the *bottom* so answers cannot overtake, so a host's response climbs ~250 cells back to the corridor — at one tick per cell that is the whole saving handed back. The one-column chain's answer pipe is 67 cells, already twice the memory's own 31."},
            {"name": "blame the `~5n` ignition", "verdict": "not the cause", "why": "charged once per case; it never showed up in a profile."},
        ],
        "commits": ["2790318", "2de44bd"],
        "sources": ["littleman/ARCH.md §4.1", "littleman/OPTIMIZATION.md"],
        "artifacts": [],
    },
    {
        "id": "mem-two-tier-wall-clock",
        "date": "2026-07-26",
        "group": "memory",
        "block": "Man-memory — a stored word is a live man",
        "title": "The two-tier store: 2.36x faster in ticks, **refused** on wall clock",
        "status": "rejected",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "The cold 256-word program grid stays on the tape; the 52 hottest slots (90.6% of all reads) move to a man-memory tier behind a range-routing adapter. Engine-verified, frames right, ticks real — and the judge returned **4/28** with `10 time-cap` / `14 time-cap`. Score counts *ticks*; the grader spends *wall clock*, and wall clock goes as **runners x ticks**.",
        "after": """
                 live men   ticks a case   runner-ticks   judged
  one 427-tape       5      20,275,186     0.10bn         28/28
  + 52-slot tier   114       8,605,207     0.98bn         4/28 TIME-CAP
                   ^^^ 52 cells + decoders, repeaters, router,
                       collector, relay = 109 extra men, each
                       stepped every tick whether read or not
""",
        "numbers": [
            ("ticks", "20,275,186", "8,605,207", "**2.36x faster**"),
            ("simulator work", "0.10bn", "0.98bn", "**9.7x more**"),
            ("the one-sided bound", "0.151bn passes", "0.768bn does not", "an earlier reading of '0.73bn passed' was wrong — it inferred a per-case bound from sorting one build's costs"),
        ],
        "alternatives": [
            {"name": "re-place the seam on the compacted CPU", "verdict": "free in area, still refused", "why": "at 26 slots it fits the same 200x199 box (the answer lanes had been hard-coded to the band the ROM's fold now occupies; `_ANS_BAND` fixed it). 10 slots -> 11/28 time-cap. The verdict did not move."},
            {"name": "`grid_side_block` for the tier", "verdict": "does not place", "why": "`collision at (109, 93)` — `_two_tier` still routes the answer as if it left the east side. Parked behind `TIER_SIDE_PORTS = False`."},
        ],
        "commits": [],
        "sources": ["littleman/LLM-DESIGN.md", "littleman/ARCH.md §4.1"],
        "artifacts": [],
    },
    {
        "id": "mem-banked-pipe-tape",
        "date": "2026-07-26",
        "group": "memory",
        "block": "STORE — banking",
        "title": "Bank the *tape* instead: 2.12x, judged 28/28",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "`men x ticks` is the constraint and the man-memory loses because it pays men *per slot*. A pipe tape does not: **four little men at n=52 and four at n=427**, constant in size. So split the store into a small hot ring and the full cold one — the latency win lands on the axis that scores and barely touches the axis that refuses. The seam already existed; only the block in the hot slot changed.",
        "after": """
  store           live men   avg ticks    area2    runner-ticks   judged
  one 427-tape        5      19,354,082   40,000   0.151bn        848,506,331,429
  hot 52 + cold       8       7,156,214   40,804   0.083bn        fatal: wall x4
  hot 104 + cold      8       8,788,539   41,616   0.110bn        400,740,741,396
  hot 208 + cold      8      11,528,142   41,616   0.143bn        not sent
                      ^^^ constant in size -- a stored word is a
                          value in a rotating ring, not a man
""",
        "numbers": [
            ("judged score", "848,506,331,429", "400,740,741,396", "**2.12x**"),
            ("runner-ticks", "0.151bn", "0.110bn", "*below* the machine that was already passing"),
            ("hot-bank sizing", "104 slots", "53 slots", "317.1e9 -> 296.0e9, sized to what it actually uses"),
        ],
        "alternatives": [
            {"name": "hot bank at exactly 52 slots", "verdict": "`fatal: wall`", "why": "the `TAPE_SIZE` trap — a ring sized to exactly its top address stalls rather than faults."},
        ],
        "commits": ["fd7b18d", "8e783d3", "50e92e6"],
        "sources": ["littleman/LLM-DESIGN.md"],
        "artifacts": ["solutions/little-little-man/000400740741396_little-little-man.man"],
    },
    {
        "id": "mem-tape-skips",
        "date": "2026-07-26",
        "group": "memory",
        "block": "STORE — the pass loop",
        "title": "Two- and four-word-per-lap tape skips, and the per-task table that says when to use them",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man", "pathfinder", "snake"],
        "what": "The tape worker advanced one word per lap at 8 ticks. `worker_v2_jump` puts a `BP` test and one `rs,m` body on *each* side, so it advances two words per lap at ~5. Batch 4 peels `count mod 4` with two `x`/`]` bit tests and then runs a four-wide bulk ring — exact by construction: `(c&1) + 2*((c>>1)&1) + 4*(c>>2) = c`.",
        "after": """
  batch   grid     ticks (200-slot boundary probe)
    1     84x61    22,211
    2     96x61    15,617   (4x3 relay)
    4    111x61    12,403   (6x4 relay)
    4    111x61    11,431   (8x6 relay)  -- 1.94x, at 27 more columns
""",
        "numbers": [
            ("little-little-man", "363,025,672,731", "317,139,442,159", "two-word lap"),
            ("then", "317,139,442,159", "313,589,490,978", "four-word lap + L/U teleports"),
            ("pathfinder (CPU+PATH)", "105,794,282,683", "84,002,439,826", "batch 4, 6x4 relay"),
            ("tcp / gradebook / sudoku", "batch 1 wins", "", "their tick reductions cannot repay a wider squared footprint"),
        ],
        "alternatives": [
            {"name": "batch 3", "verdict": "rejected structurally", "why": "arbitrary counts need `divmod(count,3)`, and `/` puts the remainder in `B` — which is the only register that survives a tape pass and is already holding the signed operation tag. Batch 4 resolves the register-liveness problem by construction."},
            {"name": "a `Y` chain of workers", "verdict": "rejected", "why": "no independent work before a read succeeds, so all children contend for the same ordered FIFOs and backpressure serialises them. Worse, the right child keeps the parent's creation-order slot while new workers are newer — one observed failure wrote address 1's value into address 4."},
            {"name": "batch 4 as the global default", "verdict": "no", "why": "TCP 568,891,733 -> 866,425,973. The table is per-task for a reason."},
        ],
        "commits": ["fdce6fb", "e659c14", "8e2d631", "e1b65d6"],
        "sources": ["littleman/TAPE-SKIP-ROADMAP.md"],
        "artifacts": [],
    },
    {
        "id": "mem-relay-throughput",
        "date": "2026-07-25",
        "group": "memory",
        "block": "Rings and relays",
        "title": "The rotation cost was inherited, not measured — and it was wrong twice over",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "Every projection in the dataflow survey multiplied rotation counts by `b = 3.2`, a number carried over from STREAM's ring and never checked. Measured: the worker got more than twice as fast and **throughput did not move**, because the binding constraint is the *turnaround room* — a 6-cell walking cycle carrying one word per lap caps any ring at 6.0. Nothing on the worker's side reveals this.",
        "after": """
  ticks/rotation = max( 2 + 3/m , (2(w+h) - 4) / relay_words(w,h) )
                        ^worker            ^relay perimeter

  worker m   relay   words/lap   measured   binding
     1        4x3        2         5.00      worker
     2        4x3        2         5.00      relay      <- fat worker, thin relay: no gain
     2        6x4        5         3.50      worker
     3        6x4        5         3.20      relay
     3        8x6        9         3.00      worker
     4        8x6        9         2.75      worker
""",
        "numbers": [
            ("every projection in DATAFLOW-SURVEY", "b = 3.2", "b = 6.0 as built", "**1.9x optimistic**"),
            ("subset-sum's design margin", "2x claimed", "1.2-1.5x honest", "the fat relay is most of the margin, not an optimisation"),
        ],
        "alternatives": [
            {"name": "tune the worker loop further", "verdict": "buys nothing", "why": "`counted_ring(\"rsrsrs\")` at 3.0 cells/value measured exactly the same 6.0 as `counted_ring(\"rs\")` at 5.0. **Size the ring so the cheaper term binds.**"},
        ],
        "commits": ["c1c52fd", "01fe08d"],
        "sources": ["littleman/DATAFLOW-SURVEY.md §1.1", "littleman/TAPE-SKIP-ROADMAP.md"],
        "artifacts": [],
    },
    {
        "id": "mem-stream-tier",
        "date": "2026-07-25",
        "group": "memory",
        "block": "STREAM — a third memory tier",
        "title": "`matmul`: three rotate-only rings and a fused hardware MAC",
        "status": "shipped",
        "era": "contest",
        "problems": ["matmul"],
        "what": "The ordinary tape makes `matmul` infeasible — the ~8,450-access estimate correctly rejects random-access STORE. But the final loop order only *rotates* A, B and the current C row, so three FIFO rings plus a four-glyph hardware MAC replace every one of those accesses, and 4,096 worst-case MACs run in a local counted loop.",
        "after": """
   ring A  --->+
   ring B  --->|  MAC  |---> ring C
               +-------+
   4 glyphs, 0.59 CPU instructions per MAC
   rotate-only: no addressing, no adapter, no tape
""",
        "numbers": [
            ("matmul", "unscoreable", "96x96, 120,460 avg ticks, 1.11bn", ""),
            ("ticks per MAC", "37", "7.3", "-80%"),
        ],
        "alternatives": [
            {"name": "16 `Y`-forked MAC workers", "verdict": "worse than the single worker", "why": "one STREAM worker already needs three rings, 14 scalar slots and 0.59 CPU instructions per MAC. Splitting the MAC's B-return and product sends does not shorten its three-tick critical path and needs termination corridors."},
        ],
        "commits": ["4e362d9", "39429f2"],
        "sources": ["littleman/ARCH.md §8", "littleman/DATAFLOW-SURVEY.md"],
        "artifacts": ["tasks/solutions/matmul_ring.man"],
    },
    {
        "id": "mem-take-it-out-of-the-store",
        "date": "2026-07-25",
        "group": "memory",
        "block": "Coprocessor rings",
        "title": "The version that pays is not swapping the store — it is taking the big structure *out* of it",
        "status": "shipped",
        "era": "contest",
        "problems": ["snake", "little-little-man"],
        "what": "A 50-cell array taxes every unrelated scalar read by `8.06 x 50 = 403` ticks. Moving `snake`'s body into a write-only coprocessor ring took its tape from 66 slots to 9 — and a read from 523 ticks to ~180 — without changing the store's *design* at all.",
        "numbers": [
            ("snake tape N", "66", "9", ""),
            ("a read", "523 ticks", "~180", "-66%"),
            ("snake judged", "15,891,242,682", "3,369,020,288", "**4.7x**"),
            ("a `STEP` on a six-cell body", "~5,300 ticks (tape scan)", "218 ticks", "~1.35 CPU instructions"),
        ],
        "alternatives": [
            {"name": "swap `little-little-man`'s store for a man-memory (design B)", "verdict": "loses on width", "why": "the whole store as a 7x61 grid is 196x215 = ~73,984 area2 against 41,616. Design A (a 3x14 hot tier in the free space east of the CPU band) was the one worth building — and then the wall-clock verdict killed it anyway."},
        ],
        "commits": ["98fcc27"],
        "sources": ["littleman/ARCH.md §8.0", "littleman/LLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "mem-packed-cells",
        "date": "2026-07-26",
        "group": "memory",
        "block": "Packing the store",
        "title": "Packing cells into a 64-bit word: measured twice, loses twice",
        "status": "rejected",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "Four cells to a word takes the tape from 427 slots to 235 and a read from 3,416 to 1,880 ticks — exactly the right shape, at no extra men. It is still a loss, because the read-modify-write on every stamped wall costs more than that saves, and the unpack code grows `P`, which every taken branch then recirculates.",
        "numbers": [
            ("4 cells/word", "21.4M ticks", "23.6M", "+10%"),
            ("8 cells/word", "tape 427 -> 239, read 3,416 -> 1,912", "P 3,377 -> 3,919; avg ticks 20.3M -> 20.9M", "score 7.74e11 -> 9.48e11"),
        ],
        "alternatives": [],
        "commits": [],
        "sources": ["littleman/LLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "mem-storeopt-sweep",
        "date": "2026-07-25",
        "group": "memory",
        "block": "The store seam",
        "title": "`storeopt` — a deterministic STORE backend swap, and the sweep that found no winner",
        "status": "rejected",
        "era": "contest",
        "problems": ["brackets", "snake", "matmul", "sudoku-validity", "gradebook", "tcp"],
        "what": "Make memory replacement an AST *seam* operation: locate the registered memory rooms by translation-invariant room signatures, record the request/response boundary cells and headings, remove the old rooms and every attached route, place the replacement and route both seams — then assert the new AST still has one incoming and one outgoing compute-room attachment before validating cases. Six best submissions swept; none improved.",
        "after": """
  program        slots   `men-y` shape   ticks      objective
  brackets         5     101x81          +20.8%     +36.6%
  snake-ring       9     129x136         +26.1%     +124.1%
  matmul          16     130x90          +25.3%     +161.4%
  sudoku-validity 31     167x97          +6.7%      +331.9%
  gradebook       32     198x105         +16.7%     +252.0%
  tcp             52     253x78          **-7.4%**  +398.6%
                                          ^^^^^^ the only tick win,
                                          buried by 253 columns
""",
        "numbers": [("candidates retained", "6 built and validated", "**0**", "no backend improves its best submitted source")],
        "alternatives": [
            {"name": "treat exact CPU room dimensions as seam identity", "verdict": "wrong", "why": "legal `mem_pad` placement can widen that room. A hand-packed source is accepted when its memory room group is a rigidly moved copy of the registered block."},
        ],
        "commits": ["b7fb22e", "ed29867"],
        "sources": ["littleman/OPTIMIZATION.md §STORE backend replacement"],
        "artifacts": [],
    },
    {
        "id": "mem-store-teleport",
        "date": "2026-07-27",
        "group": "memory",
        "block": "The store seam",
        "title": "Teleport the store's response — an `R` has no distance term",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The store's answer had been walking 59 cells of pipe back to the CPU on *every* read. `L`+`U` teleport rooms cut the effective route to 6 cells, because an `R` receive has no distance term at all. At ~14k reads a frame that is ~53 cells of transit each, stopped.",
        "numbers": [("frame 1", "5,778,747", "5,243,226", "-535k, -9.3%"), ("response pipe", "59 cells", "6 cells", "")],
        "alternatives": [
            {"name": "delete the teleport rooms as vestigial", "verdict": "audited, both stay", "why": "teleport L spans 691 columns (essential); teleport U spans 10 rows replacing ~7 pipe cells on every read — ~140k ticks a frame at ~20k reads."},
        ],
        "commits": ["eae745d"],
        "sources": ["scratch/deadman3d-opt/METRICS.md iter 05"],
        "artifacts": [],
    },
    {
        "id": "mem-taped-tier",
        "date": "2026-07-28",
        "group": "memory",
        "block": "deadman-3d (DOOM)",
        "title": "The taped store tier — 691 live men down to 20",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The visualiser chokes on the man-memory's hundreds of little men, so DOOM got a second store tier: banked rotating-pipe tapes behind a **gate chain**, as a drop-in `V3Store` so the adapter, teleports and pad are all unchanged. Each gate is ONE man with four arms — `U` takes the op, `b` parks it, `r M \\`M+1\\` W - X` splits mine/downstream on the address, `d`/`a` split read/write on `BP` — and the downstream arms **rebase**, so banks decode plain local addresses and the last bank needs no gate.",
        "after": """
   op --> [gate 0] --> [gate 1] --> [gate 2] --> bank 3
             |            |            |
          bank 0       bank 1       bank 2      (rebased: addr - M)
             \\____________|____________/
                    collector teleport
  census: 691 live men -> 20   (8 tape + 3 gates + 1 collector = 12 of them)
""",
        "numbers": [
            ("live men", "691", "20", "**34.5x**"),
            ("box", "307x307, bbox 94,249", "307x233, bbox 71,531", ""),
            ("rounds 0..1", "11,508,389", "18,620,300", "+62% — the accepted ring tax, under the 2x flag"),
        ],
        "alternatives": [
            {"name": "4/5 uniform banks", "verdict": "beaten by ~5M ticks", "why": "a tape access costs the whole ring's lap, and the HOT addresses are the high ones — giving them two small rings (40, 33) beat every uniform plan."},
            {"name": "splitting further (32/41, 56/17); skip_batch 4", "verdict": "worse", "why": "batch 4 was wider *and* slower; 5-6 banks at sb2 exceed the 307 width."},
        ],
        "commits": ["9b3ac67"],
        "sources": ["scratch/deadman3d-opt/METRICS.md §TAPED"],
        "artifacts": ["littleman/examples/deadman-3d_taped.man"],
    },
    {
        "id": "mem-per-address-traffic",
        "date": "2026-07-28",
        "group": "memory",
        "block": "deadman-3d (DOOM)",
        "title": "M9 — the bank split re-swept against per-ADDRESS traffic, not per-bank",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "Every earlier pass profiled per **bank**, which cannot see a seam in the wrong place. Counting on the emulator's abstract wire per *address* showed 15 addresses (`XCOL..COLOR`, the DDA inner loop) carrying 56% of all reads and 2 more (`PW`, `WADDR`, the texture loop) another 26% — and the shipped seam had put all of it in one 85-slot ring.",
        "after": """
  addresses          what                     reads    writes
  517..531           the DDA inner loop       56.2%    56.2%
  532..533           the texture inner loop   25.6%    31.2%
  1..352             the map, walked in order  8.4%     0.0%
  353..516           boot-mostly + the ZBUF    3.5%     2.0%
  534..600           the rest of the scalars   6.3%    10.6%

  (256,195, 64, 85) order (3,0,1,2)  ->  75,782,738
  (352,164, 15, 69) order (3,2,0,1)  ->  61,799,020   -18.5%
                     ^^  ^^ the two hot loops get their OWN tiny rings
""",
        "numbers": [
            ("8-command gate", "75,782,738", "61,799,020", "-18.5%"),
            ("115-frame tour", "1,018,297,264", "838,732,969", "**-17.63%**, same 289x269"),
        ],
        "alternatives": [
            {"name": "the `~8 ticks per slot per access` ring-tax model", "verdict": "backwards here", "why": "the model's own optimum `(126,390,17,67)` does not even build. **Bank 0 wants to be BIG**: the map is walked in address order, so its ring is already turned to the next word and the tax is not paid."},
            {"name": "five or three banks", "verdict": "geometry", "why": "five is 272 columns from the store's west wall — an east edge of 333 against a 300 ceiling; three needs bank 0 to swallow 516 slots and does not route at any fold."},
        ],
        "commits": ["4d6ad94", "3d33c93"],
        "sources": ["scratch/deadman3d-opt/METRICS.md §M9"],
        "artifacts": [],
    },
    {
        "id": "mem-hot-bank-first",
        "date": "2026-07-28",
        "group": "memory",
        "block": "deadman-3d (DOOM)",
        "title": "The hot bank leads the gate chain, and the gate loses its nop spacers",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "Every access walks the gate chain from the front, so the bank order is a free parameter worth real ticks: put the hottest bank first and every hot access skips the gates it would otherwise cross. Separately, the bank gate's five nop spacers were pure walk.",
        "numbers": [
            ("hot bank first", "", "-7.44%", ""),
            ("+ the compact gate", "", "-7.84%", ""),
            ("gate nop spacers removed", "", "-0.98%", ""),
        ],
        "alternatives": [],
        "commits": ["f0a8f89", "effebcf"],
        "sources": [],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# CPU
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "cpu-per-program-synth",
        "date": "2026-07-24",
        "group": "cpu",
        "block": "The synthesiser",
        "title": "Synthesise a per-program CPU, not one universal CPU",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "The call at 23:04 on day 1 that shaped everything after it. The decode trie's depth is `ceil(log2 |opcodes used|)` and its leaves spread geometrically, so the opcode *count* sets the CPU room's height — and footprint is squared. There is no 'the LM-1 CPU'; there is a CPU synthesiser, and each task gets its own instance sized to the opcodes, slots, ports and blocks it actually uses.",
        "after": """
  opcodes used   trie depth   lanes   CPU rows
      7 (triangle)     3        8       ~19
     16 (ISA v1)       4       16       ~27
     24 (v1 + ext)     5       32       ~35
                                        ^^^ squared in the score
""",
        "numbers": [("per task, the only bespoke artefacts", "a whole hand-drawn grid", "an `.asm` file and a block config", "data, not code")],
        "alternatives": [
            {"name": "one universal CPU, sixteen programs", "verdict": "superseded within a day", "why": "a program would pay for every opcode it does not use, in a dimension that is squared."},
        ],
        "commits": ["d512679", "d1b1994"],
        "sources": ["littleman/ARCH.md §7.5"],
        "artifacts": [],
    },
    {
        "id": "cpu-trie-decoder",
        "date": "2026-07-24",
        "group": "cpu",
        "block": "Decode",
        "title": "The backpack is an instruction decoder — a binary trie, no comparison chain",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "`b` loads the opcode into `BP`; `x` turns by `BP`'s low bit and `]` shifts it right. So `b` + repeated `]`/`x` walks a **binary trie over the opcode's bits** — that trie *is* the decoder. Depth *k* dispatches 2^k opcodes and each leaf is a distinct cell, which is exactly where that opcode's micro-program starts.",
        "after": """
  +-----------+
  |....>4sH...|      depth-2 trie, 4 opcodes, ~6 ticks
  |...>x......|
  |...]>6sH...|      leaves come out in BIT-REVERSED order --
  |@0bx.......|      so opcode NUMBERING is a free layout variable:
  |...]>5sH...|      put IN next to the north wall, OUT next to
  |...>x......|      the south one, memory ops next to the east
  |....>7sH...|
  +-----------+
""",
        "numbers": [("decode at depth 2", "", "~6 ticks", "12 ticks total including send and halt")],
        "alternatives": [
            {"name": "a `d`/`m` ladder", "verdict": "kept as an option, never needed", "why": "3 ticks per rung, so opcode 0 costs 3 and opcode 15 costs 48 — better than a trie *only* if opcode frequency is skewed and hot opcodes sit first."},
            {"name": "stacked `` `NN` `` literals in the lanes", "verdict": "a load error", "why": "four stacked literal rows put backticks in the same **column**, forming unintended *vertical* pairs. Bare digits instead."},
        ],
        "commits": ["8f3c6ed"],
        "sources": ["littleman/ARCH.md §2.2, §2.4"],
        "artifacts": [],
    },
    {
        "id": "cpu-fixed-width-words",
        "date": "2026-07-24",
        "group": "cpu",
        "block": "Fetch",
        "title": "Fixed-width 2-word instructions — the change that made the CPU close geometrically",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "If instructions are variable-width, an operand-taking lane must read the ring from *inside the lane* — and a lane sitting 4 rows from the input pipe finds that pipe nearer than the ring, so the operand fetch silently reads program input instead. Making every instruction 2 words moves all ring access into the fetch stage, so each lane needs only the one pipe its own micro-program uses.",
        "numbers": [("cost", "", "extra ring words", "buys a CPU that works")],
        "alternatives": [
            {"name": "variable-width (one word for a zero-operand opcode)", "verdict": "a silent wrong answer", "why": "`>rbr` unconditionally takes two words, so `LDI 42 / OUT / HALT` pairs up as `(LDI,42),(OUT,HALT)` and emits 42 forever. The generator pads to two words **and rescales every skip count**, since the assembler resolved them in variable-width positions."},
            {"name": "bit-packed operands", "verdict": "no", "why": "extracting one needs `}`, which needs `B`, which holds ACC."},
        ],
        "commits": [],
        "sources": ["littleman/ARCH.md §5.2, §2.7"],
        "artifacts": [],
    },
    {
        "id": "cpu-return-path",
        "date": "2026-07-25",
        "group": "cpu",
        "block": "The return path",
        "title": "The return path was 25% of the CPU, and it was a placement mistake",
        "status": "shipped",
        "era": "contest",
        "problems": ["brackets", "plotter", "tcp", "gradebook"],
        "what": "The first heat-map profile said 25% of all CPU time was the return path — pure walking, no work. The collector had been placed *below* the structures band, so the riser was 38 cells where the lane band alone needs 16. Moving the collector directly under the lane band and making slab exits **rise** into it instead of dropping past it took it to 17.9%.",
        "before": """
  [ lane band 16 rows ]
  [ structures band   ]
  [ collector         ]  <- riser is 38 cells, paid twice per instruction
""",
        "after": """
  [ lane band 16 rows ]
  [ collector         ]  <- riser is 16; slabs RISE into it
  [ structures band   ]
""",
        "numbers": [
            ("brackets", "62,930", "55,986", "-11.0%"),
            ("plotter", "482,933", "421,917", "-12.6%"),
            ("tcp", "96,923", "92,383", "-4.7%"),
            ("gradebook", "694,713", "681,441", "-1.9%"),
        ],
        "alternatives": [
            {"name": "fill the drop column with `v` all the way down", "verdict": "breaks a program", "why": "where it crosses a slab's westbound entry row it turns that man *south*, into the middle of the drop. Only a turn cell may be an arrow; the body of a drop or riser must be `.`, the one glyph two men crossing in different directions both survive."},
            {"name": "rise at `base + 2`", "verdict": "breaks a program", "why": "that is exactly the column where each branch arm parks its `W`, so the returning man walks through a register swap. It rises at `base + 11`."},
        ],
        "commits": ["e0d9574", "f7e2258"],
        "sources": ["littleman/ARCH.md §2.9"],
        "artifacts": [],
    },
    {
        "id": "cpu-lane-order",
        "date": "2026-07-26",
        "group": "cpu",
        "block": "Lane layout",
        "title": "`LANE_ORDER` — order the lanes by how often each opcode actually runs",
        "status": "shipped",
        "era": "contest",
        "problems": ["brackets", "gradebook", "matmul", "sudoku-validity"],
        "what": "Weighting each lane by its opcode's measured execution count and minimising `walk = (drop_x - lane_end) + (collector - row) + (drop_x - 1)` resolves a genuine two-sided tension that a length rule cannot see: a **hot** lane wants to sit low (both `-row` and `2*drop_x` improve at once) while a **long** lane wants to sit high (every lane above pays for its extent).",
        "numbers": [
            ("brackets", "26,000 ticks", "25,111", "0.966x on score"),
            ("gradebook", "12,996 fp / 301,571 t", "12,769 / 298,571", "0.973x — lane order moved the *box* too, 114 -> 113 columns"),
            ("matmul", "120,714", "118,638", "0.988x"),
            ("sudoku-validity", "434,667", "432,167", "0.994x"),
        ],
        "alternatives": [
            {"name": "order by *true east extent* rather than micro-program length", "verdict": "uniformly ~0.2% worse", "why": "the drop staircase is not what sets the width — for `brackets` the width is floored by the structures band, so lane order changes only *which* row each opcode gets."},
            {"name": "an unconstrained search over walked cells", "verdict": "lost on score", "why": "it 'won' 16% of walked cells and widened the CPU three columns. **Width has to be a constraint, not a term.**"},
        ],
        "commits": ["0ac03c6", "cda0eae"],
        "sources": ["littleman/ARCH.md §7.6"],
        "artifacts": [],
    },
    {
        "id": "cpu-16-opcode-cliff",
        "date": "2026-07-26",
        "group": "cpu",
        "block": "The opcode budget",
        "title": "The sixteen-opcode cliff — there is no partial credit",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man", "snake", "plotter", "pathfinder"],
        "what": "The lane band is `2*(1<<k) - 1` rows and `k = (len(used)-1).bit_length()`, so **19, 18 and 17 opcodes all give the identical 63-row band**. Only 16 or fewer reaches `k = 4` and 31 rows. Any route to the fold pays its whole cost before a single row appears — which is the main reason to price it before starting. `little-little-man` was folded from 19 to 16 by three separate removals.",
        "after": """
  19 opcodes, k=5:  32 leaf slots, 13 EMPTY, band = 63 rows
                    (and the empties are not spread out -- they collect
                     into one visible 28-row gap)
  16 opcodes, k=4:                          band = 31 rows
                                            ^^^ 25 rows off the HEIGHT,
                                            and the machine then flips
                                            width-bound so the ROM fold
                                            has room to work again
""",
        "numbers": [
            ("stand-in forecast", "193x193 = 37,249", "166x166 = 27,556", "-26.0%"),
            ("actually built", "192x194 = 37,636", "184x183 = 33,856", "**-16.6%** — the gap is the DSP relay's own 38x13 room"),
            ("ticks, for free", "6,890,324", "6,389,522", "-7.3% — a depth-4 trie is a shorter walk on every instruction"),
        ],
        "alternatives": [
            {"name": "reclaim the 13 empty leaves", "verdict": "impossible without changing `k`", "why": "leaf spacing is a property of the depth — `trie(level,row)`'s fan-out is `1 << (k-level)`. Reclaiming them means an *unbalanced* trie, which is a different decoder, not a tighter one."},
            {"name": "`SUBI n` == `ADDI (2**64 - n)` — free, removes an opcode with no extra instructions", "verdict": "**cannot be used**", "why": "`rom.digit_width` takes the maximum over *every* word and pads them all to it, so one 20-digit literal widens all ~3,400 ROM words from 4 digits to 20 and roughly triples the ROM — which already sets the machine's width."},
            {"name": "expect the ROM fold to move once 32 rows come off the height", "verdict": "measured non-move", "why": "swept 78..99: 89 and 90 tie and 89 stays the pick. Pinned in a test because the next person will guess the same thing."},
        ],
        "commits": ["5dd6831", "ae85919", "5a474dc"],
        "sources": ["littleman/LLM-DESIGN.md §The sixteen-opcode cliff"],
        "artifacts": [],
    },
    {
        "id": "cpu-dsp-fold",
        "date": "2026-07-26",
        "group": "cpu",
        "block": "The opcode budget",
        "title": "`DSPA`/`DSPD`/`DSPS` -> one `DSP p` behind a fan-out relay",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "The three display ports are three physical pipes and an `s` binds one of them *statically*, so a single opcode can only reach them through a room that fans out. `DSP p` had been in the ISA table at code 14 since the beginning with a working emulator handler — what made it 'impossible' was its *micro*, which sent one word and so left a relay no way to learn the port. It now sends two.",
        "after": """
  CPU lane --> [ DSP relay ]  ---> ADDR
                U, then X on        DATA
                sign(p-1):          SWAP
                -1 / 0 / +1
  three glyphs in one room instead of a wider word in every ROM literal
""",
        "numbers": [("opcodes", "19", "16 (with `MULI` folded too)", "-2 from `DSP` alone")],
        "alternatives": [
            {"name": "a selector carrying a sign", "verdict": "impossible", "why": "`rom.digit_width` rejects a negative literal, so a ROM operand is non-negative and `X` has nothing to test. The relay *makes* the sign itself with `M`, `` `1` ``, `W`, `-`."},
            {"name": "a two-way branch", "verdict": "leaves a port to luck", "why": "the middle arm is reached by zero, so all three of `X`'s exits carry traffic by construction."},
            {"name": "the relay as a probe with arms ending on `H`", "verdict": "0/14, and every check passed", "why": "every pipe bound, `check_bindings` passed, the machine built at exactly the right size — and it stalled to the cap on every case. A probe serves one request; a room serving every display op must **return its man to the read**. The relay is a closed circuit for that reason."},
        ],
        "commits": ["919f193", "5a474dc"],
        "sources": ["littleman/LLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "cpu-mem-pad",
        "date": "2026-07-27",
        "group": "cpu",
        "block": "Lane layout",
        "title": "`mem_pad` — 13-18 columns of pure geometry, paid twice per instruction",
        "status": "shipped",
        "era": "contest",
        "problems": ["all CPU builds", "deadman-3d"],
        "what": "The CPU is wider than it needs to be purely so a memory `r` binds to the tape's response pipe (east) rather than the ROM pipe (west) — that is what `mem_pad` is, and the width is paid *twice* per instruction (east to the drop column, west along the collector). `INPUT_NORTH` removes the west-wall input pipe that was forcing the memory band 39 columns east.",
        "numbers": [
            ("deadman-3d mem_pad", "39", "18", "-690k ticks a frame"),
            ("break-even for moving the response pipe north", "", "~70 extra pipe cells against ~26 saved per instruction", "instructions outnumber reads ~2.7:1"),
        ],
        "alternatives": [
            {"name": "assume the input wall is what forces the pad", "verdict": "no longer true", "why": "measured post-contest: it is the STORE *teleport* room at `rom_bottom+1..+4` needing a corridor >= 6 rows deep. Building with the input west is byte-for-byte the same box."},
        ],
        "commits": [],
        "sources": ["littleman/ARCH.md §2.9", "scratch/deadman3d-opt/METRICS.md"],
        "artifacts": [],
    },
    {
        "id": "cpu-trim-dead-lanes",
        "date": "2026-07-28",
        "group": "cpu",
        "block": "The opcode budget",
        "title": "`TRIM_DEAD_LANES` — delete the rows the empty trie leaves were holding",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The contest-era verdict was that the empty leaves 'cannot be reclaimed without changing k'. Post-contest that was built anyway: a non-uniform trie whose per-branch step is the leaf count below it, so a lane's row becomes its slot's **rank** among the used slots rather than the slot itself. Opt-in per slug, plus a top return bus.",
        "numbers": [("estimated when declined", "~-500k ticks", "built", "2 ticks per removed row per instruction")],
        "alternatives": [
            {"name": "bottom-fill the middle lanes into the trie's 11 spare slots", "verdict": "implemented and reverted", "why": "it drags `mem_out_row`/`resp_row` down beside the slab band, and the slabs' discard `r` must stay nearest the ROM pipe; every feasible pad re-inflates the walk it was meant to cut."},
            {"name": "a top return bus on the contest-era CPU", "verdict": "declined then", "why": "interior row 0 is the CPU's own wall — a top bus means re-basing the whole band, and with hot lanes already below the fetch row the upper lanes are the cold ones."},
        ],
        "commits": ["c90c315"],
        "sources": ["scratch/deadman3d-opt/METRICS.md"],
        "artifacts": [],
    },
    {
        "id": "cpu-ram-program",
        "date": "2026-07-28",
        "group": "cpu",
        "block": "Instruction supply",
        "title": "The RAM-program CPU: Stage 1 demand fetch, Stage 2 prefetch — and a measured **do-not-build**",
        "status": "rejected",
        "era": "post-contest",
        "problems": ["brackets", "gradebook", "deadman-3d"],
        "what": "Replace the looping drum with a stored program in a men-v3 RAM, boot-loaded from the ROM, so a jump is an address instead of a discard loop. Stage 1 (raw demand fetch) charges every instruction a store round trip and charges jumps nothing extra. Stage 2 free-runs `(pc, pc+1)` READ pairs and polls a jump-request pipe with `q`, so sequential supply hides behind execution and only a taken jump pays a bounded refill.",
        "after": """
  build              box        avg ticks   per instr   per taken jump
  baseline drum      89x60       23,207       122.1     ~105
  Stage 1 (demand)  116x321      65,232      ~374        +0        <- 2.81x
  Stage 2, grid     165x170      32,152       137.1     288.6      <- 1.39x
  Stage 2, 1-column 354x275      34,369       137.2     452.5

  `ticks ~= 820 + 374*instrs`, and words_skipped contributes NOTHING.
  That is the architectural point, measured.
""",
        "numbers": [
            ("Stage 1, brackets", "23,207 / 7,921 fp", "65,232 / 103,041 fp", "2.81x ticks, **13x footprint**"),
            ("Stage 2 sequential gap", "374 t/instr", "137 t/instr", "+12% over the drum's 122 — supply hidden, as designed"),
            ("DOOM projection", "", "-0.2..-1.1M ticks a frame against a >=2.7x footprint loss", "**do not build**"),
        ],
        "alternatives": [
            {"name": "a banked (compact) program store", "verdict": "**unsound**, not merely slow", "why": "the collector merges the column pipes with `R`, whose tie-break is **reading order, not arrival order**. Under queueing a westerly column's answer overtakes an easterly one, the addr-0 sentinel cuts ahead of stale words, and the flush exits early. Sequential streams survive by luck (address order == column reading order); backward jumps are exactly the case that breaks. `brackets`' 9/9 on the grid store was *lucky, not sound*."},
            {"name": "a single-column store (order-preserving by construction)", "verdict": "correct but geometry-bound", "why": "3 rows per word, so DOOM's 2,402 words is ~7,200 rows — 24x taller than today. `merge-free => single column => 3 rows/word` is now a theorem about this store family."},
        ],
        "commits": ["30a8294"],
        "sources": ["scratch/ram-program/METRICS.md"],
        "artifacts": ["scratch/ram-program/brackets_ram2sc.man"],
    },
    {
        "id": "cpu-seek-drum",
        "date": "2026-07-28",
        "group": "cpu",
        "block": "Instruction supply",
        "title": "The **seek drum** — keep the drum, give it random access for long jumps only",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The RAM work's real conclusion was that the tick win lives in the *jump mechanism*, not in replacing the store. So: keep the packed drum's ~3.3 cells a word for sequential supply, and add a gadget on every data-row transition (`q` then `d`/`a`). With no request pending the drum man walks straight into his row — 2 cells, the whole sequential tax. With one pending he is diverted down a cascade to a station that splits the request into `row` and `offset`, emits a **sentinel -1** then the offset, and enters the row down a pitch-2 ladder chosen by the row's parity.",
        "after": """
  drum row --> [q][d/a] --> row body ...        (no request: 2 cells)
                  \\
                   cascade --> collector --> riser --> STATION
                                                       K=128 in B
                                                       / splits row|offset
                                                       emits -1 sentinel
                                                       x on row parity
                                                       --> west/east ladder
  CPU side: send row*K+offset, flush the corridor to the sentinel with a
  two-glyph r/X sign loop (ACC is NEVER touched), read the offset, then run
  the stock 2x4 counted discard for it.
""",
        "numbers": [
            ("skip distance histogram (a DOOM frame)", "2,418 jumps skip 0-64 = 15.2% of words", "128 jumps skip 1024+ = **75.5%**", "186 of 2,610 jumps carry 84.5% of the bill"),
            ("canonical tour", "640,512,397", "520,564,274", "**-18.7%**, and 372x377 -> 382x382 *exactly square*"),
            ("taped tour", "1,250,728,623", "1,113,752,187", "**-11.0%**, 279x258 -> 295x269"),
            ("57-command walk, canonical", "327,726,821", "274,139,241", "-16.4%"),
        ],
        "alternatives": [
            {"name": "seek **every** jump (no split)", "verdict": "+25.8% a frame", "why": "a seek is a flat few hundred ticks and a discarded word is ~4.5, so short jumps lose badly. It *looks* like a win on a boot-inclusive gate purely because boot's 107 jumps skip ~3,900 words each — **a gate that includes boot cannot referee a per-frame change.**"},
            {"name": "split `BRZ`/`BRN` as well as `JMPF`", "verdict": "also a loss", "why": "each extra family costs a lane and a 13-column slab; the slab band pushes the memory block east and the pad it forces (17 -> 22 -> 29 -> 36) charges every memory instruction that walk twice over. `JMPF` alone carries 80% of the long-jump words for a third of the width. And `BRN` has no long jumps in steady state at all."},
            {"name": "lower the threshold below 256", "verdict": "a plateau, not a knee", "why": "the JMPF share is 68.1% at 64 and 67.9% at 256 — going lower buys 0.2 points while paying a flat ~1,140-tick seek for jumps whose discard was under ~1,150."},
            {"name": "place the seek slabs beside their originals", "verdict": "nothing binds", "why": "a classic discard's `r` must be nearer the ROM pipe than the STORE's response pipe; a seek slab has no `r` at all. The sixth slab's `r` sat 70 cells from `mem_resp` and 95 from `rom`. Classic slabs must stay **west** of seek slabs."},
        ],
        "commits": ["fc97866", "994b4f1", "886ea07"],
        "sources": ["scratch/ram-program/METRICS.md §Stage 3"],
        "artifacts": ["littleman/examples/deadman-3d.man"],
    },
    {
        "id": "cpu-code-banks",
        "date": "2026-07-24",
        "group": "cpu",
        "block": "Control flow",
        "title": "Code banks — one looping ROM per subprogram, so a call is a *turn*",
        "status": "parked",
        "era": "contest",
        "problems": ["little-little-man", "little-little-little-man"],
        "what": "A program does not have to live in one ROM. Give each subprogram its own looping ROM with its own pipe, and 'call subprogram k' becomes 'route the man to fetch-site k' — a turn, essentially free, instead of an O(P) word discard. Verified in hardware (`programs/two-roms.man`) and never wired into the generator.",
        "after": """
  +----+   +--------------------+   +----+
  |>1sv|>->|>rsrs..........rsrsv|<-<|>7sv|
  |^s2<|   |^..................<|   |^s8<|
  |@..^|   |@..................^|   |@..^|
  +----+   +--------------------+   +----+
   bank A        consumer            bank B
   which ROM a fetch reads is decided PURELY by where the `r` sits.
   Alignment survives switching -- every visit starts at word 0,
   provided the call consumes exactly one whole lap.
""",
        "numbers": [
            ("little-little-man projection", "22.2M ticks", "~13M", "~470 taken branches a case x ~1,900 recirculated words"),
            ("little-little-little-man CPU bracket", "5.92M ticks / 6.78e10", "~2.9M / ~3.3e10", "a real 2.2x — for a machine that does not exist"),
        ],
        "alternatives": [
            {"name": "treat it as a call stack", "verdict": "it is not one", "why": "there is no return address — 'call' is really 'switch fetch source', so these are *inlined call sites*. Recursion needs a return-address cell and a dispatch trie on it."},
            {"name": "let a block exit a bank early", "verdict": "desynchronises it", "why": "any path that abandons a bank mid-body leaves it misaligned for the next call; it must still drain its remaining words."},
        ],
        "commits": ["908305f"],
        "sources": ["littleman/ARCH.md §5.5"],
        "artifacts": ["littleman/programs/two-roms.man"],
    },
    {
        "id": "cpu-doom-forwarder",
        "date": "2026-07-28",
        "group": "cpu",
        "block": "The store seam",
        "title": "DOOM's response path: one forwarder room instead of three",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "Three chained rooms on the store's answer path, each costing its own walk, collapsed into one.",
        "numbers": [("taped", "", "-0.52%", "")],
        "alternatives": [],
        "commits": ["12ac19c"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "cpu-llm-slab-walk",
        "date": "2026-07-28",
        "group": "cpu",
        "block": "Control flow",
        "title": "`little-little-man` -1.44%: the walk TO a slab is not the discard in it",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["little-little-man"],
        "what": "Post-contest re-profiling separated the cost of *reaching* a jump slab from the cost of the discard loop inside it. The two had been pooled, and the approach walk turned out to be its own term.",
        "numbers": [("little-little-man", "", "-1.44%", "")],
        "alternatives": [],
        "commits": ["49cd6f8"],
        "sources": [],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# ROM
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "rom-snake-packer",
        "date": "2026-07-24",
        "group": "rom",
        "block": "Compression (`history-lesson`)",
        "title": "A rule-respecting tight packer for 2,810 fixed tokens",
        "status": "shipped",
        "era": "contest",
        "problems": ["history-lesson"],
        "what": "One man walks a serpentine of numeric literals and `s`ends each word. The packer honours the two codegen hazards exactly — digits reverse on right-to-left rows, and backticks pair on rows *and columns independently*, so stacked literals must never align their backtick columns.",
        "numbers": [("history-lesson", "21.5KB", "18.8KB", "-12%")],
        "alternatives": [],
        "commits": ["eaa46b1"],
        "sources": ["littleman/ARCH.md §4.2"],
        "artifacts": [],
    },
    {
        "id": "rom-base-n",
        "date": "2026-07-24",
        "group": "rom",
        "block": "Compression (`history-lesson`)",
        "title": "Base-1000, then base-128 with 8 bytes a word",
        "status": "shipped",
        "era": "contest",
        "problems": ["history-lesson"],
        "what": "`history-lesson` is **footprint-only** — its verdict comes back with no tick term at all — so ticks are free and the optimum is maximum compression at arbitrary decode cost. Pack the text into large-radix words and ship a decoder.",
        "numbers": [
            ("size", "18.8KB", "11.3KB", "base-1000"),
            ("score", "13,924", "9,409", "base-128 / 8 bytes per word"),
            ("final judged", "", "**8,100** at 90x90", "1/1 case, footprint-only"),
        ],
        "alternatives": [
            {"name": "the 151x124 ROM grid because it was 6x faster in ticks", "verdict": "a pure loss, and it cost a submission slot", "why": "the problem's `scoring` field says footprint-only. **Read it before optimising for the wrong axis.**"},
        ],
        "commits": ["d90fc6f", "efeced7", "3f5e468"],
        "sources": ["littleman/ARCH.md"],
        "artifacts": ["solutions/history-lesson/000000000008100_history-lesson.man"],
    },
    {
        "id": "rom-looping",
        "date": "2026-07-24",
        "group": "rom",
        "block": "Program supply",
        "title": "The looping ROM makes the code ring — and the write-back invariant — unnecessary",
        "status": "shipped",
        "era": "contest",
        "problems": ["all CPU builds"],
        "what": "The ring's only job is to present the program's words over and over in order, forever. It does that by *storing* them; a ROM man walking a closed loop does the same by *regenerating* them, and the CPU cannot tell the difference — same order, same PC-is-phase property, same jump mechanism.",
        "after": """
  +------+  +-+
  |>7s8sv|>>|O|     emits 9 7 8 9 7 8 ... indefinitely at 4 ticks/word
  |^..s9<|  +-+
  |@....^|
  +------+
                    code ring        looping ROM
  rooms             ROM + LOOP       ROM
  pipe capacity     >= P or deadlock none -- words are regenerated
  fetch             >rsbrsx          >rbr, for every program
  throughput        6 ticks/word     4 here; ~2.3 for a 14-word loop
  packing           constrained      unconstrained
""",
        "numbers": [("CPU footprint", "1,600", "1,444", "dropping the ring")],
        "alternatives": [
            {"name": "keep the ring for mutable program words", "verdict": "nothing given up", "why": "the only thing a ring still buys is self-modifying code or spill slots living in the ring. LM-1 uses neither."},
        ],
        "commits": ["41b6e38", "f31a885"],
        "sources": ["littleman/ARCH.md §5.3"],
        "artifacts": [],
    },
    {
        "id": "rom-packed-tokens",
        "date": "2026-07-25",
        "group": "rom",
        "block": "Program supply",
        "title": "Pack the CPU ROM with variable-width tokens",
        "status": "shipped",
        "era": "contest",
        "problems": ["all CPU builds"],
        "what": "`rom.digit_width` had been sizing every group by the widest word in the image, so a single wide literal charged every word. Pricing each token on its own value packs the drum to ~3.4 cells a word.",
        "numbers": [("judged points", "", "-1.63B", "across the CPU builds")],
        "alternatives": [],
        "commits": ["78fc658"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "rom-recirculation-cost",
        "date": "2026-07-26",
        "group": "rom",
        "block": "Recirculation",
        "title": "Recirculation is 36-52% of six different machines — and the rate is not what anyone assumed",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-man", "pathfinder", "snake", "gradebook", "matmul", "sudoku-validity"],
        "what": "A backward edge walks almost the whole program: a loop whose body is `body` instructions costs `P - body` words of discarding *per iteration*. The first version of this measurement sampled three cases a problem and multiplied by an assumed 6-9 ticks a word. Both inputs were wrong, in opposite directions, and the conclusion got *stronger*.",
        "after": """
  program              words/case   discard ticks   total      share
  little-little-man       764,936     3,671,693     7,009,707  52.4%  (engine)
  pathfinder              148,058       710,680     1,575,791  45.1%
  snake                    15,180        72,865       169,651  42.9%
  gradebook                 9,905        47,545       118,795  40.0%
  matmul                    4,868        23,366        62,796  37.2%
  sudoku-validity          16,304        78,260       218,224  35.9%
  plotter / brackets / tcp / palette                           <18%
""",
        "numbers": [
            ("the rate", "6-9 ticks a word (assumed)", "4.8 (measured)", ""),
            ("share moves when *other* terms move", "21.4% at a 3,416-tick store read", "52% after the banked store", "without one extra word being discarded"),
        ],
        "alternatives": [
            {"name": "ask 'does this program jump?'", "verdict": "the wrong question", "why": "`sudoku-validity` has no jumps at all and still spends 35.9% here — a taken *branch* discards through the same loop. The right question is 'does it loop?'"},
        ],
        "commits": ["b4288e1"],
        "sources": ["littleman/ROM-RECIRCULATION.md"],
        "artifacts": [],
    },
    {
        "id": "rom-discard-unroll",
        "date": "2026-07-26",
        "group": "rom",
        "block": "Recirculation",
        "title": "Unroll the discard loop two words to a lap — and why deeper is blocked",
        "status": "shipped",
        "era": "contest",
        "problems": ["all CPU builds"],
        "what": "`_discard_loop` became a 2x4 burst retiring two words per lap, taking the rate from 6 ticks a word to 4. A 2x(k+2) block retires `k` words in `2k+4` cells, so k=3 is 3.33 and k=4 is 3.0 — but the obstacle is *exactness*: `rom_words` guarantees every count is **even**, which is what makes k=2 total.",
        "numbers": [("rate", "6.0 ticks/word", "4.0", "-33%")],
        "alternatives": [
            {"name": "a 7-cell `d,r,r,<,^,m,>` cycle counting *instructions*", "verdict": "**impossible**", "why": "a grid graph is bipartite, so every closed walk has even length — there is no 7-cell cycle. And what the loop costs is the cells the man *walks*, not the glyphs that do work, so dropping an `m` to a corridor cell leaves the same 8-cell lap."},
            {"name": "k=4, by padding every branch target to an even instruction index", "verdict": "not built", "why": "costs `NOP`s of `P`, which lengthens *every* discard, against 4.8 -> ~3.8 ticks a word (~11% of `little-little-man`, 7-10% of five others)."},
            {"name": "k=3 with a remainder arm / two exits in one lap", "verdict": "not built", "why": "two escape corridors out of one slab, and `_SLAB_PITCH`/binding has to still hold."},
        ],
        "commits": ["b016681"],
        "sources": ["littleman/ROM-RECIRCULATION.md §Still open"],
        "artifacts": [],
    },
    {
        "id": "rom-buffer-corridor",
        "date": "2026-07-26",
        "group": "rom",
        "block": "Recirculation",
        "title": "Buffer the ROM corridor (ROM-PLUS) — a big win on one machine, flat on every other",
        "status": "superseded",
        "era": "contest",
        "problems": ["little-little-man", "brackets", "tcp", "gradebook"],
        "what": "A pipe *is* a FIFO whose capacity equals its length, so the corridor from the ROM to the fetch row is a queue of words already in flight. Making it hold a whole program means a backward jump drains a **pre-filled buffer** instead of pacing the man. It bought 25% on `little-little-man` at the time it landed — and when re-measured as a general feature it is flat.",
        "after": """
  discard cost per word = max( 6 ticks CPU loop , ROM emission ticks/word )

  packed   : max(6, 3.36) = 6.00   CPU-bound   <- the ROM man is already hidden
  unpacked : max(6, 7.00) = 7.00   ROM-bound   (+16.7%, measured +3.5%)

  ... so buffering the corridor changes nothing, and so would a repeater
  at 2 ticks/word: max(6, 2) is still 6. Both blocked by the same nozzle.
""",
        "numbers": [
            ("little-little-man, when it landed", "313,589,490,978", "234,535,216,575", "-25%, 28/28"),
            ("brackets + a 357-word buffer", "24,206", "24,549", "**+1.4%**"),
            ("tcp / gradebook + buffer", "88,168 / 295,933", "same", "**0.0%**"),
        ],
        "alternatives": [
            {"name": "keep it on as a general feature", "verdict": "off by default", "why": "free when off (every machine byte-identical) and free on a **width**-bound machine where the band it snakes through is dead space — but on a **height**-bound one it is pure loss: `little-little-man` at 203x204 pays +6.0% at 400 words, +16.3% at 1,069, +55% at a full program."},
        ],
        "commits": ["36a8996", "149272d"],
        "sources": ["littleman/ROM-RECIRCULATION.md"],
        "artifacts": [],
    },
    {
        "id": "rom-drain",
        "date": "2026-07-26",
        "group": "rom",
        "block": "Recirculation",
        "title": "The DRAIN unit — 4.0 to 2.51 ticks a word, and it bought 0.18%",
        "status": "rejected",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "Built, wired in behind `DRAIN_UNIT_BITS`, measured — and a net loss, for the reason the whole document had been guessing at. The discard was never paying 4.0: this program's ROM is 3,498 words in a 17,480-cell packed lap, which is **5.0 ticks a word**, so `max(4.0, 5.0) = 5.0` and the CPU loop had been *idle* on `r` for a fifth of every discard.",
        "after": """
  build              box       area2    avg ticks   score
  today, counted     192x194   37,636   7,594,099   285,811,507,276
  unit_bits=2 drain  192x200   40,000   7,580,362   303,214,497,143
                     ^^^ +6 rows (the CPU sits ABOVE the display,
                         so a deeper slab pushes the display down)
                                        ^^^ -0.18% of ticks
""",
        "numbers": [
            ("ticks", "7,594,099", "7,580,362", "-0.18%"),
            ("area2", "37,636", "40,000", "+6.3%"),
            ("the producer, two independent methods", "engine A/B: 241 laps a case = 55.5% ROM-paced", "word count: 908,850 words a case = 59.8%", "agree to 8%"),
        ],
        "alternatives": [
            {"name": "turn it on later", "verdict": "the standing instruction", "why": "name the program in `DRAIN_UNIT_BITS` once the producer is under 4.0 cells a word, which is where `max(drain, ROM)` starts selecting the drain again."},
        ],
        "commits": ["114e85c", "9c2dd67"],
        "sources": ["littleman/ROM-RECIRCULATION.md"],
        "artifacts": [],
    },
    {
        "id": "rom-repeater",
        "date": "2026-07-26",
        "group": "rom",
        "block": "Recirculation",
        "title": "The ring repeater — designed to 1 word a tick, never built",
        "status": "parked",
        "era": "contest",
        "problems": ["little-little-man"],
        "what": "A little over half of `little-little-man` is the CPU standing on an `r` waiting for the ROM man to walk round. A ring stores one cell per word against the ROM's five and re-emits at ~2 ticks a word, so it is smaller *and* faster. Three facts fix its design, each measured rather than argued.",
        "after": """
  1. a relay is 2.00 ticks a word, and there is no cheaper cycle:
     +-+  +--------------------------+
     |I|>>| @rsrsrsrsrsrsrsrsrsrsrsrsH|
     +-+  +--------------------------+

  3. TWO banks reach 1.00 a word, with no merger man:
        r r      <- one word from bank A, one from bank B
        ^ ^         (nearest-pipe binding picks them apart
        |  \\___ south pipe    purely by where the cells stand)
         \\______ west pipe
     the image is already fixed-width (opcode, operand) pairs, so
     bank A holds even words and bank B odd ones -- and every
     discard count is even, so a drain lap takes one of each exactly.

  ROM lap                    ROM-paced   total    vs today
  17,480 (today)             4.21M       7.59M    --
   7,000 (repeater, 2 c/w)   1.69M       5.07M    -33%
   3,498 (ring, 1 c/w)       0.84M       4.22M    -44%
""",
        "numbers": [("projected", "7.59M ticks", "4.22M", "-44%")],
        "alternatives": [
            {"name": "two men on the corridor in opposite phases", "verdict": "**parity forbids it**", "why": "a room holds one `@`, so the second man comes from `Y`, whose children are born at `(x, y-1)` and `(x, y+1)`. Any path to row `y` column `c` has parity `|c-x| + 1`, so two children on one row are **always an even number of cells apart** — always the same phase on a period-2 `rsrs` corridor. No arrangement of detours escapes it."},
            {"name": "let the relay room take both the ROM's pipe and its own return", "verdict": "the hard part", "why": "`r` picks the nearest and would deadlock on an empty ring; `R` picks either and corrupts the order the moment both are ready. The way out is that the choice is *once*, not per-word: a counted **seeding phase** reads from the cell nearest the ROM, then falls into a steady loop reading from the cell nearest the return."},
        ],
        "commits": ["a5950ee"],
        "sources": ["littleman/ROM-RECIRCULATION.md §The repeater"],
        "artifacts": [],
    },
    {
        "id": "rom-opcode-slots",
        "date": "2026-07-29",
        "group": "rom",
        "block": "Drum density",
        "title": "`OPCODE_SLOTS` — relabel the trie's leaves, keep every lane exactly where it is",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "Under `TRIM_DEAD_LANES` a lane's row is its leaf slot's **rank** among the used slots, so *any rank-preserving relabelling leaves every row, drop column and lane tick where it was* and moves only `number = _bitrev(slot, k)`. DOOM uses 22 of 32 slots; exactly ten slots bit-reverse below ten, and a DP over slot x rank spends them on the hot opcodes. The contiguous default could not aim at that spread.",
        "after": """
  the drum, profiled:  P = 4,304 words in 19,912 cells = 4.626 cells/word
    (NOT the 3.36 the cost model quoted -- that was sudoku-validity's)

  opcodes were 44.8% of it: k=5 means codes 0..31, and a code >= 10
  costs `NN`s = 5 cells against Ns = 2.

  one-digit opcode words   610 / 2,152  ->  1,401 / 2,152
  opcode cells             8,930        ->  6,557
  whole drum               4.626 c/w    ->  4.075
  ROM block                284x94       ->  252x93
""",
        "numbers": [
            ("8-command gate", "61,826,043", "61,570,950", "-0.41%"),
            ("115-frame tour", "839,384,674", "838,737,298", "**-0.077%**"),
            ("trie walk (execution-weighted)", "64,444", "54,722", "-15% — *also* cheaper at decode, because the spread leaves fewer contracted single-child chains"),
            ("the ROM's floor on taped width", "286", "254", "**that reserve is what the change is for**"),
        ],
        "alternatives": [
            {"name": "re-number the store's addresses (961 of them, all three digits = 29% of the drum)", "verdict": "worth ~3,400 cells, **unsound**", "why": "`LDA` and `MOVA` compute addresses at *run time*, so a build-time image rewrite cannot see them; and the `.asm` is shared with the canonical tier, which must stay byte-identical."},
            {"name": "a smarter packer", "verdict": "greedy leftmost is already optimal", "why": "the 12% blank is the **vertical-backtick parity rule** (10 rows, 2,350 cells), not slide-waste — the even-words-per-row rule the seek protocol needs costs *zero*. A lookahead packer scoring against the next 4 and next 12 tokens returns the identical row count in 15 of 15 width x depth combinations."},
            {"name": "`SEEK_K` 128 -> 64 to shave a digit off 39 wide jump literals", "verdict": "unavailable", "why": "`SEEK_K` must exceed the words on any packed row, and the widest holds 66. Worth 39 cells if it were."},
        ],
        "commits": ["1777c61", "3b77cca", "b387609"],
        "sources": ["littleman/ROM-RECIRCULATION.md §The drum's contents"],
        "artifacts": [],
    },
    {
        "id": "rom-density-is-an-area-knob",
        "date": "2026-07-29",
        "group": "rom",
        "block": "Drum density",
        "title": "A 13% shorter lap bought 0.077% — the finding is worth more than the win",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The control settles it: adding **one blank cell per token** — pure lap inflation, nothing else changed — costs +1.09% on a boot-heavy 8-command gate for +18.2% of lap. So the whole drum is ~6% of that gate and **~0.6% of the 115-frame tour**. `max(6 ticks CPU loop, ROM ticks/word)` is comfortably CPU-bound at 4.6 cells a word, and DOOM's taped tier is store-bound besides.",
        "numbers": [("the drum's share of tour ticks", "", "~0.6%", "**density in the drum is an area knob for this machine and very nearly nothing else**")],
        "alternatives": [],
        "commits": ["e039cf8", "d1e72df"],
        "sources": ["littleman/ROM-RECIRCULATION.md"],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# DATAFLOW
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "df-the-ratio",
        "date": "2026-07-25",
        "group": "dataflow",
        "block": "The survey",
        "title": "A walked glyph is 1 tick; an issued LM-1 instruction is 46",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "The measurement that is the whole second half of the contest. Compile for coverage, hand-build for rank — and knowing the ratio is what let us stop optimising the compiler and start replacing it.",
        "after": """
  mechanism                                         ticks   ratio
  one glyph the man walks over (bespoke grid)           1      1x
  one rotation of a pipe ring                         6.0      6x
  one relay lap                                        ~6      6x
  one issued LM-1 instruction                          46     46x
  one tape access at N=97                            ~780    780x

  ...and a bespoke grid is 8x8..32x32 where a generated one is
  98x75..112x116, so max(w,h)^2 alone is a 12x-200x factor
  BEFORE a single tick is saved.
""",
        "numbers": [
            ("triangle", "471,744 generated", "960 by hand", "**492x**"),
            ("tcp", "1,463,308,918 compiled", "535,084 hand-built", "**2,735x**"),
        ],
        "alternatives": [
            {"name": "read `BLOCKED` as 'unsolved'", "verdict": "wrong", "why": "three of the four `BLOCKED` problems were *already* solved by generator-emitted dataflow grids beating every CPU machine in the repo by two to four orders of magnitude. `BLOCKED` meant 'no `.asm` solves it' — a statement about the compiler, not the problem."},
            {"name": "'put it in a ring' and 'delete the CPU' as one lever", "verdict": "keep them separate", "why": "`palette` is 92% instruction issue and 7% tape: a ring buys it **nothing**, and a bespoke grid buys it ~500x. A ring is a memory optimisation; deleting the CPU is a compute one."},
        ],
        "commits": ["f882f10"],
        "sources": ["littleman/DATAFLOW-SURVEY.md"],
        "artifacts": [],
    },
    {
        "id": "df-tcp-ring",
        "date": "2026-07-25",
        "group": "dataflow",
        "block": "`tcp`",
        "title": "`tcp` rebuilt as a 17-word ring machine — 168x, then 2,735x",
        "status": "shipped",
        "era": "contest",
        "problems": ["tcp"],
        "what": "80% of the compiled machine's ticks were tape. But the access pattern is 'write at offset <= 15 from the head', then drain sequentially from the base — it is a **shift register that had been implemented as a rotating tape addressed through an adapter**. Resident state is 16 slots using `val = 0` as the empty sentinel (legal: `1 <= val <= 999`).",
        "numbers": [
            ("compiled", "112x78, 1.138e9", "39x39, 7.85e6", "**168x**"),
            ("then squared", "42x41 = 7.85e6", "39x39 = 6.76e6", ""),
            ("final, two-tape queue", "", "17x17, 1,852 avgTicks, **535,084**", "20/20"),
        ],
        "alternatives": [],
        "commits": ["94d5aef", "8bdb268", "b136b58"],
        "sources": ["littleman/DATAFLOW-SURVEY.md §3"],
        "artifacts": ["solutions/tcp/000000000535084_tcp.man"],
    },
    {
        "id": "df-brackets-one-register",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`brackets`",
        "title": "A pipe cannot be a stack — so pack the whole stack into one integer",
        "status": "shipped",
        "era": "contest",
        "problems": ["brackets"],
        "what": "`s` enters the source end and `r` leaves the destination end, so a single pipe is strictly FIFO and popping the newest entry from a ring of depth `k` costs `2(k-1)+1` ring ops. But `brackets` never needs pop-newest as a *pipe* operation: keep the depth in `BP` and the tag stack as a base-4 integer in `B`. `mask = mask*4 + tag` pushes; `mask/4` pops, because `/` leaves the remainder in `B` — **which is exactly a pop**.",
        "after": """
  push:  B = mask, A = tag,  ++++M   ->  B = mask*4 + tag
         (multiplication by a small constant is repeated `+`,
          because B survives it -- no third register needed)
  pop:   `4`, /                     ->  A = mask/4, B = tag

  depth 32 x 2 bits = 64 bits: it just fits, and the stack costs
  ZERO pipe traffic.
""",
        "numbers": [
            ("score", "1,222,788", "330,456", "**3.7x**, 26/26"),
            ("area", "9,604 (98x75 compiled)", "625 (25x25)", "**15x**"),
            ("earlier steps", "685k -> 219k (26x26, three hand-folded men)", "-> 123k (24x25, 197 ticks)", "3.1x then 5.6x"),
        ],
        "alternatives": [
            {"name": "the stack as a ring", "verdict": "O(depth) per pop", "why": "two pipes between two rooms give a ring, not a stack."},
            {"name": "a 25x14 attempt", "verdict": "16/26", "why": "a partial pass scores nothing."},
            {"name": "the block placer for the three-man layout", "verdict": "priced and rejected with numbers", "why": ""},
        ],
        "commits": ["d90a299", "4bd79ac", "d39ec4e", "fdb35ec"],
        "sources": ["littleman/DATAFLOW-SURVEY.md §3", "WRITEUP.md"],
        "artifacts": ["solutions/brackets/000000000330456_brackets.man"],
    },
    {
        "id": "df-sudoku-transpose",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`sudoku-validity`",
        "title": "Transpose the state store: 27 unit masks become 9 per-value words — and drop the CPU",
        "status": "shipped",
        "era": "contest",
        "problems": ["sudoku-validity"],
        "what": "The membership test *is* an adder: `mask + 2^v` carries out of bit `v` exactly when bit `v` was already set. The obstacle was addressing — three of 27 masks per round at non-sequential indices. Transposing the store to nine per-value words made the hot inner operation `r; +; s` with a branch on the carry, a station of ~6 glyphs, and the CPU became unnecessary.",
        "numbers": [
            ("dropping the CPU", "2.38e9", "8.82e6", "**270x**"),
            ("the ring machine", "28x28 / 12,076 ticks", "27x27 (9.47e6 -> 8.68e6)", "setup path merges into the return corridor"),
            ("folded", "27x27 = 8.82e6", "20x20 = 2.78e6", "3.2x"),
            ("judged", "2,384,422,055", "**2,815,180**", "847x"),
        ],
        "alternatives": [
            {"name": "bit-packing the masks", "verdict": "measured, loses", "why": ""},
            {"name": "a systolic variant", "verdict": "measured, loses", "why": "both priced against the 20x20 on 2026-07-27 and both lost."},
            {"name": "three split workers with three independent 9-mask banks", "verdict": "designed, geometry gate not met", "why": "optimistic ratio `0.47*4/17 + 0.37/3 + 0.16 ~ 0.394` = 2.54x faster, but it only beats the 89x94 grid if the tiled three-bank machine stays under ~150 cells a side."},
        ],
        "commits": ["205fa85", "ecbb098", "acdf3aa", "a43c380", "fbbaae5"],
        "sources": ["littleman/ARCH.md §8.2", "littleman/DATAFLOW-SURVEY.md"],
        "artifacts": ["solutions/sudoku-validity/000000002815180_sudoku-validity.man"],
    },
    {
        "id": "df-matmul-dense",
        "date": "2026-07-27",
        "group": "dataflow",
        "block": "`matmul`",
        "title": "`matmul` as a dense hand-built band machine",
        "status": "shipped",
        "era": "contest",
        "problems": ["matmul"],
        "what": "After the STREAM tier solved correctness, the hand-built band machine took the score: bands fitted rather than assumed, the hot lane made a fall-through, channel columns the router never reached trimmed away, and sixteen stages computing the 16x16x16 public case in 8,563 ticks.",
        "numbers": [
            ("layout priced by ticks, not rows", "7.0e8", "3.39e8", ""),
            ("narrow the strip-served bands", "3.39e8", "3.03e8", ""),
            ("fit the north band instead of assuming it", "2.865e8", "2.746e8", ""),
            ("the hot lane becomes a fall-through", "2.746e8", "2.463e8", ""),
            ("final judged", "1,464,201,360", "**232,294,501**", "6.3x, at 72x81"),
        ],
        "alternatives": [
            {"name": "the MAC rectangle *and* a one-row-per-pipe band", "verdict": "mutually exclusive", "why": "recorded as a measured dead end so it is not re-tried."},
            {"name": "folding the box stack into two columns / a stacked relay", "verdict": "out for every ring", "why": ""},
            {"name": "a systolic MAC chain", "verdict": "validated one stage deep, parked", "why": "no time to build the rest."},
            {"name": "narrowing `matmul`'s width from 85 to 71", "verdict": "worth exactly zero", "why": "it is charged from its *height*."},
        ],
        "commits": ["32d5bcf", "915f017", "71186b6", "d85a1fc", "13249da"],
        "sources": ["WRITEUP.md"],
        "artifacts": ["solutions/matmul/000000232294501_matmul.man"],
    },
    {
        "id": "df-matmul-stall",
        "date": "2026-07-27",
        "group": "dataflow",
        "block": "`matmul`",
        "title": "The stall that took four ruled-out causes and two phantom pipe mouths to find",
        "status": "shipped",
        "era": "contest",
        "problems": ["matmul"],
        "what": "A dense machine that built, loaded and bound correctly simply stopped. Four candidate causes were ruled out one at a time before the answer: two *phantom* pipe mouths the engine had minted from pipe legs running alongside room corners, plus four constants missing from a room that `check_room` could not see. **Counting the pipes the engine finds against the number drawn catches the whole family in one line.**",
        "numbers": [],
        "alternatives": [
            {"name": "a lane that lands on a block", "verdict": "must arrive facing it", "why": "a separate rule found in the same build."},
        ],
        "commits": ["f514d4b", "e679c0f", "3898045", "2ad88b7"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "df-snake-coprocessor",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`snake`",
        "title": "`snake`: a dataflow ring machine, then banked, then fused laps",
        "status": "shipped",
        "era": "contest",
        "problems": ["snake"],
        "what": "A ladder of six measured steps from the compiled display machine to the shipped 75x69: the ring machine, one corridor for two lanes with one target, block order chosen by corridor length, banking the room, spending the freed rows on width, and finally fusing the ring laps.",
        "after": """
  1.69e9  ->  4.07e8   a dataflow ring machine (all 5 cases)
          ->  3.95e8   shave the band to 9 rows
          ->  3.38e8   one corridor for two lanes with one target
          ->  2.62e8   choose block order by corridor length
          ->  2.52e8   restart the order search instead of lengthening it
          ->  1.21e8   bank the room, 70x129 -> 84x79
          ->  1.02e8   pin the banked build, 79x76
          ->  8.20e7   spend the freed rows on width, 75x70
          ->  7.42e7   fuse the ring laps (same 75 columns)
          ->  7.18e7   re-search the shape on the fused CFG, 75x69
""",
        "numbers": [("judged", "1,782,079,128", "**108,396,066**", "16.4x, at 75x69")],
        "alternatives": [
            {"name": "a further 8.8e7 build", "verdict": "too late", "why": "built locally after the last submission window closed."},
        ],
        "commits": ["ff2bf10", "53dc00b", "3b11011", "750e64f", "ef4b681", "61f2d73"],
        "sources": ["WRITEUP.md"],
        "artifacts": ["solutions/snake/000000108396066_snake.man"],
    },
    {
        "id": "df-gradebook-cfg",
        "date": "2026-07-27",
        "group": "dataflow",
        "block": "`gradebook`",
        "title": "`gradebook`: fuse loops out of the CFG, then re-search the block order on the fused graph",
        "status": "shipped",
        "era": "contest",
        "problems": ["gradebook"],
        "what": "A parser-shaped problem, which is where bespoke grids get big — and footprint is squared. The win came from the control-flow graph rather than the geometry: fuse five loops out of it, refuse to *create* a new two-visit loop rather than only removing old ones, then re-run the order search on the fused graph.",
        "numbers": [
            ("sweep order and bank widths jointly", "", "-22% of ticks", ""),
            ("a flat turnaround + tick-searched order", "9.03e7", "8.27e7", ""),
            ("fuse five loops", "8.27e7", "7.55e7", ""),
            ("re-search the order on the fused CFG", "70x72", "70x70 (6.85e7)", ""),
            ("judged", "8,062,853,492", "**194,662,790**", "41x"),
        ],
        "alternatives": [],
        "commits": ["5dd6831", "5dbd8cf", "148a65c", "bc09515"],
        "sources": [],
        "artifacts": ["solutions/gradebook/000000194662790_gradebook.man"],
    },
    {
        "id": "df-memory-one-pass",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`memory`",
        "title": "`memory`: the one-pass ring worker, folded from 55x308 into 108x107",
        "status": "shipped",
        "era": "contest",
        "problems": ["memory"],
        "what": "Three shapes were built. A 31x31 ring was small but slow; a 55x308 one-pass worker was fast but enormous; folding the one-pass worker into 108x107 took the product. Then a ladder of narrowings on the grid itself — a third of the ticks had been the *igniter*.",
        "after": """
  M columns of N man-cells    ->  34,543,367   (correct the per-op claim,
                                                then narrow the columns)
  collector is two glyphs, so its room is two columns -> 30,708,348
  stop the collector at the last band it serves        -> one row, not two
  the collector room was never needed                  -> 20,510,172
  the igniter's increment is one glyph                 -> 19,973,628
""",
        "numbers": [("judged", "40,628,384", "**19,973,628**", "2.0x, at 108x107")],
        "alternatives": [
            {"name": "a flat router", "verdict": "exists and costs 3% — kept, not shipped", "why": ""},
            {"name": "the 31x31 ring", "verdict": "smaller, slower", "why": "the product decided it."},
        ],
        "commits": ["f9cdecf", "c6f5f17", "3bcaf47", "2fb5aca", "008c464"],
        "sources": ["WRITEUP.md"],
        "artifacts": ["solutions/memory/000000019973628_memory.man"],
    },
    {
        "id": "df-pathfinder-bitparallel",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`pathfinder`",
        "title": "`pathfinder`: bit-parallel BFS on four 64-bit words, distance mod 3, and `g = 255 - p`",
        "status": "shipped",
        "era": "contest",
        "problems": ["pathfinder"],
        "what": "The language has `&`, `|`, `~`, `{`, `}`, which no earlier problem needed — so a 16x16 board is four 64-bit words rather than a 256-cell array, and one BFS level is ~16 word operations instead of a queue over 256 tape cells. The bespoke grid then adds two ideas only a dataflow machine can spend: **distance mod 3** in three label planes (the grid is bipartite, so three residues suffice, and *pushing the planes back into the ring in a different order is the rotation* — relabelling is free), and **`g = 255 - p`**, a 180-degree board rotation that keeps every plane word non-negative so the bit tests are branch-free.",
        "after": """
              LM-1               bespoke grid
  board       4 words in tape    the same 4 words, in a ring
  BFS         level per round    distance MOD 3, three label planes
  distances   4 direction masks  none -- roles ROTATE as planes re-enter
  walls       stored             free = NB | S1 | S2 | S3, derived
  path        28 slots, read back   NOT STORED AT ALL
  grid        180x184 = 33,856   84x175 = 30,625
  fixed/case  1,680,572          51,842      <- 32.4x
  per move       61,159           4,154      <- 14.7x
  15M cap at     218 moves        3,598      <- 16.5x
""",
        "numbers": [
            ("bitplane BFS, one word per row", "17/18 — scores nothing", "", ""),
            ("bit-parallel on four words", "", "18/18", ""),
            ("judged", "unscored (17/18)", "**10,636,538,807**", "the CPU version does not even finish"),
        ],
        "alternatives": [
            {"name": "the cross-band placer", "verdict": "wedges on real floorplans", "why": "superseded by band-grid layout with anchors derived from the loop order."},
            {"name": "guard a word's block on an empty frontier", "verdict": "worse: 4.99M -> 5.06M", "why": "a word can only be skipped when its own frontier *and both neighbours'* are empty, and the frontier occupies ~1.9 of 4 words *contiguously* — so the guard almost never fires."},
            {"name": "more unrolling", "verdict": "nearly spent", "why": "(2,4) -> (4,8) -> (8,16) moves the per-move jump cost from ~1,174 to ~807 to ~638 words, i.e. ~5% of the per-move cost for a `P` that nearly triples."},
            {"name": "a paint-only PATH coprocessor", "verdict": "built, 34.3% off CPU score, never finished into a submission", "why": "still ~8x behind the bespoke machine."},
        ],
        "commits": ["508c508", "f20af31", "271381d", "2d4afb7"],
        "sources": ["littleman/ARCH.md §8.3"],
        "artifacts": ["solutions/pathfinder/000010636538807_pathfinder.man"],
    },
    {
        "id": "df-pathfinder-ten-steps",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`pathfinder`",
        "title": "Ten incremental compactions, each one archived under its judged score",
        "status": "shipped",
        "era": "contest",
        "problems": ["pathfinder"],
        "what": "Not one idea but a disciplined ladder, every rung sent to the judge: widen the ring relay, widen then max out the scratch relays, trim a channel column, compact the channel to its floor, reuse safe literal columns, tune then use the best send anchors, swap the up and right blocks, move setup beside its dispatch.",
        "numbers": [
            ("1 widen the ring relay", "11,096,155,486", "11,091,197,639", ""),
            ("2-3 scratch relays", "11,091,197,639", "11,068,577,674", ""),
            ("4-6 channel + literal columns", "11,068,577,674", "10,914,234,479", ""),
            ("7-8 send anchors", "10,914,234,479", "10,914,173,229", ""),
            ("9-10 block swap + setup placement", "10,914,173,229", "**10,636,538,807**", ""),
        ],
        "alternatives": [
            {"name": "the south-wall row drop", "verdict": "reverted", "why": "main's route compaction had made it a pure loss."},
        ],
        "commits": ["34c1511", "0868aeb"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "df-sort-reverse-rings",
        "date": "2026-07-27",
        "group": "dataflow",
        "block": "`sort-numbers` / `reverse-a-list`",
        "title": "Feed the ring from the relay, then carry two values a lap",
        "status": "shipped",
        "era": "contest",
        "problems": ["sort-numbers", "reverse-a-list"],
        "what": "Both problems started as 25x25 and 19x18 rings straight off the primitives. Feeding the ring *from the relay* rather than through its own port took them to 14x14; a pair ring carrying two values a lap took `reverse-a-list` to 13x13; and testing the loop once per **lane** instead of once per value took the ticks again.",
        "numbers": [
            ("sort-numbers, relay feed", "17x17, 4.86e5", "14x14, 2.92e5", ""),
            ("sort-numbers, one loop test per lane", "291,844", "258,188", "-11.5%, at zero rows"),
            ("sort-numbers judged", "3,273,525", "**413,066**", "7.9x"),
            ("reverse-a-list judged", "513,410", "**34,535**", "14.9x"),
        ],
        "alternatives": [
            {"name": "a counterpipe variant of `reverse-a-list`", "verdict": "2x worse, archived anyway", "why": "66,983,000 against the 61,447 that shipped — kept in the archive because a measured loss is a result."},
            {"name": "a local average as a score", "verdict": "wrong", "why": "the judge runs twenty cases; we ran eight."},
        ],
        "commits": ["78337b5", "f855430", "1836b59", "9554be2"],
        "sources": ["WRITEUP.md"],
        "artifacts": ["solutions/sort-numbers/000000000413066_sort-numbers.man", "solutions/reverse-a-list/000000000034535_reverse-a-list.man"],
    },
    {
        "id": "df-lllm-hold-the-word",
        "date": "2026-07-27",
        "group": "dataflow",
        "block": "`little-little-little-man`",
        "title": "Hold the word, not the lap — 5.60e10 to 7.40e9",
        "status": "shipped",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "The store lap was **92% of all block visits**: an interpreted tick rotated `16H + 1` words to reach one cell. Packing eight classes to a 64-bit word makes the ring 32 words; making the rotation *relative* is then possible — but relative rotation **on its own buys nothing**, because an `rr`/`sr` pair rotates the ring by one and the commonest step of all is 'the same word again' (53% of ticks). So the word does not go back: it stays in the register file with a **hole** in the ring where it came from.",
        "after": """
  store variant                      word-moves a tick
  unpacked, full lap                       184
  packed 8/word, full lap                 23.0
  packed, relative, word RETURNED         22.4   <- the trap:
                                                    the read's own
                                                    rotation eats it all
  packed, relative, word HELD              6.6   <- 28x
""",
        "numbers": [
            ("score", "5.60e10", "7.40e9", "**7.6x** at the same charged side"),
            ("the store loop's share", "92% of block visits", "2% of ticks", ""),
            ("the panel moved east of the worker", "a 30-row band", "8 rows", "-22 rows for nothing"),
            ("bands 21 columns apart -> 12", "", "nine ticks off *every* block visit", ""),
            ("setup decode, 8 block visits per cell", "56% of ticks", "5 visits", "`WALL_BIAS` adds the wall-row flag to the byte *before* hashing it"),
        ],
        "alternatives": [
            {"name": "packing 8 classes per word, first attempt", "verdict": "abandoned, then rescued", "why": "packing needs three live values and a man has two hands. What unblocks it: the **register file is the third hand**, the accumulator rides pre-shifted so a cell's contribution is one `+`, and the word boundary is found by a **carry** (ACC starts at `1<<5` and shifts five bits a cell, so its sentinel reaches bit 40 on exactly the eighth cell) rather than by a counter there is no room for."},
        ],
        "commits": ["df0cdcb", "18c9351"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": ["tasks/solutions/little-little-little-man_ring.man"],
    },
    {
        "id": "df-lllm-cpu-or-ring",
        "date": "2026-07-26",
        "group": "dataflow",
        "block": "`little-little-little-man`",
        "title": "CPU or ring? — priced without writing the interpreter, and it is a wash",
        "status": "rejected",
        "era": "contest",
        "problems": ["little-little-little-man"],
        "what": "`lm1.machine.build` synthesises a machine from a `Program` and reports its dimensions, so a CPU can be priced against a *synthetic* program of the right size and opcode mix. Footprint says yes — a CPU is 4.3x smaller. Ticks say no, and ticks decide it.",
        "after": """
                        area2     ticks      score
  the ring, measured    49,284    1,460,882  7.20e10
  CPU, 300 instrs        9,025    5.92M      5.34e10
  CPU, 600 instrs       11,449    5.92M      6.78e10   <- break-even
  CPU, 900 instrs       14,161    5.92M      8.38e10   -- worse

  a 4.3x smaller box buys 6%, because the CPU is 4.1x slower,
  and it goes NEGATIVE past ~700 instructions.
""",
        "numbers": [
            ("cost per interpreted tick", "CPU 24,934", "ring 24,972", "the fetch-decode-return tax gives back what dense control flow wins"),
            ("of the CPU's 5.92M", "68% is the setup loop", "3.08M of that is one loop closure recirculating `P - body` 256 times", ""),
        ],
        "alternatives": [
            {"name": "the first estimate, which omitted ROM recirculation", "verdict": "wrong by 2.4x", "why": "it put a CPU at ~2.5M ticks."},
            {"name": "code banks, which would take the CPU to ~2.9M / ~3.3e10", "verdict": "prices a machine that does not exist", "why": "a real 2.2x, on unbuilt hardware."},
        ],
        "commits": ["b6f8f65", "8379e73"],
        "sources": ["littleman/LLLM-DESIGN.md"],
        "artifacts": [],
    },
    {
        "id": "df-subset-sum-design",
        "date": "2026-07-25",
        "group": "dataflow",
        "block": "`subset-sum`",
        "title": "The design that unblocked the last unsolved problem — and why a ring alone could not",
        "status": "shipped",
        "era": "contest",
        "problems": ["subset-sum"],
        "what": "The CPU build answered 6 of 7 cases; the seventh needed 41,487 oracle iterations at ~19 instructions = 788,253 instructions, where `15M / 46 = 326,086` is all the cap buys **even with a free tape**. So deleting the tape gets 3.6x on a 34x gap. What closes it is the other lever — 46 ticks per instruction becoming ~1 tick per glyph.",
        "after": """
  the stack, three ways, priced:
   1. explicit stack ring       803,521 ring ops -- DOUBLES the traffic
   2. marks in the ring         L extra rotations per backtrack -- worse
   3. a LINKED LIST threaded through the ring cells   <- the answer
      when position p is taken, write the previous q into p's OWN cell.
      On backtrack, jump p -> q+1, and the last cell that rotation reads
      IS q's -- so recovering old_q and v[q] is FREE. Zero extra traffic,
      because the traversal that reaches q+1 already passes q.

  and no third register is needed either:
      suf[p+1] = suf[p] - v[p], with the SENTINEL holding -Total, so
      `suf <- suf - v_j` is correct EVERYWHERE (0 - (-Total) = Total).
""",
        "numbers": [
            ("direct lex-order DFS vs greedy+oracle", "41,516 iters, n oracle calls + sort + deletion + rebuild", "112,018 iters, **one loop**", "2.7x more iterations, far less machine"),
            ("the `r > suf[p]` prune", "189,702 worst iters (0.80x cap)", "112,018 (0.52x)", "kept — 0.80x is not a margin worth having"),
            ("judged", "51,103,406,206", "**5,218,553,037**", "9.8x"),
        ],
        "alternatives": [
            {"name": "a `Y` fork tree over include/exclude", "verdict": "blocked on value distribution", "why": "every live branch still needs the next value, and neither a tape read nor an input receive broadcasts inside a room. At depth 16 there are already 65,536 runners — the entire live limit."},
            {"name": "claiming a 2x margin", "verdict": "corrected to 1.2-1.5x", "why": "the ring term was costed at `b = 3.2` against the relay's real 6.0, and `a` had to include ~20-26 ticks of *parking* per iteration, since only `B` survives a receive."},
        ],
        "commits": ["754a2b0", "65cda6f", "48bdbe4"],
        "sources": ["littleman/DATAFLOW-SURVEY.md §4"],
        "artifacts": ["solutions/subset-sum/000005218553037_subset-sum.man"],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# I/O AND DISPLAY
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "io-panel-framebuffer",
        "date": "2026-07-25",
        "group": "io",
        "block": "The LM-75 panel",
        "title": "`SWAP <- 1` keeps `next` *and* the cursor — so a frame is a delta",
        "status": "shipped",
        "era": "contest",
        "problems": ["snake", "little-little-man", "little-little-little-man", "deadman-3d"],
        "what": "`plotter` and `palette` both commit with `SWAP <- 0`, which clears `next`, so both repaint every pixel every frame. That is not the only mode. Probed on the engine: paint one pixel, commit 1, paint a second, commit 1 — frame 2 holds **both**. A round-based display problem can treat the panel as a framebuffer that survives commits and write only what *changed*.",
        "after": """
  snake's tick:      2 pixels, not 256
  little-little-man: a man's move paints two pixels (the cell he leaves,
                     back to the colour of the class under it; his new
                     cell, 9); a value's move paints two.  There is no
                     raster sweep at all.
""",
        "numbers": [
            ("little-little-man, deleting the raster sweep", "40.6M ticks", "27.1M", "-33%"),
            ("the row-end padding loop deleted", "24.8M", "22.5M", "4.4M a case doing nothing — the panel starts black"),
        ],
        "alternatives": [
            {"name": "send an `ADDR` word before a full-board fill", "verdict": "unnecessary", "why": "the write cursor is 0 at power-on, *before* any `ADDR` is ever sent — probed with a grid that has no ADDR pipe at all."},
            {"name": "commit silently on a no-frame round", "verdict": "impossible", "why": "**every** `SWAP` emits exactly one frame, even an unchanged one, and one `SWAP` never emits two. A no-frame round is a *hardware obligation*: an extra commit desynchronises every later frame."},
        ],
        "commits": [],
        "sources": ["littleman/ARCH.md §4.4"],
        "artifacts": [],
    },
    {
        "id": "io-port-arrival-order",
        "date": "2026-07-26",
        "group": "io",
        "block": "The LM-75 panel",
        "title": "The port-order constraint is on **arrival ticks**, not on pipe lengths",
        "status": "shipped",
        "era": "contest",
        "problems": ["plotter", "snake", "pathfinder"],
        "what": "A value sent on tick `T` into an `L`-cell pipe is consumed during `T + (L-1)`, and within one tick the panel processes ADDR -> DATA -> SWAP. So the conditions are `ta+(La-1) <= td+(Ld-1)` and `ts+(Ls-1) >= td+(Ld-1)` — and **equality is safe in both**.",
        "after": """
  measured, all six on the engine's own frameJudge:
    swap == data                     -> pixel lands in the right frame   OK
    SWAP pipe 2 shorter, sent 2 later -> fine                            OK
    ADDR pipe 8 shorter, sent 8 later -> fine                            OK
    SWAP length 2 vs DATA length 6    -> the commit ARRIVES FIRST:
                                         blank frame, pixel one frame late
""",
        "numbers": [],
        "alternatives": [
            {"name": "`addr == data` / `swap >= data` as the rule", "verdict": "sufficient, not minimal", "why": "a safe special case of the inequalities above, for arms that send on consecutive ticks."},
            {"name": "reason about it from the drawn geometry", "verdict": "a whole silent-failure family", "why": "a stray `|` one cell behind a bend's arrowhead **deletes the whole pipe silently** — no load error, `analyze` just reports one pipe fewer, and the `s` binds a sibling."},
        ],
        "commits": ["737dc0e", "f97311c"],
        "sources": ["littleman/ARCH.md §4.4"],
        "artifacts": [],
    },
    {
        "id": "io-plotter-block",
        "date": "2026-07-25",
        "group": "io",
        "block": "`plotter`",
        "title": "Replace the CPU entirely with a line-drawing block",
        "status": "shipped",
        "era": "contest",
        "problems": ["plotter"],
        "what": "First the compiled machine had to be *made legal* — it was 6% over the step cap on the worst legal case, on a figure nobody had run. Three transformations took the inner loop from ~20 tape accesses per pixel to 4; then the CPU was deleted outright in favour of a dedicated painter, and a ladder of compactions took the block from 66.6M to 22.77M.",
        "after": """
  the three transformations, all verified over all 589,824 endpoint pairs:
   1. carry `addr = 32*y + x` instead of (x,y) -- the map is injective
      on the panel, so `x==x1 and y==y1` is exactly `addr == addr1`
   2. split on the MAJOR AXIS, making one of Bresenham's two error tests
      identically true -- two arms whose whole effect is ONE addition
      of a per-round constant
   3. pack err and addr into one word at radix 1024, so that addition is
      one ADD; the surviving test becomes sign(q) by folding the
      threshold into the packed value (it is a whole multiple of the
      radix, so it cannot disturb the low field); MODI 1024 recovers
      addr with NO access at all
""",
        "numbers": [
            ("20 rounds of the worst legal segment", "5.31M — **1.06x the cap**", "1.94M — 0.39x", ""),
            ("score", "12,544 x 483k = 6.06bn", "13,456 x 204k = 2.75bn", "-2.2x"),
            ("then the block", "7.76e9", "45.8M", "**169x**"),
            ("then compaction", "66,665,235", "**22,774,730**", "2.9x, at 44x56"),
            ("unrolling u = 1/2/4/6", "2,485,405", "2,075,485 / 1,894,525 / 1,846,233", "four is where it flattens"),
        ],
        "alternatives": [
            {"name": "a `DIVI`/shift opcode for the branch-free `sx = 2*floor((dx-1)/32) + 1`", "verdict": "ruled out by the opcode ceiling", "why": "16 opcodes is free, 17 is not."},
            {"name": "`privateTestCount: 0` as evidence a problem is safe", "verdict": "not evidence", "why": "it says 0 for every problem here, and it said 0 for `gradebook` too, which the judge then served a private case anyway."},
        ],
        "commits": ["6dc796c", "73e79c3", "b1b6337", "5ce05e5", "9940caf", "f52a07c", "60b2d7a", "ddc7d04"],
        "sources": ["littleman/ARCH.md §8.1", "littleman/PLOTTER-BLOCK.md"],
        "artifacts": ["solutions/plotter/000000022774730_plotter.man"],
    },
    {
        "id": "io-coprocessor-rules",
        "date": "2026-07-25",
        "group": "io",
        "block": "Coprocessors",
        "title": "A coprocessor must not answer back — and one that owns the display costs the CPU nothing",
        "status": "shipped",
        "era": "contest",
        "problems": ["snake", "pathfinder"],
        "what": "An incoming pipe is a rival for every `r` in the CPU, including the jump slab's ROM read, so a *replying* unit cannot be placed on a machine that has jumps at all — measured across all 4,800 (fold, `mem_pad`, `stream_pad`) combinations, always failing on that binding. Give the unit enough **authority** instead: `STEP` either moves the snake or ends the game.",
        "numbers": [
            ("snake, moving the body into the unit", "15,891,242,682", "3,369,020,288", "**4.7x** — and the box got *bigger*, 16,641 -> 18,496"),
            ("a `STEP` on a six-cell body", "~5,300 ticks (the tape scan)", "218 ticks", "~1.35 CPU instructions"),
            ("dropping to 16 opcodes with the unit", "158x167, 151,544 avg", "121x136, 122,264", ""),
        ],
        "alternatives": [
            {"name": "`matmul`'s STREAM block, which *does* reply", "verdict": "escapes only by containing no `JMPF`", "why": ""},
        ],
        "commits": ["fbc7472", "d452156", "98fcc27"],
        "sources": ["littleman/ARCH.md §8.0"],
        "artifacts": [],
    },
    {
        "id": "io-doom-unit",
        "date": "2026-07-27",
        "group": "io",
        "block": "deadman-3d (DOOM)",
        "title": "The DOOM painter unit — a baked sprite is one command word instead of ~130 CPU sends",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "`.unit doom`: COL / FLASH / HUD / COMMIT, one word each, so the paint loops, the 8-pixel flash and the 512-pixel HUD unroll all leave the CPU and the unit paints *concurrently*. Later arms — RUN (count bare DATA sends at the cursor), CURS (a bare cursor move), GUN/GUNF (baked sprites) — turn the whole HUD repaint into 15 pre-encoded constants.",
        "after": """
  arm     words/frame  share    pixels/frame  columns it occupies
  RUN        85.54     46.6%       641.6         4
  COL        64.00     34.9%     1,719.2        11
  CURS       31.92     17.4%           0         1
  COMMIT      1.00      0.55%          0         1
  GUN         0.88      0.48%       57.8        23
  GUNF        0.12      0.07%       11.0        33
          ^^^ the three arms carrying 98.9% of the words occupy 16
              columns; the two sprite arms carry 0.55% and occupy 56
""",
        "numbers": [
            ("baseline frame", "31,080,274", "5,213,912", "**5.96x** over six iterations"),
            ("V4 gun + live HUD", "", "+177k a frame (+2.1%)", ""),
            ("V3 textures", "", "+132k a frame (+1.6%)", ""),
        ],
        "alternatives": [
            {"name": "a `VRUN` one-word-per-pixel arm on the spare leaf", "verdict": "**costed before building, no-go**", "why": "76 sprite pixels x 4 instructions saved = 304 instructions on the *worst* frame = 1.6%, and 0.16% on the average one, against a ~5% bar plus a new trie leaf in every command word's decode. The design's '~30% off the sprite paint' was right about the *paint*; the paint is not a big enough share of a frame."},
        ],
        "commits": ["c834fe0", "722b248", "eae745d"],
        "sources": ["scratch/deadman3d-opt/METRICS.md"],
        "artifacts": ["littleman/examples/deadman-3d.man"],
    },
    {
        "id": "io-doom-hires",
        "date": "2026-07-28",
        "group": "io",
        "block": "deadman-3d (DOOM)",
        "title": "128x96 as four tiled 64x48 LM-75 panels behind a 1-of-4 router",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The panel is a fixed size, so a bigger viewport means *more panels*. Four 64x48 LM-75s tiled two by two behind a 1-of-4 address router, with the raycaster itself at 128x96 across the four tiles and monster billboards drawn at 2x across both seams.",
        "numbers": [
            ("hires ROM fold", "573x1155", "573x394", ""),
            ("then re-swept", "496x409", "496x353", "both new registries + a re-swept fold"),
        ],
        "alternatives": [],
        "commits": ["355c8c0", "c7d8ea4", "2f4cd1f", "2a20a64"],
        "sources": [],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# PARALLELISM
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "y-audit",
        "date": "2026-07-25",
        "group": "parallel",
        "block": "The split audit",
        "title": "All sixteen problems audited against `Y` — two 'high', and neither shipped",
        "status": "rejected",
        "era": "contest",
        "problems": ["all"],
        "what": "`Y` duplicates A, B and BP into two runners **without adding a room or a pipe**. It does not duplicate pipe values or tape state and provides no join. The profitable shape is therefore: build shared state in registers, split into disjoint corridors, let both children perform *independent side effects*, halt separately.",
        "after": """
  verdict      problems
  high         memory (bank selector -- implemented, proven, not shipped)
               sudoku-validity (three identical test-and-sets)
  medium       gradebook (only in a banked rewrite)
  conditional  subset-sum (blocked on value distribution)
  low/none     the other eleven

  three implementation rules that fell out:
   * small rooms: `Y` as a one-cell register fan-out, when each child
     terminates in a DIFFERENT side effect
   * large rooms: do not split the CPU while leaving one tape --
     split or specialise the DATA PLANE too
   * identical subfunctions are NOT sufficient: they must have
     independent inputs/state, or commute through a side effect
""",
        "numbers": [("creation order, proven on three validators", "two children reach one pipe on one tick", "output is exactly `[2, 1]`", "the right child keeps the parent's slot, the left runs last")],
        "alternatives": [
            {"name": "the `memory` two-bank selector", "verdict": "proven, and the geometry gate says no", "why": "modelled rotation falls 800 -> 400 ticks for 102 pipe cells against 101. But the placed machine must stay under **43 cells** on its longest side (`31 x sqrt(2)`) merely to tie the 31x31, and two independent workers plus routing do not meet that. Measured, halving the cells saved only 18% — fixed setup, relay cadence and pipe phase dominate."},
        ],
        "commits": ["691f777", "f772da6"],
        "sources": ["littleman/ARCH.md §8.2"],
        "artifacts": [],
    },
    {
        "id": "y-spawn-ladder",
        "date": "2026-07-27",
        "group": "parallel",
        "block": "Verified gadgets",
        "title": "The spawn ladder — n men with backpacks n-1..0, one every 4 ticks",
        "status": "shipped",
        "era": "contest",
        "problems": ["reverse-a-list"],
        "what": "Two `Y`s four cells apart on one closed loop, with an `m` and a `d` alternating all the way round so the test always fires on the value just handed out. The split inherits the backpack, so **the countdown is a free side effect of the loop counter**.",
        "after": """
  d m > Y        (0,0) d  corner: BP>0 east (stay), BP=0 north (exit)
  Y < m d        (1,0) m  BP -= 1
                 (2,0) >  MERGE POINT -- a nop for the loop, and it
                          re-aims anything arriving from outside
                 (3,0) Y  keeper south, worker born NORTH facing north
   period 8, two Y's four cells apart -> uniform spawn interval 4
""",
        "numbers": [("verified", "n = 1, 2, 3, 5, 8", "exactly n men, no runaways", "")],
        "alternatives": [
            {"name": "a 3x2 loop (period 6, interval 3)", "verdict": "runs away on half of all n", "why": "one cell tighter, but only two non-corner cells — it cannot hold both `m`s *and* a merge point."},
            {"name": "omit the `>`", "verdict": "the init man has nowhere to join", "why": "every predecessor of a loop cell is another loop cell. Any tight loop that has to be *entered* needs a glyph that fixes the heading absolutely."},
            {"name": "read a pair before testing the backpack", "verdict": "17/40 on multi-round fuzz", "why": "the keeper blocks on `r` when the list runs out, then swallows the *next round's length* as a list value. Single-round cases still pass, because the judge stops once the output is complete. `Y` must be followed immediately by its `d`."},
        ],
        "commits": ["0cab629", "7c7dd6e", "b10c01a"],
        "sources": ["littleman/PRIMITIVES.md"],
        "artifacts": [],
    },
    {
        "id": "y-parity-wall",
        "date": "2026-07-27",
        "group": "parallel",
        "block": "Verified gadgets",
        "title": "The parity wall — why the delay ring cannot be small, and why it is not an effort problem",
        "status": "shipped",
        "era": "contest",
        "problems": ["reverse-a-list"],
        "what": "Two `Y`s `k` cells apart on one loop spawn workers whose birth *times* differ by `k` and whose birth *cells* differ in `x+y` parity by `k` as well — so their bipartite invariants `t+x+y` differ by `2k`, **always even, whatever the loop's shape or size**. Every burst-spawned worker therefore lands in the same parity class, and an `L`-cell ring offers one class only `L/2` slots.",
        "after": """
  carrying n values one-per-man forces  L >= 2n = 32
  -- exactly the ring already running, and NO re-routing shrinks it.
  Flight paths cannot help: adding cells changes index and time
  together, leaving `index - time` parity alone.

  only two things break the wall:
    * two INDEPENDENT spawn loops, phased an odd number of ticks apart
    * the PAIR CARRIER: `r M r ... s W s` -- one man, two values,
      emitted reversed, so half the men and half the ring
""",
        "numbers": [
            ("coupling law", "`E_i = T0 + c*i + F_i + s*(n-i) + Q_i`", "reverse order needs `s > c - dF - dQ`", "the delay ring can never be tighter than the spawn loop"),
        ],
        "alternatives": [
            {"name": "a transparent station for odd `n`", "verdict": "one cell short", "why": "a `d` with no `m` between it and the previous `d` can never fire, so it behaves as a plain turn for every man on the ring *except* one joining between the two with `BP = 0` — a station reachable by exactly one kind of man. What is unsolved is who becomes that man: the loop keeper is the only candidate that knows the list is exhausted, but it must `Y`, and a `Y` splits unconditionally. The 8x5 ring's three interior rows leave two free cells, one short of the guarded split."},
        ],
        "commits": ["0cab629"],
        "sources": ["littleman/PRIMITIVES.md"],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# TOOLING
# ═══════════════════════════════════════════════════════════════════════════

ENTRIES += [
    {
        "id": "tool-fast-littleman",
        "date": "2026-07-25",
        "group": "tooling",
        "block": "Verification",
        "title": "Build the verifier you can afford to run",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "The bundled wasm engine OOMs on our largest machines and boots a process per case. `FastLittleman` parses a grid once and runs a native C++ tick loop loaded into the Python process, compiled once and cached by source hash. That is what made a 14-case sweep a 90-second routine — and sweeping parameters possible at all.",
        "numbers": [
            ("median speedup", "", "5.79x on `sudoku-validity`", ""),
            ("short workloads", "", "239x on `triangle`", "warm native cache"),
            ("parity", "all public cases, all 12 checked-in families", "identical verdicts **and exact tick counts**", "including `palette` and `plotter` frames"),
        ],
        "alternatives": [
            {"name": "replace the wasm engine entirely", "verdict": "no", "why": "`littleman.py` and `lm.mjs` stay the semantic oracle for differential tests, snapshots, stepping, analysis and routing. `FastLittleman` is validation-focused and does not replace those APIs."},
        ],
        "commits": [],
        "sources": ["AGENTS.md", "WRITEUP.md"],
        "artifacts": [],
    },
    {
        "id": "tool-sparse-scheduling",
        "date": "2026-07-27",
        "group": "tooling",
        "block": "Verification",
        "title": "Sparse scheduling in the fast engine — 245x on the big machines, tick-exact",
        "status": "shipped",
        "era": "post-contest",
        "problems": ["deadman-3d"],
        "what": "The DOOM machines run hundreds of millions of ticks over hundreds of live men, and stepping every man every tick is almost all wasted work. Sparse scheduling only advances men that can actually move — and it is tick-exact, so every measurement in the post-contest track is comparable to the contest ones.",
        "numbers": [("big machines", "", "**245x**", "tick-exact")],
        "alternatives": [],
        "commits": ["dbdde60"],
        "sources": [],
        "artifacts": [],
    },
    {
        "id": "tool-profiler",
        "date": "2026-07-25",
        "group": "tooling",
        "block": "Profiling",
        "title": "The heat-map profiler — and the two traps that made its first answers wrong",
        "status": "shipped",
        "era": "contest",
        "problems": ["all CPU builds"],
        "what": "`tools/heatmap.mjs` samples every runner's cell as the engine steps and `lm1/profile.py` attributes those cells to the regions the generator recorded at build time. Two things had to be right before the numbers meant anything: **split by runner** (a *servant* blocked on its input is idle, not a bottleneck) and **name the cells** (a generated grid has ~10k cells and no comments).",
        "after": """
  plotter, one round (300k ticks), before any change:

    bucket        share   note
    lanes          47 %   but ~80% of each MEMORY lane is its blocked `r`
    return path    25 %   pure walking -- no work at all
    slabs         7.7 %
    trie          6.1 %
    fetch         1.4 %
""",
        "numbers": [
            ("the first pooled run", "'the adapter's `r` is 19% of all time'", "it is idle 89% of its life", ""),
        ],
        "alternatives": [
            {"name": "trust `critical_runner()`", "verdict": "pin the CPU's man by hand", "why": "it takes the **least-stalled** runner, and on a machine whose tape ring never stops walking that is the *tape's* man — reported as `tape 100%` with an all-zero rollup."},
            {"name": "profile an ungated run", "verdict": "fabricates the answer", "why": "`heatmap.mjs` passes no expected frames, so input is never gated and the CPU parks on the `IN` lane's `r` forever once input runs out. At `--cap 3000000` that invented 720,069 ticks — **24% of the profile** — as a lane doing no work in the scored run."},
            {"name": "use it on the biggest machines", "verdict": "cannot referee them", "why": "its wasm OOMs above a few million ticks and a `little-little-man` case needs ~12M."},
        ],
        "commits": ["e0d9574"],
        "sources": ["littleman/ARCH.md §2.9, §4.1"],
        "artifacts": [],
    },
    {
        "id": "tool-submit-archive",
        "date": "2026-07-25",
        "group": "tooling",
        "block": "Process",
        "title": "Make the submission tool unable to lose work",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "Every graded grid is archived as `solutions/<slug>/<zero-padded-score>_<slug>.man` plus a `.descr` carrying the verdict, the fingerprint, the commit and a free-form note. A listing sorts best-first and **a worse run can never overwrite a better one**. `send` verifies locally first and refuses a grid it has already sent, matched by hash against the archive.",
        "numbers": [("archived submissions", "", "152 across 16 problems", "each one a rung of a measured ladder")],
        "alternatives": [
            {"name": "read the score off the archive before submitting", "verdict": "changed to read the live score", "why": "the archive is a frontier, not a clock — filenames contain scores, not reliable timestamps."},
            {"name": "`Python-urllib`'s default User-Agent", "verdict": "Cloudflare `403 error code: 1010`", "why": "a browser-signature ban, not an auth failure. Cost real time to diagnose."},
        ],
        "commits": ["e1a3e1e", "5092d5e"],
        "sources": ["README.md"],
        "artifacts": [],
    },
    {
        "id": "tool-judge-factor",
        "date": "2026-07-27",
        "group": "tooling",
        "block": "Process",
        "title": "The local-to-judge tick factor is 1.096-1.098x — measured on every problem shipped",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "Local `optimize.verify` averages the public cases and the judge averages public *plus* private, and the private half runs ~19% heavier. So `judge_avg = 1.096 x local_avg` — confirmed on four consecutive submissions and used to predict the next one before sending it (estimated 9,632k, judge measured 9,629,487).",
        "numbers": [("prediction error", "", "0.2%", "the fourth consecutive submission that factor predicted")],
        "alternatives": [
            {"name": "establishing it on the final day", "verdict": "the one thing to do differently", "why": "until then every decision carried an error bar it did not need."},
        ],
        "commits": ["08f6b59", "963e621"],
        "sources": ["WRITEUP.md"],
        "artifacts": [],
    },
    {
        "id": "tool-route-check",
        "date": "2026-07-24",
        "group": "tooling",
        "block": "Correctness",
        "title": "Nearest-pipe binding is geometry, not readiness — so assert it after every move",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "`s` targets the nearest **outgoing** pipe and `r` the nearest **incoming** one, by Manhattan distance, ties by reading order, and *nearest*, not nearest-that-can-proceed. The failure mode is a **wrong answer with no error at all**, so every layout move is followed by a binding check.",
        "after": """
  put every pipe anchor on ONE wall and ties become arithmetically
  impossible:  distance from any interior cell is |x - col| + y + 2,
  the y term is common, so nearest-pipe collapses to nearest ANCHOR
  COLUMN -- a 1-D rule that holds at every row.
  Space the anchors so each adjacent pair's columns sum to an ODD
  number, and |x-c1| == |x-c2| is unsolvable in integers: the tie
  that "loses silently" CANNOT OCCUR BY CONSTRUCTION.
""",
        "numbers": [("pathfinder", "", "all 327 pipe ops asserted, not hoped for", "")],
        "alternatives": [
            {"name": "trust that the grid loads", "verdict": "not sufficient evidence", "why": "a branch's discard `r` once landed exactly 28 cells from *both* the ROM pipe and the tape's response pipe; the engine broke the tie by reading order and every taken jump blocked forever."},
            {"name": "count only lane glyphs", "verdict": "missed one", "why": "worth running on *every* pipe glyph, including the ones inside generated sub-structures."},
        ],
        "commits": [],
        "sources": ["littleman/ARCH.md §7.1, §8.3"],
        "artifacts": [],
    },
    {
        "id": "tool-debug-sidecars",
        "date": "2026-07-25",
        "group": "tooling",
        "block": "Debuggability",
        "title": "Generators emit their own debug sidecars — `--man` / `--html` / `--json` in one invocation",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "A generated `.man` carries no comments, so the generator is the only thing that knows what a cell means. Every generator writes all three artifacts in **one** invocation, so an overlay can never drift from the grid it describes. Alongside: `man_png` renders a grid as an image, and `manwalk` traces where the men actually walk, so corridors can be told from waste.",
        "numbers": [("test suite", "145s", "12s", "with overlays and submission dedup added at the same time")],
        "alternatives": [],
        "commits": ["0990222", "629f34d", "0f95f53"],
        "sources": ["littleman/DEBUGGING.md", "AGENTS.md"],
        "artifacts": [],
    },
    {
        "id": "tool-tests-assert-correctness",
        "date": "2026-07-26",
        "group": "tooling",
        "block": "Process",
        "title": "Tests assert correctness, not quality — and a pinned tick cost a real result",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "Do not assert a footprint, score or measured tick count as a recorded value: the judge already keeps our best submission, so an improvement must not be a test failure. Assert behaviour — outputs round by round, every pipe binds, a checked-in `.man` matches its generator, cases stay under the semantic cap, independent engines agree.",
        "numbers": [],
        "alternatives": [
            {"name": "pin each case's exact settle tick", "verdict": "cost a result", "why": "`test_lm1_matmul` asserted 'the recorded tick is enough, and one tick fewer is not', so a **faster** grid fails it with a message indistinguishable from a wrong product. `matmul` was struck from `LANE_ORDER` on that evidence and restored after checking the outputs directly. Confirm *which half* of such an assertion broke before concluding anything."},
            {"name": "treat a public-case pass as proof of correctness", "verdict": "it is not", "why": "nine cases is thin cover for a stack machine, and a reordered grid is *different hardware* running the same ISA. `brackets` gets random and boundary inputs past its public set."},
            {"name": "let `optimize.verify` default to the fast engine for a hardware bug", "verdict": "pass `lm=Littleman()`", "why": "a check whose job is to catch a hardware bug must use the oracle."},
        ],
        "commits": ["3f68866", "22a081d", "9ff12e4"],
        "sources": ["AGENTS.md"],
        "artifacts": [],
    },
    {
        "id": "tool-remeasure-constants",
        "date": "2026-07-26",
        "group": "tooling",
        "block": "Process",
        "title": "Re-measure every inherited constant — three of our largest wins were corrections",
        "status": "shipped",
        "era": "contest",
        "problems": ["all"],
        "what": "The pattern is the finding. Every one of these was a number carried forward from a different machine, a different case set, or a plan rather than a run.",
        "after": """
  constant                     believed        measured        cost of the error
  ring rotation                3.2 t/rot       6.0 as built    1.9x optimistic
                                                               on every projection
  the tape's cost              per SLOT        per ACCESS      plotter shipped 6%
                                                               OVER the step cap
  ROM emission (llm)           3.36 cells/w    5.00            the drain bought
                                                               0.18%, not 20%
  ROM emission (doom)          3.36 cells/w    4.626           a whole cost model
  discard rate                 6-9 t/word      4.8             conclusions got
                                                               STRONGER
  a machine's bounding box     193x171 (stale) 180x179         the last submission
                                                               of the contest, -13%
""",
        "numbers": [],
        "alternatives": [
            {"name": "average three cases and compare against a fourteen-case tick average", "verdict": "two errors pulling opposite ways", "why": "the producer's share came out at 42% instead of ~56%, and the wrongness did not show as an inconsistency until both methods were run over the same case set."},
        ],
        "commits": ["c1c52fd", "6c93333", "b4288e1", "d7f2399"],
        "sources": ["WRITEUP.md §Ideas that generalise"],
        "artifacts": [],
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# THE SHORT LAYER
#
# `GIST` is one plain sentence per entry — what the idea *is*, with no context,
# no file names and no numbers. It is what the page shows first.
#
# `GLYPHS` is the change written in **real littleman**: grid text taken from the
# shipped machines, from the engine-verified gadgets, or from the commit diff
# that made the change. Entries with nothing honest to show simply have none.
# ═══════════════════════════════════════════════════════════════════════════

GIST: dict[str, str] = {
    # ── footprint ────────────────────────────────────────────────────────────
    "fp-squared-objective": "Only the longer side of the box is charged, and it is squared — so trade width into unused height until the machine is square.",
    "fp-triangle-8x8": "Run the pipes up the *outside* of the room instead of stacking the I/O rooms above it.",
    "fp-rom-fold-square": "Fold the ROM one row deeper until the machine stops being wider than it is tall. That crossing point is the minimum.",
    "fp-unstack-memory": "The store was the easternmost block, so it — not the ROM — was setting the width. Move it below the CPU instead of beside it.",
    "fp-dead-lines": "Delete rows and columns that hold nothing, then re-check that every send and receive still talks to the same pipe.",
    "fp-ast-moves": "Search over room moves, pipe reroutes and loop squashes automatically, keeping only moves that pass every case and improve the score.",
    "fp-output-room-off-axis": "The output room was standing in the dimension being billed. Move it into the free one.",
    "fp-lllm-lane-rows": "The layout reserved three plumbing rows per block; branching blocks can never use one of them. Stop allocating it.",
    "fp-lllm-fallthrough-east": "If the next block starts to the east, the man can just drop a row and keep walking — no return lane, so the row disappears.",
    "fp-lllm-own-band": "Let a block begin at the band it actually uses instead of always at the far west edge.",
    "fp-lllm-wraps": "Three attempts to reclaim rows by re-shaping the program's text, all of which made the machine bigger.",
    "fp-subset-sum-turnarounds": "Rows were scarce, so spin every ring the other way round — each reversal trades a row band for a column band.",
    "fp-subset-sum-two-rooms": "Split one tall worker into two rooms sharing a single ring, so neither has to be tall.",
    "fp-llm-rom-refold": "Re-fold the ROM against the machine's *current* box, not the one it had when the fold constant was chosen.",
    "fp-lshaped-rom": "A quarter of the box was one empty rectangle. Bend the ROM into an L so it fills that hole instead of growing the box.",
    "fp-deadman-square": "Reshape a 330-cell single-column store into an 8-column block, and the machine goes from a tall ribbon to an exact square.",
    "fp-deadman-m7c": "Measure which block actually sets each dimension before cutting anything — then cut only that block.",
    "fp-doom-unit-lift": "Lift the painter's loop corridor eight rows; every band below it is a fixed offset, so the whole lower half moves as one piece.",
    # ── memory ───────────────────────────────────────────────────────────────
    "mem-rotating-tape": "A pipe is a queue whose capacity is its length, so a folded corridor is memory you have already paid for — and it holds values with no man.",
    "mem-size-n-to-slots": "The tape is the same size on the grid at every capacity, so pick the capacity the problem actually needs and take the speed for free.",
    "mem-per-access-not-per-slot": "Memory is billed per *access*, not per stored word — so count accesses, not instructions.",
    "mem-write-cheap-read-dear": "A write is fire-and-forget and a read blocks. Spend writes freely; hunt reads.",
    "mem-sign-biased-request": "Encode read-vs-write in the *sign* of the address word, so a memory op needs no literal and no scratch slot.",
    "mem-serpentine-tape": "Fold the tape's ring as a serpentine instead of two L-shapes, and it can hold twenty times more without getting wider.",
    "mem-man-memory": "Broadcast the address to every cell at once and let the one that matches answer — so lookup time stops depending on how much is stored.",
    "mem-man-memory-as-store": "That fast memory is three rows tall per word, and the box is squared — so it wins on time and loses on score everywhere but the tiniest sizes.",
    "mem-two-tier-wall-clock": "Putting the hot words in the fast memory halved the ticks and tripled the *simulator's* work, so the judge timed it out.",
    "mem-banked-pipe-tape": "Split the tape into a small hot ring and the big cold one. Same idea as the fast tier, but a tape costs no extra men.",
    "mem-tape-skips": "Make the tape's worker advance two or four words per lap instead of one, peeling the remainder off with bit tests.",
    "mem-relay-throughput": "A ring's speed is set by its turnaround room, not by its worker — so a faster worker behind a small relay buys nothing.",
    "mem-stream-tier": "When access is sequential, throw addressing away entirely: three rings that only rotate, feeding a four-glyph multiply-accumulate.",
    "mem-take-it-out-of-the-store": "Do not make the store faster — take the big array out of it, so every unrelated read stops paying for that array.",
    "mem-packed-cells": "Packing several cells into one word shortens the tape and costs more in read-modify-write than it saves.",
    "mem-storeopt-sweep": "Make swapping the memory backend a one-command operation, then sweep every shipped machine — and find no winner.",
    "mem-store-teleport": "Answer the CPU by teleport instead of by pipe, so the reply stops paying for the distance it travels.",
    "mem-taped-tier": "Chain several small tapes behind one-man gates that pass an address down the line, subtracting as they go.",
    "mem-per-address-traffic": "Profile traffic per *address* rather than per bank, then draw the bank seams around the two hot inner loops.",
    "mem-hot-bank-first": "Put the busiest bank at the front of the gate chain so hot accesses walk past fewer gates.",
    # ── cpu ──────────────────────────────────────────────────────────────────
    "cpu-per-program-synth": "Do not build one CPU for every program. Generate the smallest CPU that runs *this* program.",
    "cpu-trie-decoder": "Decode an opcode by walking a binary tree of its bits — the tree *is* the decoder, and each leaf is where that instruction starts.",
    "cpu-fixed-width-words": "Give every instruction the same width so that only the fetch stage ever touches the program stream.",
    "cpu-return-path": "A quarter of all CPU time was the walk back to fetch, because the collector had been placed on the far side of a band.",
    "cpu-lane-order": "Order the instruction lanes by how often each opcode actually runs, with the machine's width held as a hard constraint.",
    "cpu-16-opcode-cliff": "The decoder's size steps at powers of two, so going from seventeen opcodes to sixteen halves the whole lane band.",
    "cpu-dsp-fold": "Three display opcodes become one, by putting a small routing room downstream that reads the port out of the word.",
    "cpu-mem-pad": "The CPU is padded wider than it needs to be purely so a memory receive picks the right pipe — and that padding is walked twice per instruction.",
    "cpu-trim-dead-lanes": "Build an uneven decode tree so unused leaves cost no rows at all.",
    "cpu-ram-program": "Store the program in memory and fetch it by address, so a jump is an address instead of a discard loop. It costs more than it saves.",
    "cpu-seek-drum": "Keep the program on the drum, but let a jump *ask* the drum to skip to a row — and only do it for the jumps that are long.",
    "cpu-code-banks": "Give each subroutine its own program loop, so calling one is a turn rather than a wait.",
    "cpu-doom-forwarder": "Collapse three chained rooms on the answer path into one.",
    "cpu-llm-slab-walk": "Separate the cost of *reaching* a jump block from the cost of the loop inside it; the approach walk was its own term.",
    # ── rom ──────────────────────────────────────────────────────────────────
    "rom-snake-packer": "Pack fixed text into a serpentine of literals as tightly as the language's own pairing rules allow.",
    "rom-base-n": "When a problem is scored on size alone, ticks are free — so compress as hard as you like and pay for it in decoding.",
    "rom-looping": "Instead of *storing* the program in a ring, have a man walk a closed loop and *regenerate* it forever.",
    "rom-packed-tokens": "Price each program word by its own digits instead of padding every word to the widest one.",
    "rom-recirculation-cost": "With no program counter, a backward jump has to let the rest of the program go past — and that is a third to a half of every CPU machine.",
    "rom-discard-unroll": "Retire two skipped words per lap of the discard loop instead of one. Deeper is blocked by needing the count to divide exactly.",
    "rom-buffer-corridor": "Make the corridor from the ROM to the CPU long enough to hold the program, so a jump drains a queue instead of waiting for a man.",
    "rom-drain": "A dedicated fast discard unit — which is worthless, because the thing it was waiting on was the ROM man, not the loop.",
    "rom-repeater": "Replace the ROM man with a ring that re-emits the program, and read two banks in one room to reach a word per tick.",
    "rom-opcode-slots": "Renumber the opcodes so the common ones get single-digit codes — and pick numbers that leave every lane's row untouched.",
    "rom-density-is-an-area-knob": "Deliberately inflating the program's encoding proves how little it costs: on this machine the ROM is space, not time.",
    # ── dataflow ─────────────────────────────────────────────────────────────
    "df-the-ratio": "A walked glyph costs one tick and an issued instruction costs forty-six. Compile for coverage; hand-build for rank.",
    "df-tcp-ring": "The reorder buffer was a shift register implemented as an addressed tape. Make it a ring and the addressing disappears.",
    "df-brackets-one-register": "A pipe cannot be a stack, so put the whole stack in one integer — multiply to push, divide to pop, remainder is the popped value.",
    "df-sudoku-transpose": "Store one word per value instead of one per unit, and the duplicate test becomes an addition with a carry.",
    "df-matmul-dense": "Fit the machine's bands to what they carry rather than assuming a shape, and make the hottest path a straight fall-through.",
    "df-matmul-stall": "A machine that built, loaded and bound correctly still stalled — because the engine had silently minted two pipes nobody drew.",
    "df-snake-coprocessor": "Move the snake's body out of memory into its own ring, then keep re-searching the block order as the graph changes.",
    "df-gradebook-cfg": "Fuse loops out of the control-flow graph, then re-run the layout search on the fused graph.",
    "df-memory-one-pass": "Fold a fast-but-huge one-pass worker into a compact box, then delete the ignition and collector overhead a row at a time.",
    "df-pathfinder-bitparallel": "Hold the whole board in four integers and do a breadth-first search with bitwise operations, labelling distance modulo three.",
    "df-pathfinder-ten-steps": "Ten small compactions in a row, every one of them sent to the judge before the next was started.",
    "df-sort-reverse-rings": "Feed the ring from its relay rather than through its own port, and carry two values per lap instead of one.",
    "df-lllm-hold-the-word": "Keep the word you just read in a register and leave a hole in the ring, so reading the same word again costs nothing.",
    "df-lllm-cpu-or-ring": "Price a CPU against a synthetic program of the right size before writing the interpreter — and find it is a wash.",
    "df-subset-sum-design": "Thread the search's stack through the ring's own cells, so backtracking reads the link for free on the way past.",
    # ── io ───────────────────────────────────────────────────────────────────
    "io-panel-framebuffer": "Committing a frame does not have to clear it — so paint only the pixels that changed.",
    "io-port-arrival-order": "The display's three ports must be ordered by when values *arrive*, not by how long their pipes are.",
    "io-plotter-block": "Carry a packed position-and-error in one word, split on the major axis, and then replace the CPU with a dedicated line drawer.",
    "io-coprocessor-rules": "A coprocessor must never reply, because its pipe would compete for every receive in the CPU. Give it authority to act instead.",
    "io-doom-unit": "Move painting into a unit driven by one command word per shape, so the CPU stops sending pixels one at a time.",
    "io-doom-hires": "A bigger viewport means more panels: four displays tiled behind a router that picks one by address.",
    # ── parallel ─────────────────────────────────────────────────────────────
    "y-audit": "Splitting a man is only useful when both children end in *different* side effects; identical work on shared data just queues.",
    "y-spawn-ladder": "A loop with two splits and two decrements hands out N men holding a countdown, one every four ticks, for free.",
    "y-parity-wall": "Men spawned from one loop always land in the same parity class, so a delay ring can never be smaller than twice the values it carries.",
    # ── tooling ──────────────────────────────────────────────────────────────
    "tool-fast-littleman": "Write your own tick loop so a full sweep takes a minute instead of an afternoon.",
    "tool-sparse-scheduling": "Only step the men that can actually move — same ticks, hundreds of times faster.",
    "tool-profiler": "Sample where every man is standing, but split by man: one blocked on input is idle, not a bottleneck.",
    "tool-submit-archive": "Name every graded file after its verified score, so a worse run can never overwrite a better one.",
    "tool-judge-factor": "The judge's average runs about 10% heavier than the local one, because it also runs the private cases.",
    "tool-route-check": "A send picks the nearest pipe, not the nearest *ready* one — so re-assert every binding after every move.",
    "tool-debug-sidecars": "A generated grid has no comments, so make the generator emit its own annotated overlay in the same run.",
    "tool-tests-assert-correctness": "Never pin a score or a tick count in a test, or an improvement becomes a failure.",
    "tool-remeasure-constants": "Every constant you inherited is a guess until you measure it on the machine you are actually running.",
}

# Real littleman. `before`/`after` are grid text; `note` is one line of pointing.
GLYPHS: dict[str, dict] = {
    "fp-triangle-8x8": {
        "before": """+-+ +-+
|I| |O|
+-+ +-+
 v   ^
 v   ^
+-------+
|@rM1+*v|
|Hs/W2M<|
+-------+""",
        "after": """ +-----+
 |@rM*v|
 |v2M+<|
>|>W/sH|
^+-----+
+-++-+v
|I||O|<
+-++-+""",
        "note": "The room shifts one column right; the freed left edge carries the input pipe up the outside, and the two I/O rooms abut with no gap column.",
    },
    "fp-squared-objective": {
        "after": """+-+  +------------+  +-+
|I|>>|@rM1+*M2W/sH|>>|O|
+-+  +------------+  +-+""",
        "note": "Eleven glyphs laid out flat: 24x3, and `max(w,h)^2` bills the 24 while 21 rows of the box do nothing. Folding the same glyphs onto two rows is worth 7x and changes no logic.",
    },
    "mem-rotating-tape": {
        "after": """+----------------------+
| @`100`b        v     |
|v                    <|
|>rXrbM`100`-M  v      |
|  >rbM`100`-NM v      |
|          v    <      |
|          >>dv  >>d  ^|
|           mr    m0   |
|            s     s   |
+----------------------+""",
        "note": "`b` loads the rotation count, `d`/`m` peel it off one lap at a time, and `r`/`s` carry each value round. The stored values live in the pipe, so none of them is a man.",
    },
    "rom-looping": {
        "before": """+------------------+
|@7s8s9s0sH........|      ROM: walked ONCE
+------------------+
  v
+----+   +---------+
|@>Rv|>->|@v.......|      LOOP: holds every word,
|.^s<|<-<|.>rsXH...|      so the pipe must be >= P
+----+   |.^..>..sv|      or the machine deadlocks
         +---------+""",
        "after": """+------+  +-+
|>7s8sv|>>|O|      one room, no ring, no write-back:
|^..s9<|  +-+      the man walks a CLOSED loop and
|@....^|           re-emits 7 8 9 7 8 9 ... forever
+------+""",
        "note": "Fetch drops from `>rsbrsx` to `>rbr` for every program, and the ring's minimum-length constraint — which silently deadlocked any packing pass — disappears entirely.",
    },
    "cpu-code-banks": {
        "after": """+----+   +--------------------+   +----+
|>1sv|>->|>rsrs..........rsrsv|<-<|>7sv|
|^s2<|   |^..................<|   |^s8<|
|@..^|   |@..................^|   |@..^|
+----+   +--------------------+   +----+
  bank A          consumer          bank B""",
        "note": "Which bank a fetch reads is decided purely by which `r` cell it stands on — no opcode, no multiplexer, no bank register. Emits `2 1 8 7` repeating.",
    },
    "cpu-trie-decoder": {
        "after": """+-----------+
|....>4sH...|
|...>x......|
|...]>6sH...|
|@0bx.......|      `b` loads the opcode into the backpack
|...]>5sH...|      `x` turns on its low bit, `]` shifts it right
|...>x......|
|....>7sH...|
+-----------+""",
        "note": "Depth 2 dispatches four opcodes in ~6 ticks. The leaves come out bit-reversed, which is why the opcode *number* is a free layout variable.",
    },
    "rom-repeater": {
        "after": """+-+  +--------------------------+
|I|>>| @rsrsrsrsrsrsrsrsrsrsrsrsH|
+-+  +--------------------------+
        2.00 ticks a word, and there is no cheaper cycle

  r r    <- two `r` cells in ONE room bind two DIFFERENT rings,
  ^ ^       purely by which is nearer. One word from each per
  |  \\__    two ticks = one word a tick, with no merger man.
   \\____""",
        "note": "A merger man is exactly what would have put a two-tick cycle back into the path. Never built: seeding an empty ring is the hard part.",
    },
    "mem-relay-throughput": {
        "before": """+--------+   +----+
|@1s2s3sv|>->|@>rv|      the relay is a 6-cell walking
|.....vr<|   |.^s<|      cycle carrying ONE word a lap,
|.....>s^|   +----+      so it caps the ring at 6.0
+--------+                whatever the worker does""",
        "after": """+--------+   +------+
|@1s2s3sv|>->|@>rsrv|     a longer perimeter carries more
|.....vr<|   |.^srs<|     words a lap -- one man alternating
|.....>s^|   +------+     r/s round it is still a FIFO
+--------+""",
        "note": "Binding stays unambiguous however many pipe glyphs the relay holds, because a turnaround room has exactly one incoming and one outgoing pipe.",
    },
    "y-spawn-ladder": {
        "after": """d m > Y
Y < m d

  (0,0) d  corner: backpack > 0 -> turn (stay), = 0 -> straight (exit)
  (1,0) m  backpack -= 1
  (2,0) >  merge point: a nop for the loop, re-aims anyone entering
  (3,0) Y  keeper goes south, worker is born north facing north""",
        "note": "Enter at the `>` with the count in the backpack. The men come out holding n-1, n-2, ... 0 — the countdown is a free side effect of the loop counter.",
    },
    "y-parity-wall": {
        "after": """r M r  ...  s W s

  `r` loads v0, `M` parks it in the off hand, `r` loads v1;
  later `s` emits v1, `W` swaps, `s` emits v0.
  One man carries TWO values and hands them back reversed.""",
        "note": "This is the only thing that beats the parity wall without a second spawn loop: half as many men means half the ring.",
    },
    "df-brackets-one-register": {
        "after": """push:   ++++M      with the mask in B and the tag in A,
                   four adds give mask*4, `M` moves it back

pop:    `4` /      the quotient is the shorter mask...
                   ...and `/` leaves the REMAINDER in B,
                   which is exactly the popped tag""",
        "note": "Multiplying by a small constant is repeated `+` because the off hand survives it — so a 32-deep typed stack needs no third register and no pipe traffic at all.",
    },
    "df-tcp-ring": {
        "before": """|>rb}xs ||>rXv @rb0Mdv|      ... one arm of a 112x78
|    ]N ||  >>1+Mmdvv |      generated CPU: fetch, a
|^  sx^ ||^       < < |      decode trie, sixteen lanes,
|@rs5M ^||^s0      < <|      an adapter and a 33-column tape""",
        "after": """+------+
|>@rsrv|      the whole reorder buffer: sixteen slots
|     s|      indexed by PHASE, which is what a ring
|^srsr<|      does natively. No addressing at all.
+------+""",
        "note": "An arrival always lands within 15 of the head, and values are >= 1, so 0 is a free empty-slot sentinel. 1.138e9 -> 7.85e6.",
    },
    "cpu-seek-drum": {
        "after": """... row body ... q d ... row body ...
                 ^ ^
                 | +-- backpack > 0 (a request is waiting): turn into
                 |     the cascade, down to the collector, west to the
                 |     riser, up to the station
                 +---- `q` = how many values are in the incoming pipe;
                       with none, the man walks straight on (2 cells,
                       the entire sequential tax)

station:  `128` M  r  /  ... x        splits the request into a row
                                      and an offset, emits -1, then
                                      turns on the row's parity""",
        "note": "CPU side, the flush is two glyphs: `r` then `X`. Program words are non-negative, so the sentinel's sign is the whole test — and the accumulator is never touched.",
    },
    "rom-discard-unroll": {
        "before": """  > r s v
  ^ . m <      a 6-cell lap retiring ONE word: 6 ticks a word""",
        "after": """  > r s r s v
  ^ . . . m <   a 2x4 lap retiring TWO: 4 ticks a word""",
        "note": "Deeper laps are arithmetic, not effort: `2x(k+2)` cells retire `k` words. k=2 works because every skip count is guaranteed even; k=4 would need every count divisible by four.",
    },
    "io-panel-framebuffer": {
        "after": """  s -> ADDR      set the cursor
  s -> DATA      paint, cursor advances by one
  s -> SWAP  1   COMMIT AND KEEP -- `next` and the cursor both survive

  so the next frame starts from the last one, and a round paints
  only what moved:  two pixels, not two hundred and fifty-six.""",
        "note": "`SWAP 0` clears the buffer and forces a full repaint; `SWAP 1` does not. Every commit emits exactly one frame, so a silent round is impossible by construction.",
    },
    "mem-sign-biased-request": {
        "before": """0 s   r   s   r      LD: literal `0`, send op, read the operand
                     off the ring, send it, read the answer --
                     and the literal has already destroyed the
                     operand sitting in the hand""",
        "after": """  s   r               the address IS the request:
                        +a  read
                        -a  write
                     no literal, no scratch slot, no lane
                     touching the program stream""",
        "note": "A small adapter room expands the signed word back into the tape's real `op addr [value]` protocol, so the verified tape never changes. Address 0 is ambiguous, so hardware addresses start at 1.",
    },
    "df-lllm-hold-the-word": {
        "before": """r s   r s   r s  ...      every read puts the word back at the
                          tail, so the ring rotates by one -- and
                          asking for the SAME word again costs a
                          whole lap""",
        "after": """r ...                     the word stays in the register file
                          and the ring keeps a HOLE where it came
                          from; a hit touches the store not at all,
                          a miss pushes the held word into the hole""",
        "note": "53% of interpreted ticks want the word they just had. 184 word-moves a tick unpacked, 22.4 packed-and-relative, 6.6 once the word is held.",
    },
    "cpu-return-path": {
        "before": """[ lane band       ]
[ structures band ]
[ collector       ]   <- the riser is 38 cells and every
                         instruction walks it""",
        "after": """[ lane band       ]
[ collector       ]   <- 16 cells; the jump blocks now RISE
[ structures band ]      into it instead of dropping past it""",
        "note": "The body of a riser must be `.` and never `^` — an arrow there turns any man crossing that row north, and `.` is the only glyph two men crossing in different directions both survive.",
    },
    "rom-buffer-corridor": {
        "after": """ROM +-->-->-->-->-->-->-->-->--+
                                v      a pipe IS a queue whose
    +--<--<--<--<--<--<--<--<---+      capacity is its length, so
    v                                  a long corridor holds the
   CPU fetch                           program already in flight""",
        "note": "It measures flat, and the model says why: the discard costs `max(CPU loop, ROM emission)`, and the loop is the slower of the two. A repeater at 2 ticks a word would be equally flat.",
    },
    "df-sudoku-transpose": {
        "after": """r + s        with a value's bit added to its unit mask,
             the carry out of that bit IS the duplicate test --
             `mask + 2^v` carries exactly when bit v was set""",
        "note": "Transposing 27 unit masks into 9 per-value words is what makes the addressing sequential enough for a ring to serve it.",
    },
    "mem-taped-tier": {
        "after": """U b  r M `M+1` W - X   ... d/a

  `U` takes the operation, `b` parks it in the backpack,
  the subtract-and-turn splits mine from downstream on the
  address, and `d`/`a` split read from write on the backpack.
  Downstream arms REBASE -- they send `addr - M` on --
  so every bank decodes plain local addresses.""",
        "note": "One man per gate, four arms. The last bank in the chain needs no gate at all.",
    },
    "tool-route-check": {
        "after": """  put every anchor on ONE wall:

      distance from any interior cell  =  |x - col| + y + 2
                                                      ^^^^^ common

  ...so nearest-pipe collapses to nearest anchor COLUMN, and if
  adjacent anchor columns sum to an ODD number then |x-c1| = |x-c2|
  has no integer solution -- the silent tie cannot occur at all.""",
        "note": "The alternative is padding the machine wider until the binding happens to work, then checking afterwards — which is what the generated CPU does.",
    },
    "df-subset-sum-design": {
        "after": """cell j  =  (v_j * L + link_j) * 2 + mark_j

  when position p is taken, write the previous deepest-taken
  index into p's OWN cell.  Backtracking rotates from p to q+1,
  and the LAST cell that rotation reads is q's -- so the link
  and the value come back for free, on a pass you were making
  anyway.  The stack costs zero extra ring traffic.""",
        "note": "The sentinel cell holds `-Total`, which makes `suf <- suf - v` correct even at the wrap, so no suffix array is stored anywhere.",
    },
    "mem-man-memory": {
        "after": """  S  ->  every cell at once      the address is BROADCAST
  ...                            and the one cell holding it
  R  <-  the matching cell       replies through a teleport

  nothing walks, so the answer time depends only on how DEEP
  the columns are -- never on how many there are.""",
        "note": "Width is free and depth is what you pay for. The catch is that every stored word is a live man, which is what the wall-clock verdict later charged for.",
    },
    "cpu-16-opcode-cliff": {
        "after": """  17..19 opcodes  ->  tree depth 5  ->  63 rows of lanes
     <= 16        ->  tree depth 4  ->  31 rows

  and there is no partial credit: 19, 18 and 17 give the
  IDENTICAL band, because the depth steps at powers of two.""",
        "note": "Any route to the fold pays its whole cost before a single row appears, which is why it has to be priced before it is started.",
    },
}

# ── the second wave of figures, all sourced from real grids ─────────────────

GLYPHS.update({
    "mem-tape-skips": {
        "before": """|          v    <               s  |
|          >        >   v      ^<  |
|                   drsmv          |
|                   ^msrd          |
+----------------------------------+
   one `rs` pair on each side of the ring:
   TWO words a lap, ~5 ticks a word""",
        "after": """|          v    <        >  v >    v          ^  <|
|          >             x  >]x    >]>         v  |
|                        >rs^ >rsrs^ drsrsrsrsmv  |
|                                    ^msrsrsrsrd  |
+-------------------------------------------------+
   `x` peels 1 word, `]` shifts, `x` peels 2, `]` shifts,
   then FOUR `rs` pairs a side: (c&1) + 2*((c>>1)&1) + 4*(c>>2) = c""",
        "note": "Exact by construction, and it dodges the register problem batch 3 has: `/` would put the remainder in the off hand, which is the only register that survives a tape pass and is already holding the signed operation tag.",
    },
    "fp-subset-sum-turnarounds": {
        "before": """|             >rsM8W{Mrsrs+b1M0        v       |
|             v                        <       |
|                                              |
|             >   >  d                 v       |
|                                              |
|                 ^]+x]v                       |
|                 ^    <                       |
|             v                        <       |""",
        "after": """|             >rsM8W{Mrsrs+b1M0v               |
|                        >    v                |
|                        ^]x+]v                |
|                                              |
|             v            d  <<               |""",
        "note": "Same glyphs, wound the other way: the return legs stop reaching for the far wall, so the block collapses from eight rows to five. Rows were the charged side, so every one of these was worth score.",
    },
    "fp-lllm-fallthrough-east": {
        "before": """|  >                  >1Ns          rrrrrM0ssss1sWsv    |
|                     v                            <    |
|    >                >                     rM2*sv      |
                      ^^^ the man walks WEST to the next
                          block's entry, and that leg owns
                          the whole row""",
        "after": """|  >                  >1Ns          rrrrrM0ssss1sWsv    |
|    >                >             >        rM2*sv     |
                                    ^ he drops one row at
                                      his OWN column and keeps
                                      walking east -- no row claimed""",
        "note": "12 of the 40 unconditional edges qualify. A row that is never allocated is also a row the returning man never walks, so this took 2.7% of ticks with it.",
    },
    "fp-lllm-own-band": {
        "before": """|                          @0                    ss     |
                          ^^ every block starts at the far
                             west, then walks dead columns
                             out to the band it actually uses""",
        "after": """|                          @                     0ss    |
                                                ^^ it starts at
                                                   its own band --
                                                   legal for any
                                                   block that never
                                                   needs one further west""",
        "note": "52 of 63 blocks qualify. Moving each block's *entry* east as well fails: the channel bank is west, so an arriving man turns east at his channel and would have to run across the lanes.",
    },
    "df-sort-reverse-rings": {
        "before": """|@v>>+s v<| >v
| 1^X-mrd |  |
|v< >+Ws ^|<<|
|v1sW   < | ||
|>Mr-X rM^| ||
|    v   M| ||
|    >bsr^| ||""",
        "after": """|>>+s dv  | >v
|^X-mr<  <|  |
| >+Wsav  |<<|
|v1sW  <  | ||
|>Mr-Xv   | ||
|^1sr <  M| ||
|^@1<>bsr^| ||""",
        "note": "The loop test moved to fire once per *lane* instead of once per value, and the spawn walked down into the corner it was already turning at. Same seven rows, 11.5% fewer ticks, zero rows spent.",
    },
    "df-memory-one-pass": {
        "before": """|@1v     |  |@v     |
|   >r~Xv|  |  >rbrv|
|v+Y^rr<r|  |vY^  Md|
|M  ^srs<|  |  ^sMW<|
   ^^ a decoder tile AND a separate collector tile,
      repeated down every band""",
        "after": """|@1v     |
|   >r~Xv|
|v+Y^rr<r|
|M  ^srs<|
   ^^ the collector room turned out never to be needed:
      the answer leaves the decoder directly""",
        "note": "Then the igniter's increment folded to a single glyph. Four separate narrowings took the same machine from 34.5M to 19,973,628.",
    },
    "rom-snake-packer": {
        "after": """|@`58899754071776433`s`59043114540103273`s`18210167481643241`s v|
|v s`87361058549445295`s`20161637875165586`s`92100327750986875`s<|
|>`55876124605544307`s`57301593206110703`s`57319336668024940`s  v|
|v s`23633240978350646`s`90051853094967216`s`57546407147788595`s<|""",
        "note": "One man walks the whole serpentine. Two rules shape it and both are load errors if broken: digits reverse on right-to-left rows, and backticks pair down *columns* as well as along rows, so stacked literals must never align their backtick columns.",
    },
    "rom-base-n": {
        "before": """`72`s`101`s`108`s`108`s`111`s      one word per CHARACTER""",
        "after": """`58899754071776433`s                one word per EIGHT of them,
                                    unpacked by a decoder""",
        "note": "On a problem scored by area alone, decoding is free — so the only question is how many characters fit in a signed 64-bit word. Base-1000 first, then base-128 at 8 bytes a word.",
    },
    "rom-packed-tokens": {
        "before": """`0007`s`0000`s`0011`s`0042`s      every word padded to the
                                 WIDEST word in the image""",
        "after": """7s0s`11`s`42`s                   every word priced on its own
                                 digits: 2 cells, not 6""",
        "note": "The old width came from one call taking the maximum over the whole image. It is also why `SUBI n` cannot be rewritten as `ADDI (2^64 - n)`: one 20-digit literal would widen every word in the drum.",
    },
    "rom-opcode-slots": {
        "before": """`12`s`353`s   `17`s`0`s   `12`s`517`s
 ^^^^ a two-digit opcode is 5 cells where
      a one-digit one is 2 -- and 1,542 of
      2,152 opcode words were two-digit""",
        "after": """4s`353`s      2s0s       0s`517`s
^^ the SAME lanes, in the same rows: a lane's row is its
   slot's RANK among the used slots, so any rank-preserving
   relabelling moves nothing but the number""",
        "note": "The decoder uses 22 of its 32 slots and exactly ten of the spare ones bit-reverse below ten, so the hot opcodes can be given them. The trie walk gets *shorter* too, because the spread leaves fewer single-child chains to unwind.",
    },
    "df-the-ratio": {
        "before": """|>rb}xs ||>rXv @rb0Mdv|      one lane of a generated CPU:
|    ]N ||  >>1+Mmdvv |      fetch, decode, execute, walk
|^  sx^ ||^       < < |      home -- 46 ticks per instruction
|@rs5M ^||^s0      < <|""",
        "after": """+-+  +------------+  +-+
|I|>>|@rM1+*M2W/sH|>>|O|     the whole problem, hand-drawn:
+-+  +------------+  +-+     one tick per glyph the man
                             walks over""",
        "note": "Eleven glyphs against a machine of ten thousand cells. The generated CPU scores 471,744 on this problem and the hand-drawn one scores 960.",
    },
    "df-matmul-dense": {
        "after": """|     vM*7M3b*srMsrs          <^Yv^Yv                     |
|     >1           {s6M7*M1{svr@^>>^                      |
|                           v<^<<   <>v                   |
|                           >>rmd    Y                    |
|                            ^  Y     >              sH   |""",
        "note": "The hot path is a straight fall-through rather than a lane that has to be re-entered, and the bands are fitted to what they carry instead of being assumed. Both changes are glyph placement, not new machinery.",
    },
    "mem-stream-tier": {
        "after": """+----+   +----+   +----+      three rotate-only rings...
|>@rv|   |>@rv|   |>@rv|      each one is a room of four
|^ s<|   |^ s<|   |^ s<|      glyphs and a pipe pair
+----+   +----+   +----+
   |        |        |
   v        v        v
|          v3b*srMsrs           <     |    ...into a fused
|          >M   7*M1{s6M7*M1{sv       |    multiply-accumulate""",
        "note": "No addressing anywhere: the loop order only ever asks for the *next* element, and rotating a ring is what a pipe does natively. 37 ticks per multiply-accumulate becomes 7.3.",
    },
    "df-pathfinder-bitparallel": {
        "after": """|@         >`256`                               s0s4v       |
|         v                        s                <       |
|     >    >M1W-        s`64`b0Mv                           |

|>@rsrsrsrsrsrsrv||>@rsrsrsrv||>@rsrsrsrv|
|^.srsrsrsrsrsrs<||^.srsrsrs<||^.srsrsrs<|
   ^^ three label planes, held as whole 64-bit words in rings.
      Pushing them back in a DIFFERENT ORDER is the relabelling.""",
        "note": "A 16x16 board is four integers, not 256 cells, so one search level is about sixteen word operations. Distance is kept modulo three, which works because the grid is bipartite — and re-ordering the rings is how a level advances, for free.",
    },
    "io-plotter-block": {
        "after": """>-----v
|    +================================+
|    :                                :
|>-->:                                :
||   :        the panel                :
||   :                                :
   ^^ no ROM, no decode trie, no lane band, no adapter,
      no tape -- a dedicated line drawer beside the display""",
        "note": "Before the CPU could be deleted it had to be made legal: the compiled machine was 6% over the step cap on the worst legal figure, and three packing changes took it from about twenty memory accesses per pixel down to four.",
    },
    "io-coprocessor-rules": {
        "after": """+----------------+  the unit's pipes all point INTO it.
|>@rsrsrsrsrsrv|  It never sends back, because an incoming
|^.srsrsrsrsrs<|  pipe would compete with every `r` in the
+----------------+  CPU -- including the jump block's own.
        ^
        | commands only""",
        "note": "So the unit is given authority instead of a reply: one command either moves the snake or ends the game. A replying unit could not be placed at all on a machine that has jumps — 4,800 layout combinations were tried and every one failed on that binding.",
    },
    "cpu-fixed-width-words": {
        "before": """lane:  ... r ... M      an operand-taking lane reads the
       ^^^^^                program stream from INSIDE the lane
       and a lane four rows from the input pipe finds THAT
       pipe nearer -- so it silently reads program input""",
        "after": """fetch: > r b r         every instruction is two words, so only
       ^^^^^^^         the fetch stage ever touches the stream
lane:  ... M ...       and each lane needs only its own pipe""",
        "note": "It costs program words and buys a CPU that works. The generator has to pad every zero-operand instruction *and* rescale every skip count, because the assembler resolved them in variable-width positions.",
    },
    "cpu-dsp-fold": {
        "after": """U  M `1` W -  X       one room, three arms:
                        A < 0  -> ADDR
                        A = 0  -> DATA
                        A > 0  -> SWAP
   `M `1` W -` MAKES the sign, because a program literal
   can never be negative and so `X` would have nothing to test""",
        "note": "The middle arm is reached by zero, so all three exits carry traffic by construction. The relay must be a closed circuit — the first version ended its arms on `H`, built at exactly the right size, bound every pipe, and drew nothing at all.",
    },
    "cpu-ram-program": {
        "before": """fetch:  > r b r                      read the next two words
jump:   ... discard n words ...      and let the rest go past""",
        "after": """fetch:  > 0 > s   r b r              ask for an address, then read
jump:   send target, then  r X       flush the corridor until the
                           ^^^       sentinel's sign shows up""",
        "note": "The flush is two glyphs and never touches the accumulator, because program words are non-negative and the sentinel is -1. That protocol was the only part worth keeping: it went on to become the seek drum.",
    },
    "df-matmul-stall": {
        "before": """+-------+
|      |
+-------+>--v      the pipe's last leg runs ALONG the room's
    >-------<      top-wall row, one cell west of the corner --
                   and the engine reads those two cells as a
                   SECOND pipe, which the room's `r` then binds""",
        "after": """+-------+
|      |
+-------+
     >------v      the leg turns before it reaches the wall row.
                   Count the pipes the engine finds against the
                   number you drew: one line catches the family.""",
        "note": "The machine loaded, bound and ran — it simply read the wrong data and stopped. Four other causes were ruled out first.",
    },
    "fp-dead-lines": {
        "before": """|@..s....v |
|          |     <- a row with nothing on it
|.^....s<  |""",
        "after": """|@.s....v |
|.^....s< |     <- and a column too""",
        "note": "The catch is that deleting a line *moves rooms*, and a send picks the nearest pipe by distance — so a grid that still loads may quietly be reading a different pipe. Every cut is followed by a binding check and a full case run. Ten blank lines across five machines were worth 415 million points.",
    },
    "mem-size-n-to-slots": {
        "before": """| @`200`b                     v    |
    ^^^^^ the tape rotates 200 slots on every access,
          whatever the program actually stores""",
        "after": """| @`48`b                      v    |
    ^^^^ sized to the problem's real slot count -- and the
         block is the same size on the grid either way""",
        "note": "There is no trade-off to weigh: capacity costs rows *inside* a fixed block, so shrinking it is free on area and linear on time. The one trap is sizing from the public cases instead of the constraints — overrunning by one slot walks the tape's man into a wall.",
    },
    "mem-per-access-not-per-slot": {
        "after": """LD addr:   0 s   r   s   r   M
                 ^^^^^^^^^^^^^  the whole round trip, and the
                                man BLOCKS on that last `r`

   ~316 ticks of it is fixed cost that does not amortise,
   and only ~1.9 ticks per stored slot -- so an access is
   about seven instructions no matter how small the tape is.""",
        "note": "The rule that falls out: count accesses, not instructions. That is what took the plotter from 6% over the step cap to 61% under it.",
    },
    "mem-write-cheap-read-dear": {
        "before": """ST n:   1 s   r   s   W  s  W      ~19 ticks -- fire and forget,
                                   nothing waits for it""",
        "after": """LD n:   0 s   r   s   r   M        ~523 ticks -- the man stands
                          ^        on this `r` until the answer
                                   comes back""",
        "note": "So prefer any encoding that reads once and writes twice over one that reads twice — and a read-modify-write instruction is worth its own lane, because `DECM n` is one read where load-subtract-store is two.",
    },
    "mem-store-teleport": {
        "before": """store --->--->--->--->--->--->---> CPU
        ^^ 59 cells of pipe, and every cell is a tick,
           paid on every one of ~14,000 reads a frame""",
        "after": """store  S                        R  CPU
       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
       a teleport pair: an `R` receive has no distance term
       at all, so the route costs 6 cells instead of 59""",
        "note": "Audited afterwards to check neither room was vestigial: one spans 691 columns and the other replaces about seven pipe cells on every read — roughly 140,000 ticks a frame between them.",
    },
    "mem-two-tier-wall-clock": {
        "before": """  a tape slot:   a value sitting in a pipe cell
                  ->  ZERO little men, at any capacity""",
        "after": """  a fast-memory slot:
  |@1v     |
  |   >r~Xv|      -> ONE little man, forever, whether or
  |v+Y^rr<r|         not anyone ever reads him
  |M  ^srs<|

  52 slots plus their decoders and routers = 109 extra men,
  every one of them stepped on every tick of the whole run.""",
        "note": "Ticks fell by 2.36x and the simulator's work rose by 9.7x, so the judge timed it out on 24 of 28 cases. Score counts ticks; the grader spends wall clock.",
    },
    "fp-rom-fold-square": {
        "before": """|@`123`s`456`s`789`s`101`s`112`s`131`s`415`s`161`s v|
|v s`718`s`192`s`021`s`222`s`324`s`252`s`627`s`282`s<|
   ^^ folded wide: the program sets the WIDTH, and the
      rows underneath it are already taller""",
        "after": """|@`123`s`456`s`789`s v|
|v s`101`s`112`s`131`<|
|>`415`s`161`s`718`s v|
|v s`192`s`021`s`222`<|
   ^^ one row deeper, six columns narrower -- repeat until
      the machine stops being wider than it is tall""",
        "note": "The crossing point is the minimum, not the narrowest fold. Any change to the CPU, the store or the program invalidates it, and nothing else in the test suite notices when it silently drifts.",
    },
})

# ── which findings matter most, and which have nothing to draw ───────────────

IMPORTANT: set[str] = {
    # the laws
    "fp-squared-objective", "df-the-ratio", "mem-write-cheap-read-dear",
    "mem-per-access-not-per-slot", "mem-relay-throughput", "rom-recirculation-cost",
    "tool-route-check", "tool-remeasure-constants", "y-parity-wall",
    # the big score moves
    "fp-triangle-8x8", "fp-rom-fold-square", "fp-unstack-memory",
    "fp-subset-sum-two-rooms", "fp-llm-rom-refold", "fp-deadman-square",
    "mem-rotating-tape", "mem-banked-pipe-tape", "mem-stream-tier",
    "mem-take-it-out-of-the-store", "mem-man-memory", "mem-sign-biased-request",
    "cpu-per-program-synth", "cpu-trie-decoder", "cpu-16-opcode-cliff",
    "cpu-return-path", "cpu-seek-drum", "cpu-fixed-width-words",
    "rom-looping", "rom-discard-unroll", "rom-base-n",
    "df-tcp-ring", "df-brackets-one-register", "df-sudoku-transpose",
    "df-pathfinder-bitparallel", "df-lllm-hold-the-word", "df-subset-sum-design",
    "df-snake-coprocessor", "df-memory-one-pass",
    "io-panel-framebuffer", "io-plotter-block", "io-coprocessor-rules",
    "mem-per-address-traffic", "mem-taped-tier",
    # the expensive negatives, which are findings in their own right
    "mem-two-tier-wall-clock", "mem-man-memory-as-store", "rom-drain",
    "cpu-ram-program", "rom-buffer-corridor", "df-lllm-cpu-or-ring",
    # the instruments the rest depended on
    "tool-fast-littleman", "tool-profiler", "tool-judge-factor",
    "tool-tests-assert-correctness",
}

# Entries whose change genuinely has no glyph form. The value is *why* — a
# finding about geometry, ordering, measurement or process is not a grid edit,
# and drawing one anyway would be an invention.
NOT_REPRESENTABLE: dict[str, str] = {
    "fp-unstack-memory": "moving a whole block to a different side of the machine — the block's own glyphs are unchanged",
    "fp-ast-moves": "a search over moves, not one move",
    "fp-output-room-off-axis": "the room is identical; only which dimension it stands in changed",
    "fp-lllm-lane-rows": "rows that were reserved and empty; there is no glyph to remove",
    "fp-lllm-wraps": "three rejected attempts, none of which reached a grid worth showing",
    "fp-subset-sum-two-rooms": "one room becomes two — a wall moves, the glyphs inside do not",
    "fp-llm-rom-refold": "the same program re-wrapped at a different width",
    "fp-lshaped-rom": "never built",
    "fp-deadman-square": "a store block reshaped from one column to eight; the cells inside are unchanged",
    "fp-deadman-m7c": "block placement and fold arithmetic",
    "fp-doom-unit-lift": "a rigid band of the machine translated eight rows; nothing inside it changed",
    "mem-serpentine-tape": "the tape's pipe is folded differently; its worker is untouched",
    "mem-man-memory-as-store": "a verdict about block height against a squared score",
    "mem-banked-pipe-tape": "two tapes where there was one — the tape itself is unchanged",
    "mem-take-it-out-of-the-store": "the win is what is *no longer* in the store",
    "mem-packed-cells": "a rejected encoding; the cost is in instruction count, not layout",
    "mem-storeopt-sweep": "a sweep that kept nothing",
    "mem-per-address-traffic": "bank sizes and their order — numbers, not glyphs",
    "mem-hot-bank-first": "the order of blocks in a chain",
    "cpu-per-program-synth": "a decision about what to generate, realised as every other entry here",
    "cpu-lane-order": "which row each lane occupies; every lane's own glyphs are unchanged",
    "cpu-mem-pad": "columns of padding, which are blank by definition",
    "cpu-trim-dead-lanes": "rows that were reserved for unused decoder leaves",
    "cpu-doom-forwarder": "three rooms become one; the walk inside them is the same",
    "cpu-llm-slab-walk": "a re-attribution of measured cost, not a change",
    "rom-recirculation-cost": "a measurement of what a taken branch already cost",
    "rom-drain": "a block that was built, measured and left switched off",
    "rom-density-is-an-area-knob": "a control experiment — deliberately adding a blank cell per token",
    "df-snake-coprocessor": "ten steps of block-order search over a control-flow graph",
    "df-gradebook-cfg": "loops fused out of a control-flow graph before any grid is drawn",
    "df-pathfinder-ten-steps": "ten separate compactions, each a few cells",
    "df-lllm-cpu-or-ring": "a price comparison against a machine that was never built",
    "io-port-arrival-order": "a timing rule about when values land, not about what is drawn",
    "io-doom-unit": "an arm added to a command decoder; the win is in what stops being sent",
    "io-doom-hires": "four panels where there was one",
    "y-audit": "sixteen verdicts, fifteen of them negative",
    "tool-fast-littleman": "a verifier, not a machine",
    "tool-sparse-scheduling": "a scheduling change inside the verifier",
    "tool-profiler": "an instrument",
    "tool-submit-archive": "a naming convention for submitted files",
    "tool-judge-factor": "a constant relating two measurements",
    "tool-debug-sidecars": "an overlay emitted beside a grid",
    "tool-tests-assert-correctness": "a rule about what a test may assert",
    "tool-remeasure-constants": "the pattern behind six other entries",
}

# fold the short layer into the entries themselves
for _e in ENTRIES:
    if _e["id"] in GIST:
        _e["gist"] = GIST[_e["id"]]
    if _e["id"] in GLYPHS:
        _e["glyphs"] = GLYPHS[_e["id"]]
    _e["important"] = _e["id"] in IMPORTANT
    if _e["id"] in NOT_REPRESENTABLE:
        _e["not_representable"] = NOT_REPRESENTABLE[_e["id"]]




