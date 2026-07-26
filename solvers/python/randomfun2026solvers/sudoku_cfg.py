#!/usr/bin/env python3
"""State store and block CFG for a dedicated `sudoku-validity` machine.

The workload looks like three indexed accesses a round -- test and set the row
mask, the column mask and the box mask -- so every earlier sketch spent its
budget on *reaching* 27 masks.  It is the wrong axis.  Transposing the store
turns the round into **one** access:

    row/col/box masks   27 words of 9 bits, indexed by unit -- 3 touches a round
    per-value words      9 words of 27 bits, indexed by `v`  -- 1 touch a round

`WORD[v]` is "where has `v` already been placed": bit `r` for rows 0..8, bit
`9 + c` for columns, bit `18 + b` for boxes.  A round builds one three-bit
pattern

    P = (1 << r) | (1 << (9 + c)) | (1 << (18 + b))

and the whole test-and-set is five glyphs plus a branch::

    rr      A = WORD[v]
    +       A = WORD[v] + P          (B = P survives every one of these)
    sr      push it back
    &       A = (WORD[v] + P) & P
    -       A = ((WORD[v] + P) & P) - P
    X       zero -> valid, negative -> a duplicate

**Why `+` then `&` is exact.**  The three bits of `P` sit at distinct
positions, so when none of them is set in the word the sum is the union and
`t & P == P`.  When one is set the addition carries out of it and that bit of
`t` is 0, so `t & P != P` -- and a carry can only *enter* a `P` bit from a
lower `P` bit that was itself already set, i.e. from a violation we are already
reporting.  So the test never says "invalid" for a valid grid and never says
"valid" for an invalid one.  The word written back on the failing path is
garbage, which is free: the round emits `0` and the case is over
(`"Your program only needs to output 0 once"`).

Two pipes, no little men holding state:

* ``RING`` -- the nine per-value words, rotated by ``rr``/``sr``.  A round
  rotates ``v - 1`` slots to reach ``WORD[v]``, accesses it (which advances one
  more) and rotates the remaining ``9 - v`` to restore the phase, so the
  rotation cost is a constant eight slots however the values fall.
* ``FILE`` -- a scratch FIFO for the prologue.  ``A`` is the only scratch a man
  has, ``B`` holds exactly one durable word and ``BP`` is write-only, so every
  second live value in the prologue is parked here.  Every block leaves FILE
  empty, so a park costs one glyph and never a rotation.

See :func:`layout_costs` for the numbers behind choosing this over a 27-slot
ring, a 4-word packing and a four-man one-hot memory.

**The next 10%, deliberately not taken here.**  Restoring the phase costs a
fixed eight slots a round.  Tracking it instead -- park `v`, and rotate
`(v' + 8 - v) % 9` next round -- makes the rotation uniform on 0..8, so it
averages four slots, but the modulus costs `M L8 + M rq N + M L9 W / W b`
against the four glyphs `ri sq b m` it replaces.  Net saving is ~10 ticks a
round *on average* and a ~6-tick loss in the worst case, for one more park of
`P` and a `/` in the hot path.  Worth doing once the grid is laid out and the
routing cost of the extra glyphs is known, not before.
"""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = [
    "FILE_WORDS",
    "LAYOUT_COSTS",
    "RING_WORDS",
    "WORKER",
    "block_cells",
    "glyph_cells",
    "layout_costs",
    "simulate_worker",
    "worker_glyph_cells",
]

#: One word per value 1..9; each is 27 live bits (rows 0-8, cols 9-17, boxes 18-26).
RING_WORDS = 9

#: Deepest the scratch FIFO ever gets during a round's prologue.
FILE_WORDS = 3

#: Bit offsets of the three fields inside a per-value word.
ROW_SHIFT, COL_SHIFT, BOX_SHIFT = 0, 9, 18


