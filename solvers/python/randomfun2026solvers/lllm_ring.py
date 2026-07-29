#!/usr/bin/env python3
"""`little-little-little-man` as a dataflow ring machine — no CPU, no ISA, no ROM.

`littleman/LLLM-DESIGN.md` has the full rationale.  In one paragraph: the display
harness (:mod:`lllm_panel`) is the engine-proven `snake-finish` painter, so a
frame is a **delta** and an interpreted tick repaints exactly two pixels; the
interpreted program lives as one class byte per cell circulating in a long pipe
**ring**; and the interpreter's own registers live in a short second pipe loop
used as a **rotating register file** — the only way to have more state than the
two hands and a write-only backpack a little man carries.

Two structures and one worker room:

* ``STORE`` — a **fixed 32-word ring**.  Sixteen display rows of sixteen cells,
  eight cells packed into each word at five bits apiece, so a cell's word index
  is ``POS / 8`` and its bit offset ``POS % 8`` — and ``/`` yields both in one
  glyph.  The ring is 32 words whatever ``H`` is, because a constant modulus is
  what makes the per-tick rotation ``(j - CUR) mod 32`` a sign test and an add.
* ``FILE`` — eight slots in cyclic order ``[K, HALT, BI, AI, DIR, POS, CUR,
  WORD]``.  Reading slot *i* means rotating *i* words, so every block below
  touches the slots in exactly that order and no other.

**A tick does not lap the store.**  The word the man's cell lives in stays in the
file (``WORD``) with ``CUR`` naming it, and the ring keeps a *hole* where it came
from — so half the interpreted ticks touch the store not at all, and a miss
rotates ``(j - CUR - 1) mod 32``, which is 0 or 1 whenever the man moved east or
south.  Measured over the public cases that is **6.6 word-moves an interpreted
tick against the 257-word lap** the unpacked store used to turn, and the store
loop fell from 92% of all block visits to 2% of all ticks.

**The store value is the class biased so that five bits hold it.**  Non-digits
store ``j = class - 10`` in 0..11 and digits store ``d + 12`` in 12..21; the two
ranges are disjoint and both positive, so one sign test on ``v - 12`` splits
them, and 21 fits the five bits eight-to-a-word needs.

Every non-halting lane converges on ``MOVE`` with one contract — ``A = POS``,
``B =`` the colour to restore, ``BP = DIR`` — which paints both pixels of the
delta and advances ``POS``.  A lane decides *what changed*, never *what to draw*,
which is what keeps the eleven of them to about ten glyphs each.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from randomfun2026solvers.lllm_tables import (
    CLASS_MAGIC,
    COLOUR_MAGIC,
    HASH_MUL,
    HASH_SHIFT,
    WALL_BIAS,
)

__all__ = [
    "FILE_WORDS",
    "STORE_WORDS",
    "WORKER",
    "simulate_worker",
    "store_words",
    "worker_glyph_cells",
]

PANEL_W = PANEL_H = 16

#: Cells packed into one store word, and the bits each one gets.  Classes run
#: 0..21, so five bits hold one; eight cells is a 40-bit payload and leaves room
#: above it for the sentinel the setup accumulator carries.
CELLS_PER_WORD = 8
CELL_BITS = 5
#: Bit the setup accumulator's sentinel reaches once a word is complete.
WORD_BIT = CELLS_PER_WORD * CELL_BITS

#: The store is a **fixed** 32-word ring — sixteen rows of sixteen cells, eight
#: cells to a word — whatever `H` is.  A constant modulus is what makes the
#: per-tick rotation `(j - CUR) mod 32` one sign test and one add: no ring
#: length in a register, no sentinel word, and no full lap.
STORE_WORDS = PANEL_W * PANEL_H // CELLS_PER_WORD
#: Register-file slots resident, plus one so a full rotation can never block.
#: Eight slots live here and `TICK_LIVE` transiently holds a ninth.
FILE_WORDS = 10

#: Store bias for digits — `v = d + 12` keeps digits clear of the twelve
#: non-digit classes and inside five bits.
DIGIT_BIAS = 12


def store_words(height: int) -> int:  # noqa: ARG001 - the callers pass a height
    """Words resident in ``STORE``: always :data:`STORE_WORDS`, whatever `H` is."""
    return STORE_WORDS


def _rot(n: int) -> list[str]:
    """`n` file slots read and pushed straight back — a rotation, nothing else."""
    return ["rq", "sq"] * n


# ═════════════════════════════════════════════════════════════════ the program ═
#
# Tokens: `L<n>` literal, `ri` read input, `rr`/`sr` the store ring, `rq`/`sq`
# the register file, `sp` send painter, and the plain glyphs.  A block ending in
# `X` names three lanes (`neg`/`zero`/`pos`); one ending in `x` names two
# (`one`/`zero`, off the backpack's low bit); one ending in `d` names two
# (`pos`/`zero`), which is the counted-loop test.
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ INIT ══════════════════════════════════════════════════════════════════
    # FILE during setup is `[ADDR, POS, ROWS, W, WPAD, WALL, ACC]` — the display
    # address being painted, the man's address once found, rows left, the row's
    # real width, its padding to sixteen, whether this row is room wall, and the
    # word accumulator, held *pre-shifted* so a cell only has to add.
    "INIT": ([
        "L0", "sq", "sq",                    # ADDR = 0, POS = 0
        "ri", "M", "ri", "sq",               # B = W, A = H;  ROWS = H
        "W", "sq",                           # W
        "M", "L16", "-", "sq",               # WPAD = 16 - W
        f"L{WALL_BIAS}", "sq",               # row 0 is the room's top wall
        "L32", "sq",                         # ACC = 1 << CELL_BITS
        # one rotation to reach ROWS and tell the painter the frame size
        "rq", "sq", "rq", "sq",
        "rq", "M", "sq", "L4", "W", "{", "sp",  # n = 16*H pairs
        *_rot(4),                            # W WPAD WALL ACC
    ], "ROW"),

    # ══ ROW ═══════════════════════════════════════════════════════════════════
    "ROW": (["rq", "sq", "rq", "sq", "rq", "X"],
            {"zero": "SETUP_DONE", "pos": "ROW_GO", "neg": "SETUP_DONE"}),
    "ROW_GO": ([
        "M", "L1", "W", "-", "sq",           # ROWS - 1
        "rq", "b", "sq",                     # BP = W
        *_rot(3),                            # WPAD WALL ACC
    ], "REAL_LOOP"),
    "ROW_END": (["rq", "sq", "rq", "sq", "rq", "sq", "M", "L1", "W", "-", "X"],
                {"zero": "RE_WALL", "pos": "RE_NORM", "neg": "RE_NORM"}),
    "RE_WALL": ([*_rot(2), "rq", f"L{WALL_BIAS}", "sq", *_rot(1)], "ROW"),
    "RE_NORM": ([*_rot(2), "rq", "L0", "sq", *_rot(1)], "ROW"),

    "REAL_LOOP": (["d"], {"pos": "CELL", "zero": "PAD_SET"}),
    "PAD_SET": ([
        *_rot(4),                            # ADDR POS ROWS W
        "rq", "b", "sq",                     # BP = WPAD
        *_rot(2),                            # WALL ACC
    ], "PAD_LOOP"),
    "PAD_LOOP": (["d"], {"pos": "PAD_CELL", "zero": "ROW_END"}),

    # ── one program cell ─────────────────────────────────────────────────────
    # The `WALL` slot holds 0 or :data:`WALL_BIAS` and is simply *added to the
    # byte*, so a wall row's `+` and `-` decode to the wall class through their
    # own two table entries.  That is what lets the cell body run straight into
    # the digit test with no branch of its own, and what deleted `WALL_CELL`.
    "CELL": ([
        "rq", "M", "L1", "+", "sq",          # ADDR + 1 stored, ADDR rides B
        *_rot(4),                            # POS ROWS W WPAD
        "rq", "sq",                          # WALL, back in place, live in A
        "W", "sp",                           # A = ADDR again, painted
        "ri", "+",                           # A = byte + WALL
        "M", "L48", "W", "-", "X",           # head is now ACC
    ], {"neg": "DEC_ASC", "zero": "DEC_D0", "pos": "DEC_HI"}),
    "PAD_CELL": ([
        "rq", "M", "L1", "+", "sq",
        *_rot(5),                            # POS ROWS W WPAD WALL
        *_rot(1),                            # class 0 adds nothing to ACC
        "W", "sp", "L0", "sp",               # the address, then black
    ], "TAIL_P"),

    # ── the decoder ──────────────────────────────────────────────────────────
    "DEC_ASC": (["+"], "DEC_TAB"),
    "DEC_HI": (["M", "L9", "W", "-", "X"],
               {"neg": "DEC_DIG", "zero": "DEC_DIG", "pos": "DEC_ASC2"}),
    "DEC_ASC2": (["+", "M", "L48", "+"], "DEC_TAB"),
    # A digit can never be the spawn, so it skips the `@` test outright.  The
    # colour goes out with the accumulator still in `B`, which is what keeps the
    # file op ahead of the painter op and the row free of a wrap.
    "DEC_D0": ([f"L{DIGIT_BIAS}", "M", "rq", "+", "sq", "L8", "sp"], "TAIL_R"),
    "DEC_DIG": (["+", "M", f"L{DIGIT_BIAS}", "+",
                 "M", "rq", "+", "sq", "L8", "sp"], "TAIL_R"),
    "DEC_TAB": ([
        "M", f"L{HASH_MUL}", "*",            # A = 915*c             B = c
        "M", f"L{HASH_SHIFT}", "W", "}",     # A = (915*c) >> 8
        "M", "L15", "&",                     # A = i
        "M", "L4", "*", "W",                 # A = i                 B = 4i
        f"L{CLASS_MAGIC}", "}",              # A = CLASS_MAGIC >> 4i
        "M", "L15", "&",                     # A = j
        "M", "rq", "+", "sq", "W",           # ACC += j, pushed;  A = j again
        "M", "L4", "*", "W",                 # A = j                 B = 4j
        f"L{COLOUR_MAGIC}", "}",
        "M", "L15", "&",                     # A = colour
        "sp",
        # The spawn is found by its *colour*: 9 is the man, and no glyph paints 9.
        "M", "L9", "W", "-", "X",
    ], {"zero": "AT_FIX", "pos": "TAIL_R", "neg": "TAIL_R"}),
    "AT_FIX": ([
        "rq", "sq", "M", "L1", "W", "-", "M",  # B = ADDR, the cell just painted
        "rq", "W", "sq",                       # POS = ADDR
        *_rot(5),                              # ROWS W WPAD WALL ACC
    ], "TAIL_R"),

    # ── the word boundary, found by a carry rather than a counter ────────────
    # ACC rides the file pre-shifted, so a cell's whole contribution is one `+`.
    # It starts at `1 << 5` and shifts five bits a cell, so its sentinel reaches
    # bit 40 on exactly the eighth cell.  The test is `ACC - 2^40` rather than
    # `ACC >> 40`, because the *difference* is the thing both lanes want: adding
    # `2^40` back recovers the accumulator and the difference itself **is** the
    # finished word.  So neither lane has to fetch `ACC` a second time.
    "TAIL_R": ([*_rot(6), "L40", "M", "L1", "{", "M", "rq", "-", "X"],
               {"neg": "KEEP_R", "zero": "EMIT_R", "pos": "EMIT_R"}),
    "KEEP_R": (["+", "M", "L5", "W", "{", "sq", "m", "d"],
               {"pos": "CELL", "zero": "PAD_SET"}),
    "EMIT_R": (["sr", "L32", "sq", "m", "d"],
               {"pos": "CELL", "zero": "PAD_SET"}),
    "TAIL_P": ([*_rot(6), "L40", "M", "L1", "{", "M", "rq", "-", "X"],
               {"neg": "KEEP_P", "zero": "EMIT_P", "pos": "EMIT_P"}),
    "KEEP_P": (["+", "M", "L5", "W", "{", "sq", "m", "d"],
               {"pos": "PAD_CELL", "zero": "ROW_END"}),
    "EMIT_P": (["sr", "L32", "sq", "m", "d"],
               {"pos": "PAD_CELL", "zero": "ROW_END"}),

    # ══ the round loop ════════════════════════════════════════════════════════
    # FILE at run time is `[K, HALT, BI, AI, DIR, POS, CUR]`; `CUR` is the store
    # ring's head word index, which is what turns a random read into a short
    # relative rotation.
    "SETUP_DONE": ([
        "rq", "rq", "rq", "rq",              # drop W WPAD WALL ACC
        "rq", "M", "L256", "-",              # A = 256 - ADDR
        "M", "L8", "W", "/",                 # A = the pad words still owed
        "b",
        "rq", "M",                           # POS -> B
        "L0", "sq", "sq", "sq", "sq",        # K HALT BI AI
        "L1", "sq",                          # DIR = east
        "W", "sq",                           # POS
        "L0", "sq",                          # CUR = 0
    ], "PADW"),
    "PADW": (["d"], {"pos": "PADW_STEP", "zero": "PADW_END"}),
    "PADW_STEP": (["L0", "sr", "m", "d"], {"pos": "PADW_STEP", "zero": "PADW_END"}),
    #: The cached word leaves the ring for good: from here the store holds 31
    #: words with its head one past ``CUR``, and the hole is where the cache is.
    "PADW_END": (["rr", "sq"], "ROUND"),

    "ROUND": ([
        "ri", "M", "L2", "*", "sp",          # n = 2k pairs this frame
        "rq", "W", "sq",                     # K = k
        *_rot(7),
    ], "TICK"),
    "TICK": (["rq", "X"], {"zero": "ROUND_END", "pos": "TICK_GO", "neg": "ROUND_END"}),
    "ROUND_END": (["sq", *_rot(7)], "ROUND"),
    "TICK_GO": ([
        "M", "L1", "W", "-", "sq",           # K - 1
        "rq", "sq", "X",                     # HALT
    ], {"zero": "TICK_LIVE", "pos": "TICK_PAD", "neg": "TICK_PAD"}),

    # a halted man: two idempotent writes of his own cell, so the frame is the
    # promised 2k pairs long and nothing moves.
    "TICK_PAD": ([
        *_rot(3),                            # BI AI DIR
        "rq", "M", "sp", "L9", "sp", "W", "sp", "W", "sp", "W", "sq",
        *_rot(2),                            # CUR WORD
    ], "TICK"),

    # ══ the store read ════════════════════════════════════════════════════════
    # `POS / 8` is one glyph and yields both halves: the quotient is the word
    # the man's cell lives in, and `CUR` names the word the file is *holding*.
    # A read has to take its word out of the ring, and putting it straight back
    # would advance the head — which makes the commonest step of all, "the same
    # word again", cost a whole lap.  So the word stays in the file and the ring
    # keeps a hole where it came from: half the interpreted ticks then touch the
    # store not at all, and a miss rotates `(j - CUR - 1) mod 32`, which is 0 or
    # 1 whenever the man moved east or south.  Measured over the public cases
    # that is 6.6 word-moves an interpreted tick against a 257-word lap.
    "TICK_LIVE": ([
        *_rot(3),                            # BI AI DIR
        "rq", "sq",                          # POS, back in place, live in A
        "M", "L8", "W", "/",                 # A = word index j,  B = POS % 8
        "M", "sq",                           # CUR' = j
        "rq", "-", "X",                      # A = CUR - j
    ], {"zero": "HIT", "pos": "MISS", "neg": "MISS"}),
    "HIT": ([*_rot(1)], "EXTRACT"),          # the held word is already the one
    "MISS": (["N", "M", "L1", "W", "-", "X"],
             {"neg": "MISS_N", "zero": "MISS_P", "pos": "MISS_P"}),
    "MISS_P": (["b", "rq", "sr", "d"], {"pos": "ROT_STEP", "zero": "READW"}),
    "MISS_N": (["M", "L32", "+", "b", "rq", "sr", "d"],
               {"pos": "ROT_STEP", "zero": "READW"}),
    "ROT_STEP": (["rr", "sr", "m", "d"], {"pos": "ROT_STEP", "zero": "READW"}),
    "READW": (["rr", "sq"], "EXTRACT"),      # the new word leaves its hole
    "EXTRACT": ([
        *_rot(5),                            # K HALT BI AI DIR
        "rq", "sq",                          # POS, live in A
        "M", "L8", "W", "%",                 # A = r
        "M", "L5", "*",                      # A = 5r
        "M", "L35", "-",                     # A = 5*(7 - r), the byte's shift
        "M",                                 # ... which rides B over the file
        *_rot(1),                            # CUR
        "rq", "sq", "}",                     # A = WORD >> shift
        "M", "L31", "W", "&",                # A = the stored class
    ], "DISPATCH"),

    # ══ dispatch ══════════════════════════════════════════════════════════════
    "DISPATCH": (["M", f"L{DIGIT_BIAS}", "W", "-", "X"],
                 {"neg": "J_TREE", "zero": "L_DIGIT", "pos": "L_DIGIT"}),
    # non-digit: A = j - 12, B = 12  ->  j
    "J_TREE": (["+", "M", "L5", "W", "-", "X"],
               {"neg": "J_LOW", "zero": "L_HALT", "pos": "J_HIGH"}),
    "J_LOW": (["+", "X"], {"zero": "L_SPACE", "pos": "J_1234", "neg": "J_1234"}),
    "J_1234": (["b", "x"], {"one": "J_13", "zero": "J_24"}),
    "J_13": (["]", "x"], {"one": "L_SUB", "zero": "L_M"}),
    "J_24": (["]", "x"], {"one": "L_ADD", "zero": "L_X"}),
    "J_HIGH": (["M", "L5", "W", "-", "X"],
               {"neg": "J_DIR", "zero": "L_HALT", "pos": "L_SPACE"}),
    "J_DIR": (["M", "L4", "+", "M"], "L_DIR"),   # B = j - 6 = the heading index

    # ══ lanes ═════════════════════════════════════════════════════════════════
    # Each rotates FILE up to POS and leaves MOVE's contract: A = POS,
    # B = the colour to restore, BP = DIR.  `CUR` sits untouched at the tail.
    "L_DIGIT": ([
        "M",                                  # B = d
        *_rot(3),                             # K HALT BI
        "rq", "W", "sq",                      # AI = d
        "rq", "b", "sq",
        "rq", "M", "L8", "W",
    ], "MOVE"),
    "L_SPACE": ([
        *_rot(4),
        "rq", "b", "sq",
        "rq", "M", "L0", "W",
    ], "MOVE"),
    "L_M": ([
        *_rot(2),
        "rq", "rq", "sq", "sq",               # BI = AI, AI unchanged
        "rq", "b", "sq",
        "rq", "M", "L12", "W",
    ], "MOVE"),
    "L_ADD": ([
        *_rot(2),
        "rq", "M", "sq", "rq", "+", "sq",     # AI += BI
        "rq", "b", "sq",
        "rq", "M", "L10", "W",
    ], "MOVE"),
    "L_SUB": ([
        *_rot(2),
        "rq", "M", "sq", "rq", "-", "sq",     # AI -= BI
        "rq", "b", "sq",
        "rq", "M", "L10", "W",
    ], "MOVE"),
    "L_DIR": ([
        *_rot(4),
        "rq", "W", "b", "sq",                 # DIR = j - 6
        "rq", "M", "L3", "W",
    ], "MOVE"),
    "L_X": ([
        *_rot(3),
        "rq", "sq", "X",                      # sign of AI
    ], {"zero": "LX_0", "pos": "LX_P", "neg": "LX_N"}),
    "LX_0": (["rq", "b", "sq", "rq", "M", "L3", "W"], "MOVE"),
    "LX_P": ([
        "rq", "M", "L1", "+", "M", "L3", "W", "&", "b", "sq",
        "rq", "M", "L3", "W",
    ], "MOVE"),
    "LX_N": ([
        "rq", "M", "L1", "N", "+", "M", "L3", "W", "&", "b", "sq",
        "rq", "M", "L3", "W",
    ], "MOVE"),
    # `H` and a wall are the same lane: the man never leaves either cell.
    "L_HALT": ([
        *_rot(1),                             # K
        "rq", "L1", "sq",                     # HALT = 1
        *_rot(3),                             # BI AI DIR
        "rq", "M", "sp", "L9", "sp", "W", "sp", "W", "sp", "W", "sq",
        *_rot(2),                             # CUR WORD
    ], "TICK"),

    # ══ MOVE ══════════════════════════════════════════════════════════════════
    "MOVE": (["sp", "W", "sp", "W", "x"], {"one": "MV_H", "zero": "MV_V"}),
    "MV_H": (["]", "x"], {"one": "MV_W", "zero": "MV_E"}),
    "MV_V": (["]", "x"], {"one": "MV_S", "zero": "MV_N"}),
    "MV_E": (["M", "L1", "+"], "MV_END"),
    "MV_W": (["M", "L1", "N", "+"], "MV_END"),
    "MV_S": (["M", "L16", "+"], "MV_END"),
    "MV_N": (["M", "L16", "N", "+"], "MV_END"),
    "MV_END": (["M", "sp", "L9", "sp", "W", "sq", *_rot(2)], "TICK"),
}


# ═══════════════════════════════════════════════════════════════ the simulator ═
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


def simulate_worker(
    rounds: list[dict[str, Any]], *, max_steps: int = 40_000_000
) -> tuple[list[list[str]], int]:
    """Run :data:`WORKER` over one test case; return its frames and a token count.

    An op-level model — A, B, BP, the two pipe loops as deques, the input as a
    queue and the LM-75 as a next buffer with ``SWAP 1`` semantics.  Running dry
    on the input is how a case ends in the real machine, so it ends the
    simulation rather than failing.
    """
    inp: deque[int] = deque(int(v) for r in rounds for v in r["in"])
    store: deque[int] = deque()
    regs: deque[int] = deque()
    a = b = bp = 0
    frames: list[list[str]] = []
    nxt = [[0] * PANEL_W for _ in range(PANEL_H)]
    pend: list[int] = []
    pairs_left = 0
    steps = 0

    def paint(v: int) -> None:
        nonlocal pairs_left
        pend.append(v)
        if len(pend) == 2:
            addr, colour = pend
            nxt[addr // PANEL_W][addr % PANEL_W] = colour
            pend.clear()
            pairs_left -= 1
            if pairs_left == 0:  # the painter commits the delta with SWAP 1
                frames.append(["".join(f"{p:x}" for p in row) for row in nxt])

    block = "INIT"
    while True:
        toks, succ = WORKER[block]
        branch: str | None = None
        for t in toks:
            steps += 1
            if steps > max_steps:  # pragma: no cover - a runaway guard
                raise RuntimeError(f"worker did not settle (in {block})")
            if t.startswith("L"):
                a = int(t[1:])
            elif t == "ri":
                if not inp:
                    return frames, steps
                a = inp.popleft()
            elif t == "rr":
                a = store.popleft()
            elif t == "sr":
                store.append(a)
            elif t == "rq":
                a = regs.popleft()
            elif t == "sq":
                regs.append(a)
            elif t == "sp":
                if pairs_left == 0 and not pend:
                    pairs_left = a  # the frame header
                else:
                    paint(a)
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
            elif t == "X":
                branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
            elif t == "x":
                branch = "one" if bp & 1 else "zero"
            elif t == "d":
                branch = "pos" if bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
        block = succ if isinstance(succ, str) else succ[branch]


def worker_glyph_cells() -> int:
    """Grid cells the program's glyphs need, counting literals as written."""
    total = 0
    for toks, _ in WORKER.values():
        for t in toks:
            total += 1 if not t.startswith("L") or int(t[1:]) <= 9 else len(t[1:]) + 2
    return total


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    print(f"{len(WORKER)} blocks, {worker_glyph_cells()} glyph cells")
