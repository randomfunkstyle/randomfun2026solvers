#!/usr/bin/env python3
"""`little-little-man` as an LM-1 program — the LLM language, interpreted by a CPU.

The problem hands us an LLM program (up to three rooms, up to two pipes, one man
per room, ``4 <= W, H <= 16``) as ASCII in round 1 and then one ``k`` per round,
and wants one 16x16 frame per round.  So the deliverable is an *interpreter*, and
this module writes it as :mod:`randomfun2026solvers.lm1` assembly rather than as a
hand-drawn grid: the accumulator ISA, its emulator and its machine generator are
engine-proven on the display problems, and control flow — which is most of an
interpreter — is what a CPU is for.  A bespoke dataflow ring like
:mod:`lllm_ring` pays for control flow in *rows* (one block to a row band, so
~3.3 rows a block), and this program needs three to four times LLLM's block
count; the CPU pays for it in ROM words, which are dense data.

The emitter is Python because every table here (the class numbering, the palette,
the direction deltas) has to agree with a model of the same semantics, and
because ``lm1/asm.py`` has no macros: an interpreter is not a hand-written
``.asm`` file.  :mod:`randomfun2026solvers.llm_sim` is the reference semantics
this program is validated against.

## What the machine knows about a cell

One store slot per display cell, indexed by the **display address** ``a = 16y +
x`` — so a move is ``+-1`` / ``+-16`` and the same number addresses the panel.
Each slot holds a *class*:

| class | glyph | colour |
|---|---|---|
| 0..9 | digit | 8 |
| 10 | space, `.`, `@`, anything unknown | 0 |
| 11 | `M` | 12 |
| 12 | `+` as arithmetic | 10 |
| 13 | `-` as arithmetic | 10 |
| 14 | `X` | 3 |
| 15 | `H` | 3 |
| 16..19 | heading `^` `>` `v` `<` (``class - 16`` **is** the direction) | 3 |
| 20 | room wall | 4 |
| 21 | `s` | 13 |
| 22 | `r` | 13 |
| 23 | pipe body (`-`, `|`) | 6 |
| 24..27 | pipe arrowhead `^` `>` `v` `<` (``class - 24`` is its flow direction) | 6 |

The two arrow ranges are the whole disambiguation problem: `v` inside a room is a
heading and outside one is a pipe cell; `-` is arithmetic, wall or pipe body; `|`
is wall or pipe body.  Every one of those is decided by **position**, so setup
has to find the rooms before it can finish classifying a cell.

## Finding the rooms without a flood fill

A room's horizontal walls are the only place a `+` … `-`* … `+` run can occur:
pipe bodies are bounded by arrowheads, never by `+`.  So the streaming pass that
reads the program collects those runs, and runs with the same ``(x0, x1)`` **pair
up in reading order** — the first is a room's top wall, the next its bottom.
Stacked rooms of equal width (public case `bounce house`) pair correctly because
runs arrive in increasing ``y``, and a pair is only accepted once its left and
right columns have been checked to be `|` all the way down, which is what stops
an in-room `+--+` written out of arithmetic glyphs from faking a room.

Verified against all fourteen public programs: 1..3 rooms each, every `@` inside
exactly one, every ambiguous glyph outside every room rectangle covered by a pipe
walk, and no stray glyph outside a room.

Once the rectangles are known the perimeters are **stamped** as class 20 — a
store write is nearly free on this machine and a read is not — so the sweep that
paints the first frame only runs the rectangle test on cells that are still
ambiguous, a couple of dozen per program rather than all 256.

## Pipes as an occupancy array in flow order

A pipe walk starts at the arrowhead whose *backward* neighbour is a wall and
follows the flow, arrowheads resetting the heading, until the forward neighbour is
a wall.  It records cells in flow order: index 0 is the source cell an `s` writes
and index ``len - 1`` the destination cell an `r` pops.  ``OCC[i]`` is 0 for empty
or ``value + 10``.

Transport shifts as a **train**: :mod:`llm_sim` pinned this on the reference wasm
(a full pipe's values all advance on the tick after the receiver pops), so the
shift walks the cells from the destination end downwards, moving a value whenever
the cell ahead is free *after that cell has already moved*.  One high-to-low pass
is exactly that rule, and it only covers the occupied window ``LO..HI`` — kept as
a conservative superset, re-tightened by every pass.

## The tick

1. ``STOP`` — set when a man's move landed him on a wall on the previous tick, or
   when the last live man reached an `H` — ends everything, pipes included.
2. Shift both pipes.
3. Each live man executes the class he is standing on and then moves, unless he
   halted on `H` or blocked on `s`/`r`.

A move reads the class of the cell it lands on, keeps it in ``MCLS`` for the next
tick's dispatch, and tests it for a wall — so the freeze needs no separate pass
and the dispatch needs no read.

## Frames are deltas, painted as they happen

``SWAP 1`` preserves both buffers, so a frame is a delta and nothing has to be
repainted at commit time.  A man's move paints two pixels (the vacated cell back
to the colour of the class under it — which is in hand, since the tick read it —
and the new cell to 9); a value's move paints two (its old cell back to 6, its new
cell to 14).  Intermediate ticks paint into the same buffer, so the frame the
round commits is the state after the last tick, and no per-round repaint sweep and
no "what did I paint last time" bookkeeping exists at all.

## Costs this program is shaped by (all measured on the engine)

* a store read costs ``107 + 7.81 * N`` ticks, ``N`` = tape slots, so **slot count
  is a speed decision**, and the 256 cells are packed 8 to a word when
  ``packed_cells`` is set;
* a store write is ~free (fire and forget), so setup stamps rather than tests;
* an instruction issues in ~100 ticks at decode depth 4, ~143 at depth 5;
* a **taken backward branch recirculates the rest of the ROM at 12 ticks a word**,
  which is why the men and the pipes are unrolled rather than looped: three men
  and two pipes are five loop closures a tick, each costing ``12 * (P - body)``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.llm_asm import Asm

__all__ = ["CLASS_OF_BYTE", "COLOUR_OF_CLASS", "build_asm", "MAX_MEN", "MAX_PIPE_CELLS"]

# ── the class numbering, and the tables derived from it ───────────────────────
CLS_SPACE = 10
CLS_M = 11
CLS_ADD = 12
CLS_SUB = 13
CLS_X = 14
CLS_H = 15
CLS_DIR = 16  # + direction 0..3 (N E S W)
CLS_WALL = 20
CLS_S = 21
CLS_R = 22
CLS_PIPE = 23
CLS_ARROW = 24  # + flow direction 0..3

#: The class of a byte *as if it were inside a room*.  Cells outside one are fixed
#: up by the sweep that knows the rectangles.
CLASS_OF_BYTE: dict[int, int] = {
    32: CLS_SPACE,  # space
    46: CLS_SPACE,  # '.'
    64: CLS_SPACE,  # '@' — the spawn cell is ordinary empty space
    77: CLS_M,
    43: CLS_ADD,
    45: CLS_SUB,
    88: CLS_X,
    72: CLS_H,
    94: CLS_DIR + 0,  # '^'
    62: CLS_DIR + 1,  # '>'
    118: CLS_DIR + 2,  # 'v'
    86: CLS_DIR + 2,  # 'V' — a heading, but never a pipe arrowhead
    60: CLS_DIR + 3,  # '<'
    115: CLS_S,
    114: CLS_R,
    124: CLS_PIPE,  # '|' is never an operation: wall or pipe body only
    **{48 + d: d for d in range(10)},
}

COLOUR_OF_CLASS: dict[int, int] = {
    **{d: 8 for d in range(10)},
    CLS_SPACE: 0,
    CLS_M: 12,
    CLS_ADD: 10,
    CLS_SUB: 10,
    CLS_X: 3,
    CLS_H: 3,
    CLS_DIR + 0: 3,
    CLS_DIR + 1: 3,
    CLS_DIR + 2: 3,
    CLS_DIR + 3: 3,
    CLS_WALL: 4,
    CLS_S: 13,
    CLS_R: 13,
    CLS_PIPE: 6,
    **{CLS_ARROW + d: 6 for d in range(4)},
}

#: A cell slot holds ``colour * 32 + class``.  The colour is wanted on every
#: repaint and the class on every dispatch, and one read gives both — which is
#: what deletes the eight-branch colour chain from all four of its call sites.
CELL_SHIFT = 32

#: Cells a store word holds when ``packed_cells`` is set.  A cell is
#: ``colour * 32 + class`` <= 479, so four 16-bit fields fill a 64-bit word — and
#: four is the number that keeps the address arithmetic free: ``+-16`` moves the
#: word index by four and leaves the field alone, ``+-1`` walks the fields.
CELLS_PER_WORD = 4
CELL_FIELD = 1 << 16


def enc(cls: int) -> int:
    """The stored word for a class: its colour above, the class below."""
    return COLOUR_OF_CLASS[cls] * CELL_SHIFT + cls


COLOUR_MAN = 9
COLOUR_VALUE = 14
COLOUR_PIPE = 6
VALUE_BIAS = 10  # OCC holds value + 10, so 0 means "empty"

MAX_ROOMS = 3
MAX_MEN = 3
MAX_PIPES = 2
MAX_PIPE_CELLS = 20
MAX_RUNS = 8
PANEL = 16


# ── the store map ─────────────────────────────────────────────────────────────
def _declare(a: Asm, *, packed_cells: bool) -> None:
    """Allocate every slot.  Order is deliberate: hot scalars first is free here
    (a tape read costs the same at any address), so the grouping is by phase."""
    a.slot("W", "program width", hot=True)
    a.slot("H", "program height", hot=True)
    a.slot("NMEN", "men found, 1..3", hot=True)
    a.slot("NP", "pipes found, 0..2", hot=True)
    a.slot("NROOM", "rooms found, 1..3", hot=True)
    a.slot("NRUN", "candidate horizontal wall runs", hot=True)
    a.slot("NSRC", "pipe source arrowheads found", hot=True)
    a.slot("STOP", "a wall freeze or the last H: nothing runs after this", hot=True)
    a.slot("NHALT", "men halted on an H")
    a.slot("K", "interpreted ticks left this round", hot=True)
    a.slot("CA", "cell cursor: display address", hot=True)
    a.slot("CX", "cell cursor: column", hot=True)
    a.slot("CY", "cell cursor: row", hot=True)
    a.slot("BY", "the input byte", hot=True)
    a.slot("CLS", "the class being computed / dispatched", hot=True)
    a.slot("COL", "the colour being painted")
    a.slot("PLUSX", "column of the last '+' in this row, or -1", hot=True)
    a.slot("CLEAN", "every cell since that '+' was a '-'")
    a.slot("LIMIT", "16 * H — one past the last live display address", hot=True)
    a.slot("RET", "return site for the room_kind subroutine")
    a.slot("RKX", "room_kind argument: column", hot=True)
    a.slot("RKY", "room_kind argument: row", hot=True)
    a.slot("KIND", "room_kind result: 0 outside, 1 inside, 2 wall")
    a.slot("RIDX", "room_kind result: which room, or -1")
    a.slot("RI", "room_kind's loop cursor", hot=True)
    a.slot("T0", hot=True)
    a.slot("T1", hot=True)
    a.slot("T2", hot=True)
    a.slot("T3")
    a.slot("T4", hot=True)
    a.slot("JDX", "second array cursor, for the run pairing's inner loop")
    a.slot("PKEY", "the (x0, x1) key of the run being paired", hot=True)
    a.slot("PY0", "the row of the run being paired")
    a.slot("PX0", "unpacked x0 of a candidate room", hot=True)
    a.slot("PX1", "unpacked x1 of a candidate room")
    a.slot("FCLS", "the final class the pipe walk writes for the cell it is on")
    a.slot("CP0", "packed cell access scratch — never touched by callers")
    a.slot("CP1")
    a.slot("CP2")
    a.slot("C20", "the encoded wall class, for stamping with MOVA", hot=True)
    a.slot("CZ", "the constant 0, for clearing with MOVA", hot=True)
    a.slot("IDX", "generic array cursor", hot=True)
    a.slot("VAL", "generic value staged for an indexed write", hot=True)
    a.slot("NEWHI", "pipe shift: highest occupied cell after the pass", hot=True)
    a.slot("NEWLO", "pipe shift: lowest occupied cell after the pass")
    a.slot("BEST", "s/r: the pipe chosen, or -1", hot=True)
    a.slot("BESTD", "s/r: its Manhattan distance")
    a.slot("BESTA", "s/r: its arrowhead address, for the reading-order tie")
    a.slot("PICKM", "pick(): 0 = outgoing pipes for an s, 1 = incoming for an r")
    a.slot("PICKR", "pick(): the room the man is in")
    a.slot("PICKA", "pick(): the man's cell")
    a.slot("RET2", "return site for pick()", hot=True)

    a.array("RUN", MAX_RUNS, "packed wall run: (y*16 + x0)*16 + x1")
    a.array("RX0", MAX_ROOMS, "room rectangles, inclusive")
    a.array("RY0", MAX_ROOMS)
    a.array("RX1", MAX_ROOMS)
    a.array("RY1", MAX_ROOMS)
    a.array("SRCA", MAX_PIPES + 1, "pipe source arrowhead: display address")
    a.array("SRCD", MAX_PIPES + 1, "its flow direction")
    a.array("SRCR", MAX_PIPES + 1, "the room it leaves")

    a.array("MPOS", MAX_MEN, "man: display address", hot=True)
    a.array("MDIR", MAX_MEN, "man: heading 0..3", hot=True)
    a.array("MA", MAX_MEN, "man: register A")
    a.array("MB", MAX_MEN, "man: register B")
    a.array("MHALT", MAX_MEN, "man: halted on an H", hot=True)
    a.array("MROOM", MAX_MEN, "man: which room he lives in")
    a.array("MCLS", MAX_MEN, "man: the class under him, read when he moved", hot=True)
    a.array("MPIPE", MAX_MEN, "man: the pipe his s/r bound to, cached")
    a.array("MPIPEA", MAX_MEN, "man: the cell that cache is valid for, or -1")

    a.array("PLEN", MAX_PIPES, "pipe: cells, so index len-1 is the destination", hot=True)
    a.array("PBASE", MAX_PIPES, "pipe: its first slot in PCA/OCC", hot=True)
    a.array("PSRC", MAX_PIPES, "pipe: source arrowhead address")
    a.array("PDST", MAX_PIPES, "pipe: destination arrowhead address")
    a.array("PSROOM", MAX_PIPES, "pipe: the room it leaves")
    a.array("PDROOM", MAX_PIPES, "pipe: the room it enters")
    a.array("PLO", MAX_PIPES, "pipe: lowest maybe-occupied cell", hot=True)
    a.array("PHI", MAX_PIPES, "pipe: highest maybe-occupied cell")
    a.array("PCNT", MAX_PIPES, "pipe: values in flight", hot=True)

    a.array("DTAB", 4, "address delta per heading: -16, +1, +16, -1")
    a.array("PCA", MAX_PIPE_CELLS, "pipe cell display addresses, flow order")
    a.array("OCC", MAX_PIPE_CELLS, "0 empty, else value + 10")
    if packed_cells:
        a.array("POWTAB", CELLS_PER_WORD, "65536^k, to extract field k of a word")
        a.array(
            "CELL", PANEL * PANEL // CELLS_PER_WORD, "the program grid, four 16-bit cells to a word"
        )
    else:
        a.array("CELL", PANEL * PANEL, "the program grid, one class a cell")


# ── leaf helpers ──────────────────────────────────────────────────────────────
def _cell_read(a: Asm, addr: str, out: str, *, packed: bool) -> None:
    """``out = CELL[addr]``."""
    if not packed:
        a.ld(addr)
        a.op("ADDI", "CELL")
        a.op("LDA")
        a.st(out)
        return
    a.ld(addr, f"field {addr} % {CELLS_PER_WORD} of word {addr} / {CELLS_PER_WORD}")
    a.op("MODI", CELLS_PER_WORD)
    a.op("ADDI", "POWTAB")
    a.op("LDA")
    a.st("CP0")
    a.ld(addr)
    a.op("DIVI", CELLS_PER_WORD)
    a.op("ADDI", "CELL")
    a.op("LDA")
    a.op("DIV", "CP0")
    a.op("MODI", CELL_FIELD)
    a.st(out)


def _cell_write(a: Asm, addr: str, val: str, note: str = "", *, packed: bool) -> None:
    """``CELL[addr] = val``, for the stamping and fix-up passes."""
    if not packed:
        a.ld(addr, note)
        a.op("ADDI", "CELL")
        a.op("MOVA", val)
        return
    # read the word, subtract the byte that is there, add the new one
    a.ld(addr, "packed write: word += (new - old) * 65536^k")
    a.op("MODI", CELLS_PER_WORD)
    a.op("ADDI", "POWTAB")
    a.op("LDA")
    a.st("CP0")
    a.ld(addr)
    a.op("DIVI", CELLS_PER_WORD)
    a.op("ADDI", "CELL")
    a.st("CP1")
    a.op("LDA")
    a.st("CP2")
    a.op("DIV", "CP0")
    a.op("MODI", CELL_FIELD)
    a.op("MUL", "CP0")
    a.st("CP2")
    a.ld(val)
    a.op("MUL", "CP0")
    a.op("SUB", "CP2")
    a.st("CP2")
    a.ld("CP1")
    a.op("LDA")
    a.op("ADD", "CP2")
    a.st("CP2")
    a.ld("CP1")
    a.op("MOVA", "CP2")


def _paint(a: Asm, addr: str, colour: str | int) -> None:
    """One pixel: ADDR then DATA.  The panel is 16x16, so an address *is* a cell."""
    a.ld(addr)
    a.op("DSPA")
    if isinstance(colour, int):
        a.ldi(colour)
    else:
        a.ld(colour)
    a.op("DSPD")


def _emit_colour_of(a: Asm, cls: str, out: str) -> None:
    """``out = COLOUR_OF_CLASS[cls]``, inlined: it runs 256 times in setup and
    twice a man-move, so a call's two ROM laps would cost more than the words."""
    done = a.new_label("col_done")
    lanes: list[tuple[str, int]] = []

    def lane(colour: int) -> str:
        name = a.new_label("col")
        lanes.append((name, colour))
        return name

    l8, l0, l12, l10, l3, l4, l13, l6 = (lane(c) for c in (8, 0, 12, 10, 3, 4, 13, 6))
    a.ld(cls, "colour_of")
    a.op("SUBI", 10)
    a.brn(l8, "0..9 digits")
    a.brz(l0, "10 space")
    a.ld(cls)
    a.op("SUBI", 11)
    a.brz(l12, "11 M")
    a.ld(cls)
    a.op("SUBI", 14)
    a.brn(l10, "12,13 + -")
    a.ld(cls)
    a.op("SUBI", 20)
    a.brn(l3, "14..19 X H headings")
    a.brz(l4, "20 wall")
    a.ld(cls)
    a.op("SUBI", 23)
    a.brn(l13, "21,22 s r")
    a.jmp(l6, "23..27 pipe")
    for name, colour in lanes:
        a.label(name)
        a.set_slot(out, colour)
        a.jmp(done)
    a.label(done)


