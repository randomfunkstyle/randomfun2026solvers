#!/usr/bin/env python3
"""Synthesise a per-program LM-1 CPU: program in, smallest machine that runs it out.

There is no "the LM-1 CPU" (ARCH.md §7.5). The decode trie's depth is
``ceil(log2(#opcodes used))`` and its leaves spread geometrically, so the opcode
count sets the CPU room's height -- 7 opcodes gives depth 3 and ~19 rows against
24 opcodes' depth 5 and ~35. Footprint is squared, so synthesising for the used
set is most of the score. Blocks follow the same rule: a ``register-cell`` per
spill slot actually used, no ``I``/``O`` room when the program never reads or
writes, no tape when the program needs no array.

Machine shape (all of it verified in ARCH.md §2):

* **ROM** -- a *looping* ROM (§5.3): the man walks a closed circuit and re-emits
  the program forever, so there is no code ring, no ``LOOP`` room, no capacity
  constraint and never a write-back. Fetch is ``>rbr``.
* **CPU** -- fetch, then a depth-``k`` backpack trie (§2.2/§2.4), then one lane
  per used opcode, then a return path back to the fetch cell.
* **blocks** -- whatever the program's opcodes actually need.

Instructions are **fixed-width two words** (opcode + operand, operand ignored
where unused). That is what makes the geometry close: only the fetch stage ever
touches the ROM pipe, so each lane needs just the one pipe its own micro-program
uses, and §7.1's nearest-pipe rule becomes trivial to satisfy.

Opcode *numbers* are an output of layout, not constants. The trie sorts leaves
bit-reversed, so ``number = bit_reverse_k(row_index)``: choosing a row for a lane
chooses its opcode. Lanes that need the north pipe go up top, the south pipe down
the bottom, shared east-bus lanes in the middle.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Band", "Op", "Program", "synthesise"]


class Band:
    """Which pipe a glyph must sit beside. ``None`` means it does not care."""

    IN = "in"  # north wall: input room
    OUT = "out"  # south wall: output room
    CELL = "cell"  # east wall: the register-cell spill bus (two pipes)


@dataclass(frozen=True)
class Op:
    """One opcode: its micro-program as ``(glyph, band)`` pairs."""

    name: str
    micro: tuple[tuple[str, str | None], ...]

    @property
    def bands(self) -> set[str]:
        return {b for _, b in self.micro if b is not None}

    @property
    def halts(self) -> bool:
        return any(g == "H" for g, _ in self.micro)


@dataclass
class Program:
    ops: dict[str, Op]
    code: list[tuple[str, int]]  # (opcode name, operand word)
    cells: int = 0  # register-cell spill slots to instantiate
    name: str = "program"

    @property
    def used(self) -> list[str]:
        seen: list[str] = []
        for name, _ in self.code:
            if name not in seen:
                seen.append(name)
        return seen

    @property
    def bands(self) -> set[str]:
        return {b for n in self.used for b in self.ops[n].bands}


def _bitrev(v: int, k: int) -> int:
    return int(format(v, f"0{k}b")[::-1], 2) if k else 0


class _Grid:
    def __init__(self) -> None:
        self.c: dict[tuple[int, int], str] = {}

    def put(self, x: int, y: int, ch: str) -> None:
        if (x, y) in self.c and self.c[(x, y)] != ch:
            raise ValueError(f"collision at {(x, y)}: {self.c[(x, y)]!r} vs {ch!r}")
        self.c[(x, y)] = ch

    def text(self, x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            self.put(x + i, y, ch)

    def room(self, x0: int, y0: int, x1: int, y1: int) -> None:
        for x in range(x0 + 1, x1):
            self.put(x, y0, "-")
            self.put(x, y1, "-")
        for y in range(y0 + 1, y1):
            self.put(x0, y, "|")
            self.put(x1, y, "|")
        for c in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.put(*c, "+")

    def rows(self) -> list[str]:
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        out = ["".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]
        while out and not out[-1]:
            out.pop()
        return out


# ── opcode numbering: rows first, numbers derived ────────────────────────────
def assign(prog: Program) -> tuple[int, dict[str, int], dict[str, int]]:
    """Return ``(depth, name -> opcode number, name -> lane row)``.

    Rows are picked by what pipe each lane needs -- IN at the top by the north
    pipe, OUT at the bottom by the south pipe, everything else in between -- and
    the opcode number then falls out of the trie's bit-reversal.
    """
    used = prog.used
    k = max(1, (len(used) - 1).bit_length())
    lanes = 1 << k

    def rank(name: str) -> tuple[int, int, str]:
        """Group by pipe need, then **longest micro-program first**.

        A lane drops south to the return row at its own end, and the drop crosses
        every row below it -- so a lane can only drop early if the lanes beneath
        are shorter. Ordering longest-first makes the drop columns a descending
        staircase, which is what lets the short lanes stop early instead of all
        walking out to a shared far column.
        """
        b = prog.ops[name].bands
        if Band.IN in b:
            return (0, 0, name)  # top: beside the north pipe
        if Band.OUT in b:
            return (2, 0, name)  # bottom: beside the south pipe
        return (1, -len(prog.ops[name].micro), name)

    order = sorted(used, key=rank)
    # spread them: IN-ish first, OUT-ish last, so the extremes hug their walls
    slots = list(range(lanes))
    placed: dict[str, int] = {}
    for name in [n for n in order if rank(n)[0] == 0]:
        placed[name] = slots.pop(0)
    for name in reversed([n for n in order if rank(n)[0] == 2]):
        placed[name] = slots.pop()
    for name in [n for n in order if rank(n)[0] == 1]:
        placed[name] = slots.pop(0)
    nums = {n: _bitrev(i, k) for n, i in placed.items()}
    rows = {n: 2 * i + 1 for n, i in placed.items()}
    return k, nums, rows


def synthesise(prog: Program) -> list[str]:
    k, nums, rows = assign(prog)
    lanes = 1 << k
    centre = 1 << k  # fetch row; lanes occupy odd rows 1 .. 2*lanes-1
    span = 2 * lanes - 1
    spawn_row, ret_row = span + 1, span + 2

    words: list[int] = []
    for name, operand in prog.code:
        words += [nums[name], operand]
    single = all(0 <= w <= 9 for w in words)
    if not single:
        raise NotImplementedError("multi-digit ROM words need the eastbound-row layout")

    bands = prog.bands
    lane_x0 = 7 + k  # fetch (4) + 2 nops + k trie columns
    cell_x = lane_x0 + 11  # east band, if any

    # lay the lanes out first, so the return column lands just past the widest
    lane_cells: dict[tuple[int, int], str] = {}
    for name in prog.used:
        row, x = rows[name], lane_x0
        for glyph, band in prog.ops[name].micro:
            if band == Band.CELL:
                while x < cell_x:
                    lane_cells[(x, row)] = "."
                    x += 1
            lane_cells[(x, row)] = glyph
            x += 1
    all_rows = list(range(1, span + 1, 2))
    lane_end = {
        r: max((cx for cx, cy in lane_cells if cy == r), default=lane_x0 - 1) for r in all_rows
    }
    halting = {rows[n] for n in prog.used if prog.ops[n].halts}
    # A lane drops south to the return row as soon as its micro-program ends, but
    # the drop crosses every row below it, so it must clear their cells too.
    drop_x: dict[int, int] = {}
    for r in sorted(all_rows, reverse=True):
        drop_x[r] = max([lane_end[q] for q in all_rows if q >= r]) + 1
    ret_x = max(drop_x.values())
    cpu_w = ret_x

    g = _Grid()
    CX, CY = 3, 6  # CPU room origin; west band carries the ROM pipe

    def cpu(lx: int, ly: int, s: str) -> None:
        g.text(CX + lx, CY + ly, s)

    # ── ROM: a closed loop, folded onto two rows (single digits mirror safely)
    half = (len(words) + 1) // 2
    seqW = "".join(f"{w}s" for w in words[:half])  # walked WESTBOUND
    seqE = "".join(f"{w}s" for w in words[half:])  # walked EASTBOUND
    seqE += " " * (len(seqW) - len(seqE))
    rom_w = len(seqE) + 2
    g.room(0, 0, rom_w + 1, 4)
    g.text(1, 1, ">" + seqE + "v")
    g.text(1, 2, "^" + seqW[::-1] + "<")
    g.text(2, 3, "@" + "." * (len(seqE) - 1) + "^")  # spawn joins at word 0

    # ── CPU room
    g.room(CX, CY, CX + cpu_w + 1, CY + ret_row + 1)
    cpu(1, centre, ">rbr..")  # receive opcode, BP = it, receive operand

    def trie(level: int, row: int) -> None:
        """`x` at this level's column, then walk the branch and recurse."""
        col, step = 6 + level, 1 << (k - level)
        cpu(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                # `>` turns east into the next column; `]` shifts BP for the next x
                cpu(col, row + sign * d, ">" if d == step else ("]" if d == 1 else "."))
            if level < k:
                trie(level + 1, row + sign * step)

    trie(1, centre)

    # ── lanes, each dropping at its own end (unused rows included)
    for (x, row), glyph in lane_cells.items():
        cpu(x, row, glyph)
    for r in all_rows:
        if r in halting:
            continue
        for x in range(lane_end[r] + 1, drop_x[r]):
            cpu(x, r, ".")
        for y in range(r, ret_row):
            cpu(drop_x[r], y, "v")

    # ── return path. The whole return row is `<`: a man already heading west
    # passes over it, one arriving from a drop turns onto it, so a drop column can
    # land anywhere along it.
    for x in range(2, ret_x + 1):
        cpu(x, ret_row, "<")
    cpu(1, ret_row, "^")
    for y in range(centre + 1, ret_row):
        cpu(1, y, ".")
    cpu(2, spawn_row, "@")
    for x in range(3, ret_x):
        if (CX + x, CY + spawn_row) not in g.c:
            cpu(x, spawn_row, ".")

    # ── blocks and pipes
    rom_pipe_y = CY + centre
    g.text(1, 5, "v")
    for y in range(6, rom_pipe_y):
        g.put(1, y, "|")
    g.text(1, rom_pipe_y, ">")
    g.text(2, rom_pipe_y, ">")  # -> CPU west wall

    if Band.IN in bands:
        in_name = next(n for n in prog.used if Band.IN in prog.ops[n].bands)
        col = CX + lane_x0
        g.room(col + 5, 0, col + 7, 2)
        g.text(col + 6, 1, "I")
        g.text(col + 6, 3, "v")
        g.put(col + 6, 4, "|")
        g.text(col + 6, 5, "<")
        for x in range(col + 1, col + 6):
            g.put(x, 5, "-")
        g.text(col, 5, "v")  # -> CPU north wall at the IN lane's column
        assert rows[in_name] == 1, "IN lane must be the top row"

    if Band.OUT in bands:
        out_name = next(n for n in prog.used if Band.OUT in prog.ops[n].bands)
        oy = CY + ret_row + 2
        col = CX + lane_x0 + 1
        g.text(col, oy, "v")
        g.put(col, oy + 1, "|")
        g.text(col, oy + 2, "v")
        g.room(col - 1, oy + 3, col + 1, oy + 5)
        g.text(col, oy + 4, "O")
        assert rows[out_name] == span, "OUT lane must be the bottom row"

    if Band.CELL in bands:
        ex = CX + cpu_w + 2  # first pipe cell east of the CPU
        ry = CY + centre - 1  # cell-out row / cell-in row
        g.text(ex, ry, ">")
        g.text(ex + 1, ry, ">")
        g.text(ex + 1, ry + 2, "<")
        g.text(ex, ry + 2, "<")
        g.room(ex + 2, ry - 3, ex + 7, ry + 3)
        for i, s in enumerate(("vWs<", "v..W", ">@RX", "^..W", "^Wr<")):
            g.text(ex + 3, ry - 2 + i, s)

    return g.rows()


# ── the triangle instance, as data ───────────────────────────────────────────
TRIANGLE = Program(
    name="triangle",
    cells=1,
    ops={
        o.name: o
        for o in (
            Op("IN", (("r", Band.IN), ("M", None))),
            Op(
                "STR",
                (
                    ("1", None),
                    ("s", Band.CELL),
                    ("W", Band.CELL),
                    ("s", Band.CELL),
                    ("W", Band.CELL),
                ),
            ),
            Op("ADDI", (("+", None), ("M", None))),
            Op(
                "MULR",
                (
                    ("1", None),
                    ("N", None),
                    ("s", Band.CELL),
                    ("r", Band.CELL),
                    ("*", Band.CELL),
                    ("M", Band.CELL),
                ),
            ),
            Op("DIVI", (("W", None), ("/", None), ("M", None))),
            Op("OUT", (("W", Band.OUT), ("s", Band.OUT), ("W", Band.OUT))),
            Op("HALT", (("H", None),)),
        )
    },
    code=[("IN", 0), ("STR", 0), ("ADDI", 1), ("MULR", 0), ("DIVI", 2), ("OUT", 0), ("HALT", 0)],
)

# a 3-opcode program: read one integer, print it. Proves the machine shrinks
# with the opcode set -- depth 2 instead of 3, 4 lanes instead of 8.
ECHO = Program(
    name="echo",
    ops={n: TRIANGLE.ops[n] for n in ("IN", "OUT", "HALT")},
    code=[("IN", 0), ("OUT", 0), ("HALT", 0)],
)

PROGRAMS = {"triangle": TRIANGLE, "echo": ECHO}

if __name__ == "__main__":
    import sys

    prog = PROGRAMS[sys.argv[1] if len(sys.argv) > 1 else "triangle"]
    k, nums, rows = assign(prog)
    print(f"# {prog.name}: {len(prog.used)} opcodes -> depth {k}, {1 << k} lanes", file=sys.stderr)
    for n in prog.used:
        print(f"#   {n:5} = opcode {nums[n]:2}  lane row {rows[n]:2}", file=sys.stderr)
    print("\n".join(synthesise(prog)))
