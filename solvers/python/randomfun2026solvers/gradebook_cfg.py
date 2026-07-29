#!/usr/bin/env python3
"""State store and block CFG for a dedicated `gradebook` machine.

The LM-1 build already found the right *word*: one packed cell a student,
``cell = packed * 2^14 + (16384 - id)``, which turns TOP into a single ``AND``
and AVG into a sum of raw cells.  What it could not fix is that every variable
lives in a tape, so the 62%/30% tape/ROM split in ``DATAFLOW-SURVEY.md`` §1 eats
the machine.  This module keeps the packed word and moves it into pipes.

Two changes to the LM-1 data model, both measured in :data:`LAYOUT_COSTS`:

* **base 2^18, not 2^14.**  ``T = 16384 - id`` needs 14 bits, but AVG sums the
  *raw* cells and the id column sums to at most ``16 x 15384 = 246,024``, which
  is 18 bits.  At base 2^14 that carry lands in subject 4's field and AVG has to
  subtract a load-time ``IDSUM`` constant.  At base 2^18 the whole id column
  stays below the lowest grade field, so ``(sum >> sh) & 2047`` is exact with no
  constant and no correction term.  ``18 + 4*11 = 62`` bits still fits a signed
  64-bit word (the largest sum is ``1601 * 2^51``, about 3.6e18 against 9.2e18).
* **a second ring holding the bare ids.**  A search over packed cells needs two
  live constants -- the split base and the target -- so it cycles the scratch
  file twice a slot: 13 ticks a student.  With the ids in their own ring the
  target sits in ``B`` for the whole lap, ``-`` and ``X`` never touch ``B``, and
  a slot costs 7.  The two rings are only ever advanced together (a search steps
  both; AVG and TOP take whole laps), so they can never drift apart.

Field layout is normalised to four subjects whatever ``K`` is, so a shift is a
function of ``s`` alone::

    cell(i) = SUM_s g_i(s) << (62 - 11*s)   +   (16384 - id(i))
    sh(s)   = 62 - 11*s                     -- 51, 40, 29, 18

and the four operations become:

    GET  (cell >> sh) & 2047
    SET  cell += (v - old) * (1 << sh)      -- a multiply, never a signed shift
    AVG  (SUM_i cell_i >> sh) & 2047, divided by N
    TOP  max_i (cell_i & ((2047 << sh) | 16383)), then 16384 - (best & 16383)

TOP's key is lexicographic on ``(grade, 16384 - id)`` exactly as the LM-1 build
found: a higher grade wins, and a tie is broken by the *larger* complement, i.e.
the smaller id.  An all-zero subject therefore still names the smallest id, and
a seed of ``0`` loses to every real key because ``T >= 6385``.

Three pipes and no little men holding state:

* ``RING`` (``rr``/``sr``) -- ``N`` packed cells then a sentinel ``-N``.  Cells
  are strictly positive, so a lap ends on a bare ``X`` with no counter, and the
  sentinel *is* AVG's divisor: the lap that needs ``N`` is the lap that finds it.
* ``IDS`` (``rq``/``sq``) -- the ``N`` raw ids then a sentinel ``-1``, aligned
  slot for slot with ``RING``.
* ``FILE`` (``rt``/``st``) -- scratch.  It holds ``[cnt, K]`` while the roster is
  being read and is **empty between operations**, which is what lets TOP use it
  as an ``N``-slot key buffer.

**Phase does have to be restored, and that is not obvious.**  ``sudoku_cfg``
gets away with leaving its ring wherever an access stopped, because a mask ring
is an unordered set and every round touches one slot.  Here the ring is
unordered too -- a search is a lookup, AVG is a sum, TOP is a max -- but two of
the four operations are *whole-ring* scans, and a sentinel-terminated scan only
ever covers head-to-sentinel.  A search that stops in the middle therefore hides
the cells behind it from the next AVG.  ``REST`` walks the remainder of the lap
(both rings together, 5 ticks a slot) so that the rings are aligned between
operations; it costs ~45 ticks on GET and SET and is the price of not carrying a
loop counter.

See :func:`layout_costs` for the numbers behind choosing this over the
subject-major transpose the sudoku result suggested, and :data:`PROJECTION` for
what the whole thing is expected to score.
"""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = [
    "BASE_SHIFT",
    "FIELD_BITS",
    "ID_BIAS",
    "LAYOUT_COSTS",
    "PROJECTION",
    "WORKER",
    "block_cells",
    "glyph_cells",
    "layout_costs",
    "simulate_worker",
    "worker_glyph_cells",
]

