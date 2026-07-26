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
       v  v  v south      O, or the LM-75's DATA / ADDR / SWAP, or the STREAM
                          block's command/response pair — never two of the three,
                          since each of them owns the corridor below the CPU

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

**Some problems need a third memory tier, and it is not addressed at all.** The
tape is random-access and costs a revolution per word; ``matmul`` wants 512 slots
and touches them in nothing but stream order. :mod:`~.stream` is that tier — three
rotate-only FIFO rings, an adding relay, and a fused multiply-accumulate arm — and
it plugs in through exactly two opcodes (``SND``/``RCV``, one command word each).
The block owns the ``I`` and ``O`` rooms when it is present, which is what lets a
256-value fill be *one instruction* instead of a 256-iteration ROM loop; the CPU
then has no I/O rooms of its own and reads input by asking for it. Its two pipes
leave the south wall on their own lane columns, exactly as the display's do, and
``stream_pad`` walks the pair east until the jump slab's ``r`` is still nearer the
ROM pipe than either of them (§7.1).

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
from typing import TYPE_CHECKING

from . import rom as rommod
from .asm import Program
from .isa import TARGET_SEMS, Isa, Micro, Sem

if TYPE_CHECKING:  # the tape block's own canvas; imported lazily at run time
    from ..circuit import Circuit

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
    "STREAM_SEM_BAND",
    "STREAM_SIZE",
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
    # CPU south wall: the STREAM block's command and response pipes (stream.py).
    # South rather than east because the memory lanes own the east wall, and a
    # southern pipe is ~15 rows below the lane band — far enough that a memory `r`
    # a few columns away still wins on Manhattan distance (§7.1).
    STREAM_CMD = "stream_cmd"
    STREAM_RESP = "stream_resp"


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

#: The STREAM bands, west to east in the lane band — which is also the order their
#: pipes leave the south wall. The order is load-bearing for *planarity* rather
#: than for binding: the response pipe climbs the block's east side and runs west
#: to its lane, so it must arrive east of the command pipe's descent or the two
#: cross (see :func:`_stream`).
STREAM_BANDS: tuple[str, ...] = (Band.STREAM_CMD, Band.STREAM_RESP)
_STREAM_PITCH = 6


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
    Sem.OR_MEM: (("s", Band.MEM), ("r", Band.MEM), ("|", None), ("M", None)),
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
    # ── STREAM: one command word out, one response word in ────────────────────
    Sem.STREAM_SEND: (("W", None), ("s", Band.STREAM_CMD), ("W", None)),
    Sem.STREAM_RECV: (("r", Band.STREAM_RESP), ("M", None)),
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