def _call_room_kind(a: Asm, site: int) -> None:
    """Set up the return site and jump to the shared rectangle test."""
    a.set_slot("RET", site, f"room_kind, returning to site {site}")
    a.jmp("room_kind")
    a.label(f"rk_ret{site}")


# ── setup, pass 1: stream the program in, detecting wall runs and men ──────────
def _emit_pass1(a: Asm, *, packed: bool) -> None:
    a.section("pass 1: W H, then W*H bytes -> CELL, wall runs, men")
    a.label("setup")
    a.op("IN", note="W")
    a.st("W")
    a.op("IN", note="H")
    a.st("H")
    a.zero(["NMEN", "NP", "NROOM", "NRUN", "NSRC", "STOP", "NHALT", "CZ", "CA", "CY"])
    a.set_slot("C20", enc(CLS_WALL))
    a.ld("H", "LIMIT = 16 * H: one past the last live display address")
    a.op("MULI", PANEL)
    a.st("LIMIT")
    for d, delta in enumerate((-PANEL, 1, PANEL, -1)):
        a.set_slot(a.at("DTAB", d), delta, f"DTAB[{d}]" if d == 0 else "")
    if packed:
        pow_ = 1
        for k in range(CELLS_PER_WORD):
            a.set_slot(a.at("POWTAB", k), pow_, "POWTAB = 65536^k" if k == 0 else "")
            pow_ *= CELL_FIELD
        a.zero(
            [a.at("CELL", w) for w in range(PANEL * PANEL // CELLS_PER_WORD)],
            "the packed grid starts empty",
        )

    a.ldi(0, "home the cursor: pass 1 paints in address order, so DATA advances it")
    a.op("DSPA")
    a.label("p1_row")
    a.set_slot("CX", 0)
    a.set_slot("PLUSX", -1, "no '+' seen in this row yet")
    a.set_slot("CLEAN", 0)

    a.label("p1_cell")
    a.br_lt("CX", "W", "p1_cell_go")
    a.jmp("p1_row_end")
    a.label("p1_cell_go")
    a.op("IN")
    a.st("BY")
    _emit_classify(a)

    a.label("p1_dirty")
    a.set_slot("CLEAN", 0, "any other glyph breaks a wall run")
    a.label("p1_keep")
    a.ld("CLS", "the glyph's own colour; walls and pipe cells are repainted later")
    a.op("DIVI", CELL_SHIFT)
    a.op("DSPD")
    _cell_write(a, "CA", "CLS", packed=packed)
    a.inc("CA")
    a.inc("CX")
    a.jmp("p1_cell")

    a.section("end of a row: the columns past W stay black, so only the cursor moves")
    a.label("p1_row_end")
    a.inc("CY")
    a.ld("CY", "the next row starts at 16y, whatever W is")
    a.op("MULI", PANEL)
    a.st("CA")
    a.ld("CY")
    a.op("SUB", "H")
    a.brn("p1_next_row")
    a.jmp("p1_done")
    a.label("p1_next_row")
    a.ld("CA")
    a.op("DSPA")
    a.jmp("p1_row")


def _emit_classify(a: Asm) -> None:
    """``BY`` -> ``CLS``, with the run detector and the man list as side effects.

    Falls through to ``p1_dirty`` (which clears ``CLEAN``) unless the glyph is a
    `+` or a `-`, the only two that can continue a horizontal wall run.
    """
    plain = {
        32: CLS_SPACE,
        46: CLS_SPACE,
        60: CLS_DIR + 3,
        62: CLS_DIR + 1,
        72: CLS_H,
        77: CLS_M,
        86: CLS_DIR + 2,
        88: CLS_X,
        94: CLS_DIR + 0,
        114: CLS_R,
        115: CLS_S,
        118: CLS_DIR + 2,
        124: CLS_PIPE,
    }
    lanes: dict[int, str] = {b: a.new_label("cl") for b in plain}
    l_plus, l_minus, l_digit, l_at, l_other = (
        a.new_label("cl_plus"),
        a.new_label("cl_minus"),
        a.new_label("cl_digit"),
        a.new_label("cl_at"),
        a.new_label("cl_other"),
    )

    a.ld("BY", "classify: one cumulative SUBI chain, commonest glyphs first")
    a.op("SUBI", 32)
    a.brz(lanes[32], "' '")
    a.op("SUBI", 11)
    a.brz(l_plus, "'+' 43")
    a.op("SUBI", 2)
    a.brz(l_minus, "'-' 45")
    a.op("SUBI", 1)
    a.brz(lanes[46], "'.' 46")
    a.op("SUBI", 2)
    a.brn(l_other, "below '0'")
    a.op("SUBI", 9)
    a.brn(l_digit, "'0'..'8'")
    a.brz(l_digit, "'9'")
    a.ld("BY")
    prev = 0
    for byte in (60, 62, 64, 72, 77, 86, 88, 94, 114, 115, 118, 124):
        a.op("SUBI", byte - prev)
        prev = byte
        a.brz(l_at if byte == 64 else lanes[byte], f"{chr(byte)!r} {byte}")
    a.jmp(l_other)

    for byte, name in lanes.items():
        a.label(name)
        a.set_slot("CLS", enc(plain[byte]))
        a.jmp("p1_dirty")
    a.label(l_other)
    a.set_slot("CLS", enc(CLS_SPACE))
    a.jmp("p1_dirty")
    a.label(l_digit)
    a.ld("BY", "digit: the class *is* the value, and every digit is colour 8")
    a.op("SUBI", 48)
    a.op("ADDI", COLOUR_OF_CLASS[0] * CELL_SHIFT)
    a.st("CLS")
    a.jmp("p1_dirty")

    a.label(l_at)
    a.set_slot("CLS", enc(CLS_SPACE), "'@': empty space, but a man spawns here")
    a.ld("NMEN")
    a.op("SUBI", MAX_MEN)
    a.brz("p1_dirty", "more men than the problem allows: ignore")
    a.store_at("MPOS", "NMEN", "CA")
    a.inc("NMEN")
    a.jmp("p1_dirty")

    a.label(
        l_minus,
    )
    a.set_slot("CLS", enc(CLS_SUB), "'-' continues a wall run")
    a.jmp("p1_keep")

    a.label(l_plus)
    a.set_slot("CLS", enc(CLS_ADD))
    skip = a.new_label("run_skip")
    a.ld("PLUSX", "a run needs a previous '+' …")
    a.brn(skip)
    a.ld("CLEAN", "… nothing but '-' since it …")
    a.brz(skip)
    a.ld("CX", "… and at least one cell between them")
    a.op("SUB", "PLUSX")
    a.op("SUBI", 2)
    a.brn(skip)
    a.ld("NRUN")
    a.op("SUBI", MAX_RUNS)
    a.brz(skip, "run table full")
    a.ld("CY", "RUN = (y * 16 + x0) * 16 + x1")
    a.op("MULI", PANEL)
    a.op("ADD", "PLUSX")
    a.op("MULI", PANEL)
    a.op("ADD", "CX")
    a.st("T0")
    a.store_at("RUN", "NRUN", "T0")
    a.inc("NRUN")
    a.label(skip)
    a.copy("PLUSX", "CX", "this '+' opens the next run")
    a.set_slot("CLEAN", 1)
    a.jmp("p1_keep")


def _emit_source_probe(a: Asm, wall: str, step: int, direction: int, *, packed: bool) -> None:
    """Is the cell one step outside this wall an arrowhead pointing *away*?

    Then it is a pipe's source, and the room being stamped is the room it leaves —
    which is why the probe rides along with the stamping loop instead of testing
    every arrowhead's backward neighbour in a sweep of its own.
    """
    skip, hit = a.new_label("sp_skip"), a.new_label("sp_hit")
    a.ld(wall, f"probe {step:+} for an arrowhead heading {direction}")
    a.op("ADDI" if step > 0 else "SUBI", abs(step))
    a.st("PKEY")
    a.brn(skip, "off the top of the grid")
    a.op("SUB", "LIMIT")
    a.brn(a.new_label("sp_in") if False else hit + "_in")
    a.jmp(skip, "off the bottom")
    a.label(hit + "_in")
    _cell_read(a, "PKEY", "CLS", packed=packed)
    a.ld("CLS")
    a.op("SUBI", enc(CLS_DIR + direction))
    a.brz(hit)
    a.jmp(skip)
    a.label(hit)
    a.ld("NSRC")
    a.op("SUBI", MAX_PIPES)
    a.brz(skip, "more pipes than the problem allows")
    a.store_at("SRCA", "NSRC", "PKEY")
    a.set_slot("PX0", direction)
    a.store_at("SRCD", "NSRC", "PX0")
    a.store_at("SRCR", "NSRC", "IDX")
    a.inc("NSRC")
    a.label(skip)


# ── setup, pass 2: pair the runs into rooms, then stamp their walls ────────────
def _emit_rooms(a: Asm, *, packed: bool) -> None:
    a.section("pair wall runs into rooms; a pair is only a room if its columns are '|'")
    a.label("p1_done")
    a.set_slot("IDX", 0)
    a.label("pr_i")
    a.br_lt("IDX", "NRUN", "pr_i_go")
    a.jmp("pr_done")
    a.label("pr_i_go")
    a.load_at("RUN", "IDX")
    a.st("T0")
    a.brn("pr_i_next", "already paired")
    a.op("MODI", 256, "PKEY = x0 * 16 + x1")
    a.st("PKEY")
    a.ld("T0")
    a.op("DIVI", 256)
    a.st("PY0")
    a.ld("IDX")
    a.op("ADDI", 1)
    a.st("JDX")

    a.label("pr_j")
    a.br_lt("JDX", "NRUN", "pr_j_go")
    a.jmp("pr_i_next")
    a.label("pr_j_go")
    a.load_at("RUN", "JDX")
    a.st("T1")
    a.brn("pr_j_next")
    a.op("MODI", 256)
    a.op("SUB", "PKEY")
    a.brz("pr_try", "same (x0, x1): a candidate room")
    a.jmp("pr_j_next")

    a.label("pr_try")
    a.ld("PKEY")
    a.op("DIVI", PANEL)
    a.st("PX0")
    a.ld("PKEY")
    a.op("MODI", PANEL)
    a.st("PX1")
    a.ld("T1")
    a.op("DIVI", 256)
    a.st("T2", "y1")
    a.ld("PY0", "yy = y0 + 1")
    a.op("ADDI", 1)
    a.st("T3")
    a.label("pr_val")
    a.br_lt("T3", "T2", "pr_val_go")
    a.jmp("pr_accept")
    a.label("pr_val_go")
    a.ld("T3", "left column of the candidate")
    a.op("MULI", PANEL)
    a.st("T4")
    a.op("ADD", "PX0")
    a.st("VAL")
    _cell_read(a, "VAL", "T0", packed=packed)
    a.ld("T0")
    a.op("SUBI", enc(CLS_PIPE))
    a.brz("pr_val_right")
    a.jmp("pr_j_next", "not a '|': these two runs are not a room")
    a.label("pr_val_right")
    a.ld("T4")
    a.op("ADD", "PX1")
    a.st("VAL")
    _cell_read(a, "VAL", "T0", packed=packed)
    a.ld("T0")
    a.op("SUBI", enc(CLS_PIPE))
    a.brz("pr_val_next")
    a.jmp("pr_j_next")
    a.label("pr_val_next")
    a.inc("T3")
    a.jmp("pr_val")

    a.label("pr_accept")
    a.ld("NROOM")
    a.op("SUBI", MAX_ROOMS)
    a.brz("pr_j_next", "room table full")
    a.store_at("RX0", "NROOM", "PX0")
    a.store_at("RY0", "NROOM", "PY0")
    a.store_at("RX1", "NROOM", "PX1")
    a.store_at("RY1", "NROOM", "T2")
    a.inc("NROOM")
    a.set_slot("T0", -1, "consume both runs")
    a.store_at("RUN", "IDX", "T0")
    a.store_at("RUN", "JDX", "T0")
    a.jmp("pr_i_next")

    a.label("pr_j_next")
    a.inc("JDX")
    a.jmp("pr_j")
    a.label("pr_i_next")
    a.inc("IDX")
    a.jmp("pr_i")

    a.section("stamp every room perimeter as class 20 — writes are nearly free")
    a.label("pr_done")
    a.set_slot("IDX", 0)
    a.label("st_room")
    a.br_lt("IDX", "NROOM", "st_room_go")
    a.jmp("st_done")
    a.label("st_room_go")
    a.load_at("RX0", "IDX")
    a.st("T0")
    a.load_at("RY0", "IDX")
    a.st("T1")
    a.load_at("RX1", "IDX")
    a.st("T2")
    a.load_at("RY1", "IDX")
    a.st("T3")
    a.ld("T1", "VAL walks the top wall, T4 the bottom")
    a.op("MULI", PANEL)
    a.op("ADD", "T0")
    a.st("VAL")
    a.ld("T3")
    a.op("MULI", PANEL)
    a.op("ADD", "T0")
    a.st("T4")
    a.copy("JDX", "T0")
    a.label("st_top")
    a.ld("JDX")
    a.op("SUB", "T2")
    a.op("SUBI", 1)
    a.brn("st_top_go")
    a.jmp("st_side_init")
    a.label("st_top_go")
    _cell_write(a, "VAL", "C20", packed=packed)
    _cell_write(a, "T4", "C20", packed=packed)
    _paint(a, "VAL", COLOUR_OF_CLASS[CLS_WALL])
    _paint(a, "T4", COLOUR_OF_CLASS[CLS_WALL])
    _emit_source_probe(a, "VAL", -PANEL, 0, packed=packed)
    _emit_source_probe(a, "T4", PANEL, 2, packed=packed)
    a.inc("VAL")
    a.inc("T4")
    a.inc("JDX")
    a.jmp("st_top")

    a.label("st_side_init")
    a.ld("T1", "VAL walks the left wall, T4 the right")
    a.op("MULI", PANEL)
    a.st("VAL")
    a.op("ADD", "T2")
    a.st("T4")
    a.ld("VAL")
    a.op("ADD", "T0")
    a.st("VAL")
    a.copy("JDX", "T1")
    a.label("st_side")
    a.ld("JDX")
    a.op("SUB", "T3")
    a.op("SUBI", 1)
    a.brn("st_side_go")
    a.inc("IDX")
    a.jmp("st_room")
    a.label("st_side_go")
    _cell_write(a, "VAL", "C20", packed=packed)
    _cell_write(a, "T4", "C20", packed=packed)
    _paint(a, "VAL", COLOUR_OF_CLASS[CLS_WALL])
    _paint(a, "T4", COLOUR_OF_CLASS[CLS_WALL])
    _emit_source_probe(a, "VAL", -1, 3, packed=packed)
    _emit_source_probe(a, "T4", 1, 1, packed=packed)
    a.ld("VAL")
    a.op("ADDI", PANEL)
    a.st("VAL")
    a.ld("T4")
    a.op("ADDI", PANEL)
    a.st("T4")
    a.inc("JDX")
    a.jmp("st_side")

    a.section("which room each man lives in — fixed for the whole run")
    a.label("st_done")
    a.set_slot("IDX", 0)
    a.label("mr_loop")
    a.br_lt("IDX", "NMEN", "mr_go")
    a.jmp("mr_done")
    a.label("mr_go")
    a.load_at("MPOS", "IDX")
    a.st("T0")
    a.op("MODI", PANEL)
    a.st("RKX")
    a.ld("T0")
    a.op("DIVI", PANEL)
    a.st("RKY")
    _call_room_kind(a, 0)
    a.store_at("MROOM", "IDX", "RIDX")
    a.set_slot("T0", enc(CLS_SPACE), "his own '@' cell is empty space")
    a.store_at("MCLS", "IDX", "T0")
    a.set_slot("T0", 1, "and he starts facing east")
    a.store_at("MDIR", "IDX", "T0")
    a.set_slot("T0", -1, "his pipe cache is empty")
    a.store_at("MPIPEA", "IDX", "T0")
    a.inc("IDX")
    a.jmp("mr_loop")
    a.label("mr_done")


# ── setup: the men go on top of whatever they stand on ───────────────────────
def _emit_men_paint(a: Asm) -> None:
    a.section("the men, drawn over the cells they spawned on")
    a.set_slot("IDX", 0)
    a.label("pm_loop")
    a.br_lt("IDX", "NMEN", "pm_go")
    a.jmp("pm_done")
    a.label("pm_go")
    a.load_at("MPOS", "IDX")
    a.op("DSPA")
    a.ldi(COLOUR_MAN)
    a.op("DSPD")
    a.inc("IDX")
    a.jmp("pm_loop")
    a.label("pm_done")
    a.ldi(1, "commit the opening frame; SWAP 1 keeps both buffers, so it is a base")
    a.op("DSPS")
    a.jmp("round")


# ── setup, pass 4: walk each pipe from its source arrowhead ───────────────────
def _emit_pipe_walk(a: Asm, *, packed: bool) -> None:
    a.section("walk the pipes: flow order, arrowheads re-aim, a wall ends it")
    a.set_slot("VAL", 0, "next free slot in PCA/OCC")
    a.set_slot("IDX", 0)
    a.label("pw_loop")
    a.br_lt("IDX", "NSRC", "pw_go")
    a.jmp("pw_done")
    a.label("pw_go")
    a.store_at("PBASE", "NP", "VAL")
    a.load_at("SRCA", "IDX")
    a.st("T0", "cur")
    a.store_at("PSRC", "NP", "T0")
    a.load_at("SRCD", "IDX")
    a.st("T1", "flow direction")
    a.op("ADDI", enc(CLS_ARROW))
    a.st("FCLS", "the source cell is an arrowhead")
    a.load_at("SRCR", "IDX")
    a.st("T2")
    a.store_at("PSROOM", "NP", "T2")
    a.copy("JDX", "VAL", "slot cursor")
    a.set_slot("T3", 0, "cells so far")

    a.label("pw_step")
    a.ld("T3")
    a.op("SUBI", MAX_PIPE_CELLS)
    a.brn("pw_step_go")
    a.jmp("pw_end", "longer than the problem allows: stop here")
    a.label("pw_step_go")
    a.store_at("PCA", "JDX", "T0")
    a.store_at("OCC", "JDX", "CZ")
    a.inc("JDX")
    a.inc("T3")
    _cell_write(a, "T0", "FCLS", "this cell is a pipe cell after all", packed=packed)
    _paint(a, "T0", COLOUR_PIPE)
    a.load_at("DTAB", "T1")
    a.st("T4")
    a.ld("T0")
    a.op("ADD", "T4")
    a.st("PKEY", "the next cell along the flow")
    a.brn("pw_end", "off the grid: the program is not well formed")
    a.op("SUB", "LIMIT")
    a.brn("pw_in")
    a.jmp("pw_end")
    a.label("pw_in")
    _cell_read(a, "PKEY", "CLS", packed=packed)
    a.ld("CLS", "the next cell still carries its provisional class")
    a.op("MODI", CELL_SHIFT)
    a.st("CLS")
    a.op("SUBI", CLS_WALL)
    a.brz("pw_end", "a wall ends the walk")
    a.ld("CLS")
    a.op("SUBI", CLS_ADD)
    a.brn("pw_end", "space or a digit: not a pipe, so the walk is over")
    a.ld("CLS")
    a.op("SUBI", CLS_DIR)
    a.brn("pw_body", "'+' or '-' is a body")
    a.ld("CLS")
    a.op("SUBI", CLS_WALL)
    a.brn("pw_arrow", "16..19 re-aims the flow")
    a.ld("CLS")
    a.op("SUBI", CLS_PIPE)
    a.brz("pw_body", "'|' is a body")
    a.jmp("pw_end")
    a.label("pw_arrow")
    a.ld("CLS")
    a.op("SUBI", CLS_DIR)
    a.st("T1")
    a.op("ADDI", enc(CLS_ARROW))
    a.st("FCLS")
    a.jmp("pw_next")
    a.label("pw_body")
    a.set_slot("FCLS", enc(CLS_PIPE))
    a.label("pw_next")
    a.copy("T0", "PKEY")
    a.jmp("pw_step")

    a.label("pw_end")
    a.store_at("PDST", "NP", "T0")
    a.store_at("PLEN", "NP", "T3")
    a.store_at("PLO", "NP", "CZ")
    a.store_at("PCNT", "NP", "CZ")
    a.set_slot("T4", -1, "PHI < PLO means the pipe is empty")
    a.store_at("PHI", "NP", "T4")
    a.ld("VAL", "the next pipe's cells start after this one's")
    a.op("ADD", "T3")
    a.st("VAL")
    a.ld("PKEY", "the wall the last arrowhead points into names the room")
    a.op("MODI", PANEL)
    a.st("RKX")
    a.ld("PKEY")
    a.op("DIVI", PANEL)
    a.st("RKY")
    _call_room_kind(a, 1)
    a.store_at("PDROOM", "NP", "RIDX")
    a.inc("NP")
    a.inc("IDX")
    a.jmp("pw_loop")

    a.label("pw_done")


# ── the shared rectangle test ─────────────────────────────────────────────────
def _emit_room_kind(a: Asm, sites: int) -> None:
    """``KIND``/``RIDX`` for ``(RKX, RKY)``.  Clobbers ``T0``..``T3`` and ``RI``.

    A subroutine rather than an inline block because it is *cold* — a few dozen
    calls a case — and ~50 ROM words inlined four times would tax every taken
    backward branch in the hot loop at 12 ticks a word.
    """
    a.section("room_kind(RKX, RKY) -> KIND 0 outside / 1 inside / 2 wall, RIDX")
    a.label("room_kind")
    a.set_slot("KIND", 0)
    a.set_slot("RIDX", -1)
    a.set_slot("RI", 0)
    a.label("rk_loop")
    a.br_lt("RI", "NROOM", "rk_go")
    a.jmp("rk_ret")
    a.label("rk_go")
    a.load_at("RX0", "RI")
    a.st("T0")
    a.load_at("RX1", "RI")
    a.st("T1")
    a.load_at("RY0", "RI")
    a.st("T2")
    a.load_at("RY1", "RI")
    a.st("T3")
    a.ld("RKX")
    a.op("SUB", "T0")
    a.brn("rk_next")
    a.ld("T1")
    a.op("SUB", "RKX")
    a.brn("rk_next")
    a.ld("RKY")
    a.op("SUB", "T2")
    a.brn("rk_next")
    a.ld("T3")
    a.op("SUB", "RKY")
    a.brn("rk_next")
    a.copy("RIDX", "RI", "inside this room's bounding box")
    a.set_slot("KIND", 1)
    a.ld("RKX")
    a.op("SUB", "T0")
    a.brz("rk_wall")
    a.ld("T1")
    a.op("SUB", "RKX")
    a.brz("rk_wall")
    a.ld("RKY")
    a.op("SUB", "T2")
    a.brz("rk_wall")
    a.ld("T3")
    a.op("SUB", "RKY")
    a.brz("rk_wall")
    a.jmp("rk_ret")
    a.label("rk_wall")
    a.set_slot("KIND", 2)
    a.jmp("rk_ret")
    a.label("rk_next")
    a.inc("RI")
    a.jmp("rk_loop")
    a.label("rk_ret")
    for site in range(sites - 1):
        a.ld("RET")
        a.op("SUBI", site)
        a.brz(f"rk_ret{site}")
    a.jmp(f"rk_ret{sites - 1}")


# ── the round and the tick ────────────────────────────────────────────────────
def _emit_round(a: Asm, *, packed: bool) -> None:
    a.section("one round: k ticks, then one committed frame")
    a.label("round")
    a.op("IN", note="k")
    a.st("K")
    a.label("tick")
    a.ld("STOP", "a wall freeze, or every man home on an H: pipes stop too")
    a.brz("tk_go")
    a.jmp("commit")
    a.label("tk_go")
    a.ld("K")
    a.brz("commit")
    for p in range(MAX_PIPES):
        _emit_shift(a, p)
    for i in range(MAX_MEN):
        _emit_man(a, i, packed=packed)
    a.ld("K", "one interpreted tick done")
    a.op("SUBI", 1)
    a.st("K")
    a.jmp("tick")

    a.label("commit")
    a.ldi(1, "SWAP 1: commit, keep both buffers, so the next frame is a delta")
    a.op("DSPS")
    a.jmp("round")


def _emit_shift(a: Asm, p: int) -> None:
    """Advance pipe `p` one cell, destination end first — the train rule."""
    a.section(f"pipe {p}: shift the occupied window down from its head")
    lo, hi, cnt, ln, base = (a.at(n, p) for n in ("PLO", "PHI", "PCNT", "PLEN", "PBASE"))
    done = f"sh_done{p}"
    a.ldi(p)
    a.op("SUB", "NP")
    a.brn(f"sh_go{p}")
    a.jmp(done)
    a.label(f"sh_go{p}")
    a.ld(cnt)
    a.brz(done, "nothing in flight")
    a.copy("IDX", hi)
    a.set_slot("NEWHI", -1)
    a.set_slot("NEWLO", 0)
    a.ld(ln, "T2 = the destination cell's local index")
    a.op("SUBI", 1)
    a.st("T2")

    a.label(f"sh_loop{p}")
    a.ld("IDX")
    a.op("SUB", lo)
    a.brn(f"sh_end{p}")
    a.ld(base, "T4 = the slot this cell lives in")
    a.op("ADD", "IDX")
    a.st("T4")
    a.load_at("OCC", "T4")
    a.st("T0")
    a.brz(f"sh_step{p}", "empty")
    a.ld("IDX")
    a.op("SUB", "T2")
    a.brz(f"sh_stay{p}", "the destination cell never advances")
    a.ld("T4")
    a.op("ADDI", 1)
    a.st("VAL")
    a.load_at("OCC", "VAL")
    a.brz(f"sh_move{p}")
    a.jmp(f"sh_stay{p}", "the cell ahead is still occupied")

    a.label(f"sh_move{p}")
    a.store_at("OCC", "VAL", "T0")
    a.store_at("OCC", "T4", "CZ")
    a.load_at("PCA", "T4", "the vacated cell goes back to plain pipe")
    a.op("DSPA")
    a.ldi(COLOUR_PIPE)
    a.op("DSPD")
    a.load_at("PCA", "VAL")
    a.op("DSPA")
    a.ldi(COLOUR_VALUE)
    a.op("DSPD")
    a.ld("IDX")
    a.op("ADDI", 1)
    a.st("T1")
    a.jmp(f"sh_rec{p}")
    a.label(f"sh_stay{p}")
    a.copy("T1", "IDX")
    a.label(f"sh_rec{p}")
    a.ld("NEWHI")
    a.brn(f"sh_hi{p}")
    a.jmp(f"sh_lo{p}")
    a.label(f"sh_hi{p}")
    a.copy("NEWHI", "T1", "the first value found is the new head")
    a.label(f"sh_lo{p}")
    a.copy("NEWLO", "T1", "the last one found is the new tail")
    a.label(f"sh_step{p}")
    a.ld("IDX")
    a.op("SUBI", 1)
    a.st("IDX")
    a.jmp(f"sh_loop{p}")

    a.label(f"sh_end{p}")
    a.ld("NEWHI")
    a.brn(f"sh_heal{p}")
    a.copy(hi, "NEWHI")
    a.copy(lo, "NEWLO")
    a.jmp(done)
    a.label(f"sh_heal{p}")
    a.copy(hi, "T2", "the window was stale: widen it and let the next pass tighten")
    a.set_slot(lo, 0)
    a.label(done)


def _emit_man(a: Asm, i: int, *, packed: bool) -> None:
    """Man `i` executes the class he stands on, then moves."""
    a.section(f"man {i}")
    pos, dr, ra, rb, halt, room, cls, pipe, pipea = (
        a.at(n, i)
        for n in ("MPOS", "MDIR", "MA", "MB", "MHALT", "MROOM", "MCLS", "MPIPE", "MPIPEA")
    )
    done, move = f"m_done{i}", f"m_move{i}"
    a.ldi(i)
    a.op("SUB", "NMEN")
    a.brn(f"m_go{i}")
    a.jmp(done)
    a.label(f"m_go{i}")
    a.ld(halt)
    a.brz(f"m_live{i}")
    a.jmp(done)
    # One read, then a cumulative subtract chain: BRZ and BRN leave ACC alone, and
    # a store read costs 8 ticks a slot — re-loading the class for each test was
    # nine extra reads a man a tick, the single largest item in the profile.
    a.label(f"m_live{i}")
    a.ld(cls)
    a.op("MODI", CELL_SHIFT)
    a.st("CLS")
    a.op("SUBI", CLS_SPACE)
    a.brn(f"m_digit{i}", "0..9: A = the digit")
    a.brz(move, "10: space")
    a.op("SUBI", CLS_M - CLS_SPACE)
    a.brz(f"m_mov{i}", "11: M")
    a.op("SUBI", CLS_ADD - CLS_M)
    a.brz(f"m_add{i}", "12: +")
    a.op("SUBI", CLS_SUB - CLS_ADD)
    a.brz(f"m_sub{i}", "13: -")
    a.op("SUBI", CLS_X - CLS_SUB)
    a.brz(f"m_turn{i}", "14: X")
    a.op("SUBI", CLS_H - CLS_X)
    a.brz(f"m_halt{i}", "15: H")
    a.op("SUBI", CLS_WALL - CLS_H)
    a.brn(f"m_dir{i}", "16..19: a heading")
    a.op("SUBI", CLS_S - CLS_WALL)
    a.brz(f"m_send{i}", "21: s")
    a.op("SUBI", CLS_R - CLS_S)
    a.brz(f"m_recv{i}", "22: r")
    a.jmp(move, "a wall or a pipe cell cannot be under a live man")

    a.label(f"m_digit{i}")
    a.ld("CLS")
    a.st(ra)
    a.jmp(move)
    a.label(f"m_mov{i}")
    a.ld(ra)
    a.st(rb)
    a.jmp(move)
    a.label(f"m_add{i}")
    a.ld(ra)
    a.op("ADD", rb)
    a.st(ra)
    a.jmp(move)
    a.label(f"m_sub{i}")
    a.ld(ra)
    a.op("SUB", rb)
    a.st(ra)
    a.jmp(move)
    a.label(f"m_dir{i}")
    a.ld("CLS")
    a.op("SUBI", CLS_DIR)
    a.st(dr)
    a.jmp(move)
    a.label(f"m_turn{i}")
    a.ld(ra, "X turns by sign(A)")
    a.brz(move)
    a.brn(f"m_ccw{i}")
    a.ld(dr)
    a.op("ADDI", 1)
    a.op("MODI", 4)
    a.st(dr)
    a.jmp(move)
    a.label(f"m_ccw{i}")
    a.ld(dr)
    a.op("SUBI", 1)
    a.op("MODI", 4)
    a.st(dr)
    a.jmp(move)
    a.label(f"m_halt{i}")
    a.set_slot(halt, 1, "H: he stays here forever")
    a.inc("NHALT")
    a.op("SUB", "NMEN", "the last man home stops the program")
    a.brz(f"m_last{i}")
    a.jmp(done)
    a.label(f"m_last{i}")
    a.set_slot("STOP", 1)
    a.jmp(done)

    a.label(f"m_send{i}")
    a.ld(pipea, "which pipe this cell binds to never changes, so cache it")
    a.op("SUB", pos)
    a.brz(f"m_send_hit{i}")
    a.set_slot("PICKM", 0)
    a.copy("PICKR", room)
    a.copy("PICKA", pos)
    a.set_slot("RET2", 2 * i)
    a.jmp("pick")
    a.label(f"pk_ret{2 * i}")
    a.ld("BEST")
    a.st(pipe)
    a.ld(pos)
    a.st(pipea)
    a.jmp(f"m_send_go{i}")
    a.label(f"m_send_hit{i}")
    a.ld(pipe)
    a.st("BEST")
    a.label(f"m_send_go{i}")
    a.ld("BEST")
    a.brn(move, "no outgoing pipe: nothing a well-formed program can do")
    a.load_at("PBASE", "BEST")
    a.st("T4", "the source cell is local index 0")
    a.load_at("OCC", "T4")
    a.brz(f"m_send_ok{i}")
    a.jmp(done, "the pipe's first cell is full: he blocks on the s")
    a.label(f"m_send_ok{i}")
    a.ld(ra)
    a.op("ADDI", VALUE_BIAS)
    a.st("VAL")
    a.store_at("OCC", "T4", "VAL")
    a.load_at("PCA", "T4")
    a.op("DSPA")
    a.ldi(COLOUR_VALUE)
    a.op("DSPD")
    a.load_at("PCNT", "BEST")
    a.st("T0")
    a.op("ADDI", 1)
    a.st("T1")
    a.store_at("PCNT", "BEST", "T1")
    a.store_at("PLO", "BEST", "CZ")
    a.ld("T0")
    a.brz(f"m_send_hi{i}")
    a.jmp(move)
    a.label(f"m_send_hi{i}")
    a.store_at("PHI", "BEST", "CZ", "the pipe was empty, so cell 0 is also its head")
    a.jmp(move)

    a.label(f"m_recv{i}")
    a.ld(pipea, "which pipe this cell binds to never changes, so cache it")
    a.op("SUB", pos)
    a.brz(f"m_recv_hit{i}")
    a.set_slot("PICKM", 1)
    a.copy("PICKR", room)
    a.copy("PICKA", pos)
    a.set_slot("RET2", 2 * i + 1)
    a.jmp("pick")
    a.label(f"pk_ret{2 * i + 1}")
    a.ld("BEST")
    a.st(pipe)
    a.ld(pos)
    a.st(pipea)
    a.jmp(f"m_recv_go{i}")
    a.label(f"m_recv_hit{i}")
    a.ld(pipe)
    a.st("BEST")
    a.label(f"m_recv_go{i}")
    a.ld("BEST")
    a.brn(move, "no incoming pipe")
    a.load_at("PBASE", "BEST")
    a.st("T4")
    a.load_at("PLEN", "BEST")
    a.op("SUBI", 1)
    a.op("ADD", "T4")
    a.st("T4", "the destination cell")
    a.load_at("OCC", "T4")
    a.st("T0")
    a.brz(done, "nothing has arrived: he blocks on the r")
    a.op("SUBI", VALUE_BIAS)
    a.st(ra)
    a.store_at("OCC", "T4", "CZ")
    a.load_at("PCA", "T4")
    a.op("DSPA")
    a.ldi(COLOUR_PIPE)
    a.op("DSPD")
    a.load_at("PCNT", "BEST")
    a.op("SUBI", 1)
    a.st("T1")
    a.store_at("PCNT", "BEST", "T1")
    a.jmp(move)

    a.label(move)
    a.ld(pos, "repaint the cell he leaves with the colour of the class under it")
    a.op("DSPA")
    a.ld(cls)
    a.op("DIVI", CELL_SHIFT)
    a.op("DSPD")
    a.load_at("DTAB", dr)
    a.st("T0")
    a.ld(pos)
    a.op("ADD", "T0")
    a.st(pos)
    a.op("DSPA")
    a.ldi(COLOUR_MAN)
    a.op("DSPD")
    _cell_read(a, pos, "CLS", packed=packed)
    a.ld("CLS", "keep it for the next tick's dispatch, and test it for a wall")
    a.st(cls)
    a.op("SUBI", enc(CLS_WALL))
    a.brz(f"m_wall{i}")
    a.jmp(done)
    a.label(f"m_wall{i}")
    a.set_slot("STOP", 1, "this tick still completes; nothing after it does")
    a.label(done)


def _emit_pick(a: Asm, sites: int) -> None:
    """``BEST`` = the nearest pipe of the requested direction attached to a room.

    "Nearest" is Manhattan distance from the man's cell to the arrowhead at this
    room, ties broken by reading order — and reading order *is* the address order,
    since an address is ``16y + x``.
    """
    a.section("pick(PICKM, PICKR, PICKA) -> BEST: the pipe an s or an r talks to")
    a.label("pick")
    a.set_slot("BEST", -1)
    a.set_slot("BESTD", 0)
    a.set_slot("BESTA", 0)
    a.set_slot("IDX", 0)
    a.label("pk_loop")
    a.br_lt("IDX", "NP", "pk_go")
    a.jmp("pk_ret")
    a.label("pk_go")
    a.ld("PICKM")
    a.brz("pk_send")
    a.load_at("PDROOM", "IDX")
    a.st("T0")
    a.load_at("PDST", "IDX")
    a.st("T1")
    a.jmp("pk_have")
    a.label("pk_send")
    a.load_at("PSROOM", "IDX")
    a.st("T0")
    a.load_at("PSRC", "IDX")
    a.st("T1")
    a.label("pk_have")
    a.ld("T0")
    a.op("SUB", "PICKR")
    a.brz("pk_cand")
    a.jmp("pk_next", "not attached to his room")

    a.label("pk_cand")
    a.ld("PICKA", "|dx|")
    a.op("MODI", PANEL)
    a.st("T2")
    a.ld("T1")
    a.op("MODI", PANEL)
    a.st("T3")
    a.ld("T2")
    a.op("SUB", "T3")
    a.st("T4")
    a.brn("pk_negx")
    a.jmp("pk_dy")
    a.label("pk_negx")
    a.ldi(0)
    a.op("SUB", "T4")
    a.st("T4")
    a.label("pk_dy")
    a.ld("PICKA", "|dy|")
    a.op("DIVI", PANEL)
    a.st("T2")
    a.ld("T1")
    a.op("DIVI", PANEL)
    a.st("T3")
    a.ld("T2")
    a.op("SUB", "T3")
    a.st("T2")
    a.brn("pk_negy")
    a.jmp("pk_sum")
    a.label("pk_negy")
    a.ldi(0)
    a.op("SUB", "T2")
    a.st("T2")
    a.label("pk_sum")
    a.ld("T4")
    a.op("ADD", "T2")
    a.st("T4", "the Manhattan distance")
    a.ld("BEST")
    a.brn("pk_take", "first candidate")
    a.ld("T4")
    a.op("SUB", "BESTD")
    a.brn("pk_take")
    a.brz("pk_tie")
    a.jmp("pk_next")
    a.label("pk_tie")
    a.ld("T1")
    a.op("SUB", "BESTA")
    a.brn("pk_take", "same distance: earlier in reading order wins")
    a.jmp("pk_next")
    a.label("pk_take")
    a.copy("BEST", "IDX")
    a.copy("BESTD", "T4")
    a.copy("BESTA", "T1")
    a.label("pk_next")
    a.inc("IDX")
    a.jmp("pk_loop")
    a.label("pk_ret")
    for site in range(sites - 1):
        a.ld("RET2")
        a.op("SUBI", site)
        a.brz(f"pk_ret{site}")
    a.jmp(f"pk_ret{sites - 1}")


# ── the whole program ─────────────────────────────────────────────────────────
#: The hot tier's shape, and both numbers are pinned by the machine's own box.
#: ``memory_men_grid`` is ``27 * cols`` wide and ``32 + 3 * rows`` tall, and the
#: free space east of the CPU band is 93 x 111 — so two columns is the widest that
#: still leaves room for the tape beside it inside the ROM's 203, and 26 rows the
#: tallest that fits under the panel. 52 cells is what falls out, and 52 slots is
#: 90.6 % of the 3,185 reads a case (``lm1.emulator`` with a counting store).
#: Measured on the engine over all 14 public cases, 50M tick cap:
#:
#:     |                | footprint | avg ticks  | max ticks  | score   |
#:     | tape only      |    41,616 | 20,275,186 | 31,809,643 | 8.44e11 |
#:     | + 52-slot tier |    41,616 |  8,605,207 | 13,676,774 | 3.58e11 |
#:
#: **This is now the default, and the hot bank is a pipe tape, not a man-memory.**
#: The man-memory version of this tier was judged ``11/28`` at 10 slots and ``4/28``
#: at 52 — refused on wall clock, because every slot of it is a live little man and
#: the grader's cost is ``runners x ticks``. A pipe tape has four men at *any* size,
#: so ``(4, 26)`` = 104 slots costs three extra men and was judged **28/28 at
#: 400,740,741,396** — 2.12x the single-tape machine, at a *lower* runner-tick
#: product (0.110bn against 0.151bn). See ``machine.TIER_PIPE_BANK`` and LLM-DESIGN.
HOT = (4, 26)
HOT_SLOTS = HOT[0] * HOT[1]


def build_asm(*, packed_cells: bool = False, hot_slots: int = HOT_SLOTS) -> tuple[str, int]:
    """Return the ``.asm`` text and the tape slot count it needs.

    ``hot_slots`` reserves the lowest addresses for ``machine.build(hot=...)``'s
    man-memory tier; the slots marked ``hot=True`` in :func:`_declare` are the ones
    that land there, chosen by measuring reads per slot across all 14 public cases
    (``lm1.emulator`` with a counting ``DictStore``) and taking whole declarations
    greedily by reads-per-slot. At 52 they are 90.6 % of the 3,185 reads a case.
    """
    a = Asm(hot_slots=hot_slots)
    _declare(a, packed_cells=packed_cells)
    a.jmp("setup", "the ROM starts here; setup runs once and falls into round")
    _emit_round(a, packed=packed_cells)
    _emit_pick(a, 2 * MAX_MEN)
    _emit_pass1(a, packed=packed_cells)
    _emit_rooms(a, packed=packed_cells)
    _emit_pipe_walk(a, packed=packed_cells)
    _emit_men_paint(a)
    _emit_room_kind(a, 2)
    header = (
        "; little-little-man — an interpreter for the LLM language.\n"
        "; GENERATED by randomfun2026solvers.llm_lm1; do not hand-edit.\n"
        f"; cells are {'packed eight to a word' if packed_cells else 'one to a slot'}."
    )
    return a.text(header), a.slots


#: The ROM fold, re-swept after two independent compactions landed together: the
#: packed structures band (stacked slab entry rows, 20 rows -> 10) and main's
#: compact whole-machine routing.  Rows the CPU gives up only pay once the fold
#: converts them into width, so the sweep has to be redone whenever either moves.
#:
#: The sweep's minimum is not a coincidence and is worth stating as a rule: score is
#: ``max(w, h)^2``, so only the *larger* side is charged and every unit the smaller
#: side falls short is free area we are not using.  The fold trades width for height
#: monotonically, so the optimum is wherever the two cross — the **square** machine.
#: Re-swept under the **banked store**, which moved the crossing by one row:
#: 88 -> 197x192 (38,809, the fold that shipped at 375,276,399,205), 89 -> 196x193
#: (38,416), **90 -> 192x194 (37,636)**, 91 -> 190x195 (38,025).  90 is the crossing —
#: the last fold at which width still exceeds height.
#:
#: This constant has now been swept three times against three different geometries and
#: landed on a different value each time (88 single-tape, 89 after ``height = bottom - 1``
#: was reverted, 90 under the banked store).  Treat any change to the CPU, the ROM, the
#: store or the tape as invalidating it, and re-sweep: the fold is worth ~3% of the score
#: and nothing else in the suite fails when it drifts.
ROM_ROWS = 90


def build_machine(
    *, packed_cells: bool = False, rom_rows: int = ROM_ROWS, hot: tuple[int, int] | None = HOT
):
    """Assemble the interpreter and emit the whole machine — CPU, ROM, tape, panel.

    ``hot=(cols, rows)`` adds the second store tier: a man-memory grid holding the
    lowest ``cols * rows`` slots, which answers in ~200 ticks against the tape's
    ``8 * 427`` = 3,416.  :data:`HOT` is the measured shape and the default;
    ``hot=None`` builds the single-tape machine this replaced, for comparison —
    engine-measured, the tier is **2.36x**: 20,275,186 ticks a case against
    8,605,207, at an unchanged 203x204.
    """
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1.asm import assemble

    hot_slots = hot[0] * hot[1] if hot else 0
    text, slots = build_asm(packed_cells=packed_cells, hot_slots=hot_slots)
    program = assemble(text, name="little-little-man")
    built = machine.build(program, tape_n=slots, display=(PANEL, PANEL), rom_rows=rom_rows, hot=hot)
    return built, program, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asm", type=Path, help="write the assembly here")
    ap.add_argument("--man", type=Path, help="build the machine and write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    ap.add_argument("--rom-rows", type=int, default=ROM_ROWS, help="ROM fold")
    ap.add_argument("--packed", action="store_true", help="pack four cells to a word")
    args = ap.parse_args(argv)

    if not (args.man or args.html or args.json):
        text, slots = build_asm(packed_cells=args.packed)
        if args.asm:
            args.asm.write_text(text)
        else:
            print(text)
        print(f"# {slots} store slots")
        return 0

    built, program, text = build_machine(packed_cells=args.packed, rom_rows=args.rom_rows)
    if args.asm:
        args.asm.write_text(text)
    if args.man:
        args.man.write_text("\n".join(built.rows) + "\n")
    if args.html:
        built.debug_map().write_html(built.rows, args.html)
    if args.json:
        built.debug_map().write_json(args.json)
    print(built.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
