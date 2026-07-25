#!/usr/bin/env python3
"""Synthesise a whole LM-1 machine from an assembled program.

``ARCH.md`` §7.5's decision, carried out: *there is no "the LM-1 CPU" — there is a
CPU synthesiser, and each task gets its own instance.* Feed this an
:class:`~randomfun2026solvers.lm1.asm.Program` and it emits the smallest machine
that runs it: a looping ROM sized to the word count, a decode trie of depth
``ceil(log2 |opcodes used|)``, one lane per opcode actually used, and only the
blocks the program touches.

``synth.py`` was the first instance (7 opcodes, straight-line only, single-digit
ROM words). This one adds the three things the graded problems need — control
flow, multi-digit words, and addressed memory — and is what runs ``brackets`` and
``tcp``.

Machine shape
-------------

::

      ROM  (looping, rom.py)          I
       |                              |
       v west                         v north
    +--------------------------+
    |  CPU: >rbr, trie, lanes  |--- east --->  ADAPTER ---> TAPE
    |       structures band    |<-- east ----------------------'
    +--------------------------+
       |
       v west
       O

Three design points carry all the weight.

**Sign-biased memory addressing.** The STORE wire protocol is the ``memory``
problem's ``0 addr`` / ``1 addr value``, but a lane cannot *emit* that leading
``0``/``1``: instructions are fixed-width two words (§5.2), so ``A`` already holds
the operand when the lane starts, and the literal glyph would destroy it. B holds
ACC and there is no third register — this is the same hole §6.1 finds in ``STP``.

So the CPU speaks a one-word request instead, with the **operation in the sign**:
``+a`` reads slot ``a``, ``-a`` writes it (one more word follows). Nothing needs a
literal, nothing needs a spill slot, and no lane reads the ring. A small
:func:`adapter` room expands that back into the tape's real protocol, which leaves
the verified 32×32 tape (``memory_tape.build_v2``) untouched. Address 0 would be
sign-ambiguous, so **hardware addresses start at 1** and slot 0 is unused.

**Everything hard lives below the lanes.** A lane is one row, so a jump's counted
discard loop and a branch's three-way ``X`` fan-out — both genuinely
two-dimensional — cannot fit in one. They go in a *structures band* under the lane
band, one slab each, reached by the lane's own drop column and exiting onto the
shared collector row. Lanes stay one row tall and the trie stays untouched.

**Drop columns are a descending staircase.** A lane returns to the fetch site by
turning south at its own end; that column crosses every row beneath it, so it must
sit east of every lane below (``drop_x[r] = max(lane_end[q] for q >= r) + 1``).
Equal values are fine — the columns merge, and both men are going to the same
place. A floor keeps them east of the structures band so a simple lane's drop can
pass straight through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import rom as rommod
from .asm import Program
from .isa import TARGET_SEMS, Sem

__all__ = ["Band", "Machine", "build", "hw_micro", "MEMORY_SEMS"]


class Band:
    """Which pipe a glyph needs. ``None`` means it does not care."""

    IN = "in"  # CPU north wall: the input room
    OUT = "out"  # CPU west wall, below the ROM pipe: the output room
    MEM = "mem"  # CPU east wall: request out to the adapter, response in from the tape


#: Hardware micro-programs, keyed by semantic tag — the sign-biased realisation of
#: ``ARCH.md`` §6's table. Entry state for every lane is ``A`` = the operand word
#: (already fetched, §5.2) and ``B`` = ACC; ``BP`` is zero, the trie having shifted
#: the opcode out of it. ``isa.py``'s ``micro`` stays the documented abstract form
#: and drives the emulator's cost model; this is what actually gets drawn.
_HW: dict[Sem, tuple[tuple[str, str | None], ...]] = {
    Sem.NOP: (),
    # A already holds n, so LDI is a single glyph.
    Sem.SET_IMM: (("M", None),),
    Sem.INPUT: (("r", Band.IN), ("M", None)),
    # W/s/W: ACC survives for free (§6).
    Sem.OUTPUT: (("W", None), ("s", Band.OUT), ("W", None)),
    Sem.ADD_IMM: (("+", None), ("M", None)),
    # `W -` rather than `- N`: one glyph shorter, per §6's correction to SUBI.
    Sem.SUB_IMM: (("W", None), ("-", None), ("M", None)),
    Sem.MUL_IMM: (("*", None), ("M", None)),
    # `/` leaves the remainder in B; the trailing M overwrites it with the quotient.
    Sem.DIV_IMM: (("W", None), ("/", None), ("M", None)),
    Sem.MOD_IMM: (("W", None), ("%", None), ("M", None)),
    # ── memory: the operand word *is* the request (sign-biased) ───────────────
    Sem.LOAD: (("s", Band.MEM), ("r", Band.MEM), ("M", None)),
    Sem.STORE: (
        ("N", None),  # -a: the write marker
        ("s", Band.MEM),
        ("W", None),  # A = ACC, the value
        ("s", Band.MEM),
        ("W", None),  # ACC restored
    ),
    # Read-modify-write by a constant. `M` spends ACC to keep a second copy of the
    # address, which is what survives `r`; once the write marker is out the address
    # is dead and a digit glyph can safely reload A. B still holds the old value, so
    # ACC comes out as the *pre*-operation word — free, and worth an instruction at
    # every loop head (`DECM n; BRZ done`). See isa.py's family note.
    Sem.INC_MEM: (
        ("M", None),  # B = addr
        ("s", Band.MEM),  # +addr: read
        ("r", Band.MEM),  # A = store[addr]
        ("W", None),  # A = addr, B = the old value
        ("N", None),  # -addr: the write marker
        ("s", Band.MEM),
        ("1", None),  # the address is dead; A = 1 costs one cell
        ("+", None),  # A = 1 + old
        ("s", Band.MEM),
    ),
    Sem.DEC_MEM: (
        ("M", None),
        ("s", Band.MEM),
        ("r", Band.MEM),
        ("W", None),
        ("N", None),
        ("s", Band.MEM),
        ("1", None),
        ("-", None),  # A = 1 - old
        ("N", None),  # A = old - 1; cheaper than holding a negative literal
        ("s", Band.MEM),
    ),
    Sem.ADD_MEM: (("s", Band.MEM), ("r", Band.MEM), ("+", None), ("M", None)),
    Sem.SUB_MEM: (
        ("s", Band.MEM),
        ("r", Band.MEM),
        ("W", None),
        ("-", None),
        ("M", None),
    ),
    Sem.MUL_MEM: (("s", Band.MEM), ("r", Band.MEM), ("*", None), ("M", None)),
    Sem.AND_MEM: (("s", Band.MEM), ("r", Band.MEM), ("&", None), ("M", None)),
    # ACC is the address, so `W` alone puts the request in A. Operand word unused.
    Sem.LOAD_ACC: (("W", None), ("s", Band.MEM), ("r", Band.MEM), ("M", None)),
    # Source read first (its address is an immediate), so the value only becomes
    # live after the destination address has left A — no spill slot needed.
    Sem.STORE_ACC_MEM: (
        ("s", Band.MEM),  # +src: read the source slot
        ("r", Band.MEM),  # A = its value
        ("W", None),  # A = ACC = the destination
        ("N", None),  # -dest: the write marker
        ("s", Band.MEM),
        ("W", None),  # A = the value again
        ("s", Band.MEM),
        ("M", None),
    ),
    Sem.NEGATE: (("W", None), ("N", None), ("M", None)),
    Sem.HALT: (("H", None),),
}

#: Tags whose operand word is a STORE address, so it must be >= 1 (see the module
#: docstring on the sign bias).
MEMORY_SEMS = frozenset(
    {
        Sem.LOAD,
        Sem.STORE,
        Sem.INC_MEM,
        Sem.DEC_MEM,
        Sem.ADD_MEM,
        Sem.SUB_MEM,
        Sem.MUL_MEM,
        Sem.AND_MEM,
        Sem.STORE_ACC_MEM,
    }
)

#: Tags realised as a structures-band slab rather than a flat lane.
_JUMP_SEMS = frozenset({Sem.JUMP})
_BRANCH_SEMS = frozenset({Sem.BR_ZERO, Sem.BR_NEG})


def hw_micro(sem: Sem) -> tuple[tuple[str, str | None], ...]:
    """The hardware micro-program for ``sem``, or ``()`` for the structured ones."""
    return _HW.get(sem, ())


class MachineError(RuntimeError):
    """The geometry did not close — with the constraint that failed."""


# ── grid ─────────────────────────────────────────────────────────────────────
class _Grid:
    def __init__(self) -> None:
        self.c: dict[tuple[int, int], str] = {}

    def put(self, x: int, y: int, ch: str) -> None:
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise MachineError(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch

    def soft(self, x: int, y: int, ch: str) -> None:
        """Place ``ch`` only if the cell is empty (used for filler dots)."""
        if (x, y) not in self.c:
            self.c[(x, y)] = ch

    def text(self, x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            if ch != "\0":
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

    def blit(self, ox: int, oy: int, cells: dict[tuple[int, int], str]) -> None:
        for (x, y), ch in cells.items():
            self.put(ox + x, oy + y, ch)

    def pipe(self, cells: list[tuple[int, int, str]]) -> None:
        for x, y, ch in cells:
            self.put(x, y, ch)

    def draw_pipe(self, points: list[tuple[int, int]]) -> int:
        """Draw a pipe along the rectilinear polyline ``points``, in flow order.

        Arrowheads at the first cell, every bend and the last cell; ``-``/``|``
        bodies on the straights. The last cell's arrowhead is what points the pipe
        into its destination wall, so the caller only has to name corners.
        Returns the cell count, which is the pipe's capacity.
        """
        glyph = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
        cells: list[tuple[int, int]] = [points[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            if x0 != x1 and y0 != y1:
                raise MachineError(f"pipe leg {(x0, y0)}->{(x1, y1)} is not rectilinear")
            sx = (x1 > x0) - (x1 < x0)
            sy = (y1 > y0) - (y1 < y0)
            x, y = x0, y0
            while (x, y) != (x1, y1):
                x, y = x + sx, y + sy
                cells.append((x, y))
        n = len(cells)
        for i, (x, y) in enumerate(cells):
            din = (x - cells[i - 1][0], y - cells[i - 1][1]) if i else None
            dout = (cells[i + 1][0] - x, cells[i + 1][1] - y) if i < n - 1 else None
            if i == 0:
                ch = glyph[dout]
            elif i == n - 1:
                ch = glyph[din]
            elif din == dout:
                ch = "-" if dout[0] else "|"
            else:
                ch = glyph[dout]
            self.put(x, y, ch)
        return n

    def rows(self) -> list[str]:
        w = max(x for x, _ in self.c) + 1
        h = max(y for _, y in self.c) + 1
        out = ["".join(self.c.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]
        while out and not out[-1]:
            out.pop()
        return out


def _bitrev(v: int, k: int) -> int:
    return int(format(v, f"0{k}b")[::-1], 2) if k else 0


# ── opcode numbering and lane rows ───────────────────────────────────────────
@dataclass
class _Plan:
    """Which opcode gets which number and which lane row."""

    k: int
    lanes: int
    number: dict[str, int] = field(default_factory=dict)
    row: dict[str, int] = field(default_factory=dict)
    sem: dict[str, Sem] = field(default_factory=dict)


def plan(program: Program) -> _Plan:
    """Assign lane rows by pipe need, then derive opcode numbers from the trie.

    The trie sorts its leaves in **bit-reversed** order (``ARCH.md`` §2.4), so
    picking a row *chooses* the opcode number. IN goes to the top row beside the
    north pipe, OUT to the bottom row, and everything else in between — longest
    micro-program first, so the drop columns form a descending staircase and short
    lanes can turn south early instead of all walking out to a shared column.
    """
    used = [op.mnemonic for op in program.ops_used]
    sems = {op.mnemonic: op.sem for op in program.ops_used}
    unknown = [m for m in used if sems[m] not in _HW and sems[m] not in _JUMP_SEMS | _BRANCH_SEMS]
    if unknown:
        raise MachineError(f"no hardware micro-program for {unknown}")

    k = max(1, (len(used) - 1).bit_length())
    lanes = 1 << k

    def width(m: str) -> int:
        return len(hw_micro(sems[m]))

    def group(m: str) -> int:
        s = sems[m]
        if s is Sem.INPUT:
            return 0  # top, beside the north pipe
        if s is Sem.OUTPUT:
            return 2  # bottom, beside the west output pipe
        return 1

    order = sorted(used, key=lambda m: (group(m), -width(m), m))
    slots = list(range(lanes))
    placed: dict[str, int] = {}
    for m in [n for n in order if group(n) == 0]:
        placed[m] = slots.pop(0)
    for m in reversed([n for n in order if group(n) == 2]):
        placed[m] = slots.pop()
    for m in [n for n in order if group(n) == 1]:
        placed[m] = slots.pop(0)

    return _Plan(
        k=k,
        lanes=lanes,
        number={m: _bitrev(i, k) for m, i in placed.items()},
        row={m: 2 * i + 1 for m, i in placed.items()},
        sem=sems,
    )


def rom_words(program: Program, p: _Plan) -> list[int]:
    """Re-encode the program for this machine: **fixed-width two-word** instructions.

    The assembler emits ARCH's abstract form, which is *variable* width — one word
    for a zero-operand opcode. The hardware fetch is ``>rbr`` and unconditionally
    takes two, because that is what keeps every ring access inside the fetch stage
    and leaves each lane needing only its own pipe (``ARCH.md`` §5.2). Feeding it
    variable-width words desynchronises everything: ``LDI 42 / OUT / HALT`` pairs
    up as ``(LDI, 42), (OUT, HALT)`` and emits 42 forever without ever halting.

    So every instruction becomes ``opcode, operand``, with 0 where unused — and
    **the skip counts have to be rescaled**, because they were resolved in
    variable-width word positions. In the fixed-width image instruction ``k`` lives
    at word ``2k``, so a jump landing on instruction ``t`` from instruction ``k``
    discards ``2 * ((t - k - 1) mod n)`` words. Getting this off by one silently
    executes the wrong word (§5.3).

    Operands otherwise pass through: a memory operand *is* the sign-biased request
    (positive here — the lane negates it for a write) and an immediate is itself.
    """
    instrs = sorted(program.instrs, key=lambda i: i.pos)
    n = len(instrs)
    index_of_word = {ins.pos: k for k, ins in enumerate(instrs)}

    out: list[int] = []
    for k, ins in enumerate(instrs):
        if ins.sem in TARGET_SEMS:
            assert ins.operand is not None
            after = (ins.pos + ins.words) % program.P
            target_word = (after + ins.operand) % program.P
            if target_word not in index_of_word:
                raise MachineError(
                    f"{ins.mnemonic} at word {ins.pos} jumps to word {target_word}, "
                    "which is not an instruction boundary"
                )
            operand = 2 * ((index_of_word[target_word] - k - 1) % n)
        elif ins.operand is None:
            operand = 0
        else:
            operand = ins.operand
            if ins.sem in MEMORY_SEMS and operand < 1:
                raise MachineError(
                    f"{ins.mnemonic} at word {ins.pos} addresses STORE slot "
                    f"{operand}; hardware addresses start at 1 because the "
                    "operation is encoded in the address word's sign"
                )
        out += [p.number[ins.mnemonic], operand]
    return out


# ── the CPU room ─────────────────────────────────────────────────────────────
_STRUCT_X0 = 2  # slabs hug the west wall, keeping their `r` nearest the ROM pipe
_SLAB_PITCH = 13  # columns per slab: each gets its own band (see _slab)
_JUMP_SLAB_ROWS = 5
_BRANCH_SLAB_ROWS = 8


@dataclass
class _Cpu:
    """A laid-out CPU room: cells in interior coordinates, plus its port rows."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    centre: int  # fetch row; the ROM pipe enters the west wall here
    in_row: int  # the IN lane's row; the input pipe enters the west wall here
    out_col: int  # the OUT lane's `s` column; the output pipe leaves the south wall here
    mem_out_row: int  # east wall: request out to the adapter
    mem_in_row: int  # east wall: response in from the tape
    ports: dict[str, tuple[int, int, str]]  # band -> (x, y, direction) of the wall cell
    pipe_glyphs: list[tuple[int, int, str, str]]  # (x, y, glyph, band)