#: Bits the id complement occupies at the bottom of a cell.  18, not 14, so that
#: a column of sixteen complements cannot carry into subject 4's field.
BASE_SHIFT = 18

#: Bits a grade field occupies.  11, not 7: AVG sums sixteen *packed* cells in
#: one go and ``16 * 100 = 1,600`` must not reach the neighbouring field.
FIELD_BITS = 11

#: ``T = ID_BIAS - id``; the complement is what makes TOP's tie-break fall out of
#: the ordering instead of costing a second comparison.
ID_BIAS = 16384

#: Bit offset of subject ``s`` (1-based) inside a cell: ``62, 51, 40, 29, 18``.
def shift_for(subject: int) -> int:
    """Bit offset of subject ``s``'s field -- ``62 - 11*s``."""
    return BASE_SHIFT + FIELD_BITS * (4 - subject)


# ══════════════════════════════════════════════════════════════════ the program ═
#
# Tokens: `L<n>` literal, `ri` read input, `so` send output, `rr`/`sr` the cell
# ring, `rq`/`sq` the id ring, `rt`/`st` the scratch file, and the plain glyphs.
# A block ending in `X` names three lanes (`neg`/`zero`/`pos`); one ending in `x`
# names two (`one`/`zero`, off the backpack's low bit); one ending in `d` names
# two (`pos`/`zero`), which is the counted-loop test.
#
# The backpack is the *operation counter* for a batch round, not a loop counter:
# every loop in the program is sentinel-terminated, so `b` is written once a
# round and `m` once an operation and nothing else competes for it.
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ INIT: the sentinels go in first ═══════════════════════════════════════
    # `-N` is pushed before any cell, so after the roster one rotation puts it at
    # the tail where a lap will find it.  It doubles as AVG's divisor.
    "INIT": ([
        "ri", "N", "sr",                  # RING = [-N]
        "N", "st",                         # FILE = [N]   (the student counter)
        "L1", "N", "sq",                   # IDS  = [-1]
        "ri", "st",                        # FILE = [N, K]
    ], "ROSTER"),

    # ══ ROSTER: one student a lap; FILE is [cnt, K] at the top of every lap ════
    "ROSTER": (["rt", "X"], {"pos": "STU", "zero": "PHASE", "neg": "PHASE"}),

    "STU": ([
        "M", "L1", "N", "+", "st",         # FILE = [K, cnt-1]
        "rt", "st",                        # A = K;  FILE = [cnt-1, K]
        "b",                               # BP = K -- the grade counter
        "ri", "sq",                        # A = id; IDS << id
        "N", "M", "L16384", "+",           # A = T = 16384 - id
        "st",                              # FILE = [cnt-1, K, T]
        "L0", "M",                         # B = packed = 0
    ], "HORN"),

    # Horner in subject order; `*` and `+` leave B, so the accumulator rides the
    # whole loop in B and the file is never touched.
    # Body first, test last: `1 <= K <= 4` is a rule of the problem, so the loop
    # always runs at least once and the guarding test was a block visit -- 39
    # ticks of corridor -- spent to check something the rules already promise.
    "HORN": (["L2048", "*", "M", "ri", "+", "M", "m", "d"],
             {"pos": "HORN", "zero": "PADSET"}),

    # K < 4 packs the grades too far right; `4 - K` more Horner steps with an
    # implicit zero grade slide subject 1 up to bit 51.
    # `M` is needed to build `4 - K`, so the accumulator is parked for the four
    # glyphs that need B and taken straight back out.
    "PADSET": ([
        "st",                              # FILE = [cnt-1, K, T, packed]
        "rt", "st",                        # A = cnt-1; FILE = [K, T, packed, cnt-1]
        "rt",                              # A = K;     FILE = [T, packed, cnt-1]
        "N", "M", "L4", "+",               # A = 4 - K
        "b",                               # BP = 4 - K
        "N", "M", "L4", "+",               # A = K again
        "st",                              # FILE = [T, packed, cnt-1, K]
        "rt", "st",                        # A = T;     FILE = [packed, cnt-1, K, T]
        "rt",                              # A = packed; FILE = [cnt-1, K, T]
        "M",                               # B = packed
        "d",                               # `4 - K` may be zero, so guard first
    ], {"pos": "PAD_B", "zero": "CELL"}),
    # ... but the guard is `PADSET`'s own last glyph, because `PAD_B` retests for
    # itself: the back edge, and with it the separate test block, is gone.
    "PAD_B": (["L2048", "*", "M", "m", "d"], {"pos": "PAD_B", "zero": "CELL"}),

    # ══ CELL: packed << 18 | T, into the ring ═════════════════════════════════
    "CELL": ([
        "M", "L18", "W", "{",              # A = packed << 18
        "M",                               # B = that
        "rt", "st", "rt", "st",            # cycle round to T
        "rt",                              # A = T;  FILE = [cnt-1, K]
        "+", "sr",                         # RING << cell
    ], "ROSTER"),

    # ══ PHASE: drop K, rotate both sentinels to the tail ══════════════════════
    "PHASE": (["rt", "rr", "sr", "rq", "sq"], "ROUND"),

    # ══ ROUND / OP: `O` into the backpack, then one operation a lap ═══════════
    "ROUND": (["ri", "b"], "OP"),
    "OP": (["d"], {"pos": "OP_GO", "zero": "ROUND"}),

    # A = op - 2 splits GET (neg) from SET (zero) from AVG/TOP (pos) in one `X`;
    # the second `X` splits those two.  Four leaves, two tests, no backpack.
    "OP_GO": (["m", "ri", "M", "L2", "W", "-", "X"],
              {"neg": "GET", "zero": "SET", "pos": "D34"}),
    "D34": (["M", "L1", "W", "-", "X"],
            {"zero": "AVG", "pos": "TOP", "neg": "AVG"}),

    # ══ the shared id search ══════════════════════════════════════════════════
    # The caller parks a return tag in FILE -- 0 for GET, 1 for SET -- because BP
    # is the round's operation counter and B is the search target.  The target
    # rides B untouched: `q`, `r`, `s`, `-` and `X` all leave B alone.
    "GET": (["L0", "st", "ri", "M"], "S_L"),
    "SET": (["L1", "st", "ri", "M"], "S_L"),
    # Two branches a slot, so the loop cannot collapse to one block -- but the
    # *third* block was only ever `S_SKIP` handing the cell back and returning to
    # the read.  Rotated into `S_LOOP`, which is `S_SKIP` and `S_L` end to end:
    # `S_L` is now just the entry `GET`/`SET` arrive at.
    "S_L": (["rq", "sq", "X"], {"pos": "S_TEST", "zero": "S_TEST", "neg": "S_LOOP"}),
    "S_TEST": (["-", "X"], {"zero": "FOUND", "pos": "S_LOOP", "neg": "S_LOOP"}),
    "S_LOOP": (["rr", "sr", "rq", "sq", "X"],
               {"pos": "S_TEST", "zero": "S_TEST", "neg": "S_LOOP"}),
    "FOUND": (["rt", "X"], {"zero": "G_HIT", "pos": "S_HIT", "neg": "G_HIT"}),

    # ══ GET: one shift and one mask ═══════════════════════════════════════════
    "G_HIT": ([
        "ri", "M", "L11", "*",             # A = 11*s
        "M", "L62", "W", "N", "+",         # A = 62 - 11*s = sh
        "M",                               # B = sh
        "rr", "sr",                        # A = cell, put back
        "}",                               # A = cell >> sh
        "M", "L2047", "&",                 # A = grade
        "so",
    ], "REST"),

    # ══ SET: cell += (v - old) << sh ══════════════════════════════════════════
    "S_HIT": ([
        "ri", "M", "L11", "*",
        "M", "L62", "W", "N", "+",         # A = sh
        "M",                               # B = sh -- `{`, `}` and `st` all leave it
        "L1", "{", "st",                   # FILE = [P] with P = 1 << sh
        "rr", "st",                        # A = cell (taken out); FILE = [P, cell]
        "}",                               # A = cell >> sh
        "M", "L2047", "&",                 # A = old
        "N", "M",                          # B = -old
        "ri", "+",                         # A = v - old
        "M",                               # B = v - old
        "rt",                              # A = P;    FILE = [cell]
        "*",                               # A = (v - old) * P -- a multiply, so a
        "M",                               #   demoting SET never shifts a negative
        "rt",                              # A = cell; FILE = []
        "+", "sr",                         # RING << cell'
    ], "REST"),

    # ══ REST: put the sentinels back at the tail ══════════════════════════════
    # A sentinel lap only ever covers head-to-sentinel, so "never restore phase"
    # -- the trick that works for sudoku's unordered mask ring -- is *wrong* here
    # the moment an operation is a full column scan.  A search stops where it
    # likes; this walks the rest of the lap so AVG and TOP always start aligned.
    # Both rings advance together, which is the invariant the search relies on.
    # One block, not three: both arms did the identical `sr rq sq`, so the whole
    # lap fits before the branch if the word being tested is parked in B across
    # the two ring moves and swapped back out.  See `_FUSED`.
    "REST": (["rr", "M",                   # A = cell, B = cell
              "sr",                        # the cell goes back (`sr` leaves A)
              "rq", "sq",                  # the id ring advances in lockstep
              "W", "X"],                   # A = cell again; branch on it
             {"pos": "REST", "zero": "REST", "neg": "OP"}),

    # ══ AVG: one lap of raw cells; the sentinel it stops on is the divisor ═════
    "AVG": ([
        "ri", "M", "L11", "*",
        "M", "L62", "W", "N", "+",         # A = sh
        "st",                              # FILE = [sh]
        "L0", "M",                         # B = running sum
        "rr",                              # the lap's first cell, read here
    ], "A_L"),
    # Body first again: `4 <= N`, so a lap always has a cell before its sentinel
    # and the guard was a visit spent on a promise.  The read moves to the top of
    # the preheader and the test to the bottom of the body, so `A_END` still
    # arrives holding the unsent sentinel in A and the sum in B.
    "A_L": (["sr", "+", "M", "rr", "X"],
            {"pos": "A_L", "zero": "A_L", "neg": "A_END"}),
    "A_END": ([
        "sr",                              # the sentinel goes back
        "W", "st",                         # FILE = [sh, sum]
        "W", "N", "st",                    # FILE = [sh, sum, N]
        "rt", "M",                         # B = sh
        "rt", "}",                         # A = sum >> sh
        "M", "L2047", "&",                 # A = the subject's column total
        "M", "rt", "W",                    # A = total, B = N
        "/", "so",                         # A = total // N
    ], "OP"),

    # ══ TOP: mask the column into FILE, then take the max out of it ═══════════
    # Two laps beat one.  A single lap has to keep the mask *and* the running
    # best alive across `rr`, which costs two file cycles a student (13 ticks);
    # split in two, each lap pins its one durable value in B and a student costs
    # 5 + 4.
    "TOP": ([
        "ri", "M", "L11", "*",
        "M", "L62", "W", "N", "+",         # A = sh
        "M", "L2047", "{",                 # A = 2047 << sh
        "M", "L16383", "|",                # A = mask (bits 14..17 are always 0)
        "M",                               # B = mask
        "rr", "sr",                        # the lap's first cell, read and returned
    ], "T_L"),
    # Body first, as `A_L`.  `&` and `st` leave B, so the mask still rides the
    # whole lap; the cell is sent back at the *end* of the body, which is why
    # `T_MID` no longer has to do it.
    "T_L": (["&", "st", "rr", "sr", "X"],
            {"pos": "T_L", "zero": "T_L", "neg": "T_MID"}),
    "T_MID": (["L1", "N", "st", "L0", "M"], "T_X"),
    "T_X": (["rt", "X"], {"pos": "T_CMP", "zero": "T_CMP", "neg": "T_END"}),
    "T_CMP": (["-", "X"], {"pos": "T_SET", "zero": "T_X", "neg": "T_X"}),
    "T_SET": (["+", "M"], "T_X"),
    "T_END": ([
        "W",                               # A = best
        "M", "L16383", "&",                # A = T
        "N", "M", "L16384", "+",           # A = 16384 - T = id
        "so",
    ], "OP"),
}


