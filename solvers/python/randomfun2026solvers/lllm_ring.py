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

* ``STORE`` — ``[v_0, ..., v_{16H-1}, END = -1]``, one class per display cell of
  the program's rows, padded to sixteen a row so a cell's store index **is** its
  display address.  Classes are 0..21, so ``END`` is the ring's only negative
  value and the scan that puts the store back finds its end with a bare ``X``.
* ``FILE`` — six slots in cyclic order ``[K, HALT, BI, AI, DIR, POS]``.  Reading
  slot *i* means rotating *i* words, so every block below touches the slots in
  exactly that order and no other.  ``K`` is the round's remaining ticks.

A tick is one rotation of ``FILE`` and one lap of ``STORE``: rotate ``POS`` words
under a ``b``-counted loop, read the class, push it straight back, and let a
sentinel loop return the rest while the class rides in ``B`` — only ``A`` is
clobbered by ``r``.

**The store value is not the class, it is the class biased so that no third
register is ever needed.**  Non-digits store ``j = class - 10`` in 0..11 and
digits store ``d + 12`` in 12..21; the two ranges are disjoint and both positive,
so one sign test on ``v - 12`` splits them.  The point is the decoder: the class
must be pushed to ``STORE`` *before* the colour is looked up, because the colour
table is indexed by ``j`` and the mask (`&` wants ``B = 15``) destroys the shift
amount.  ``s`` leaves ``A`` alone, so ``sr`` then the colour lookup costs nothing
— whereas computing both first would need a spill.

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

#: Longest store the machine must hold: sixteen cells a row, sixteen rows, END.
STORE_WORDS = PANEL_W * PANEL_H + 1
#: Register-file slots, plus one so a full rotation can never block.
FILE_WORDS = 7

#: Store bias for digits — `v = d + 12` keeps digits clear of the twelve
#: non-digit classes without going negative and breaking the END sentinel.
DIGIT_BIAS = 12


def store_words(height: int) -> int:
    """Words resident in ``STORE`` for a program of `height` rows."""
    return PANEL_W * height + 1