#: Tags that talk to the STREAM block, keyed to the pipe each needs.
STREAM_SEM_BAND: dict[Sem, str] = {
    Sem.STREAM_SEND: Band.STREAM_CMD,
    Sem.STREAM_RECV: Band.STREAM_RESP,
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
        Sem.OR_MEM,
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
        if s in DSP_SEM_BAND or s in STREAM_SEM_BAND:
            return 2  # just above it, beside the south wall those pipes leave
        return 1

    def rank(m: str) -> tuple[int, int, str]:
        s = sems[m]
        if s in DSP_SEM_BAND:
            # Not by width (all three are `W s W`): by band, so the westmost pipe
            # belongs to the lane placed furthest from the wall. See DSP_BANDS.
            return (2, DSP_BANDS.index(DSP_SEM_BAND[s]), m)
        if s in STREAM_SEM_BAND:
            return (2, STREAM_BANDS.index(STREAM_SEM_BAND[s]), m)
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
    stream_cols: dict[str, int] = field(default_factory=dict)  # STREAM band -> glyph column
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


def build_cpu(
    program: Program,
    p: _Plan,
    *,
    mem_pad: int = 0,
    stream_pad: int = 0,
    short_return: bool = True,
) -> _Cpu:
    """Lay the CPU: fetch, decode trie, lanes, structures band, return path.

    ``short_return`` lets a simple lane drop at the end of its own micro-program
    rather than east of the slab band; see the drop-column comment. It narrows the
    CPU, which ``matmul``'s STREAM wiring does not currently survive.
    """
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

    # STREAM lanes: same idea, one column per pipe. The pitch only has to exceed
    # the 2-row lane gap for binding, but the *response* column additionally has to
    # be east of the command column, which :func:`_stream` relies on for planarity.
    # ``stream_pad`` walks the pair east, exactly as ``mem_pad`` walks the memory
    # block: the constraint that binds is not the stream lanes themselves but the
    # *jump slab's* `r`, which must stay nearer the ROM pipe on the west wall than
    # either of these on the south (§7.1 is nearest, not nearest-useful).
    stream_used = [b for b in STREAM_BANDS if any(b == bb for mc in flat.values() for _, bb in mc)]
    stream_cols = {
        b: lane_x0 + 1 + stream_pad + i * _STREAM_PITCH for i, b in enumerate(stream_used)
    }
    band_x.update(stream_cols)

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

    # ── drop columns: turn south the moment every lane below allows it ───────
    # A drop is only ever a `v` at its head and `.` below, and a southbound man keeps
    # his heading over a `.` — so the only hard constraint is that the column be clear
    # of *glyphs* on every row the drop crosses. Going bottom-to-top, that is the
    # running suffix maximum of ``lane_end``, and nothing more.
    #
    # It used to be floored at ``struct_east + 1`` for every lane, which is the
    # slabs' east edge. A **structured** lane does need that: its drop continues past
    # the collector into its own slab. A simple lane's drop *stops* at the collector,
    # which sits above the slabs, so it never enters the band it was clearing. The
    # floor cost every simple instruction ~22 columns east and the same ~22 back west
    # along the collector, twice over, once per instruction, forever.
    #
    # Simple and structured columns must stay disjoint for a subtler reason: a
    # structured drop needs the collector row to show `.` where it passes through, so a
    # simple man arriving on that column would sail *past* the collector and be
    # swallowed by the wrong slab. Keeping simple lanes west of ``struct_east`` and
    # structured ones east of it makes that disjointness structural.
    drop_x: dict[int, int] = {}
    assigned: set[int] = set()
    if short_return:
        floor = lane_x0
        for r in sorted(all_rows, reverse=True):
            # Halting rows carry no drop but do carry glyphs, so they still raise the
            # floor for everything above them.
            floor = max(floor, lane_end[r] + 1)
            if r in halting:
                continue
            m = by_row.get(r)
            if m is not None and m in slab_rows:
                # A slab's entry column must be unique in *both* directions: its `<`
                # turns an arriving man west, so any other drop sharing the column
                # would be swallowed by this slab's entry row.
                c = max(floor, struct_east + 1)
                while c in assigned:
                    c += 1
            else:
                c = floor
                if c > struct_east:
                    # A micro-program long enough to reach the slabs has to join the
                    # structured lanes' discipline rather than risk their columns.
                    c = struct_east + 1
                    while c in assigned:
                        c += 1
            drop_x[r] = c
            assigned.add(c)
    else:
        # The original rule, kept verbatim so a slug on the long path regenerates
        # byte-for-byte: every column floored east of the slabs, strictly ordered
        # bottom-to-top, and a structured lane pushing everything above it one further.
        cur = struct_east + 1
        for r in sorted(all_rows, reverse=True):
            if r in halting:
                continue
            c = max(cur, lane_end[r] + 1)
            m = by_row.get(r)
            if m is not None and m in slab_rows:
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

    # A simple drop *stops* at the collector, and the collector sits above the slabs,
    # so being west of ``struct_east`` is harmless — that used to be forbidden back
    # when the collector was below the slab band and a drop really did cross one.
    # What must still hold is column disjointness against the drops that pass
    # *through* the collector on their way to a slab: those leave `.` on the collector
    # row, so a simple man sharing the column would sail past his turn west and be
    # swallowed by that slab.
    through = {drop_x[p.row[m]] for m in order}
    clash = {r: c for r, c in drop_x.items() if c in through and by_row.get(r) not in order}
    if clash:
        raise MachineError(
            f"simple lane drop column(s) {sorted(set(clash.values()))} collide with a "
            f"slab entry column; a simple lane would drop past the collector"
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
        stream_cols=stream_cols,
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
#: ``U`` then ``X``: the adapter has exactly one incoming pipe, on its west wall,
#: so receiving turns the northbound return east directly into the branch. A
#: positive word then turns clockwise (south, the read arm), a negative one
#: counter-clockwise (north, the write arm). Zero cannot occur, which is precisely
#: why hardware addresses start at 1.
#:
#: Compared with ``>rX``, ``UX`` moves the receive one cell left and fuses its
#: steering with the pipe operation. Both arms and the spawn/return corner can
#: therefore move left too, freeing the old rightmost column.
_ADAPTER = [
    ".>M1sWNsrs.v",  # write: B=w; A=1; send 1; A=w; A=-w=a; send a; pass the value
    "UX.........v",  # receive request, point away from west pipe, branch on sign
    ".>M0sWs....v",  # read: B=w; A=0; send 0; A=w=a; send a
    "^.........@<",  # return leg; spawn/turn moved left with the freed column
]
_Y_ADAPTER = [
    ".>Ns1srs...v",  # write: send addr, then op=1, then pass the value
    "UX.........v",
    ".>s0s......v",  # read: send addr, then op=0
    "^.........@<",
]
ADAPTER_W = len(_ADAPTER[0])
ADAPTER_H = len(_ADAPTER)
ADAPTER_IN_ROW = 2  # west wall: the request pipe from the CPU
ADAPTER_OUT_ROW = 2  # east wall: the expanded request out to the tape


def adapter_cells(*, address_first: bool = False) -> dict[tuple[int, int], str]:
    """The adapter's interior cells, local (1,1)-based."""
    out: dict[tuple[int, int], str] = {}
    rows = _Y_ADAPTER if address_first else _ADAPTER
    for y, row in enumerate(rows, start=1):
        for x, ch in enumerate(row, start=1):
            if ch != " ":
                out[(x, y)] = ch
    return out


# ── the two-tier adapter: one more branch, on the address *range* ────────────
#: The row a two-tier adapter's incoming request pipe lands on (interior, 1-based).
ADAPTER2_IN_ROW = 6
#: Interior rows the two-tier adapter needs.
ADAPTER2_H = 12
#: The interior rows the hot (tier) and cold (tape) pipes leave the east wall on.
#: Hot on top and cold on the bottom is forced by the *outside* geometry: the tier
#: sits north-east of the adapter and the tape's request is routed south, under
#: both blocks, so a hot pipe leaving below a cold one would have to cross it.
ADAPTER2_HOT_ROW = 1
ADAPTER2_COLD_ROW = 11


@dataclass(frozen=True)
class _Adapter2:
    """A range-routing adapter: two outgoing pipes instead of one."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int = ADAPTER2_H
    in_row: int = ADAPTER2_IN_ROW
    hot_row: int = ADAPTER2_HOT_ROW
    cold_row: int = ADAPTER2_COLD_ROW


def two_tier_adapter(hot_top: int) -> _Adapter2:
    """Expand one sign-biased request word onto **one of two** STORE pipes.

    The one-tier adapter branches once, on the request word's *sign*: ``+a`` is a
    read and ``-a`` a write (see :data:`_ADAPTER`). A second tier means branching
    again, on the address's *magnitude* — and nothing else, because both tiers
    speak the identical ``0 addr`` / ``1 addr value`` wire protocol and the hot
    tier is built with **global** column bases, so the CPU's own slot number is
    already the address it decodes. The whole second seam is therefore "which pipe
    does this ``s`` reach", which is a property of *where the glyph sits* (§7.1).

    Hot slots are the **low** addresses ``1 .. hot_top``, and the test is
    ``A = a - (hot_top + 1)``. ``X`` is three-way — clockwise on positive, straight
    on zero, counter-clockwise on negative — so the boundary ``a == hot_top + 1``
    lands on *straight*; with the hot range low that is the first **cold** address,
    which shares a side with the clockwise arm, and the two merge in two cells. A
    high hot range would put the zero on the seam itself and cost a dead slot.

    Interior layout, rows 1-based (``L`` is the literal ``` `hot_top+1` ```)::

         1  .......>WM1sWsrs...........v   hot (tier) write arm
         2  .....................>WM0sWv   hot read arm
         3  .......:.............:.....v
         4  ..NM L W-X v...............v   write test: A = a - (hot_top+1), then X
         5  .......>v..................v
         6  UX......:..................v
         7  ........:............:.....v
         8  ..........M L W-X v........v   read test
         9  .....................>v....v
        10  ......................>WM0sWsv cold (tape) read arm
        11  ........>WM1sWsrs..........v   cold write arm
        12  ^<<<<<<<<<<<<<<<<<<<<<<<<<@<

    Every vertical run crosses the other half's row only where that row is a nop,
    which is why the read test starts two columns east of the write test's last
    glyph and the write's descent column stays west of the read test's first.
    """
    if hot_top < 1:
        raise MachineError(f"a hot tier must hold at least one slot, not {hot_top}")
    lit = f"`{hot_top + 1}`"
    lw = len(lit)
    c_a = 7 + lw  # the write test's X
    c_d = 10 + lw  # the read test's first glyph
    c_b = c_d + lw + 3  # the read test's X
    c_r = c_b + 8  # the return column, east of every arm

    g: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = g.get((x, y))
        if old is not None and old != ch:
            raise MachineError(f"two-tier adapter collision at {(x, y)}: {old!r} vs {ch!r}")
        g[(x, y)] = ch

    def text(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            put(x + i, y, ch)

    # entry: one incoming pipe on the west wall, so `U` steers east into `X`
    put(1, ADAPTER2_IN_ROW, "U")
    put(2, ADAPTER2_IN_ROW, "X")
    put(2, 5, ".")
    put(2, 4, ">")  # A < 0: a write, counter-clockwise -> north
    put(2, 7, ".")
    put(2, 8, ">")  # A > 0: a read, clockwise -> south

    # the write half: `N` makes the address positive, then the range test
    text(3, 4, f"NM{lit}W-X")
    put(c_a + 1, 4, "v")  # a == hot_top + 1: straight, and it is a *cold* address
    put(c_a, 5, ">")  # a > hot_top: clockwise
    put(c_a + 1, 5, "v")  # the two cold paths merge here and descend
    for y in range(6, 11):
        put(c_a + 1, y, ".")
    put(c_a + 1, 11, ">")
    text(c_a + 2, 11, "+M1sWsrs")  # cold write arm
    put(c_a, 3, ".")
    put(c_a, 2, ".")
    put(c_a, 1, ">")
    text(c_a + 1, 1, "+M1sWsrs")  # hot write arm

    # the read half: the address is already positive
    for x in range(3, c_d):
        put(x, 8, ".")
    text(c_d, 8, f"M{lit}W-X")
    put(c_b + 1, 8, "v")
    put(c_b, 9, ">")
    put(c_b + 1, 9, "v")
    put(c_b + 1, 10, ">")
    text(c_b + 2, 10, "+M0sWs")  # cold read arm
    for y in (7, 6, 5, 4, 3):
        put(c_b, y, ".")
    put(c_b, 2, ">")
    text(c_b + 1, 2, "+M0sWs")  # hot read arm

    # the return leg: south down the last column, west along the floor, north home
    for y in range(1, ADAPTER2_H):
        put(c_r, y, "v")
    put(c_r, ADAPTER2_H, "<")
    put(c_r - 1, ADAPTER2_H, "@")
    for x in range(2, c_r - 1):
        put(x, ADAPTER2_H, "<")
    put(1, ADAPTER2_H, "^")
    for y in range(ADAPTER2_IN_ROW + 1, ADAPTER2_H):
        put(1, y, "^")
    return _Adapter2(cells=g, width=c_r)


# ── the tape, as a STORE block ───────────────────────────────────────────────
@dataclass
class _Tape:
    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]  # where the request pipe must arrive, pointing east
    out_cell: tuple[int, int]  # where the response pipe leaves, pointing north
    #: Ring capacity in values — the forward plus return pipe cell count. Must be
    #: ``>= n + 1``; overshoot is free (see :func:`tape_block`) but worth asserting,
    #: since a ring one value short does not fault, it just stalls the machine.
    slots: int = 0


#: The tape worker's room corner inside the block. Every anchor below is derived
#: from it, so both ring layouts hang the same four pipes off the same four cells.
_TAPE_WX, _TAPE_WY = 8, 8


def _tape_shell(n: int) -> tuple[Circuit, tuple[int, int], tuple[int, int]]:
    """The worker room and the two CPU-facing pipe stubs — the part no ring changes.

    Shared by both ring layouts so neither can drift from the other. What fixes every
    ``r``/``s`` binding *inside* the worker is the worker's four wall anchors, not the
    shape of the ring: a ring may be routed any way at all so long as it still leaves
    the east wall on ``memory_tape.V2_FWD_ROW`` and comes back north into the bottom
    wall at ``memory_tape.V2_RET_COL``. That is the licence the serpentine uses.

    Returns the canvas plus the request and response stub cells.
    """
    from ..circuit import Circuit
    from ..memory_tape import V2_IH, V2_IN_ROW, V2_IW, V2_OUT_COL, worker_v2

    g = Circuit(400, 200)
    wk = worker_v2(n)
    WX, WY = _TAPE_WX, _TAPE_WY
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
    return g, (WX - 3, iy), (ox, WY - 3)


def _tape_of(g: Circuit, in_cell: tuple[int, int], out_cell: tuple[int, int], slots: int) -> _Tape:
    cells = {k: v for k, v in g.cell.items() if v != " "}
    return _Tape(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=in_cell,
        out_cell=out_cell,
        slots=slots,
    )


def tape_block(n: int) -> _Tape:
    """``memory_tape``'s verified rotating-pipe tape, wired for use as STORE.

    ``memory_tape.assemble_v2`` builds the tape as a standalone answer to the
    ``memory`` problem, so it comes with its own ``I`` and ``O`` rooms. A program
    may have at most one of each and the CPU owns them, so those two rooms are
    replaced here by pipe stubs the caller extends. The **worker is untouched at
    every size**, which is the point: it is measured hardware (``ARCH.md`` §4.1) and
    this must not perturb it. Only the ring around it is re-routed, and only above
    107 slots — every ``n <= 107`` emits the same cells it always did, which is what
    keeps the ten checked-in ``.man`` files byte-identical.

    ``n`` is the slot count. Ring **capacity is pipe length** (``SPEC.md``: a pipe
    is a FIFO whose capacity equals its cell count) and it must be ``>= n + 1`` — a
    WRITE briefly holds one more value than it stores. The folds below are two
    L-shaped pipes whose length is a *perimeter*, so they top out at 108 slots and
    ``tape_block(108)`` used to raise; past that :func:`_serpentine_tape` routes the
    forward pipe as a boustrophedon and length scales with *area* instead. The two
    meet exactly where they overlap: fold 12 gives 108 cells, and the serpentine's
    first (five-row) tier gives 152. The ceiling is now ``n=1975``.

    The block is 33 columns wide whatever ``n`` is; **height** is 34 up to ``n=107``
    and then ``31 + rows``, i.e. two more rows per 48 further slots — 36 at
    ``n=108``, 46 at ``n=380``, 48 at ``n=420``. That height is usually *free*,
    because the block sits east of the adapter beside the CPU's own panel/stream
    stack, which is taller. Rebuilt at ``n=420``, nine of the ten machines rebuilt
    keep their bounding box to the cell: ``brackets`` 95x69, ``tcp`` 109x74,
    ``gradebook`` 114x101, ``plotter`` 109x104, ``snake`` 123x129, ``pathfinder``
    180x184. The exception is ``matmul`` (88x90 -> 100x90, 8,100 -> 10,000): it is the
    one machine that is both square *and* has a STREAM block under the CPU, so the
    extra rows push the pad search onto a wider ``mem_pad``. Size to the real high
    address, as always — this is not a knob to spend.

    Cost is unchanged too — measured **8.00 ticks per slot per access**, dead linear
    and with no step at the 107/108 seam. Excess ring capacity only delays the first
    value of a lap; the worker's own ``rs`` loop is an order of magnitude slower than
    one cell per tick, so the worker stays the bottleneck and overshoot is free.
    """
    from ..memory_tape import (
        RELAY,
        V2_FWD_ROW,
        V2_IH,
        V2_IW,
        V2_RET_COL,
        _draw_pipe,
    )

    WX, WY = _TAPE_WX, _TAPE_WY
    for fold in (0, 2, 4, 6, 8, 10, 12):
        g, in_cell, out_cell = _tape_shell(n)

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
        return _tape_of(g, in_cell, out_cell, n_fwd + n_ret)
    return _serpentine_tape(n)


#: Columns the big ring reserves under the worker, in block coordinates.
#: ``_SNAKE_WEST`` is where an interior westbound leg turns south, ``_SNAKE_CLIMB``
#: the column the return pipe climbs. The gap between them is deliberate: two
#: different pipes running in adjacent columns parse fine, but ``lllm_layout``'s
#: 257-word ring left a spare column between its snake and its climb and that ring
#: is the one thing in this repo already proven at this size, so copy it.
_SNAKE_CLIMB = 8
_SNAKE_WEST = 10
#: First serpentine row, measured from the worker's bottom wall. The return pipe's
#: westbound leg takes ``+2`` (its last cell is ``+1``, pointing into the wall), so
#: ``+4`` leaves one blank row between the two pipes.
_SNAKE_TOP = 4


def _serpentine_tape(n: int) -> _Tape:
    """The same ring, with the forward pipe snaked so capacity scales with area.

    Two L-shaped pipes give a *perimeter* of capacity and the band under the worker
    is only 26 columns wide, which is why the folded layout dies at 108 slots. The
    fix is the one ``lllm_layout._serpentine`` already uses for its 257-word store
    ring: sweep the forward pipe boustrophedon-wise across the band. Each row adds
    23 cells to the snake and one to the return pipe's climb, so the ring holds
    ``24 * rows + 32`` values — 152 at five rows, 1976 at eighty-one::

        worker  ─────────────────────────────┐        east column, as before
        ═══════════════════════╤═════════════│═
                 ret leg  ◄────┘             │        row +2, into the bottom wall
                 (blank separator row)       │
        relay ◄──┬──────────────────────◄────┘        row +4, first westbound leg
              │  └─────────────────────────►┐
              │  ┌──────────────────────◄────┘             ...
              ▲  ▼
        ┌───┐ │  └ ... last leg is westbound, into the relay's east wall
        │r s│ ▲
        └───┘ └── the return pipe climbs a column of its own back to the wall

    Three things make it planar. The relay follows the snake **down** so the last
    westbound leg lands on its east wall exactly as the folded layout's did (fwd on
    the relay's bottom interior row, ret on its top one). Interior westbound legs
    stop two columns short of the relay so only the final one reaches it. And the
    return pipe owns :data:`_SNAKE_CLIMB` alone, west of every interior leg, so it
    can climb from under the relay back to the worker's bottom wall without
    crossing the snake.

    The row count must be **odd** so the last leg runs west; that wastes at most one
    row of capacity, and capacity overshoot is free (see :func:`tape_block`).
    """
    from ..memory_tape import RELAY, V2_FWD_ROW, V2_IH, V2_IW, V2_RET_COL, _draw_pipe

    WX, WY = _TAPE_WX, _TAPE_WY
    bottom_y = WY + V2_IH
    fy = WY + V2_FWD_ROW
    ret_col = WX + V2_RET_COL
    east = WX + V2_IW + 2
    top = bottom_y + _SNAKE_TOP

    # Seventeen rows carry the ~420 slots a little-little-man interpreter wants; the
    # last tier here holds 1976 values, at which point the block is 112 rows tall.
    for rows in range(5, 82, 2):
        g, in_cell, out_cell = _tape_shell(n)
        last = top + rows - 1  # the final, relay-bound westbound leg
        relay_y = last - 3  # so `last` is the relay's bottom interior row
        for i, row in enumerate(RELAY):
            for j, ch in enumerate(row):
                g.set(1 + j, relay_y + i, ch)
        adj = len(RELAY[0]) + 1  # first column east of the relay's wall

        snake: list[tuple[int, int]] = [(WX + V2_IW + 1, fy), (east, fy), (east, top)]
        for i in range(rows):
            y = top + i
            if i == rows - 1:
                snake.append((adj, y))  # into the relay
            elif i % 2 == 0:
                snake += [(_SNAKE_WEST, y), (_SNAKE_WEST, y + 1)]
            else:
                snake += [(east, y), (east, y + 1)]
        n_fwd = _draw_pipe(g, snake)
        n_ret = _draw_pipe(
            g,
            [
                (adj, relay_y + 1),
                (_SNAKE_CLIMB, relay_y + 1),
                (_SNAKE_CLIMB, bottom_y + 2),
                (ret_col, bottom_y + 2),
                (ret_col, bottom_y + 1),
            ],
        )
        if n_fwd + n_ret < n + 1:
            continue
        return _tape_of(g, in_cell, out_cell, n_fwd + n_ret)
    raise MachineError(f"no serpentine gives the tape {n + 1} slots; widen the band")


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
    incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
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
    stream_pad: int = 0
    display: tuple[int, int] | None = None
    #: Where each display band's ``s`` glyph ended up, in *grid* coordinates — the
    #: cells ``lm.mjs route`` has to answer with that band's own pipe.
    dsp_glyphs: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: The placed STREAM block, when the program uses one (see ``stream.py``).
    stream: object | None = None
    #: The placed hot man-memory tier, when ``build(hot=...)`` asked for one.
    tier: object | None = None
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
            "stream": "#f59e0b",
        }
        notes = {
            "rom": f"looping ROM: {self.program.P} words, {self.rom_rows} rows, re-emitted forever",
            "adapter": "expands one sign-biased request word into the tape's `op addr [value]`",
            "tape": f"rotating pipe tape, N={self.tape_n} (~105+8.3N ticks/access)",
            "stream": "STREAM block: rotate-only rings, an adding relay, a fused MAC (~9.5 ticks)",
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
        stream = f", stream_pad={self.stream_pad}" if self.stream else ""
        return (
            f"{self.program.name}: {self.width}x{self.height} "
            f"footprint {self.footprint}, {len(self.plan.number)} opcodes "
            f"(depth {self.plan.k}), P={self.program.P} words on {self.rom_rows} ROM rows, "
            f"tape N={self.tape_n}, mem_pad={self.mem_pad}{stream}{panel}"
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
    stream: tuple[int, int, int] | None = None,
    resp_pad: int = 0,
    packed_rom: bool = True,
    short_return: bool | None = None,
    store: str = "tape",
    hot: tuple[int, int] | None = None,
) -> Machine:
    """Assemble the whole machine for ``program``.

    ``store`` picks the memory tier: ``"tape"`` is the rotating ring (§4.1,
    ``105 + 8.3n`` ticks per access at 33 columns whatever ``n`` is), ``"men"`` is
    ``memory_men_store.men_block`` — one little man per value, a measured
    ``22 + 14 * addr`` per access. The man-memory is faster per access at every
    small ``n`` and *narrower in area* at ``n`` around 5, but it widens as
    ``6n + 13``, so it is only the better choice while the tape size is small.
    ``"men-y"`` uses two equal man-cell banks behind a ``Y`` selector. Its
    address-first adapter drives the selector directly; CPU loads already wait
    for their response, so the standalone memory program's ordering head would
    only add latency. This tier supports every registered STORE size.

    ``tape_n`` defaults to the program's highest *static* address, which is wrong
    for any program that computes addresses at runtime (``LDA``/``MOVA``), so those
    must pass it explicitly. ``mem_pad`` is searched: it shifts the memory block
    east until every pipe glyph binds where it should (§7.1). ``display`` is the
    LM-75's interior ``(width, height)`` and is required by any program using a
    ``DSP*`` opcode — the panel resolution is the problem's, not the program's.

    ``tape_n`` is a **slot count**, so the usable addresses are ``1 .. tape_n - 1``:
    slot 0 is sign-ambiguous (see the module docstring) and slot ``tape_n`` does not
    exist. Addressing it does not fault — the tape's worker walks past the end of its
    own ring and the machine stalls, emitting nothing — so the off-by-one is checked
    here instead. Only *static* addresses can be checked; a program computing them at
    run time has to size its own tape and is trusted to.

    ``packed_rom`` lays the ROM with :func:`rom.build_packed_rom` — variable-width
    tokens instead of one padded fixed width, which roughly halves it. Pass ``False``
    for the original fixed-width fold; the word stream is identical either way, so it
    is purely a size/speed choice.
    """
    if short_return is None:
        short_return = program.name not in _LONG_RETURN
    p = plan(program)
    words = rom_words(program, p)
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1
    top = _highest_address(program)
    if top >= tape_n:
        raise MachineError(
            f"{program.name} addresses STORE slot {top} but a {tape_n}-slot tape only "
            f"reaches {tape_n - 1}; a read past the end stalls the machine silently, "
            "so raise TAPE_SIZE to at least the top address plus one"
        )
    if display is None and any(s in DSP_SEM_BAND for s in p.sem.values()):
        raise MachineError(
            "the program drives the LM-75 but no display resolution was given; "
            "pass display=(width, height) from the problem's `io.display`"
        )

    pads = [mem_pad] if mem_pad is not None else range(0, 40)
    spads = range(0, 40, 2) if stream else [0]
    last: MachineError | None = None
    best: Machine | None = None
    for spad in spads:
        for pad in pads:
            try:
                m = _assemble(
                    program,
                    p,
                    words,
                    tape_n,
                    rom_rows,
                    pad,
                    display,
                    stream,
                    resp_pad,
                    spad,
                    packed_rom,
                    short_return,
                    store,
                    hot,
                )
            except MachineError as exc:
                last = exc
                continue
            # Every feasible pad is *correct*, so take the smallest rather than the
            # first. They differ by more than a column: the pad shifts the memory
            # block east, which moves every pipe that binds to it, so which pad binds
            # is not monotone and the first feasible one is an arbitrary pick. Ties
            # break on height, which is free on a width-bound machine but never hurts.
            if best is None or (m.footprint, m.height) < (best.footprint, best.height):
                best = m
    if best is None:
        raise MachineError(f"no pad pair makes every pipe bind; last: {last}")
    return best


def _packed_fold(words: list[int], budget: int) -> int:
    """Fewest rows that keep a packed ROM within ``budget`` columns.

    The packed builder is driven by row count and derives the narrowest width that
    fits, so this inverts it: width falls monotonically as rows rise, which makes
    the search a bisection. Same intent as :func:`rom.rows_for_budget` — trade width
    into height only until the ROM stops being the widest thing in the machine.
    """
    room = budget - 5  # two turn columns, the riser, and two walls
    lo, hi = 1, max(1, len(words))
    while lo < hi:
        mid = (lo + hi) // 2
        if rommod.width_for_rows(words, mid) <= room:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _assemble(
    program: Program,
    p: _Plan,
    words: list[int],
    tape_n: int,
    rom_rows: int | None,
    mem_pad: int,
    display: tuple[int, int] | None = None,
    stream: tuple[int, int, int] | None = None,
    resp_pad: int = 0,
    stream_pad: int = 0,
    packed_rom: bool = True,
    short_return: bool = True,
    store: str = "tape",
    hot: tuple[int, int] | None = None,
) -> Machine:
    cpu = build_cpu(program, p, mem_pad=mem_pad, stream_pad=stream_pad, short_return=short_return)
    W, H = cpu.width, cpu.height

    # ROM folded to roughly the CPU's own width, so neither dimension runs away
    # from the other (footprint is max(w, h)^2, ARCH.md §7.4).
    if packed_rom:
        # Packed tokens are ~half the cells, so the same column budget swallows
        # roughly twice as many words per row and the default fold is that much
        # shallower (rom.build_packed_rom).
        nrows = rom_rows if rom_rows is not None else _packed_fold(words, max(40, W))
        romlay = rommod.build_packed_rom(words, rows=nrows)
    else:
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
    if hot is not None:
        seam = _two_tier(g, cpu, CX, CY, W, AX, tape_n, hot)
        extra_regions = seam.regions
        req_row, resp_row = seam.req_row, seam.resp_row
        store_pipes = seam.pipes
        tape = seam.tape
        tier = seam.tier
    else:
        extra_regions, tier = {}, None
    # Aligned so the request pipe leaves the CPU beside the memory lanes, but never
    # so high that the response pipe's westward leg grazes the adapter's top corner.
    # A small machine (few lanes, no memory) is the case that needs the clamp.
    AY = max(CY + cpu.mem_out_row - ADAPTER_IN_ROW, CY + cpu.mem_in_row + 3)
    resp_row_check = CY + cpu.mem_in_row
    if hot is None and resp_row_check >= AY - 1:
        raise MachineError(
            f"response row {resp_row_check} is not clear of the adapter's top wall "
            f"at {AY}: its westward leg would touch the adapter's corner and the "
            "engine would read a second, spurious pipe into the CPU"
        )
    if hot is None:
        g.room(AX, AY, AX + ADAPTER_W + 1, AY + ADAPTER_H + 1)
        g.blit(AX, AY, adapter_cells(address_first=store == "men-y"))
        req_row = AY + ADAPTER_IN_ROW
        g.draw_pipe([(CX + W + 2, req_row), (AX - 1, req_row)])

    # ── tape, east of the adapter ────────────────────────────────────────────
    if hot is None:
        if store == "men":
            from ..memory_men_store import men_block

            tape = men_block(tape_n)
        elif store == "men-y":
            from ..memory_men_y import y_men_block

            tape = y_men_block(tape_n)
        elif store == "tape":
            tape = tape_block(tape_n)
        else:
            raise MachineError(f"unknown store tier {store!r}; expected 'tape', 'men', or 'men-y'")
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
        # before running back west, so it crosses neither the request pipe nor the
        # tape.
        tout_x, tout_y = TX + tape.out_cell[0], TY + tape.out_cell[1]
        resp_row = CY + cpu.mem_in_row
        top = min(AY, CY) - 3
        # ``resp_pad`` inserts a there-and-back jog in the corridor above the machine,
        # lengthening this pipe by ``2 * resp_pad`` cells and changing nothing else. It
        # exists to *measure* ARCH.md §7.4b's "every extra pipe cell costs one tick" on
        # a real machine rather than on a 13-tick one; see tests/test_lm1_pipe_cost.py.
        jog = (
            [(tout_x, top), (tout_x + resp_pad, top), (tout_x + resp_pad, top - 1)]
            if resp_pad
            else [(tout_x, top)]
        )
        g.draw_pipe(
            [
                (tout_x, tout_y - 1),
                *jog,
                (CX + W + 3, top - (1 if resp_pad else 0)),
                (CX + W + 3, resp_row),
                (CX + W + 2, resp_row),
            ]
        )

    # ── the LM-75, below the CPU ─────────────────────────────────────────────
    dsp_touches = (
        _display(g, cpu, CX, CY + H + 1, AX, display) if (display and cpu.dsp_cols) else {}
    )

    # ── the STREAM block, below the CPU ──────────────────────────────────────
    stream_touches: dict[str, tuple[int, int]] = {}
    blk = None
    if cpu.stream_cols:
        # The snake unit's ring is sized to the problem's own bound (50 cells) inside
        # its builder, so only the STREAM block takes sizes from the caller.
        if stream is None and program.unit == "stream":
            raise MachineError(
                "the program drives the STREAM block but no ring sizes were given; "
                "pass stream=(a_slots, b_slots, c_slots) from the problem's maximum"
            )
        blk, stream_touches, (SX, SY) = _stream(g, cpu, CX, CY + H + 1, stream, unit=program.unit)

    rows = g.rows()

    # ── name every block in grid coordinates ─────────────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        f"cpu:{n}": (CX + x, CY + y, w, h) for n, (x, y, w, h) in cpu.regions.items()
    }
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)
    if hot is None:
        regions["adapter"] = (AX, AY, ADAPTER_W + 2, ADAPTER_H + 2)
        regions["tape"] = (TX, TY, tape.width, tape.height)
    else:
        regions.update(extra_regions)
    if cpu.has_in:
        regions["io:I"] = (3, iy - 1, 3, 3)
    if cpu.has_out:
        regions["io:O"] = (CX + cpu.out_col - 1, oy + 2, 3, 3)
    if blk is not None:
        # One box per sub-block, so a heat map of a STREAM machine says *which* ring
        # the ticks went into rather than colouring 989 anonymous pipe cells.
        for name, (bx, by, bw, bh) in blk.regions.items():
            regions[f"stream:{name}"] = (SX + bx, SY + by, bw, bh)
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
        **stream_touches,
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
    #
    # The STREAM block adds its own pipes, all of them internal except the two
    # already counted in ``touches`` — so ``blk.pipes - 1`` (the response pipe is
    # drawn half here, half there, and is one pipe). A unit that answers nothing has
    # no response pipe to double-count, so nothing is subtracted for it.
    extra = (blk.pipes - (1 if Band.STREAM_RESP in stream_touches else 0)) if blk else 0
    # The memory tier's own internal pipes: the tape's two ring legs, or the
    # man-memory's command and answer pipe per cell. Everything the machine itself
    # drew is already in ``touches``, plus the one response pipe drawn half here.
    if hot is None:
        store_pipes = (
            tape.pipes if store == "men-y" else (2 * tape_n if store == "men" else 2)
        ) + 1
    _check_pipe_count(rows, expected=len(touches) + store_pipes + extra)
    return Machine(
        rows=rows,
        regions=regions,
        program=program,
        plan=p,
        tape_n=tape_n,
        rom_rows=romlay.rows_used,
        mem_pad=mem_pad,
        stream_pad=stream_pad,
        display=display if dsp_touches else None,
        dsp_glyphs={
            band: (CX + x, CY + y) for x, y, _glyph, band in cpu.pipe_glyphs if band in DSP_BANDS
        },
        stream=blk,
        tier=tier,
    )


@dataclass
class _Seam:
    """What the two-tier store section hands back to :func:`_assemble`."""

    regions: dict[str, tuple[int, int, int, int]]
    req_row: int
    resp_row: int
    pipes: int  # every pipe this section drew, plus both blocks' internal ones
    tape: object
    tier: object


#: The merger: ``R`` takes from *any* incoming pipe, so two tiers answering into
#: one room need no addressing and no ordering logic — the CPU blocks on every
#: read, so exactly one answer is ever in flight. Same six-cell loop
#: ``memory_men.collector_rows(1)`` uses, which is where it was measured.
_MERGER = ("@>Rv", " ^s<")
_MERGER_W = 4
_MERGER_H = 2


def _two_tier(
    g: _Grid,
    cpu: _Cpu,
    cx: int,
    cy: int,
    w: int,
    ax: int,
    tape_n: int,
    hot: tuple[int, int],
) -> _Seam:
    """Place a range-routing adapter, a hot man-memory tier, the tape, and a merger.

    The floor plan, west to east::

        CPU |  merger      (the corridor band above the adapter)
            |  ADAPTER2 --hot--> grid tier --.
            |     `-----cold----------------- \\--> tape
                                    both answers -> merger -> CPU

    Three placement facts carry it, and each one is forced:

    * **The adapter sits *below* the response corridor.** Its entry row is 6 rows
      down, so the one-tier alignment (``AY = mem_out_row - 2``) would put the
      CPU's response row inside the adapter's own rows. The request pipe takes a
      one-column jog instead, which costs two cells and frees the whole band
      between the CPU's top and the adapter for the merger.
    * **The hot tier goes first and the tape second.** The tier answers ~89 % of
      the reads, so its pipes are the ones worth keeping short; the tape's request
      is routed the long way round, *under* both blocks, which costs ~250 cells on
      a path that is already 3,416 ticks.
    * **Nothing crosses in the corridor.** Both answers run west above the CPU's
      top row on rows of their own; the cold request never enters that band, which
      is the only reason a single-layer grid can carry four pipes at once.
    """
    from ..memory_men_grid_store import grid_block

    cols, rows_ = hot
    tier = grid_block(cols, rows_, base=1)
    hot_top = tier.slots  # addresses 1..hot_top are the tier's; the rest are tape's
    if hot_top >= tape_n:
        raise MachineError(
            f"a {cols}x{rows_} tier holds slots 1..{hot_top}, which is the whole "
            f"{tape_n}-slot store; drop the tier or grow the program"
        )
    adapter = two_tier_adapter(hot_top)
    tape = tape_block(tape_n)
    # One column further east than the one-tier adapter: the request pipe drops
    # down a corridor column and has to turn east *before* the west wall, so it
    # needs a cell between its descent and the room.
    ax += 1

    resp_row = cy + cpu.mem_in_row
    req_row = cy + cpu.mem_out_row
    # Far enough below the corridor that the merger, both answers and the request
    # jog all fit between the CPU's top row and the adapter's north wall.
    ay = cy + _MERGER_H + 8
    if ay <= req_row + 1:
        ay = req_row + 2
    aw = ax + adapter.width + 1  # the adapter's east wall
    g.room(ax, ay, aw, ay + adapter.height + 1)
    g.blit(ax, ay, adapter.cells)
    # CPU east wall -> adapter west wall, with a one-column jog down the corridor.
    g.draw_pipe(
        [
            (cx + w + 2, req_row),
            (cx + w + 3, req_row),
            (cx + w + 3, ay + adapter.in_row),
            (ax - 1, ay + adapter.in_row),
        ]
    )

    # ── the hot tier, immediately east of the adapter ────────────────────────
    gx, gy = aw + 5, cy
    g.blit(gx, gy, tier.cells)
    hot_out = ay + adapter.hot_row
    tin_x, tin_y = gx + tier.in_cell[0], gy + tier.in_cell[1]
    g.draw_pipe([(aw + 1, hot_out), (aw + 3, hot_out), (aw + 3, tin_y), (tin_x - 1, tin_y)])

    # ── the tape, east of the tier; its request goes the long way, underneath ──
    tx, ty = gx + tier.width + 4, cy
    g.blit(tx, ty, tape.cells)
    cold_out = ay + adapter.cold_row
    bot = max(gy + tier.height, ty + tape.height) + 3
    ttin_x, ttin_y = tx + tape.in_cell[0], ty + tape.in_cell[1]
    lane = tx - 3  # the free columns between the two blocks
    g.draw_pipe(
        [
            (aw + 1, cold_out),
            (aw + 2, cold_out),
            (aw + 2, bot),
            (lane, bot),
            (lane, ttin_y),
            (ttin_x - 1, ttin_y),
        ]
    )

    # ── the merger, in the corridor band between the CPU's top and the adapter ──
    mx, my = ax + 1, cy + 1
    g.room(mx - 1, my - 1, mx + _MERGER_W, my + _MERGER_H)
    for j, row in enumerate(_MERGER):
        g.text(mx, my + j, row.replace(" ", "\0"))
    g.draw_pipe(
        [
            (mx - 2, my + 1),
            (cx + w + 3, my + 1),
            (cx + w + 3, resp_row),
            (cx + w + 2, resp_row),
        ]
    )

    # Both answers climb clear of every room, then run west on rows of their own.
    # Two rules, and between them there is exactly one legal assignment:
    #
    # * the **nearer** block takes the **lower** lane, or its riser would cut the
    #   far block's westward run on the way up to a higher one;
    # * the **upper** lane turns south at the **western** column, so its descent
    #   crosses the lower lane one column west of where that lane's run stops.
    hot_lane, cold_lane = cy - 1, cy - 3
    turn = mx + _MERGER_W + 2
    door = mx + _MERGER_W + 1
    g.draw_pipe(
        [
            (tx + tape.out_cell[0], ty + tape.out_cell[1] - 1),
            (tx + tape.out_cell[0], cold_lane),
            (turn, cold_lane),
            (turn, my),
            (door, my),
        ]
    )
    g.draw_pipe(
        [
            (gx + tier.out_cell[0], gy + tier.out_cell[1] - 1),
            (gx + tier.out_cell[0], hot_lane),
            (turn + 1, hot_lane),
            (turn + 1, my + 1),
            (door, my + 1),
        ]
    )

    regions = {
        "adapter": (ax, ay, adapter.width + 2, adapter.height + 2),
        "merger": (mx - 1, my - 1, _MERGER_W + 2, _MERGER_H + 2),
        "tier": (gx, gy, tier.width, tier.height),
        "tape": (tx, ty, tape.width, tape.height),
    }
    # ``touches`` already counts mem_req and mem_resp. These are the four this
    # section drew — adapter to each store, each store to the merger — plus the
    # tape's two ring legs and the tier's internals. The tier's own request and
    # answer stubs are halves of two of those four, hence the ``- 2``.
    return _Seam(
        regions=regions,
        req_row=req_row,
        resp_row=resp_row,
        pipes=4 + 2 + tier.pipes - 2,
        tape=tape,
        tier=tier,
    )


def _stream(
    g: _Grid,
    cpu: _Cpu,
    cx: int,
    wall_y: int,
    sizes: tuple[int, int, int] | None,
    *,
    unit: str = "stream",
) -> tuple[object, dict[str, tuple[int, int]], tuple[int, int]]:
    """Place the coprocessor below the CPU and wire its pipes. Returns touches.

    The command pipe drops straight out of the CPU's south wall into the block's
    north wall; a response pipe, if the unit has one, climbs the block's *east* side,
    runs west above it and turns north into its own lane's column. That is why
    ``STREAM_BANDS`` puts the response lane east of the command lane: the westward leg
    then stops east of the command pipe's descent instead of crossing it.

    Which block is placed comes from the program's ``.unit`` (``asm.UNITS``). The
    snake unit brings its own ring *and* the LM-75 panel, so on that machine there is
    no separate ``_display`` call and the CPU has no display lanes at all.
    """
    if unit == "snake":
        from . import snake_unit

        blk = snake_unit.build_snake()
    elif unit == "path":
        # The PATH unit is snake's block with the ring taken out: `pathfinder` keeps
        # its whole state (four 64-bit board words) in the CPU's tape and asks the
        # unit only to paint, so all the unit owns is the panel and the robot's cell.
        from . import path_unit

        blk = path_unit.build_path()
    else:
        from . import stream as streammod

        assert sizes is not None
        a_slots, b_slots, c_slots = sizes
        blk = streammod.build_stream(a_slots=a_slots, b_slots=b_slots, c_slots=c_slots)
    bx, by = 1, wall_y + 5
    g.blit(bx, by, blk.cells)

    cmd_col = cx + cpu.stream_cols[Band.STREAM_CMD]
    cmd_x, cmd_y = bx + blk.cmd_cell[0], by + blk.cmd_cell[1]
    lane = by - 2  # the corridor the response pipe runs west along

    # A unit that answers nothing has no response pipe, and that is a *binding*
    # property rather than a saving: §7.1 makes an incoming pipe a rival for every
    # `r` in the CPU, so a response landing on the south wall competes with the jump
    # slab's ROM read. `matmul` gets away with it only by having no `JMPF` at all;
    # measured on `snake`, all 4,800 (fold, mem_pad, stream_pad) combinations fail on
    # exactly that binding. An outgoing command pipe has no such competition.
    talks_back = Band.STREAM_RESP in cpu.stream_cols
    touches = {Band.STREAM_CMD: (cmd_col, wall_y + 1)}
    if talks_back:
        resp_col = cx + cpu.stream_cols[Band.STREAM_RESP]
        resp_x, resp_y = bx + blk.resp_cell[0], by + blk.resp_cell[1]
        if resp_col <= cmd_x:
            raise MachineError(
                f"the response lane's column {resp_col} is not east of the command pipe's "
                f"descent at {cmd_x}: the two pipes would cross"
            )
        touches[Band.STREAM_RESP] = (resp_col, wall_y + 1)

    route = [(cmd_col, wall_y + 1), (cmd_col, lane - 1), (cmd_x, lane - 1), (cmd_x, cmd_y)]
    g.draw_pipe([p for i, p in enumerate(route) if i == 0 or p != route[i - 1]])
    if talks_back:
        g.draw_pipe([(resp_x, resp_y), (resp_x, lane), (resp_col, lane), (resp_col, wall_y + 1)])
    return blk, touches, (bx, by)


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
#:
#: The rule is enforced in :func:`build`, not just documented: a tape sized to
#: *exactly* its top address does not fault, it stalls, which is the hardest kind of
#: bug to see. See ``tests/test_lm1_machine.py``.
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
    # matmul keeps both matrices in the STREAM block's rings, so its tape holds only
    # the fourteen scalars the loops need (top address 14, plus a slot of slack).
    # That is the whole point of the new tier: at n=16 an access is ~185 ticks rather
    # than the ~830 a 104-slot tape costs, and *none* of them is in the inner loop.
    "matmul": 16,
    # snake keeps eleven scalars plus a 50-slot body ring (BODY+49 = 65, so 66). The
    # ring is sized to the *constraint*, not the public cases: 100 rounds allow at most
    # 49 growths, so the snake cannot exceed 50 cells, and `MODI 50` wraps a ring of any
    # size just as cheaply as a power of two. The addresses are computed at run time, so
    # this line is the only place the tape's real extent is stated.
    #
    # It is worth ~4% on its own: measured on the engine, the same program at N=66/90
    # runs the longest public case in 2,169,980 / 2,617,836 ticks — 18.7k ticks per slot
    # per case, i.e. ~8.3 ticks per slot on every one of its ~2,250 accesses (§4.1).
    "snake": 66,
    # snake-ring keeps *only* scalars: the body lives in the coprocessor's ring, so the
    # tape is eight slots and a read costs ~180 ticks instead of ~653. That is the whole
    # point of the rewrite — see snake-ring.asm's header.
    "snake-ring": 9,
    # pathfinder's board is a bitset, not an array: 256 cells live in four 64-bit
    # words, so the whole BFS — frontier, reached set, and the four direction masks —
    # is 40 slots instead of 512. Every slot taxes every read by ~8 ticks (§4.1),
    # which is also why its power-of-two masks are computed rather than tabulated.
    "pathfinder": 52,
    # pathfinder-unit keeps the identical tape: moving the *painting* into a
    # coprocessor changes the opcode count and the trie's depth, not the data. The
    # board still lives in the CPU's four bitset words, unlike snake-ring, where the
    # coprocessor took over the data structure itself and shrank the tape 66 -> 9.
    "pathfinder-unit": 52,
}

#: Ring capacities per problem: ``(A, B, accumulator)`` in *values*, from the
#: constraint box rather than the public cases. ``matmul`` allows N, M, K <= 16, so
#: A and B hold 256 entries each and a row of C holds 16; one spare value each,
#: because a ring is briefly holding one more than it stores.
STREAM_SIZE: dict[str, tuple[int, int, int]] = {"matmul": (257, 257, 17)}

#: ROM fold overrides, where the default heuristic is not the footprint optimum.
#: The default folds the ROM toward the *CPU's own* width, which is right only while
#: the CPU and the tape are the sole things setting the bounding box.
#:
#: Every number here is the minimum over a full fold sweep, and every one is checked
#: against the default in the tests, so a regression in the heuristic is a failure
#: rather than a quietly worse score.
#:
#: All of them were re-swept for the **packed** ROM (``rom.build_packed_rom``), which
#: is the default. Packed tokens are roughly half the cells, so the same word count
#: wants about half as many rows and every entry moved: a fold tuned for the padded
#: fixed-width ROM now overshoots and makes height binding again. ``brackets`` and
#: ``tcp`` still need no entry — they are width-bound by the machine, not the ROM, so
#: their height is free and the default costs nothing.
ROM_ROWS = {
    # The panel adds ~30 rows and makes height binding, so the ROM has to stop being
    # what sets the width and then stop: 111x104 (12,321) at 8 rows, against the
    # default's 111x123 (15,129). `plotter` is height-bound past that point, which is
    # why unrolling its inner loop (see plotter.asm) is a real footprint trade rather
    # than a free one. See tests/test_lm1_display.py.
    "plotter": 8,
    # gradebook's three per-student scans are unrolled 16 ways (see gradebook.asm on
    # why a loop iteration costs a whole ROM lap), so its image is 836 words and the
    # ROM, not the tape, sets the box. 31 rows is the first fold that gets the ROM
    # under the machine's own 113 columns; wider costs width, narrower only costs
    # height. 113x101 (12,769). See tests/test_lm1_gradebook.py.
    "gradebook": 31,
    # No display and a 30-slot tape leave the machine 83 columns wide, so the same
    # rule applies one size down: 23 rows is where the ROM stops setting the width.
    # 83x80 (6,889). See tests/test_lm1_sudoku.py.
    "sudoku-validity": 23,
    # matmul is the one machine that came out *square* on the padded ROM (96x96). The
    # STREAM block's ring band is as wide as the tape row above it, and its height is
    # what the ROM trades against; packed, the trade lands at 88x90 (8,100) on 5 rows.
    "matmul": 5,
    # Like plotter: the panel adds rows, so height is binding and the fold has to stop
    # trading width for it. 9 rows is the minimum of a full sweep — 123x129 (16,641),
    # against the default's 119x142 (20,164). One row either side is worse (8 rows is
    # 135 wide, 10 rows is 130 tall), so this is a real optimum, not a plateau.
    "snake": 9,
    # snake-ring is height-bound: the coprocessor block is 66x60 and sits below the
    # CPU, so the box is set by rows whatever the fold does to the width. 6 rows is the
    # sweep minimum at 122x136; 5 is 144x135 and 7 is 111x137, so this is a real
    # optimum rather than a plateau.
    "snake-ring": 6,
    # pathfinder's P is 2,484 words — the level step is unrolled over the four board
    # words and then twice again — so the ROM dominates the box and wants folding
    # almost square. Swept: 24/40/60/72/80/100/140 rows give footprints of 267k/98k/
    # 45k/33.9k/36.9k/44.9k/63.5k, so 72 (180x184) is a real minimum, not a plateau.
    "pathfinder": 72,
    # pathfinder-unit: P is 2,416 and the CPU is smaller (depth-4 trie, three fewer
    # lanes), so the fold optimum moves; swept once the block exists.
    "pathfinder-unit": 72,
}


#: Slugs that must keep the old, long return path. Letting a simple lane drop early
#: narrows the CPU, and ``matmul``'s STREAM wiring does not survive that: every one of
#: 3,600 (fold, mem_pad, stream_pad) combinations fails to place its pipes, all of them
#: a `v` landing on an occupied cell near the top of the grid. The short path is worth
#: a few percent of ticks here, so this is a deferred fix rather than a dead end --
#: matmul keeps the grid that scored 1,464,201,360.
_LONG_RETURN = {"matmul"}


def display_for(slug: str) -> tuple[int, int] | None:
    """The problem's LM-75 resolution, or ``None`` when it has no display.

    Read from the problem JSON rather than recorded here: "exactly one display at
    the stated resolution" (``SPEC.md``) makes this the problem's number, and a
    panel of the wrong size fails every case.
    """
    from . import programs

    panel = (programs.problem_json(programs.problem_of(slug)).get("io") or {}).get("display")
    return (int(panel["width"]), int(panel["height"])) if panel else None


def build_for(slug: str, *, store: str = "tape") -> Machine:
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
        stream=STREAM_SIZE.get(slug),
        store=store,
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