_BIN = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "~": lambda a, b: a ^ b,
    "{": lambda a, b: a << b if 0 <= b <= 63 else 0,
    "}": lambda a, b: a >> b if b >= 0 else 0,
}


def glyph_cells(token: str) -> int:
    """Grid cells `token` occupies -- a literal is written between backticks."""
    if token.startswith("L") and token[1:].isdigit():
        return 1 if int(token[1:]) <= 9 else len(token[1:]) + 2
    return 1


def block_cells(name: str) -> int:
    """Glyph cells in one block, counting literals as written."""
    return sum(glyph_cells(t) for t in WORKER[name][0])


def worker_glyph_cells() -> int:
    """Grid cells the program's glyphs need, counting literals as written."""
    return sum(block_cells(name) for name in WORKER)


def simulate_worker(
    rounds: list[dict[str, Any]], *, max_steps: int = 8_000_000
) -> tuple[list[int], int]:
    """Run :data:`WORKER` over one test case; return its outputs and a tick count.

    An op-level model -- A, B, a write-only BP, the three pipes as deques and the
    input as a queue.  Ticks are counted in **glyph cells**, since a little man
    walks a multi-digit literal one cell a tick; the corridors a real grid needs
    between blocks are not counted here.  Running dry on the input is how a case
    ends in the real machine, so it ends the simulation rather than failing.
    """
    inp: deque[int] = deque(int(v) for r in rounds for v in r["in"])
    ring: deque[int] = deque()
    ids: deque[int] = deque()
    file: deque[int] = deque()
    a = b = bp = 0
    out: list[int] = []
    ticks = 0

    block = "INIT"
    while True:
        toks, succ = WORKER[block]
        branch: str | None = None
        for t in toks:
            ticks += glyph_cells(t)
            if ticks > max_steps:  # pragma: no cover - a runaway guard
                raise RuntimeError(f"worker did not settle (in {block})")
            if t.startswith("L"):
                a = int(t[1:])
            elif t == "ri":
                if not inp:
                    return out, ticks
                a = inp.popleft()
            elif t == "so":
                out.append(a)
            elif t == "rr":
                a = ring.popleft()
            elif t == "sr":
                ring.append(a)
            elif t == "rq":
                a = ids.popleft()
            elif t == "sq":
                ids.append(a)
            elif t == "rt":
                a = file.popleft()
            elif t == "st":
                file.append(a)
            elif t == "M":
                b = a
            elif t == "W":
                a, b = b, a
            elif t == "N":
                a = -a
            elif t == "/":
                a, b = a // b, a % b
            elif t == "%":
                a = a % b if b else 0
            elif t in _BIN:
                a = _BIN[t](a, b)
            elif t == "b":
                bp = a
            elif t == "m":
                bp -= 1
            elif t == "]":
                bp >>= 1
            elif t == "H":
                return out, ticks
            elif t == "X":
                branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
            elif t == "x":
                branch = "one" if bp & 1 else "zero"
            elif t == "d":
                branch = "pos" if bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
        block = succ if isinstance(succ, str) else succ[branch]