# ═════════════════════════════════════════════════════════════════ the program ═
#
# Tokens: `L<n>` literal, `ri` read input, `rr`/`sr` the store ring, `rq`/`sq`
# the register file, `sp` send painter, and the plain glyphs.  A block ending in
# `X` names three lanes (`neg`/`zero`/`pos`); one ending in `x` names two
# (`one`/`zero`, off the backpack's low bit); one ending in `d` names two
# (`pos`/`zero`), which is the counted-loop test.
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ INIT ══════════════════════════════════════════════════════════════════
    # FILE during setup is `[ADDR, POS, ROWS, W, WPAD, WALL]` — the display
    # address being painted, the man's address once found, rows left, the row's
    # real width, its padding to sixteen, and whether this row is room wall.
    # ADDR doubles as the store index, which is the whole point of padding rows
    # to sixteen.
    "INIT": ([
        "L0", "sq", "sq",                    # ADDR = 0, POS = 0
        "ri", "M", "ri", "sq",               # B = W, A = H;  ROWS = H
        "W", "sq",                           # W
        "M", "L16", "-", "sq",               # WPAD = 16 - W
        "L1", "sq",                          # row 0 is the room's top wall
        # one rotation to reach ROWS and tell the painter the frame size
        "rq", "sq", "rq", "sq",
        "rq", "M", "sq", "L4", "W", "{", "sp",  # n = 16*H pairs
        "rq", "sq", "rq", "sq", "rq", "sq",
    ], "ROW"),

    # ══ ROW ═══════════════════════════════════════════════════════════════════
    # Rows 0 and H-1 are entirely room wall, and in every other row `|` is the
    # only wall glyph — so `+` and `-` inside are unambiguously arithmetic and
    # the decoder needs no positional test at all.  The wall flag is set for
    # row 0 by INIT and re-armed by ROW_END when one row is left, so the
    # distinction costs one file slot and no per-cell work.
    "ROW": (["rq", "sq", "rq", "sq", "rq", "X"],
            {"zero": "SETUP_DONE", "pos": "ROW_GO", "neg": "SETUP_DONE"}),
    "ROW_GO": ([
        "M", "L1", "W", "-", "sq",           # ROWS - 1
        "rq", "b", "sq",                     # BP = W
        "rq", "sq", "rq", "sq",              # WPAD, WALL
    ], "REAL_LOOP"),
    "ROW_END": (["rq", "sq", "rq", "sq", "rq", "sq", "M", "L1", "W", "-", "X"],
                {"zero": "RE_WALL", "pos": "RE_NORM", "neg": "RE_NORM"}),
    "RE_WALL": (["rq", "sq", "rq", "sq", "rq", "L1", "sq"], "ROW"),
    "RE_NORM": (["rq", "sq", "rq", "sq", "rq", "L0", "sq"], "ROW"),

    "REAL_LOOP": (["d"], {"pos": "CELL", "zero": "PAD_SET"}),
    "PAD_SET": ([
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",   # ADDR POS ROWS W
        "rq", "b", "sq",                                  # BP = WPAD
        "rq", "sq",                                       # WALL
    ], "PAD_LOOP"),
    "PAD_LOOP": (["d"], {"pos": "PAD_CELL", "zero": "ROW_END"}),

    # ── one program cell: address out, byte in, colour out, class stored ──────
    "CELL": ([
        "rq", "sp", "M", "L1", "+", "sq",    # paint at ADDR, store ADDR + 1
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",   # POS ROWS W WPAD
        "rq", "sq", "X",                                  # WALL
    ], {"pos": "WALL_CELL", "zero": "REAL_CELL", "neg": "REAL_CELL"}),
    "WALL_CELL": (["ri", "L4", "sp", "L10", "sr", "m"], "REAL_LOOP"),
    "REAL_CELL": (["ri"], "DEC"),
    "PAD_CELL": ([
        "rq", "sp", "M", "L1", "+", "sq",
        "L0", "sp",                          # padding is black
        "L0", "sr",                          # ... and an unreachable class
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",
        "m",
    ], "PAD_LOOP"),

    # ── the decoder ──────────────────────────────────────────────────────────
    "DEC": (["M", "L48", "W", "-", "X"],
            {"neg": "DEC_ASC", "zero": "DEC_D0", "pos": "DEC_HI"}),
    "DEC_ASC": (["+"], "DEC_TAB"),
    "DEC_D0": ([f"L{DIGIT_BIAS}", "sr", "L8", "sp"], "CELL_TAIL"),
    "DEC_HI": (["M", "L9", "W", "-", "X"],
               {"neg": "DEC_DIG", "zero": "DEC_DIG", "pos": "DEC_ASC2"}),
    "DEC_DIG": (["+", "M", f"L{DIGIT_BIAS}", "+", "sr", "L8", "sp"], "CELL_TAIL"),
    "DEC_ASC2": (["+", "M", "L48", "+"], "DEC_TAB"),
    "DEC_TAB": ([
        "M", f"L{HASH_MUL}", "*",            # A = 29*c              B = c
        "M", f"L{HASH_SHIFT}", "W", "}",     # A = (29*c) >> 6
        "M", "L15", "&",                     # A = i
        "M", "L4", "*", "W",                 # A = i                 B = 4i
        f"L{CLASS_MAGIC}", "}",              # A = CLASS_MAGIC >> 4i
        "M", "L15", "&",                     # A = j
        "sr",                                # store it *before* the second read
        "M", "L4", "*", "W",                 # A = j                 B = 4j
        f"L{COLOUR_MAGIC}", "}",
        "M", "L15", "&",                     # A = colour
        "sp",
    ], "CELL_TAIL"),

    # ── close the cell ───────────────────────────────────────────────────────
    # The spawn is found by its *colour*: 9 is the man, and no glyph paints 9,
    # so `colour == 9` is `@` and costs one sign test instead of a second decode
    # branch.  The fix-up runs once per program and may take a whole extra
    # rotation of the file to reach ADDR again.
    "CELL_TAIL": (["M", "L9", "W", "-", "X"],
                  {"zero": "AT_FIX", "pos": "CELL_ROT", "neg": "CELL_ROT"}),
    "CELL_ROT": (["m"], "REAL_LOOP"),
    "AT_FIX": ([
        "rq", "sq", "M", "L1", "W", "-", "M",  # B = ADDR, the cell just painted
        "rq", "W", "sq",                       # POS = ADDR
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",   # back to canonical
        "m",
    ], "REAL_LOOP"),

    # ══ the round loop ════════════════════════════════════════════════════════
    # FILE at run time is `[K, HALT, BI, AI, DIR, POS]`.
    "SETUP_DONE": ([
        "L1", "N", "sr",                     # END
        "rq", "rq", "rq", "rq", "rq", "M",   # drop W WPAD WALL ADDR, POS -> B
        "L0", "sq", "sq", "sq", "sq",        # K HALT BI AI
        "L1", "sq",                          # DIR = east
        "W", "sq",                           # POS
    ], "ROUND"),
    "ROUND": ([
        "ri", "M", "L2", "*", "sp",          # n = 2k pairs this frame
        "rq", "W", "sq",                     # K = k
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",
    ], "TICK"),
    "TICK": (["rq", "X"], {"zero": "ROUND_END", "pos": "TICK_GO", "neg": "ROUND_END"}),
    "ROUND_END": ([
        "sq", "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",
    ], "ROUND"),
    "TICK_GO": ([
        "M", "L1", "W", "-", "sq",           # K - 1
        "rq", "sq", "X",                     # HALT
    ], {"zero": "TICK_LIVE", "pos": "TICK_PAD", "neg": "TICK_PAD"}),

    # a halted man: two idempotent writes of his own cell, so the frame is the
    # promised 2k pairs long and nothing moves.
    "TICK_PAD": ([
        "rq", "sq", "rq", "sq", "rq", "sq",  # BI AI DIR
        "rq", "M", "sp", "L9", "sp", "W", "sp", "W", "sp", "W", "sq",
    ], "TICK"),

    "TICK_LIVE": ([
        "rq", "sq", "rq", "sq", "rq", "sq",  # BI AI DIR
        "rq", "sq",                          # POS, back in place, live in A
        "b",                                 # BP = POS
    ], "SEEK"),
    "SEEK": (["d"], {"pos": "SEEK_STEP", "zero": "READ"}),
    "SEEK_STEP": (["rr", "sr", "m"], "SEEK"),
    "READ": (["rr", "sr", "M"], "REST"),     # class rides in B over the scan
    "REST": (["rr", "X"], {"neg": "REST_END", "zero": "REST_PUSH", "pos": "REST_PUSH"}),
    "REST_PUSH": (["sr"], "REST"),
    "REST_END": (["sr", "W"], "DISPATCH"),

    # ══ dispatch ══════════════════════════════════════════════════════════════
    "DISPATCH": ([f"M", f"L{DIGIT_BIAS}", "W", "-", "X"],
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
    # Each rotates FILE once and leaves MOVE's contract: A = POS, B = colour,
    # BP = DIR.
    "L_DIGIT": ([
        "M",                                  # B = d
        "rq", "sq", "rq", "sq", "rq", "sq",   # K HALT BI
        "rq", "W", "sq",                      # AI = d
        "rq", "b", "sq",
        "rq", "M", "L8", "W",
    ], "MOVE"),
    "L_SPACE": ([
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",
        "rq", "b", "sq",
        "rq", "M", "L0", "W",
    ], "MOVE"),
    "L_M": ([
        "rq", "sq", "rq", "sq",
        "rq", "rq", "sq", "sq",               # BI = AI, AI unchanged
        "rq", "b", "sq",
        "rq", "M", "L12", "W",
    ], "MOVE"),
    "L_ADD": ([
        "rq", "sq", "rq", "sq",
        "rq", "M", "sq", "rq", "+", "sq",     # AI += BI
        "rq", "b", "sq",
        "rq", "M", "L10", "W",
    ], "MOVE"),
    "L_SUB": ([
        "rq", "sq", "rq", "sq",
        "rq", "M", "sq", "rq", "-", "sq",     # AI -= BI
        "rq", "b", "sq",
        "rq", "M", "L10", "W",
    ], "MOVE"),
    "L_DIR": ([
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",
        "rq", "W", "b", "sq",                 # DIR = j - 6
        "rq", "M", "L3", "W",
    ], "MOVE"),
    "L_X": ([
        "rq", "sq", "rq", "sq", "rq", "sq",
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
    # `H` and a wall are the same lane: the man never leaves either cell, so the
    # colour underneath is never repainted and only the setup frame ever showed
    # it.  Two idempotent writes keep the frame 2k pairs long.
    "L_HALT": ([
        "rq", "sq",                           # K
        "rq", "L1", "sq",                     # HALT = 1
        "rq", "sq", "rq", "sq", "rq", "sq",   # BI AI DIR
        "rq", "M", "sp", "L9", "sp", "W", "sp", "W", "sp", "W", "sq",
    ], "TICK"),

    # ══ MOVE ══════════════════════════════════════════════════════════════════
    "MOVE": (["sp", "W", "sp", "W", "x"], {"one": "MV_H", "zero": "MV_V"}),
    "MV_H": (["]", "x"], {"one": "MV_W", "zero": "MV_E"}),
    "MV_V": (["]", "x"], {"one": "MV_S", "zero": "MV_N"}),
    "MV_E": (["M", "L1", "+"], "MV_END"),
    "MV_W": (["M", "L1", "N", "+"], "MV_END"),
    "MV_S": (["M", "L16", "+"], "MV_END"),
    "MV_N": (["M", "L16", "N", "+"], "MV_END"),
    "MV_END": (["M", "sp", "L9", "sp", "W", "sq"], "TICK"),
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