# ══════════════════════════════════════════════════════════════════ the program ═
#
# Tokens: `L<n>` literal, `ri` read input, `so` send output, `rr`/`sr` the mask
# ring, `rq`/`sq` the scratch file, `H` halt, and the plain glyphs.  A block
# ending in `X` names three lanes (`neg`/`zero`/`pos`); one ending in `x` names
# two (`one`/`zero`, off the backpack's low bit); one ending in `d` names two
# (`pos`/`zero`), which is the counted-loop test.
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ INIT: nine empty words into the ring ══════════════════════════════════
    # `s` leaves A alone, so the fill is one literal and a counted loop.
    "INIT": (["L9", "b", "L0"], "FILL"),
    "FILL": (["d"], {"pos": "FILL_BODY", "zero": "ROUND"}),
    "FILL_BODY": (["sr", "m"], "FILL"),

    # ══ ROUND: read `r c v`, build P, arm the rotation ════════════════════════
    # The order is forced by there being one durable register.  `{`, `/` and `*`
    # all read B, so a value that must outlive the next arithmetic glyph is
    # parked in FILE and popped back.  `/` earns its keep twice: one glyph gives
    # `r/3` in A and `r%3` in B, and the same for `c`.
    "ROUND": ([
        "ri", "sq",                            # A = r;  FILE = [r]
        "M", "L3", "W", "/",                   # A = r/3, B = r%3
        "M", "L6", "+", "M", "L3", "*",        # A = 3*(r/3 + 6) = 18 + 3*(r/3)
        "sq",                                  # FILE = [r, 3q']
        "ri", "sq",                            # A = c;  FILE = [r, 3q', c]
        "M", "L3", "W", "/",                   # A = c/3, B = c%3
        "M",                                   # B = c/3
        "rq", "sq",                            # r to the back: [3q', c, r]
        "rq",                                  # A = 3q'; FILE = [c, r]
        "+",                                   # A = 18 + box, the offset folded in
        "M", "L1", "{",                        # A = BB = 1 << (18 + box)
        "sq",                                  # FILE = [c, r, BB]
        "rq",                                  # A = c;  FILE = [r, BB]
        "M", "L9", "+", "M", "L1", "{",        # A = BC = 1 << (9 + c)
        "sq",                                  # FILE = [r, BB, BC]
        "rq",                                  # A = r;  FILE = [BB, BC]
        "M", "L1", "{",                        # A = BR = 1 << r
        "M",                                   # B = BR
        "rq", "+", "M",                        # A = BB + BR;  B = BB + BR
        "rq", "+",                             # A = P
        "M",                                   # B = P -- pinned for the rest
        "ri", "sq",                            # A = v;  FILE = [v]
        "b", "m",                              # BP = v - 1
    ], "ROT1"),

    # ══ ROT1: rotate `v - 1` slots so WORD[v] is at the ring head ═════════════
    # `r` clobbers A only, so B = P rides through the whole loop untouched.
    "ROT1": (["d"], {"pos": "ROT1_BODY", "zero": "ACCESS"}),
    "ROT1_BODY": (["rr", "sr", "m"], "ROT1"),

    # ══ ACCESS: the entire round's test and set ═══════════════════════════════
    "ACCESS": (["rr", "+", "sr", "&", "-", "X"],
               {"zero": "OK", "neg": "BAD", "pos": "BAD"}),

    # ── a duplicate: emit 0 and stop; the case ends here by the rules ─────────
    "BAD": (["L0", "so", "H"], "BAD"),

    # ── still valid: emit 1, then arm the `9 - v` slots back to phase 0 ───────
    "OK": (["L1", "so", "rq", "N", "M", "L9", "+", "b"], "ROT2"),
    "ROT2": (["d"], {"pos": "ROT2_BODY", "zero": "ROUND"}),
    "ROT2_BODY": (["rr", "sr", "m"], "ROT2"),
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
    rounds: list[dict[str, Any]], *, max_steps: int = 4_000_000
) -> tuple[list[int], int]:
    """Run :data:`WORKER` over one test case; return its outputs and a tick count.

    An op-level model -- A, B, a write-only BP, the two pipes as deques and the
    input as a queue.  Ticks are counted in **glyph cells**, since a little man
    walks a multi-digit literal one cell a tick; the corridors a real grid needs
    between blocks are not counted here.  Running dry on the input is how a case
    ends in the real machine, so it ends the simulation rather than failing.
    """
    inp: deque[int] = deque(int(v) for r in rounds for v in r["in"])
    ring: deque[int] = deque()
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
                a = file.popleft()
            elif t == "sq":
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
# Ticks a round, hand-costed against the primitives the shipped machines use: a
# counted ring moves one pipe slot in 5 ticks (`circuit.Circuit.counted_ring` --
# 10 cells, two values a lap), a straight-line `rs` pair moves one in 2, and a
# glyph is a tick.
#
#   A. 27 words of 9 bits, one mask a slot.  Three touches a round at indices
#      r, 9+c, 18+b -- monotone, so a single lap of 27 does all three: 24
#      counted rotations = 120 ticks, plus 3 x 5 for the test-and-set and four
#      loop arms.  Cheapest in glyphs, hopeless in ticks; the rotation alone
#      exceeds the whole budget of layout C.
#
#   B. 4 words of 7 masks (243 bits inside 252).  Rotation nearly vanishes --
#      the three word indices are monotone in {0,1}/{1,2}/{2,3}, so one lap of 4
#      is typical -- but every access must now compute `j = 9*i + v - 1`, split
#      it with `/` into a word index and a shift, and rebuild `1 << s`.  That is
#      ~30 ticks of arithmetic an access, three times a round, and each one
#      needs a third live value, so the word index spills to FILE and comes back.
#      Packing moves the cost from rotation to addressing; it does not remove it.
#
#   C. 9 words of 27 bits, transposed by value (this module).  One touch a
#      round.  Rotation is a fixed eight slots and the access is 6 glyphs; the
#      prologue that builds P is the whole remaining cost.
#
#   D. 4 little men each holding a word in B, one-hot addressed.  A man cannot
#      compare an address against his own index -- every comparison glyph reads
#      B and B *is* the value -- so the decode has to be one-hot through the
#      backpack (`b`, `]`, `x`), the address has to be built (`1 << w`) and
#      broadcast down the file, and the answer still comes back out through a
#      pipe.  For four cells that is more ticks than four rotations, plus a
#      decoder, an igniter and an answer pipe in glyphs, plus four extra live
#      runners on every tick of the grader's clock (`simcost.live_runners`).
#      Strictly dominated; kept here so the comparison is complete.
LAYOUT_COSTS: dict[str, dict[str, Any]] = {
    "A: 27-slot ring, one mask a slot": {
        "words": 27,
        "accesses_per_round": 3,
        "rotation_ticks": 120,
        "other_ticks": 50,
        "ticks_per_round": 170,
        "glyph_cells": 95,
        "side": 18,
    },
    "B: 4 words x 7 masks, packed": {
        "words": 4,
        "accesses_per_round": 3,
        "rotation_ticks": 20,
        "other_ticks": 115,
        "ticks_per_round": 135,
        "glyph_cells": 230,
        "side": 30,
    },
    "C: 9 words x 27 bits, by value": {
        "words": 9,
        "accesses_per_round": 1,
        "rotation_ticks": 34,   # 8 slots x (rr sr m) + two loop tests
        "other_ticks": 65,      # 51 to build P, 6 to access, 8 to re-arm, emit
        "ticks_per_round": 99,  # measured by simulate_worker, not estimated
        "glyph_cells": 0,       # filled in from the real CFG by layout_costs()
        "side": 28,
    },
    "D: 4 men, one word in B each, one-hot": {
        "words": 4,
        "accesses_per_round": 3,
        "rotation_ticks": 0,
        "other_ticks": 200,
        "ticks_per_round": 200,
        "glyph_cells": 300,
        "side": 35,
    },
}


def layout_costs() -> dict[str, dict[str, Any]]:
    """The costed comparison, with row C's glyph count taken from :data:`WORKER`."""
    table = {k: dict(v) for k, v in LAYOUT_COSTS.items()}
    table["C: 9 words x 27 bits, by value"]["glyph_cells"] = worker_glyph_cells()
    return table


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    print(f"{len(WORKER)} blocks, {worker_glyph_cells()} glyph cells")
    for name, row in layout_costs().items():
        print(f"  {row['ticks_per_round']:>4} ticks/round  side ~{row['side']:>2}  {name}")