# ═════════════════════════════════════════════════════════════ the layout choice ═
#
# Ticks an operation at the worst legal size (N=16, K=4), hand-costed on the same
# primitives the shipped ring machines use: a straight-line `rs` pair moves one
# ring slot in 2 ticks, a glyph is a tick, a multi-digit literal is one tick a
# digit plus two backticks.  A search is charged its average distance, N/2.
#
#   A. **student-major, one packed word a student** (this module).  Ops are one
#      indexed search (GET/SET) or one lap (AVG/TOP).  The lap is the ideal ring
#      shape and the search never restores phase.
#
#   B. **subject-major: K blocks of N key words**, `key(s,i) = g_i(s)*2^18 + T_i`.
#      This is the sudoku transpose applied here, and it *does* make TOP cheaper
#      -- a raw compare with no mask, B pinned to the running best, 6 ticks a
#      student against 9.  It loses anyway.  A column is 16 grades of 7 real bits
#      = 112, so a subject does not fit a word and the "one word a round" prize
#      is not on the table; what is on the table is K*N = 64 ring slots instead
#      of 16, and GET/SET have to find a student *inside* a block, which is the
#      same search plus a rotation to the block and out of it.  Rotation is 2
#      ticks a slot however cheap the body is, so quadrupling the ring costs more
#      than the mask ever did.  Measured in the table below.
#
#   C. **subject-major plus maintained aggregates** -- `SUM[s]` and `BEST[s]` in
#      a 2K-slot ring, so AVG and TOP are a short rotation and a divide instead
#      of a lap.  This is the cheapest in ticks by a wide margin (roughly 65 a
#      operation against 150) and it is still the wrong answer, because a
#      demoting SET has to rescan and because the maintenance is ~10 more blocks.
#      The score is `side^2 * ticks`: ten blocks is ~22 rows on the 2.2-rows-a-
#      block slope the built machines sit on, which takes a 50-row machine to 72
#      and the area from 2,500 to 5,200.  A 2.1x area loss does not buy a 2.3x
#      tick win once the rescan is priced in.  **Footprint is squared and ticks
#      are not**; that is the whole argument, and it is why this module stays
#      simple rather than getting clever about caching.
#
#   D. **the shipped LM-1 CPU**, for scale: 93x92, ~738,432 judge ticks a case.
#
# `side` is projected from block count on the slope the built machines sit on --
# ``sudoku_ring`` is 11 blocks in 18 interior rows, ``subset_sum_grid`` 81 in
# 179, i.e. 1.6 to 2.2 rows a block, against a width of 25 to 46.  Row A's 37
# blocks put it at 60-85 a side; 70 is the middle of that and what the score
# below assumes.  Rows A's per-operation ticks are **measured** by
# :func:`simulate_worker` on the worst legal batch (N=16, K=4, 10x8); the rest
# are hand-costed on the same primitives.
LAYOUT_COSTS: dict[str, dict[str, Any]] = {
    "A: student-major packed word, two rings": {
        "ring_slots": 17,
        "get": 157,       # measured
        "set": 169,       # measured
        "avg": 136,       # measured
        "top": 245,       # measured
        "ticks_per_op": 177,
        "blocks": 0,      # filled in from the real CFG by layout_costs()
        "glyph_cells": 0,
        "side": 70,
    },
    "B: subject-major, K blocks of N keys": {
        "ring_slots": 65,
        "get": 204,       # rotate to block s (~32 slots) + search + re-align
        "set": 204,       # a field write is cheaper, reaching it is not
        "avg": 183,
        "top": 214,       # the mask disappears; the rotation more than replaces it
        "ticks_per_op": 201,
        "blocks": 40,
        "glyph_cells": 340,
        "side": 75,
    },
    "C: subject-major plus SUM/BEST caches": {
        "ring_slots": 73,
        "get": 204,
        "set": 260,       # write, fix SUM, fix BEST, rescan when it demotes
        "avg": 30,
        "top": 30,
        "ticks_per_op": 128,
        "blocks": 50,
        "glyph_cells": 470,
        "side": 90,
    },
    "D: the shipped LM-1 CPU": {
        "ring_slots": 0,
        "get": 9000,
        "set": 9000,
        "avg": 12000,
        "top": 14000,
        "ticks_per_op": 11000,
        "blocks": 0,
        "glyph_cells": 0,
        "side": 93,
    },
}