def _flat_lane(
    micro: tuple[tuple[str, str | None], ...], x0: int, mem_x: int, row: int
) -> dict[tuple[int, int], tuple[str, str | None]]:
    """Lay one flat lane, pushing its first MEM glyph out to ``mem_x``.

    Everything from that glyph on follows contiguously, so the whole memory block
    sits in the room's eastern band and binds to the east-wall pipes instead of to
    the ROM pipe on the west (``ARCH.md`` §7.1).
    """
    first_mem = next((i for i, (_, b) in enumerate(micro) if b == Band.MEM), None)
    out: dict[tuple[int, int], tuple[str, str | None]] = {}
    x = x0
    for i, (glyph, band) in enumerate(micro):
        if i == first_mem:
            while x < mem_x:
                out[(x, row)] = (".", None)
                x += 1
        out[(x, row)] = (glyph, band)
        x += 1
    return out


def build_cpu(program: Program, p: _Plan, *, mem_pad: int = 0) -> _Cpu:
    """Lay the CPU: fetch, decode trie, lanes, structures band, return path."""
    k, lanes = p.k, p.lanes
    centre = 1 << k
    span = 2 * lanes - 1
    lane_x0 = 5 + k
    used = list(p.number)
    by_row = {p.row[m]: m for m in used}
    all_rows = list(range(1, span + 1, 2))

    flat = {m: hw_micro(p.sem[m]) for m in used if p.sem[m] in _HW}
    structured = [m for m in used if p.sem[m] in _JUMP_SEMS | _BRANCH_SEMS]
    halting = {p.row[m] for m in used if p.sem[m] is Sem.HALT}

    prefixes = [
        next((i for i, (_, b) in enumerate(mc) if b == Band.MEM), len(mc))
        for mc in flat.values()
        if any(b == Band.MEM for _, b in mc)
    ]
    mem_x = lane_x0 + (max(prefixes) if prefixes else 0) + mem_pad

    # Each slab gets its own column band, so an exit dropping to the collector
    # never crosses a slab below it.
    slab_rows = {
        m: (_JUMP_SLAB_ROWS if p.sem[m] in _JUMP_SEMS else _BRANCH_SLAB_ROWS) for m in structured
    }
    struct_east = _STRUCT_X0 + max(1, len(structured)) * _SLAB_PITCH

    # ── lane extents ─────────────────────────────────────────────────────────
    lane_cells: dict[tuple[int, int], tuple[str, str | None]] = {}
    lane_end: dict[int, int] = {}
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            lane_end[r] = lane_x0 - 1
        elif m in flat:
            cells = _flat_lane(flat[m], lane_x0, mem_x, r)
            lane_cells.update(cells)
            lane_end[r] = max((x for x, _ in cells), default=lane_x0 - 1)
        else:
            # A structured opcode's lane is only its preamble; the rest is a slab.
            pre = "b" if p.sem[m] in _JUMP_SEMS else "W"
            lane_cells[(lane_x0, r)] = (pre, None)
            lane_end[r] = lane_x0

    # ── drop columns: strictly ordered bottom-to-top, floored east of the slabs
    drop_x: dict[int, int] = {}
    cur = struct_east + 1
    assigned: set[int] = set()
    for r in sorted(all_rows, reverse=True):
        if r in halting:
            continue
        c = max(cur, lane_end[r] + 1)
        m = by_row.get(r)
        if m is not None and m in slab_rows:
            # A slab's entry column must be unique in *both* directions: its `<`
            # turns an arriving man west, so any other drop sharing the column
            # would be swallowed by this slab's entry row.
            while c in assigned:
                c += 1
            cur = c + 1
        else:
            cur = c
        drop_x[r] = c
        assigned.add(c)

    # A deeper slab needs a larger entry column, because its drop passes through
    # every shallower slab's westbound entry row.
    order = sorted(structured, key=lambda m: drop_x[p.row[m]])
    slab_at: dict[str, int] = {}
    slab_base: dict[str, int] = {}
    y = span + 2
    for i, m in enumerate(order):
        slab_at[m] = y
        slab_base[m] = _STRUCT_X0 + i * _SLAB_PITCH
        y += slab_rows[m]
    collector = y

    g = _Grid()
    pipe_glyphs: list[tuple[int, int, str, str]] = []

    def emit(x: int, yy: int, glyph: str, band: str | None) -> None:
        g.put(x, yy, glyph)
        if glyph in "rs" and band:
            pipe_glyphs.append((x, yy, glyph, band))

    # ── fetch: opcode -> BP, then the operand word -> A (fixed width, §5.2) ──
    g.text(1, centre, ">rbr")
    pipe_glyphs += [(2, centre, "r", "rom"), (4, centre, "r", "rom")]

    # ── decode trie: one `x` per level, `]` shifting BP on each branch ────────
    def trie(level: int, row: int) -> None:
        col, step = 4 + level, 1 << (k - level)
        g.put(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                g.put(col, row + sign * d, ">" if d == step else ("]" if d == 1 else "."))
            if level < k:
                trie(level + 1, row + sign * step)

    trie(1, centre)

    for (x, yy), (glyph, band) in lane_cells.items():
        emit(x, yy, glyph, band)
    for r in all_rows:
        if r in halting:
            continue
        for x in range(lane_end[r] + 1, drop_x[r]):
            g.soft(x, r, ".")

    # ── drops: simple lanes to the collector, structured ones to their slab ──
    # Only the *head* of a drop is a `v`; the rest is `.`. A southbound man keeps
    # his heading over a `.`, and so does a westbound one — which is what lets a
    # drop cross a slab's westbound entry row at all. A `v` there would turn the
    # entry man south into the middle of the drop.
    for r in all_rows:
        if r in halting:
            continue
        g.put(drop_x[r], r, "v")
    for r in all_rows:
        if r in halting:
            continue
        m = by_row.get(r)
        stop = slab_at[m] if (m is not None and m in slab_at) else collector
        for yy in range(r + 1, stop):
            g.soft(drop_x[r], yy, ".")

    # ── structures band ──────────────────────────────────────────────────────
    struct_drops: set[int] = set()
    for m in order:
        struct_drops |= _slab(g, m, p, slab_at[m], slab_base[m], collector, pipe_glyphs)

    # Entry rows, drawn last: `soft` leaves every crossing drop's `.` in place and
    # only fills the genuinely free cells with `<`.
    for m in order:
        s0, dx = slab_at[m], drop_x[p.row[m]]
        base = slab_base[m]
        g.put(dx, s0, "<")
        for x in range(base + 1, dx):
            g.soft(x, s0, "<")
        g.put(base, s0, "v")

    # ── collector -> west riser -> back into the fetch cell ──────────────────
    ret_x = max([*drop_x.values(), *struct_drops])
    for x in range(3, ret_x + 1):
        g.soft(x, collector, "<")
    g.put(1, collector, "^")
    for yy in range(centre + 1, collector):
        g.soft(1, yy, ".")
    # The man spawns *on* the collector row: he starts facing east, the `<` beside
    # him turns him straight back west, and he joins the return path to the fetch
    # site. A dedicated spawn row below the collector would just walk him into the
    # east wall, since nothing down there steers him.
    g.put(2, collector, "@")

    if struct_east >= min(drop_x.values(), default=struct_east + 1):
        raise MachineError(
            f"slabs reach column {struct_east} but the westmost drop column is "
            f"{min(drop_x.values())}: a simple lane's drop would cross a slab"
        )

    width = ret_x + 1
    height = collector
    mem_rows = sorted(
        r
        for r in all_rows
        if by_row.get(r) in flat and any(b == Band.MEM for _, b in flat[by_row[r]])
    )
    mem_out_row = mem_rows[len(mem_rows) // 2] if mem_rows else centre
    in_rows = [p.row[m] for m in used if p.sem[m] is Sem.INPUT]
    out_cols = [lane_x0 + 1]
    return _Cpu(
        cells=g.c,
        width=width,
        height=height,
        centre=centre,
        in_row=in_rows[0] if in_rows else 1,
        out_col=out_cols[0],
        mem_out_row=mem_out_row,
        # The response pipe attaches *above* the request pipe: it comes back over
        # the top of the adapter, so attaching below would make it cross the
        # request pipe's row on the way down.
        #
        # The gap is 4 rows, not 2, and that matters. The adapter's top wall sits
        # at ``mem_out_row - 2``; with a 2-row gap the response pipe's final
        # westward leg would run along exactly that row, one cell west of the
        # adapter's corner — and since a pipe may attach at a corner (§7.4b), the
        # engine then reads those two cells as a *second*, spurious adapter -> CPU
        # pipe. The CPU's memory `r` binds to it and reads the adapter's op words
        # instead of the tape's answers.
        mem_in_row=max(mem_out_row - 4, 1),
        ports={},
        pipe_glyphs=pipe_glyphs,
    )


def _slab(
    g: _Grid,
    mnemonic: str,
    p: _Plan,
    s0: int,
    base: int,
    collector: int,
    pipe_glyphs: list[tuple[int, int, str, str]],
) -> set[int]:
    """Draw one structures-band slab below its entry row. Returns its drop columns.

    The entry row ``s0`` itself is drawn by the caller, which knows every column
    that has to pass through it.

    A jump is just the discard loop: ``BP`` already holds ``n`` from the lane's
    ``b``, so the loop drops ``n`` words and rejoins.

    A branch is the ``X`` fan-out. Entered heading east with ``A`` = ACC and
    ``B`` = n (the lane's ``W``), ``X`` turns by ``sign(ACC)`` — so negative,
    zero and positive land on three different cells and ``BRZ``/``BRN`` are the
    same hardware with the lanes relabelled (§6). Each arm restores ``A`` = n,
    ``B`` = ACC with its own ``W``; the taken arm then loads ``BP`` and discards,
    and the other two go straight back to the fetch site.
    """
    sem = p.sem[mnemonic]
    g.soft(base, s0 + 1, ".")

    if sem in _JUMP_SEMS:
        _discard_loop(g, base, s0 + 2, pipe_glyphs)
        exit_col = base + 2
        g.put(exit_col, s0 + 2, "v")
        for y in range(s0 + 3, collector):
            g.soft(exit_col, y, ".")
        return {exit_col}

    g.put(base, s0 + 2, ">")
    g.put(base + 1, s0 + 2, "X")
    g.put(base + 1, s0 + 1, ">")  # ACC < 0: CCW from east is north
    g.put(base + 1, s0 + 3, ">")  # ACC > 0: CW from east is south
    rows = {"neg": s0 + 1, "zero": s0 + 2, "pos": s0 + 3}
    # Arms are spaced 3 apart so the taken arm's 2-wide loop plus its exit column
    # never land on another arm's drop. Higher arm -> further east, so a drop never
    # crosses the eastward run of an arm below it.
    cols = {"neg": base + 9, "zero": base + 6, "pos": base + 3}
    taken = "zero" if sem is Sem.BR_ZERO else "neg"
    loop_y = s0 + 5
    drops: set[int] = set()

    turn_row = s0 + 4
    for arm, row in rows.items():
        x = base + 2
        g.put(x, row, "W")
        x += 1
        if arm == taken:
            g.put(x, row, "b")
            x += 1
        for c in range(x, cols[arm]):
            g.soft(c, row, ".")
        g.put(cols[arm], row, "v")
        end = turn_row if arm == taken else collector
        for y in range(row + 1, end):
            g.soft(cols[arm], y, ".")
        if arm != taken:
            drops.add(cols[arm])

    # The taken arm turns west along `turn_row` and runs back to the slab's west
    # edge, where the discard loop sits. That is not tidiness: the loop's `r` has to
    # be the nearest thing to the *ROM* pipe on the west wall, and left where the
    # arm drops it, it ties with the tape's response pipe on the east wall — a tie
    # the engine breaks by reading order, so the jump silently blocks on an empty
    # pipe forever (§7.1: nearest, not nearest-that-can-proceed).
    g.put(cols[taken], turn_row, "<")
    for c in range(base + 1, cols[taken]):
        g.soft(c, turn_row, ".")
    g.put(base, turn_row, "v")

    _discard_loop(g, base, loop_y, pipe_glyphs)
    exit_col = base + 2
    g.put(exit_col, loop_y, "v")
    for y in range(loop_y + 1, collector):
        g.soft(exit_col, y, ".")
    drops.add(exit_col)
    return drops


def _discard_loop(g: _Grid, x: int, y: int, pipe_glyphs: list[tuple[int, int, str, str]]) -> None:
    """A ``b``-counted loop that discards one ROM word per lap.

    ``circuit.counted_loop``'s shape, inlined. It **tests before the body**, so a
    count of 0 runs it zero times — which is exactly what a not-taken branch and a
    ``JMPF 0`` both need::

        (x,y)=`>`   (x+1,y)=`d`     d: BP>0 -> south into the body, 0 -> east, out
        (x,y+1)=`m` (x+1,y+1)=`r`   r discards a word, m decrements on the way back
        (x,y+2)=`^` (x+1,y+2)=`<`

    The `r` sits near the west wall, which is what makes it bind to the ROM pipe
    rather than to the input or STORE pipes (§7.1).
    """
    g.put(x, y, ">")
    g.put(x + 1, y, "d")
    g.put(x, y + 1, "m")
    g.put(x + 1, y + 1, "r")
    pipe_glyphs.append((x + 1, y + 1, "r", "rom"))
    g.put(x, y + 2, "^")
    g.put(x + 1, y + 2, "<")


# ── the adapter: sign-biased request -> the tape's real wire protocol ────────
#: ``+a`` becomes ``0 a`` (read); ``-a`` becomes ``1 a`` followed by the value word
#: passed straight through. Two pipes only — one in from the CPU, one out to the
#: tape — so every ``r``/``s`` in here binds unambiguously whatever the geometry.
#:
#: ``r`` then ``X``: a positive word turns him clockwise (south, the read arm), a
#: negative one counter-clockwise (north, the write arm). Zero cannot occur, which
#: is precisely why hardware addresses start at 1.
#:
#: The ``>`` in front of MAIN's ``r`` is load-bearing, not decoration: the return
#: leg arrives up column 1 heading *north*, and a man who executes ``r`` while
#: heading north steps north again — out through the roof. A port is (cell,
#: heading), never just a cell (``ARCH.md`` §7.2).
_ADAPTER = [
    "..>M1sWNsrs.v",  # write: B=w; A=1; send 1; A=w; A=-w=a; send a; pass the value
    ">rX.........v",  # main: turn east, read one request word, branch on its sign
    "..>M0sWs....v",  # read: B=w; A=0; send 0; A=w=a; send a
    "^..........@<",  # return leg; the spawn joins it just before MAIN
]
ADAPTER_W = len(_ADAPTER[0])
ADAPTER_H = len(_ADAPTER)
ADAPTER_IN_ROW = 2  # west wall: the request pipe from the CPU
ADAPTER_OUT_ROW = 2  # east wall: the expanded request out to the tape


def adapter_cells() -> dict[tuple[int, int], str]:
    """The adapter's interior cells, local (1,1)-based."""
    out: dict[tuple[int, int], str] = {}
    for y, row in enumerate(_ADAPTER, start=1):
        for x, ch in enumerate(row, start=1):
            if ch != " ":
                out[(x, y)] = ch
    return out


# ── the tape, as a STORE block ───────────────────────────────────────────────
@dataclass
class _Tape:
    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]  # where the request pipe must arrive, pointing east
    out_cell: tuple[int, int]  # where the response pipe leaves, pointing north


def tape_block(n: int) -> _Tape:
    """``memory_tape``'s verified rotating-pipe tape, wired for use as STORE.

    ``memory_tape.assemble_v2`` builds the tape as a standalone answer to the
    ``memory`` problem, so it comes with its own ``I`` and ``O`` rooms. A program
    may have at most one of each and the CPU owns them, so those two rooms are
    replaced here by pipe stubs the caller extends. Everything else — worker,
    relay, folded ring — is untouched, which is the point: it is measured hardware
    (``ARCH.md`` §4.1) and this must not perturb it.

    ``n`` is the slot count. Footprint is 32×32 whatever ``n`` is and cost is
    ``~105 + 8.3n`` ticks per access, so ``n`` is sized to the program's actual
    high address and there is no trade-off to weigh.
    """
    from ..circuit import Circuit
    from ..memory_tape import (
        RELAY,
        V2_FWD_ROW,
        V2_IH,
        V2_IN_ROW,
        V2_IW,
        V2_OUT_COL,
        V2_RET_COL,
        _draw_pipe,
        worker_v2,
    )

    for fold in (0, 2, 4, 6, 8, 10, 12):
        g = Circuit(400, 200)
        wk = worker_v2(n)
        WX, WY = 8, 8
        for (x, y), ch in wk.cell.items():
            g.set(WX + x, WY + y, ch)
        for x in range(-1, V2_IW + 1):
            g.set(WX + x, WY - 1, "+" if x in (-1, V2_IW) else "-")
            g.set(WX + x, WY + V2_IH, "+" if x in (-1, V2_IW) else "-")
        for y in range(V2_IH):
            g.set(WX - 1, WY + y, "|")
            g.set(WX + V2_IW, WY + y, "|")

        # request stub: two cells pointing east into the worker's left wall
        iy = WY + V2_IN_ROW
        g.set(WX - 3, iy, ">")
        g.set(WX - 2, iy, ">")
        # response stub: two cells climbing north out of the worker's top wall
        ox = WX + V2_OUT_COL
        g.set(ox, WY - 2, "^")
        g.set(ox, WY - 3, "^")

        bottom_y = WY + V2_IH
        fy = WY + V2_FWD_ROW
        ret_col = WX + V2_RET_COL
        east = WX + V2_IW + 2
        b_fwd = bottom_y + 6
        r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
        relay_y = bottom_y + 3
        for i, row in enumerate(RELAY):
            for j, ch in enumerate(row):
                g.set(1 + j, relay_y + i, ch)
        relay_wall = len(RELAY[0])
        adj = relay_wall + 1

        n_fwd = _draw_pipe(g, [(WX + V2_IW + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)])
        n_ret = _draw_pipe(
            g,
            [
                (adj, r_a),
                (east - 1, r_a),
                (east - 1, r_b),
                (adj + fold, r_b),
                (adj + fold, r_c),
                (ret_col, r_c),
                (ret_col, bottom_y + 1),
            ],
        )
        if n_fwd + n_ret < n + 1:
            continue
        cells = {k: v for k, v in g.cell.items() if v != " "}
        return _Tape(
            cells=cells,
            width=max(x for x, _ in cells) + 1,
            height=max(y for _, y in cells) + 1,
            in_cell=(WX - 3, iy),
            out_cell=(ox, WY - 3),
        )
    raise MachineError(f"no fold gives the tape {n + 1} slots")


# ── pipe binding: the §7.1 safety net, before the interpreter sees anything ──
def check_bindings(
    glyphs: list[tuple[int, int, str, str]], touches: dict[str, tuple[int, int]]
) -> None:
    """Assert every ``r``/``s`` is strictly nearest the pipe it is meant to use.

    ``s`` targets the nearest *outgoing* pipe and ``r`` the nearest *incoming* one,
    Manhattan, ties by reading order — and *nearest*, not nearest-that-can-proceed
    (``ARCH.md`` §7.1). Getting this wrong is invisible until a program silently
    reads the wrong pipe, so it is checked here and again with
    ``tools/route-check.mjs`` on the real grid.
    """
    incoming = {"rom", "in", "mem_resp"}
    for x, y, glyph, band in glyphs:
        if band == Band.MEM:
            want = "mem_req" if glyph == "s" else "mem_resp"
        else:
            want = band  # "rom", Band.IN == "in", Band.OUT == "out"
        rivals = {
            name: abs(px - x) + abs(py - y)
            for name, (px, py) in touches.items()
            if (name in incoming) == (glyph == "r")
        }
        if want not in rivals:
            raise MachineError(f"{glyph!r} at {(x, y)} wants pipe {want!r}, which is absent")
        best = min(rivals.values())
        if rivals[want] != best or sum(1 for d in rivals.values() if d == best) > 1:
            order = sorted(rivals.items(), key=lambda kv: kv[1])
            raise MachineError(
                f"{glyph!r} at {(x, y)} must bind {want!r} but distances are {order}"
            )


# ── whole-machine assembly ───────────────────────────────────────────────────
@dataclass
class Machine:
    """A generated machine: its grid, its score inputs, and how it was sized."""

    rows: list[str]
    program: Program
    plan: _Plan
    tape_n: int
    rom_rows: int
    mem_pad: int

    @property
    def width(self) -> int:
        return max(len(r) for r in self.rows)

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def report(self) -> str:
        return (
            f"{self.program.name}: {self.width}x{self.height} "
            f"footprint {self.footprint}, {len(self.plan.number)} opcodes "
            f"(depth {self.plan.k}), P={self.program.P} words on {self.rom_rows} ROM rows, "
            f"tape N={self.tape_n}, mem_pad={self.mem_pad}"
        )


def _highest_address(program: Program) -> int:
    return max(
        (i.operand for i in program.instrs if i.sem in MEMORY_SEMS and i.operand is not None),
        default=1,
    )


def build(
    program: Program,
    *,
    tape_n: int | None = None,
    rom_rows: int | None = None,
    mem_pad: int | None = None,
) -> Machine:
    """Assemble the whole machine for ``program``.

    ``tape_n`` defaults to the program's highest *static* address, which is wrong
    for any program that computes addresses at runtime (``LDA``/``MOVA``), so those
    must pass it explicitly. ``mem_pad`` is searched: it shifts the memory block
    east until every pipe glyph binds where it should (§7.1).
    """
    p = plan(program)
    words = rom_words(program, p)
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1

    pads = [mem_pad] if mem_pad is not None else range(0, 40)
    last: MachineError | None = None
    for pad in pads:
        try:
            return _assemble(program, p, words, tape_n, rom_rows, pad)
        except MachineError as exc:
            last = exc
    raise MachineError(f"no mem_pad makes every pipe bind; last: {last}")


def _assemble(
    program: Program,
    p: _Plan,
    words: list[int],
    tape_n: int,
    rom_rows: int | None,
    mem_pad: int,
) -> Machine:
    cpu = build_cpu(program, p, mem_pad=mem_pad)
    W, H = cpu.width, cpu.height

    # ROM folded to roughly the CPU's own width, so neither dimension runs away
    # from the other (footprint is max(w, h)^2, ARCH.md §7.4).
    nrows = (
        rom_rows
        if rom_rows is not None
        else rommod.rows_for_budget(len(words), rommod.digit_width(words), max(40, W))
    )
    romlay = rommod.build_rom(words, rows=nrows)

    g = _Grid()

    # ── ROM room, top-left ───────────────────────────────────────────────────
    RX, RY = 0, 0
    g.room(RX, RY, RX + romlay.width, RY + romlay.height + 1)
    g.blit(RX, RY + 1, romlay.cells)
    rom_bottom = RY + romlay.height + 1

    # ── CPU room ─────────────────────────────────────────────────────────────
    # The west margin carries two pipes that must not cross: the ROM corridor runs
    # down column 1, west of the I room, and only turns east on the fetch row —
    # which is far below the I room, so the two never meet.
    CX, CY = 9, rom_bottom + 6
    g.room(CX, CY, CX + W + 1, CY + H + 1)
    g.blit(CX, CY, cpu.cells)

    # ── ROM -> CPU west wall at the fetch row ────────────────────────────────
    # Down the corridor west of the CPU, then east into the wall. The ROM has a
    # single outgoing pipe, so which of its `s` glyphs is nearest does not matter.
    fetch_y = CY + cpu.centre
    g.draw_pipe([(1, rom_bottom + 1), (1, fetch_y), (CX - 1, fetch_y)])

    # ── I room -> CPU west wall at the IN lane's row ─────────────────────────
    # The input pipe goes on the *west* wall too, far from every memory glyph: that
    # east/west separation is what keeps a memory `r` bound to the tape's response
    # pipe rather than to this one, whatever row it sits on.
    iy = CY + cpu.in_row
    g.room(3, iy - 1, 5, iy + 1)
    g.put(4, iy, "I")
    g.draw_pipe([(6, iy), (CX - 1, iy)])

    # ── CPU south wall -> O room ─────────────────────────────────────────────
    oy = CY + H + 2
    g.draw_pipe([(CX + cpu.out_col, oy), (CX + cpu.out_col, oy + 1)])
    g.room(CX + cpu.out_col - 1, oy + 2, CX + cpu.out_col + 1, oy + 4)
    g.put(CX + cpu.out_col, oy + 3, "O")

    # ── adapter, east of the CPU ─────────────────────────────────────────────
    AX = CX + W + 4
    # Aligned so the request pipe leaves the CPU beside the memory lanes, but never
    # so high that the response pipe's westward leg grazes the adapter's top corner.
    # A small machine (few lanes, no memory) is the case that needs the clamp.
    AY = max(CY + cpu.mem_out_row - ADAPTER_IN_ROW, CY + cpu.mem_in_row + 3)
    resp_row_check = CY + cpu.mem_in_row
    if resp_row_check >= AY - 1:
        raise MachineError(
            f"response row {resp_row_check} is not clear of the adapter's top wall "
            f"at {AY}: its westward leg would touch the adapter's corner and the "
            "engine would read a second, spurious pipe into the CPU"
        )
    g.room(AX, AY, AX + ADAPTER_W + 1, AY + ADAPTER_H + 1)
    g.blit(AX, AY, adapter_cells())
    req_row = AY + ADAPTER_IN_ROW
    g.draw_pipe([(CX + W + 2, req_row), (AX - 1, req_row)])

    # ── tape, east of the adapter ────────────────────────────────────────────
    tape = tape_block(tape_n)
    TX = AX + ADAPTER_W + 6
    TY = CY
    g.blit(TX, TY, tape.cells)

    # adapter east wall -> the tape's request stub
    tin_x, tin_y = TX + tape.in_cell[0], TY + tape.in_cell[1]
    ax_out = AX + ADAPTER_W + 2
    mid = ax_out + 2
    g.draw_pipe(
        [
            (ax_out, AY + ADAPTER_OUT_ROW),
            (mid, AY + ADAPTER_OUT_ROW),
            (mid, tin_y),
            (tin_x - 1, tin_y),
        ]
    )

    # the tape's response stub -> CPU east wall. It climbs clear of both rooms
    # before running back west, so it crosses neither the request pipe nor the tape.
    tout_x, tout_y = TX + tape.out_cell[0], TY + tape.out_cell[1]
    resp_row = CY + cpu.mem_in_row
    top = min(AY, CY) - 3
    g.draw_pipe(
        [
            (tout_x, tout_y - 1),
            (tout_x, top),
            (CX + W + 3, top),
            (CX + W + 3, resp_row),
            (CX + W + 2, resp_row),
        ]
    )

    rows = g.rows()
    touches = {
        "rom": (CX - 1, CY + cpu.centre),
        "in": (CX - 1, iy),
        "out": (CX + cpu.out_col, CY + H + 2),
        "mem_req": (CX + W + 2, req_row),
        "mem_resp": (CX + W + 2, resp_row),
    }
    check_bindings(
        [(CX + x, CY + y, glyph, band) for x, y, glyph, band in cpu.pipe_glyphs], touches
    )
    # Five pipes at the CPU plus the tape's own two ring pipes and one into it: any
    # other count means the grid is geometrically ambiguous somewhere — usually a
    # leg running alongside a room corner, which the engine reads as an extra pipe
    # (see mem_in_row). Cheap to check and it localises a whole class of bug.
    _check_pipe_count(rows, expected=8)
    return Machine(
        rows=rows,
        program=program,
        plan=p,
        tape_n=tape_n,
        rom_rows=romlay.rows_used,
        mem_pad=mem_pad,
    )


def _check_pipe_count(rows: list[str], *, expected: int) -> None:
    """Assert the engine finds exactly the pipes the generator drew.

    A pipe leg that runs alongside a room's corner is legal (``ARCH.md`` §7.4b
    verified that pipes may attach at corners) and therefore *silent* — the engine
    simply sees one more pipe than intended, an instruction binds to it, and the
    program reads the wrong words. Counting is a one-line way to catch the whole
    family.
    """
    from ..littleman import Littleman, LittlemanError

    try:
        found = len(Littleman().analyze("\n".join(rows)).pipes)
    except LittlemanError as exc:  # pragma: no cover - engine unavailable
        raise MachineError(f"could not analyse the grid: {exc}") from exc
    if found != expected:
        raise MachineError(
            f"engine sees {found} pipes, generator drew {expected}: some leg is "
            "geometrically ambiguous (a corner attachment, most likely)"
        )


#: Tape size per problem, from the *constraints* rather than the public data:
#: ``tcp`` allows n=48, so addresses reach BUF+47 = 51 even though no public case
#: goes past 35. ``ARCH.md`` §4.1: footprint is 32x32 whatever N is and cost is
#: ~105 + 8.3N ticks, so there is no trade-off — just size it to the real maximum.
# Sized to the highest address each program actually reaches, and it matters more
# than ARCH.md §4.1 implies: the tape is a *rotating* ring, so a request waits for
# its slot to come round. Measured on the real engine, one extra slot costs ~114
# ticks per case on brackets and ~999 on tcp — the difference being how many
# accesses each program makes, not the footprint (the tape block is 33 columns
# wide at every N). brackets reaches address 4, tcp reaches BUF+47 = 51.
TAPE_SIZE = {"brackets": 5, "tcp": 51}


def build_for(slug: str) -> Machine:
    """Generate the machine for a checked-in task program."""
    from . import programs

    if slug not in TAPE_SIZE:
        raise MachineError(f"no tape size recorded for {slug!r}; have {sorted(TAPE_SIZE)}")
    return build(programs.load(slug), tape_n=TAPE_SIZE[slug])


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug", choices=sorted(TAPE_SIZE), help="task program to synthesise")
    ap.add_argument("--out", help="write the grid here instead of stdout")
    ap.add_argument("--report", action="store_true", help="print the size report to stderr")
    args = ap.parse_args(argv)

    m = build_for(args.slug)
    if args.report:
        import sys as _sys

        print(m.report(), file=_sys.stderr)
    text = "\n".join(m.rows) + "\n"
    if args.out:
        from pathlib import Path as _Path

        _Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
