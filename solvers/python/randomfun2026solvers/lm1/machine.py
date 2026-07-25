#!/usr/bin/env python3
"""Synthesise a whole LM-1 machine from an assembled program.

``ARCH.md`` §7.5's decision, carried out: *there is no "the LM-1 CPU" — there is a
CPU synthesiser, and each task gets its own instance.* Feed this an
:class:`~randomfun2026solvers.lm1.asm.Program` and it emits the smallest machine
that runs it: a looping ROM sized to the word count, a decode trie of depth
``ceil(log2 |opcodes used|)``, one lane per opcode actually used, and only the
blocks the program touches.

``synth.py`` was the first instance (7 opcodes, straight-line only, single-digit
ROM words). This one adds the four things the graded problems need — control flow,
multi-digit words, addressed memory, and the LM-75 — and is what runs ``brackets``,
``tcp``, ``gradebook``, ``plotter`` and ``palette``.

Machine shape
-------------

::

      ROM  (looping, rom.py)
       |
       v west                  I --- west --->
    +--------------------------+
    |  CPU: >rbr, trie, lanes  |--- east --->  ADAPTER ---> TAPE
    |       structures band    |<-- east ----------------------'
    +--------------------------+
       |  |  |
       v  v  v south      O, or the LM-75's DATA / ADDR / SWAP — never both,
                          since a display problem may emit no program output

The ``I`` and ``O`` rooms are drawn only when a lane actually uses them: an unused
pipe is not merely dead weight, it still competes for every ``r``/``s`` in the room
(§7.1 is nearest, not nearest-useful). A program that writes both the panel and the
``O`` room is refused outright — a display-judged problem emitting program output has
already failed (``SPEC.md``), and the ``O`` room stands where the port pipes turn.

Four design points carry all the weight.

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

**A display port is a place, not a number.** Which port a write hits is decided by
the *side* of the panel its pipe attaches to, and which pipe an ``s`` uses is decided
by where the glyph sits (``ARCH.md`` §7.1) — so ``DSP p``, taking the port from a
word, is unbuildable. Three opcodes get three lanes, each with its own column in the
lane band, and each pipe leaves the south wall **in that same column**, which makes
every ``s`` strictly nearest its own port by exactly the columns between them. The
three pipes then fan around the panel without crossing, which pins both the column
order and the routing — see :func:`_display`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import rom as rommod
from .asm import Program
from .isa import TARGET_SEMS, Isa, Micro, Sem

__all__ = [
    "Band",
    "Machine",
    "build",
    "build_for",
    "display_for",
    "hw_micro",
    "image_program",
    "DSP_BANDS",
    "DSP_SEM_BAND",
    "MEMORY_SEMS",
    "ROM_ROWS",
    "TAPE_SIZE",
]


class Band:
    """Which pipe a glyph needs. ``None`` means it does not care."""

    IN = "in"  # CPU north wall: the input room
    OUT = "out"  # CPU west wall, below the ROM pipe: the output room
    MEM = "mem"  # CPU east wall: request out to the adapter, response in from the tape
    # CPU south wall: the three LM-75 ports. `DSP p` cannot be built — it picks a
    # pipe from its *operand*, and which pipe an `s` talks to is a static property
    # of where the glyph sits (§7.1) — so each port gets its own opcode, its own
    # lane and its own pipe. Which *side of the display* a pipe lands on is what
    # makes it ADDR / DATA / SWAP (``SPEC.md`` § The LM-75 display).
    DSP_ADDR = "dsp_addr"  # display top wall
    DSP_DATA = "dsp_data"  # display left wall
    DSP_SWAP = "dsp_swap"  # display bottom wall


#: The display bands, west to east in the CPU's lane band — which is also the order
#: their pipes leave the south wall, and it is load-bearing: DATA turns west round
#: the display, ADDR drops straight into its top, SWAP turns east and runs under it,
#: so a westward leg never has to cross a column belonging to a lane further west.
DSP_BANDS: tuple[str, ...] = (Band.DSP_DATA, Band.DSP_ADDR, Band.DSP_SWAP)

#: Columns between one display lane's ``s`` and the next. It must exceed the row
#: gap between neighbouring lanes (2), or an ``s`` binds its neighbour's pipe:
#: every display pipe attaches to the south wall directly below its own ``s``, so
#: the Manhattan tie-break is ``column separation`` vs ``row separation`` (§7.1).
_DSP_PITCH = 6


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
    # Exactly SUB_MEM's shape: `/` is native, and the `W` is there for the same
    # reason — the divisor arrives in A from the tape and the dividend is ACC in B.
    # `/` leaves the remainder in B; the trailing `M` overwrites it.
    Sem.DIV_MEM: (
        ("s", Band.MEM),
        ("r", Band.MEM),
        ("W", None),
        ("/", None),
        ("M", None),
    ),
    Sem.AND_MEM: (("s", Band.MEM), ("r", Band.MEM), ("&", None), ("M", None)),
    # ── indexed memory: `LDP`/`STP`, and *no SPILL block* ─────────────────────
    # ``isa.py`` gives both of these a spill slot, because in the ``0 addr`` /
    # ``1 addr value`` wire protocol the request-opening literal clobbers A while B
    # still holds ACC (``ARCH.md`` §6.1's "real hole in §5.1"). The sign-biased
    # request closes that hole: the operation rides in the address word's sign, so
    # there is no literal to clobber anything and a pointer never has to be parked.
    #
    # LDP: send +a, take the pointer straight back into A, send it *as* the next
    # request — one glyph shorter than a spill round trip, and it keeps the whole
    # lane on the east bus.
    Sem.LOAD_IND: (
        ("s", Band.MEM),  # +a
        ("r", Band.MEM),  # A = ptr
        ("s", Band.MEM),  # +ptr (A is already the request)
        ("r", Band.MEM),  # A = store[ptr]
        ("M", None),
    ),
    # STP: `N` turns the pointer into the write marker while ACC waits in B, then
    # one `W` hands the value over — the pointer is dead by then, so the two are
    # never live at once. ACC survives (the trailing `M` re-lands it in B).
    Sem.STORE_IND: (
        ("s", Band.MEM),  # +a
        ("r", Band.MEM),  # A = ptr
        ("N", None),  # A = -ptr: the write marker
        ("s", Band.MEM),
        ("W", None),  # A = ACC, the value
        ("s", Band.MEM),
        ("M", None),  # B = the value = ACC, unchanged
    ),
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
    # ── LM-75 ports: the `W`/`s`/`W` sandwich, so ACC survives (as with OUT) ───
    Sem.DISPLAY_ADDR: (("W", None), ("s", Band.DSP_ADDR), ("W", None)),
    Sem.DISPLAY_DATA: (("W", None), ("s", Band.DSP_DATA), ("W", None)),
    Sem.DISPLAY_SWAP: (("W", None), ("s", Band.DSP_SWAP), ("W", None)),
    Sem.HALT: (("H", None),),
}

#: Tags that drive an LM-75 port, keyed to the band (hence the pipe) each needs.
DSP_SEM_BAND: dict[Sem, str] = {
    Sem.DISPLAY_ADDR: Band.DSP_ADDR,
    Sem.DISPLAY_DATA: Band.DSP_DATA,
    Sem.DISPLAY_SWAP: Band.DSP_SWAP,
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
        Sem.DIV_MEM,
        Sem.AND_MEM,
        Sem.STORE_ACC_MEM,
        Sem.LOAD_IND,
        Sem.STORE_IND,
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

    def room(self, x0: int, y0: int, x1: int, y1: int, *, h: str = "-", v: str = "|") -> None:
        """A room's walls. ``h``/``v`` are ``=``/``:`` for an LM-75 display room."""
        for x in range(x0 + 1, x1):
            self.put(x, y0, h)
            self.put(x, y1, h)
        for y in range(y0 + 1, y1):
            self.put(x0, y, v)
            self.put(x1, y, v)
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
    north pipe, OUT to the bottom row, the LM-75 lanes just above it beside the
    south wall their pipes leave from, and everything else in between — longest
    micro-program first, so the drop columns form a descending staircase and short
    lanes can turn south early instead of all walking out to a shared column.
    """
    used = [op.mnemonic for op in program.ops_used]
    sems = {op.mnemonic: op.sem for op in program.ops_used}
    unknown = [m for m in used if sems[m] not in _HW and sems[m] not in _JUMP_SEMS | _BRANCH_SEMS]
    if unknown:
        raise MachineError(f"no hardware micro-program for {unknown}")

    tags = set(sems.values())
    if Sem.OUTPUT in tags and tags & set(DSP_SEM_BAND):
        raise MachineError(
            "this program writes to both the output room and the display; a "
            "display-judged problem must emit no program output (SPEC.md), and the "
            "O room would sit in the corridor the panel's pipes use"
        )

    k = max(1, (len(used) - 1).bit_length())
    lanes = 1 << k

    def width(m: str) -> int:
        return len(hw_micro(sems[m]))

    def group(m: str) -> int:
        s = sems[m]
        if s is Sem.INPUT:
            return 0  # top, beside the north pipe
        if s is Sem.OUTPUT:
            return 3  # bottom, beside the south output pipe
        if s in DSP_SEM_BAND:
            return 2  # just above it, beside the south wall the LM-75 pipes leave
        return 1

    def rank(m: str) -> tuple[int, int, str]:
        s = sems[m]
        if s in DSP_SEM_BAND:
            # Not by width (all three are `W s W`): by band, so the westmost pipe
            # belongs to the lane placed furthest from the wall. See DSP_BANDS.
            return (2, DSP_BANDS.index(DSP_SEM_BAND[s]), m)
        return (group(m), -width(m), m)

    order = sorted(used, key=rank)
    slots = list(range(lanes))
    placed: dict[str, int] = {}
    for m in [n for n in order if group(n) == 0]:
        placed[m] = slots.pop(0)
    for g in (3, 2):
        for m in reversed([n for n in order if group(n) == g]):
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


def image_program(program: Program, p: _Plan | None = None) -> Program:
    """The ROM image as an executable :class:`Program` — the bytecode, not the source.

    :func:`rom_words` renumbers every opcode (the trie's leaf order *is* the
    numbering) and rescales every jump into the fixed-width image, so what the
    generated hardware executes is not the word list the assembler produced. This
    wraps that image in an ISA where **every** opcode takes an operand word, which
    is exactly the ``>rbr`` fetch's behaviour, so
    :class:`~.emulator.Emulator` can run the image itself and a test can compare
    its ports and output against the source program's.

    Without this the emulator only ever proves the *source* correct, and a bug in
    the renumbering or in the skip-count rescaling shows up first as a wrong
    picture on the real interpreter.
    """
    p = p if p is not None else plan(program)
    words = rom_words(program, p)
    ops = []
    for mnemonic, number in p.number.items():
        op = program.isa.by_mnemonic(mnemonic)
        # The fetch takes two words whatever the opcode, so a zero-operand lane
        # still consumes (and clobbers A with) the padding word.
        micro = op.micro if op.operands else (Micro.RING_READ, *op.micro)
        ops.append(op.model_copy(update={"code": number, "operands": 1, "micro": micro}))
    return Program(
        name=f"{program.name}-image",
        isa=Isa(name=f"{program.isa.name}-fixed", ops=tuple(ops)),
        words=tuple(words),
        instrs=(),
        labels={},
        equs={},
    )


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
    # An unused room is not just dead weight: its pipe still competes for every
    # `r`/`s` in the CPU (§7.1 is nearest, not nearest-useful), so it is not drawn.
    has_in: bool = True  # False when no lane reads the input room
    has_out: bool = True  # False on a display problem: no `O` room at all
    dsp_cols: dict[str, int] = field(default_factory=dict)  # display band -> `s` column
    #: Named boxes in *interior* coordinates, for profiling and overlays. The grid
    #: cannot carry comments, so this is the only record of what a cell means.
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


def _flat_lane(
    micro: tuple[tuple[str, str | None], ...], x0: int, band_x: dict[str, int], row: int
) -> dict[tuple[int, int], tuple[str, str | None]]:
    """Lay one flat lane, pushing each band's *first* glyph out to ``band_x[band]``.

    Everything from that glyph on follows contiguously, so a lane's memory block
    sits in the room's eastern band and binds to the east-wall pipes instead of to
    the ROM pipe on the west (``ARCH.md`` §7.1) — and a display lane's ``s`` lands
    in the column its own port's pipe leaves the south wall from.
    """
    out: dict[tuple[int, int], tuple[str, str | None]] = {}
    x = x0
    pushed: set[str] = set()
    for glyph, band in micro:
        target = band_x.get(band) if band is not None else None
        if target is not None and band not in pushed:
            pushed.add(band)
            while x < target:
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
    band_x = {Band.MEM: mem_x}

    # Display lanes: one `s` per port, spread ``_DSP_PITCH`` columns apart so each
    # binds the pipe that leaves the south wall directly beneath it.
    dsp_used = [b for b in DSP_BANDS if any(b == bb for mc in flat.values() for _, bb in mc)]
    dsp_cols = {b: lane_x0 + 1 + i * _DSP_PITCH for i, b in enumerate(dsp_used)}
    band_x.update(dsp_cols)

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
            cells = _flat_lane(flat[m], lane_x0, band_x, r)
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
    # The collector sits **immediately** below the lane band, above the slabs, and
    # slab exits *rise* into it rather than dropping past it. That is worth real
    # ticks: every instruction walks the riser from the collector up to the fetch
    # row, so putting the collector below ~21 rows of slabs made the riser 38 cells
    # instead of 16 — paid once per instruction, for the whole program. A profile
    # (`tools/heatmap.mjs` + `lm1.profile`) put the return path at 25 % of the CPU's
    # time before this.
    collector = span + 1
    y = collector + 1
    for i, m in enumerate(order):
        slab_at[m] = y
        slab_base[m] = _STRUCT_X0 + i * _SLAB_PITCH
        y += slab_rows[m]
    bottom = y

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
            g.soft(drop_x[r], yy, ".")  # crosses the collector row for a slab lane

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
    # `soft` after the drops, so a slab-entry column that has to pass *through* the
    # collector keeps its `.` and is not turned west by a `<`.
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
    height = bottom
    mem_rows = sorted(
        r
        for r in all_rows
        if by_row.get(r) in flat and any(b == Band.MEM for _, b in flat[by_row[r]])
    )
    mem_out_row = mem_rows[len(mem_rows) // 2] if mem_rows else centre
    in_rows = [p.row[m] for m in used if p.sem[m] is Sem.INPUT]
    out_cols = [lane_x0 + 1]

    # ── name every region, so a profile is readable ───────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        "fetch": (1, centre, 4, 1),
        "trie": (5, 1, k, span),
        "return:riser": (1, centre + 1, 1, collector - centre),
        "return:collector": (2, collector, ret_x - 1, 1),
    }
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            continue
        end = drop_x.get(r, lane_end[r])
        regions[f"lane:{m}"] = (lane_x0, r, max(1, end - lane_x0 + 1), 1)
    for m in order:
        regions[f"slab:{m}"] = (slab_base[m], slab_at[m], _SLAB_PITCH, slab_rows[m])

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
        regions=regions,
        pipe_glyphs=pipe_glyphs,
        has_in=bool(in_rows),
        has_out=any(p.sem[m] is Sem.OUTPUT for m in used),
        dsp_cols=dsp_cols,
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
        # Rise back to the collector, which is now *above* the slabs: the exit is a
        # `^` column, and the collector's own `<` turns the arriving man west.
        g.put(exit_col, s0 + 2, "^")
        for y in range(collector + 1, s0 + 2):
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
        if arm == taken:
            g.put(cols[arm], row, "v")
            for y in range(row + 1, turn_row):
                g.soft(cols[arm], y, ".")
        else:
            # Not-taken arms rise to the collector above the slab.
            g.put(cols[arm], row, "^")
            for y in range(collector + 1, row):
                g.soft(cols[arm], y, ".")
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
    # East of every arm, which is not cosmetic. The exit now *rises* to the
    # collector, so it crosses all three arm rows on the way — and `base + 2` is
    # exactly where each arm keeps its `W`, so a riser there walks the returning man
    # through a register swap. `base + 11` clears `cols["neg"]` (`base + 9`) and
    # still fits inside _SLAB_PITCH.
    exit_col = base + 11
    # Only the turn cell is an arrow: the body must be `.` because it also crosses
    # shallower slabs' westbound entry rows, where a `^` would send that man north.
    g.put(exit_col, loop_y, "^")
    for y in range(collector + 1, loop_y):
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
    display: tuple[int, int] | None = None
    #: Where each display band's ``s`` glyph ended up, in *grid* coordinates — the
    #: cells ``lm.mjs route`` has to answer with that band's own pipe.
    dsp_glyphs: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Named boxes in grid coordinates: ``name -> (x, y, w, h)``. The generator is
    #: the only thing that knows what any cell *means* — the grid carries no
    #: comments — so it records that here for ``man_debug`` overlays and for
    #: ``tools/heatmap.mjs``, which is otherwise a profile of anonymous cells.
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)

    def debug_map(self):  # -> man_debug.DebugMap
        """A ``man_debug.DebugMap`` naming every region, for overlays and profiling.

        A generated grid carries no comments, so this sidecar is the *only* record of
        what any cell means. Emitting it alongside the ``.man`` (``--html`` /
        ``--json``) is what makes ``tools/debug-trace.mjs`` and the heat map readable
        on a machine nobody drew by hand.
        """
        from ..man_debug import DebugMap

        dbg = DebugMap(f"{self.program.name} — generated by lm1.machine")
        palette = {
            "rom": "#a855f7",
            "cpu": "#3b82f6",
            "adapter": "#22c55e",
            "tape": "#0ea5e9",
            "display": "#ec4899",
            "io": "#94a3b8",
        }
        notes = {
            "rom": f"looping ROM: {self.program.P} words, {self.rom_rows} rows, re-emitted forever",
            "adapter": "expands one sign-biased request word into the tape's `op addr [value]`",
            "tape": f"rotating pipe tape, N={self.tape_n} (~105+8.3N ticks/access)",
            "display": "LM-75: top=ADDR, left=DATA, bottom=SWAP",
            "cpu:fetch": ">rbr — opcode into BP, then the operand into A (fixed-width 2 words)",
            "cpu:trie": f"depth-{self.plan.k} backpack trie; leaves are bit-reversed",
            "cpu:return:collector": "every lane funnels west along here",
            "cpu:return:riser": "up to the fetch row — paid once per instruction",
        }
        for name, (x, y, w, h) in sorted(self.regions.items()):
            kind = name.split(":", 1)[0]
            note = notes.get(name, "")
            if not note and name.startswith("cpu:lane:"):
                mnemonic = name.rsplit(":", 1)[1]
                note = f"opcode {self.plan.number.get(mnemonic, '?')} — {mnemonic}"
            elif not note and name.startswith("cpu:slab:"):
                note = f"{name.rsplit(':', 1)[1]}: discard loop / X fan-out (2-D, so not a lane)"
            dbg.region(name, x, y, w, h, note=note, color=palette.get(kind, "#64748b"), tags=[kind])
        return dbg

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
        panel = f", LM-75 {self.display[0]}x{self.display[1]}" if self.display else ""
        return (
            f"{self.program.name}: {self.width}x{self.height} "
            f"footprint {self.footprint}, {len(self.plan.number)} opcodes "
            f"(depth {self.plan.k}), P={self.program.P} words on {self.rom_rows} ROM rows, "
            f"tape N={self.tape_n}, mem_pad={self.mem_pad}{panel}"
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
    display: tuple[int, int] | None = None,
) -> Machine:
    """Assemble the whole machine for ``program``.

    ``tape_n`` defaults to the program's highest *static* address, which is wrong
    for any program that computes addresses at runtime (``LDA``/``MOVA``), so those
    must pass it explicitly. ``mem_pad`` is searched: it shifts the memory block
    east until every pipe glyph binds where it should (§7.1). ``display`` is the
    LM-75's interior ``(width, height)`` and is required by any program using a
    ``DSP*`` opcode — the panel resolution is the problem's, not the program's.
    """
    p = plan(program)
    words = rom_words(program, p)
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1
    if display is None and any(s in DSP_SEM_BAND for s in p.sem.values()):
        raise MachineError(
            "the program drives the LM-75 but no display resolution was given; "
            "pass display=(width, height) from the problem's `io.display`"
        )

    pads = [mem_pad] if mem_pad is not None else range(0, 40)
    last: MachineError | None = None
    for pad in pads:
        try:
            return _assemble(program, p, words, tape_n, rom_rows, pad, display)
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
    display: tuple[int, int] | None = None,
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
    #
    # Only when a lane reads it: a second I room is a load error and an unused one is
    # dead weight, but more to the point an unused *pipe* still competes for every
    # `r` in the room (§7.1 is nearest, not nearest-useful) — `palette` reads no
    # input at all.
    iy = CY + cpu.in_row
    if cpu.has_in:
        g.room(3, iy - 1, 5, iy + 1)
        g.put(4, iy, "I")
        g.draw_pipe([(6, iy), (CX - 1, iy)])

    # ── CPU south wall -> O room ─────────────────────────────────────────────
    # Omitted on a display problem: emitting program output there is an error
    # (``SPEC.md``), and an unused outgoing pipe would still compete for every `s`.
    oy = CY + H + 2
    if cpu.has_out:
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

    # ── the LM-75, below the CPU ─────────────────────────────────────────────
    dsp_touches = (
        _display(g, cpu, CX, CY + H + 1, AX, display) if (display and cpu.dsp_cols) else {}
    )

    rows = g.rows()

    # ── name every block in grid coordinates ─────────────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        f"cpu:{n}": (CX + x, CY + y, w, h) for n, (x, y, w, h) in cpu.regions.items()
    }
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)
    regions["adapter"] = (AX, AY, ADAPTER_W + 2, ADAPTER_H + 2)
    regions["tape"] = (TX, TY, tape.width, tape.height)
    if cpu.has_in:
        regions["io:I"] = (3, iy - 1, 3, 3)
    if cpu.has_out:
        regions["io:O"] = (CX + cpu.out_col - 1, oy + 2, 3, 3)
    # The panel is the only thing that uses `=`/`:`, so its box can just be read
    # back off the grid rather than threaded out of the routing helper.
    panel = [(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch in "=:"]
    if panel:
        px = min(x for x, _ in panel)
        py = min(y for _, y in panel)
        regions["display"] = (
            px,
            py,
            max(x for x, _ in panel) - px + 1,
            max(y for _, y in panel) - py + 1,
        )

    touches = {
        "rom": (CX - 1, CY + cpu.centre),
        "mem_req": (CX + W + 2, req_row),
        "mem_resp": (CX + W + 2, resp_row),
        **dsp_touches,
    }
    if cpu.has_in:
        touches["in"] = (CX - 1, iy)
    if cpu.has_out:
        touches["out"] = (CX + cpu.out_col, CY + H + 2)
    check_bindings(
        [(CX + x, CY + y, glyph, band) for x, y, glyph, band in cpu.pipe_glyphs], touches
    )
    # Every pipe at the CPU plus the tape's own two ring pipes and one into it: any
    # other count means the grid is geometrically ambiguous somewhere — usually a
    # leg running alongside a room corner, which the engine reads as an extra pipe
    # (see mem_in_row). Cheap to check and it localises a whole class of bug.
    _check_pipe_count(rows, expected=len(touches) + 3)
    return Machine(
        rows=rows,
        regions=regions,
        program=program,
        plan=p,
        tape_n=tape_n,
        rom_rows=romlay.rows_used,
        mem_pad=mem_pad,
        display=display if dsp_touches else None,
        dsp_glyphs={
            band: (CX + x, CY + y) for x, y, _glyph, band in cpu.pipe_glyphs if band in DSP_BANDS
        },
    )


def _display(
    g: _Grid,
    cpu: _Cpu,
    cx: int,
    wall_y: int,
    east_limit: int,
    size: tuple[int, int],
) -> dict[str, tuple[int, int]]:
    """Draw the LM-75 below the CPU and wire its three ports. Returns pipe touches.

    The panel is a room with ``=``/``:`` walls, and **which side a pipe lands on is
    what makes it ADDR / DATA / SWAP** (``SPEC.md``) — so the wiring, not the CPU,
    decides what each lane's ``s`` means. All three pipes leave the CPU's south
    wall, directly beneath their own lane's ``s`` (that is how each one binds,
    §7.1), which puts the panel below the CPU and forces exactly one detour: ADDR
    must arrive from the north and SWAP from the south, and the CPU is on one side.
    So ADDR drops straight in, DATA turns west round the panel, SWAP turns east and
    runs beneath it. West-to-east lane order (``DSP_BANDS``) is what keeps those
    three routes from crossing: each westward leg starts west of every column a
    lane further east uses.

    Corner attachments are a load error on a display, so every terminal lands
    strictly between two corners.

    **The panel's west wall is not simply the CPU's.** ADDR is the one pipe with no
    corridor row to turn on — there are only two of those and DATA and SWAP have
    them — so it has to descend straight into the top wall, which means the panel
    must *span ADDR's column*. A 32-wide panel under a 48-wide CPU spans it with
    ``dx = cx``; ``palette``'s 8-wide one does not, and the panel slides east until
    it does. :func:`_panel_x` derives the column and says why the window is what it
    is.
    """
    dw, dh = size
    if dw < 3:
        raise MachineError(f"a {dw}-wide panel has no room between its corners for SWAP")
    cols = {band: cx + col for band, col in cpu.dsp_cols.items()}
    dx = _panel_x(dw, cols)
    dy = wall_y + 4  # three corridor rows between the CPU's south wall and the panel
    right, bottom = dx + dw + 1, dy + dh + 1
    # SWAP's descent: its own column when that is already clear of the east wall,
    # otherwise the first column that is. It comes back *up* inside the panel's span.
    swap = cols[Band.DSP_SWAP]
    east = max(swap, right + 2)
    up_col = min(max(swap, dx + 2), right - 2)
    # ``east`` is east of both the panel and every lane column, so this one check also
    # covers the panel itself running into the adapter.
    if east >= east_limit - 1:
        raise MachineError(f"SWAP's east corridor at column {east} collides with the adapter")

    g.room(dx, dy, right, bottom, h="=", v=":")

    touches: dict[str, tuple[int, int]] = {}
    for band, x in cols.items():
        y0 = wall_y + 1
        lrow, under, up = dy + dh // 2, bottom + 2, bottom + 1
        if band == Band.DSP_ADDR:  # straight down into the top wall
            route = [(x, y0), (x, dy - 1)]
        elif band == Band.DSP_DATA:  # west of the panel, then east into the left wall
            route = [(x, y0), (x, y0 + 1), (dx - 2, y0 + 1), (dx - 2, lrow), (dx - 1, lrow)]
        else:  # SWAP: east of the panel, under it, then north into the bottom wall
            route = [
                (x, y0),
                (x, y0 + 2),
                (east, y0 + 2),
                (east, under),
                (up_col, under),
                (up_col, up),
            ]
        # A zero-length leg would make draw_pipe put two arrowheads on one cell.
        g.draw_pipe([p for i, p in enumerate(route) if i == 0 or p != route[i - 1]])
        touches[band] = (x, y0)
    return touches


def _panel_x(dw: int, cols: dict[str, int]) -> int:
    """The panel's west wall column, given each port's ``s`` column in grid coordinates.

    Three constraints, all of them geometry rather than taste:

    * ADDR descends straight into the top wall, so its column must lie **strictly
      inside** the panel's span: ``dx < addr < dx + dw + 1``;
    * DATA turns *west* out of the corridor to descend at ``dx - 2``, so that column
      must actually be west of DATA's own: ``dx - 2 < data``. Otherwise the leg runs
      east instead and crosses ADDR's and SWAP's descents;
    * ``dx - 2`` has to clear the ROM corridor in column 1.

    Inside that window the panel is centred on ADDR, which for a panel at least as
    wide as the lane band's spread lands it back on ``dx = cx`` — so the wide case
    (``plotter``) is unchanged and only a narrow panel moves.
    """
    addr, data = cols[Band.DSP_ADDR], cols[Band.DSP_DATA]
    low = max(4, addr - dw)
    high = min(data + 1, addr - 1)
    if low > high:
        raise MachineError(
            f"a {dw}-wide panel cannot span ADDR's column {addr} while leaving DATA's "
            f"turn at {data} pointing west: the window [{low}, {high}] is empty. Widen "
            "the panel, or narrow _DSP_PITCH so the three lanes sit closer together."
        )
    return min(max(addr - 1 - (dw - 1) // 2, low), high)


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


# ── the per-slug registry ────────────────────────────────────────────────────
# Everything :func:`build_for` needs that is not derivable from the ``.asm`` lives
# here, one entry per slug. Adding a program means adding a `TAPE_SIZE` line, and a
# `ROM_ROWS` line only if the default fold is not the footprint optimum.

#: Tape size per problem, from the *constraints* rather than the public data:
#: ``tcp`` allows n=48, so addresses reach BUF+47 = 51 even though no public case
#: goes past 35. ``ARCH.md`` §4.1: footprint is 32x32 whatever N is and cost is
#: ~105 + 8.3N ticks, so there is no trade-off — just size it to the real maximum.
#:
#: A program that computes addresses at runtime (``LDA``/``MOVA``) has no *static*
#: high address, so this is the only place its tape size is stated.
#:
#: **Size it to the highest address actually reached**, and note this matters more
#: than §4.1's "~105 + 8.3N" implies: the tape is a *rotating* ring, so a request
#: waits for its slot to come round. Measured on the real engine, one extra slot
#: costs ~114 ticks per case on ``brackets`` and ~999 on ``tcp`` — the difference
#: being how many accesses each program makes. It is not a footprint trade at all:
#: the tape block is 33 columns wide at every N.
TAPE_SIZE = {
    "brackets": 5,  # reaches address 4
    # 52, NOT 51. tcp's highest address is BUF + 47 = 51, and a tape sized to
    # exactly that **crashes** (`fatal: wall`) at n=48 after 32 of 48 values —
    # verified: 51 fails, 52 and 53 pass. The public cases only reach seq ~35, so
    # nothing catches it there. The rule is `highest address + 1`, which is also
    # what brackets follows (reaches 4, sized 5).
    "tcp": 52,
    "gradebook": 32,  # one packed cell per student (16) + 15 scalars
    "plotter": 11,  # reaches address 10 — ten names aliased onto ten slots
    "palette": 3,  # one colour counter; the pixels need no tape at all
    "sudoku-validity": 31,  # 27 unit masks + 3 cursors, one slot of slack
}

#: ROM fold overrides, where ``rom.rows_for_budget``'s default is not the footprint
#: optimum. The default folds the ROM toward the *CPU's own* width, which is right
#: only while the CPU and the tape are the sole things setting the bounding box.
#: ``brackets`` and ``tcp`` come out width-bound, so their height is free and the
#: default costs nothing; the two below are height-bound and had to be folded flatter
#: and wider. Every machine here is ~112 columns (adapter + the 32-wide tape), so the
#: rule of thumb is: fold until the ROM is no wider than that, and no further.
#:
#: Both numbers are the minimum over every fold, and both are checked against the
#: default in the tests, so a regression in the heuristic is a failure rather than a
#: quietly worse score.
#:
#: Kept per-slug rather than folded into the heuristic so the checked-in
#: ``brackets``/``tcp`` grids stay byte-identical.
ROM_ROWS = {
    # The panel adds ~30 rows and makes height the binding dimension, so this is the
    # minimum over every fold: 112x116 (13,456) against the default's 112x148 (21,904).
    # 20 rows is where the ROM stops being what sets the width (112 is the adapter plus
    # the 32-wide tape); folding further only adds height. `plotter` is height-bound
    # past that point, which is why unrolling its inner loop (see plotter.asm) is a real
    # footprint trade rather than a free one — it is still a large net win on score,
    # since it cuts ticks 24% for 7% more area. See tests/test_lm1_display.py.
    "plotter": 20,
    # gradebook's three per-student scans are unrolled 16 ways (see gradebook.asm on
    # why a loop iteration costs a whole ROM lap), so its image is 836 words and the
    # ROM, not the tape, sets the box. The minimum over every fold is 117x123
    # (15,129); the default's 48-column fold is 279x89 (77,841). Height-bound past
    # ~56 rows, width-bound below ~50. See tests/test_lm1_gradebook.py.
    "gradebook": 53,
    # 89x94 at 37 rows against 84x146 at the default: no display and a 30-slot tape
    # leave the machine narrow enough that the default fold makes height binding.
    # See tests/test_lm1_sudoku.py.
    "sudoku-validity": 37,
}


def display_for(slug: str) -> tuple[int, int] | None:
    """The problem's LM-75 resolution, or ``None`` when it has no display.

    Read from the problem JSON rather than recorded here: "exactly one display at
    the stated resolution" (``SPEC.md``) makes this the problem's number, and a
    panel of the wrong size fails every case.
    """
    from . import programs

    panel = (programs.problem_json(programs.problem_of(slug)).get("io") or {}).get("display")
    return (int(panel["width"]), int(panel["height"])) if panel else None


def build_for(slug: str) -> Machine:
    """Generate the machine for a checked-in task program.

    Everything not derivable from the ``.asm`` comes from the registry above, except
    the panel size, which comes from the problem JSON: a display-judged problem
    requires *exactly one* display at the stated resolution, so it is not a free
    variable the generator may shrink.
    """
    from . import programs

    if slug not in TAPE_SIZE:
        raise MachineError(f"no tape size recorded for {slug!r}; have {sorted(TAPE_SIZE)}")
    return build(
        programs.load(slug),
        tape_n=TAPE_SIZE[slug],
        rom_rows=ROM_ROWS.get(slug),
        display=display_for(slug),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path as _Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug", choices=sorted(TAPE_SIZE), help="task program to synthesise")
    # `--man/--html/--json` is the house convention for a generator (see
    # `memory_onepass_v2.py`): the grid and its debug sidecars come out of one
    # invocation, so an overlay can never drift from the ASCII it describes.
    ap.add_argument("--man", "--out", dest="man", type=_Path, help="write the grid here")
    ap.add_argument("--html", type=_Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=_Path, help="write the debug region sidecar here")
    ap.add_argument("--report", action="store_true", help="print the size report to stderr")
    args = ap.parse_args(argv)

    m = build_for(args.slug)
    if args.report:
        import sys as _sys

        print(m.report(), file=_sys.stderr)
    text = "\n".join(m.rows) + "\n"
    if args.man:
        args.man.write_text(text, encoding="utf-8")
    if args.html:
        m.debug_map().write_html(m.rows, args.html)
    if args.json:
        m.debug_map().write_json(args.json)
    if not (args.man or args.html or args.json):
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
