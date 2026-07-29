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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import rom as rommod
from .asm import Program
from .isa import SEEK_OF, TARGET_SEMS, Isa, Micro, Sem
from .routing import RouteBox, RouteError, constrained_route

if TYPE_CHECKING:  # the tape block's own canvas; imported lazily at run time
    from ..circuit import Circuit

__all__ = [
    "Band",
    "Machine",
    "adapter_tape_gap",
    "build",
    "build_for",
    "display_for",
    "hw_micro",
    "image_program",
    "DISPLAY_OVERRIDE",
    "DSP_BANDS",
    "DSP_LANE_BANDS",
    "DSP_SEM_BAND",
    "MEM_PAD",
    "MEMORY_SEMS",
    "ROM_ROWS",
    "STORE_SHAPE",
    "STORE_TIER",
    "STREAM_SEM_BAND",
    "STREAM_SIZE",
    "TAPE_SIZE",
    "TIER_LAYOUT",
]


class Band:
    """Which pipe a glyph needs. ``None`` means it does not care."""

    IN = "in"  # CPU north wall: the input room
    OUT = "out"  # CPU west wall, below the ROM pipe: the output room
    MEM = "mem"  # CPU east wall: request out to the adapter, response in from the tape
    # CPU south wall: the LM-75 ports. A *lane* cannot pick a pipe from its operand —
    # which pipe an `s` talks to is a static property of where the glyph sits (§7.1)
    # — so the three-opcode form gives each port its own opcode, lane and pipe.
    # Which *side of the display* a pipe lands on is what makes it ADDR / DATA /
    # SWAP (``SPEC.md`` § The LM-75 display).
    DSP_ADDR = "dsp_addr"  # display top wall
    DSP_DATA = "dsp_data"  # display left wall
    DSP_SWAP = "dsp_swap"  # display bottom wall
    # `DSP p`'s single pipe. The lane sends two words down it — the port selector,
    # then ACC — and `dsprelay.py`'s room does the choosing *behind* the seam, where
    # its three `s` glyphs each sit statically beside their own port. One band here
    # instead of three is what takes a 19-opcode program to 16, `k` from 5 to 4, and
    # the lane band from 63 rows to 31.
    DSP = "dsp"
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

#: Bands that can occupy a display *lane* and so need a column on the CPU's south
#: wall. The three ports are one arrangement; ``Band.DSP`` is the other, where a
#: single lane feeds `dsprelay`'s room and the ports hang off *its* wall instead.
#: Kept apart from ``DSP_BANDS`` because that tuple means the panel's three sides
#: and their west-to-east routing order, which the relay does not change.
DSP_LANE_BANDS: tuple[str, ...] = (*DSP_BANDS, Band.DSP)

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
    # `DSP p`: A holds the operand when the lane starts (§5.2) and B holds ACC, so
    # `s` sends the selector, `W` brings ACC over, `s` sends it, and the second `W`
    # puts ACC back. Two words down one pipe; the relay reads the first and forwards
    # the second to the port it names.
    Sem.DISPLAY: (
        ("s", Band.DSP),
        ("W", None),
        ("s", Band.DSP),
        ("W", None),
    ),
    Sem.HALT: (("H", None),),
}