#: What this is worth, stated as a range because the graded data is unseen.
#:
#: The op model charges glyph cells only; a real grid also walks the corridors
#: between blocks, which measured ~2.5x on ``sudoku`` (99 modelled a round, 250
#: on the grid).  The judge's cases then run heavier than the public ones -- for
#: *this* problem the shipped CPU measured 738,432 judge ticks against 286,287
#: local, a factor of 2.58.  Applying both to the model's public average of
#: 1,726 ticks a case gives ~11,100 judge ticks, and ``70^2 x 11,100`` is
#: 5.4e7 against the CPU's 6.39e9.
#:
#: The pessimistic end assumes every graded case is the worst batch the rules
#: allow -- N=16, K=4, ten rounds of eight ``TOP``s, which the model runs in
#: 21,440 ticks.  That is 53,600 on the grid and ``70^2 x 53,600`` = 2.6e8, a
#: 24x.  It is a floor, not a forecast: gradebook's own public data tops out at
#: 22 operations, and a case of eighty identical TOPs is not what a grader
#: writes.  A mixed 50-operation case at full width lands at 49x.
PROJECTION: dict[str, Any] = {
    "cpu_score": 6_386_700_098,
    "cpu_side": 93,
    "cpu_judge_ticks": 738_432,
    "model_public_mean": 1_726,
    "model_public_max": 4_432,
    "corridor_factor": 2.5,
    "judge_factor": 2.58,
    "projected_side": 70,
    "score_central": 70**2 * 11_100,   # ~117x
    "score_pessimistic": 70**2 * 53_600,  # ~24x
}


def layout_costs() -> dict[str, dict[str, Any]]:
    """The costed comparison, with row A's size taken from :data:`WORKER`."""
    table = {k: dict(v) for k, v in LAYOUT_COSTS.items()}
    row = table["A: student-major packed word, two rings"]
    row["blocks"] = len(WORKER)
    row["glyph_cells"] = worker_glyph_cells()
    for name, entry in table.items():
        entry["score"] = entry["side"] ** 2 * entry["ticks_per_op"]
        del name
    return table


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    print(f"{len(WORKER)} blocks, {worker_glyph_cells()} glyph cells")
    for name, row in layout_costs().items():
        print(f"  {row['ticks_per_op']:>6} ticks/op  side ~{row['side']:>3}  {name}")