#: Tags that drive an LM-75 port, keyed to the band (hence the pipe) each needs.
DSP_SEM_BAND: dict[Sem, str] = {
    Sem.DISPLAY_ADDR: Band.DSP_ADDR,
    Sem.DISPLAY_DATA: Band.DSP_DATA,
    Sem.DISPLAY_SWAP: Band.DSP_SWAP,
    Sem.DISPLAY: Band.DSP,
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
_JUMP_SEMS = frozenset({Sem.JUMP, Sem.JUMP_SEEK})
_BRANCH_SEMS = frozenset({Sem.BR_ZERO, Sem.BR_NEG, Sem.BR_ZERO_SEEK, Sem.BR_NEG_SEEK})
#: The half of the two families above that seeks the drum instead of discarding.
_SEEK_SEMS = frozenset({Sem.JUMP_SEEK, Sem.BR_ZERO_SEEK, Sem.BR_NEG_SEEK})


def hw_micro(sem: Sem) -> tuple[tuple[str, str | None], ...]:
    """The hardware micro-program for ``sem``, or ``()`` for the structured ones."""
    return _HW.get(sem, ())


class MachineError(RuntimeError):
    """The geometry did not close — with the constraint that failed."""


# ── grid ─────────────────────────────────────────────────────────────────────
class _Grid:
    def __init__(self) -> None:
        self.c: dict[tuple[int, int], str] = {}
        #: Every cell this grid drew as part of a *pipe*, as opposed to a room wall
        #: or a room's interior. Nothing on the grid says which is which — ``|`` is
        #: both a wall and a vertical pipe body — so a router that wants to keep its
        #: legs off other pipes has to be told, and this is the record.
        self.drawn: set[tuple[int, int]] = set()

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
        self.drawn.update(cells)
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


def _relabel_slots(
    placed: dict[str, int], want: Mapping[str, int], lanes: int
) -> dict[str, int]:
    """Apply a rank-preserving leaf relabelling to ``placed``.

    Every failure mode here is one that would silently move a lane — a missing
    mnemonic, a duplicate slot, or a map that permutes the north-to-south order —
    so all three are errors rather than a best effort. See :func:`plan`.

    A map may name opcodes this build does not use, and only those: whether
    ``JMPS``/``BRZS``/``BRNS`` exist at all is :func:`seek_split`'s decision, made
    from a threshold the registry cannot evaluate, so one registered map has to
    serve ``seek=True`` and ``seek=False`` alike. Dropping names from both sides
    of a sorted comparison cannot reorder what is left, so the rank check below
    still holds on the subset.
    """
    missing = sorted(set(placed) - set(want))
    if missing:
        raise MachineError(f"opcode slot map does not name the used opcodes {missing}")
    want = {m: s for m, s in want.items() if m in placed}
    if len(set(want.values())) != len(want):
        raise MachineError("opcode slot map assigns one slot twice")
    bad = sorted(m for m, s in want.items() if not 0 <= s < lanes)
    if bad:
        raise MachineError(f"opcode slot map is outside 0..{lanes - 1} for {bad}")
    if sorted(placed, key=lambda m: want[m]) != sorted(placed, key=lambda m: placed[m]):
        raise MachineError(
            "opcode slot map must preserve the lanes' north-to-south order — it "
            "re-labels leaves, it does not re-order lanes; use middle_order for that"
        )
    return dict(want)


def plan(
    program: Program,
    *,
    middle_order: Sequence[str] | None = None,
    slots: Mapping[str, int] | None = None,
) -> _Plan:
    """Assign lane rows by pipe need, then derive opcode numbers from the trie.

    The trie sorts its leaves in **bit-reversed** order (``ARCH.md`` §2.4), so
    picking a row *chooses* the opcode number. IN goes to the top row beside the
    north pipe, OUT to the bottom row, the LM-75 lanes just above it beside the
    south wall their pipes leave from, and everything else in between — longest
    micro-program first, so the drop columns form a descending staircase and short
    lanes can turn south early instead of all walking out to a shared column.

    ``middle_order`` overrides that last rule — the order of the *unpinned* lanes,
    north to south. Length-descending is a good guess in the dark, but the row a
    lane sits on is a **tick** cost and the weight on it is how often the opcode
    runs, which length knows nothing about: a lane's return walk is
    ``2 * drop_x - row`` (east to the drop column, south to the collector, west
    along it), so a hot lane wants to be *low* on both terms at once. See
    ``LANE_ORDER`` for the per-program orders this bought and §7.6 for the method.

    ``slots`` re-labels the *leaf slots* the lanes land on without moving a single
    lane. Under ``trim_dead`` a lane's row is ``y0 + 2 * rank(slot)`` — the rank of
    its slot among the used ones, not the slot itself — so any **rank-preserving**
    relabelling leaves every row, every drop column and every measured lane tick
    exactly where they were, and changes only ``number = _bitrev(slot, k)``. That
    is a pure *ROM-encoding* knob: an opcode below 10 is one digit and costs the
    drum ``Ns`` = 2 cells, one at 10 or above costs ```NN`s`` = 5, and which ten of
    the ``1 << k`` slots bit-reverse below 10 is fixed geometry the default
    contiguous packing has no way to aim at. See :data:`OPCODE_SLOTS`.

    The rank-preserving condition is *enforced*, not documented: a map that
    reorders the lanes is rejected, so this can never silently undo a tuned
    ``LANE_ORDER``. It is only row-neutral under ``trim_dead`` (:func:`build`
    checks that), because the untrimmed band puts a lane at ``2 * slot + 1``.
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
            return (2, DSP_LANE_BANDS.index(DSP_SEM_BAND[s]), m)
        if s in STREAM_SEM_BAND:
            return (2, STREAM_BANDS.index(STREAM_SEM_BAND[s]), m)
        return (group(m), -width(m), m)

    order = sorted(used, key=rank)
    free = list(range(lanes))
    placed: dict[str, int] = {}
    for m in [n for n in order if group(n) == 0]:
        placed[m] = free.pop(0)
    for g in (3, 2):
        for m in reversed([n for n in order if group(n) == g]):
            placed[m] = free.pop()
    middle = [n for n in order if group(n) == 1]
    if middle_order is not None:
        want = list(middle_order)
        if sorted(want) != sorted(middle):
            raise MachineError(
                f"middle_order must be a permutation of the unpinned lanes "
                f"{sorted(middle)}; got {sorted(want)}"
            )
        middle = want
    for m in middle:
        placed[m] = free.pop(0)

    if slots is not None:
        placed = _relabel_slots(placed, slots, lanes)

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


def _target_index(program: Program, instrs: Sequence, index_of_word: dict, k: int) -> int:
    """Which instruction a jump/branch at ``instrs[k]`` lands on."""
    ins = instrs[k]
    assert ins.operand is not None
    after = (ins.pos + ins.words) % program.P
    target_word = (after + ins.operand) % program.P
    if target_word not in index_of_word:
        raise MachineError(
            f"{ins.mnemonic} at word {ins.pos} jumps to word {target_word}, "
            "which is not an instruction boundary"
        )
    return index_of_word[target_word]


#: A taken jump discards ~4.5 ticks a word, while a drum seek is a flat few
#: hundred whatever the distance — so the seek only pays above a crossover.
#: Measured on ``deadman-3d``: a seek is ~1,140 ticks and a discarded word 4.5,
#: putting the break-even near 250 words. 256 is that, rounded to a power of two.
#:
#: The distribution is what makes the split worth having rather than a tuning
#: knob: in a ``deadman-3d`` frame **186 jumps of 2,610 skip 84.5% of all the
#: words**, so seeking only those buys nearly the whole discard bill while the
#: 2,424 tight-loop jumps keep the counted discard they are already good at.
#:
#: Re-derived after M7b/M8 changed the image (P 3,957 -> 4,002 words, 2,152
#: instructions) — the shape is unmoved. Frame 1 of the demo walk, 2,610 taken
#: jumps discarding 387,532 ring words (``scratch/ram-program/jump_hist.py``):
#:
#: | skip (ring words) | jumps | words | share of the bill |
#: |---|---|---|---|
#: | 0-64 | 2,418 | 58,952 | 15.2% |
#: | 64-256 | 6 | 1,151 | 0.3% |
#: | 256-1024 | 58 | 34,796 | 9.0% |
#: | 1024+ | 128 | 292,633 | **75.5%** |
#:
#: The threshold sweep is a *plateau*, which is why the exact value does not
#: matter much and why 256 is still the right corner. In fixed-image units,
#: ``JMPF`` alone: thr 64 -> 68.1% of the bill, 128 -> 68.1%, 192 -> 68.0%,
#: **256 -> 67.9%**, 384 -> 66.4%, 512 -> 65.5%, 1024 -> 59.0%. Dropping below
#: 256 buys 0.2 points of bill while paying a flat ~1,140-tick seek for jumps
#: whose discard was under ~1,150 — i.e. nothing, at real risk. Above 384 the
#: cliff starts.
SEEK_THRESHOLD = 256


#: Which classic opcodes ``seek_split`` may rewrite. Every extra family costs a
#: lane *and* a 13-column slab, and the slab band is what pushes the memory
#: block east, so restricting this is the footprint knob. Measured on
#: ``deadman-3d``: ``JMPF`` alone carries 80% of the long-jump words — re-derived
#: post-M8 as **263,260 of 327,429** in frame 1, i.e. 67.9% of the frame's whole
#: 387,532-word discard bill, for a third of the width the full set costs. The
#: only other family with long jumps is ``BRZ`` (25 jumps, 64,169 words); ``BRN``
#: has none in steady state (its 107 long jumps are all boot's).
#:
#: **``BRZ`` was re-tried on the taped tier and it does not pay.** It captures
#: exactly the bill it was predicted to — 64,169 of frame 1's 387,532 words, so
#: the split goes from 67.9% to **84.5%** of the discard, +16.6 points — and buys
#: essentially nothing (native/fast engine, taped, the 57-command ``WALK``):
#:
#: | build | box | ticks | Δ |
#: |---|---|---|---|
#: | ``JMPF`` (shipped) | 295x269 | 591,485,564 | — |
#: | ``JMPF``+``BRZ`` | **309x271** | 588,983,630 | **-0.42%** |
#:
#: The extra slab lifts the ``mem_pad`` floor 22 -> 29 (the progression the
#: paragraph above predicts), and that pad charges every memory instruction the
#: extra walk twice. DOOM's taped tier is memory-bound, so the pad gives back
#: almost the whole discard win. What is left costs **14 columns**: the width
#: floors at 309 whatever the fold — ``rom_rows`` 80..110 all land on 309, because
#: the *store* binds the width, not the drum — which breaks the taped machine's
#: checked-in 300 ceiling (``test_deadman3d.py``) for four tenths of a percent.
#: Not shipped. ``SEEK_THRESHOLD`` needs no re-tuning either: ``JMPF``+``BRZ`` is
#: the same plateau (thr 64 -> 84.7%, 256 -> 84.5%, cliff still above 384).
SEEK_OPS: tuple[str, ...] = ("JMPF",)


def seek_split(
    program: Program,
    *,
    threshold: int = SEEK_THRESHOLD,
    ops: Sequence[str] = SEEK_OPS,
) -> Program:
    """Rewrite long jumps/branches to their seek-drum opcodes (build-time only).

    Source, listings and the emulator keep talking about ``JMPF``/``BRZ``/
    ``BRN``; only the *hardware* image distinguishes them, so this returns a
    copy of ``program`` whose long structured instructions carry the ``*S``
    mnemonics and their sems. Distance is the classic forward-skip count, which
    is exactly what the discard would have paid for.
    """
    instrs = sorted(program.instrs, key=lambda i: i.pos)
    n = len(instrs)
    index_of_word = {ins.pos: k for k, ins in enumerate(instrs)}
    allowed = set(ops)
    out = []
    for k, ins in enumerate(instrs):
        if ins.sem in TARGET_SEMS and ins.sem in SEEK_OF and ins.mnemonic in allowed:
            skip = 2 * ((_target_index(program, instrs, index_of_word, k) - k - 1) % n)
            if skip >= threshold:
                seek_sem = SEEK_OF[ins.sem]
                op = next(o for o in program.isa if o.sem is seek_sem)
                out.append(ins.model_copy(update={"mnemonic": op.mnemonic, "code": op.code, "sem": seek_sem}))
                continue
        out.append(ins)
    return program.model_copy(update={"instrs": tuple(out)})


def seek_words(program: Program, p: _Plan, *, rows: int):
    """The fixed-width image for a seek build, and its drum layout.

    A **seek** operand is ``row * SEEK_K + offset`` of the target's opcode word
    in the *packed* drum; a classic jump keeps its forward-skip count. The seek
    operands depend on the packing, which depends on the token widths, which
    depend on the operands — so they are emitted as fixed-width zero-padded
    literals and the layout is a two-pass fixed point.
    """
    from .seekrom import SEEK_K, build_seek_rom, seek_target

    instrs = sorted(program.instrs, key=lambda i: i.pos)
    n = len(instrs)
    index_of_word = {ins.pos: k for k, ins in enumerate(instrs)}

    def encode(target_operand: dict[int, int]) -> list[int]:
        out: list[int] = []
        for k, ins in enumerate(instrs):
            if ins.sem in _SEEK_SEMS:
                operand = target_operand.get(k, 0)
            elif ins.sem in TARGET_SEMS:
                t = _target_index(program, instrs, index_of_word, k)
                operand = 2 * ((t - k - 1) % n)
            elif ins.operand is None:
                operand = 0
            else:
                operand = ins.operand
                if ins.sem in MEMORY_SEMS and operand < 1:
                    raise MachineError(
                        f"{ins.mnemonic} at word {ins.pos} addresses STORE slot "
                        f"{operand}; hardware addresses start at 1"
                    )
            out += [p.number[ins.mnemonic], operand]
        return out

    targets = {
        k: _target_index(program, instrs, index_of_word, k)
        for k, ins in enumerate(instrs)
        if ins.sem in _SEEK_SEMS
    }
    if not targets:
        raise MachineError(
            "seek=True but no jump is long enough to seek; build without it"
        )
    # Fixed-width literals for the seek operands only, so the packing does not
    # move as their values resolve.
    wide = frozenset(2 * k + 1 for k in targets)
    operands = {k: 0 for k in targets}
    probe = build_seek_rom(encode(operands), rows=rows, wide=wide, wide_digits=5)
    digits = len(str((probe.rows_used + 2) * SEEK_K))
    layout = build_seek_rom(encode(operands), rows=rows, wide=wide, wide_digits=digits)
    for _ in range(4):
        operands = {k: seek_target(layout, 2 * t) for k, t in targets.items()}
        words = encode(operands)
        new_layout = build_seek_rom(words, rows=rows, wide=wide, wide_digits=digits)
        if new_layout.word_pos == layout.word_pos:
            return words, new_layout
        layout = new_layout
    raise MachineError("seek operand layout did not converge")


# ── the CPU room ─────────────────────────────────────────────────────────────
_STRUCT_X0 = 2  # slabs hug the west wall, keeping their `r` nearest the ROM pipe
_STRUCT_X0_SEEK = 5  # seek mode: columns 1..4 belong to the flush/remainder tail
_SLAB_PITCH = 13  # columns per slab: each gets its own band (see _slab)
#: The narrowest pitch the band is *drawn* at. A branch slab spans exactly eleven
#: columns — its exit riser at ``base - 1`` through the ``neg`` arm at ``base + 9``
#: — so eleven is where consecutive slabs touch without overlapping, and ten makes
#: slab ``i``'s riser share a column with slab ``i-1``'s ``neg`` arm. That happens
#: to *work* when the shared column is another riser (both men head north and the
#: collector catches both), which is luck, not geometry: relabel which arm a branch
#: takes and it becomes a drop into a riser. Ten measured 0.01% better than eleven
#: on `little-little-man` — inside the noise, and not worth standing on.
_SLAB_PITCH_FLOOR = 11
_JUMP_SLAB_ROWS = 4
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
    #: The IN lane's ``r`` column — where the input pipe enters the *north* wall
    #: when the machine opts into :data:`INPUT_NORTH` (see there for why).
    in_col: int = 0
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


def _uneven_trie(
    k: int, slot_rows: dict[int, int], lane_x0: int
) -> tuple[int, dict[tuple[int, int], str]]:
    """Lay a depth-``k`` decode trie pruned to the *used* leaf slots.

    Dead-lane removal (``TRIM_DEAD_LANES``) compacts the lane band to one row pair
    per used opcode, so the trie can no longer spread its branches by ``1 << k``:
    each subtree's vertical extent is exactly its surviving-leaf band, a branching
    ``x`` sits on the gap row above its down-half's first lane, and every
    single-child chain is contracted into one edge straight to its branching
    descendant (or lane). Semantics that must survive the re-routing:

    * an ``x`` at original trie level ``L`` tests BP's low bit, which must be bit
      ``L-1`` of the opcode — so exactly ``L-1`` ``]``-shifts must precede it.
      Levels get **two** columns here (``x`` at ``3 + 2L``) so an edge that
      contracts ``d`` levels has ``2d - 1 >= d`` horizontal cells for its ``]``s;
      the uniform trie's vertical ``]`` at distance 1 cannot host more than one.
    * a contracted path *stops* shifting once no ``x`` remains below it, so a lane
      may be entered with junk in BP. That is safe: no lane micro-program reads BP
      before writing it (flat lanes never touch it; jump lanes open with ``b``,
      branch arms with ``W`` then ``b``).

    Returns the root entry row — the fetch row — and the cells. Opcode numbers are
    untouched: the ROM image is byte-identical to the uniform trie's.
    """
    cells: dict[tuple[int, int], str] = {}
    used = sorted(slot_rows)

    def node(level: int, lo: int, hi: int) -> tuple[int, int | None]:
        """Entry (row, level) of the subtree over slots [lo, hi); level None = lane."""
        sl = [s for s in used if lo <= s < hi]
        mid = lo
        up: list[int] = []
        down: list[int] = []
        while len(sl) > 1:
            mid = (lo + hi) // 2
            up = [s for s in sl if s < mid]
            down = [s for s in sl if s >= mid]
            if up and down:
                break
            # Single-child level: contract it — the edge below carries its `]`.
            lo, hi = (lo, mid) if up else (mid, hi)
            level += 1
        if len(sl) == 1:
            return slot_rows[sl[0]], None
        col = 3 + 2 * level
        xrow = slot_rows[min(down)] - 1  # the gap row above the down half
        cells[(col, xrow)] = "x"
        for half, sign in (((lo, mid), -1), ((mid, hi), +1)):
            crow, clevel = node(level + 1, *half)
            for yy in range(xrow + sign, crow, sign):
                cells[(col, yy)] = "."
            cells[(col, crow)] = ">"
            shifts = 0 if clevel is None else clevel - level
            end = (2 + 2 * clevel) if clevel is not None else (lane_x0 - 1)
            for i, cx in enumerate(range(col + 1, end + 1)):
                cells[(cx, crow)] = "]" if i < shifts else "."
        return xrow, level

    entry, elevel = node(1, 0, 1 << k)
    # The approach from the fetch cell: `>rbr` ends at column 4, the first `x` (or
    # the lone lane) starts east of it, and a contracted root still owes its shifts.
    shifts = 0 if elevel is None else elevel - 1
    end = (2 + 2 * elevel) if elevel is not None else (lane_x0 - 1)
    for i, cx in enumerate(range(5, end + 1)):
        cells[(cx, entry)] = "]" if i < shifts else "."
    return entry, cells


def _tight_struct_entry(
    p: _Plan,
    structured: list[str],
    row_of: dict[str, int],
    struct_x0: int,
    drain_unit_bits: int,
    pitch: int = _SLAB_PITCH,
) -> tuple[dict[str, int], frozenset[int]]:
    """Westmost legal entry column per slab, and the columns no entry may use.

    The slab band is a staircase: slab ``i`` sits at ``struct_x0 + i * pitch``
    on entry row ``collector + 1 + i``, one pitch east and one row south of slab
    ``i - 1``. A structured lane's drop therefore only has to clear the lane band
    (the caller's ``floor``) and land inside **its own** column band — every
    shallower body stops at ``base - pitch + 9``, west of the westmost entry this
    returns, and every deeper one starts a whole pitch east.
    The ``struct_east + 1`` floor the default rule uses is a much stronger
    condition than the geometry needs.

    Two families of column are still forbidden outright, because a slab's *risers*
    run up to the collector and would meet the drop head-on there:

    * ``base - 1`` — every slab's exit riser, the column the discarded-and-done
      man climbs on;
    * ``base + 3 / 6 / 9`` — a branch's three arm columns; the two not-taken arms
      rise to the collector on theirs.

    A drop sharing one of those leaves `.` on the collector row where the riser
    needs its `<`, and the rising man sails north into the lane band instead of
    turning west for the fetch site. They are reserved for every slab, not only
    the shallower ones, so the answer does not depend on which slab is asking.

    The westmost entry itself is per-slab:

    * a branch turns its man south at ``base``, so ``base + 1`` is enough;
    * a counted-discard jump owns ``a<`` at ``base``/``base + 1``, so ``base + 2``;
    * a drained jump (:func:`_drain_block`) turns south on the block's spine at
      ``base - 1 + spine``, so the drop must land at least one cell east of it.
    """
    order = sorted(structured, key=lambda m: -row_of[m])
    base = {m: struct_x0 + i * pitch for i, m in enumerate(order)}
    spine = 0
    if drain_unit_bits:
        from .drain import build_drain

        spine = build_drain(0, unit_bits=drain_unit_bits, even=True).spine
    first: dict[str, int] = {}
    reserved: set[int] = set()
    for m in order:
        b = base[m]
        reserved.add(b - 1)
        if p.sem[m] in _JUMP_SEMS:
            first[m] = b + spine if drain_unit_bits else b + 2
        else:
            first[m] = b + 1
            reserved |= {b + 3, b + 6, b + 9}
    return first, frozenset(reserved)


def build_cpu(
    program: Program,
    p: _Plan,
    *,
    mem_pad: int = 0,
    stream_pad: int = 0,
    short_return: bool = True,
    drain_unit_bits: int = 0,
    trim_dead: bool = False,
    top_bus: bool = False,
    seek: bool = False,
    seek_taken_drop_east: bool = False,
    tight_drops: bool = False,
    slab_pitch: int = _SLAB_PITCH,
) -> _Cpu:
    """Lay the CPU: fetch, decode trie, lanes, structures band, return path.

    ``short_return`` lets a simple lane drop at the end of its own micro-program
    rather than east of the slab band; see the drop-column comment. It narrows the
    CPU, which ``matmul``'s STREAM wiring does not currently survive.

    ``trim_dead`` removes the unused leaf slots' rows (see :func:`_uneven_trie`);
    ``top_bus`` adds a second return bus above the band and routes each simple
    lane over whichever bus is cheaper; ``tight_drops`` walks each slab's entry
    column back to its own band (:func:`_tight_struct_entry`); ``slab_pitch``
    narrows the staircase's step. All four default off/unchanged and leave the
    layout byte-identical; opt in per slug via :data:`TRIM_DEAD_LANES` /
    :data:`TOP_RETURN_BUS` / :data:`TIGHT_STRUCT_DROPS` / :data:`SLAB_PITCH`.
    """
    if (trim_dead or top_bus) and not short_return:
        raise MachineError("trim_dead/top_bus require the short-return drop rule")
    if tight_drops and not short_return:
        raise MachineError("tight_drops requires the short-return drop rule")
    if slab_pitch < _SLAB_PITCH_FLOOR:
        raise MachineError(
            f"slab pitch {slab_pitch} is below the {_SLAB_PITCH_FLOOR}-column span a "
            "branch slab occupies (`base - 1` .. `base + 9`); slabs would overlap"
        )
    if tight_drops and seek:
        # Seek slabs are a different shape (a taken row *below* the band, a shared
        # flush tail in columns 1..4) and their entry geometry has not been proved
        # against a tightened column. Say so rather than emit an unvalidated grid.
        raise MachineError("tight_drops is not supported with the seek drum")
    k, lanes = p.k, p.lanes
    used = list(p.number)
    bus_row = 1
    y0 = 2 if top_bus else 1  # with a top bus, row 1 belongs to the bus
    if trim_dead:
        slots = sorted((p.row[m] - 1) // 2 for m in used)
        rank = {s: i for i, s in enumerate(slots)}
        row_of = {m: y0 + 2 * rank[(p.row[m] - 1) // 2] for m in used}
        n_rows = len(slots)
        lane_x0 = 4 + 2 * k  # two columns per trie level (see _uneven_trie)
    else:
        row_of = {m: p.row[m] + (y0 - 1) for m in used}
        n_rows = lanes
        lane_x0 = 5 + k
    span = 2 * n_rows - 1
    by_row = {row_of[m]: m for m in used}
    all_rows = [y0 + 2 * i for i in range(n_rows)]
    if trim_dead:
        centre, trie_cells = _uneven_trie(
            k, {(p.row[m] - 1) // 2: row_of[m] for m in used}, lane_x0
        )
    else:
        centre, trie_cells = (1 << k) + (y0 - 1), None

    flat = {m: hw_micro(p.sem[m]) for m in used if p.sem[m] in _HW}
    structured = [m for m in used if p.sem[m] in _JUMP_SEMS | _BRANCH_SEMS]
    halting = {row_of[m] for m in used if p.sem[m] is Sem.HALT}

    prefixes = [
        next((i for i, (_, b) in enumerate(mc) if b == Band.MEM), len(mc))
        for mc in flat.values()
        if any(b == Band.MEM for _, b in mc)
    ]
    mem_x = lane_x0 + (max(prefixes) if prefixes else 0) + mem_pad
    band_x = {Band.MEM: mem_x}

    # Display lanes: one `s` per port, spread ``_DSP_PITCH`` columns apart so each
    # binds the pipe that leaves the south wall directly beneath it.
    dsp_used = [b for b in DSP_LANE_BANDS if any(b == bb for mc in flat.values() for _, bb in mc)]
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
    # A drain hangs *below* the entry row rather than beside it, so it sets the
    # slab's depth: a jump is its entry row plus the block, a branch is its four
    # rows of `X` fan-out and turn row plus the block.
    if seek and drain_unit_bits:
        raise MachineError("seek mode replaces the discard entirely; no drain to size")
    if seek:
        # Hybrid: a *seek* slab is shallow (a drop to the taken row, plus the
        # branch's X fan-out); a *classic* slab keeps its counted discard, which
        # short jumps are already good at. Bases start at 5 because the seek
        # tail owns columns 1..4 (flush loop, remainder read, its discard).
        slab_rows = {}
        for m in structured:
            if p.sem[m] in _SEEK_SEMS:
                slab_rows[m] = 2 if p.sem[m] in _JUMP_SEMS else 5
            else:
                slab_rows[m] = (
                    _JUMP_SLAB_ROWS if p.sem[m] in _JUMP_SEMS else _BRANCH_SLAB_ROWS
                )
        # The tail lives strictly below the band, so the slabs keep column 2 —
        # which is what keeps a classic discard `r` nearest the ROM pipe.
        struct_x0 = _STRUCT_X0
    elif drain_unit_bits:
        from .drain import build_drain

        _drain_h = build_drain(0, unit_bits=drain_unit_bits, even=True).height
        slab_rows = {m: (1 if p.sem[m] in _JUMP_SEMS else 5) + _drain_h for m in structured}
        struct_x0 = _STRUCT_X0
    else:
        slab_rows = {
            m: (_JUMP_SLAB_ROWS if p.sem[m] in _JUMP_SEMS else _BRANCH_SLAB_ROWS)
            for m in structured
        }
        struct_x0 = _STRUCT_X0
    struct_east = struct_x0 + max(1, len(structured)) * slab_pitch

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
            # Seek mode keeps the operand (row*K+rem) in A — no count to park.
            # A seek jump keeps the operand in A (it is sent, not counted); a
            # classic jump parks it in BP for the discard loop.
            pre = ("." if p.sem[m] in _SEEK_SEMS else "b") if p.sem[m] in _JUMP_SEMS else "W"
            lane_cells[(lane_x0, r)] = (pre, None)
            lane_end[r] = lane_x0

    # ── bus choice: which lanes return over the top instead of the bottom ────
    # The top bus (row 1) is the collector's mirror: a lane exits east, *rises* on a
    # column clear of every lane above it — the running prefix maximum of
    # ``lane_end``, the same bookkeeping as the drops' suffix maximum — walks west
    # along row 1, and descends column 1 into the fetch cell. Cheaper for lanes near
    # the top of the band, which otherwise pay the whole band height twice (down to
    # the collector, back up the riser). Slab lanes are excluded: their drops
    # continue past the collector into the structures band below.
    top_lanes: set[int] = set()
    collector = all_rows[-1] + 1
    if top_bus:
        prefix_floor: dict[int, int] = {}
        pre = lane_x0 - 1
        for r in all_rows:
            pre = max(pre, lane_end[r])
            prefix_floor[r] = pre + 1
        suffix_floor: dict[int, int] = {}
        suf = lane_x0 - 1
        for r in reversed(all_rows):
            suf = max(suf, lane_end[r])
            suffix_floor[r] = suf + 1
        for r in all_rows:
            m = by_row.get(r)
            if m is None or r in halting or m in slab_rows:
                continue
            down_cost = (
                (suffix_floor[r] - lane_end[r])
                + (collector - r)
                + (suffix_floor[r] - 1)
                + (collector - centre)
            )
            up_cost = (
                (prefix_floor[r] - lane_end[r])
                + (r - bus_row)
                + (prefix_floor[r] - 1)
                + (centre - bus_row)
            )
            if up_cost < down_cost:
                top_lanes.add(r)

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
    #
    # ``tight_drops`` replaces that structural disjointness with a bookkept one —
    # see :func:`_tight_struct_entry` for the geometry and :data:`TIGHT_STRUCT_DROPS`
    # for why it is worth doing.
    drop_x: dict[int, int] = {}
    assigned: set[int] = set()
    struct_cols: set[int] = set()
    tight_first, tight_reserved = (
        _tight_struct_entry(p, structured, row_of, struct_x0, drain_unit_bits, slab_pitch)
        if tight_drops
        else ({}, frozenset())
    )
    if short_return:
        floor = lane_x0
        struct_min = lane_x0
        for r in sorted(all_rows, reverse=True):
            # Halting rows carry no drop but do carry glyphs, so they still raise the
            # floor for everything above them — as do top-bus lanes, whose return
            # leaves by the ascent column assigned after the drops.
            floor = max(floor, lane_end[r] + 1)
            if r in halting or r in top_lanes:
                continue
            m = by_row.get(r)
            if m is not None and m in slab_rows:
                # A slab's entry column must be unique in *both* directions: its `<`
                # turns an arriving man west, so any other drop sharing the column
                # would be swallowed by this slab's entry row.
                #
                # ``struct_min`` keeps the structured columns strictly increasing
                # bottom-to-top, which is what makes ``order`` below reproduce the
                # slab order ``tight_first`` was computed against. Without it a
                # bumped column could overtake the next lane's and silently pair a
                # slab with another slab's entry column.
                c = (
                    max(floor, tight_first[m], struct_min)
                    if tight_drops
                    else max(floor, struct_east + 1)
                )
                while c in assigned or c in tight_reserved:
                    c += 1
                struct_min = c + 1
                struct_cols.add(c)
            else:
                # A micro-program long enough to reach the slabs has to join the
                # structured lanes' uniqueness discipline rather than risk
                # their columns.  Never reset it to ``struct_east + 1``:
                # ``floor`` is the suffix maximum that proves this column clears
                # the current lane and every lane below it. Moving west from that
                # floor can put the drop directly on a live lane glyph.
                #
                # ``struct_cols`` is what that discipline is actually protecting: a
                # slab entry leaves `.` on the collector row, and a simple man
                # sharing the column would sail past his turn west into the slab.
                # Under ``tight_drops`` it is tracked directly, so simple lanes go
                # back to sharing columns freely — two `v`s in one column are fine
                # (a southbound man keeps his heading over a `v`), and every lane
                # east of ``struct_east`` otherwise pays for a uniqueness it never
                # needed against its neighbours.
                c = floor
                while c in struct_cols or (not tight_drops and c > struct_east and c in assigned):
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
    order = sorted(structured, key=lambda m: drop_x[row_of[m]])
    if tight_drops and order != sorted(structured, key=lambda m: -row_of[m]):
        # ``tight_first`` priced each slab against the base this order gives it, so a
        # disagreement means the two disciplines have drifted and the entry columns
        # would be measured against the wrong slabs. Fail rather than mis-wire.
        raise MachineError("tight slab entries: the drop order left the slab order")
    slab_at: dict[str, int] = {}
    slab_base: dict[str, int] = {}
    # The collector sits **immediately** below the lane band, above the slabs, and
    # slab exits *rise* into it rather than dropping past it. That is worth real
    # ticks: every instruction walks the riser from the collector up to the fetch
    # row, so putting the collector below ~21 rows of slabs made the riser 38 cells
    # instead of 16 — paid once per instruction, for the whole program. A profile
    # (`tools/heatmap.mjs` + `lm1.profile`) put the return path at 25 % of the CPU's
    # time before this.
    # (``collector`` itself is defined with the bus choice above: the row below
    # the last lane, wherever dead-lane removal left it.)
    # Entry rows are stacked directly — slab *i* enters on ``collector + 1 + i`` —
    # rather than each slab getting a private row band. Only the entry row is an
    # exclusive resource: it is the westbound run slab *i*'s drop lands on, and it
    # spans ``[base_i, drop_x_i]``, which crosses every *shallower* body band but
    # stops east of every deeper one (``base_j > base_i + slab_pitch - 1`` for
    # ``j > i``). So slab *i*'s body, hanging directly below its own entry row
    # inside its own column band, is crossed by no other slab's entry run, and the
    # bands overlap in rows for free: ``n`` entry rows plus the tallest body,
    # against the old staircase's sum of all the bodies. Risers and drops that
    # must pass *through* an entry row leave `.` holes in its `<` run (they are
    # drawn first; the `<`s are ``soft``), and a westbound man keeps his heading
    # over a `.` — the same mechanism the drop columns have always used.
    for i, m in enumerate(order):
        slab_at[m] = collector + 1 + i
        slab_base[m] = struct_x0 + i * slab_pitch
    bottom = max((slab_at[m] + slab_rows[m] for m in order), default=collector + 1)
    taken_row = bottom + 1  # seek mode only: the eastbound send row below the slabs
    if seek:
        bottom = taken_row + 9

    # ── ascent columns for the top bus, the drops' mirror ────────────────────
    # Assigned *after* the drops so they can refuse every drop column: a rising
    # man crossing a drop's `v` would be turned south, and a dropping man crossing
    # an ascent's `^` would be turned north. Two ascents may share a column — a
    # northbound man keeps his heading over another lane's `^` — just as simple
    # drops have always shared.
    asc_x: dict[int, int] = {}
    if top_lanes:
        pre = lane_x0 - 1
        for r in all_rows:
            pre = max(pre, lane_end[r])
            if r in top_lanes:
                c = pre + 1
                while c in assigned:
                    c += 1
                asc_x[r] = c

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

    if trie_cells is None:
        trie(1, centre)
    else:
        # Dead-lane removal: the pruned, contracted trie (see _uneven_trie).
        for (x, yy), ch in trie_cells.items():
            g.put(x, yy, ch)

    for (x, yy), (glyph, band) in lane_cells.items():
        emit(x, yy, glyph, band)
    for r in all_rows:
        if r in halting:
            continue
        for x in range(lane_end[r] + 1, (asc_x[r] if r in top_lanes else drop_x[r])):
            g.soft(x, r, ".")

    # ── drops: simple lanes to the collector, structured ones to their slab ──
    # Only the *head* of a drop is a `v`; the rest is `.`. A southbound man keeps
    # his heading over a `.`, and so does a westbound one — which is what lets a
    # drop cross a slab's westbound entry row at all. A `v` there would turn the
    # entry man south into the middle of the drop.
    for r in all_rows:
        if r in halting or r in top_lanes:
            continue
        g.put(drop_x[r], r, "v")
    for r in all_rows:
        if r in halting or r in top_lanes:
            continue
        m = by_row.get(r)
        stop = slab_at[m] if (m is not None and m in slab_at) else collector
        for yy in range(r + 1, stop):
            g.soft(drop_x[r], yy, ".")  # crosses the collector row for a slab lane

    # ── the top bus: rises east of the band, returns along row 1, drops col 1 ──
    # Heads first (hard), then the runs (soft), so two ascents sharing a column
    # keep both `^`s. The bus row was reserved by ``y0``: nothing else is on row 1,
    # and column 1 above the fetch row is blank in every layout.
    for r, c in sorted(asc_x.items()):
        g.put(c, r, "^")
    for r, c in asc_x.items():
        for yy in range(bus_row + 1, r):
            g.soft(c, yy, ".")
    if top_lanes:
        g.put(1, bus_row, "v")
        for x in range(2, max(asc_x.values()) + 1):
            g.soft(x, bus_row, "<")
        for yy in range(bus_row + 1, centre):
            g.soft(1, yy, ".")

    # ── structures band ──────────────────────────────────────────────────────
    struct_drops: set[int] = set()
    taken_drops: list[int] = []
    #: Where each seek slab turns south off its entry row; ``slab_base`` unless
    #: :data:`SEEK_TAKEN_DROP_EAST` straightened it (see below).
    turn_x: dict[str, int] = {}
    if seek:
        # Send-and-flush slabs (engine-proven by the Stage-2 RAM work): the
        # taken path carries `row*K+rem` in A down to the eastbound taken row,
        # sends it out the east-wall request pipe, then drops into the flush
        # block: r/X until the drum's sentinel (-1), read the remainder, and
        # run the stock 2x4 counted discard. ACC stays in B throughout.
        # A seek *jump* has no slab body at all — it is one turn south and a drop —
        # so the column it turns at is a free choice, unlike a branch's, whose `X`
        # fan-out really does start at ``base``. Taking ``base`` anyway makes the man
        # walk west across the whole slab band on the entry row and then straight
        # back east along the taken row to reach the ``s``: a U-turn of
        # ``2 * (e_s - 1 - base)`` ticks on every taken jump, and it holds nothing.
        # ``jump_x`` instead turns him at the last column that still lands west of
        # the ``s``. That column is east of ``struct_east``, hence east of every slab
        # body, so the drop passes through nobody's slab on the way down.
        jump_x = struct_east + 1 if seek_taken_drop_east else None
        for m in order:
            s0, base = slab_at[m], slab_base[m]
            if p.sem[m] not in _SEEK_SEMS:
                # A short jump keeps the classic counted discard, verbatim.
                struct_drops |= _slab(
                    g, m, p, s0, base, collector, pipe_glyphs, drain_unit_bits
                )
            elif p.sem[m] in _JUMP_SEMS:
                # never west of ``base`` (that is the shipped column and the floor),
                # never east of the lane's own drop (its `<` owns that cell).
                jx = base if jump_x is None else max(base, min(jump_x, drop_x[row_of[m]] - 1))
                for yy in range(s0 + 1, taken_row):
                    if jx != base and (jx, yy) in g.c:
                        raise MachineError(f"seek jump drop column {jx} is occupied at y={yy}")
                    g.soft(jx, yy, ".")
                taken_drops.append(jx)
                turn_x[m] = jx
            else:
                g.soft(base, s0 + 1, ".")
                g.put(base, s0 + 2, ">")
                g.put(base + 1, s0 + 2, "X")
                g.put(base + 1, s0 + 1, ">")
                g.put(base + 1, s0 + 3, ">")
                arm_rows = {"neg": s0 + 1, "zero": s0 + 2, "pos": s0 + 3}
                arm_cols = {"neg": base + 9, "zero": base + 6, "pos": base + 3}
                taken = "zero" if p.sem[m] is Sem.BR_ZERO_SEEK else "neg"
                for arm, row in arm_rows.items():
                    g.put(base + 2, row, "W")
                    for cc in range(base + 3, arm_cols[arm]):
                        g.soft(cc, row, ".")
                    if arm == taken:
                        g.put(arm_cols[arm], row, "v")
                        for yy in range(row + 1, taken_row):
                            g.soft(arm_cols[arm], yy, ".")
                        taken_drops.append(arm_cols[arm])
                    else:
                        g.put(arm_cols[arm], row, "^")
                        for yy in range(collector + 1, row):
                            g.soft(arm_cols[arm], yy, ".")
                        struct_drops.add(arm_cols[arm])
        for m in order:
            s0, dx = slab_at[m], drop_x[row_of[m]]
            base = slab_base[m]
            g.put(dx, s0, "<")
            if p.sem[m] not in _SEEK_SEMS and p.sem[m] in _JUMP_SEMS:
                # the classic discard loop owns `a<` at base..base+1
                for x in range(base + 2, dx):
                    g.soft(x, s0, "<")
            else:
                turn = turn_x.get(m, base)
                for x in range(turn + 1, dx):
                    g.soft(x, s0, "<")
                g.put(turn, s0, "v")

        # ── the seek tail, entirely BELOW the slab band ───────────────────────
        # Nothing here shares a row with a slab, so the classic slabs keep their
        # west-hugging columns (and with them the ROM binding their discard `r`
        # depends on). Rows t .. t+7, columns 1 .. e_s+1.
        #
        #   t    : taken drops land, run east, `s` the request, turn south
        #   t+1  : westbound corridor back to the flush loop's column
        #   t+1..t+3, cols 2..6 : the flush loop
        #   t+4..t+7, cols 2..3 : the stock 2x4 counted discard for the remainder
        #
        # The flush `X` is entered heading **south**, which is what puts the
        # sentinel's exit on a downward path: heading south, A>0 turns west and
        # A==0 goes straight — both stay in the loop — while A<0 turns east and
        # leaves. Program words are non-negative (ARCH §4.2) and the drum's
        # sentinel is -1, so the sign is the whole test and ACC is never touched.
        t = taken_row
        e_s = struct_east + 2
        for col in taken_drops:
            g.put(col, t, ">")
        for x in range(min(taken_drops), e_s):
            g.soft(x, t, ".")
        emit(e_s, t, "s", "cmd")
        g.put(e_s + 1, t, "v")
        g.put(e_s + 1, t + 1, "<")
        for x in range(4, e_s + 1):
            g.soft(x, t + 1, ".")
        # flush loop: `r` then a sign `X`, both walked southbound
        g.put(3, t + 1, "v")
        emit(3, t + 2, "r", "rom")
        g.put(3, t + 3, "X")
        g.put(2, t + 3, "^")  # A>0: keep flushing
        g.put(2, t + 2, "^")
        g.put(2, t + 1, ">")
        g.put(3, t + 4, "<")  # A==0: keep flushing
        g.put(2, t + 4, "^")
        # A<0 — the sentinel: read the remainder, park it in BP, drop to the
        # counted discard. Every seek offset is even (rows pack even word
        # counts), which is exactly the 2x4 burst loop's invariant.
        emit(4, t + 3, "r", "rom")
        g.put(5, t + 3, "b")
        g.put(6, t + 3, "v")
        g.put(6, t + 4, ".")
        g.put(6, t + 5, "<")
        g.soft(5, t + 5, ".")
        g.soft(4, t + 5, ".")
        _discard_loop(g, 2, t + 5, pipe_glyphs)
        # the loop leaves westbound with BP == 0; rise column 1 to the collector
        g.put(1, t + 5, "^")
        for yy in range(collector + 1, t + 5):
            g.soft(1, yy, ".")
    else:
        for m in order:
            struct_drops |= _slab(
                g, m, p, slab_at[m], slab_base[m], collector, pipe_glyphs, drain_unit_bits
            )

        # Entry rows, drawn last: `soft` leaves every crossing drop's `.` in place
        # and only fills the genuinely free cells with `<`.
        for m in order:
            s0, dx = slab_at[m], drop_x[row_of[m]]
            base = slab_base[m]
            g.put(dx, s0, "<")
            if p.sem[m] in _JUMP_SEMS:
                # The compact discard loop owns `a<` at base..base+1 and is entered
                # directly from the westbound slab-entry corridor.
                for x in range(base + 2, dx):
                    g.soft(x, s0, "<")
            else:
                for x in range(base + 1, dx):
                    g.soft(x, s0, "<")
                g.put(base, s0, "v")

    # ── collector -> west riser -> back into the fetch cell ──────────────────
    # `soft` after the drops, so a slab-entry column that has to pass *through* the
    # collector keeps its `.` and is not turned west by a `<`.
    ret_x = max([*drop_x.values(), *struct_drops, lane_x0])
    if seek:
        ret_x = max(ret_x, struct_east + 3)  # the taken row's send site + its `v`
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
    through = {drop_x[row_of[m]] for m in order}
    clash = {r: c for r, c in drop_x.items() if c in through and by_row.get(r) not in order}
    if clash:
        raise MachineError(
            f"simple lane drop column(s) {sorted(set(clash.values()))} collide with a "
            f"slab entry column; a simple lane would drop past the collector"
        )

    width = max(ret_x, *asc_x.values()) + 1 if asc_x else ret_x + 1
    # ``bottom`` is one *past* the deepest slab's last glyph row, so on most layouts
    # this row is blank — it holds nothing but the two side walls, and `height =
    # bottom - 1` renders and binds on every registered machine.  It was worth a row
    # on `little-little-man` (195x197 -> 195x196, area2 38,809 -> 38,416, judged at
    # 817,968,537,932) while that machine was height-bound.
    #
    # It is not blank everywhere, and it is not worth taking now.  Measured against
    # this generator, with the whole-machine route compaction in: the row is free on
    # ten of the eleven machines and changes *no* footprint, because compaction
    # already put every one of them under its width — including the LLM, which is
    # 195x192 either way.  On `matmul` it is load-bearing: the memory-response pipe
    # routes through it, `mem_pad` 0..4 stop binding without it ("'r' at (22, 11)
    # must bind 'mem_resp'"), and the pad search escapes two columns east to
    # 87x85 — area2 7,396 -> 7,569, a 2.3% loss and the only footprint it moves.
    height = bottom
    mem_rows = sorted(
        r
        for r in all_rows
        if by_row.get(r) in flat and any(b == Band.MEM for _, b in flat[by_row[r]])
    )
    mem_out_row = mem_rows[len(mem_rows) // 2] if mem_rows else centre
    in_rows = [row_of[m] for m in used if p.sem[m] is Sem.INPUT]
    out_cols = [lane_x0 + 1]

    # ── name every region, so a profile is readable ───────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        "fetch": (1, centre, 4, 1),
        "trie": (5, y0, lane_x0 - 5, span),
        "return:riser": (1, centre + 1, 1, collector - centre),
        "return:collector": (2, collector, ret_x - 1, 1),
    }
    if asc_x:
        regions["return:topbus"] = (1, bus_row, max(asc_x.values()), 1)
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            continue
        end = asc_x.get(r, drop_x.get(r, lane_end[r]))
        regions[f"lane:{m}"] = (lane_x0, r, max(1, end - lane_x0 + 1), 1)
    for m in order:
        regions[f"slab:{m}"] = (slab_base[m], slab_at[m], slab_pitch, slab_rows[m])

    return _Cpu(
        cells=g.c,
        width=width,
        height=height,
        centre=centre,
        in_row=in_rows[0] if in_rows else 1,
        in_col=lane_x0,
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
    drain_unit_bits: int = 0,
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

    # BP==0 (or a not-taken arm) leaves a man westbound out of the discard loop's
    # `a`. He rises at ``base - 1`` — the first column west of the loop, still
    # clear of the shallower band, whose bodies stop at ``base' + 9`` (the `neg`
    # arm), i.e. ``base - 4`` at the default pitch 13 and ``base - 2`` at
    # :data:`_SLAB_PITCH_FLOOR`. Entry rows are stacked now, so running him west
    # to x=1 at this depth would walk him straight through every shallower slab's
    # loop; the riser instead crosses only shallower *entry rows*, as `.` holes
    # in their soft `<` runs. For slab 0 (``base == _STRUCT_X0``) this is the
    # x=1 shared riser, exactly as before.
    exit_x = base - 1

    if sem in _JUMP_SEMS:
        if drain_unit_bits:
            ey = _drain_block(g, base, s0, pipe_glyphs, drain_unit_bits)
        else:
            _discard_loop(g, base, s0, pipe_glyphs)
            ey = s0
        g.put(exit_x, ey, "^")
        for yy in range(collector + 1, ey):
            g.soft(exit_x, yy, ".")
        return {exit_x}

    g.soft(base, s0 + 1, ".")
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
    for c in range(base + 2, cols[taken]):
        g.soft(c, turn_row, ".")
    if drain_unit_bits:
        end_row = _drain_block(g, base, turn_row, pipe_glyphs, drain_unit_bits)
    else:
        _discard_loop(g, base, turn_row, pipe_glyphs)
        end_row = turn_row
    g.put(exit_x, end_row, "^")
    for y in range(collector + 1, end_row):
        g.soft(exit_x, y, ".")
    drops.add(exit_x)
    return drops


#: Per-program opt-in for :mod:`.drain`'s ladder+loop in place of the counted
#: discard loop below. Empty by default, so every machine that does not name
#: itself here is byte-identical.
#:
#: ``little-little-man`` discards a *mean 642,113 ROM words a case* — measured on
#: the ROM image, 30.8% of its ~8.33M ticks at the counted loop's 4 ticks a word.
#: That is now the largest line in its profile, and the counted loop cannot go
#: below 2 (``drain``'s module docstring). A ladder+loop at ``unit_bits`` costs
#: ``1 + 6/2**unit_bits`` ticks a word instead.
#:
#: The block is deeper and wider than the 2x4 it replaces, and that is free here
#: for a reason worth writing down: the machine's height is set by the **display**
#: (south edge 192), not by the slab band, whose deepest slab ends at 169 — and
#: its width by the **ROM** at 192, not by the slabs. Read the regions before
#: assuming a slab is on either critical path.
DRAIN_UNIT_BITS: dict[str, int] = {
    # Only worth switching on once the producer is no longer the binding stage:
    # with the buffered corridor below, `max(drain, ROM)` selects the drain again.
    # Measured on this machine, buffer alone was -8.2% of ticks and buffer+drain
    # -12.7%, so the drain is worth ~4.5 points of that and 6 rows of slab depth.
    "little-little-man": 2,
}

#: Per-program opt-in for **tight slab entry columns** — the walk *to* a jump or
#: branch, which is a different cost from the discard it performs there.
#:
#: The default rule floors every structured lane's drop at ``struct_east + 1``,
#: east of the whole slab band. So a structured instruction walks east from
#: ``lane_x0`` to that column, south to its entry row, then all the way back west
#: to its own ``base`` — and the man who leaves the slab climbs at ``base - 1``
#: and walks the collector west to the riser. Those three legs telescope: the
#: round trip is ``2 * drop_x - lane_x0 - 2`` cells and **the slab's own column
#: cancels out**. Only the drop column costs anything, and it costs double.
#:
#: :func:`_tight_struct_entry` shows the floor is far stronger than the staircase
#: needs. Landing each slab in its own band instead makes the walk depend on the
#: slab's index rather than on the band's total width, which is the whole point:
#: the default cost grows with the number of structured opcodes *for every one of
#: them*, so adding a third branch lengthens the first one's walk.
#:
#: Measured on ``little-little-man`` (16 opcodes, three slabs, ``lane_x0`` 9,
#: ``struct_east`` 41), cells walked per execution of that opcode:
#:
#: | slab | base | drop before | drop after | walk before | walk after |
#: |---|---|---|---|---|---|
#: | `JMPF` | 2 | 42 | 15 | 73 | **19** |
#: | `BRZ` | 15 | 43 | 16 | 75 | **21** |
#: | `BRN` | 28 | 44 | 29 | 77 | **47** |
#:
#: The residue on `BRN` is its ``base``: the third slab starts 26 columns east, and
#: its drop cannot precede it. That is what :data:`SLAB_PITCH` is for.
#:
#: The simple lanes gain twice over. Their own drops stop bumping east past three
#: reserved slab columns, and the uniqueness rule east of ``struct_east`` — which
#: only ever existed to keep them off those columns — goes with it, so they share
#: their floor freely again as they always have west of the band.
#:
#: Empty by default, so every machine not named here is byte-identical. Requires
#: the short-return drop rule and is not available under the seek drum, whose
#: slabs are a different shape.
TIGHT_STRUCT_DROPS: set[str] = {"little-little-man"}

#: Per-program **slab pitch**, the staircase's step. The default 13 gives a branch
#: slab two spare columns it never uses: its glyphs run from the exit riser at
#: ``base - 1`` to the ``neg`` arm at ``base + 9``, eleven columns
#: (:data:`_SLAB_PITCH_FLOOR`). Narrowing the step walks every slab after the first
#: west, which under :data:`TIGHT_STRUCT_DROPS` walks its entry column west with it
#: — two cells off the round trip per column, per execution of that opcode.
#:
#: It also moves the *deepest* slab's discard ``r`` west, and that turns out to be
#: what the CPU's east wall is really pinned to. §7.1 makes that ``r`` bind the ROM
#: pipe on the west wall, and its rival is the memory-response pipe touching the
#: **east** wall — so a narrower CPU is a *closer* rival, and the pad search has to
#: escape east until the tie breaks. Measured on ``little-little-man``, sweeping
#: ``mem_pad`` for each pitch with tight entries on (fast engine, 14 public cases,
#: 192x193 throughout — the ROM sets the box, so none of this moves the footprint):
#:
#: | pitch | binds from | avgTicks |
#: |---|---|---|
#: | 13 (default) | 30 | 4,975,064 |
#: | 12 | 26 | 4,962,708 |
#: | **11** | **22** | **4,952,101** |
#:
#: Every column of pitch buys back a column of ``mem_pad`` and then some. Empty by
#: default, so every machine not named here is byte-identical.
#:
#: **Declined for ``deadman-3d_hires``, twice, and the reason is structural.** A
#: narrower pitch does move that grid (pitch 11 and 12 both build, both differ
#: byte-for-byte from the default) — it just cannot move the *box*: hires is
#: 496 wide on the router wall's 495 columns, not on its CPU, at both the old
#: fold (496x401 either way) and the new one (496x353 either way). The knob's
#: whole payoff is trading CPU columns for ``mem_pad``, and on a machine whose
#: width floor is set 200 columns further east there is nothing to trade into.
#: Left off rather than added dead; revisit if the wall ever narrows past the
#: CPU.
SLAB_PITCH: dict[str, int] = {"little-little-man": 11}

#: :data:`SLAB_PITCH`'s replacement while the seek drum is on — the same split
#: :data:`MEM_PAD` / :data:`SEEK_MEM_PAD` already make, and for the same reason: the
#: drum reshapes the band, so a pitch swept against one form does not carry to the
#: other.
#:
#: Why ``deadman-3d`` is here and not above. Narrowing the pitch pulls the CPU's east
#: wall west, and §7.1 binds the deepest slab's discard ``r`` against the
#: memory-response pipe touching that wall — so a *narrower* CPU is a **closer** rival.
#: A seek build clears the tie because :data:`SEEK_MEM_PAD` (22) already sits east of
#: it; the classic build does not, and ``deadman-3d`` at pitch 11 cannot bind at
#: ``MEM_PAD`` 17 at all. Forcing it needs 30 — thirteen columns spent to buy back two
#: per slab — and takes the classic machine 372x377 -> 378x377, which is the baseline in
#: :data:`SEEK_DRUM`'s own "classic -> seek" table. Keeping the two registries apart
#: gives the shipped build its 295x269 -> 289x269 and leaves that reference number
#: reproducible.
#:
#: Note ``little-little-man`` must stay in :data:`SLAB_PITCH`: it has no drum, so a
#: seek-only lookup would silently hand it back the default 13 and undo the -1.44% the
#: registry exists for.
SEEK_SLAB_PITCH: dict[str, int] = {"deadman-3d": 11}


def _drain_block(
    g: _Grid,
    base: int,
    y: int,
    pipe_glyphs: list[tuple[int, int, str, str]],
    unit_bits: int,
) -> int:
    """Place a :mod:`.drain` ladder+loop in a slab. Returns the row it leaves on.

    Same contract as :func:`_discard_loop`: the man arrives heading **west** along
    ``y`` and leaves heading **west** with ``BP == 0``, on the caller's riser
    column ``base - 1``. He just leaves further down, because the block hangs
    below the entry row instead of beside it.
    """
    from .drain import build_drain

    block = build_drain(0, unit_bits=unit_bits, even=True)
    ox, oy = base - 1, y + 1  # local column 0 is the reserved exit column

    # Turn the westbound man south into the block. On a branch's ``turn_row`` the
    # arm's westward run is already drawn, and on a jump's entry row it is drawn
    # afterwards — so this cell is `.` or `<` or empty, all three meaning "the man
    # is walking west here", which is precisely who we want to divert. Anything
    # else is a real collision and must not be papered over.
    turn = (ox + block.spine, y)
    if g.c.get(turn) not in (None, ".", "<"):
        raise MachineError(f"drain entry at {turn} would overwrite {g.c[turn]!r}")
    g.c[turn] = "v"
    for (bx, by), ch in block.cells.items():
        g.put(ox + bx, oy + by, ch)
        if ch == "r":
            pipe_glyphs.append((ox + bx, oy + by, "r", "rom"))
    assert ox + block.exit[0] == base - 1, "the block must leave on the riser column"
    return oy + block.exit[1]


def _discard_loop(
    g: _Grid,
    x: int,
    y: int,
    pipe_glyphs: list[tuple[int, int, str, str]],
) -> None:
    """Discard two adjacent ROM words per lap.

    The generated ROM image is fixed-width: every instruction is exactly two
    words, and :func:`rom_words` scales every jump target by two.  BP is therefore
    even on every entry to this block.  Spending that invariant gives a compact
    2x4 burst loop instead of testing around every single read::

        a<
        rm
        rm
        >^

    Enter the top-right ``<`` heading west.  At the top-left ``a``, BP > 0 turns
    counter-clockwise/south through two consecutive ``r`` cells; the right edge
    decrements twice on the way back.  BP == 0 continues west.  Thus a zero skip
    still performs no reads, while every non-zero skip costs four walked cells
    per discarded word instead of six.

    Both ``r`` cells stay at the slab's west edge so they bind to the ROM pipe,
    not an input, STORE, or coprocessor response pipe (§7.1).
    """
    g.put(x, y, "a")
    g.put(x + 1, y, "<")
    for yy in (y + 1, y + 2):
        g.put(x, yy, "r")
        pipe_glyphs.append((x, yy, "r", "rom"))
        g.put(x + 1, yy, "m")
    g.put(x, y + 3, ">")
    g.put(x + 1, y + 3, "^")


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


# ── the tape, as a STORE block ───────────────────────────────────────────────
#: Every memory tier ``build`` will accept, in the order they are worth trying.
#: ``tape`` is the default and stays it — see ARCH.md §4.1 for why ``grid`` loses on
#: footprint despite an access cost that ignores ``n``.
STORE_TIERS = ("tape", "grid", "men", "men-y", "men-v3", "taped")

#: Blank columns between the CPU's east wall and the adapter room, and between the
#: adapter and the STORE block. Both are paid **twice**: once in the machine's width,
#: which is squared in the score, and once in the memory *response* pipe, whose whole
#: length is charged on every read because a read is strictly serial (§7.4b) — and a
#: read is where 45% of `gradebook`'s CPU time goes. So these are not cosmetic spacing;
#: they are two of the cheapest numbers in the generator to be wrong about.
#:
#: ``CPU_ADAPTER_GAP`` is a hard floor: every program fails to place its pipes at 3 or
#: less, so 4 is the real minimum and not a guess.
#:
#: ``ADAPTER_TAPE_GAP`` was 6 and wanted to be **1**, which is worth ~9-10% of
#: footprint on every width-bound machine, plus five cells off every read:
#:
#: | | 6 | 1 |
#: |---|---|---|
#: | `brackets` | 95x70, 9,025 | **90x70, 8,100** |
#: | `gradebook` | 113x101, 12,769 | **108x101, 11,664** |
#: | `palette` | 95x89, 9,025 | **90x89, 8,100** |
#: | `sudoku-validity` | 83x80, 6,889 | **80x80, 6,400** |
#: | `tcp` | 109x74, 11,881 | **104x74, 10,816** |
#:
#: Height-bound machines (`matmul`, `snake`, `snake-ring`, `plotter`) keep their
#: footprint and still gain the ticks. `palette` was in that list on the strength of
#: being a display problem like `plotter`; it is not, it is 95 wide against 89 tall and
#: the five columns come straight off its score.
CPU_ADAPTER_GAP = 4
ADAPTER_TAPE_GAP = 1

#: Whole-machine placement floors. These are geometric minima, not visual
#: padding:
#:
#: * with an input room, columns 3..5 hold that room, 6..7 are the mandatory
#:   two-cell pipe, and the CPU west wall can therefore start at x=8;
#: * without input, the ROM corridor itself needs two cells (x=1..2), so the CPU
#:   can start at x=3;
#: * STREAM layouts retain their own west clearance. FastLittleman accepts the
#:   x=3 placement, but the reference engine leaves the coprocessor machine
#:   permanently blocked there, so this is a separately validated floor;
#: * with an input room, the ordinary ROM and CPU walls may be adjacent. The
#:   input-free x=3 layout needs one blank row for reference-engine compatibility;
#:   a buffered boustrophedon needs still more clearance.
CPU_X_WITH_INPUT = 8
CPU_X_WITHOUT_INPUT = 3
CPU_X_WITH_STREAM = 8
ROM_CPU_GAP = 1
ROM_CPU_GAP_WITHOUT_INPUT = 2
ROM_BUFFER_CPU_GAP = 3

#: The memory response runs above the adapter/tape attachment, not above the
#: whole CPU. One row above the highest attachment is the geometric minimum;
#: zero collides the horizontal and vertical legs on real depth-4 layouts.
MEM_RESPONSE_CLEARANCE = 1

#: Slack, in cells, added around the endpoints' bounding rectangle when ``compact``
#: routes a STORE connection. Zero is right only while STORE is east of everything
#: it talks to; a block moved west or south has to leave the rectangle to turn
#: around, because both ends' headings are fixed by the rooms they attach to.
_ROUTE_MARGIN = 2


def _keepout(g: _Grid, spare: Iterable[tuple[int, int]] = ()) -> frozenset[tuple[int, int]]:
    """Occupied cells, plus a one-cell halo around every pipe already drawn.

    Two pipes in adjacent cells are legal glyphs and an illegal *machine*: the engine
    reads the pair as one pipe, and the failure is silent — a ``send`` lands in the
    wrong FIFO. The router is a shortest-path search over free cells and has no
    concept of "free but too close", so the separation has to arrive as blocked
    cells. Room walls get no halo: every route in this generator has to finish
    against one.
    """
    out = set(g.c)
    for x, y in g.drawn:
        out |= {(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)}
    return frozenset(out - set(spare))


#: Programs that need a wider adapter-to-STORE gap than the default.
#:
#: ``matmul`` is the only one, and it does not merely fail to *place* at 1 — it places,
#: loads, and then **hangs**, every case at the tick cap. The STREAM block's rings sit
#: in that corridor, so a gap the request pipe fits through is not necessarily one the
#: rings survive, and the binding checks cannot see the difference. It is verified
#: working at 3, 4, 5 and 6 (and 3 is even slightly faster), but `matmul` is
#: height-bound at 90 rows so *no* gap changes its footprint and the tick win is ~1%.
#: Not worth re-validating a STREAM machine for, so it stays on the exact geometry that
#: scored 1,446,608,970.
ADAPTER_TAPE_GAP_FOR: dict[str, int] = {"matmul": 6}

#: The floor the *store tier* imposes on that gap, which is a separate thing from the
#: per-program override above: it is the block east of the corridor, not the CPU west of
#: it, that fails to bind. Only ``tape`` — the shipped tier — reaches 1. Measured, by
#: building `snake-ring` on each tier at every gap from 1 to 7:
#:
#: | tier | binds from | note |
#: |---|---|---|
#: | `tape` | **1** | the default; footprint flat across all seven |
#: | `men-y` | 3 | flat too, so the floor costs it nothing |
#: | `men` | 5 | and it *grows* per column: 21,025 at 5, 21,609 at 7 |
#: | `grid` | 6 | |
#:
#: All three non-``tape`` tiers are measured negatives (ARCH.md §4.1) whose numbers are
#: quoted in tests as comparisons, so they are pinned at the 6 they were measured on
#: rather than dropped to their true floors — re-measuring a losing tier buys nothing,
#: and moving `men` off 6 would silently restate a recorded result.
ADAPTER_TAPE_GAP_BY_STORE: dict[str, int] = {"grid": 6, "men": 6, "men-y": 6}


def adapter_tape_gap(program_name: str, store: str) -> int:
    """Blank columns between the adapter and the STORE block, for one build.

    The wider of the two constraints wins: a program's own override (``matmul``'s
    STREAM rings) and the floor its store tier needs to bind at all. ``men-v3``
    keeps the request route's jog column (``ax_out + 2``) clear of the block's
    own column 0, where its inlet stub lives.
    """
    gap = ADAPTER_TAPE_GAP_FOR.get(program_name, ADAPTER_TAPE_GAP)
    if store in ("men-v3", "taped"):
        gap = max(gap, 6)
    return max(gap, ADAPTER_TAPE_GAP_BY_STORE.get(store, 0))


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


def _resolve_tape_skip_batch(
    n: int,
    skip_batch: int | None,
    jump_threshold: int,
) -> int:
    """Resolve explicit batch 1/2/4 or auto-select by STORE size."""
    if jump_threshold < 1:
        raise ValueError(f"jump_threshold must be positive, got {jump_threshold}")
    if skip_batch is None:
        return 2 if n >= jump_threshold else 1
    if skip_batch not in (1, 2, 4):
        raise ValueError(f"skip_batch must be 1, 2, 4, or None, got {skip_batch}")
    return skip_batch


def _tape_worker_spec(skip_batch: int):
    """Return the worker and wall anchors for one tape skip implementation."""
    from ..memory_tape import (
        V2_FWD_ROW,
        V2_IH,
        V2_IN_ROW,
        V2_IW,
        V2_JUMP4_FWD_ROW,
        V2_JUMP4_IH,
        V2_JUMP4_IW,
        V2_JUMP4_RET_COL,
        V2_JUMP_FWD_ROW,
        V2_JUMP_IH,
        V2_JUMP_IW,
        V2_JUMP_RET_COL,
        V2_OUT_COL,
        V2_RET_COL,
        worker_v2,
        worker_v2_jump,
        worker_v2_jump4,
    )

    if skip_batch == 1:
        return (
            worker_v2,
            V2_IW,
            V2_IH,
            V2_IN_ROW,
            V2_OUT_COL,
            V2_FWD_ROW,
            V2_RET_COL,
        )
    if skip_batch == 2:
        return (
            worker_v2_jump,
            V2_JUMP_IW,
            V2_JUMP_IH,
            V2_IN_ROW,
            V2_OUT_COL,
            V2_JUMP_FWD_ROW,
            V2_JUMP_RET_COL,
        )
    if skip_batch == 4:
        return (
            worker_v2_jump4,
            V2_JUMP4_IW,
            V2_JUMP4_IH,
            V2_IN_ROW,
            V2_OUT_COL,
            V2_JUMP4_FWD_ROW,
            V2_JUMP4_RET_COL,
        )
    raise ValueError(f"skip_batch must be 1, 2, or 4, got {skip_batch}")


def _resolve_tape_relay(
    skip_batch: int,
    relay_size: tuple[int, int] | None,
) -> tuple[list[str], tuple[int, int] | None]:
    """Return relay art and its reported interior dimensions.

    Batch 1 retains the byte-identical legacy relay unless explicitly tuned.
    Batch 2 gets the two-word relay with the same 4x3 interior/exterior size;
    batch 4 defaults to the engine-pinned 8x6 relay.
    """
    from ..dataflow_relay import relay
    from ..memory_tape import RELAY

    if relay_size is None:
        if skip_batch == 1:
            return RELAY, None
        relay_size = (4, 3) if skip_batch == 2 else (8, 6)
    w, h = relay_size
    return relay(w, h), (w, h)


def _tape_shell(
    n: int,
    *,
    skip_batch: int = 1,
) -> tuple[Circuit, tuple[int, int], tuple[int, int]]:
    """The worker room and the two CPU-facing pipe stubs — the part no ring changes.

    Shared by both ring layouts so neither can drift from the other. What fixes every
    ``r``/``s`` binding *inside* the worker is the worker's four wall anchors, not the
    shape of the ring: a ring may be routed any way at all so long as it uses the
    selected worker's forward-row and return-column anchors. That is the licence the
    serpentine uses.

    Returns the canvas plus the request and response stub cells.
    """
    from ..circuit import Circuit

    (
        worker,
        worker_width,
        worker_height,
        input_row,
        output_col,
        _forward_row,
        _return_col,
    ) = _tape_worker_spec(skip_batch)

    g = Circuit(400, 200)
    wk = worker(n)
    WX, WY = _TAPE_WX, _TAPE_WY
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(-1, worker_width + 1):
        g.set(WX + x, WY - 1, "+" if x in (-1, worker_width) else "-")
        g.set(
            WX + x,
            WY + worker_height,
            "+" if x in (-1, worker_width) else "-",
        )
    for y in range(worker_height):
        g.set(WX - 1, WY + y, "|")
        g.set(WX + worker_width, WY + y, "|")

    # request stub: two cells pointing east into the worker's left wall
    iy = WY + input_row
    g.set(WX - 3, iy, ">")
    g.set(WX - 2, iy, ">")
    # response stub: two cells climbing north out of the worker's top wall
    ox = WX + output_col
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


def grid_block(n: int) -> _Tape:
    """``memory_men_addr``'s address-carrying man-memory, wired as STORE.

    One little man per slot, all ``n`` of them holding their own address in B, so
    the router *broadcasts* rather than walks: **an access costs ~31 ticks and the
    cost does not depend on ``n``**, against the rotating tape's ~316 + 8.06·n. On
    a machine whose reads dominate — and §4.1 measured a tape read at 523 ticks
    versus 19 for a write — that is the difference between a memory that sets the
    tick count and one that does not.

    What it costs instead is *shape*. The block is 36 columns wide whatever ``n``
    is, and ``~3n`` rows tall, where the tape is 32x32 flat. So this is a good
    trade exactly when the machine's bounding box is already set by its **height**
    and has slack in width — the block then hides underneath a dimension already
    being paid for, and the swap is free on footprint. It is a bad trade on a
    short, wide machine, where every one of those rows is a new longest side.

    There is also a one-off ``~5n`` ticks of **ignition** while the spawner walks
    south handing each band its address. It is charged once per case, not per
    access, so it disappears against any program doing real work.
    """
    from ..memory_men_addr import build_addr

    a = build_addr(n, io=False)
    assert a.in_cell is not None and a.out_cell is not None, "io=False must report stubs"
    cells = {(x, y): ch for y, row in enumerate(a.rows) for x, ch in enumerate(row) if ch != " "}
    return _Tape(
        cells=cells,
        width=a.width,
        height=a.height,
        in_cell=a.in_cell,
        out_cell=a.out_cell,
    )


def tape_block(
    n: int,
    *,
    skip_batch: int | None = 1,
    jump_threshold: int = 128,
    relay_size: tuple[int, int] | None = None,
) -> _Tape:
    """``memory_tape``'s verified rotating-pipe tape, wired for use as STORE.

    ``memory_tape.assemble_v2`` builds the tape as a standalone answer to the
    ``memory`` problem, so it comes with its own ``I`` and ``O`` rooms. A program
    may have at most one of each and the CPU owns them, so those two rooms are
    replaced here by pipe stubs the caller extends.  ``skip_batch=1`` is the
    measured legacy worker and remains byte-identical at every size.
    ``skip_batch=2`` selects :func:`memory_tape.worker_v2_jump`: its two-sided
    counted rings test BP once per word but carry two words per lap.  It is wider
    (45 rather than 33 block columns) and therefore deliberately opt-in.
    ``skip_batch=None`` selects it automatically when ``n >= jump_threshold``.

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
    stack, which is taller. A nearly square machine with a STREAM block under the
    CPU is the exception: extra tape rows can push the pad search onto a wider
    ``mem_pad``. Exact whole-machine dimensions belong in generated reports, not
    this low-level block description, because independent routing improvements
    otherwise make the documentation stale. Size to the real high address, as
    always — this is not a knob to spend.

    At ``skip_batch=1`` the cost is **8.00 ticks per slot per access**, dead linear
    and with no step at the 107/108 seam.  The two-sided worker reduces the dominant
    loop to about five ticks per skipped word; its odd exit re-enters with BP=0, so
    it never consumes a speculative extra word.  Excess ring capacity only delays
    the first value of a lap; in both modes the worker remains the bottleneck.
    """
    from ..memory_tape import _draw_pipe

    skip_batch = _resolve_tape_skip_batch(n, skip_batch, jump_threshold)
    relay_art, _resolved_relay_size = _resolve_tape_relay(skip_batch, relay_size)

    (
        _worker,
        worker_width,
        worker_height,
        _input_row,
        _output_col,
        forward_row,
        return_col,
    ) = _tape_worker_spec(skip_batch)

    WX, WY = _TAPE_WX, _TAPE_WY
    for fold in (0, 2, 4, 6, 8, 10, 12):
        g, in_cell, out_cell = _tape_shell(n, skip_batch=skip_batch)

        bottom_y = WY + worker_height
        fy = WY + forward_row
        ret_col = WX + return_col
        east = WX + worker_width + 2
        b_fwd = bottom_y + 6
        r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
        relay_y = bottom_y + 3
        relay_h = len(relay_art) - 2
        for i, row in enumerate(relay_art):
            for j, ch in enumerate(row):
                g.set(1 + j, relay_y + i, ch)
        relay_wall = len(relay_art[0])
        adj = relay_wall + 1
        b_fwd = relay_y + relay_h

        n_fwd = _draw_pipe(
            g,
            [
                (WX + worker_width + 1, fy),
                (east, fy),
                (east, b_fwd),
                (adj, b_fwd),
            ],
        )
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
    return _serpentine_tape(n, skip_batch=skip_batch, relay_art=relay_art)


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


def _serpentine_tape(
    n: int,
    *,
    skip_batch: int = 1,
    relay_art: list[str] | None = None,
) -> _Tape:
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
    from ..memory_tape import RELAY, _draw_pipe

    relay_art = relay_art or RELAY
    relay_h = len(relay_art) - 2

    (
        _worker,
        worker_width,
        worker_height,
        _input_row,
        _output_col,
        forward_row,
        return_col_offset,
    ) = _tape_worker_spec(skip_batch)

    WX, WY = _TAPE_WX, _TAPE_WY
    bottom_y = WY + worker_height
    fy = WY + forward_row
    ret_col = WX + return_col_offset
    east = WX + worker_width + 2
    top = bottom_y + _SNAKE_TOP

    # Seventeen rows carry the ~420 slots a little-little-man interpreter wants; the
    # last tier here holds 1976 values, at which point the block is 112 rows tall.
    for rows in range(5, 82, 2):
        g, in_cell, out_cell = _tape_shell(n, skip_batch=skip_batch)
        last = top + rows - 1  # the final, relay-bound westbound leg
        relay_y = last - relay_h  # so `last` is the relay's bottom interior row
        for i, row in enumerate(relay_art):
            for j, ch in enumerate(row):
                g.set(1 + j, relay_y + i, ch)
        adj = len(relay_art[0]) + 1  # first column east of the relay's wall
        snake_climb = max(_SNAKE_CLIMB, adj + 1)
        snake_west = max(_SNAKE_WEST, snake_climb + 2)

        snake: list[tuple[int, int]] = [
            (WX + worker_width + 1, fy),
            (east, fy),
            (east, top),
        ]
        for i in range(rows):
            y = top + i
            if i == rows - 1:
                snake.append((adj, y))  # into the relay
            elif i % 2 == 0:
                snake += [(snake_west, y), (snake_west, y + 1)]
            else:
                snake += [(east, y), (east, y + 1)]
        n_fwd = _draw_pipe(g, snake)
        n_ret = _draw_pipe(
            g,
            [
                (adj, relay_y + 1),
                (snake_climb, relay_y + 1),
                (snake_climb, bottom_y + 2),
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
    tape_skip_batch: int = 1
    tape_relay_size: tuple[int, int] | None = None
    stream_pad: int = 0
    display: tuple[int, int] | None = None
    #: Where each display band's ``s`` glyph ended up, in *grid* coordinates — the
    #: cells ``lm.mjs route`` has to answer with that band's own pipe.
    dsp_glyphs: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: The placed STREAM block, when the program uses one (see ``stream.py``).
    stream: object | None = None
    #: Cells in the ROM -> CPU corridor, which by ``SPEC.md`` is also its capacity in
    #: words. The straight corridor is incidental (~30); anything more is ROM-PLUS
    #: buffering bought on purpose (see :data:`ROM_BUFFER`).
    rom_capacity: int = 0
    #: Pipe lengths for the serial routes whose latency is paid on every
    #: instruction or STORE access.
    route_lengths: dict[str, int] = field(default_factory=dict)
    #: The placed hot bank, when ``build(hot=...)`` asked for one (see _two_tier).
    tier: object | None = None
    #: Opt-in constraint placement metadata. The first compactable block is STORE;
    #: fixed blocks remain at their normal coordinates.
    compact: bool = False
    store_offset: tuple[int, int] = (0, 0)
    #: How far the whole memory subsystem (adapter + STORE) was moved off its
    #: default east-of-the-CPU anchor, in grid cells.
    mem_offset: tuple[int, int] = (0, 0)
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
            "rom:corridor": (
                f"ROM-PLUS: the fetch pipe snaked to {self.rom_capacity} cells, i.e. "
                f"{self.rom_capacity} words of buffer (a pipe's capacity is its length)"
            ),
            "adapter": "expands one sign-biased request word into the tape's `op addr [value]`",
            "tape": (
                f"rotating pipe tape, N={self.tape_n}, "
                f"skip_batch={self.tape_skip_batch}, relay={self.tape_relay_size}"
            ),
            "stream": "STREAM block: rotate-only rings, an adding relay, a fused MAC (~9.5 ticks)",
            "display": "LM-75: top=ADDR, left=DATA, bottom=SWAP",
            "cpu:fetch": ">rbr — opcode into BP, then the operand into A (fixed-width 2 words)",
            "cpu:trie": f"depth-{self.plan.k} backpack trie; leaves are bit-reversed",
            "cpu:return:collector": "every lane funnels west along here",
            "cpu:return:riser": "up to the fetch row — paid once per instruction",
        }
        if self.program.name == "deadman-3d":
            # The demo's input protocol rides the sidecar, so the grid itself
            # documents how to drive it (the deliverable's "input instructions").
            from ..deadman3d import INPUT_PROTOCOL

            notes["io:I"] = INPUT_PROTOCOL
            notes["stream:panel"] = "the DOOM unit's 64x48 LM-75: top=ADDR, left=DATA, bottom=SWAP"
            notes["stream:unit"] = (
                "the DOOM column painter (lm1/d3_unit.py): one command word per "
                "viewport column / RLE run / cursor move / gun sprite / COMMIT, "
                "8*arg + code. The map, the title screen, the status bar and "
                "the baked pistol sprites are all derived from the Freedoom "
                "project (https://github.com/freedoom/freedoom @ d14dbbe: "
                "levels/e1m1.wad + its textures, graphics/titlepic, "
                "graphics/stbar, sprites/pisga0+pisfa0; "
                "BSD-style licence) — see deadman3d.py's art credits."
            )
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
        placement = f", compact store_dy={self.store_offset[1]}" if self.compact else ""
        routes = (
            ", routes "
            + " ".join(f"{name}={length}" for name, length in sorted(self.route_lengths.items()))
            if self.route_lengths
            else ""
        )
        return (
            f"{self.program.name}: {self.width}x{self.height} "
            f"footprint {self.footprint}, {len(self.plan.number)} opcodes "
            f"(depth {self.plan.k}), P={self.program.P} words on {self.rom_rows} ROM rows, "
            f"tape N={self.tape_n}, skip_batch={self.tape_skip_batch}, "
            f"relay={self.tape_relay_size}, "
            f"mem_pad={self.mem_pad}{stream}{panel}{routes}"
            f"{placement}"
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
    tape_skip_batch: int | None = 1,
    tape_relay_size: tuple[int, int] | None = None,
    tape_jump_threshold: int = 128,
    middle_order: Sequence[str] | None = None,
    opcode_slots: Mapping[str, int] | None = None,
    rom_buffer: int | None = None,
    compact: bool = False,
    hot: tuple[int, int] | None = None,
    mem_offset: tuple[int, int] = (0, 0),
    store_offset: tuple[int, int] = (0, 0),
    in_north: bool = False,
    store_teleport: bool = False,
    store_answer_west: bool = False,
    store_request_teleport: bool = False,
    store_chain_reach: bool = False,
    store_chain_pad: int = 0,
    store_feed_teleport: bool = False,
    store_request_reach: bool = False,
    store_compact_gate: bool = False,
    store_bank_order: tuple[int, ...] | None = None,
    trim_dead: bool = False,
    top_bus: bool = False,
    store_shape: tuple[int, int] | None = None,
    seek: bool = False,
    seek_threshold: int = SEEK_THRESHOLD,
    seek_ops: Sequence[str] = SEEK_OPS,
    seek_teleport: bool = False,
    seek_taken_drop_east: bool = False,
    in_west: int = 0,
    doom_loop_row: int | None = None,
    doom_leaf_cols: tuple[int, ...] | None = None,
) -> Machine:
    """Assemble the whole machine for ``program``.

    ``store`` picks the memory tier. ``"grid"`` is the address-carrying man-memory
    (:func:`grid_block`) and is the one to reach for first: its access cost is ~31
    ticks *independent of ``n``*, so it wins on ticks at every size, and it pays for
    that in rows rather than ticks — free on footprint whenever the machine's
    bounding box is already set by height. ``"tape"`` is the rotating ring (§4.1,
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

    ``tape_skip_batch`` selects the tape worker: ``1`` is the compact legacy loop,
    ``2`` the wider two-word counted ring, and ``4`` the exact power-of-two worker
    with a configurable fat relay. Pass ``None`` to choose batch 2 when
    ``tape_n >= tape_jump_threshold`` and batch 1 below it.

    **Batch 2 buys ~12% of ticks and costs 12 columns, so it pays only on a machine
    wide enough not to notice them.** Swept across every program with a tape, sb1 vs
    sb2 footprint:

    | free (+0.0%) | costly |
    |---|---|
    | `pathfinder`, `snake`, `snake-ring`, `pathfinder-unit` | 7 width-bound targets |

    The costly ones are width-bound, and **re-sweeping the ROM fold does not rescue
    them** — it makes them worse, because a narrower fold is a taller machine: at its
    own best fold `gradebook` is 117x84 = 13,689 (+58.3% against 8,649) and
    `sudoku-validity` 89x74 = 7,921 (+33.6%). That is the opposite of what the fold
    re-sweep did for `little-little-man`, where the ROM already set the width at 192
    and the tape sat well inside it.

    So among *shipped* solutions this is a `little-little-man`-only win. The two
    problems above it in score do not have a tape at all: `subset-sum` ships
    `subset_sum_mitm` and `pathfinder` ships `pathfinder_grid`, both bespoke ring
    machines. Measured, so it need not be tried again.

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

    ``compact`` is deliberately opt-in while constraint placement is young. It
    anchors every ordinary block, declares STORE vertically movable inside the
    baseline bounding box, reroutes only STORE's request/response connections, and
    minimizes footprint followed by total serial-pipe length.
    """
    # Checked here rather than in ``_assemble``, which the pad search calls up to 40
    # times while *catching* MachineError — so a typo'd tier came back as whichever
    # unrelated collision the last pad happened to hit.
    if store not in STORE_TIERS:
        raise MachineError(
            f"unknown store tier {store!r}; expected one of {', '.join(map(repr, STORE_TIERS))}"
        )
    if tape_skip_batch not in (None, 1, 2, 4):
        raise MachineError(f"tape_skip_batch must be 1, 2, 4, or None, got {tape_skip_batch}")
    if tape_jump_threshold < 1:
        raise MachineError(f"tape_jump_threshold must be positive, got {tape_jump_threshold}")
    if store not in ("tape", "taped") and tape_skip_batch != 1:
        raise MachineError("tape_skip_batch applies only to the tape tiers")
    if store != "tape" and tape_relay_size is not None:
        raise MachineError("tape_relay_size applies only to store='tape'")
    if short_return is None:
        short_return = program.name not in _LONG_RETURN
    if seek:
        # Build-time only: long jumps become JMPS/BRZS/BRNS, which plan() then
        # gives lanes of their own. A registered LANE_ORDER is a permutation of
        # the *unpinned* lanes, so each new mnemonic is spliced in beside the
        # classic opcode it was split from rather than invalidating the order.
        program = seek_split(program, threshold=seek_threshold, ops=seek_ops)
        if middle_order is not None:
            # A registered LANE_ORDER is a permutation of the unpinned lanes, so
            # the new mnemonics have to be spliced in. **Above every classic
            # structured lane**, deliberately: a lane's slab index follows its
            # drop column, which grows upward through the band, so putting the
            # seek lanes higher leaves the classic slabs hugging the west wall —
            # and a classic slab's discard `r` must stay nearer the ROM pipe
            # there than the STORE's response pipe on the east (§7.1). A seek
            # slab has no `r` at all, so it is free to sit east.
            used = {op.mnemonic for op in program.ops_used}
            classic = ("JMPF", "BRZ", "BRN")
            order = list(middle_order)
            at = min(
                (order.index(c) for c in classic if c in order),
                default=len(order),
            )
            for new in ("JMPS", "BRZS", "BRNS"):
                if new in used and new not in order:
                    order.insert(at, new)
                    at += 1
            middle_order = order
    if opcode_slots is not None and not trim_dead:
        # Untrimmed, a lane sits at ``2 * slot + 1``, so relabelling the leaves
        # *moves* it — the whole point of the knob is that it does not.
        raise MachineError("opcode_slots is row-neutral only with trim_dead")
    p = plan(program, middle_order=middle_order, slots=opcode_slots)
    seek_layout = None
    if seek:
        # The seek drum resolves jump operands as row*K+offset in its own packed
        # layout, so the words and the drum are built together (fixed point).
        # Its side margins and fixed-width jump literals make each row wider
        # than the plain packed drum's, so the fold deepens until the drum fits
        # the same width budget the registry's row count bought.
        plain = rom_words(program, p)
        seek_fold = rom_rows if rom_rows is not None else max(2, _packed_fold(plain, 60))
        budget = rommod.build_packed_rom(plain, rows=seek_fold).width + 4
        for extra in range(0, 24):
            words, seek_layout = seek_words(program, p, rows=seek_fold + extra)
            if seek_layout.width <= budget:
                break
    else:
        words = rom_words(program, p)
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1
    effective_skip_batch = _resolve_tape_skip_batch(
        tape_n,
        tape_skip_batch,
        tape_jump_threshold,
    )
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
                    rom_buffer,
                    False,
                    store_offset[1],
                    store_offset[0],
                    mem_offset[0],
                    mem_offset[1],
                    hot,
                    tape_skip_batch=effective_skip_batch,
                    tape_relay_size=tape_relay_size,
                    in_north=in_north,
                    store_teleport=store_teleport,
                    store_answer_west=store_answer_west,
                    store_request_teleport=store_request_teleport,
                    store_chain_reach=store_chain_reach,
                    store_chain_pad=store_chain_pad,
                    store_feed_teleport=store_feed_teleport,
                    store_request_reach=store_request_reach,
                    store_compact_gate=store_compact_gate,
                    store_bank_order=store_bank_order,
                    trim_dead=trim_dead,
                    top_bus=top_bus,
                    store_shape=store_shape,
                    seek_layout=seek_layout,
                    seek_teleport=seek_teleport,
                    seek_taken_drop_east=seek_taken_drop_east,
                    in_west=in_west,
                    doom_loop_row=doom_loop_row,
                    doom_leaf_cols=doom_leaf_cols,
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
    if compact:
        # Keep every unlisted block fixed. STORE may move vertically anywhere that
        # stays inside the baseline bounding box; both connections touching it are
        # then rerouted from constraints. This is intentionally the first, small
        # placement search rather than permission to rewrite the whole machine.
        _tx, tape_y, _tw, tape_h = best.regions["tape"]
        min_dy = -tape_y
        max_dy = best.height - (tape_y + tape_h)
        compact_best: Machine | None = None
        for store_dy in range(min_dy, max_dy + 1):
            try:
                candidate = _assemble(
                    program,
                    p,
                    words,
                    tape_n,
                    rom_rows,
                    best.mem_pad,
                    display,
                    stream,
                    resp_pad,
                    best.stream_pad,
                    packed_rom,
                    short_return,
                    store,
                    rom_buffer,
                    True,
                    store_dy,
                    0,
                    0,
                    0,
                    hot,
                    tape_skip_batch=effective_skip_batch,
                    tape_relay_size=tape_relay_size,
                    in_north=in_north,
                    store_teleport=store_teleport,
                    store_answer_west=store_answer_west,
                    store_request_teleport=store_request_teleport,
                    store_chain_reach=store_chain_reach,
                    store_chain_pad=store_chain_pad,
                    store_feed_teleport=store_feed_teleport,
                    store_request_reach=store_request_reach,
                    store_compact_gate=store_compact_gate,
                    store_bank_order=store_bank_order,
                    trim_dead=trim_dead,
                    top_bus=top_bus,
                    store_shape=store_shape,
                    seek_layout=seek_layout,
                    seek_teleport=seek_teleport,
                    seek_taken_drop_east=seek_taken_drop_east,
                    in_west=in_west,
                    doom_loop_row=doom_loop_row,
                    doom_leaf_cols=doom_leaf_cols,
                )
            except MachineError:
                continue
            # Footprint is the contest objective. Once tied, shorter serial pipes
            # win, then occupied rectangle area and the smallest displacement.
            key = (
                candidate.footprint,
                sum(candidate.route_lengths.values()),
                candidate.width * candidate.height,
                abs(store_dy),
            )
            if compact_best is None:
                compact_best = candidate
            else:
                incumbent = (
                    compact_best.footprint,
                    sum(compact_best.route_lengths.values()),
                    compact_best.width * compact_best.height,
                    abs(compact_best.store_offset[1]),
                )
                if key < incumbent:
                    compact_best = candidate
        if compact_best is not None:
            return compact_best
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
    rom_buffer: int | None = None,
    compact: bool = False,
    store_dy: int = 0,
    store_dx: int = 0,
    mem_dx: int = 0,
    mem_dy: int = 0,
    hot: tuple[int, int] | None = None,
    tape_skip_batch: int = 1,
    tape_relay_size: tuple[int, int] | None = None,
    in_north: bool = False,
    store_teleport: bool = False,
    store_answer_west: bool = False,
    store_request_teleport: bool = False,
    store_chain_reach: bool = False,
    store_chain_pad: int = 0,
    store_feed_teleport: bool = False,
    store_request_reach: bool = False,
    store_compact_gate: bool = False,
    store_bank_order: tuple[int, ...] | None = None,
    trim_dead: bool = False,
    top_bus: bool = False,
    store_shape: tuple[int, int] | None = None,
    seek_layout=None,
    seek_teleport: bool = False,
    seek_taken_drop_east: bool = False,
    in_west: int = 0,
    doom_loop_row: int | None = None,
    doom_leaf_cols: tuple[int, ...] | None = None,
) -> Machine:
    seek = seek_layout is not None
    if seek and not short_return:
        raise MachineError("the seek drum requires the short-return drop rule")
    cpu = build_cpu(
        program,
        p,
        mem_pad=mem_pad,
        stream_pad=stream_pad,
        short_return=short_return,
        drain_unit_bits=0 if seek else DRAIN_UNIT_BITS.get(program.name, 0),
        tight_drops=not seek and program.name in TIGHT_STRUCT_DROPS,
        slab_pitch=(SEEK_SLAB_PITCH if seek else SLAB_PITCH).get(
            program.name, _SLAB_PITCH
        ),
        trim_dead=trim_dead,
        top_bus=top_bus,
        seek=seek,
        seek_taken_drop_east=seek_taken_drop_east,
    )
    W, H = cpu.width, cpu.height

    # ROM folded to roughly the CPU's own width, so neither dimension runs away
    # from the other (footprint is max(w, h)^2, ARCH.md §7.4).
    if seek:
        romlay = seek_layout  # built (with the operand fixpoint) by the caller
        nrows = seek_layout.rows_used
    elif packed_rom:
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

    if hot is not None and (mem_dx or mem_dy or store_dx or store_dy):
        # Two independent rearrangements of the same block. The banked seam builds its
        # own adapter, tier and tape at coordinates ``_two_tier`` owns, so an offset
        # meant for the single-store path would move nothing and silently claim to.
        # Nothing wants both yet — `hot` is the LLM interpreter, the offsets are `tcp`
        # and `gradebook` — so say so rather than pick one.
        raise MachineError(
            "a banked store and a relocated memory block cannot be combined: `hot` "
            "places the adapter, tier and tape itself, so mem/store offsets would be "
            "ignored"
        )

    g = _Grid()
    route_lengths: dict[str, int] = {}

    # ── ROM room, top-left ───────────────────────────────────────────────────
    RX, RY = 0, 0
    if seek:
        # The seek drum's cells span x 0..width-1 (col 0 is the seek riser), so
        # its interior starts one cell east of the wall.
        g.room(RX, RY, RX + romlay.width + 1, RY + romlay.height + 1)
        g.blit(RX + 1, RY + 1, romlay.cells)
        rom_bottom = RY + romlay.height + 1
    else:
        g.room(RX, RY, RX + romlay.width, RY + romlay.height + 1)
        g.blit(RX, RY + 1, romlay.cells)
        rom_bottom = RY + romlay.height + 1

    # ── CPU room ─────────────────────────────────────────────────────────────
    # The west margin carries two pipes that must not cross: the ROM corridor runs
    # down column 1, west of the I room, and only turns east on the fetch row —
    # which is far below the I room, so the two never meet. Two cells are the
    # minimum pipe length, hence x=8 with the input room (room 3..5, pipe 6..7)
    # and x=3 when the program has no input (ROM pipe 1..2).
    #
    # ROM-PLUS (``ROM_BUFFER``) buys its queue in the band between the ROM's bottom
    # wall and the CPU's top. Its final horizontal leg needs one blank row before
    # the CPU wall. The ordinary straight corridor uses adjacent room walls when
    # an input room keeps the CPU at x=8; the input-free x=3 layout needs one blank
    # row for reference-engine compatibility.
    CX = CPU_X_WITH_STREAM if stream else CPU_X_WITH_INPUT if cpu.has_in else CPU_X_WITHOUT_INPUT
    band_rows = 0
    if rom_buffer:
        corridor_x_hi = (
            max(CX + W + 1, romlay.width - 1) if program.name in ROM_CORRIDOR_WIDE else CX + W + 1
        )
        band_rows = rom_corridor_rows(rom_buffer, corridor_x_hi - 1)
    cpu_gap = (
        ROM_BUFFER_CPU_GAP
        if band_rows
        else ROM_CPU_GAP
        if cpu.has_in
        else ROM_CPU_GAP_WITHOUT_INPUT
    )
    if in_north:
        # The I room moves into the corridor band above the CPU (INPUT_NORTH),
        # so the band must be deep enough to hold a 3x3 room plus its two-cell
        # pipe into the north wall.
        #
        # ROM-PLUS puts a boustrophedon in the same band, and its rows run the
        # *whole* width — straight through the I room's columns. At gap 6 the
        # room's top wall lands on exactly the snake's last row (the collision is
        # `'-' vs '+'` at the room's west wall). One extra row of gap drops the
        # room clear of it; the corridor's own descent is in column 1, far west of
        # the room, so nothing else in the band moves.
        cpu_gap = max(cpu_gap, 7 if band_rows else 6)
    CY = rom_bottom + cpu_gap + band_rows
    g.room(CX, CY, CX + W + 1, CY + H + 1)
    g.blit(CX, CY, cpu.cells)

    # ── ROM -> CPU west wall at the fetch row ────────────────────────────────
    # Down the corridor west of the CPU, then east into the wall. The ROM has a
    # single outgoing pipe, so which of its `s` glyphs is nearest does not matter.
    fetch_y = CY + cpu.centre
    rom_capacity = g.draw_pipe(
        rom_corridor(
            want=rom_buffer or 0,
            x_lo=1,
            x_hi=corridor_x_hi if rom_buffer else CX + W + 1,
            y_top=rom_bottom + 1,
            rows=band_rows,
            fetch_y=fetch_y,
            wall_x=CX - 1,
        )
    )
    route_lengths["rom->cpu"] = rom_capacity

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
    in_x = CX + cpu.in_col - in_west
    if in_west and not CX + 1 <= in_x <= CX + W:
        raise MachineError(f"in_west {in_west} puts the input pipe off the CPU north wall")
    if cpu.has_in and in_north:
        # INPUT_NORTH: the I room sits in the corridor band and its pipe drops
        # into the north wall directly above the IN lane's own `r`. On the west
        # wall this pipe was the *binding* constraint that pushed the whole
        # memory band east (a memory `r` a few rows from `in_row` was nearer it
        # than the east-wall response), which cost every memory instruction the
        # extra walk twice over; from the north, its distance to any lane glyph
        # grows with the lane's depth and it rivals nothing.
        g.room(in_x - 1, CY - 5, in_x + 1, CY - 3)
        g.put(in_x, CY - 4, "I")
        route_lengths["input->cpu"] = g.draw_pipe([(in_x, CY - 2), (in_x, CY - 1)])
    elif cpu.has_in:
        g.room(3, iy - 1, 5, iy + 1)
        g.put(4, iy, "I")
        route_lengths["input->cpu"] = g.draw_pipe([(6, iy), (CX - 1, iy)])

    # ── CPU south wall -> O room ─────────────────────────────────────────────
    # Omitted on a display problem: emitting program output there is an error
    # (``SPEC.md``), and an unused outgoing pipe would still compete for every `s`.
    oy = CY + H + 2
    if cpu.has_out:
        route_lengths["cpu->output"] = g.draw_pipe(
            [(CX + cpu.out_col, oy), (CX + cpu.out_col, oy + 1)]
        )
        g.room(CX + cpu.out_col - 1, oy + 2, CX + cpu.out_col + 1, oy + 4)
        g.put(CX + cpu.out_col, oy + 3, "O")

    # ── adapter, east of the CPU ─────────────────────────────────────────────
    AX = CX + W + CPU_ADAPTER_GAP
    if hot is not None:
        # A second store seam. The hot bank is a small pipe tape and the cold one the
        # full store; the adapter routes by address range and the merger's ``R`` takes
        # whichever answers. See :func:`_two_tier` and ``TIER_PIPE_BANK``.
        seam = _two_tier(
            g,
            cpu,
            CX,
            CY,
            W,
            AX,
            tape_n,
            hot,
            tape_skip_batch=tape_skip_batch,
            tape_relay_size=tape_relay_size,
        )
        extra_regions = seam.regions
        req_row, resp_row = seam.req_row, seam.resp_row
        store_pipes = seam.pipes
        tape = seam.tape
        tier = seam.tier
    else:
        extra_regions, tier = {}, None
        AX0 = AX
        # Aligned so the request pipe leaves the CPU beside the memory lanes, but never
        # so high that the response pipe's westward leg grazes the adapter's top corner.
        # A small machine (few lanes, no memory) is the case that needs the clamp.
        AY0 = max(CY + cpu.mem_out_row - ADAPTER_IN_ROW, CY + cpu.mem_in_row + 3)
        resp_row_check = CY + cpu.mem_in_row
        if resp_row_check >= AY0 - 1:
            raise MachineError(
                f"response row {resp_row_check} is not clear of the adapter's top wall "
                f"at {AY0}: its westward leg would touch the adapter's corner and the "
                "engine would read a second, spurious pipe into the CPU"
            )
        # ``req_row`` is where the request pipe meets the *CPU*, so it is a property of
        # the CPU's memory lanes and stays put however far the memory moves. Only the
        # far end of that pipe travels with the adapter.
        req_row = AY0 + ADAPTER_IN_ROW
        AX, AY = AX0 + mem_dx, AY0 + mem_dy
        if AX < 1 or AY < 1:
            raise MachineError(f"the memory block leaves the grid: adapter at ({AX}, {AY})")
        g.room(AX, AY, AX + ADAPTER_W + 1, AY + ADAPTER_H + 1)
        g.blit(AX, AY, adapter_cells(address_first=store == "men-y"))
        cpu_out = (CX + W + 2, req_row)
        adapter_in = (AX - 1, AY + ADAPTER_IN_ROW)
        if mem_dx or mem_dy:
            try:
                request = constrained_route(
                    cpu_out,
                    adapter_in,
                    box=RouteBox(
                        left=max(0, min(cpu_out[0], adapter_in[0]) - _ROUTE_MARGIN),
                        top=max(0, min(cpu_out[1], adapter_in[1]) - _ROUTE_MARGIN),
                        right=max(cpu_out[0], adapter_in[0]) + _ROUTE_MARGIN,
                        bottom=max(cpu_out[1], adapter_in[1]) + _ROUTE_MARGIN,
                    ),
                    blocked=_keepout(g, (cpu_out, adapter_in)),
                    start_direction=(1, 0),
                    end_direction=(1, 0),
                )
            except RouteError as exc:
                raise MachineError(f"cannot route the request to the adapter: {exc}") from exc
            route_lengths["cpu->adapter"] = g.draw_pipe(request)
        else:
            route_lengths["cpu->adapter"] = g.draw_pipe([cpu_out, adapter_in])

        # ── tape, east of the adapter ────────────────────────────────────────
        if store == "men":
            from ..memory_men_store import men_block

            tape = men_block(tape_n)
        elif store == "men-y":
            from ..memory_men_y import y_men_block

            tape = y_men_block(tape_n)
        elif store == "grid":
            tape = grid_block(tape_n)
        elif store == "men-v3":
            from ..memory_men_v3 import v3_store_block, v3_store_grid_block

            v3_ops = STORE_OPS.get(program.name, 500)
            if store_shape is not None and store_shape[0] > 1:
                v3_cols, v3_rows = store_shape
                if v3_cols * v3_rows < tape_n:
                    raise MachineError(
                        f"STORE shape {v3_cols}x{v3_rows} holds {v3_cols * v3_rows} "
                        f"cells but the tape needs {tape_n}"
                    )
                tape = v3_store_grid_block(v3_cols, v3_rows, ops=v3_ops)
            else:
                tape = v3_store_block(tape_n, ops=v3_ops)
        elif store == "taped":
            from ..memory_taped import taped_store_block

            # The block's own placement does not depend on the block, so its
            # origin is known before it is built — which is what lets the answer
            # collector be widened to a column named in *machine* coordinates.
            tx_pre = AX + ADAPTER_W + adapter_tape_gap(program.name, store) + store_dx
            tape = taped_store_block(
                tape_n,
                TAPED_BANKS.get(program.name, 4),
                skip_batch=(
                    tape_skip_batch
                    if tape_skip_batch != 1
                    else TAPED_SKIP_BATCH.get(program.name, 1)
                ),
                # Land the collector's west wall on ``CX + W + 3``: the column
                # the deleted teleport U used to occupy, one clear of the
                # response pipe's own attachment cell.
                answer_west=(CX + W + 4 - tx_pre) if store_answer_west else None,
                compact_gate=store_compact_gate,
                order=store_bank_order,
                chain_reach=store_chain_reach,
                chain_pad=store_chain_pad,
                feed_teleport=store_feed_teleport,
                # Land the first gate's roof one row under the adapter's floor,
                # so its west wall stands beside the adapter and the request is
                # a drop, not a corridor. Same trick as ``answer_west``: the
                # block's origin is known before the block is.
                request_roof=(
                    (AY + ADAPTER_H + 2) - (CY + mem_dy + store_dy)
                    if store_request_reach
                    else None
                ),
            )
        elif store == "tape":
            tape = tape_block(
                tape_n,
                skip_batch=tape_skip_batch,
                relay_size=tape_relay_size,
            )
        else:
            raise MachineError(
                f"unknown store tier {store!r}; expected one of {STORE_TIERS!r}"
            )
        TX = AX + ADAPTER_W + adapter_tape_gap(program.name, store) + store_dx
        TY = CY + mem_dy + store_dy
        if TY < 0 or TX < 0:
            raise MachineError(f"STORE placement leaves the grid: ({TX}, {TY})")
        g.blit(TX, TY, tape.cells)

        # adapter east wall -> the tape's request stub
        tin_x, tin_y = TX + tape.in_cell[0], TY + tape.in_cell[1]
        ax_out = AX + ADAPTER_W + 2
        mid = ax_out + 2
        adapter_out = (ax_out, AY + ADAPTER_OUT_ROW)
        store_in = (tin_x - 1, tin_y)
        moved = bool(mem_dx or mem_dy or store_dx)
        req_tele_pipes = 0
        req_tele_regions: dict[str, tuple[int, int, int, int]] = {}
        if store_request_reach and store_request_teleport:
            raise MachineError(
                "store_request_reach and store_request_teleport are two answers to "
                "one question: the gate's own room reaching the adapter, or a "
                "forwarder bridging the gap. Pick one."
            )
        if (store_request_reach or store_chain_reach) and store != "taped":
            raise MachineError(
                f"only the taped tier has gate rooms to grow, not {store!r}"
            )
        if store_request_reach:
            # No room at all on this leg any more — the STORE's **own** first
            # gate grew its roof up to the adapter's floor, so what used to be a
            # 58-cell corridor, and then a forwarder plus two stubs, is now a
            # two-cell drop off the adapter's south wall onto the gate's west
            # wall. A forwarder costs ~5.2 cells of re-serialisation (one man on
            # a six-cell loop against a pipe's free pipelining, M12), so not
            # having one is worth more than having a short one — and the gate's
            # ``U`` was reading from *any* incoming pipe with no distance term
            # the whole time. It turns away from the **wall**, not from the
            # direction the request came down, which is the only reason the
            # entry may move 33 rows north of the man who reads it.
            #
            # The request leaves the adapter's south wall, which is free: the
            # adapter has exactly one incoming and one outgoing pipe (see
            # :data:`_ADAPTER`), so every ``r``/``s`` in it binds wherever they
            # attach.
            floor_y = AY + ADAPTER_H + 1
            if not AX + 1 <= tin_x <= AX + ADAPTER_W:
                raise MachineError(
                    f"the store's request column {tin_x} is not under the adapter's "
                    f"floor ({AX + 1}..{AX + ADAPTER_W}); the drop has nowhere to start"
                )
            if tin_y - 1 <= floor_y + 1:
                raise MachineError(
                    f"the store's request row {tin_y} leaves no drop below the "
                    f"adapter's floor at {floor_y}"
                )
            # ... down to one cell short of the block's own ``>``, which is the
            # cell that turns the drop into the gate's west wall.
            route_lengths["adapter->store"] = g.draw_pipe(
                [(tin_x, floor_y + 1), (tin_x, tin_y - 1)]
            )
        elif store_request_teleport:
            # The request crosses a **room** instead of a 58-cell pipe.
            #
            # This is the same lever the answer path already pulled, on the leg
            # the profile says is now the expensive one: *every* store access —
            # 87,490 CPU-blocking reads in a nine-round run, plus every write's
            # request — walks all of ``adapter->store``, and the CPU is stopped
            # on the answer for the whole round trip, so those cells are paid
            # serially and in full. ``R`` has **no distance term** (SPEC.md
            # §Nearest — "nearest" only picks *which* pipe), so a tall room
            # spans the canvas gap between the adapter's floor and the first
            # gate in one instruction, and what is left is two stubs.
            #
            # The room hangs in the corridor between the adapter's south wall
            # and the gate strip's north wall, which is empty for its whole
            # height. Its exit column lands on ``store_in`` so the gate's own
            # two-cell stub still delivers the request through the **west**
            # wall: the gate's ``U`` turns away from the side it read from, so
            # the entry side is load-bearing and is deliberately not moved.
            #
            # The request leaves the adapter's **south** wall rather than its
            # east one, which is free: the adapter has exactly one incoming and
            # one outgoing pipe (see :data:`_ADAPTER`), so every ``r``/``s`` in
            # it binds unambiguously wherever the pipes attach.
            #
            # The room is the mechanism, not its absence — deleting the answer
            # path's forwarders in favour of a plain pipe cost +4.14%, and a
            # relay *chain* lost to a single forwarder plus a short pipe. So
            # this adds exactly one room and leaves two stubs.
            from ..memory_men import teleport_v

            floor_y = AY + ADAPTER_H + 1  # the adapter's south wall
            rx0 = store_in[0] - 1  # west wall, one clear of the exit column
            rx1 = rx0 + _TELE_W + 1
            ry0, ry1 = floor_y + 3, tin_y - 4
            # The roof column has to be under the adapter's floor *and* inside
            # the room's north wall; the west end of the room is west of the
            # adapter, so the shared column is the east one.
            drop = max(rx0 + 1, AX + 1)
            if drop > min(rx1 - 1, AX + ADAPTER_W):
                raise MachineError(
                    "the store request teleport's roof does not reach the adapter's "
                    f"floor: columns {rx0 + 1}..{rx1 - 1} miss {AX + 1}..{AX + ADAPTER_W}"
                )
            if ry1 - ry0 - 1 < _TELE_H:
                raise MachineError(
                    f"the store request teleport has no interior: rows {ry0}..{ry1}"
                )
            g.room(rx0, ry0, rx1, ry1)
            t_rows, _ = teleport_v(ry1 - ry0 - 1)
            for kk, row in enumerate(t_rows):
                g.text(rx0 + 1, ry0 + 1 + kk, row.replace(" ", "\0"))
            # adapter floor -> the room's roof ...
            n1 = g.draw_pipe([(drop, floor_y + 1), (drop, ry0 - 1)])
            # ... and the room's floor -> the gate's own request stub, which the
            # last cell joins (it is already a ``>``, so the draw is idempotent).
            n2 = g.draw_pipe([(store_in[0], ry1 + 1), store_in, (tin_x, tin_y)])
            route_lengths["adapter->store"] = n1 + n2 - 1
            req_tele_pipes = 1  # the request used to be ONE pipe; now it is two
            req_tele_regions = {
                "teleport:REQ": (rx0, ry0, rx1 - rx0 + 1, ry1 - ry0 + 1)
            }
        elif compact or moved:
            try:
                # The box is the endpoints' bounding rectangle plus a two-cell margin.
                # A STORE placed *west* of the adapter needs that margin: the pipe leaves
                # the adapter eastward and must arrive eastward, so it has to overshoot
                # west of its own target and come back — a detour the tight box forbids.
                store_request = constrained_route(
                    adapter_out,
                    store_in,
                    box=RouteBox(
                        # ``TX`` is the block's own empty column 0 — the one lane that
                        # runs the full height of the STORE block without meeting the
                        # relay. An adapter parked *below* the block has to use it to
                        # climb back up to the request stub, so the box must reach it.
                        left=max(0, min(adapter_out[0], store_in[0], TX) - _ROUTE_MARGIN),
                        top=max(0, min(adapter_out[1], store_in[1]) - _ROUTE_MARGIN),
                        right=max(adapter_out[0], store_in[0]) + _ROUTE_MARGIN,
                        bottom=max(adapter_out[1], store_in[1]) + _ROUTE_MARGIN,
                    ),
                    blocked=_keepout(g, (adapter_out, store_in)) if moved else g.c,
                    start_direction=(1, 0),
                    end_direction=(1, 0),
                )
            except RouteError as exc:
                raise MachineError(f"cannot compact STORE request route: {exc}") from exc
            route_lengths["adapter->store"] = g.draw_pipe(store_request)
        else:
            route_lengths["adapter->store"] = g.draw_pipe(
                [
                    adapter_out,
                    (mid, AY + ADAPTER_OUT_ROW),
                    (mid, tin_y),
                    store_in,
                ]
            )

        # The tape's response stub -> CPU east wall. The default preserves the shipped
        # waypoint route byte-for-byte. ``compact`` instead states the real geometry:
        # stay in the corridor above the three attachments, avoid every occupied cell,
        # enter the CPU from the east, and use the fewest cells. That distinction is
        # what lets placement become constraint-driven later without baking another
        # set of coordinates into this connection.
        tout_x, tout_y = TX + tape.out_cell[0], TY + tape.out_cell[1]
        resp_row = CY + cpu.mem_in_row
        top = min(AY, tout_y, resp_row) - MEM_RESPONSE_CLEARANCE
        # ``resp_pad`` inserts a there-and-back jog in the corridor above the machine,
        # lengthening this pipe by ``2 * resp_pad`` cells and changing nothing else. It
        # exists to *measure* ARCH.md §7.4b's "every extra pipe cell costs one tick" on a
        # real machine rather than on a 13-tick one; see tests/test_lm1_pipe_cost.py.
        tele_regions: dict[str, tuple[int, int, int, int]] = {}
        tele_pipes = 0
        if store_answer_west:
            # No forwarding rooms at all: the STORE's own answer collector was
            # widened until its west end reached the CPU, so the response is one
            # stub pipe again — the thing L and U were invented to replace.
            #
            # The collector is a teleport already (it merges four banks with
            # ``R``, which has no distance term), and widening a teleport is
            # free: the value still crosses it in one instruction. So the two
            # rooms this generator used to add were not buying a teleport, they
            # were *relaying between* teleports — three hops where the store
            # could simply have handed the value over itself. It leaves on
            # ``resp_row``'s own attachment cell, so every memory ``r``'s
            # binding is untouched.
            n1 = g.draw_pipe(
                [(tout_x, tout_y + 1), (tout_x, resp_row), (CX + W + 2, resp_row)]
            )
            route_lengths["store->cpu"] = n1
        elif store_teleport:
            # The response comes home through two teleports instead of a long
            # pipe. ``R`` receives from any incoming pipe with **no distance
            # term** (SPEC.md), so a wide room is a horizontal teleport and a
            # tall one a vertical teleport: the value crosses each room in one
            # instruction, and the per-read latency collapses from the pipe's
            # whole length (~59 cells here) to the three short stubs (~7).
            # L collects the store's answer above its north wall and carries it
            # west; U carries it down the CPU's east side and hands it to the
            # response row with exactly the attachment cell the plain pipe used,
            # so every memory ``r``'s binding is untouched. Cost: two men.
            from ..memory_men import teleport, teleport_v

            ux = CX + W + 4  # U hugs the CPU's east wall
            lx0 = ux + _TELE_W + 4
            lx1 = tout_x + 2
            l_y1 = tout_y - 3  # L's south wall: two stub cells below it reach tout
            l_y0 = l_y1 - _TELE_H - 1
            # U spans from just above L's hand-off row down to the response row,
            # wherever the store's outlet put L (men-v3's is at the block's top).
            u_top = min(CY + 3, resp_row - 6, l_y0)
            u_bot = resp_row + 1
            if u_bot - u_top < 3:
                raise MachineError("teleport U has no interior: resp_row too high")
            u_rows, _ = teleport_v(u_bot - u_top - 1)
            g.room(ux, u_top, ux + _TELE_W + 1, u_bot)
            for kk, row in enumerate(u_rows):
                g.text(ux + 1, u_top + 1 + kk, row.replace(" ", "\0"))
            if lx1 - lx0 < 8 or l_y0 <= 1:
                raise MachineError("teleport L has no room between the CPU and the STORE")
            l_rows, _ = teleport(lx1 - lx0 - 1)
            g.room(lx0, l_y0, lx1, l_y1)
            for kk, row in enumerate(l_rows):
                g.text(lx0 + 1, l_y0 + 1 + kk, row.replace(" ", "\0"))
            # the store's answer climbs two cells into L's south wall ...
            n1 = g.draw_pipe([(tout_x, tout_y - 1), (tout_x, l_y1 + 1)])
            # ... L hands it west to U's east side ...
            n2 = g.draw_pipe([(lx0 - 1, l_y0 + 1), (ux + _TELE_W + 2, l_y0 + 1)])
            # ... and U drops it onto the response row's own attachment cell.
            n3 = g.draw_pipe([(ux - 1, resp_row), (CX + W + 2, resp_row)])
            route_lengths["store->cpu"] = n1 + n2 + n3
            tele_pipes = 2  # the response used to be ONE pipe; now it is three
            tele_regions = {
                "teleport:L": (lx0, l_y0, lx1 - lx0 + 1, _TELE_H + 2),
                "teleport:U": (ux, u_top, _TELE_W + 2, u_bot - u_top + 1),
            }
        elif compact or moved:
            start = (tout_x, tout_y - 1)
            end = (CX + W + 2, resp_row)
            # The corridor between the CPU and the adapter is spoken for: the request
            # pipe crosses it on ``req_row``. A STORE that no longer sits north-east of
            # everything therefore has to climb *east of the adapter*, so the box has to
            # reach past the adapter's east wall or no route exists at all.
            box = RouteBox(
                left=max(0, min(start[0], end[0]) - _ROUTE_MARGIN),
                top=max(0, top - (1 if resp_pad else 0)),
                right=max(start[0], end[0] + 1, ax_out) + _ROUTE_MARGIN,
                bottom=max(start[1], end[1]) + _ROUTE_MARGIN,
            )
            blocked = _keepout(g, (start, end)) if moved else g.c
            try:
                shortest = constrained_route(
                    start,
                    end,
                    box=box,
                    blocked=blocked,
                    end_direction=(-1, 0),
                )
                response_route = constrained_route(
                    start,
                    end,
                    box=box,
                    blocked=blocked,
                    min_cells=len(shortest) + 2 * resp_pad,
                    end_direction=(-1, 0),
                )
            except RouteError as exc:
                raise MachineError(f"cannot compact STORE response route: {exc}") from exc
            route_lengths["store->cpu"] = g.draw_pipe(response_route)
        else:
            jog = (
                [(tout_x, top), (tout_x + resp_pad, top), (tout_x + resp_pad, top - 1)]
                if resp_pad
                else [(tout_x, top)]
            )
            route_lengths["store->cpu"] = g.draw_pipe(
                [
                    (tout_x, tout_y - 1),
                    *jog,
                    (CX + W + 3, top - (1 if resp_pad else 0)),
                    (CX + W + 3, resp_row),
                    (CX + W + 2, resp_row),
                ]
            )

    # ── the LM-75, below the CPU ─────────────────────────────────────────────
    # With `DSP p` the CPU owns one display pipe instead of three, so the fan-out
    # room goes in first and the panel hangs off *its* south wall. `_display` is
    # unchanged: the relay hands it the same three columns the CPU used to.
    dsp_touches: dict[str, tuple[int, int]] = {}
    relay_cols: dict[str, int] | None = None
    relay_wall = CY + H + 1
    if display and cpu.dsp_cols:
        if Band.DSP in cpu.dsp_cols:
            in_col = CX + cpu.dsp_cols[Band.DSP]
            relay_cols, relay_wall = _dsp_relay(g, CX, CY + H + 1, in_col)
            dsp_touches[Band.DSP] = (in_col, CY + H + 2)
        dsp_touches |= _display(g, cpu, CX, relay_wall, AX, display, cols=relay_cols)

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
        blk, stream_touches, (SX, SY) = _stream(
            g, cpu, CX, CY + H + 1, stream, unit=program.unit,
            doom_loop_row=doom_loop_row, doom_leaf_cols=doom_leaf_cols,
        )

    # ── seek: the jump-request pipe, CPU east wall -> around -> ROM east wall ─
    # Drawn last so its northbound leg can clear everything already placed. Its
    # length is only jump-notice latency; the drum's q sees the value the tick
    # it enters the pipe, so the man parks on the station's r rather than
    # emitting words the CPU would flush.
    seek_regions: dict[str, tuple[int, int, int, int]] = {}
    if seek:
        cmd_y = CY + (H - 9)  # build_cpu: bottom = taken_row + 9
        x_e = max(x for x, _ in g.c) + 2
        rom_east = RX + romlay.width + 1
        ry = RY + 2
        if blk is not None:
            # Cross in the gap between the store block's bottom and the STREAM
            # unit's top (both exist on the machines that hang a unit below).
            y_b = SY - 2
            if hot is None and y_b <= TY + tape.height - 1:
                raise MachineError(
                    f"seek: no clear row between the store block and the unit "
                    f"(y_b={y_b}, store bottom cell={TY + tape.height - 1})"
                )
            if y_b <= cmd_y + 1:
                raise MachineError("seek: the unit sits too high for the cmd dive")
        else:
            y_b = max(y for _, y in g.c) + 2
        if seek_teleport:
            route_lengths["cpu->drum"], seek_regions = _seek_teleport(
                g, cmd_y=cmd_y, src_x=CX + W + 2, x_e=x_e, rom_east=rom_east, ry=ry, y_b=y_b
            )
        else:
            route_lengths["cpu->drum"] = g.draw_pipe(
                [
                    (CX + W + 2, cmd_y),
                    (CX + W + 3, cmd_y),  # east out of the wall, then dive south
                    (CX + W + 3, y_b),
                    (x_e, y_b),
                    (x_e, ry),
                    (rom_east + 1, ry),
                ]
            )

    rows = g.rows()

    # ── name every block in grid coordinates ─────────────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        f"cpu:{n}": (CX + x, CY + y, w, h) for n, (x, y, w, h) in cpu.regions.items()
    }
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)
    if hot is None:
        regions["adapter"] = (AX, AY, ADAPTER_W + 2, ADAPTER_H + 2)
        regions["tape"] = (TX, TY, tape.width, tape.height)
        regions.update(tele_regions)
        regions.update(req_tele_regions)
    else:
        regions.update(extra_regions)
    regions.update(seek_regions)
    # The fetch corridor, which is otherwise the one unnamed thing on the overlay — and
    # the only place the ROM-PLUS snake would show up at all. Its cell count *is* its
    # capacity in words (``SPEC.md``), so the note is the number that matters.
    if band_rows:
        regions["rom:corridor"] = (1, rom_bottom + 1, (CX + W + 1), band_rows + 1)
    if cpu.has_in:
        regions["io:I"] = (in_x - 1, CY - 5, 3, 3) if in_north else (3, iy - 1, 3, 3)
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
        touches["in"] = (in_x, CY - 1) if in_north else (CX - 1, iy)
    if cpu.has_out:
        touches["out"] = (CX + cpu.out_col, CY + H + 2)
    if seek:
        touches["cmd"] = (CX + W + 2, CY + (H - 9))
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
    # ``grid`` bands three pipes per slot — router->decoder, decoder->cell,
    # cell->collector — and every one of them is a two-cell stub between facing
    # walls, which is exactly why the extra decoder hop is nearly free.
    if hot is None:
        _STORE_PIPES = {
            "men-y": None, "men-v3": None, "taped": None,
            "men": 2 * tape_n, "grid": 3 * tape_n, "tape": 2,
        }
        store_pipes = (
            tape.pipes if store in ("men-y", "men-v3", "taped") else _STORE_PIPES[store]
        ) + 1 + tele_pipes + req_tele_pipes
    # The teleported seek request is three pipes where ``touches["cmd"]`` counts one.
    _check_pipe_count(
        rows, expected=len(touches) + store_pipes + extra + (2 if seek_regions else 0)
    )
    return Machine(
        rows=rows,
        regions=regions,
        program=program,
        plan=p,
        tape_n=tape_n,
        rom_rows=romlay.rows_used,
        mem_pad=mem_pad,
        tape_skip_batch=tape_skip_batch,
        tape_relay_size=_resolve_tape_relay(tape_skip_batch, tape_relay_size)[1],
        stream_pad=stream_pad,
        display=display if dsp_touches else None,
        dsp_glyphs={
            band: (CX + x, CY + y) for x, y, _glyph, band in cpu.pipe_glyphs if band in DSP_BANDS
        },
        stream=blk,
        tier=tier,
        rom_capacity=rom_capacity,
        route_lengths=route_lengths,
        compact=compact,
        store_offset=(store_dx, store_dy),
        mem_offset=(mem_dx, mem_dy),
    )


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
#: Every memory tier ``build`` will accept, in the order they are worth trying.
#: ``tape`` is the default and stays it — see ARCH.md §4.1 for why ``grid`` loses on
#: footprint despite an access cost that ignores ``n``.
STORE_TIERS = ("tape", "grid", "men", "men-y", "men-v3", "taped")

#: Blank columns between the CPU's east wall and the adapter room, and between the
#: adapter and the STORE block. Both are paid **twice**: once in the machine's width,
#: which is squared in the score, and once in the memory *response* pipe, whose whole
#: length is charged on every read because a read is strictly serial (§7.4b) — and a
#: read is where 45% of `gradebook`'s CPU time goes. So these are not cosmetic spacing;
#: they are two of the cheapest numbers in the generator to be wrong about.
#:
#: ``CPU_ADAPTER_GAP`` is a hard floor: every program fails to place its pipes at 3 or
#: less, so 4 is the real minimum and not a guess.
#:
#: ``ADAPTER_TAPE_GAP`` was 6 and wanted to be **1**, which is worth ~9-10% of
#: footprint on every width-bound machine, plus five cells off every read:
#:
#: | | 6 | 1 |
#: |---|---|---|
#: | `brackets` | 95x70, 9,025 | **90x70, 8,100** |
#: | `gradebook` | 113x101, 12,769 | **108x101, 11,664** |
#: | `palette` | 95x89, 9,025 | **90x89, 8,100** |
#: | `sudoku-validity` | 83x80, 6,889 | **80x80, 6,400** |
#: | `tcp` | 109x74, 11,881 | **104x74, 10,816** |
#:
#: Height-bound machines (`matmul`, `snake`, `snake-ring`, `plotter`) keep their
#: footprint and still gain the ticks. `palette` was in that list on the strength of
#: being a display problem like `plotter`; it is not, it is 95 wide against 89 tall and
#: the five columns come straight off its score.
CPU_ADAPTER_GAP = 4
ADAPTER_TAPE_GAP = 1

#: Whole-machine placement floors. These are geometric minima, not visual
#: padding:
#:
#: * with an input room, columns 3..5 hold that room, 6..7 are the mandatory
#:   two-cell pipe, and the CPU west wall can therefore start at x=8;
#: * without input, the ROM corridor itself needs two cells (x=1..2), so the CPU
#:   can start at x=3;
#: * STREAM layouts retain their own west clearance. FastLittleman accepts the
#:   x=3 placement, but the reference engine leaves the coprocessor machine
#:   permanently blocked there, so this is a separately validated floor;
#: * with an input room, the ordinary ROM and CPU walls may be adjacent. The
#:   input-free x=3 layout needs one blank row for reference-engine compatibility;
#:   a buffered boustrophedon needs still more clearance.
CPU_X_WITH_INPUT = 8
CPU_X_WITHOUT_INPUT = 3
CPU_X_WITH_STREAM = 8
ROM_CPU_GAP = 1
ROM_CPU_GAP_WITHOUT_INPUT = 2
ROM_BUFFER_CPU_GAP = 3

#: The memory response runs above the adapter/tape attachment, not above the
#: whole CPU. One row above the highest attachment is the geometric minimum;
#: zero collides the horizontal and vertical legs on real depth-4 layouts.
MEM_RESPONSE_CLEARANCE = 1

#: Programs that need a wider adapter-to-STORE gap than the default.
#:
#: ``matmul`` is the only one, and it does not merely fail to *place* at 1 — it places,
#: loads, and then **hangs**, every case at the tick cap. The STREAM block's rings sit
#: in that corridor, so a gap the request pipe fits through is not necessarily one the
#: rings survive, and the binding checks cannot see the difference. It is verified
#: working at 3, 4, 5 and 6 (and 3 is even slightly faster), but `matmul` is
#: height-bound at 90 rows so *no* gap changes its footprint and the tick win is ~1%.
#: Not worth re-validating a STREAM machine for, so it stays on the exact geometry that
#: scored 1,446,608,970.
ADAPTER_TAPE_GAP_FOR: dict[str, int] = {"matmul": 6}

#: The floor the *store tier* imposes on that gap, which is a separate thing from the
#: per-program override above: it is the block east of the corridor, not the CPU west of
#: it, that fails to bind. Only ``tape`` — the shipped tier — reaches 1. Measured, by
#: building `snake-ring` on each tier at every gap from 1 to 7:
#:
#: | tier | binds from | note |
#: |---|---|---|
#: | `tape` | **1** | the default; footprint flat across all seven |
#: | `men-y` | 3 | flat too, so the floor costs it nothing |
#: | `men` | 5 | and it *grows* per column: 21,025 at 5, 21,609 at 7 |
#: | `grid` | 6 | |
#:
#: All three non-``tape`` tiers are measured negatives (ARCH.md §4.1) whose numbers are
#: quoted in tests as comparisons, so they are pinned at the 6 they were measured on
#: rather than dropped to their true floors — re-measuring a losing tier buys nothing,
#: and moving `men` off 6 would silently restate a recorded result.
ADAPTER_TAPE_GAP_BY_STORE: dict[str, int] = {"grid": 6, "men": 6, "men-y": 6}


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

#: Rows kept clear between the CPU's top row and the two store blocks, for the two
#: answer lanes to run west in.
#:
#: The original seam climbed to ``cy - 1`` / ``cy - 3`` — the band *above* the CPU —
#: which worked only while the ROM's fold left that band empty. It does not any more:
#: at ``rom_rows=85`` the ROM's bottom rows sit directly on the CPU's top and reach
#: past the tape's column, so the tape's riser hit ``'-' vs '|'`` at ``(166, 88)`` on
#: every pad pair. There is no clear row above, because the ROM is what is there.
#:
#: So the lanes move *below* the CPU's top row instead, which is also what the
#: one-tier build was changed to do ("routing above the whole CPU was a large detour
#: paid on every read"): the blocks drop three rows, and the two rows that frees are
#: exactly the merger's own interior rows, so each answer runs straight west into a
#: merger door with no climb and no descent at all.
_ANS_BAND = 3

#: A teleport room's ``R``/``s`` loop is 4x2 whatever the room's length: these are the
#: *loop's* extent, not the room's. See :func:`memory_men.teleport`.
_TELE_W = 4
_TELE_H = 2


def _seek_teleport(
    g: _Grid, *, cmd_y: int, src_x: int, x_e: int, rom_east: int, ry: int, y_b: int
) -> tuple[int, dict[str, tuple[int, int, int, int]]]:
    """The CPU -> drum seek request, teleported instead of piped end to end.

    The plain route is the machine's longest pipe by a factor of seven: out of the
    CPU's east wall, south past everything that hangs below it, east across the whole
    store block, north up the outside and back west into the drum's east wall — 437
    cells on ``deadman-3d``'s taped machine. Every cell is a tick of latency on the
    station's ``r``, and the CPU is flushing the corridor while it waits, so the whole
    length is on the critical path of every taken jump.

    ``R`` has no distance term (SPEC.md: "nearest" is only ``r``'s rule), so a room is
    crossed in one instruction whatever its size. Two rooms cover the two long legs:

    * **H** — a wide room in the free band between the store block's bottom and
      whatever hangs below the CPU. It swallows the whole eastward crossing.
    * **V** — a tall room in the empty column east of the drum and north of the
      store, from the drum's own seek row down to the store block's top. It swallows
      the northward climb *and* the westward return into the drum.

    What is left is three stubs: the CPU's dive into H, the one column that threads
    the store block's east side (the only through-column there is), and two cells from
    V into the drum. The drum's attachment cell and the CPU's send cell are both
    exactly where the plain pipe put them, so no ``r``/``s``/``q`` binding moves.
    """
    from ..memory_men import teleport, teleport_v

    def clear(x0: int, y0: int, x1: int, y1: int) -> bool:
        return not any((x, y) in g.c for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))

    # ── H, the horizontal teleport ───────────────────────────────────────────
    hx0, hx1 = src_x + 1, x_e
    hy1 = y_b + 1
    hy0 = hy1 - (_TELE_H + 1)
    if not clear(hx0, hy0, hx1, hy1):
        raise MachineError("seek teleport: no clear band below the store for room H")
    while hy0 - 1 > cmd_y + 2 and clear(hx0, hy0 - 1, hx1, hy0 - 1):
        hy0 -= 1  # raise the north wall: every row raised is a cell off the dive
    # ── V, the vertical teleport ─────────────────────────────────────────────
    vx0, vx1, vy0 = rom_east + 3, x_e, ry - 1  # +3 leaves the drum stub its two cells
    vy1 = vy0 + _TELE_H + 1
    if vx1 - vx0 < _TELE_W + 1 or not clear(vx0, vy0, vx1, vy1):
        raise MachineError("seek teleport: no clear column east of the drum for room V")
    while vy1 + 1 < hy0 - 2 and clear(vx0, vy1 + 1, vx1, vy1 + 1):
        vy1 += 1  # lower the south wall: every row lowered is a cell off the climb
    # The through-column: H's north wall east of V's south wall, so the climb is
    # one straight leg. On the taped machine it is the only column clear of the
    # store block's east return pipe at all.
    thru = hx1 - 1
    if not clear(thru, vy1 + 1, thru, hy0 - 1):
        raise MachineError(f"seek teleport: column {thru} is not clear between V and H")

    g.room(hx0, hy0, hx1, hy1)
    h_rows, _ = teleport(hx1 - hx0 - 1)
    for k, row in enumerate(h_rows):
        g.text(hx0 + 1, hy0 + 1 + k, row.replace(" ", "\0"))
    g.room(vx0, vy0, vx1, vy1)
    v_rows, _ = teleport_v(vy1 - vy0 - 1)
    for k, row in enumerate(v_rows):
        g.text(vx0 + 1, vy0 + 1 + k, row.replace(" ", "\0"))

    # the CPU's own send cell, east two and down into H's north wall ...
    n1 = g.draw_pipe([(src_x, cmd_y), (hx0 + 1, cmd_y), (hx0 + 1, hy0 - 1)])
    # ... H hands it up the through-column into V's south wall ...
    n2 = g.draw_pipe([(thru, hy0 - 1), (thru, vy1 + 1)])
    # ... and V hands it west onto the drum's own attachment cell.
    n3 = g.draw_pipe([(vx0 - 1, ry), (rom_east + 1, ry)])
    return n1 + n2 + n3, {
        "seek:H": (hx0, hy0, hx1 - hx0 + 1, hy1 - hy0 + 1),
        "seek:V": (vx0, vy0, vx1 - vx0 + 1, vy1 - vy0 + 1),
    }

#: Use the side-ported grid man-memory for the hot tier, so its answer leaves the
#: same wall its request enters. The bottom-ported block forces the answer to travel
#: the block's whole width and then back west across the machine; with both ports on
#: one wall the answer is a few cells long and never leaves the adapter's column band.
#:
#: **Off, because it does not place yet.** With both stubs on the west wall the
#: request pipe and the answer pipe leave the same side and cross:
#: ``collision at (109, 93): '>' vs '|'``. The routing in :func:`_two_tier` still
#: assumes the answer comes out east. Every measured number in ``LLM-DESIGN.md`` is
#: the bottom-ported block, so this stays off until the west-wall routing is written.
TIER_SIDE_PORTS = False

#: Make the hot bank a **pipe tape** instead of a man-memory.
#:
#: The seam was built to accelerate the hot addresses, and it does — but every slot
#: of a man-memory is a live little man, and the grader's cost is ``runners x ticks``.
#: Measured on `little-little-man`: a 10-slot man tier scored 13% better in ticks and
#: was refused ``11/28``; a 52-slot one was refused ``4/28``. A pipe tape stores its
#: words as values in a rotating ring, so it has **four men at n=52 and four at
#: n=427** — constant in size. Banking the store into a small hot ring and the full
#: cold one therefore buys most of the latency win at almost none of the wall clock.
TIER_PIPE_BANK = True

#: Skip-batch for the *hot* bank only; ``None`` means share the cold bank's.
HOT_SKIP_BATCH: int | None = None


@dataclass
class _PipeBank:
    """A :func:`tape_block` dressed as a tier: the seam only wants these six fields.

    ``tape_block`` reports ``slots`` as the *ring cell* count (``2n + 4``), so the
    addressable size has to be carried alongside it rather than read back off it.
    """

    tape: object
    slots: int

    @property
    def cells(self) -> dict[tuple[int, int], str]:
        return self.tape.cells

    @property
    def width(self) -> int:
        return self.tape.width

    @property
    def height(self) -> int:
        return self.tape.height

    @property
    def in_cell(self) -> tuple[int, int]:
        return self.tape.in_cell

    @property
    def out_cell(self) -> tuple[int, int]:
        return self.tape.out_cell

    #: A tape's ring is two pipes at every size, and unlike the man-memory's the
    #: block's own request and answer stubs are *not* halves of the section's pipes —
    #: they are the ring legs themselves. So the seam's ``- 2`` has to be cancelled
    #: here, which is what the extra two account for: 4 + 2 + 4 - 2 = 8.
    pipes: int = 4


def _two_tier(
    g: _Grid,
    cpu: _Cpu,
    cx: int,
    cy: int,
    w: int,
    ax: int,
    tape_n: int,
    hot: tuple[int, int],
    *,
    tape_skip_batch: int = 1,
    tape_relay_size: tuple[int, int] | None = None,
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
    # ``grid_side_block`` puts the answer stub on the **same wall** as the request
    # instead of on the far side of the block, which is the whole reason it exists
    # ("a tape-shaped slot"). Both of the tier's pipes then face the merger, so the
    # answer needs no lane across the machine at all — see ``TIER_SIDE_PORTS``.
    cols, rows_ = hot
    if TIER_PIPE_BANK:
        # A *pipe tape* as the hot bank. Its whole point is that a stored word is a
        # value in a pipe, not a little man, so this costs four men at any size while
        # a man-memory costs two per slot plus a fixed staff. It answers in
        # ``8.0 * hot_top`` rather than ~31 ticks, which is far worse per read and
        # still 8x better than the 427-slot cold bank — and the men are what the
        # grader's wall clock is spent on, not the ticks.
        hot_top = cols * rows_
        # The two banks need not share a worker. They are very different: the hot one
        # is small and answers ~90% of reads, the cold one is 4x larger and answers the
        # rest, so the lap-length/width trade lands differently on each.
        # ``HOT_SKIP_BATCH`` overrides the hot bank alone; ``None`` means "same as the
        # cold bank".
        tier = _PipeBank(
            tape_block(
                hot_top,
                skip_batch=tape_skip_batch if HOT_SKIP_BATCH is None else HOT_SKIP_BATCH,
                relay_size=tape_relay_size,
            ),
            hot_top,
        )
    else:
        if TIER_SIDE_PORTS:
            from ..memory_men_grid_side import grid_side_block as tier_block
        else:
            from ..memory_men_grid_store import grid_block as tier_block

        tier = tier_block(cols, rows_, base=1)
        hot_top = tier.slots  # addresses 1..hot_top are the tier's; the rest are tape's
    if hot_top >= tape_n:
        raise MachineError(
            f"a {cols}x{rows_} tier holds slots 1..{hot_top}, which is the whole "
            f"{tape_n}-slot store; drop the tier or grow the program"
        )
    adapter = two_tier_adapter(hot_top)
    tape = tape_block(
        tape_n,
        skip_batch=tape_skip_batch,
        relay_size=tape_relay_size,
    )
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
    # Slide the bank up until its own ``^`` answer stub ends one cell below L's south
    # wall, so the block's built-in pipe *is* the answer connection and no caller pipe
    # is needed. ``blit`` writes only the cells a block defines and a ``tape_block``'s
    # top rows are empty, so its box may overlap L freely — no *glyph* does.
    gx, gy = aw + 5, cy + _TELE_H + 2 - tier.out_cell[1]
    g.blit(gx, gy, tier.cells)
    hot_out = ay + adapter.hot_row
    tin_x, tin_y = gx + tier.in_cell[0], gy + tier.in_cell[1]
    g.draw_pipe([(aw + 1, hot_out), (aw + 3, hot_out), (aw + 3, tin_y), (tin_x - 1, tin_y)])

    # ── the cold tape, stacked *under* the hot bank ───────────────────────────
    # Side by side, the cold request travelled under both blocks — ~250 cells — and the
    # machine's used width ran to the far edge of the second block. Stacked, the request
    # drops down a free column west of both banks and turns straight in.
    tx, ty = gx, gy + tier.height
    g.blit(tx, ty, tape.cells)
    cold_out = ay + adapter.cold_row
    ttin_x, ttin_y = tx + tape.in_cell[0], ty + tape.in_cell[1]
    drop = aw + 2
    g.draw_pipe([(aw + 1, cold_out), (drop, cold_out), (drop, ttin_y), (ttin_x - 1, ttin_y)])

    # ── two teleports carry both answers home ────────────────────────────────
    # ``R`` receives from *any* incoming pipe in the room and, unlike ``r``, has **no
    # distance term** (SPEC.md "Which pipe do I talk to?"). So a long room is a
    # teleport: a value entering a pipe at its far end is taken by the man at the near
    # end in one instruction, having transited no pipe cells at all. Measured on the
    # engine: the same 66 ticks with the man 1 column from the entry pipe and 31.
    #
    #     L L L L L L L L <  U        L = teleport   (wide, leaves west to the CPU)
    #              ^         U        U = teleport_v (tall, leaves north into L)
    #          [ hot  ]      U
    #          [ cold ]----> U
    #
    # **L** replaces the merger: ``R`` needs no pipe affinity, so one room collects both
    # banks with no ordering logic, then carries the answer the machine's whole width
    # west for nothing. **U** makes the *cold* bank's climb free — without it that climb
    # is an ordinary pipe running the height of the hot bank, and it grows with the cold
    # bank, the taller of the two. A teleport is O(1) in its length.
    from ..memory_men import teleport, teleport_v

    l_wall = cy + _TELE_H + 1
    ux = gx + tier.width
    lx0, lx1 = cx + w + 3, ux - 3

    cold_row = ty + tape.out_cell[1] - 1
    u_rows, _ = teleport_v(cold_row - cy)
    g.room(ux, cy, ux + _TELE_W + 1, cy + len(u_rows) + 1)
    for k, row in enumerate(u_rows):
        g.text(ux + 1, cy + 1 + k, row.replace(" ", "\0"))

    l_rows, _ = teleport(lx1 - lx0 - 1)
    g.room(lx0, cy, lx1, l_wall)
    for k, row in enumerate(l_rows):
        g.text(lx0 + 1, cy + 1 + k, row.replace(" ", "\0"))

    g.draw_pipe([(tx + tape.out_cell[0], cold_row), (ux - 1, cold_row)])
    g.draw_pipe([(ux - 1, cy + 2), (lx1 + 1, cy + 2)])
    g.draw_pipe([(lx0 + 3, l_wall + 1), (lx0 + 3, resp_row), (cx + w + 2, resp_row)])

    regions = {
        "adapter": (ax, ay, adapter.width + 2, adapter.height + 2),
        "teleport:L": (lx0, cy, lx1 - lx0 + 1, _TELE_H + 2),
        "teleport:U": (ux, cy, _TELE_W + 2, len(u_rows) + 2),
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
        # Ten pipes: CPU->adapter, adapter->each bank, hot->L (the block's own stub),
        # cold->U, U->L, L->CPU, and two ring legs per bank. ``touches`` already counts
        # mem_req (CPU->adapter) and mem_resp (L->CPU), hence the ``- 2``.
        pipes=7 + 4 - 2,
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
    doom_loop_row: int | None = None,
    doom_leaf_cols: tuple[int, ...] | None = None,
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
    elif unit == "doom":
        # The DOOM unit owns deadman-3d's 64x48 panel, its column paint loops, and
        # the baked HUD/FLASH patterns; like snake and path it answers nothing, so
        # the CPU keeps its jumps and there is no separate `_display` call.
        from . import d3_unit

        # ``doom_loop_row`` lifts the unit's loop corridor and with it the panel,
        # which is the machine's own floor — see ``DOOM_LOOP_ROW``. None keeps the
        # shipped row, so the canonical artifacts do not move.
        # ``doom_leaf_cols`` re-spaces the decode trie's leaves — the columns
        # lever to ``doom_loop_row``'s rows one. None keeps the shipped pitch.
        blk = d3_unit.build_doom(
            loop_row=d3_unit.R_LOOP if doom_loop_row is None else doom_loop_row,
            leaf_cols=d3_unit.LEAF_COLS if doom_leaf_cols is None else doom_leaf_cols,
        )
    elif unit == "doom4":
        # The tiled wall: four unmodified DOOM blocks behind a 1-of-4 router, so
        # one command lane paints a 128x96 framebuffer across four 64x48 panels
        # (the LM-75's interior is capped at 64x64 — SPEC.md). It presents exactly
        # the `doom` interface: one command pipe in, nothing back.
        #
        # ``doom_loop_row`` reaches all four blocks at once, and the 2x2 stacks
        # two of them — so the lift comes off the wall's height twice over. See
        # ``DOOM_LOOP_ROW``.
        from . import d3_router

        # The **packed** wall: the four panels in one 2x2 cluster with a
        # two-column, three-row gutter, so the thing the machine paints is a
        # contiguous 128x96 screen rather than four monitors 177 columns apart.
        # ``build_wall``'s scattered arrangement stays for the probe and the
        # tests that pin the router's own geometry.
        blk = d3_router.build_packed_wall(loop_row=doom_loop_row, leaf_cols=doom_leaf_cols)
    else:
        from . import stream as streammod

        assert sizes is not None
        a_slots, b_slots, c_slots = sizes
        blk = streammod.build_stream(a_slots=a_slots, b_slots=b_slots, c_slots=c_slots)
    bx, by = 1, wall_y + 5
    if unit in ("doom", "doom4"):
        # The DOOM block is ~172 columns wide — far wider than the CPU — and the
        # grid STORE's man-memory runs hundreds of rows down the machine's east
        # side, so the flat slot just below the CPU is occupied. Hang the block
        # below everything already drawn instead: the command pipe simply grows,
        # and the demo pays footprint in rows, which an ungraded slug never
        # counts. (Doom-only, so every other unit's checked-in grid stays
        # byte-identical.)
        by = max(by, max(y for (_x, y) in g.c) + 3)
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


#: Columns between one relay outlet and the next. Every outlet leaves the *same*
#: wall, so the row term in §7.1's Manhattan distance is identical for all three and
#: cancels: binding here is decided by column alone, and any pitch clear of the arm
#: rows is safe. 12 is far more than needed and costs nothing but relay width.
_RELAY_PITCH = 12

#: The relay's interior, and where its inlet meets the north wall.
_RELAY_W, _RELAY_H = 14 + 2 * _RELAY_PITCH, 13


def _dsp_relay(g: _Grid, cx: int, wall_y: int, in_col: int) -> tuple[dict[str, int], int]:
    """Place `DSP p`'s fan-out room. Returns its three outlet columns and south wall.

    The lane sends two words down one pipe — the selector, then ACC. This room reads
    the selector, subtracts one so the three cases are -1/0/+1 (ROM words are
    non-negative, so the selector cannot carry a sign of its own), branches on it
    three ways, and forwards ACC to the port the selector named.

    Its three ``s`` glyphs sit statically beside their own outlets, which is what
    makes the choice legal at all: a *lane* cannot pick a pipe from an operand
    (§7.1), but a room downstream of the seam can, because the pipe each ``s`` binds
    is still a property of where that glyph sits. Every outlet leaves the *same*
    wall, so the row term in the Manhattan distance is identical for all three and
    cancels — binding here is column-only, and the pitch decides it outright.

    **It is a closed circuit, not a one-shot.** The probe in ``dsprelay.py`` ends
    each arm on ``H`` because it serves a single request; a room that serves every
    display op the program executes must return its man to the read. Built as a
    probe first, this passed every binding check, built clean, and drew nothing —
    the man halted on the first paint and the machine then stalled to the tick cap
    on all fourteen cases. Each arm therefore runs east to a shared riser, down to a
    return corridor, west, and back up into the ``>`` that re-enters the read; the
    spawn joins that same ``>``, so there is exactly one path through the read.

    Outlets run **west to east as DATA, ADDR, SWAP** — ``DSP_BANDS`` order — because
    :func:`_display` routes DATA west round the panel, ADDR straight down into it and
    SWAP east and under, and a westward leg must never cross a column belonging to a
    port further west.
    """
    x0, y0 = cx, wall_y + 4  # three corridor rows, as the panel takes below the CPU
    g.room(x0, y0, x0 + _RELAY_W + 1, y0 + _RELAY_H + 1)
    g.draw_pipe([(in_col, wall_y + 1), (in_col, y0 - 1)])

    ix = x0 + 1
    main, addr_row, swap_row, ret = y0 + 6, y0 + 3, y0 + 9, y0 + 11
    # `@` and the return both enter the `>` at ix+1, so there is one path through the
    # read. A = selector, B = 1, then A = selector - 1 and `X` branches on its sign;
    # the man walks east, so counter-clockwise is north, straight on is east, and
    # clockwise is south.
    g.text(ix, main, "@>rM`1`W-X")
    bx = ix + 9

    ports = {
        Band.DSP_DATA: (main, ix + 12),
        Band.DSP_ADDR: (addr_row, ix + 12 + _RELAY_PITCH),
        Band.DSP_SWAP: (swap_row, ix + 12 + 2 * _RELAY_PITCH),
    }
    east = ix + 13 + 2 * _RELAY_PITCH

    for row, col in ports.values():
        if row != main:  # turn the branch arm out to its own row, then run east
            step = 1 if row > main else -1
            for y in range(main + step, row, step):
                g.put(bx, y, "v" if step == 1 else "^")
            g.put(bx, row, ">")
        g.text(col - 1, row, "rs")  # read the value, send it to this port

    # The return: east to a shared riser, south to the corridor, west, north into the
    # `>`. All three arms share every cell of it, which is why it costs one lane.
    for y in range(addr_row, ret):
        g.put(east, y, "v")
    g.put(east, ret, "<")
    for x in range(ix + 2, east):
        g.put(x, ret, "<")
    g.put(ix + 1, ret, "^")
    for y in range(main + 1, ret):
        g.put(ix + 1, y, "^")

    return {b: c for b, (_r, c) in ports.items()}, y0 + _RELAY_H + 1


def _display(
    g: _Grid,
    cpu: _Cpu,
    cx: int,
    wall_y: int,
    east_limit: int,
    size: tuple[int, int],
    cols: dict[str, int] | None = None,
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
    # Absolute columns of the three ports. Normally the CPU's own lane `s` columns;
    # with `DSP p` folded to one lane they are the *relay's* south-wall outlets, and
    # `wall_y` is the relay's south wall. Everything below is unchanged either way —
    # the relay presents exactly the interface the CPU used to.
    cols = cols if cols is not None else {band: cx + col for band, col in cpu.dsp_cols.items()}
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
    # lambda-deadman is plotter plus one slot: COL (address 11) holds the segment's
    # colour and is live across the whole round, so it can alias nothing. Highest
    # address + 1, as everywhere — an exactly-sized tape stalls, it does not fault.
    "lambda-deadman": 12,
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
    # deadman-3d (the E1M1 raycaster demo) boot-loads 451 data slots (256 packed
    # map quarter-columns for the 64x64 grid, POW16, 16 packed heading words,
    # M5's 64-word nukage bit plane, spawn state, and M7a's 16-monster table +
    # HP block + 60 packed sprite columns), runs the 64-slot per-frame ZBUF
    # after them and 84 scalars after that (the sprite pass's selection slots
    # and paint cursors among them, plus M7b's LIVE/HIT/CID shot scalars), so
    # the highest address is PTR = 599 — see `deadman3d.tape_slots()`, which
    # the tests pin this against. Highest address + 1, as everywhere: an
    # exactly-sized tape stalls silently rather than faulting.
    "deadman-3d": 600,
}

#: Task-level tape choices that beat the compact default on full public-case score.
#: They are deliberately per task: the batch-4 worker saves ticks everywhere but its
#: wider room loses on a width-bound machine. Pathfinder's 177x176 ROM/CPU box hides
#: the whole STORE, and its smaller 6x4 relay ties 8x6 on ticks. Snake's batch-2
#: worker also remains inside the existing 123x113 box; this tunes the tape reference
#: machine, while the submitted ``snake-ring`` coprocessor remains a separate build.
#: Pathfinder-unit hides the same batch-4 worker inside its 153x157 CPU/PATH box.
TASK_TAPE_CONFIG: dict[str, tuple[int, tuple[int, int] | None]] = {
    "pathfinder": (4, (6, 4)),
    "pathfinder-unit": (4, (6, 4)),
    "snake": (2, None),
}

#: Ring capacities per problem: ``(A, B, accumulator)`` in *values*, from the
#: constraint box rather than the public cases. ``matmul`` allows N, M, K <= 16, so
#: A and B hold 256 entries each and a row of C holds 16; one spare value each,
#: because a ring is briefly holding one more than it stores.
STREAM_SIZE: dict[str, tuple[int, int, int]] = {"matmul": (257, 257, 17)}

#: Lane orders that beat ``plan``'s length-descending default, north to south.
#:
#: A lane's row is a **tick** cost: the return walk is ``2 * drop_x - row`` (east to
#: the drop column, south to the collector, west along it), and ``drop_x`` is the
#: running suffix maximum of the lane extents at or below that row. So a hot opcode
#: wants to sit *low* — both terms improve at once — while a *long* one wants to sit
#: high, because everything above it pays for its extent. Length-descending gets the
#: second half right and knows nothing about the first, which is where these come
#: from: weight each lane by how often the opcode actually runs (measured on the
#: emulator over the public cases) and minimise the weighted walk.
#:
#: Every entry was found by search and then **verified on the engine**, keeping only
#: candidates whose footprint did not grow — that filter is not optional, because the
#: order picks ``mem_pad``, which sets the memory lanes' length, which sets the CPU's
#: width, which is squared in the score. Measured, against the same build with the
#: default order:
#:
#: | program | footprint | ticks | score |
#: |---|---|---|---|
#: | `brackets` | 9,025 → 9,025 | 26,000 → 25,111 | **0.966x** |
#: | `gradebook` | 12,996 → **12,769** | 301,571 → 298,571 | **0.973x** |
#: | `matmul` | 8,100 → 8,100 | 120,714 → 118,638 | **0.988x** |
#: | `sudoku-validity` | 6,889 → 6,889 | 434,667 → 432,167 | **0.994x** |
#:
#: `gradebook` also *loses* a column, which is the tell that the default was not on
#: the frontier at all: 114 → 113 is a footprint win the length rule left behind.
#: `tcp` and `snake-ring` were searched the same way and kept their default order —
#: see §7.6. Re-run `scratch/lane_order_search.py` when a program's `.asm` changes,
#: since the weights come from its own execution profile.
#:
#: One trap, because it cost an hour and a wrong conclusion:
#: ``test_lm1_matmul`` pins each case's **exact** settle tick ("the recorded tick is
#: enough, and one tick fewer is not"), so a *faster* grid fails it and the failure
#: reads exactly like a wrong answer. `matmul` was dropped from this table on that
#: evidence and put back after checking the outputs directly — they were correct on
#: all seven public cases, on the reference engine, at every case's new lower tick.
#: When a pinned-tick test fails here, confirm which half of the assertion broke
#: before concluding anything about correctness.
#: `gradebook` was in this table and is **not** any more, which is the clearest
#: example of why the footprint constraint above is a constraint and not a term. Its
#: pinned order was searched when STORE sat 33 columns east of the adapter, so the
#: machine's width was the tape's east edge and the CPU's own width was slack the
#: order could spend. :data:`MEM_PLACE` moved STORE up against the CPU, and the CPU's
#: width is now the *whole* width — so the default order's two narrower columns are
#: two columns off the machine. Measured, both re-swept against the relocated block:
#:
#: | order | box | area2 | avg ticks | score |
#: |---|---|---|---|---|
#: | pinned | 95x93 | 9,025 | 285,036 | 2,572,448,611 |
#: | default | 93x92 | **8,649** | 286,287 | **2,476,096,263** |
#:
#: The tick win the order was bought for survived — it is still 0.4% — and it is now
#: worth a great deal less than the 4.2% of area it costs. Re-run
#: `scratch/lane_order_search.py` under the new geometry before pinning one again;
#: the weights are unchanged but the width constraint it filters on is not.
LANE_ORDER: dict[str, tuple[str, ...]] = {
    # deadman-3d, north to south, weighted by the frame-1 execution profile
    # (LD 5,528 ... NEG 32): a row above the fetch row costs 2 ticks per row of
    # height while every row below it costs a constant, so the coldest lanes
    # take the top, the hot memory lanes sit as close above the fetch row as
    # the response-pipe binding allows, and the hot immediates and the
    # structured lanes take the constant-cost rows below it. (A full
    # bottom-fill into the trie's 11 spare slots was tried and fails binding:
    # it drags the response row down beside the slabs, whose discard `r` must
    # stay nearest the ROM pipe.)
    "deadman-3d": (
        "NEG", "MOVA", "INCM", "ADDI", "MUL", "LDA", "DIV", "SUB", "ADD",
        "ST", "LD", "MODI", "DIVI", "SUBI", "MULI", "LDI",
        "BRN", "BRZ", "JMPF",
    ),
    "brackets": (
        "HALT",
        "LDI",
        "DECM",
        "SUB",
        "ADD",
        "JMPF",
        "LD",
        "ST",
        "MULI",
        "SUBI",
        "DIVI",
        "MODI",
        "BRZ",
    ),
    "matmul": ("MUL", "BRN", "SUB", "ADDI", "ST", "LD"),
    "sudoku-validity": (
        "HALT",
        "STP",
        "ADD",
        "LDP",
        "MULI",
        "ST",
        "MODI",
        "DIVI",
        "SUBI",
        "ADDI",
        "BRZ",
    ),
}

#: ROM fold overrides, where the default heuristic is not the footprint optimum.
#: The default folds the ROM toward the *CPU's own* width, which is right only while
#: the CPU and the tape are the sole things setting the bounding box.
#:
#: Every number here is the minimum over a full fold sweep, and every one is checked
#: against the default in the tests, so a regression in the heuristic is a failure
#: rather than a quietly worse score.
#:
#: All of them were re-swept for the **packed** ROM
#: (``rom.build_packed_rom``), which is the default. Width-bound programs are
#: recorded too: choosing the shallowest useful fold on their footprint plateau
#: produces the tightest MxN box even when ``max(M,N)^2`` is unchanged.
ROM_ROWS = {
    "brackets": 7,  # 89x63
    "palette": 8,  # 84x75
    # Re-swept jointly with :data:`MEM_PLACE`: with the memory stacked under the CPU
    # the width floor is the ROM's own, and the shallowest fold that still fits inside
    # the height the block now costs is the two-row one. 80x80, area2 6,400.
    "tcp": 2,
    # The panel adds rows, so the fold stops when height becomes binding.
    "plotter": 9,  # 103x99
    # The vector-display demo keeps plotter's fold: the inner loop is unchanged and
    # the slug is ungraded, so nobody swept it. Drop the entry if the default wins.
    "lambda-deadman": 9,
    # The unrolled scans make the ROM set the width.
    # Also re-swept against :data:`MEM_PLACE`, and again after `gradebook` left
    # :data:`LANE_ORDER` — the narrower default CPU moves the width/height crossing, so
    # the fold optimum moves with it. 32 rows was the minimum only because everything
    # below it was hidden behind the tape's 107th column. 93x92, area2 8,649.
    "gradebook": 38,
    "sudoku-validity": 25,  # 77x77
    # STREAM keeps its reference-safe west clearance; row five remains the sweep
    # minimum after the other serial routes are shortened.
    "matmul": 5,
    "snake": 9,  # 123x123
    "snake-ring": 6,  # 121x130
    "pathfinder": 73,  # 177x179
    # The command arms are not complete and no checked-in grid exists yet.
    "pathfinder-unit": 72,
    # deadman-3d: re-swept jointly with :data:`STORE_SHAPE` for min max(w, h)
    # (the viewer holds the machine's full bounding rectangle, so squareness is
    # the demo's objective). Historic sweeps in scratch/deadman3d-opt/
    # METRICS.md: 307x307 at the pre-M5 330-slot tape, 335x333 at M5's 395.
    # M7a grew the tape to 597 (monsters + sprites + ZBUF) and the ROM ~980
    # words (the sprite phase, P 2973 -> 3957): the re-sweep (shapes 9..11
    # wide x rom_rows 40..80 x store_dy) moves the optimum to the 10-wide
    # store (the 9-wide block is 67 rows and floors the HEIGHT at ~397, the
    # 11-wide chain floors the width at 391).
    #
    # M7b re-sweep (rom_rows 48..76 x shapes 6..14 x mem_pad 8..47 x store_dy,
    # build-only, then native-gated at the knee). The canonical machine is a
    # pure ROM-width / stack-height trade: width = max(rom_w(rows), 75 + store
    # block width) and height = rows + 3*store_rows + 112, so one more fold row
    # buys ~6 columns and costs exactly one row until the store chain's 363
    # floor takes over at 63 rows. 60 -> 379x376 (max 379); **61 -> 373x377
    # (max 377)**; 62 -> 369x378; 63 -> 363x379. 61 is the crossing, and it is
    # also 0.8% FASTER on the native 9-round gate (46,529,444 vs 46,890,041) —
    # a deeper fold shortens nothing the CPU walks, but it narrows the ROM's own
    # rows. Everything else in the space is dominated: 10 columns is the widest
    # store the 373-wide box holds (11 floors the width at 391) and 600 slots
    # then force 60 rows, so store_h is not a free variable at all.
    "deadman-3d": 61,
    # deadman-3d_hires had no entry at all, and `_packed_fold`'s default is the
    # wrong shape for it by two orders of magnitude: at P=8,895 words it laid the
    # ROM out **68 columns wide and 800 rows tall** — 69% of a 573x1155 machine's
    # height in 12% of its width. Nobody had swept it, because the program grew
    # from P=6,215 to 8,895 in one session (the monster billboards ~2,453 words,
    # the numerals ~227) and the family only just moved to the taped store.
    #
    # The trade is `deadman-3d`'s own, run much further: the machine is
    # width = max(rom_w(rows), wall_w + 1) by height = rows + 304, and the ROM's
    # own width goes as ~49,700/rows (measured: 35 -> 1417x339, 50 -> 996x354,
    # 65 -> 769x369, 80 -> 626x384, 90 -> 573x394, 110 -> 573x414, 150 ->
    # 573x453, 190 -> 573x494, 230 -> 573x533, and the un-folded default ->
    # 573x1155). `Machine.area2` is max(w, h)**2, so the objective is the larger
    # side, and the crossing is wherever the ROM's own width meets the wall's.
    # Against the 572-wide wall that was 90 rows (573x394); the wall then lost
    # its router column band (:data:`d3_router.BLOCK_X0`, 572 -> 495) and the
    # crossing moved with it, exactly as predicted — re-swept at 495:
    #   95 -> 528x399   100 -> 503x404   105 -> 496x409   110 -> 496x414
    # and then the compact tape gate (:data:`TAPED_COMPACT_GATE`) took five rows
    # out of the store block, which moved the crossing again — re-swept a third
    # time against 495 columns and the 224x58 block:
    #  100 -> 503x399   **102 -> 496x401**   104 -> 496x403   105 -> 496x404
    #  110 -> 496x409
    # Below 102 the ROM is wider than the wall; above it every row is a row of
    # pure height. Re-sweep whenever the wall narrows again, the store block
    # changes shape, or the program grows — all three have moved this number
    # already.
    #
    # And a fourth time, after the two knobs of the consolidation pass. They act
    # on opposite sides of the trade and so had to be swept together:
    # :data:`OPCODE_SLOTS` takes 36% out of the drum's opcode cells, which moves
    # the ROM's own width curve *left*, and :data:`DOOM_LOOP_ROW` takes 34 rows
    # out of the wall, which drops the whole height curve by a constant. Neither
    # moves the 496 floor — that is the router wall's — so the crossing is still
    # "the shallowest fold whose ROM is no wider than the wall", just reached
    # sooner. Both on, height = rom_rows + 265:
    #   80 -> 540x345   84 -> 514x349   86 -> 503x351   87 -> 497x352
    #   **88 -> 496x353**   89 -> 496x354   90 -> 496x355   95 -> 496x360
    #  102 -> 496x367
    # 87 misses by one column, so 88 is the crossing and every row past it is
    # pure height. Separately the knobs give 88 -> 496x387 (slots alone, the
    # crossing already at 88) and 102 -> 496x367 (the lift alone, the crossing
    # unmoved at 102); together, 496x353.
    #
    # And the wall then became the **packed** one
    # (:func:`d3_router.build_packed_wall`): the four panels moved out of their
    # blocks into a single 134x103 cluster, which is what makes the machine
    # paint a contiguous 128x96 screen. It cost rows and gave back one column —
    # 495x228 -> 493x305, so the machine is 496x353 -> 494x447 and `area2`
    # 246,016 -> 244,036. 88 still holds, and for the same reason as before: it
    # is the shallowest fold whose ROM is no wider than the wall (87 is 497),
    # and every row past it is now pure height with only 47 rows of slack left
    # rather than 143. Nothing deeper can pay — the 494 is the wall's floor, not
    # the ROM's, so folding further narrows a drum that is already inside it.
    "deadman-3d_hires": 88,
}


#: **ROM-PLUS**: programs whose ROM corridor is widened into a buffer, and how many
#: words of it to ask for. Opt-in per slug — an ordinary ROM keeps the straight
#: corridor, which is two turns and costs nothing.
#:
#: The reasoning, which is `rom.py`'s and `ARCH.md` §5.3's: a jump discards
#: ``2 * ((t - k - 1) mod n)`` words (:func:`rom_words`) — **mod n**, always forward —
#: so a backward edge makes the CPU wait out the rest of the ROM man's lap, and that
#: wait is 20-53% of these programs' ticks. Halving the ROM's cells halved it once
#: already; the man walks 3.46 cells per word on `gradebook` and 2.67 on `tcp`, so a
#: word arrives roughly every three ticks.
#:
#: A pipe is "a FIFO whose capacity equals its length" (``SPEC.md``), so a *long*
#: corridor is a queue of words already in flight. The CPU spends ~47% of its time in
#: the memory lanes, and the ROM man refills that queue throughout — so when a backward
#: jump lands, the discard loop drains a pre-filled buffer at its own speed instead of
#: pacing the man. Nothing else changes: the corridor still attaches to the CPU's west
#: wall on the fetch row, and :func:`check_bindings` measures the *attachment point*,
#: not the route, so a snake binds exactly as the straight run did.
#:
#: This is a buffer, not a ring. A true code ring (the ``LOOP`` room of ``ARCH.md``
#: §3's diagram) would make the CPU re-send every word it reads or the ring drains,
#: which costs an ``s`` per discarded word and gives back much of the win.
#:
#: **Stays empty. Behind a seek drum it is actively harmful, and that is the whole
#: story.** Measured flat on ``brackets``/``tcp``/``gradebook``
#: (``ROM-RECIRCULATION.md``); measured on ``deadman-3d`` — the one slug with a
#: :data:`SEEK_DRUM` — it is a large *loss*, structurally rather than by a tuning
#: miss. ``seekrom``'s protocol makes the CPU **flush the corridor to the ``-1``
#: sentinel** on every seek, so the corridor's length is paid in full by each long
#: jump. A buffer is precisely a longer corridor, so it prices every seek at its
#: own capacity (native/fast engine, taped, the 57-command ``WALK``):
#:
#: | corridor | ≈ of P=4,002 | seek drum | ticks | Δ |
#: |---|---|---|---|---|
#: | 44 (routing accident) | 1/91 | on | 591,485,564 | — |
#: | 130 (shortest buildable) | 1/32 | on | 593,636,532 | **+0.36%** |
#: | 250 | 1/16 | on | 599,111,202 | **+1.29%** |
#: | 500 | 1/8 | on | 611,878,828 | **+3.45%** |
#: | 1,000 | 1/4 | on | 639,137,034 | **+8.06%** |
#: | 1,677 | 2/5 | on | 675,651,202 | **+14.23%** |
#: | 2,000 | 1/2 | on | 690,875,164 | **+16.80%** |
#: | 44 | — | **off** | 653,734,716 | — |
#: | 1,677 | 2/5 | **off** | 629,578,991 | **-3.7%** |
#:
#: **No dip and no optimum** — cost rises from the first buildable row-pair, so a
#: shorter buffer does not rescue it. The curve is *linear* to within 0.5% over a
#: 45x range, slope **50,813 ticks per corridor word**; against 186 long jumps a
#: frame over 57 frames that is **4.79 ticks per word per seek**, i.e. exactly the
#: 4.8 ticks a recirculated word ``ROM-RECIRCULATION.md`` measures. The flush
#: drains at the ordinary discard rate, so a buffer converts discard the drum had
#: *removed* straight back into discard the CPU pays, one word for one word.
#:
#: The last two rows are the control, and they are what makes this a *conflict*
#: rather than a dead feature: on the classic drum the buffer does what it was
#: designed to do and is worth -3.7%; on the seek drum the same corridor costs
#: +14.2%. Canonical at 1,677 is **+30.3%**. Combining it with a wider
#: :data:`SEEK_OPS` is *super*-additive (+17.3%, against +13.8% predicted from the
#: two alone), because each extra split family adds seeks and every seek flushes.
ROM_BUFFER: dict[str, int] = {}


def rom_corridor(
    *, want: int, x_lo: int, x_hi: int, y_top: int, rows: int, fetch_y: int, wall_x: int
) -> list[tuple[int, int]]:
    """Waypoints for a ROM->CPU corridor that buffers ``want`` words.

    ``rows`` boustrophedon rows across ``[x_lo, x_hi]``, then a descent in ``x_lo`` to
    ``fetch_y`` and one leg east into the CPU wall at ``wall_x``. ``rows`` is even so
    the snake ends back in ``x_lo`` with the descent column already under it; an odd
    count would strand it at ``x_hi``, which is over the CPU room.

    **The first cell is always a southward stub at** ``(x_lo, y_top)``, and the snake
    starts on the row below it. A pipe attaches to the room its first arrow points
    *away* from, so a corridor that opened by running east would leave that cell
    pointing along the ROM's bottom wall instead of out of it — the ROM's ``s`` then
    binds nothing and the machine dies ``no-pipe`` in its first few dozen ticks. With
    ``rows=0`` this degenerates to exactly the straight corridor.
    """
    if rows % 2:
        raise MachineError(f"rom corridor needs an even row count, got {rows}")
    pts: list[tuple[int, int]] = [(x_lo, y_top)]
    for i in range(rows):
        y = y_top + 1 + i
        pts += [(x_lo, y), (x_hi, y)] if i % 2 == 0 else [(x_hi, y), (x_lo, y)]
    pts += [(x_lo, fetch_y), (wall_x, fetch_y)]
    return pts


#: Slugs whose ROM corridor may snake across the **whole grid** rather than
#: stopping at the CPU's east wall. The band it lives in runs from the ROM's
#: bottom wall to the CPU's top, and everything that is not the ROM — CPU, tier,
#: adapter, tape, teleports — is placed at or below that top and moves down with
#: it, so the band is machine-wide and empty. Confining the snake to the CPU's
#: ~53 columns costs rows in direct proportion: ``little-little-man`` buffers
#: 3,500 words in 68 rows against 20 at full width, and rows are footprint.
ROM_CORRIDOR_WIDE: set[str] = {"little-little-man"}


def rom_corridor_rows(want: int, span: int) -> int:
    """Even row count whose boustrophedon holds ``want`` words across ``span`` columns.

    Sized from the snake alone and not from the descent it shares with the plain
    corridor, so the corridor comes out at least ``want`` and usually a little over.
    Over-provisioning is the safe direction: the buffer is only ever as useful as it
    is full.
    """
    if span < 2:
        raise MachineError(f"a ROM corridor needs at least 2 columns, got {span}")
    return 2 * -(-want // (2 * span))


#: Slugs that must keep the old, long return path. Letting a simple lane drop early
#: narrows the CPU, and ``matmul``'s STREAM wiring does not survive that: every one of
#: 3,600 (fold, mem_pad, stream_pad) combinations fails to place its pipes, all of them
#: a `v` landing on an occupied cell near the top of the grid. The short path is worth
#: a few percent of ticks here, so this is a deferred fix rather than a dead end --
#: matmul keeps the grid that scored 1,464,201,360.
_LONG_RETURN = {"matmul"}


#: Where the memory subsystem sits, per slug: ``(mem_offset, store_offset)``, both
#: in grid cells relative to the default "adapter east of the CPU, STORE east of the
#: adapter" anchor. Absent means the default, which is right for most programs.
#:
#: The default chains CPU -> adapter -> STORE **west to east**, so the machine's width
#: is ``cpu + 4 + adapter(14) + 1 + store(33)`` whatever else is true of it. On a
#: width-bound machine with height to spare that chain is the score, because only the
#: larger side is squared: `tcp` was 103x57, i.e. 46 wasted rows paying for 103
#: columns. Two rearrangements undo it, and which one wins is set by how much height
#: the program can afford:
#:
#: * **stacked below** (``mem_offset`` south and west, ``store_offset`` zero) moves
#:   adapter and STORE together into the band under the CPU. Width collapses to the
#:   ROM's, height grows by the block's ~34 rows. `tcp` 103x57 -> 80x80, area2
#:   10,609 -> 6,400, and it costs +82 pipe cells, worth +3% ticks — a 38% score win.
#: * **stacked vertically in place** (``mem_offset`` south, ``store_offset`` the same
#:   distance north) leaves STORE beside the CPU and drops the adapter *under* it, so
#:   the fourteen columns the adapter used to occupy in the chain disappear and STORE
#:   slides west against the CPU. Height is unchanged, which is what `gradebook` needs
#:   — it only has 22 spare rows and the block wants 34. 107x85 -> 93x92, area2
#:   11,449 -> 8,649, +58 pipe cells for +5% ticks: a 20% score win.
#:
#: Both are measured, engine-verified on every public case, and re-swept jointly with
#: :data:`ROM_ROWS` — the fold is only free to keep narrowing once STORE has stopped
#: setting the width, which is why those two numbers move together. `matmul` and
#: `sudoku-validity` are deliberately absent: `matmul` is height-bound at 86 rows and
#: `sudoku-validity` is square at 77x77, so for both of them the first row either
#: rearrangement adds is a straight loss.
MEM_PLACE: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "tcp": ((-23, 40), (0, 0)),
    "gradebook": ((0, 26), (-12, -26)),
    # deadman-3d: each row of store_dy shortens the serial adapter->store
    # request route by one cell (~19.7k reads/frame pay it), and each row costs
    # one row of machine height. The M7a machine (10x60 store + rom 60,
    # 374x376) is height-bound with NO slack — every dy row pushes max(w, h)
    # up one — so the M5-era dy 15 goes back to 0; squareness is the demo's
    # objective and outranks the ~0.3M/frame the shorter hot route bought
    # (historic dy sweep at the 42-row fold: dy 0/3/10 ->
    # 11.567M/11.508M/11.371M on the frame gate).
    "deadman-3d": ((0, 0), (0, 0)),
}


#: Per-``(slug, STORE tier)`` layout overrides, for a slug that ships **two**
#: machines off one program. The registry above is written for a slug's
#: canonical tier; a variant tier can have a completely different width/height
#: trade, and forcing it to share the canonical numbers just makes it bigger.
#:
#: ``deadman-3d`` is the case: its men-v3 store is a 288x204 block, so the
#: canonical machine's width floors at 363 and its height at ``rom_rows + 316``
#: — a knife-edge crossing at 61 fold rows. The taped store is 224x59 (four
#: banks), which floors the width 140 columns lower and leaves ~100 rows of
#: height slack, so the taped machine wants a *much* deeper fold and the store
#: pulled west. Sharing ``ROM_ROWS`` costs it 373 against 279.
#:
#: Recognised keys: ``rom_rows``, ``mem_offset``, ``store_offset`` (the
#: :data:`MEM_PLACE` pair, overridable one at a time). Absent ``(slug, tier)``
#: pairs change nothing, so every other machine stays byte-identical.
TIER_LAYOUT: dict[tuple[str, str], dict[str, object]] = {
    # deadman-3d's taped variant, swept jointly (bank plan x store dx x
    # rom_rows) on the native 9-round gate. The store's width is
    # ``48*banks + 32`` at skip-batch 2 and does NOT depend on the bank sizes,
    # so the bank COUNT is the width knob and the sizes stay pure tick tuning.
    # ``store_offset`` dx pulls the block west along the adapter's request
    # route; -20 is the last value that still binds (-21 fails to route), and
    # the fold then deepens until the ROM meets the store chain's floor.
    # 395x231 -> **279x258** (max 395 -> 279, bbox 91,245 -> 71,982).
    ("deadman-3d", "taped"): {"rom_rows": 83, "store_offset": (-20, 0)},
    # deadman-3d_hires: **not** a width/height trade — this machine's box is set
    # by the 496-column wall and nothing the store does moves it. The offset is
    # here for one reason: it is what lets :data:`STORE_REQUEST_REACH` reach.
    # The store's request column is 101 and the adapter's floor spans 81..92, so
    # the two-cell drop has nowhere to start until the block is pulled west; the
    # window is dx -20..-9 and every value in it binds. Which one is chosen does
    # not matter at all — the 21-round tour comes out at 1,085,082,598 ticks
    # **to the tick** at -9, -14 and -20, because with the roof reaching, the
    # only thing crossing the gap is the drop and the rest is translation. -14
    # is the middle, so the drop has five columns of margin either side.
    # ``rom_rows`` is deliberately absent and falls through to ROM_ROWS' 88.
    ("deadman-3d_hires", "taped"): {"store_offset": (-14, 0)},
}


#: Per-``(slug, STORE tier)`` **program** override, as ``"module:callable"``. The
#: callable takes no arguments and returns a :class:`~.asm.Program`; it is imported
#: lazily so this module keeps importing without the solver packages.
#:
#: :data:`TIER_LAYOUT` is this idea one level down — a slug that ships two machines
#: off one source may want different *layout* per tier. This is the same escape
#: hatch for the *source itself*, and it exists for exactly one reason: a slug whose
#: canonical grid is byte-frozen cannot take a program fix that its unfrozen tier
#: can. An explicit ``program=`` argument to :func:`build_for` still wins, so
#: deadman-3d's ``--wad`` mode keeps building the level it just installed.
#:
#: ``deadman-3d``'s entry deletes the DDA x-arm's redundant ``LD WADDR``. ``ST`` is
#: ACC-preserving (``isa.py`` op 8; ``emulator._store`` writes ``em.b`` and never
#: assigns it), so the reload between ``ST WADDR`` and ``LDA`` re-fetched the word
#: the accumulator already held — and a store read is the most expensive thing this
#: machine does. Per-opcode attribution at stride 1 (``scratch/DOOM-OPCODES.md``):
#: 5,536 executions in nine frames at 470.9 ticks each = **4.32% of the run**, the
#: largest single lever the profiler found and the only one that is a program change
#: rather than a layout one. It also drops 32 words of ROM.
#:
#: The canonical men-v3 grid is pinned at ``f62d63fd`` and ``deadman-3d.asm`` is what
#: pins it, so the fix is keyed here rather than applied to the shared source — the
#: same rule :data:`MEM_PAD_FOR`, :data:`INPUT_NORTH_WEST` and
#: :data:`SEEK_TAKEN_DROP_EAST` already follow. The canonical machine would take the
#: same win; it is simply not allowed to move.
#: ``deadman-3d_hires`` wants the same fix and **cannot be given it here**, which
#: is worth stating so nobody adds the key and believes it did something. This
#: registry is read only by :func:`_tier_program`, which :func:`build_for` calls
#: only when no ``program=`` was passed — and ``deadman3d_hires.build_local``
#: always passes one, because its level comes out of an IWAD at call time and
#: there is no checked-in ``.asm`` to load. An entry keyed
#: ``("deadman-3d_hires", "taped")`` would be inert. It is also unnecessary: the
#: registry exists to keep a **byte-frozen** grid off a program fix, and that
#: family commits nothing, so ``deadman3d_hires.hires_source`` simply passes
#: ``dda_acc_reload=False`` itself — worth -4.405% there, against -4.37% here.
TIER_PROGRAM: dict[tuple[str, str], str] = {
    ("deadman-3d", "taped"): "randomfun2026solvers.deadman3d:taped_program",
}


#: Demo slugs are not bound to any problem's panel — an ungraded demo may pick
#: any resolution the LM-75 allows (64x64 max), and ``deadman-3d`` wants DOOM's
#: 4:3 rather than the 32x24 its borrowed ``plotter`` JSON states. Consulted by
#: :func:`display_for` *before* the problem JSON; graded problems must never
#: appear here, because for them the panel size is the judge's, not ours.
DISPLAY_OVERRIDE: dict[str, tuple[int, int]] = {
    "deadman-3d": (64, 48),
    # The *logical* framebuffer. Physically it is four 64x48 panels inside the
    # `doom4` wall (the LM-75 interior stops at 64x64), so the CPU has no display
    # lanes and this number never reaches `_display` — it is here so the machine
    # and its sidecar record what the demo actually renders.
    "deadman-3d_hires": (128, 96),
}

#: Per-slug ``mem_pad`` for machines whose pad the default search should not (or
#: cannot) pick: :func:`build` searches ``range(0, 40)`` and takes the best pad
#: that binds every pipe. ``deadman-3d``'s is recorded to pin the checked-in grid
#: and skip the search. (36 when the CPU owned the panel; 18 under the 32x32
#: program; re-searched to 17 — the smallest that binds under the real
#: INPUT_NORTH + teleport + MEM_PLACE config — after the 64x64 map and the
#: men-v3 store, which tie every pad on footprint. Re-searched again under the
#: 8x42 :data:`STORE_SHAPE` + rom 42 geometry: the full pad search still lands
#: on 17.) Consulted by :func:`build_for` like :data:`ROM_ROWS`; absent slugs
#: keep the search.
MEM_PAD: dict[str, int] = {"deadman-3d": 17}

#: Slugs whose ``I`` room attaches to the CPU's **north** wall instead of the
#: west. On the west wall the input pipe rivals every memory ``r`` a few rows
#: from ``in_row`` (§7.1), which is what forced ``deadman-3d``'s memory band 39
#: columns east — a walk every memory instruction paid twice, out and back
#: along the collector. From the north the pipe's distance to any lane glyph
#: grows with the lane's depth, so the memory band packs west and the pad falls
#: to the search minimum. Opt-in per slug so every other machine's checked-in
#: grid stays byte-identical.
INPUT_NORTH: set[str] = {"deadman-3d", "deadman-3d_hires"}

#: Slugs whose CPU removes the decode trie's **dead leaf rows**: only used
#: opcodes get a lane pair, the trie is re-routed over the compacted band with
#: pruned branches and contracted chains (:func:`_uneven_trie`), and the band
#: shrinks by two rows per unused leaf slot. The opcode numbering — and so the
#: ROM image — is untouched. Costs ``k - 1`` extra trie columns (two per level,
#: for the horizontal ``]`` shifts). Every instruction's decode descent, return
#: drop and riser shorten with the band. Opt-in per slug so every other
#: machine's checked-in grid stays byte-identical.
TRIM_DEAD_LANES: set[str] = {"deadman-3d", "deadman-3d_hires"}  # band 63 -> 41 rows, -13.6% on the gate

#: Per-slug opt-in for the seek-drum (``seekrom``): the ROM keeps its packed
#: fold and its ~3.3 cells a word, but gains per-row ``q``/``d`` gadgets and two
#: ladders, so a **long** taken jump seeks the target's row instead of
#: recirculating every word before it. ``seek_split`` decides per instruction —
#: short jumps keep the counted discard, which they are already good at.
#:
#: Empty by default, so every machine not named here is byte-identical. Measured
#: on ``deadman-3d`` (native, round-gated, frames matched):
#:
#: | build | box | fp | per gameplay frame |
#: |---|---|---|---|
#: | drum (canonical) | 374x376 | 141,376 | 5,826,361 |
#: | seek, JMPF only | 379x382 | 145,924 | **4,916,381 (-15.6%)** |
#: | seek, JMPF+BRZ | 393x382 | 154,449 | 4,946,150 (-15.1%) |
#: | seek, all three | 407x382 | 165,649 | 5,375,802 (-7.7%) |
#:
#: Splitting more than ``JMPF`` is a *loss*: each extra family costs a lane and
#: a 13-column slab, the slab band is what pushes the memory block east, and the
#: pad it forces (17 -> 22 -> 29 -> 36) charges every memory instruction the
#: extra walk twice. Hence :data:`SEEK_OPS` defaults to ``JMPF`` alone.
#:
#: **Re-measured against the M7c layout re-sweep** (native, round-gated on the
#: checked-in 115-frame tour, 116 rounds, both ``passed=True``); the table above
#: is the pre-M7c reading and is kept only for the shape of the effect:
#:
#: | tier | box (classic -> seek) | tour ticks | Δ |
#: |---|---|---|---|
#: | canonical (men-v3) | 372x377 -> **382x382** | 640,512,397 -> **520,564,274** | **-18.7%** |
#: | taped | 279x258 -> **295x269** | 1,250,728,623 -> **1,113,752,187** | **-11.0%** |
#:
#: The canonical machine comes out *exactly square*, five columns under the
#: pre-existing 390 ceiling. The taped tier is the one that pays: its width is
#: store-bound and the seek slabs push the block 16 columns east, which
#: ``store_offset`` cannot claw back (dx -20 is still the last value that
#: routes). It stays inside its 300 ceiling and the 10% skew doctrine, but that
#: is the trade — re-check it before the next taped sweep.
SEEK_DRUM: set[str] = {"deadman-3d"}

#: Per-slug override for :data:`SEEK_OPS`. Absent slugs keep the ``JMPF``-only
#: default, so this is byte-identical for everything not named here.
SEEK_OPS_FOR: dict[str, tuple[str, ...]] = {}

#: ``MEM_PAD``'s replacement while the seek drum is on: the extra lane and slab
#: move the band, so the pinned pad no longer binds. Searched once and recorded
#: here so a seek build stays deterministic (and fast).
#:
#: **The "22 is the floor" claim below is stale for the taped tier** — see
#: :data:`MEM_PAD_FOR`, which re-swept it on the current machine and found 18
#: without moving anything and 16 with :data:`INPUT_NORTH_WEST`. It is left at 22
#: here because the canonical tier's grid is pinned at ``f62d63fd``; the taped tier
#: overrides it by ``(slug, tier)``.
SEEK_MEM_PAD: dict[str, int] = {"deadman-3d": 22}

#: :data:`TIER_LAYOUT`'s overlay while the seek drum is on. Seeking re-shapes
#: the ROM block (a lane and a 13-column slab per split family), so the
#: width/height crossing the classic sweep found is no longer where it was and
#: the fold has to be re-picked per tier. Empty means "keep the classic tier
#: layout"; a key here wins over :data:`TIER_LAYOUT`'s for the same tier.
#:
#: Both folds re-swept build-only against the seek slabs (``mem_pad`` 22 is the
#: floor on both tiers — at 21 and below the classic slabs' ``r`` can no longer
#: beat ``mem_resp`` to the ROM pipe and nothing binds):
#:
#: * canonical, ``rom_rows`` 59/60/61/62/63: 386x381 / **382x382** / 379x383 /
#:   379x385 / 379x386. 60 is the crossing and it lands exactly square, so the
#:   seek build folds one row *shallower* than the classic 61.
#: * taped, ``rom_rows`` 76/78/80/82..96: 304x265 / 299x266 / 295x269 /
#:   295x272 .. 295x286. The width floors at 295 (the store binds, and
#:   ``store_offset`` dx -20 is still the last value that routes), so the fold
#:   stops at the first row that reaches the floor rather than the classic 83.
#:
#: **The taped fold re-swept once the width floor moved.** That sweep's floor of
#: 295 was the answer-path's, not the store's: the STORE teleport L room sat at
#: ``rom_bottom+1..+4`` and its collector reached to 293. :data:`STORE_ANSWER_WEST`
#: deleted the room and :data:`SEEK_SLAB_PITCH` narrowed the slabs, and the floor
#: fell to **287** — ``TX 61 + the block's 224 columns + the east return pipe`` —
#: but ``rom_rows`` was left at 80, which is one row *short* of reaching it. The
#: curve, re-measured build-only and then on the 8-command native gate under the
#: current bank plan:
#:
#: ::
#:
#:     rom_rows   box        ticks (8-cmd gate)
#:     76         304x265    61,698,016
#:     78         299x266    61,613,459
#:     79         292x268    61,714,266
#:     80         289x269    61,799,020   (was shipped)
#:     81         287x271    61,826,043   <- the floor, at the least height
#:     82, 83     287x272    do not RUN (see below)
#:     84         287x274    61,689,668
#:     85..87     287x275/6  do not RUN
#:     88         287x278    61,666,460
#:     92         287x282    61,598,564
#:     96         287x286    61,522,369
#:     100        287x291    (width floored, height now over)
#:
#: 81 is the crossing: every deeper fold buys nothing in width and costs rows.
#: Ticks are flat across the whole range — the entire 76..96 span is 0.5% — so
#: this is a size number and only a size number; the +0.08% at 81 against 80 is
#: inside that band. On the 115-frame tour: 838,732,969 at 80 (289x269) against
#: 839,384,674 at 81 (287x271).
#:
#: **Folds 82, 83, 85, 86 and 87 build but do not run.** At those depths the
#: ROM's packed words land so that a literal's *reverse* reading exceeds 63 bits
#: — a ``` ` ``` pair is readable from either end, so both readings have to be
#: values, and "every value in the language is a signed 64-bit integer"
#: (``littleman/reference/language-reference.txt``). ``fast_littleman`` checks
#: both directions and rejects the grid. It is a property of the fold's word
#: packing, not of anything here, and it is why 84 — 0.2% faster than 81 and
#: equally narrow — is not the pin: it sits in a hole between folds that do not
#: run at all.
#: **The hole moved when the slot map did.** Which folds "do not RUN" is a
#: property of the *packed words*, and :data:`OPCODE_SLOTS` changes the words — so
#: the whole curve above was re-swept against the dispatch-tuned map below. A map
#: with fewer one-digit opcodes is a **wider drum**, and the seek teleport's V room
#: wants a clear column band east of it (:func:`_seek_teleport`), so the shallow
#: folds stop binding: 78..82 fail V outright, and **83 binds the shipped build but
#: not the counterfactual ones** — `tests/test_deadman3d.py` proves several
#: registries load-bearing by rebuilding with one flipped off, and
#: ``TAPED_FEED_TELEPORT=False`` is a wider block than the shipped one.
#: ``scratch/band_root_variants.py`` sweeps every such variant per fold; 84 is the
#: shallowest that binds all of them.
#:
#: The tour ticks across the folds that bind, all round-gated:
#:
#: ::
#:
#:     83   293x255   595,520,499   (shipped build only — a variant fails V)
#:     84   293x257   597,185,956   <- the pin
#:     85   293x257   597,185,956
#:     86   293x259   599,627,097
#:
#: So 83 is 0.28% faster and unbuildable for the suite; the pin costs that and
#: keeps every counterfactual. Re-sweep both numbers together whenever either
#: moves — neither is safe to carry across the other.
SEEK_TIER_LAYOUT: dict[tuple[str, str], dict[str, object]] = {
    ("deadman-3d", "men-v3"): {"rom_rows": 60},
    ("deadman-3d", "taped"): {"rom_rows": 84},
}

#: Per-``(slug, tier)`` **leaf relabelling** for the decode trie: which slot each
#: opcode's lane occupies, north to south. Absent keys keep the contiguous default
#: and every other machine stays byte-identical.
#:
#: **It is not only a ROM-encoding knob, though it was designed as one — see "The
#: second objective" below.** A lane's row is its slot's
#: **rank** under :data:`TRIM_DEAD_LANES`, so a rank-preserving relabelling leaves
#: every row, drop column and lane tick untouched (:func:`plan` enforces that) and
#: only moves ``number = _bitrev(slot, k)``. The drum charges an opcode below ten
#: ``Ns`` = 2 cells and one from ten up ```NN`s`` = 5, and the ten slots that
#: bit-reverse below ten are ``0, 2, 4, 8, 12, 16, 18, 20, 24, 28`` — a spread the
#: default packing (``0..N-1`` plus the pinned tail) cannot aim at. With 22 lanes in
#: 32 slots there are ten spare, and spending them to land the hot opcodes on those
#: ranks is a pure win in cells.
#:
#: DOOM's map is the exact optimum of that assignment (``scratch/rom-opt/slots.py``
#: — a DP over slot x rank against the **static** opcode histogram, which is what
#: the drum's cells and its lap length are both counted in, not the execution
#: profile ``LANE_ORDER`` is weighted by). Measured on the taped tier:
#:
#: | quantity | default | relabelled |
#: |---|---|---|
#: | one-digit opcode words | 610 of 2,152 | **1,401 of 2,152** |
#: | opcode cells | 8,930 | **6,557** (-2,373) |
#: | whole drum, cells/word | 4.626 | **4.075** |
#: | drum data columns | 267 | **236** |
#:
#: **What those cells are worth in ticks is almost nothing, and that is the
#: finding.** The 13% shorter lap moves the 115-frame tour 839,384,674 ->
#: 838,737,298, **-0.077%**; a `+1 blank cell per token` control prices the whole
#: drum at ~0.6% of tour ticks. The taped width floors at 287 on the *store*
#: (``TX 61 + 224 + the east return pipe``), so the drum's 284 was one column
#: under the floor and its 252 is 35 under: what this bought is a **32-column
#: reserve** for whoever narrows the store, not the tick. Full profile, and the
#: three levers costed and rejected beside it, in ``ROM-RECIRCULATION.md``
#: §"The drum's *contents*".
#:
#: **A map is an assignment fitted to one histogram, so it does not travel.**
#: ``deadman-3d_hires``'s entry is its own DP solution over its own program
#: (``scratch/deadman3d-opt/hires_slots.py``), and it agrees with
#: ``deadman-3d``'s on six of twenty-one lanes. It could not have been copied
#: even in principle: hires is not in :data:`SEEK_DRUM`, so there is no
#: ``seek_split`` and no ``JMPS`` lane at all — 21 lanes against 22 — and its
#: rank order is ``plan``'s length-descending default rather than a tuned
#: :data:`LANE_ORDER`, so the *ranks* the DP assigns to differ too. Its
#: histogram is a different shape besides: ``SND`` is 1,251 of 5,116 instructions
#: (a 28-block billboard chain and a numeral painter ``deadman-3d`` has no
#: equivalent of), where the committed program's paint traffic is a fraction of
#: that.
#:
#: | quantity | default | relabelled |
#: |---|---|---|
#: | one-digit opcode instrs | 2,046 of 5,116 | **4,392 of 5,116** |
#: | opcode cells | 19,442 | **12,404** (-7,038, -36%) |
#:
#: What that is worth is **height, via the fold**. hires' width floors at 496 on
#: the router wall (:data:`d3_router.BLOCK_X0`'s 495, plus one), never on the
#: drum, so a narrower drum buys no column directly — it lets a *shallower*
#: ``rom_rows`` reach the same floor, and hires' height is ``rom_rows`` plus a
#: constant. The crossing moves 102 -> **88** and the machine 496x401 ->
#: 496x387 on this knob alone (see :data:`ROM_ROWS`, where the two knobs are
#: swept together).
#: ## The second objective: the map also shapes the **decode trie**
#:
#: The rank-preservation above is what makes this knob safe, and it is also what
#: hid the rest of it: rows do not move, so nothing *looks* like geometry. But
#: :func:`_uneven_trie` splits the **slot space** at each dyadic midpoint, not the
#: used slots, so the slot *values* decide every branch — and therefore
#:
#: * the **zigzag**: the vertical the walk actually travels above the ``|fetch row
#:   - lane row|`` it owes (2,587,216 ticks under the old map, 4.29% of the
#:   profiled run);
#: * the **riser**: the root is the trie's in-order median, so its row is
#:   ``band_y0 + 2u - 1`` for ``u`` slots in ``[0, 16)``, and the riser is
#:   ``collector - root``. ``u = 11`` centred the fetch row; every slot moved west
#:   across 16 walks the root one lane south and takes two ticks off the riser.
#:
#: Writing the loop out shows why both matter and the drop does not: with the
#: collector at ``C`` and the root at ``F``, a lane at ``r`` costs
#: ``2C - 2*min(r, F) - 1 + zigzag(r)`` — below the root the descent and the drop
#: telescope. (That is the same fact :data:`LANE_ORDER`'s comment records as "a
#: row above the fetch row costs 2 ticks per row of height while every row below
#: it costs a constant".)
#:
#: So the map is a two-objective problem and the old one solved half of it. The
#: two are in real tension — the dispatch optimum alone costs 9,098 opcode cells
#: against the drum DP's 6,557 — and pricing both in tour ticks
#: (``scratch/band_root_probe.py``, ``search_joint``) lands between them. Measured
#: on the 116-round tour, every one round-gated:
#:
#: | map | opcode cells | dispatch | fold | box | tour ticks | |
#: |---|---:|---:|---:|---|---:|---:|
#: | drum DP (was shipped) | **6,557** | 12,426,204 | 81 | 293x254 | 609,871,597 | |
#: | dispatch-only optimum | 9,098 | **11,167,796** | 90 | 293x262 | 598,773,720 | -1.820% |
#: | cheapest drum that helps | 6,617 | 11,747,422 | 81 | 293x254 | 602,765,896 | -1.165% |
#: | **joint (shipped)** | 7,631 | **11,167,796** | 84 | 293x257 | **597,185,956** | **-2.080%** |
#:
#: The joint map reaches the *same* dispatch optimum as the dispatch-only one for
#: 1,467 fewer cells, which is what lets it keep a shallow fold — and the fold is
#: worth having: the same drum DP map at fold 88 runs 611,508,114, so seven rows
#: of fold is +0.268% on its own. **Width does not move** (293, the store's floor);
#: height goes 254 -> 257, which under `AGENTS.md` § "optimise ticks, not
#: footprint" is free.
#:
#: What this is *not*: a lane reorder. Rank order is byte-identical to the old
#: map's, so :data:`LANE_ORDER` is untouched and every lane's micro-program, drop
#: column and row are where they were. Only the numbers moved — and, through them,
#: the shape of the tree that reads them.
OPCODE_SLOTS: dict[tuple[str, str], dict[str, int]] = {
    ("deadman-3d", "taped"): {
        "IN": 0, "NEG": 1, "MOVA": 2, "INCM": 3, "ADDI": 4, "MUL": 5,
        "LDA": 6, "DIV": 7, "SUB": 8, "ADD": 11, "ST": 12, "LD": 14,
        "MODI": 15, "DIVI": 16, "SUBI": 18, "MULI": 20, "LDI": 24,
        "JMPS": 25, "BRN": 26, "BRZ": 28, "JMPF": 30, "SND": 31,
    },
    ("deadman-3d_hires", "taped"): {
        "IN": 0, "INCM": 1, "MOVA": 2, "DIV": 3, "ST": 4, "SUB": 5,
        "ADD": 8, "LDA": 9, "MUL": 10, "DIVI": 11, "LD": 12, "MODI": 13,
        "NEG": 14, "SUBI": 16, "ADDI": 17, "MULI": 18, "LDI": 20, "BRN": 21,
        "BRZ": 22, "JMPF": 24, "SND": 28,
    },
}

#: Slugs whose CPU gets a **second return bus above the band**: a simple lane
#: returns over whichever bus is cheaper — the classic drop to the collector
#: below, or an ascent to row 1 and a walk west into a column-1 drop onto the
#: fetch row. Upper lanes otherwise pay the whole band height twice (down to
#: the collector, back up the riser). Costs one row of height and moves every
#: lane down by it, so it must re-prove every pipe binding. Opt-in per slug so
#: every other machine's checked-in grid stays byte-identical.
TOP_RETURN_BUS: set[str] = set()

#: Slugs whose STORE **response** comes home through two teleport rooms (an L
#: above the store's north wall and a U down the CPU's east side) instead of a
#: long pipe. ``R`` has no distance term, so each room is crossed in one
#: instruction and the per-read latency collapses from the pipe's length (~59
#: cells on ``deadman-3d``) to three short stubs (~7 cells) — first-order on a
#: machine making ~15k grid-store reads a frame. Costs two men; opt-in per slug
#: so every other machine's checked-in grid stays byte-identical.
STORE_TELEPORT: set[str] = {"deadman-3d", "deadman-3d_hires"}

#: ``(slug, tier)`` pairs whose STORE **widens its own answer collector** to the
#: CPU's east wall instead of being relayed there by :data:`STORE_TELEPORT`'s two
#: rooms. Wins the same argument the two rooms did, one hop earlier.
#:
#: The taped store already ends in a teleport — one 151-column room merging four
#: banks with ``R``, which has no distance term. This generator then built two
#: more rooms to carry that room's answer to the CPU, so a read crossed **three**
#: forwarders and ten pipe cells to get home. But widening a teleport is free:
#: pulling the collector's own west wall out to ``CX + W + 3`` (the block's west
#: end is empty for these rows) puts the answer beside the CPU with no extra
#: room at all, and a six-cell stub finishes it. One forwarder, seven cells.
#:
#: Measured on the checked-in 115-frame tour, taped tier:
#:
#: * three rooms / 10 cells (shipped): 1,113,752,187
#: * two rooms   / 10 cells:           1,112,107,549  (-0.15%)
#: * **one room / 7 cells (this)**:    see the commit message
#: * zero rooms  / 57 cells:           1,159,488,639  (+4.1%)
#:
#: The last line is why the rooms were not simply deleted: ``R``'s missing
#: distance term is worth ~1M ticks a pipe cell here. The win is not fewer
#: teleports, it is not *relaying between* teleports.
#:
#: men-v3 must stay off, and not for want of trying: its collector is at the
#: block's floor, ~190 rows below ``resp_row``, so widening it west does not
#: shorten anything — the answer still has to climb. Keyed by tier for that
#: reason; absent pairs keep the two-room build byte-identical.
#: ``deadman-3d_hires`` is deliberately **not** here, and it is not an oversight.
#: The collector's west wall is asked for at ``CX + W + 4 - tx_pre``; on hires
#: that is **-18**, because its CPU is narrower (no seek drum, its own
#: ``mem_pad``) while the store block sits at the same adapter gap — so the
#: column the collapse wants is east of the store's own origin and there is no
#: room to widen into. ``deadman-3d`` buys that room with a
#: ``TIER_LAYOUT`` ``store_offset`` of ``(-20, 0)``; swept for hires, ``-18``
#: still gives ``answer_west 0`` (the guard wants >= 1) and ``-19``/``-20``
#: reach the guard but then fail placement outright — "no pad pair makes every
#: pipe bind; collision at (82, 124)" — while ``-24``/``-30`` fail before that.
#: The collapse therefore needs store-placement surgery on this machine, not a
#: registry key, and the entry is left off rather than added dead. hires keeps
#: its `STORE_TELEPORT` room pair, which is what `build_for` falls back to.
#: **Re-swept after the consolidation pass** (fold 102 -> 88, the wall 34 rows
#: shorter): identical failures at every offset — ``-18`` still ``answer_west
#: 0``, ``-19``/``-20``/``-24``/``-30`` still "no pad pair makes every pipe
#: bind". Neither knob touches the CPU's width or the adapter gap, so the
#: column arithmetic never moved, which is the expected result and now a
#: measured one.
#:
#: **Re-measured a third time against the shipped hi-res geometry** — the first
#: two sweeps predate hires having a :data:`TIER_LAYOUT` ``store_offset`` at
#: all, and :data:`STORE_REQUEST_REACH` now gives it one, which is exactly the
#: westward move the collapse wanted. The two windows do intersect, and only
#: just: the collector's wall is ``-18 - store_dx`` and the guard wants ``>= 1``
#: (so ``dx <= -19``), while the roof needs the request column inside the
#: adapter's floor (so ``dx in -20..-9``). **dx -19 and -20 satisfy both**, and
#: the collapse still cannot be placed. The constraint is no longer the one the
#: note above records, and it is worth naming because it is structural:
#:
#: 1. At the shipped ``answer_west``, all **80** attempts (40 ``mem_pad`` x roof
#:    on/off) fail at **row 107** — the two-tier adapter's own top row. The
#:    straight ``store->cpu`` leg assumes the collector's exit is *below*
#:    ``resp_row``; on hires it is above, so the same two corners describe a
#:    climb, and the climb crosses the adapter. The row never moves because it
#:    is a CPU coordinate.
#: 2. Routing that leg around the adapter instead (the instrument the
#:    ``compact or moved`` branch already uses) clears row 107 and exposes the
#:    real one: hires' ``resp_row`` sits **inside** the widened collector's own
#:    row band, so the collector's west wall lands precisely on the cell the
#:    response pipe must occupy to enter the CPU from the east.
#: 3. Parking that wall further east frees the approach cell and leaves **no
#:    free route at all** — verified with the search box opened to the whole
#:    grid, so it is the machine and not the box. The attachment is enclosed by
#:    the CPU to the west, the collector to the east and the adapter below, and
#:    ``_keepout``'s halo forbids running alongside any of them.
#:
#: So the room that cannot be absorbed is neither of the two the collapse
#: deletes — it is the **collector itself**, which on this machine cannot be
#: both beside the CPU and clear of its response row. `deadman-3d_hires` keeps
#: its `STORE_TELEPORT` pair. Probes: ``scratch/deadman3d-opt/hires_answer.py``
#: and ``hires_answer_pads.py``; arithmetic in ``METRICS.md`` H2.
STORE_ANSWER_WEST: set[tuple[str, str]] = {("deadman-3d", "taped")}

#: ``(slug, tier)`` pairs whose **seek request** reaches the drum through two teleport
#: rooms instead of one pipe around the outside of the machine. See
#: :func:`_seek_teleport` for the geometry; the registry exists so every other
#: machine's checked-in grid stays byte-identical. Costs two men.
#:
#: The plain route is the longest pipe on the machine by seven times — 437 cells on
#: ``deadman-3d`` taped against 58 for the next one — and every cell of it is latency
#: on the drum station's ``r``. The teleported route is **53**.
#:
#: The arithmetic, measured on the 116-round tour with the native engine:
#:
#: * the derivative is **8,063 tour ticks a pipe cell** (+100 cells of pad:
#:   838,511,442 -> 839,317,714), i.e. ~70 taken seeks a frame, one tick a cell each;
#: * so the whole 437-cell pipe is worth ~3.5M ticks, **0.42% of the tour**, and
#:   collapsing it to 53 is worth ~3.1M.
#:
#: That ceiling is set by the seek *rate*, not the pipe: this is a rare, fully
#: serialised event (~70 a frame) where the store's teleported answer was a common
#: one (~15k reads a frame). A seven-times-longer pipe on a two-hundred-times-rarer
#: event is worth less, not more — which is the whole reason to measure the
#: derivative before building.
SEEK_TELEPORT: set[tuple[str, str]] = {("deadman-3d", "taped")}

#: Slugs whose **seek jump** turns south at the east end of its entry row instead of
#: at its slab's ``base``, deleting the U-turn under the CPU.
#:
#: A taken ``JMPS`` used to arrive at its entry row on the lane's own drop column,
#: walk *west* across the whole slab band to ``slab_base``, drop to the taken row,
#: and then walk straight back *east* to the ``s`` — 15 west and 13 east on
#: ``deadman-3d``. The west leg looks like it must be holding something, and for a
#: *branch* slab it would be: ``base`` is where the ``X`` fan-out and its three arm
#: rows start. A seek jump has no slab body at all — the whole "slab" is one ``v``
#: and a column of dots — so ``base`` is inherited convention and nothing else.
#: Nothing binds to it either: the only ``r``/``s`` glyphs in the seek tail are the
#: ``s`` at ``e_s`` and the flush ``r``\ s in columns 3..4, and none of them move.
#:
#: The new column is ``struct_east + 1`` = ``e_s - 1``, the last one that still
#: lands west of the ``s``, clamped to the lane's own drop column. Being east of
#: ``struct_east`` it is east of every slab body by construction, so the drop
#: crosses nobody on the way down; the generator asserts the column is clear
#: anyway.
#:
#: Worth ``2 * (e_s - 1 - base)`` = **24 ticks a taken JMPS**. Measured on the
#: 116-round tour, native, taped tier: see the commit message. Keyed by
#: ``(slug, tier)`` — the CPU band is identical on both deadman-3d tiers, so the
#: canonical machine would take the same win, but its grid is pinned at
#: ``f62d63fd`` and only the taped family is allowed to move.
SEEK_TAKEN_DROP_EAST: set[tuple[str, str]] = {("deadman-3d", "taped")}

#: How many columns west of ``lane_x0`` an :data:`INPUT_NORTH` I room sits, per
#: ``(slug, tier)``. Absent (0) keeps the shipped column, which is ``lane_x0``
#: itself — the pipe drops onto the IN lane's own ``r``.
#:
#: **The move is worth exactly zero on its own, and that is measured**: the pipe is
#: two cells wherever the room goes, the box is store-bound at 287x253, and the
#: 116-round tour is *bit-identical* at 837,925,922 with the room at x=21 and at
#: x=8. Nothing about the room's column is geometric.
#:
#: What it is worth is :data:`MEM_PAD_FOR`. The input pipe is one of the three
#: rivals every memory ``r`` in the CPU is checked against, and pulling the memory
#: band west (a smaller ``mem_pad``) walks those ``r``\ s *toward* it. With the room
#: at ``lane_x0`` the pad floors at 18 — ``'r' at (41,103) must bind 'mem_resp' but
#: 'in' is 25`` — and with the room at the CPU's north-west corner the rival becomes
#: the ROM pipe instead and the floor falls to **16**. Two columns of memory band,
#: bought by moving a 3x3 room thirteen columns west.
#:
#: The westernmost legal column is ``CX + 1``: the room's own west wall then shares
#: the CPU's west wall column (they are on different rows) and the pipe still lands
#: on a north-wall cell rather than the corner.
INPUT_NORTH_WEST: dict[tuple[str, str], int] = {("deadman-3d", "taped"): 13}

#: Per-``(slug, tier)`` ``mem_pad``, overriding :data:`MEM_PAD` /
#: :data:`SEEK_MEM_PAD`. The pad is how far east of the trie the memory band starts;
#: every column of it is paid twice by every memory instruction's lane walk.
#:
#: :data:`SEEK_MEM_PAD`'s 22 was recorded as the binding floor ("at 21 and below the
#: classic slabs' ``r`` can no longer beat ``mem_resp`` to the ROM pipe"). That note
#: is **stale** — ``SEEK_SLAB_PITCH``, :data:`STORE_ANSWER_WEST` and M10 all moved
#: the glyphs it was measured against. Re-swept on the current taped machine:
#:
#: | ``mem_pad`` | I room at x=21 | I room at x=8 (:data:`INPUT_NORTH_WEST`) |
#: |---|---|---|
#: | 22 (was shipped) | builds — 837,925,922 | builds |
#: | 18 | builds — **827,599,542** (-1.23%) | builds |
#: | 17 | binds against ``in`` | builds |
#: | 16 | binds against ``in`` | builds — **822,436,488** (-1.85%) |
#: | 15 | binds against ``in`` | binds against ``rom`` |
#:
#: 16 is the floor with the room moved, and the rival there is the ROM pipe, which
#: the room's column cannot help with. Keyed by tier so the canonical machine, whose
#: grid is pinned at ``f62d63fd``, keeps ``SEEK_MEM_PAD``'s 22 untouched.
MEM_PAD_FOR: dict[tuple[str, str], int] = {("deadman-3d", "taped"): 16}
#: ``(slug, tier)`` pairs whose **request** leg crosses a room instead of a pipe
#: — the mirror image of :data:`STORE_ANSWER_WEST`, on the leg that was left.
#:
#: A stride-1 heatmap and exact pipe counters (native ``fast_littleman``, gated
#: nine-round run, 61,555,215 ticks) put the CPU **blocked on the store answer
#: for 47.19% of the whole run**: 87,490 reads at 332 ticks each, standing on
#: the memory lanes' ``r``. 12,443,858 of those blocked ticks — 20.22% of the
#: run — is *pure pipe transit*, and the single biggest term in it is this leg:
#: ``adapter->store`` is 60 parsed cells and **every** access pays all of them,
#: 5,249,400 ticks = 8.53%. It is the only pipe on the critical path that no
#: access avoids.
#:
#: Nothing about the store is faster afterwards; the request simply stops
#: walking. A tall teleport hangs in the corridor between the adapter's floor
#: and the first gate's roof (empty for its whole height in the checked-in
#: grid), the request leaves the adapter's **south** wall instead of its east
#: one, and 58 drawn cells become **six**: two down into the room's roof and
#: four out of its floor onto the gate's own stub. The room is crossed in one
#: instruction because ``R`` has no distance term (``SPEC.md`` §Nearest).
#:
#: Two things this deliberately does **not** do, both of them measured on the
#: answer path first: it does not delete a forwarder in favour of a plain pipe
#: (that cost +4.14% there), and it does not build a relay chain (one forwarder
#: plus a short pipe beat three forwarders). One room, two stubs.
#:
#: Measured on the checked-in 116-round tour, native engine, round-gated —
#: **838,511,442 -> 788,880,295, -5.92%**, box unmoved at 287x253. The estimate
#: was 8.53% and the gap is accounted for rather than shrugged at (arithmetic in
#: ``scratch/deadman3d-opt/METRICS.md`` M12). Padding this leg gives it a
#: derivative of **1,060,929 tour ticks a pipe cell**, so the whole 60 cells
#: were worth 7.59%, not 8.53 — some request transit overlaps the ring's seek.
#: Of the 52 cells removed, 46.8 came back; the ~5.2 that did not are the
#: forwarder's own six-cell loop. A pipe is a 60-deep FIFO and pipelined the
#: request's two words one tick apart, where one man re-serialises them at one
#: per six ticks. Six is the shortest cycle that holds both an ``R`` and an
#: ``s``, and a second man would reorder the words, so that is the floor here.
#:
#: Keyed by tier: only the taped tier has a gate chain for the request to reach,
#: and the room's south wall is placed off the gate strip's own entry row. The
#: hi-res family is left off for the same reason it is off ``STORE_ANSWER_WEST``
#: — see that note; absent pairs keep every existing grid byte-identical.
#: Superseded for ``deadman-3d`` by :data:`STORE_REQUEST_REACH`, which does the
#: same job with no room at all; the branch stays because it is the only form
#: available to a store whose first room cannot grow (men-v3's cannot — its
#: request stub is mid-block) and because withholding it is how the tests state
#: what a forwarder is worth. Empty by default: absent pairs keep every existing
#: grid byte-identical.
#: **Declined for ``deadman-3d_hires`` on the measurement, not on principle.**
#: Once its store is pulled west far enough for either form to bind
#: (:data:`TIER_LAYOUT`), both do — and the roof wins: -0.469% against this
#: room's **-0.362%** on the same 21-round tour at the same offset
#: (1,085,082,598 vs 1,086,250,847 ticks over frames 1..20, against a base of
#: 1,090,194,166). The 0.107pp gap is the forwarder's own six-tick
#: re-serialisation, which is exactly what :data:`STORE_REQUEST_REACH` exists to
#: stop paying — so the supersession is measured on this machine rather than
#: carried over from ``deadman-3d``.
STORE_REQUEST_TELEPORT: set[tuple[str, str]] = set()

#: ``(slug, tier)`` pairs whose STORE grows its **first gate's room** north until
#: the west wall stands under the adapter's floor, and takes the request as a
#: two-cell drop. Supersedes :data:`STORE_REQUEST_TELEPORT` for the same key —
#: setting both is a build error, because the room and the reach are two answers
#: to one question.
#:
#: The forwarder this replaces was already worth -5.92% (M12). What is left to
#: take is the forwarder *itself*: a pipe is a FIFO and pipelines the request's
#: two or three words one tick apart, where one man on a six-cell ``@>Rv``/``^s<``
#: cycle re-serialises them at one word per six ticks — ~5.2 cells' worth, ~0.66%
#: — plus the four cells of out-stub the room's south wall forced. A grown gate
#: pays neither: ``U`` receives from any incoming pipe with **no distance term**
#: (``SPEC.md`` §Nearest), and it turns away from the **wall** the pipe attaches
#: to rather than from the direction the pipe comes from. That last clause is the
#: whole permission slip and it was measured, not read: gate 0's room pulled 30
#: rows north with the request fed 33 rows above its man reads all 600 addresses
#: of the real plan back correctly (``scratch/deadman3d-opt/probe_gate_grow.py``).
#: ``deadman-3d_hires`` takes it too, but it had to be *made* reachable: its
#: store's request column is 101 against an adapter floor of 81..92, so out of
#: the box the build fails outright ("the drop has nowhere to start") rather
#: than merely costing more. A :data:`TIER_LAYOUT` ``store_offset`` of
#: ``(-14, 0)`` pulls the block into the window — see that entry for why the
#: exact value is arbitrary. **-0.469% net** on the 21-round hi-res tour
#: (1,090,194,166 -> 1,085,082,598 over frames 1..20), which is the roof's own
#: -0.550% less the +0.081% the offset costs on its own. Not the -1.478%
#: ``deadman-3d`` got, and the reason is the same one that shrinks every store
#: lever on this machine: at 128x96 the frame is four times the pixels but the
#: store is the same store, so a request leg is a smaller share of the frame.
STORE_REQUEST_REACH: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs whose taped STORE grows every gate after the first
#: **west** until its wall stands beside the previous gate's, so the chain link
#: is a hop over the intervening feed riser instead of a run under a whole bank.
#:
#: Same mechanism as :data:`STORE_REQUEST_REACH` and the same permission slip; it
#: is worth stating separately because it is worth more. The link is **25 cells
#: down to 7**, and unlike the arms below it every one of those cells is on the
#: critical path of *most* accesses: ``A > 0`` means "not mine, pass downstream",
#: so a request for the bank at chain position ``k`` walks links 0..k-1 first,
#: and with :data:`TAPED_BANK_ORDER`'s ``(3, 2, 0, 1)`` over
#: :data:`TAPED_BANKS`' ``(352, 164, 15, 69)`` the first link alone carries 68%
#: of the reads and the second 12%.
#:
#: What it deliberately does **not** touch is the ``reqK->bankK`` arms
#: (45/45/44/97 cells). Those want the gate to reach its *callee*, and it cannot:
#: the two outgoing pipes share the east wall, ``s`` takes the **nearest**, and
#: the tightest of the gate's eight ``s`` glyphs has three cells of margin
#: between the local pipe and the downstream one. Move the local attachment more
#: than four rows off the body and the north arms bind to the downstream pipe —
#: reads land in the wrong bank with no error at all. Arithmetic in
#: ``scratch/deadman3d-opt/METRICS.md`` M13.
#: **Declined for ``deadman-3d_hires``, and its own bank order is the reason.**
#: This lever is worth what the chain *links* carry, and hires carries almost
#: nothing on them: :data:`TAPED_BANK_ORDER`'s ``(3, 0, 1, 2)`` puts the bank
#: holding 90.79% of reads and 99.85% of writes at chain position **0**, which
#: walks zero links — 0.13 gates a read against ``deadman-3d``'s 1.15. So where
#: link 0 carries 68% of that machine's accesses it carries ~4% of this one's,
#: and the measurement follows: **-0.020%** on the 21-round hi-res tour
#: (1,090,194,166 -> 1,089,980,434 over frames 1..20), against -1.950% there.
#: It is free in box terms — 500x348 either way — and it is still not taken: a
#: two-hundred-thousandth of a run does not buy pinning three gate rooms into a
#: grown form that the next store change would have to work around. On top of
#: the shipped hi-res set it is smaller still, -178,101 ticks = -0.017%.
TAPED_CHAIN_REACH: set[tuple[str, str]] = {("deadman-3d", "taped")}

#: ``(slug, tier)`` pairs whose taped STORE puts a **vertical forwarder** on every
#: ``reqK->bankK`` arm — the legs a grown gate room provably cannot take, because
#: they run to the gate's *callee* (see :data:`TAPED_CHAIN_REACH`).
#:
#: They are the block's largest remaining term: 45, 45, 44 and 97 cells, and
#: every access walks exactly one of them, so weighted by
#: :data:`TAPED_BANK_ORDER`'s traffic they are ~46.5 accesses-weighted cells
#: against a measured 1,112,500 tour ticks each. A one-in/one-out room has none of
#: the gate's binding problem — ``R`` takes from any incoming pipe and ``s`` has a
#: single outgoing one — so this is the M12 lever again, on four legs at once.
#:
#: It costs a man a bank (census 20 -> 24 against the visualizer's own ceiling of
#: 30) and two columns of pitch: the room is ``memory_men.teleport_v`` in the
#: corridor between two banks, which is six columns wide and had four. Absent
#: pairs keep every existing grid byte-identical.
#: ``deadman-3d_hires`` takes it, at a tenth of the value and for the mirror
#: image of the reason :data:`TAPED_CHAIN_REACH` is declined there. Its bank
#: order puts the hot bank at chain position 0, so ~91% of accesses walk
#: ``req0->bank0`` and *only* that arm — which is the one this lever shortens
#: most and the chain lever does not touch at all. **-0.286%** on the 21-round
#: hi-res tour (1,090,194,166 -> 1,087,081,434 over frames 1..20) against
#: -4.194% on ``deadman-3d``; the arm is the same ~45 cells but a 128x96 frame
#: is four times the work between two store accesses, so any one leg is a
#: quarter of the share. The two columns of extra pitch cost nothing here: this
#: machine's width is the 496-column wall's, not the store's, at 500x348 either
#: way.
TAPED_FEED_TELEPORT: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs whose taped STORE builds its gates from the
#: **spacer-free** body (``memory_taped.COMPACT_GATE_H``) instead of the shipped
#: 12-row one. Keyed by tier because only the taped tier has gates at all;
#: absent pairs keep the 12-row build byte-identical.
#:
#: The gate inherited its 12 rows from the two-tier adapter, and five of them
#: are lone ``.`` nops the man simply walks across. Deleting them is not mainly a
#: smaller room — it is **ticks**, because the request walks every one of those
#: cells, and it is ticks *per gate in the chain*: ``A > 0`` means "not mine,
#: pass downstream", so a request for bank ``k`` walks the south path of all
#: ``k`` gates ahead of it before it walks its own arm. Cells off the walk, by
#: arm (counted by walking the grid, not derived):
#:
#: ::
#:
#:     arm                        request U->s     full loop U->U
#:     north read  (local read)   22 -> 19  (3)    70 -> 60  (10)
#:     north write (local write)  22 -> 20  (2)    66 -> 58  (8)
#:     south read  (pass down)    25 -> 23  (2)    60 -> 56  (4)
#:     south write (pass down)    25 -> 24  (1)    60 -> 56  (4)
#:
#: The request column is latency the CPU waits on outright; the loop column is
#: the gate man's recovery, which only shows when he, not the ring, is what the
#: next access waits for.
#:
#: **The chain is where the money is, because DOOM's traffic is lopsided.**
#: Profiled on the wire (``0 addr`` / ``1 addr value``) over the checked-in
#: 115-frame tour: 10,118 reads and 3,315 writes a frame, and with
#: :data:`TAPED_BANKS`' ``(256, 195, 64, 85)``
#:
#: * bank 3 (516..600, the per-frame scalars) takes **88.5%** of reads and
#:   **97.9%** of writes — and it is the bank with *no gate of its own*, so it
#:   is reached only by passing all three gates' south arms;
#: * bank 0 (the 256 map words) takes 7.7%, bank 1 3.7%, bank 2 (ZBUF) 0.1%.
#:
#: Average gates traversed: **2.81** a read, **3.00** a write. So the average
#: read sheds 5.73 cells and the average write 3.02 — 68.0k ticks a frame on the
#: request path alone.
#:
#: Measured on the checked-in 115-frame tour, native engine, all frames clean:
#:
#: * 12-row gate (shipped): 1,113,752,187   295x269
#: * **7-row gate (this)**:  1,102,849,373   295x269   **-0.98%**
#:
#: That is -10,902,814 ticks, **-94,807 a frame** — more than the 68.0k the
#: request path alone predicts, so ~10% of the return-leg saving is on the
#: critical path too: with the hot bank an 85-slot ring, the gate men really are
#: part of what the next access waits for.
#:
#: **Size did not move, and was never going to.** The block loses its five rows
#: (224x63 -> 224x58) but it sits at rows 97..154 of a machine whose floor is the
#: display/stream panel at rows 217..266. 295x269 both ways.
#:
#: The follow-up this measurement pointed at — the hot bank is *last*, so 88.5%
#: of reads pay three gate traversals to reach the cheapest ring — is
#: :data:`TAPED_BANK_ORDER`, and it is worth an order of magnitude more. The two
#: do not add; see that entry.
TAPED_COMPACT_GATE: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    # Same tier, same gate chain, and the spacer rows are a property of the gate
    # room rather than of the program: the store block goes 224x63 -> 224x58 for
    # hires exactly as it does for `deadman-3d`, and those five rows come
    # straight off the machine's height (see `ROM_ROWS["deadman-3d_hires"]`,
    # whose fold is chosen against that height).
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` -> the DOOM unit's loop-corridor row (``d3_unit.R_LOOP``,
#: shipped 27). Absent means "keep the shipped row", which is what holds
#: ``deadman-3d``, ``deadman-3d_trim`` and ``deadman-3d_hires`` byte-identical.
#:
#: **The unit's height is the machine's floor, and seventeen rows of it were
#: doing nothing.**
#: The DOOM block hangs below everything (``rom`` rows 0..93, CPU/tape 94..168,
#: the block 170..270), so the machine's last row *is* the block's, and the
#: block's height is ``R_ADDR + PANEL_H + 6`` — the panel hangs two rows below
#: ADDR's band row and the SWAP under-run three below the panel. The unit's own
#: interior bottom (``R_COLLECT``) is 16 rows clear of that, so it is ADDR, and
#: only ADDR, that sets the floor.
#:
#: Every row below the loop corridor is a fixed offset from it
#: (``d3_unit.BELOW_LOOP``) because the two counted-loop bodies are rigid
#: ladders and the band rows are where their ``r``/``s`` glyphs land. So the
#: whole lower half translates as one piece, every ``_send_band`` decision (a
#: comparison of row *differences*) is invariant, and all four pipe lengths —
#: which depend on ``ADDR-DATA`` and ``ADDR-SWAP``, not on ADDR — are unchanged.
#: Rows 19..26 of the shipped map hold no cell at all, and rows 10..18 come free
#: once RUN's ``>`` steps into a climb column of its own — COL already does this,
#: which is why COL's leaf column may carry machinery on the corridor row and
#: RUN's could not. Below 10 the limit stops being a collision and becomes rule 1:
#: COL's seed push sits at a fixed row 20, *above* the corridor, and must stay
#: nearer the ring band (``loop+3``) than ADDR (``loop+19``); their midpoint is
#: ``loop+11``. 9 is the reading-order tie the builder refuses, 8 and below would
#: send the wall seed to the panel.
#:
#: Measured. Block 235x101 -> 235x84 at ``loop_row`` 10; pipe lengths (addr 15,
#: data 15, swap 35) and binding margins (min 2) are identical at every value in
#: 10..27, and the standalone probe — every arm, a negative-seed COL, both
#: sprites, the banding masks — passes pixel-for-pixel on the native engine at
#: all eighteen, in 44,054 steps at 10 against 45,447 at 27 (the arms' descents
#: to the corridor are shorter, so the lift is very slightly *cheaper* too).
#:
#: The taped machine then goes **287x271 -> 287x254**, 839,384,674 ->
#: 839,158,874 ticks on the 116-round tour (-0.03%, i.e. flat). The width is the
#: taped store's floor (see :data:`SEEK_TIER_LAYOUT`) and the unit never reached
#: it — the block's east edge is column 235 — so this is height and only height.
#: It is banked height as well: ``rom_rows`` 81 is the shallowest fold that
#: reaches the 287 floor, and the seventeen rows come off the total that fold has
#: to fit under.
#:
#: **``deadman-3d_hires`` collects it twice.** Its wall is four blocks in a 2x2
#: (``d3_router.build_wall``), and the wall's height — which is what adds to the
#: machine's, the wall hanging below everything — is *two* block heights plus the
#: router and the gaps. So one lift of seventeen rows comes off it at both
#: levels: measured, 496x401 -> **496x367** at the same fold, exactly 34 rows,
#: and the sweep 27, 20, 15, 12, 11, 10 -> 401, 387, 377, 371, 369, 367 is 2 rows
#: per row of corridor throughout, with no plateau and no floor of its own.
#: :data:`d3_unit.MIN_LOOP_ROW` is still the binding constraint — 9 raises
#: ``DoomUnitError`` on COL's seed push, unchanged by the tiling, because
#: ``build_wall`` hands the same row to four *unmodified* blocks and each one
#: re-checks it.
#:
#: The lift and :data:`OPCODE_SLOTS` are independent (one is the wall's height,
#: the other the drum's width) and they compose exactly: together the fold
#: crosses at 88 and the machine is **496x353**, 48 rows off 496x401.
DOOM_LOOP_ROW: dict[tuple[str, str], int] = {
    ("deadman-3d", "taped"): 10,
    ("deadman-3d_hires", "taped"): 10,
}

#: ``(slug, tier)`` pairs whose DOOM unit lays its six arms at their **own**
#: widths instead of the uniform :data:`d3_unit.LEAF_PITCH`. Absent means the
#: shipped pitch, so the canonical artifacts do not move.
#:
#: This is :data:`DOOM_LOOP_ROW`'s lever turned ninety degrees, and it buys the
#: same thing: **walk**. Dispatch is one man from MAIN east to the trie root,
#: across three trie rows to his leaf, down the arm, and the whole way back west
#: along the collector row — roughly ``2 x leaf_column``, paid on every command.
#: The uniform pitch was set by the widest arm (GUNF's 33-column sprite chain)
#: and three of the six arms occupy one column, so 40 of 156 interior columns
#: were dead and the trie walked straight through both gaps. Re-spacing the
#: leaves takes the interior from 156 columns to 92 and the block from 235x101
#: to 171x101, and it moves **no code**: :func:`d3_unit.arm_codes` reads them off
#: the leaves' rank, not their columns.
#:
#: What it does not do is reorder them. The traffic mix is savagely lopsided the
#: wrong way — RUN 46.6%, COL 34.9%, CURS 17.4% against COMMIT 0.55%, GUN 0.48%,
#: GUNF 0.07%, and the three hot arms are the three easternmost — but COL is
#: pinned to leaf 7 (code 0, so the CPU's per-column send is a bare ``MULI 8``)
#: and RUN to an eastern leaf (rule 2: at leaf 1 its ``r`` binds the ``cmd``
#: pipe instead of the ring and the arm reads the wrong word). The gaps are
#: free; the order is not.
#:
#: Spelled out rather than imported — ``d3_unit`` reaches back into this module
#: for ``_Grid``, so the import only ever goes one way — and pinned against
#: :data:`d3_unit.COMPACT_LEAF_COLS` in ``tests/test_deadman3d.py``.
DOOM_LEAF_COLS: dict[tuple[str, str], tuple[int, ...]] = {
    ("deadman-3d", "taped"): (3, 7, 27, 33, 37, 41, 73, 79),
    ("deadman-3d_hires", "taped"): (3, 7, 27, 33, 37, 41, 73, 79),
}

#: ``(slug, tier)`` pairs whose taped STORE visits its banks in a **chain order**
#: different from :data:`TAPED_BANKS`' address order. The value is a permutation
#: of the bank indices; :func:`memory_taped.gate_chain` turns it into a per-gate
#: literal and gate form, and rejects the orders the hardware cannot express.
#:
#: **The chain is a linear scan, and address order is not traffic order.** A gate
#: forwards everything that is not its own, so a request for the bank at chain
#: position ``j`` walks ``j`` gates' pass-through arms before it walks its own.
#: DOOM's traffic is savagely lopsided, and it was pointing the wrong way.
#: Measured on the emulator's abstract wire (``0 addr`` / ``1 addr value``, see
#: ``lm1.store``), differencing a four-command run against the boot round alone
#: so the figures are per *gameplay* frame — 11,222 reads and 3,416 writes:
#:
#: ::
#:
#:     bank (address range)           reads    writes    chain position
#:     0  1..256   map words          8.37%     0.00%     0 -> 1
#:     1  257..451 spawn/monsters     3.48%     0.04%     1 -> 2
#:     2  452..515 ZBUF               0.00%     1.87%     2 -> 3 (terminal)
#:     3  516..600 frame scalars     88.14%    98.08%     3 -> 0
#:
#: (The ZBUF is written once per column but read only where a sprite survives
#: the cull, so these four frames read it not at all; the 115-frame tour puts it
#: at 0.1%.) Average gate rooms traversed: **2.80 -> 1.15 a read**, **3.00 ->
#: 1.04 a write**. On the request path the average read's walk through the chain
#: goes 63.9 -> 21.3 cells, the average write's 68.9 -> 19.9.
#:
#: The order is simply the traffic order, ``(3, 0, 1, 2)``. Only *some*
#: permutations exist — a gate hands on one contiguous rebased space, so each
#: peels a bank off an **end** — but descending traffic happens to be an
#: end-peeling here, so nothing was given up to get it.
#:
#: Measured on the checked-in 115-frame tour, native engine, all frames clean,
#: against the same 1,113,752,187 baseline the compact gate was measured on:
#:
#: * reorder alone (12-row gate):   1,030,923,183   295x269   **-7.44%**
#: * reorder + compact gate:        1,026,440,454   295x269   **-7.84%**
#:
#: **The two are strongly sub-additive**, which is the interesting part:
#: separately 10.9M + 82.8M = 93.7M, together 87.3M. Once the hot bank is first,
#: the spacers the hot request no longer crosses stop mattering — the
#: compaction's remaining 4.5M is what it is worth on *one* gate instead of
#: three. Kept anyway: it is free, and it still pays on the cold banks.
#:
#: **No program change and no size change.** The high-end gate form
#: (:func:`memory_taped.bank_gate`'s ``high=``) claims the top of the space
#: instead of the bottom, so ``deadman3d.tape_slots()`` is untouched and
#: ``deadman-3d_taped.input.txt`` stays byte-identical to the canonical
#: machine's. The gate room does not change shape either — ``N`` negates in one
#: glyph where the low form's ``+`` restores, and the high form's pass-through
#: arms are two cells *shorter* — so the block is 224x58 and the machine
#: 295x269 both ways.
#:
#: **Re-derived when :data:`TAPED_BANKS` was re-swept**, because an order is only
#: correct for the split it was fitted to. Under ``(352, 164, 15, 69)`` the hot
#: addresses are two banks, not one — the DDA scalars (bank 2) and ``PW``/
#: ``WADDR`` (bank 3) — and both have to lead. ``(3, 2, 0, 1)`` is the descending
#: traffic order and it is still an end-peeling: 3 and 2 come off the top, then 0
#: off the bottom, leaving 1 terminal. Two high gates now instead of one; the
#: block is 224x60 (bank 0's 352-slot ring is two rows deeper than the old 256)
#: and the machine is unchanged at 289x269.
#:
#: The alternatives, on the 8-command native gate, ticks against
#: ``(3, 2, 0, 1)``'s 61,799,020:
#:
#: * ``(3, 2, 1, 0)``  61,979,795  (+0.29%, the two cold banks swapped)
#: * ``(3, 0, 2, 1)``  63,602,816  (+2.9%, the DDA ring behind the map)
#: * ``(0, 3, 2, 1)``  64,478,669  (+4.3%)
#: * address order     67,253,690  (+8.8%)
TAPED_BANK_ORDER: dict[tuple[str, str], tuple[int, ...]] = {
    ("deadman-3d", "taped"): (3, 2, 0, 1),
    # **Eleven entries, because the cut moved.** This was `(3, 0, 1, 2)`, read
    # off hires' traffic over the uniform quarters `taped_plan` hands a slug with
    # no `TAPED_BANKS` entry:
    #
    #     bank  range        reads            writes
    #      0     1.. 226      938   5.67%         0   0.00%
    #      1   227.. 452      550   3.32%         2   0.04%
    #      2   453.. 678       36   0.22%         7   0.11%
    #      3   679.. 901   15,032  90.79%     6,214  99.85%
    #
    # That reading was correct and is now moot: hires *has* a `TAPED_BANKS`
    # entry, the 223-slot bank holding 90.79% of reads no longer exists, and an
    # order is only ever a reading of one particular cut's traffic. The two must
    # move together — the stale order costs 2.8pp on the DP-4 cut, and at
    # address order the block does not even build (`taped block collision at
    # (30, 18)`). Re-derived by the same interval DP that picks the cut
    # (`hires_bankcut.py`), and `gate_chain` accepts it: every gate still peels
    # an end of what is left, which is what makes the reachable orders exactly
    # the 2**(n-1) end-peelings rather than all n!.
    #
    # What survives from the old reading is the *fact* it recorded, and it is
    # why the re-cut paid so well: bank 3 held the ZBUF, CMD and every per-frame
    # scalar, and at 128x96 the scalar traffic doubles against the map traffic —
    # so hires is *more* lopsided than `deadman-3d`, not less.
    ("deadman-3d_hires", "taped"): (10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4),
}

#: Per-slug STORE tier for :func:`build_for` (see :func:`build`'s ``store``).
#: ``deadman-3d``'s 330-slot store is far past the rotating tape's ~103-slot
#: practical cap (and a ring that size would cost ~1,100 ticks a read anyway),
#: so it rides :func:`grid_block`, the address-carrying man-memory: ~31 ticks an
#: access flat, paid for in rows — which an ungraded demo does not count.
#: ``men-v3`` is the unrolled-router man-memory (``memory_men_v3``): ~11 ticks
#: an access against the grid store's ~31, flat in the address, at the price of
#: area — which an ungraded demo does not count. Measured on deadman-3d's
#: ~14.3k accesses per frame it is worth ~0.3M ticks over ``grid``.
STORE_TIER: dict[str, str] = {"deadman-3d": "men-v3", "deadman-3d_hires": "men-v3"}

#: Router-strip length for the men-v3 tier (the ``ops`` knob of
#: ``v3_store_block`` / ``v3_store_grid_block``), per slug; unlisted slugs keep
#: v3's own default (500). ``deadman-3d`` runs the SINGLE looping block (v2's
#: router footprint: one unrolled operation whose end walks straight back to its
#: start): the CPU issues its store reads ~1k ticks apart with lookup work in
#: between, so the walk home happens while the router idles waiting for the next
#: request — off the critical path entirely. The unrolled strip only pays for
#: back-to-back op streams, which this machine never generates; measured on the
#: native frame gate the ops=1 delta is recorded in scratch/deadman3d-opt/
#: METRICS.md. Re-sweep before borrowing this for any machine whose reads
#: arrive in bursts.
STORE_OPS: dict[str, int] = {"deadman-3d": 1, "deadman-3d_hires": 1}

#: STORE shape for the men-v3 tier: ``(cols, rows)`` columns of the multi-column
#: block (``v3_store_grid_block``); absent slugs keep the one-column strip.
#: Capacity is ``cols * rows`` and must cover :data:`TAPE_SIZE` — the shape is
#: pure geometry, addressing is global and unchanged. ``deadman-3d``'s store was
#: the machine's whole silhouette (the one-column block is 681x999 and set BOTH
#: dimensions of the 756x1197 bbox); the viewer holds the full bounding
#: rectangle, so the shape is chosen jointly with :data:`ROM_ROWS` to minimise
#: ``max(w, h)`` (the historic sweeps are in scratch/deadman3d-opt/
#: METRICS.md: 8x42 + rom 44 = the exact 307x307 square at 330 slots,
#: 9x44 + rom 50 = 335x333 at M5's 395). M7a grew the tape to 597 slots
#: (the monster table, HP block, 60 packed sprite columns and the 64-slot
#: ZBUF) and the ROM ~980 words (the sprite phase); the M7a re-sweep
#: (shapes 9..11 wide x rom_rows 40..80 x store_dy) puts the minimum at
#: 10x60 (600 cells) + rom 60 + dy 0 = 374x376 (max 376): the 9-wide
#: block's 67 rows floor the machine HEIGHT at ~397, the 11-wide chain
#: floors the width at 391, and 10-wide crosses rom-width against height
#: at the 60-row fold.
STORE_SHAPE: dict[str, tuple[int, int]] = {"deadman-3d": (10, 60),
                                           # The hi-res tape passed 828 slots when
                                           # the billboards went two words a column
                                           # (240 packed sprite words against 60) and
                                           # the frame grew a 128-slot ZBUF; 12x60 =
                                           # 720 no longer covers it, 14x60 = 840
                                           # does, at the same 60-row fold. This tier
                                           # is the fallback here — `deadman3d_hires`
                                           # builds on the **taped** store, whose
                                           # banks size themselves from TAPE_SIZE.
                                           "deadman-3d_hires": (14, 60)}

#: Bank plan for the **taped** tier (``memory_taped.taped_store_block``): the
#: 600 slots as banked pipe tapes behind a gate chain. This tier exists for the
#: *little-man census* — a bank is two men (worker + relay) and a gate one,
#: against the man-memory's ~two per slot — so the visualizer renders ~20 men
#: instead of ~700. The price is the ring tax (~5-8 ticks per slot per read),
#: which is why the canonical deadman-3d machine stays on men-v3; build the
#: taped variant with ``build_for("deadman-3d", store="taped")``.
#:
#: The plan is a tuple of bank sizes in address order (an int means uniform).
#: deadman-3d's is traffic-shaped, swept on the native frame gate: the high
#: addresses are the hot ones (POWB 257..272, HDG 273..288, then POSX and the
#: per-frame scalars up to PTR — 599 since M7b), so they get SMALL rings —
#: bank locals, and so the ring tax, stay tiny where the traffic is — and the
#: 256 map words split across two 128s. Uniform 4x83 measured 23.7M ticks on
#: rounds 0..1 of the 330-slot machine; the traffic-shaped plan 18.6M at the
#: same men, width and height. M5's third bank added the once-a-frame nukage
#: plane beside POWB/HDGB (96 words); M7a appends two more: the boot-mostly
#: spawn + monster + sprite block (99 words, 353..451 — sprite columns are
#: read only while a billboard paints) and the HOT 64-slot ZBUF as its own
#: cheap ring (64 depth writes plus the occlusion reads every frame must not
#: pay a big ring's lap), with the per-frame scalars keeping a small last
#: ring (84 since M7b's three shot scalars — the last bank grows with them, so
#: the plan keeps covering the whole tape; a plan that under-covers stalls).
#:
#: **The bank COUNT is a width knob; the bank SIZES are not.** Measured: the
#: placed block is ``48*banks + 32`` columns wide at skip-batch 2 (``36*banks +
#: 32`` at 1) whatever the sizes, so six banks cost 320 columns and put the
#: taped machine's whole width floor 16 columns past the ROM's. Merging the two
#: COLD pairs — the 128+128 map halves and the 96 nukage plane + 99 boot-mostly
#: spawn/monster/sprite block — down to ``(256, 195, 64, 85)`` takes the block
#: to 224 columns while leaving the two HOT rings (the 64-slot ZBUF and the
#: per-frame scalars) exactly as small as they were. On the native 9-round gate
#: that is FASTER, not slower: 90,157,275 against six banks' 93,649,383 at the
#: same fold — one fewer gate on every access beats the merged cold rings' laps.
#: Merging the hot pair too (three banks, ``(256, 195, 149)``) is the wrong
#: direction and re-proves the original lesson: 121,458,179, +29%.
#:
#: **Re-swept against the traffic once the chain order came free**
#: (:data:`TAPED_BANK_ORDER`). ``(256, 195, 64, 85)`` was fitted while the chain
#: was forced into address order, so the hot bank was pinned *last* and the only
#: lever left was its size. With the order free the two questions separate: the
#: order decides how many gates a bank is behind, and the sizes decide the ring
#: tax — and the sizes turn out to have been fitted to the wrong boundary.
#:
#: Per-**address** traffic, on the emulator's abstract wire, differencing a
#: four-command run against the boot round alone (``scratch/deadman3d-opt/
#: traffic.py``; 11,222 reads and 3,416 writes a gameplay frame):
#:
#: ::
#:
#:     addresses                       reads    writes   what
#:     517..531  XCOL..COLOR          56.2%     56.2%    the DDA inner loop
#:     532..533  PW, WADDR            25.6%     31.2%    the texture inner loop
#:     1..352    MAPB + POSX..PLANEY   8.4%      0.0%    the map, scanned
#:     353..516  MONB/SPRB/ZBUF/CMD    3.5%      2.0%    boot-mostly + ZBUF
#:     534..600  FRACX..PTR            6.3%     10.6%    the rest of the scalars
#:
#: The old plan's seam at 515/516 cut straight through that: **every** one of
#: those addresses sat in one 85-slot ring. The new plan puts the fifteen
#: DDA scalars in a ring of their own and leaves ``PW``/``WADDR`` — the two
#: hottest single addresses in the machine — in the bank the chain reaches
#: with no gate at all:
#:
#: ::
#:
#:     bank  addresses    M    chain pos   share of accesses
#:     0     1..352      352      2          6.45%
#:     1     353..516    164      3          3.10%
#:     2     517..531     15      1         56.19%
#:     3     532..600     69      0         34.26%
#:
#: Both seams are knife-edges, and both were measured, not derived — the
#: 8-command native gate, ticks against ``(256, 195, 64, 85)``'s 75,782,738:
#:
#: * ``(352, 164, 15, 69)`` (this)  61,799,020  **-18.5%**
#: * ``(352, 164, 16, 68)``         62,405,534  (``WADDR`` moved to the small
#:   ring: one gate hop costs it more than the 54 slots it saves)
#: * ``(352, 164, 14, 70)``         62,132,237
#: * ``(352, 165, 14, 69)``         63,382,964  (``XCOL`` out of the small ring)
#: * ``(352, 163, 16, 69)``         61,943,676
#: * ``(352, 164, 17, 67)``         63,237,686
#: * ``(256, 260, 17, 67)``         64,365,449  (the old bank-0 seam)
#: * ``(160, 356, 17, 67)``         66,243,101
#:
#: Bank 0 wants to be **big**, which the ring-tax model gets exactly backwards:
#: the map is walked in address order, so its ring is already turned to the next
#: word and the tax is not paid. 352 is where that stops — the next seam up
#: (354) costs +0.9% and 358 costs +6.0%, and past ~356 the block outgrows the
#: 60 rows :func:`build` can place it in (370 and up fail to route at every
#: fold, so bank 0 has a hard ceiling here as well as a soft one).
#:
#: Five banks would be the obvious next question and the answer is the width:
#: ``48*5 + 32 = 272`` columns from the store's west wall at x=61 is an east
#: edge of 333, past the 300-column ceiling ``tests/test_deadman3d.py`` pins.
#: Three banks (176 columns, and it would fit) needs bank 0 to swallow
#: everything below 517 — 516 slots, a block 66 rows deep, which does not route
#: at any fold either. Four is what the geometry allows.
#: **``deadman-3d_hires`` is eleven banks because nothing stops it.** Four is
#: what ``deadman-3d``'s geometry allows, per the paragraph above — the 300-column
#: ceiling ``tests/test_deadman3d.py`` pins makes a fifth bank unbuildable. hires
#: has no such ceiling: it is out of contest scope (``AGENTS.md``), and its width
#: floors on the router wall at :data:`d3_router.BLOCK_X0`'s 496 whatever the
#: store does. So the bank count is free to sit at the tick optimum instead of at
#: the last count that fits, and the tick optimum is much further out.
#:
#: The cut is a DP over contiguous splits against this family's own per-address
#: traffic (``scratch/deadman3d-opt/hires_bankcut.py``: the ``lm1.store`` wire
#: traced per address, a 4-frame gameplay run differenced against boot, 15,342
#: reads + 6,809 writes a frame), then measured
#: (``scratch/deadman3d-opt/hires_bankrun.py``) on the 21-round tour over frames
#: 1..20 — the only metric this family has. Against the uniform quarters
#: ``taped_plan`` was handing it, **990,895,368 ticks**:
#:
#: | banks | plan | box | ticks | Δ |
#: |---|---|---|---|---|
#: | 4 (uniform, was) | ``(226, 226, 226, 223)`` | 514x447 | 990,895,368 | — |
#: | 4 (DP) | ``(127, 673, 22, 79)`` | 514x465 | — | -56.42% (3-round) |
#: | 10 | — | 514x455 | 367,214,821 | -62.94% |
#: | **11** | **this** | **514x451** | **365,333,921** | **-63.13%** |
#: | 12 | — | 547x451 | 372,330,182 | -62.43% |
#: | 16 | — | 699x451 | — | -63.08% (3-round) |
#: | 20 | — | 851x447 | — | -61.30% (3-round) |
#:
#: The curve turns over at 11: past it a gate hop costs more than the shorter
#: ring saves. Eleven is also the *narrowest* of the good counts — 514 is HEAD's
#: own width, so this is -63% for four rows.
#:
#: **Why it was worth this much.** The uniform quarters put 93.1% of all accesses
#: in a single 223-slot ring; the ring tax is ``~8 * local`` an access, so
#: nine-tenths of the traffic was paying ~1,800 ticks a read. hires' histogram was
#: already measured — :data:`TAPED_BANK_ORDER` read the bank *order* off it — and
#: the sizes were simply never re-cut to match. That is the whole finding.
#:
#: The order below is **not separable from this**: ``(3, 0, 1, 2)`` was read off
#: the uniform quarters' traffic, and re-cutting the banks rewrites the traffic it
#: was read off. Priced on the DP-4 cut, the stale order gives -53.6% where the
#: matching one gives -56.4%. Any change here needs a new order beside it.
TAPED_BANKS: dict[str, int | tuple[int, ...]] = {
    "deadman-3d": (352, 164, 15, 69),
    "deadman-3d_hires": (102, 21, 229, 7, 306, 135, 6, 9, 7, 58, 21)}

#: Ring-worker batch for the taped tier's banks. ``2`` is the two-word counted
#: worker (~5 ticks per skipped word against batch 1's 8): +12 columns per bank
#: and measured -13% on the frame gate; the machine still fits the 307 width.
TAPED_SKIP_BATCH: dict[str, int] = {"deadman-3d": 2}


def display_for(slug: str) -> tuple[int, int] | None:
    """The problem's LM-75 resolution, or ``None`` when it has no display.

    For a graded problem this is read from the problem JSON rather than recorded
    here: "exactly one display at the stated resolution" (``SPEC.md``) makes this
    the problem's number, and a panel of the wrong size fails every case. Demo
    slugs are the exception — :data:`DISPLAY_OVERRIDE` wins for them, because a
    demo is not bound to any problem's panel.

    ``tape_skip_batch`` is the tape worker implementation parameter.  ``1`` keeps
    the compact legacy loop; ``2`` uses the wider two-word counted ring.  The latter
    is intended for large stores (for example ``tape_n=200``) whose runtime is
    dominated by reads/skips.  It is explicit because fewer ticks can still lose
    contest score when its wider STORE increases the machine's squared footprint.
    """
    from . import programs

    if slug in DISPLAY_OVERRIDE:
        return DISPLAY_OVERRIDE[slug]
    panel = (programs.problem_json(programs.problem_of(slug)).get("io") or {}).get("display")
    return (int(panel["width"]), int(panel["height"])) if panel else None


def _tier_program(slug: str, store: str):
    """The program a ``(slug, tier)`` builds from — :data:`TIER_PROGRAM`, else the
    checked-in ``<slug>.asm``."""
    from importlib import import_module

    from . import programs

    ref = TIER_PROGRAM.get((slug, store))
    if ref is None:
        return programs.load(slug)
    module, _, attr = ref.partition(":")
    prog = getattr(import_module(module), attr)()
    if prog.name != slug:
        raise MachineError(
            f"TIER_PROGRAM[{(slug, store)!r}] returned a program named {prog.name!r}; "
            f"the registry keys off the program name, so it must be {slug!r}"
        )
    return prog


def build_for(
    slug: str,
    *,
    store: str | None = None,
    tape_skip_batch: int | None | str = "task",
    tape_relay_size: tuple[int, int] | None = None,
    tape_jump_threshold: int = 128,
    compact: bool = False,
    trim_dead: bool | None = None,
    top_bus: bool | None = None,
    seek: bool | None = None,
    store_chain_pad: int = 0,
    program=None,
) -> Machine:
    """Generate the machine for a checked-in task program.

    Everything not derivable from the ``.asm`` comes from the registry above, except
    the panel size, which comes from the problem JSON: a display-judged problem
    requires *exactly one* display at the stated resolution, so it is not a free
    variable the generator may shrink. ``store=None`` takes the slug's registered
    tier from :data:`STORE_TIER` (default ``"tape"``); pass one explicitly to
    override. Pass ``compact=True`` for the opt-in constraint-placement pass;
    default generation remains the checked-in layout. ``program`` overrides the
    checked-in ``.asm`` (deadman-3d's ``--wad`` mode builds a locally imported
    level's machine without touching the program directory).

    ``store_chain_pad`` is the one measurement instrument here rather than a
    registry, for the same reason ``build``'s ``resp_pad`` is: it leaves
    :data:`TAPED_CHAIN_REACH`'s gates that many columns short of their callers,
    lengthening each chain link by exactly that much and moving nothing else, so
    the leg's tick derivative comes out of a division instead of an argument.
    Default 0 — the shipped grid.
    """
    from . import programs

    if slug not in TAPE_SIZE:
        raise MachineError(f"no tape size recorded for {slug!r}; have {sorted(TAPE_SIZE)}")
    if store is None:
        store = STORE_TIER.get(slug, "tape")
    if tape_skip_batch == "task":
        tape_skip_batch, task_relay = TASK_TAPE_CONFIG.get(slug, (1, None))
        if tape_relay_size is None:
            tape_relay_size = task_relay
    elif not isinstance(tape_skip_batch, (int, type(None))):
        raise MachineError(
            f"tape_skip_batch must be 1, 2, 4, None, or 'task', got {tape_skip_batch!r}"
        )
    _seek = (slug in SEEK_DRUM) if seek is None else seek
    # A slug that ships two machines off one program gets one layout per tier:
    # the store block's shape is what sets the width/height trade, and the two
    # tiers' blocks are nothing alike (see :data:`TIER_LAYOUT`). Empty for every
    # other `(slug, tier)`, so their grids stay byte-identical. A seek drum
    # re-shapes the ROM block, so it gets its own overlay keyed the same way.
    tier = dict(TIER_LAYOUT.get((slug, store), {}))
    if _seek:
        tier.update(SEEK_TIER_LAYOUT.get((slug, store), {}))
    unknown = set(tier) - {"rom_rows", "mem_offset", "store_offset"}
    if unknown:
        raise MachineError(f"TIER_LAYOUT[{(slug, store)!r}] has unknown keys {sorted(unknown)}")
    mem_offset, store_offset = MEM_PLACE.get(slug, ((0, 0), (0, 0)))
    if program is None:
        program = _tier_program(slug, store)
    return build(
        program,
        tape_n=TAPE_SIZE[slug],
        rom_rows=tier.get("rom_rows", ROM_ROWS.get(slug)),
        mem_pad=(
            MEM_PAD_FOR.get((slug, store), SEEK_MEM_PAD.get(slug, MEM_PAD.get(slug)))
            if _seek
            else MEM_PAD.get(slug)
        ),
        display=display_for(slug),
        stream=STREAM_SIZE.get(slug),
        store=store,
        tape_skip_batch=tape_skip_batch,
        tape_relay_size=tape_relay_size,
        tape_jump_threshold=tape_jump_threshold,
        middle_order=LANE_ORDER.get(slug),
        opcode_slots=OPCODE_SLOTS.get((slug, store)),
        rom_buffer=ROM_BUFFER.get(slug),
        compact=compact,
        mem_offset=tier.get("mem_offset", mem_offset),
        store_offset=tier.get("store_offset", store_offset),
        in_north=slug in INPUT_NORTH,
        store_teleport=slug in STORE_TELEPORT and (slug, store) not in STORE_ANSWER_WEST,
        store_answer_west=(slug, store) in STORE_ANSWER_WEST,
        store_request_teleport=(slug, store) in STORE_REQUEST_TELEPORT,
        store_chain_reach=(slug, store) in TAPED_CHAIN_REACH,
        store_chain_pad=store_chain_pad,
        store_feed_teleport=(slug, store) in TAPED_FEED_TELEPORT,
        store_request_reach=(slug, store) in STORE_REQUEST_REACH,
        store_compact_gate=(slug, store) in TAPED_COMPACT_GATE,
        store_bank_order=TAPED_BANK_ORDER.get((slug, store)),
        trim_dead=(slug in TRIM_DEAD_LANES) if trim_dead is None else trim_dead,
        seek=_seek,
        seek_teleport=_seek and (slug, store) in SEEK_TELEPORT,
        in_west=INPUT_NORTH_WEST.get((slug, store), 0),
        seek_taken_drop_east=_seek and (slug, store) in SEEK_TAKEN_DROP_EAST,
        seek_ops=SEEK_OPS_FOR.get(slug, SEEK_OPS),
        top_bus=(slug in TOP_RETURN_BUS) if top_bus is None else top_bus,
        store_shape=STORE_SHAPE.get(slug),
        doom_loop_row=DOOM_LOOP_ROW.get((slug, store)),
        doom_leaf_cols=DOOM_LEAF_COLS.get((slug, store)),
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
    ap.add_argument(
        "--store",
        choices=STORE_TIERS,
        help="override the slug's registered STORE tier (e.g. deadman-3d's taped variant)",
    )
    ap.add_argument("--report", action="store_true", help="print the size report to stderr")
    ap.add_argument(
        "--compact",
        action="store_true",
        help="use opt-in constraint routing for movable/compactable connections",
    )
    ap.add_argument(
        "--tape-skip-batch",
        choices=("1", "2", "4", "auto", "task"),
        default="task",
        help="tape skip strategy; task uses the measured per-task choice (default: task)",
    )
    ap.add_argument(
        "--tape-relay",
        metavar="WxH",
        help="fat relay interior, for example 6x4 or 8x6 (default by skip batch)",
    )
    ap.add_argument(
        "--tape-jump-threshold",
        type=int,
        default=128,
        help="minimum tape size where --tape-skip-batch auto selects 2 (default: 128)",
    )
    args = ap.parse_args(argv)

    relay_size = None
    if args.tape_relay:
        try:
            rw, rh = args.tape_relay.lower().split("x", 1)
            relay_size = (int(rw), int(rh))
        except (TypeError, ValueError) as exc:
            ap.error(f"--tape-relay must be WxH, got {args.tape_relay!r}: {exc}")
    if args.tape_skip_batch == "task":
        skip_batch: int | None | str = "task"
    elif args.tape_skip_batch == "auto":
        skip_batch = None
    else:
        skip_batch = int(args.tape_skip_batch)
    m = build_for(
        args.slug,
        store=args.store,
        tape_skip_batch=skip_batch,
        tape_relay_size=relay_size,
        tape_jump_threshold=args.tape_jump_threshold,
        compact=args.compact,
    )
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
