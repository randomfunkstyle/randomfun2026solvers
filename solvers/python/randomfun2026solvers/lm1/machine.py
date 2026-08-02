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
sit east of every lane below (``drop_x[r] = max(lane_end[q] for q >= r) + 1``, or,
under :data:`TUCKED_DROPS`, clear of every *operation* below it — the same rule
read per column instead of as one number, since a lane's ``.`` padding blocks
nothing).
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

import contextlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
    "LEAN_TRIE",
    "HIGH_DROPS_FREE",
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
    # The SPILL block: `PUSH`/`POP` (opcodes 22/23), one word each way. One band
    # for both glyphs, exactly as :data:`Band.MEM` does it — the `s` binds
    # ``spill_req`` and the `r` binds ``spill_resp``, which is what lets a single
    # ``band_x`` column carry both lanes' pipe glyph (see :data:`SPILL_LAYOUT`).
    #
    # ``isa.py`` has had these two opcodes since the ISA was written and this
    # module refused to draw them, on a reason that is *narrower* than it reads:
    # the comment at :data:`Sem.LOAD_IND` says the sign-biased STORE protocol
    # means ``LDP``/``STP`` need no spill slot, which is true and has nothing to
    # say about a program that wants one for its own scratch words.
    SPILL = "spill"


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
    # ── SPILL: the one block ``isa.py`` names and this module never drew ──────
    # ``PUSH``: ACC is in B, so `W` brings it into A, `s` hands it to the block and
    # the second `W` puts it back — the same sandwich ``OUT`` and the display ports
    # use, and for the same reason (ACC has to survive).
    Sem.SPILL_PUSH: (("W", None), ("s", Band.SPILL), ("W", None)),
    # ``POP``: the block's answer lands in A and `M` makes it ACC. Nothing else is
    # live, so there is no sandwich to pay for.
    Sem.SPILL_POP: (("r", Band.SPILL), ("M", None)),
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
        #: The functional part currently being drawn, and every cell each part
        #: claimed.  A generated grid carries no comments, so ``Machine.regions``
        #: is the only record of what a cell *means* — and a box drawn by hand
        #: from remembered geometry goes stale silently and misattributes profile
        #: heat (``cpu:trie``'s box over-reached its content by fourteen rows for
        #: exactly that reason).  Recording the cells as they are drawn makes the
        #: box a *consequence* of the drawing instead of a claim about it, so it
        #: cannot drift: see :func:`_mark_boxes`.
        self.stack: list[str] = []
        self.marks: dict[str, list[tuple[int, int]]] = {}

    def _claim(self, x: int, y: int) -> None:
        for name in self.stack:
            self.marks.setdefault(name, []).append((x, y))

    @contextlib.contextmanager
    def part(self, name: str | None, *, exclusive: bool = False) -> Iterator[None]:
        """Attribute every cell drawn inside the block to ``name``.

        Parts **nest**: a cell drawn inside ``slab:BRZ`` > ``discard:BRZ`` is
        claimed by both, so the outer box is the union of its children and the
        inner one is the tight sub-box — which is exactly the reading a profile
        wants ("the slab" and "the two ``r``s inside it that block on the ROM").

        ``exclusive=True`` breaks out of the enclosing parts instead, for a run
        that is drawn *by* a structure but does not belong *inside* its box — a
        slab's exit riser climbs past every shallower slab to the collector, and
        rolling it up would stretch the slab's box over the whole band above it.
        """
        prev = self.stack
        self.stack = ([] if exclusive else list(prev)) + ([name] if name else [])
        try:
            yield
        finally:
            self.stack = prev

    def put(self, x: int, y: int, ch: str) -> None:
        old = self.c.get((x, y))
        if old is not None and old != ch:
            raise MachineError(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        self.c[(x, y)] = ch
        self._claim(x, y)

    def soft(self, x: int, y: int, ch: str) -> None:
        """Place ``ch`` only if the cell is empty (used for filler dots)."""
        if (x, y) not in self.c:
            self.c[(x, y)] = ch
        # Claimed either way: a `soft` that lost to an earlier glyph is still a
        # cell this part walks (that is the whole point of the run), and the box
        # has to contain it.
        self._claim(x, y)

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


def _mark_boxes(g: _Grid) -> dict[str, tuple[int, int, int, int]]:
    """Each marked part's bounding box — tight by construction.

    An over-reaching region box is *worse* than no box at all: ``_region_of``
    hands a cell to the smallest box containing it, so a box claiming rows it
    never draws on silently absorbs whoever really owns them. Deriving the box
    from :meth:`_Grid.part`'s own cells makes that impossible — the box is the
    extent of the drawing, and moving a glyph moves the box with it.
    """
    out: dict[str, tuple[int, int, int, int]] = {}
    for name, cells in g.marks.items():
        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        box = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        # Two names for one box make ``_region_of``'s smallest-wins tie-break
        # arbitrary, and a name that never wins is a label nobody can act on.
        # It happens where a nested part fills its parent exactly — a *jump*
        # slab is nothing but its discard loop — so the outer, more general name
        # is the one to keep (parts are claimed outermost-first).
        if box not in out.values():
            out[name] = box
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
#: **Re-swept on hires/men-v3 after :data:`SEEK_TWIN_STATION` and ``FETCH_FOLD``,
#: both of which changed what a seek costs. 256 still wins, and the curve around it
#: is not a curve.** 21 rounds, ``passed=True``, ``fatal=None``, against
#: 82,530,131:
#:
#: | thr | ticks | Δ |
#: |---|---|---|
#: | 64 | builds, **will not load** | ``numeric literal does not fit signed 64 bits`` |
#: | 128 | 82,647,440 | +0.142% |
#: | 192 | 82,761,338 | +0.280% |
#: | **256** | **82,530,131** | **—** |
#: | 384 | 83,672,251 | +1.384% |
#: | 512 | 82,773,838 | +0.295% |
#:
#: Non-monotone in both directions and 384 is a spike, so this is not a smooth
#: break-even being crossed: what the threshold moves is *which* jumps become
#: ``JMPS``, and that changes the ROM image and the literals in it, not just a
#: count. **Sweeping the threshold alone cannot pay.** The leverage the split
#: promises is on the 27,893 counted-discard ``JMPF``s carrying 341,177 words, and
#: reaching them needs the *reposition* to get cheaper first — the threshold then
#: follows. Note also that 49 of the 83 flush words per seek exist only because
#: ``rom_capacity`` is 49: a corridor is a FIFO whose length is its capacity, and a
#: seek must drain everything in flight, which no cheaper notice path touches.
#:
#: The load rung is not optional when sweeping this: a build that refuses is
#: obvious, but a build that assembles and then dies in
#: :class:`~randomfun2026solvers.fast_littleman.FastLittleman` is not.
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


def seek_words(program: Program, p: _Plan, *, rows: int, twin_station: bool = False):
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
    kw = {"rows": rows, "wide": wide, "twin_station": twin_station}
    probe = build_seek_rom(encode(operands), wide_digits=5, **kw)
    digits = len(str((probe.rows_used + 2) * SEEK_K))
    layout = build_seek_rom(encode(operands), wide_digits=digits, **kw)
    for _ in range(4):
        operands = {k: seek_target(layout, 2 * t) for k, t in targets.items()}
        words = encode(operands)
        new_layout = build_seek_rom(words, wide_digits=digits, **kw)
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


#: ``(north, south)`` offsets from ``base`` for the two **rising** arms under
#: :data:`SLAB_TIGHT_RISERS`, and the number :func:`_slab_east_span` reports as the
#: branch span. Swept rather than derived: the drain block below the arms hard-puts
#: its ``<`` at ``base + 3`` and ``base + 4``, so how far west a riser may go is a
#: question about the *discard* block's width, not about the arms.
_SLAB_RISER_SLOTS = (5, 3)


def _slab_east_span(
    p: _Plan,
    m: str,
    drain_unit_bits: int,
    drain_ops: tuple[str, ...] | None,
    jump_east: bool,
    seek_jump_gap: int = 0,
    tight_arms: bool = False,
    tight_risers: bool = False,
    tuck_drain: bool = False,
) -> int:
    """How many columns **east of ``base``** slab ``m``'s own glyphs reach.

    The pitch is one number for the whole band, floored at the eleven columns a
    *branch* spans (:data:`_SLAB_PITCH_FLOOR`). Only a branch spans eleven. This
    is the per-slab truth the uniform step rounds up to, and
    :data:`PACKED_SLAB_BAND` is what spends it:

    * a **branch**, classic or seek, fans out to the ``neg`` arm at ``base + 9``;
    * a **classic jump** is the counted discard loop's ``a<`` at ``base``/``base
      + 1`` — or, drained, a :func:`_drain_block` pinned at ``base - 1``, whose
      east edge is ``base + width - 2``;
    * a **seek jump** has no body at all. It is one turn south and a drop, and
      under :data:`SEEK_TAKEN_DROP_EAST` that turn is at ``struct_east + 1``,
      far east of the band — so its ``base`` column is drawn on by nobody and
      the slab costs the staircase nothing but its exit riser's column.
      Without that registry the turn falls back *to* ``base``, and the drop then
      runs from the entry row all the way down to the taken row, crossing every
      deeper slab's body: that one keeps a branch's span rather than reasoning
      about it.

    ``seek_jump_gap`` is the one number here that is a *tuning* value and not a
    measurement of glyphs. A seek jump draws nothing, but the column it turns
    south on has to be free from its entry row to the taken row — below every
    slab — and it may not be east of its own drop. Pack the band flat and the
    only such column left is ``base`` itself, which is the U-turn
    :data:`SEEK_TAKEN_DROP_EAST` exists to remove. Leaving a couple of columns
    open beside the jump gives the turn somewhere nearer the drop to land.
    See :data:`PACKED_SLAB_BAND`.

    The riser of the *next* slab lives at its own ``base - 1``, which is why the
    caller steps by ``span + 2`` and not ``span + 1``.
    """
    sem = p.sem[m]
    if sem is Sem.JUMP_SEEK:
        return seek_jump_gap if jump_east else 9
    # NOTE: :data:`SLAB_TIGHT_ARMS` deliberately does **not** narrow this span,
    # even though a ``BR_NEG``'s easternmost riser falls to ``base + 6`` when its
    # taken arm stops reaching ``base + 9``. Spending the three columns measured
    # **+5.76%** on hires/men-v3: closing the band moves ``struct_east`` west, the
    # request `s` with it, and the §7.1 tie that fixes ``mem_pad`` then needs a pad
    # of 10 where 2 had done. Eight extra pad cells are walked by every one of
    # ~87,000 store reads, and that costs an order of magnitude more than the arm
    # compaction saves. The arms are narrowed for their **walk**; the band keeps
    # its columns. See the registry's note.
    drained = 0
    if _drained(m, drain_unit_bits, drain_ops):
        from .drain import build_drain

        # The block is pinned at ``base - 1`` (:func:`_drain_block`), so its east
        # edge is ``base + width - 2``. A *branch* under :data:`SLAB_TUCKED_DRAIN`
        # is pinned one column further east, onto the taken arm's own column, so
        # its east edge is ``base + width - 1``.
        drained = build_drain(0, unit_bits=drain_unit_bits, even=True).width - 2
        if tuck_drain and sem not in _JUMP_SEMS:
            drained += 1
    if sem in _JUMP_SEMS:
        return max(1, drained)
    # A drained branch keeps its ``X`` fan-out as well; whichever reaches further.
    # :data:`SLAB_TIGHT_RISERS` moves the two *rising* arms onto ``base + 3`` and
    # ``base + 5``, so the branch's easternmost glyph is the northern riser at
    # ``+5`` rather than ``neg``'s slot at ``+9``. The span has to follow, or the
    # staircase keeps reserving columns nobody draws on.
    return max(_SLAB_RISER_SLOTS[0] if tight_risers else 9, drained)


def _slab_bases(
    p: _Plan,
    order: list[str],
    struct_x0: int,
    pitch: int,
    *,
    packed: bool = False,
    drain_unit_bits: int = 0,
    drain_ops: tuple[str, ...] | None = None,
    jump_east: bool = False,
    seek_jump_gap: int = 0,
    tight_arms: bool = False,
    tight_risers: bool = False,
    tuck_drain: bool = False,
) -> tuple[dict[str, int], int]:
    """``(base per slab, struct_east)`` for the staircase.

    The unpacked answer is the shipped one — ``struct_x0 + i * pitch``, and
    ``struct_east`` a whole pitch past the last slab. ``packed`` steps by what
    each slab actually draws (:func:`_slab_east_span`) instead, and defines
    ``struct_east`` the same way the uniform step happens to: two columns past
    the deepest slab's east edge, which is the first column east of every body.
    """
    uniform = struct_x0 + max(1, len(order)) * pitch
    if not packed:
        return {m: struct_x0 + i * pitch for i, m in enumerate(order)}, uniform
    bases: dict[str, int] = {}
    b = struct_x0
    #: The first column east of every body. With no structured opcodes at all there
    #: are no bodies, and the uniform answer's one-slab floor is what the rest of
    #: the builder has always been handed.
    east = uniform - 2
    for m in order:
        bases[m] = b
        span = _slab_east_span(
            p, m, drain_unit_bits, drain_ops, jump_east, seek_jump_gap, tight_arms,
            tight_risers, tuck_drain,
        )
        east = b + span
        b += span + 2
    return bases, east + 2


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
    #: Which part drew which cells (:meth:`_Grid.part`) — the evidence the boxes
    #: in ``regions`` were derived from. ``regions`` is the bounding box of each
    #: of these lists; this keeps the lists themselves so a test can check that
    #: the boxes actually hand each part its own cells back rather than merely
    #: being tight (``tests/test_lm1_cpu_regions.py``). Cells appear under more
    #: than one part where the drawing genuinely shares them.
    marks: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


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


def _fold_cut(micro: tuple[tuple[str, str | None], ...]) -> int:
    """Where a lane's micro-program may leave its row — the vertical fold's hinge.

    A man executes whatever cell he steps on, so a micro-program is a *path*, not a
    row: running the tail south down the lane's own drop column costs exactly what
    running it east costs, because the drop is cells he was going to walk anyway.
    What changes is where he **stops going east**, and that — ``lane_end``, hence
    ``drop_x``, hence ``2 * drop_x`` on every instruction — is the whole prize.

    The hinge is the last glyph carrying a **band**, so the tail is the maximal
    band-free suffix. Everything up to and including the hinge stays on the row,
    which is stricter than :func:`_flat_lane`'s own rule — it only pins each band's
    *first* glyph — and the extra strictness is measured, twice, on
    ``deadman-3d_hires``:

    * Folding a repeat memory glyph moves it **south**, and the ROM pipe enters the
      west wall at the fetch row, so south is *toward* a rival. ``LD``'s ``r`` from
      ``(36, 163)`` to ``(36, 164)`` — one row — takes the ``mem_pad`` floor from 12
      to 13 and walks the memory block a column east for *every* memory lane:
      ``LD`` 38 -> 37 bought at ``ST``/``SUB``/``DIV`` 40 -> 41 and
      ``ADD``/``LDA`` 39 -> 40. A net loss.
    * Folding one **east** instead — into a drop column the lanes below have already
      pushed past the flat lane's end, so nothing moves west — was tried on ``INCM``,
      the topmost memory lane and the one whose ``r`` the pad floor is actually
      measured against (at pad 11, ``mem_resp`` 28 against ``in`` 28, and ties fail).
      It works, and it buys nothing: with ``INCM``'s block moved to column 43, pad 11
      fails on ``LD``'s ``r`` at ``(35, 163)`` instead, ``rom`` 36 against
      ``mem_resp`` 37. The squeeze is two-sided — the input pipe on the north wall
      against the top of the band, the ROM pipe on the west against the bottom — and
      a fold can relieve one end only by walking into the other.

    Every other band is stricter still and not by measurement: it binds a pipe
    leaving the **south** wall directly below the glyph's own column
    (``_DSP_PITCH``, ``_STREAM_PITCH``) or the north wall above it (``in_col``), so
    moving one a single column binds a neighbour's pipe, or nothing.

    The hinge is never 0: the first glyph stays on the lane's row at ``lane_x0``,
    because a ``v`` there would turn the man south *before* he has executed
    anything, and into the lane below's row.
    """
    hinge = max((i for i, (_g, band) in enumerate(micro) if band is not None), default=0)
    return max(1, hinge + 1)


def _uneven_gaps(k: int, slots: Sequence[int], straight: bool = False) -> set[int]:
    """Which adjacent lane pairs still need a blank row between them.

    The band has historically been laid at a uniform pitch of two: one row per
    lane and one for the ``x`` that splits it from the next. That is one row per
    *node*, and most of them do not need one. A node at level ``L`` sits in column
    ``3 + 2L`` and every lane in its subtree is entered at column ``>= 5 + 2L``,
    so the node — and both its legs — are strictly **west** of every lane beneath
    it. The lane's man starts east of the node and never walks onto it, and no leg
    ever lands inside a lane's shift run. **A node can share a lane's row.**

    Exactly one case cannot, and it is forced by ``x`` itself: ``x`` *always*
    turns, clockwise to the south and counter-clockwise to the north, with no
    outcome that leaves the man on his own row. So a node's row must lie strictly
    **between** its two children's rows. :func:`_uneven_trie` puts a node on the
    row above its down-half's first lane, which is the up-half's *last* lane — and
    if the up half is a single lane, that lane **is** the node's own up child. Its
    entry ``>`` then lands on the node's cell and overwrites the ``x``, and every
    opcode routed through it walks east into the wrong lane, silently.

    So: a gap row is needed after rank ``i`` exactly when the node splitting lane
    ``i`` from lane ``i + 1`` has a **single-lane up half**. Everywhere else the
    two lanes may sit one row apart.

    ``straight`` (:data:`STRAIGHT_TRIE`) dissolves most of what is left, and it is
    ``x``'s "always turns" that it trades away. ``SPEC.md``:

        ``d`` — turn **clockwise** if BP > 0, else go straight.

    So a node drawn as ``d`` leaves the man on its own row when ``BP == 0``, and
    can therefore *be* its up child's entry rather than colliding with it. The
    equivalence is exact where it is used and nowhere else: after the ``L - 1``
    ``]``-shifts a level-``L`` node owes, ``BP`` is the slot's **offset inside that
    node's dyadic interval** (``slot - lo``), so

    * ``x`` sends the man north on offset-bit ``top`` clear, south on it set;
    * ``d`` sends him straight on ``offset == 0`` and south otherwise.

    Those agree iff the up half is exactly ``{lo}`` — one lane, sitting at the
    interval's base. The down half always has ``offset > 0``, so that side never
    needs checking. Slots are non-negative, so ``x``'s "a negative backpack is not
    treated as zero" caveat cannot bite either.

    Every gap this function currently reports comes from a single-lane up half, and
    under the contiguous packing that lane is always at ``lo`` — which is why the
    flag removes **all ten** of ``deadman-3d_hires``' gaps and takes its band from
    32 rows to 22. A searched slot map can put the lone up lane somewhere other
    than ``lo`` (``{1, 2}`` inside ``[0, 4)``, say), and there the gap stays.

    Returns the set of ranks after which a gap is required. Mirrors
    :func:`_uneven_trie`'s recursion, including its single-child contraction, so
    the tree it measures is the one that gets built.
    """
    used = sorted(slots)
    rank = {s: i for i, s in enumerate(used)}
    gaps: set[int] = set()

    def node(lo: int, hi: int) -> None:
        sl = [s for s in used if lo <= s < hi]
        mid, up, down = lo, [], []
        while len(sl) > 1:
            mid = (lo + hi) // 2
            up = [s for s in sl if s < mid]
            down = [s for s in sl if s >= mid]
            if up and down:
                break
            lo, hi = (lo, mid) if up else (mid, hi)
        if len(sl) <= 1:
            return
        if len(up) == 1 and not (straight and up[0] == lo):
            gaps.add(rank[max(up)])
        node(lo, mid)
        node(mid, hi)

    node(0, 1 << k)
    return gaps


def _lean_row(nd: dict, gap: int, side: int, greedy: bool = False) -> int:
    """Where a decode node should actually stand, given its two children's rows.

    :func:`_trie_shape` puts every node on ``gap`` — the row above its down half's
    first lane. That is *a* legal row, not the cheap one. The only hard rule is
    ``x``'s: a node's row must lie strictly **between** its two children's rows.
    Everywhere inside that open interval the node is free, and the interval is
    wide wherever the two halves are unbalanced.

    Which end of it is cheapest is settled by arithmetic rather than by a
    frequency table, and that is what makes this safe to switch on blind. Write
    ``p`` for the parent's row, ``u``/``d`` for the two children's, and ``W_u`` /
    ``W_d`` for how many instructions descend into each half. Every instruction
    that reaches this node came through the parent and leaves through one child,
    so its share of the walk is

        W(p, x) = (W_u + W_d)|p - x| + W_u|x - u| + W_d|x - d|

    and the node's own traffic ``W_u + W_d`` is, by construction, **half the total
    weight** — a weighted median of three points one of which holds half the mass
    sits on that point. So the optimum is ``x = p``, and ``p`` lies outside
    ``(u, d)`` (a node is the boundary of its parent's split, so the parent starts
    above a down child's whole subtree and below an up child's), which makes the
    best legal row simply **the end of the interval nearest the parent**.
    Differentiating confirms it: for ``p > d`` the slope in ``x`` is ``-2 W_d``,
    for ``p < u`` it is ``+2 W_u`` — monotone either way, no frequencies needed.

    Note the direction, not the distance, is all this uses, which is what lets the
    recursion place children before parents and still be right. It also means the
    rule is not quite exact once leaning has moved the parents too: a parent that
    has itself leaned can end up *inside* a grandchild's interval, and there the
    grandchild's own optimum would have been that parent's row rather than the end
    of the interval. The tree stays legal either way — a parent is placed after its
    children and clamped strictly between them — so this costs a row somewhere, not
    a routing.

    Two guards keep this from paying for itself elsewhere:

    * ``need`` leaves each child ``shifts`` cells of *vertical* slack, so
      :func:`_trie_columns` can still hang that edge's ``]``s on the leg and give
      the child ``parent + 1``. Without it the leaned edge goes to zero slack, the
      child's column moves to ``parent + 2`` and ``lane_x0`` — which every lane,
      every drop and the whole walk back west are measured from — grows by one,
      which costs more than the lean saves. With it a leaned tree's columns are
      **never wider** than the boundary tree's: the slack it leaves is exactly the
      slack the tight rule asks for, and the other leg only ever gets longer.
    * the row never moves *away* from ``gap``, so a node with no room to lean
      keeps the shipped placement and the tree degrades to the old one node by
      node rather than all at once.

    ``greedy`` drops the first guard and leans the whole way — ``drow - 1`` or
    ``urow + 1`` — which is worth another row on the nodes that have one to give.
    It *may* widen a column, and whether it actually does is not a property of the
    node: it depends on whether that node happens to sit on the branch that is
    already the deepest, because ``lane_x0`` is a **maximum** over the whole tree.
    So the caller decides it per node and by measurement rather than by rule — see
    :func:`build_cpu`, which shapes the tree once per candidate and keeps only the
    leans the band's origin does not notice.
    """
    (_su, urow, uclevel, uci), (_sd, drow, dclevel, dci) = nd["kids"]
    level = nd["level"]
    # ``shifts + 1`` rows of separation is ``shifts`` cells of leg slack.
    need_u = 1 if greedy else ((uclevel - level + 1) if uci is not None else 1)
    need_d = 1 if greedy else ((dclevel - level + 1) if dci is not None else 1)
    row = max(gap, drow - need_d) if side < 0 else min(gap, urow + need_u)
    return max(urow + 1, min(drow - 1, row))


def _trie_shape(
    k: int,
    slot_rows: dict[int, int],
    straight: bool = False,
    inline_far: bool = False,
    lean: bool | str | frozenset[int] = False,
) -> tuple[int, int | None, list[dict], int | None]:
    """The pruned, contracted decode tree — rows and levels, no columns yet.

    Split out of :func:`_uneven_trie` because the columns are no longer a function
    of the level alone (:func:`_trie_columns`), and a column cannot be chosen until
    the whole subtree below it is known. Returns ``(entry row, entry level, nodes,
    root index)``; ``entry level`` and ``root`` are ``None`` for a one-lane trie.
    Each node is ``{level, row, inline, kids}`` and each kid is
    ``(sign, child row, child level or None, child index or None)``.

    ``lean`` (:data:`LEAN_TRIE`) moves each node **toward its own parent**, which
    is free and is worth real ticks. See :func:`_lean_row` for the arithmetic.
    """
    used = sorted(slot_rows)
    tree: list[dict] = []

    def node(level: int, lo: int, hi: int, side: int = 0) -> tuple[int, int | None, int | None]:
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
            return slot_rows[sl[0]], None, None
        # The one node shape that may go straight instead of turning: a single-lane
        # up half sitting at the interval's base (so ``BP == 0`` picks it exactly).
        # See :func:`_uneven_gaps` for why the two glyphs agree exactly there.
        gap = slot_rows[min(down)] - 1  # the gap row above the down half
        # An ``x`` has to stand on ``gap``, because it always turns. A ``d`` does
        # not: it goes **straight** on ``BP == 0``, so it stands on its up child's
        # own row and *is* that lane's entry, however far above the down half that
        # row is — the man it sends south simply walks further, over ``.`` cells
        # that belong to nobody (the rows between a single-lane up half and the down
        # half hold no lane, by construction). Anchoring the ``d`` to the child
        # rather than to the gap is what lets :data:`HIGH_COLLECTOR` open a blank
        # row here without landing a turn glyph in the corridor; without
        # ``inline_far`` the two anchors have to coincide, which is the shipped rule.
        inline = (
            straight
            and len(up) == 1
            and up[0] == lo
            and (inline_far or slot_rows[up[0]] == gap)
        )
        me = len(tree)
        nd = {"level": level, "row": slot_rows[up[0]] if inline else gap,
              "inline": inline, "kids": []}
        tree.append(nd)
        for half, sign in (((lo, mid), -1), ((mid, hi), +1)):
            crow, clevel, ci = node(level + 1, *half, side=sign)
            nd["kids"].append((sign, crow, clevel, ci))
        # The children's rows are only known now, which is why this is not folded
        # into the ``gap`` above: a node leans against its *children*, and the
        # recursion has to have placed them first.
        if lean is not False and side and not inline:
            nd["row"] = _lean_row(
                nd, gap, side,
                lean == "greedy" or (isinstance(lean, frozenset) and me in lean),
            )
        return nd["row"], level, me

    entry, elevel, root = node(1, 0, 1 << k)
    return entry, elevel, tree, root


def _trie_columns(
    tree: list[dict], root: int | None, elevel: int | None, tight: bool, fetch_w: int = 4
) -> dict[int, int]:
    """Which column each decode node stands in.

    The shipped rule is ``3 + 2 * level``: **two** columns a level, so that an edge
    contracting ``d`` levels has ``2d - 1 >= d`` cells for its ``]``s. It is a
    sufficient condition and a wasteful one — the trie's horizontal traverse is
    ``lane_x0 - 5`` on *every* instruction, and ``lane_x0`` is the deepest node's
    column plus one, so every column here is also a column on every drop and every
    metre of the walk back west. On ``deadman-3d_hires`` that is ``4 + 2k = 14``.

    ``tight`` prices it per **node** instead, off two observations:

    * most edges owe **one** shift, not ``d`` — a contraction is the exception; and
    * the run east along the child's row is not the only place a ``]`` can stand.
      The edge's *vertical* leg is ``.`` today, the man keeps his heading over a
      ``]``, and the leg's cells are in the parent's column, which is strictly west
      of every lane in the subtree. So a leg with any slack in it hosts the shift
      for free and the child needs only ``parent + 1``.

    A child therefore sits at ``parent + 1 + max(0, shifts - leg slack)``, which on
    this machine gives ``5,6,7,8,10`` down one side and ``5,6,8,9,11`` down the
    other: **``lane_x0`` 14 -> 12**, and the two columns come off the trie walk and
    off the return walk at once. Levels 1..4 pay one column each; only the deepest
    pair, whose rows are adjacent so the leg has no slack, still pays two.

    One column a level flat is *not* feasible and the reason is worth keeping: it
    leaves ``d - 1`` cells for ``d`` shifts on every edge, including the
    uncontracted ``d = 1`` ones, which get none at all.

    ``fetch_w`` is the **prologue's width** — how many columns the fetch row spends
    before the trie may start. It is 4 for ``>rbr`` and 2 for the folded ``>r``
    (:data:`FETCH_FOLD`), and it translates the *whole* tree, because ``place`` is
    recursive from the root: every column here is the root's plus a fixed offset.
    """
    cols: dict[int, int] = {}
    if root is None:
        return cols

    def place(i: int, col: int) -> None:
        cols[i] = col
        nd = tree[i]
        for sign, crow, clevel, ci in nd["kids"]:
            if ci is None:
                continue
            if not tight:
                place(ci, 3 + 2 * clevel)
                continue
            slack = (
                0
                if (nd["inline"] and sign < 0)
                else max(0, abs(crow - nd["row"]) - 1)
            )
            place(ci, col + 1 + max(0, (clevel - nd["level"]) - slack))

    place(
        root,
        max(fetch_w + 1, fetch_w + elevel) if tight else (fetch_w - 1) + 2 * elevel,
    )
    return cols


def _uneven_trie(
    k: int,
    slot_rows: dict[int, int],
    lane_x0: int,
    straight: bool = False,
    inline_far: bool = False,
    tight_cols: bool = False,
    lean: bool | str | frozenset[int] = False,
    fetch_w: int = 4,
    avoid_hi: bool = False,
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

    ``straight`` (:data:`STRAIGHT_TRIE`) draws the nodes that would otherwise force
    a blank row as ``d`` instead of ``x``. ``d`` goes **straight** on ``BP == 0``
    rather than turning, so such a node sits *on its own up child's row* and is
    that lane's entry: no ``>``, no collision, no gap row. :func:`_uneven_gaps`
    carries the proof that the two glyphs are equivalent exactly there, and this
    function additionally requires the two rows to have actually been packed
    together — a ``d`` on a blank row would send the man east along nothing.

    ``inline_far`` (:data:`HIGH_COLLECTOR`) lifts that last requirement, which is
    the *placement* half of the rule and not the correctness half. A ``d`` going
    straight is its up child's entry wherever that child sits, so anchoring it to
    ``slot_rows[up[0]]`` instead of to the gap above the down half works at any
    separation: the man it turns south simply walks further, over rows that hold no
    lane (a single-lane up half has nothing under it). It is off by default because
    at ``lane_pitch = 2`` it would fire on **every** such node and move a geometry
    two tests pin as inert; ``high_collector`` needs it because the row it opens
    above the fetch is exactly such a separation, and an ``x`` would land in it.
    See ``test_the_substitution_is_inert_at_a_pitch_of_two``.

    Returns the root entry row — the fetch row — and the cells. Opcode numbers are
    untouched: the ROM image is byte-identical to the uniform trie's.
    """
    cells: dict[tuple[int, int], str] = {}
    nodes: dict[tuple[int, int], int] = {}  # branch cells, checked below
    entry, elevel, tree, root = _trie_shape(k, slot_rows, straight, inline_far, lean)
    col_of = _trie_columns(tree, root, elevel, tight_cols, fetch_w)
    #: The high corridor's row, when the caller needs it kept free of ``]``. It is
    #: ``centre - 1`` and ``centre`` is ``entry``, which is why this is derived
    #: here rather than passed in — the caller does not know it yet.
    avoid_row = (entry - 1) if avoid_hi else None

    for i, nd in enumerate(tree):
        col, xrow, level = col_of[i], nd["row"], nd["level"]
        cells[(col, xrow)] = "d" if nd["inline"] else "x"
        nodes[(col, xrow)] = level
        for sign, crow, clevel, ci in nd["kids"]:
            shifts = 0 if clevel is None else clevel - level
            child_col = col_of[ci] if ci is not None else lane_x0
            room = child_col - col - 1  # the run east along the child's row
            if nd["inline"] and sign < 0:
                # The inline child's entry *is* the ``d``: no leg, no ``>``.
                down_shifts = 0
            else:
                # A ``]`` may stand on the leg as happily as on the run: the man
                # keeps his heading over it, and a node's column is strictly west of
                # every lane in its subtree, so nobody else ever walks these cells.
                # Only the overflow goes here, so a leg with slack stays all ``.``
                # and the two-columns-a-level layout is reproduced exactly.
                leg = list(range(xrow + sign, crow, sign))
                down_shifts = max(0, shifts - room)
                # *Where* on the leg is free: the man walks every cell of it with
                # the same heading and nothing between the node and the child
                # reads BP, so only the shift **count** is semantics. ``avoid_row``
                # (:data:`FETCH_TUCK`) spends that freedom to keep the corridor row
                # clear of ``]``, because there the shifts stop being dead — the
                # tucked prologue's ``b`` runs *east* of this cell, so a ``]`` here
                # would shift a BP that has already been loaded.
                order = sorted(
                    range(len(leg)), key=lambda j: (leg[j] == avoid_row, j)
                )
                take = set(order[:down_shifts])
                if avoid_row is not None and any(leg[j] == avoid_row for j in take):
                    raise MachineError(
                        f"fetch_tuck: the leg at column {col} owes {down_shifts} "
                        f"shift(s) into {len(leg)} cell(s), so one lands on corridor "
                        f"row {avoid_row}, where the tucked prologue's `b` is already "
                        "behind the man"
                    )
                for j, yy in enumerate(leg):
                    cells[(col, yy)] = "]" if j in take else "."
                cells[(col, crow)] = ">"
            if avoid_row is not None and crow == avoid_row and shifts - down_shifts > 0:
                # The horizontal run has no such freedom — its cells are the
                # child's own approach — so this is a hard stop rather than a
                # re-ordering. It cannot fire while the corridor row is blank of
                # lanes by construction, which is what opened it.
                raise MachineError(
                    f"fetch_tuck: a child's approach run lands `]` on corridor row "
                    f"{avoid_row}, east of the tucked prologue's `b`"
                )
            for j, cx in enumerate(range(col + 1, child_col)):
                cells[(cx, crow)] = "]" if j < shifts - down_shifts else "."

    # The approach from the fetch cell: the prologue ends at column ``fetch_w`` (4
    # for `>rbr`, 2 for the folded `>r`), the first `x` (or the lone lane) starts
    # east of it, and a contracted root still owes its shifts.
    shifts = 0 if elevel is None else elevel - 1
    end = (col_of[root] - 1) if root is not None else (lane_x0 - 1)
    for i, cx in enumerate(range(fetch_w + 1, end + 1)):
        cells[(cx, entry)] = "]" if i < shifts else "."

    # Every branch cell must still be an `x`. This is not paranoia about the code:
    # it is the one failure this trie has that the grid cannot show you. `x`
    # **always turns**, so a node's row has to lie strictly *between* its two
    # children's rows — and a node whose children are both lanes therefore needs a
    # row between two **adjacent** lanes. Squeeze the band and that node's own
    # up-lane lands on its row and overwrites the `x` with its entry `>`; every
    # opcode routed through it then walks east into the wrong lane, silently, with
    # no binding error and no collision. Nine of this program's twenty nodes are
    # such pairs, so a naive one-row band loses nine of them at once.
    lost = sorted(pos for pos in nodes if cells[pos] not in ("x", "d"))
    if lost:
        raise MachineError(
            f"{len(lost)} decode branch(es) overwritten at {lost[:4]}"
            f"{'...' if len(lost) > 4 else ''}: an `x` needs a row strictly between "
            "its two children, so a node with two lane children needs a row between "
            "two adjacent lanes. The band cannot be compacted past that."
        )
    return entry, cells


#: The empty reserved-column set, so the corridor case of the drop search reads
#: as one substitution rather than as a branch (:data:`HIGH_DROPS_FREE`).
_NO_COLS: frozenset[int] = frozenset()


def _tight_struct_entry(
    p: _Plan,
    structured: list[str],
    row_of: dict[str, int],
    struct_x0: int,
    drain_unit_bits: int,
    pitch: int = _SLAB_PITCH,
    bases: dict[str, int] | None = None,
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

    None of that mentions a slab's *body*, which is why it carries to the seek
    drum unchanged: a seek branch's arms are the same ``base + 3/6/9`` and a seek
    jump turns south somewhere in ``[base, drop_x - 1]``, both already covered.
    Slab 0's ``base - 1`` is column 1, which is also the seek tail's shared riser,
    and the tail is otherwise strictly below the band. See
    :data:`SEEK_TIGHT_STRUCT_DROPS` for what the drum *does* change — the taken
    row's eastward run, which is a price, not a collision.
    """
    order = sorted(structured, key=lambda m: -row_of[m])
    base = bases if bases is not None else {m: struct_x0 + i * pitch for i, m in enumerate(order)}
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
    drain_ops: tuple[str, ...] | None = None,
    trim_dead: bool = False,
    top_bus: bool = False,
    seek: bool = False,
    seek_taken_drop_east: bool = False,
    tight_drops: bool = False,
    tuck_drops: bool = False,
    fold_lanes: bool = False,
    slab_pitch: int = _SLAB_PITCH,
    tuck_drain: bool = False,
    lane_pitch: int = 2,
    squash_band: bool | int = False,
    straight_trie: bool = False,
    high_collector: bool = False,
    trie_slack_rows: tuple[int, ...] = (),
    tight_trie_cols: bool = False,
    lean_trie: bool | str = False,
    high_drops_free: bool = False,
    packed_band: bool = False,
    seek_jump_gap: int = 0,
    seek_jump_east: bool = False,
    seek_tail_west: int = 0,
    seek_tail_wall: bool = False,
    risers_west: bool = False,
    spill_col: int = 0,
    tight_arms: bool = False,
    tight_risers: bool = False,
    sparse_collector: bool = False,
    fetch_fold: bool = False,
    fetch_tuck: bool = False,
) -> _Cpu:
    """Lay the CPU: fetch, decode trie, lanes, structures band, return path.

    ``short_return`` lets a simple lane drop at the end of its own micro-program
    rather than east of the slab band; see the drop-column comment. It narrows the
    CPU, which ``matmul``'s STREAM wiring does not currently survive.

    ``trim_dead`` removes the unused leaf slots' rows (see :func:`_uneven_trie`);
    ``top_bus`` adds a second return bus above the band and routes each simple
    lane over whichever bus is cheaper; ``tight_drops`` walks each slab's entry
    column back to its own band (:func:`_tight_struct_entry`); ``tuck_drops`` lets a
    *simple* lane's drop sit inside another lane's ``.`` padding (:data:`TUCKED_DROPS`);
    ``fold_lanes`` runs a lane's micro-program **south down its own drop column**
    once the last band-anchored glyph is behind it (:func:`_fold_cut`,
    :data:`FOLDED_LANES`); ``slab_pitch`` narrows the staircase's step. All six
    default off/unchanged and leave the layout byte-identical; opt in per slug via
    :data:`TRIM_DEAD_LANES` / :data:`TOP_RETURN_BUS` / :data:`TIGHT_STRUCT_DROPS` /
    :data:`TUCKED_DROPS` / :data:`FOLDED_LANES` / :data:`SLAB_PITCH` — the last two
    of those reading :data:`SEEK_TIGHT_STRUCT_DROPS` / :data:`SEEK_SLAB_PITCH`
    instead while the drum is on.

    ``high_collector`` opens one blank row directly above the fetch row and runs a
    **second** collector along it, so a lane above the trie root stops there
    instead of falling past it to the collector under the band (:data:`HIGH_COLLECTOR`).

    ``fetch_fold`` moves the opcode ``r`` and the ``b`` off the fetch row and onto
    the cells the returning man already walks, leaving ``>r`` (:data:`FETCH_FOLD`).

    ``fetch_tuck`` goes one step further and moves the *operand* ``r`` off the
    fetch row too, so the corridor's whole prologue lives east of the root's
    up-leg and its U-turn onto the fetch row shrinks by a column at each end
    (:data:`FETCH_TUCK`).
    """
    if (trim_dead or top_bus) and not short_return:
        raise MachineError("trim_dead/top_bus require the short-return drop rule")
    if lane_pitch != 2:
        if not trim_dead:
            # The untrimmed band puts a lane at ``2 * slot + 1`` straight from the
            # plan, and the uniform trie's step is ``1 << (k - level)`` rows — both
            # hard-wired to the pair. Only the pruned trie derives its geometry
            # from ``slot_rows``, which is what makes a pitch a free variable.
            raise MachineError("lane_pitch requires trim_dead (the pruned trie)")
        if not 1 <= lane_pitch <= 2:
            raise MachineError(f"lane_pitch must be 1 or 2, got {lane_pitch}")
    if straight_trie and not trim_dead:
        # Only the pruned trie derives its geometry from ``slot_rows``; the uniform
        # one hard-wires a lane to ``2 * slot + 1`` and there is no packed pair for
        # a ``d`` to stand on.
        raise MachineError("straight_trie requires trim_dead (the pruned trie)")
    if tight_drops and not short_return:
        raise MachineError("tight_drops requires the short-return drop rule")
    if tuck_drops and not short_return:
        # The long path is kept verbatim so its slugs regenerate byte-for-byte; it
        # has no per-column bookkeeping and is not getting any.
        raise MachineError("tuck_drops requires the short-return drop rule")
    if fold_lanes and (not short_return or top_bus):
        # A fold puts glyphs *in* the drop column, so it needs a drop column that is
        # the lane's own end (short return) and that actually goes south. A top-bus
        # lane leaves by an ascent column assigned long after the tail would have to
        # be placed, and the rows above it are the trie's, not the drop's.
        raise MachineError("fold_lanes requires the short-return drop rule and no top bus")
    if slab_pitch < _SLAB_PITCH_FLOOR:
        raise MachineError(
            f"slab pitch {slab_pitch} is below the {_SLAB_PITCH_FLOOR}-column span a "
            "branch slab occupies (`base - 1` .. `base + 9`); slabs would overlap"
        )
    if tight_drops and seek and not seek_taken_drop_east:
        # A *seek* jump slab has no body: its entry row is one `<` run and a turn
        # south, and the column it turns at is chosen at draw time — ``base``
        # without :data:`SEEK_TAKEN_DROP_EAST`, and the last column west of the
        # request `s` with it. Tightening the entry column pulls that turn west
        # too, and at ``base`` the man then walks the *taken* row back east from
        # column 2 to the send site, which is the whole slab band again with the
        # sign flipped. The two knobs are one knob: see
        # :data:`SEEK_TIGHT_STRUCT_DROPS`.
        raise MachineError("tight_drops under the seek drum requires seek_taken_drop_east")
    if high_collector:
        if not trim_dead or lane_pitch != 1:
            # The corridor is paid for out of the stagger's slack and is placed by
            # rank, both of which only exist under the pruned, pitch-1 band.
            raise MachineError("high_collector requires trim_dead and lane_pitch 1")
        if top_bus:
            # Both want column 1 above the fetch row: the bus to descend into it,
            # the corridor to drop into it. Two headings, one cell.
            raise MachineError("high_collector and top_bus both own column 1 above the fetch")
    if fetch_fold:
        if not trim_dead or not tight_trie_cols:
            # The fold's whole payoff is the two columns it takes off ``lane_x0``,
            # and ``lane_x0`` is only a function of the root's column under the
            # pruned trie's per-node rule. Under the uniform ``3 + 2 * level`` rule
            # the tree is anchored to the level, not to the prologue.
            raise MachineError("fetch_fold requires trim_dead and tight_trie_cols")
        if top_bus:
            # The fold's riser copy owns column 1 *below* the fetch and the corridor
            # copy owns the corridor row; a top bus re-routes the return past both.
            raise MachineError("fetch_fold and top_bus disagree about the return path")
    if fetch_tuck and not (fetch_fold and high_collector):
        # The tuck is a *corridor* optimisation: it shortens the U-turn the
        # westbound man makes around the root's up-leg, and there is no such man
        # without the high corridor. It is expressed as a delta on the fold
        # because the fold is what emptied the fetch row down to two columns.
        raise MachineError("fetch_tuck requires fetch_fold and high_collector")
    #: The prologue's width: how many columns the fetch row spends before the trie
    #: may begin. ``>rbr`` is 4; the fold leaves ``>r`` and is 2, and the tuck
    #: leaves ``>>`` — still 2, because the root's column is what fetch_w buys and
    #: the tuck does not move it.
    fetch_w = 2 if fetch_fold else 4
    k, lanes = p.k, p.lanes
    used = list(p.number)
    bus_row = 1
    y0 = 2 if top_bus else 1  # with a top bus, row 1 belongs to the bus
    pitch = lane_pitch if trim_dead else 2
    if trim_dead:
        slots = sorted((p.row[m] - 1) // 2 for m in used)
        rank = {s: i for i, s in enumerate(slots)}
        n_rows = len(slots)
        if pitch == 1:
            # Staggered: lanes sit one row apart, and a second row goes in only
            # where the ``x`` splitting the pair has nowhere else to stand. See
            # :func:`_uneven_gaps` — most nodes can share the lane row above them,
            # because a node is always strictly west of the lanes in its subtree.
            gaps = _uneven_gaps(k, slots, straight_trie)
            at = [y0]
            for i in range(n_rows - 1):
                at.append(at[-1] + (2 if i in gaps else 1))
            if high_collector:
                # One blank row, opened where the *second* return corridor wants to
                # run: directly above the lane the root's ``x`` shares a row with.
                #
                # The root splits at ``1 << (k - 1)``, and its ``x`` stands on the
                # row above the down half's first lane — which, at pitch 1, is the
                # last **up**-half lane's row. So rank ``n_up - 1`` is the fetch row
                # and ranks ``0 .. n_up - 2`` are strictly above it: exactly the
                # lanes that today fall past the fetch to the collector under the
                # band and then climb back up. Opening the row between ranks
                # ``n_up - 2`` and ``n_up - 1`` puts the corridor one row above the
                # fetch and serves every one of them.
                #
                # It is taken out of the **stagger's slack**, not out of the room:
                # ``slack`` below shrinks by one and the band starts one row higher,
                # so the collector, the structures band and the room's height do not
                # move, and neither does anything anchored to them.
                n_up = sum(1 for s in slots if s < (1 << (k - 1)))
                if not 2 <= n_up < n_rows:
                    raise MachineError(
                        f"high_collector needs lanes on both sides of the root split "
                        f"and at least two above it; {n_up} of {n_rows} are above"
                    )
                at = [r + (1 if i >= n_up - 1 else 0) for i, r in enumerate(at)]
            if trie_slack_rows:
                # :data:`TRIE_SLACK_ROWS` — a blank row opened between two lanes
                # purely to give a decode **edge** vertical slack.
                #
                # ``_trie_columns`` puts a child at ``parent + 1 + max(0, d -
                # slack)`` with ``slack = |crow - prow| - 1``, so an edge whose two
                # nodes sit on adjacent rows pays an extra column — and that column
                # is on ``lane_x0``, hence on the trie walk, every drop and every
                # metre of the walk back west. At pitch 1 the deepest nodes are
                # pinned one row apart by construction (a non-inline node stands at
                # ``slot_rows[min(down half)] - 1``), so no *lean* can open that
                # edge: :data:`LEAN_TRIE` only moves a node toward its parent.
                # Moving the **lanes** can, and this is the only lever that does.
                #
                # It is paid out of the same stagger slack :data:`HIGH_COLLECTOR`
                # spends, and by the same mechanism: ``slack`` below shrinks by one
                # per row opened, the band starts one row higher, and the collector,
                # the structures band and the room's height do not move.
                at = [
                    r + sum(1 for j in trie_slack_rows if j < i)
                    for i, r in enumerate(at)
                ]
            # **Bottom-align it.** The rows the stagger saves are left blank above
            # the band instead of being taken out of the room, so the collector,
            # the whole structures band below it and the room's own height all
            # stay exactly where they were — and so does every block placed
            # against them. Nothing outside the CPU has to move for this.
            #
            # It costs nothing that matters: the win is the *vertical travel*
            # inside the band, and all three terms measure from the collector —
            # the drop is `collector - 1 - row`, the riser is `collector - centre`
            # and the trie descent is the band's own height. Shrinking the room
            # as well was tried and is a false economy: it collides with the
            # store, and chasing it with a store offset only re-breaks whichever
            # counterfactual build has a different-shaped block.
            # ``squash_band`` is a **row count**, not a flag, because the choice is
            # not all-or-nothing. ``False``/0 shifts by the whole slack (the
            # bottom-aligned default above); ``True`` shifts by none, taking every
            # row out of the room; an ``int`` takes exactly that many and leaves
            # the rest blank above the band.
            #
            # The partial case is the one that matters: on ``deadman-3d_hires`` a
            # full squash moves the STREAM unit north with ``CY + H`` and leaves
            # ``_seek_teleport``'s room H two rows short of its four-row minimum,
            # while the store — anchored to ``CY`` — does not move to follow. Two
            # rows handed back is the difference between a build and a
            # ``MachineError``, and there was no way to say that with a bool.
            slack = (2 * n_rows - 1) - (at[-1] - y0 + 1)
            if squash_band is True:
                take = slack
            elif not squash_band:
                take = 0
            else:
                take = min(int(squash_band), slack)
            if take < slack:
                at = [r + (slack - take) for r in at]
        else:
            at = [y0 + 2 * i for i in range(n_rows)]
        row_of = {m: at[rank[(p.row[m] - 1) // 2]] for m in used}
        lane_x0 = fetch_w + 2 * k  # two columns per trie level (see _uneven_trie)
    else:
        row_of = {m: p.row[m] + (y0 - 1) for m in used}
        n_rows = lanes
        lane_x0 = fetch_w + 1 + k
        at = [y0 + 2 * i for i in range(n_rows)]
    by_row = {row_of[m]: m for m in used}
    all_rows = list(at)
    if trim_dead:
        slot_rows = {(p.row[m] - 1) // 2: row_of[m] for m in used}
        lean_mode: bool | str | frozenset[int] = bool(lean_trie)
        if tight_trie_cols:
            # ``lane_x0`` is the deepest node's column plus one, and with per-node
            # columns that is no longer ``4 + 2k`` — so the trie has to be *shaped*
            # before the band's origin is known. Nothing above reads ``lane_x0``.
            #
            # It is also the only place that can price :data:`LEAN_TRIE`'s greedy
            # form, which buys a row per node and sometimes spends a column doing
            # it. ``lane_x0`` is the one number that decides whether that column is
            # one anybody pays for, and this pre-pass is what computes it.
            def _shape(mode: bool | str | frozenset[int]) -> tuple[int, int]:
                _e, _el, _tree, _root = _trie_shape(
                    k, slot_rows, straight_trie, high_collector, mode
                )
                cols = _trie_columns(_tree, _root, _el, True, fetch_w)
                return max(cols.values(), default=fetch_w) + 1, len(_tree)

            lane_x0, n_nodes = _shape(bool(lean_trie))
            if lean_trie == "greedy":
                # So ask node by node, and keep only the leans the band's origin
                # does not notice. Accepting one lean never makes another worse —
                # ``_lean_row`` reads only which *side* the parent is on, never
                # where it ended up — so a single forward pass is a fixpoint, and
                # it is twenty-odd shapes of a pure function, once per build.
                picked: frozenset[int] = frozenset()
                for i in range(n_nodes):
                    trial = picked | {i}
                    if _shape(trial)[0] <= lane_x0:
                        picked = trial
                if picked:
                    lean_mode = picked
        centre, trie_cells = _uneven_trie(
            k,
            slot_rows,
            lane_x0,
            straight_trie,
            inline_far=high_collector,
            tight_cols=tight_trie_cols,
            lean=lean_mode,
            fetch_w=fetch_w,
            avoid_hi=fetch_tuck,
        )
    else:
        centre, trie_cells = (1 << k) + (y0 - 1), None

    #: The high corridor's row, or ``None``. It is the blank row opened above the
    #: fetch, so every lane strictly above it returns there instead of at the
    #: collector: ``(hi_row - r) + (centre - hi_row)`` is ``centre - r``, the
    #: Manhattan distance, against ``(collector - r) + (collector - centre)``.
    hi_row = centre - 1 if high_collector else None
    if hi_row is not None and (hi_row in set(all_rows) or hi_row <= y0):
        raise MachineError(
            f"high_collector: row {hi_row} above the fetch is not blank — the row "
            "the corridor was opened for is not the row the trie root chose"
        )

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

    # SPILL: its own column, and the column is the whole design freedom the block
    # has. Both spill glyphs are the *first* band glyph of their lane, so without
    # this they would sit at ``lane_x0`` — hard against the west wall, where the
    # ROM pipe and the input pipe both beat anything hanging off the east one.
    # ``spill_col`` walks them east exactly as ``mem_pad`` walks the memory block,
    # and the cells it walks over are `.` padding, so the cost is a couple of
    # ticks of walking per PUSH/POP against a store round trip.
    if any(b == Band.SPILL for mc in flat.values() for _, b in mc):
        band_x[Band.SPILL] = lane_x0 + spill_col

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
    if seek:
        # Hybrid: a *seek* slab is shallow (a drop to the taken row, plus the
        # branch's X fan-out); a *classic* slab keeps its counted discard, which
        # short jumps are already good at. Bases start at 5 because the seek
        # tail owns columns 1..4 (flush loop, remainder read, its discard).
        #
        # ``drain_unit_bits`` used to be refused outright here — "seek mode
        # replaces the discard entirely; no drain to size". That is true of the
        # *seek* slabs and false of the classic ones, which still run
        # :func:`_discard_loop` at four ticks a word and are the only part of the
        # discard pool that never blocks. Sizing them like the classic build does
        # is the whole of the fix; :func:`_slab` already takes the parameter and
        # already routes it on both the jump and the branch path.
        _drain_h = 0
        if drain_unit_bits:
            from .drain import build_drain

            _drain_h = build_drain(0, unit_bits=drain_unit_bits, even=True).height
        slab_rows = {}
        for m in structured:
            if p.sem[m] in _SEEK_SEMS:
                slab_rows[m] = 2 if p.sem[m] in _JUMP_SEMS else 5
            elif _drained(m, drain_unit_bits, drain_ops):
                slab_rows[m] = (
                    1 if p.sem[m] in _JUMP_SEMS else (3 if tuck_drain else 5)
                ) + _drain_h
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
        slab_rows = {
            m: (1 if p.sem[m] in _JUMP_SEMS else (3 if tuck_drain else 5)) + _drain_h
            for m in structured
        }
        struct_x0 = _STRUCT_X0
    else:
        slab_rows = {
            m: (_JUMP_SLAB_ROWS if p.sem[m] in _JUMP_SEMS else _BRANCH_SLAB_ROWS)
            for m in structured
        }
        struct_x0 = _STRUCT_X0
    # The staircase, solved before the drops because ``struct_east`` is a floor for
    # them (and, under ``tight_drops``, each slab's own base is). ``band_order`` is
    # the order the *rows* imply; the drop solver re-derives it from the columns it
    # picks and the two are checked against each other below.
    band_order = sorted(structured, key=lambda m: -row_of[m])
    band_base, struct_east = _slab_bases(
        p,
        band_order,
        struct_x0,
        slab_pitch,
        packed=packed_band,
        drain_unit_bits=drain_unit_bits,
        drain_ops=drain_ops,
        jump_east=seek_taken_drop_east,
        seek_jump_gap=seek_jump_gap,
        tight_arms=tight_arms,
        tight_risers=tight_risers,
        tuck_drain=tuck_drain,
    )

    # ── lane extents ─────────────────────────────────────────────────────────
    lane_cells: dict[tuple[int, int], tuple[str, str | None]] = {}
    lane_end: dict[int, int] = {}
    #: Per row, the glyphs :func:`_fold_cut` took off the row. They are placed once
    #: the drop column is known — some back on the row, in the ``.`` padding the old
    #: layout wasted, and the rest down the drop itself.
    lane_tail: dict[int, tuple[tuple[str, str | None], ...]] = {}
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            lane_end[r] = lane_x0 - 1
        elif m in flat:
            micro = flat[m]
            if fold_lanes and r not in halting:
                cut = _fold_cut(micro)
                micro, tail = micro[:cut], micro[cut:]
                if tail:
                    lane_tail[r] = tail
            cells = _flat_lane(micro, lane_x0, band_x, r)
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

    #: Per row, the columns that carry an **operation** — everything a lane draws
    #: except ``.``. A lane's cells are not all operations: :func:`_flat_lane` pads
    #: with ``.`` while pushing a band's first glyph out to its column, so a lane
    #: whose memory block sits at ``mem_x`` has a run of a dozen inert cells in the
    #: middle of it. This is what :data:`TUCKED_DROPS` reads instead of ``lane_end``.
    lane_ops: dict[int, set[int]] = {r: set() for r in all_rows}
    for (x, yy), (glyph, _band) in lane_cells.items():
        if glyph != ".":
            lane_ops[yy].add(x)

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

    lane_rows = set(all_rows)

    #: Is every slab lane **below** the high corridor? :data:`HIGH_DROPS_FREE`
    #: needs it: it lets a corridor-bound drop share a slab's entry column, and
    #: the argument for that is that the two occupy *disjoint rows* of the column
    #: (the simple drop stops at ``hi_row``, the slab's starts below it). A slab
    #: lane above the corridor would break exactly that, so the knob turns itself
    #: off rather than mis-wire.
    hi_free = high_drops_free and hi_row is not None and all(
        row_of[m] > hi_row for m in structured
    )

    def _stop(r: int, m: str | None) -> int:
        """The row a lane's drop ends on: its slab, the high corridor, or the collector."""
        if m is not None and m in slab_rows:
            return collector  # a slab lane falls past both corridors; see ``slab_at``
        if hi_row is not None and r < hi_row:
            return hi_row
        return collector

    def _bump(c: int, struct_cols: set[int], blocked: set[int], assigned: set[int]) -> int:
        """The first column at or east of ``c`` the drop discipline still allows."""
        while (
            c in struct_cols
            or (tuck_drops and c in blocked)
            or (not tight_drops and c > struct_east and c in assigned)
        ):
            c += 1
        return c

    def _room(c: int, down: int, r: int) -> bool:
        """Can ``down`` folded glyphs stand in column ``c`` on the rows below ``r``?

        A folded glyph is an **operation** sitting on somebody else's row, so the
        test is not "is the cell empty" but "does any other man ever step here".
        Three ways he could, and all three are refused:

        * the tail must stop above the row this lane's own drop ends on — the
          collector, or the high corridor when there is one — which every man walks;
        * the cell must not already carry an operation — another lane's glyph, or
          another lane's fold. Free under the default rule, where ``c`` is a suffix
          maximum of ``lane_end``, and not free under ``tuck_drops``, where it is
          not;
        * a lane crossing ``c`` on its own row would execute the glyph, so that
          lane's ``drop_x`` must be strictly **west** of ``c`` — he turned south
          before he got there. Equality is the same failure at the ``v`` itself, and
          a lane with no drop at all (halting, top bus) is refused outright rather
          than reasoned about. Rows with no lane on them are nobody's walk.
        """
        if r + down >= _stop(r, by_row.get(r)):
            return False
        for yy in range(r + 1, r + 1 + down):
            if c in lane_ops.get(yy, ()):
                return False
            if yy in lane_rows and drop_x.get(yy, c) >= c:
                return False
        return True

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
        _tight_struct_entry(
            p, structured, row_of, struct_x0, drain_unit_bits, slab_pitch, band_base
        )
        if tight_drops
        else ({}, frozenset())
    )
    if short_return:
        floor = lane_x0
        struct_min = lane_x0
        #: The same suffix walk as ``floor``, kept per column instead of as one
        #: number: every column at or below the current row that carries an
        #: operation. ``floor`` is its coarse envelope — ``max(blocked) + 1`` — and
        #: the gap between them is what :data:`TUCKED_DROPS` recovers.
        blocked: set[int] = set()
        #: ``blocked``, restarted at the high corridor. A drop that stops on
        #: ``hi_row`` crosses only the rows between its lane and the corridor, so
        #: the whole band *below* the corridor — which includes the longest lanes
        #: and every slab — is not in its way and has no business setting its
        #: column. See :data:`HIGH_DROPS_FREE`.
        hi_blocked: set[int] = set()
        for r in sorted(all_rows, reverse=True):
            # Halting rows carry no drop but do carry glyphs, so they still raise the
            # floor for everything above them — as do top-bus lanes, whose return
            # leaves by the ascent column assigned after the drops.
            floor = max(floor, lane_end[r] + 1)
            blocked |= lane_ops[r]
            if hi_free and r < hi_row:
                hi_blocked |= lane_ops[r]
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
                #
                # ``tuck_drops`` keeps that argument and drops only the scalar: the
                # thing a descent may not cross is an *operation*, and ``floor`` is
                # a suffix maximum of last **cells**, which is a strictly weaker
                # statement. See :data:`TUCKED_DROPS` — the column still has to
                # clear the lane's own micro-program (``lane_end[r] + 1``, the man
                # walks over every cell of it) and every operation below it, which
                # is exactly ``blocked``.
                #
                # ``lane_tail`` is the vertical fold (:func:`_fold_cut`): the glyphs
                # this lane took off its row, to be put back once the column is
                # known — as many as the ``.`` padding west of it will hold, and the
                # remainder straight down the drop, on cells the man was going to
                # walk anyway.
                #
                # A tail in the column makes that column **exclusive**, which is the
                # fold's whole price: a second lane's drop crossing it would execute
                # this lane's tail, and this lane's own ``v`` may not stand on a
                # neighbour's. So a folded lane refuses any row below whose drop
                # already took ``c``, and leaves the floor at ``c + 1`` rather than
                # at ``c``, where an unfolded lane's would let the columns merge.
                #
                # That price is exactly one column, so the fold is taken **only when
                # it saves at least one** — ``c_fold < c_plain`` below. At a saving
                # of one the lanes above break even (their floor is ``c_fold + 1 ==
                # c_plain``) and this lane gains; at two or more everybody gains; at
                # zero it would be a pure loss, and the glyphs go back on the row.
                tail = lane_tail.get(r, ())
                plain_end = lane_end[r] + len(tail)
                c_plain = plain_end + 1 if tuck_drops else max(floor, plain_end + 1)
                # A lane the corridor catches is asked a strictly weaker question:
                # no slab column is reserved against it and only the rows it
                # actually crosses block it (:data:`HIGH_DROPS_FREE`).
                catch = hi_free and r < hi_row
                cols_, blk_ = (
                    (_NO_COLS, hi_blocked) if catch else (struct_cols, blocked)
                )
                c = _bump(
                    (lane_end[r] + 1) if tuck_drops else floor, cols_, blk_, assigned
                )
                while c < c_plain:
                    down = max(0, len(tail) - max(0, c - lane_end[r] - 1))
                    if _room(c, down, r):
                        break
                    c = _bump(c + 1, cols_, blk_, assigned)
                if c >= c_plain:
                    c = _bump(c_plain, cols_, blk_, assigned)
                if tail:
                    # The row holds whatever fits west of the turn; the rest goes
                    # down. Either the search above proved this exact column has the
                    # room, or it fell through to ``c_plain``, where nothing has to.
                    across = min(len(tail), max(0, c - lane_end[r] - 1))
                    down = len(tail) - across
                    if down and not _room(c, down, r):
                        raise MachineError(
                            f"lane fold at row {r}: {down} glyph(s) cannot stand in "
                            f"column {c} — another man walks those cells"
                        )
                    for i, cell in enumerate(tail[:across]):
                        lane_cells[(lane_end[r] + 1 + i, r)] = cell
                        lane_ops[r].add(lane_end[r] + 1 + i)
                    for i, cell in enumerate(tail[across:]):
                        lane_cells[(c, r + 1 + i)] = cell
                        lane_ops.setdefault(r + 1 + i, set()).add(c)
                    lane_end[r] += across
                    blocked |= lane_ops[r]
                    if catch:
                        hi_blocked |= lane_ops[r]
                    # The lane's own cells now run to ``lane_end``, and a vertical
                    # tail additionally owns ``c`` on every row it occupies.
                    floor = max(floor, lane_end[r] + 1)
                    if down:
                        floor = max(floor, c + 1)
                        blocked.add(c)
                        if catch:
                            hi_blocked.add(c)
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
    #
    # **Reordering the band by frequency is not the lever it looks like, and the
    # reason is that the entry cost is a function of the *drop*, not the base.**
    # The three legs telescope (:data:`TIGHT_STRUCT_DROPS`) to
    # ``2 * drop_x - lane_x0 - 2``: the slab's own column cancels, so where a slab
    # sits in the staircase is free and only which column its drop got is not.
    # Reordering is therefore a permutation of the *available* columns, and on
    # hires/men-v3 there are exactly four of them — 24, 25, 28, 29 — with 26 and 27
    # spoken for by ``BRZ``'s ``neg`` arm and ``BRN``'s exit riser whatever the
    # order, and nothing below 24 reachable because ``floor`` (the lane band's east
    # envelope at the deepest lane row) is 24. Best case, handing the two branches
    # the two cheapest columns instead of the two dearest, is
    # ``2 * (5 * 59,670 + 3 * 57,307 - 3 * 36,094)`` = 723,978 ticks, **0.82%** —
    # and it needs the *lane* rows reordered, since ``struct_min`` below ties the
    # drop order to the row order, which drags in the trie, ``OPCODE_SLOTS`` and
    # every ROM word. Not attempted; recorded so the size is known before anyone
    # spends the coupling on it.
    #
    # Note also that ``BRN`` sitting easternmost — flagged as the open question on
    # :data:`SEEK_CLASSIC_DRAIN_OPS` back when the drops were floored at
    # ``struct_east + 1`` — is now the *cheap* end, not the dear one. Under tight
    # drops the easternmost slab's drop lands one column past its own base, so
    # ``cpu:entry:BRN`` is 0.13 t/instr against ``cpu:entry:BRZ``'s 0.84. The
    # question answered itself when the drops moved west.
    order = sorted(structured, key=lambda m: drop_x[row_of[m]])
    if (tight_drops or packed_band) and order != band_order:
        # ``tight_first`` priced each slab against the base this order gives it, so a
        # disagreement means the two disciplines have drifted and the entry columns
        # would be measured against the wrong slabs. Fail rather than mis-wire.
        # ``packed_band`` needs the same agreement for a stronger reason: its step is
        # a function of *which* slab sits where, so a reordering would put a branch
        # on a jump's three columns.
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
    # ``slab_base`` is ``band_base``, solved with ``struct_east`` above: under
    # ``packed_band`` the step is per-slab (:func:`_slab_east_span`) rather than the
    # uniform ``slab_pitch``, so it cannot be re-derived from ``i`` here.
    for i, m in enumerate(order):
        slab_at[m] = collector + 1 + i
        slab_base[m] = band_base[m]
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
    #
    # ``fetch_fold`` keeps the ``r b r`` order and keeps both reads; it only moves
    # the first two glyphs *earlier along the same walk*, onto the approach the man
    # was making anyway. What is left on the fetch row is ``>r`` — the arrival turn
    # and the operand — so the trie root starts at column 3 instead of column 5.
    # See :data:`FETCH_FOLD`; the two copies of the prologue are drawn with the
    # return paths below, because that is where the cells they stand on are.
    #
    # ``fetch_tuck`` empties the row completely: both ``>``s are pure steering,
    # the corridor's man turns south onto column 2 and the riser's onto column 1,
    # and each carries his own ``r b r`` on cells he already walked. What the row
    # then costs is only the two columns west of the root, which is what
    # ``fetch_w`` already charged.
    with g.part("fetch"):
        g.text(1, centre, ">>" if fetch_tuck else (">r" if fetch_fold else ">rbr"))
    if not fetch_tuck:
        pipe_glyphs += (
            [(2, centre, "r", "rom")]
            if fetch_fold
            else [(2, centre, "r", "rom"), (4, centre, "r", "rom")]
        )

    # ── decode trie: one `x` per level, `]` shifting BP on each branch ────────
    def trie(level: int, row: int) -> None:
        col, step = 4 + level, 1 << (k - level)
        g.put(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                g.put(col, row + sign * d, ">" if d == step else ("]" if d == 1 else "."))
            if level < k:
                trie(level + 1, row + sign * step)

    with g.part("trie"):
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
        # Without ``trim_dead`` an unused leaf slot still gets a row, a trie
        # terminal `>` and a run out to a drop column — live glyphs no opcode can
        # reach. They have no mnemonic and so no ``lane:`` box; naming them keeps
        # the map total, and a region that never takes a sample is itself the
        # finding (see :func:`_uneven_trie` and :data:`TRIM_DEAD_LANES`).
        with g.part(None if by_row.get(r) else "lanes:unused"):
            for x in range(lane_end[r] + 1, (asc_x[r] if r in top_lanes else drop_x[r])):
                g.soft(x, r, ".")

    # ── drops: simple lanes to the collector, structured ones to their slab ──
    # Only the *head* of a drop is a `v`; the rest is `.`. A southbound man keeps
    # his heading over a `.`, and so does a westbound one — which is what lets a
    # drop cross a slab's westbound entry row at all. A `v` there would turn the
    # entry man south into the middle of the drop.
    #
    # ``drops`` is deliberately one *band* rather than a box per column. A drop
    # crosses every lane row between its own and the collector, so a per-column
    # box would be smaller than the lane boxes it crosses and would take their
    # cells away from them — the crossing is genuinely shared and the lane is the
    # more useful reading. The band's area beats every lane, slab and collector
    # box, so it wins only where nothing tighter claims the cell.
    with g.part("drops"):
        for r in all_rows:
            if r in halting or r in top_lanes:
                continue
            g.put(drop_x[r], r, "v")
        for r in all_rows:
            if r in halting or r in top_lanes:
                continue
            m = by_row.get(r)
            stop = slab_at[m] if (m is not None and m in slab_at) else _stop(r, m)
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
        # :data:`SEEK_TAIL_WEST` walks the whole tail — this turn, its drop, the
        # taken row's ``> s v`` and the flush walk's ``<`` — the same number of
        # columns west into the span ``_slab_east_span`` reserves but nobody draws
        # on. Moving the turn alone is worth nothing (the man walks straight back
        # east to a stationary ``s``), so ``e_s`` below carries the same offset.
        tail_west = seek_tail_west if seek_taken_drop_east else 0
        jump_x = struct_east + 1 - tail_west if seek_taken_drop_east else None
        for m in order:
            s0, base = slab_at[m], slab_base[m]
            if p.sem[m] not in _SEEK_SEMS:
                # A short jump keeps the classic counted discard, verbatim —
                # unless this mnemonic is one the registry drains (see
                # :data:`SEEK_CLASSIC_DRAIN`). The choice is per-mnemonic because
                # the drain's own `r` cells are what decide §7.1: the easternmost
                # classic slab's block reaches far enough east to tie 'rom'
                # against 'mem_resp', and the western ones do not.
                with g.part(f"slab:{m}"):
                    struct_drops |= _slab(
                        g, m, p, s0, base, collector, pipe_glyphs,
                        drain_unit_bits if _drained(m, drain_unit_bits, drain_ops) else 0,
                        tight_arms=tight_arms,
                        tight_risers=tight_risers or risers_west,
                        tuck_drain=tuck_drain,
                    )
            elif p.sem[m] in _JUMP_SEMS:
                # never west of ``base`` (that is the shipped column and the floor),
                # never east of the lane's own drop (its `<` owns that cell).
                #
                # And never *through* another slab. The comment above is only true
                # while ``jump_x`` itself is reachable: cap it at ``drop_x - 1`` and
                # the column lands wherever that lane's drop happened to, which at
                # the shipped pitch is a gap between two slabs and under
                # :data:`PACKED_SLAB_BAND` is not — the band closes up under the
                # drop and the descent runs straight down a neighbour's exit riser
                # (``collision at (17, 46): '.' vs '^'``). The descent spans the
                # entry row to the taken row, which is *below* every slab's body, so
                # every other slab's columns are forbidden and not only the deeper
                # ones. Walk west to the first column that is nobody's; ``base`` is
                # always one, because a seek jump draws nothing there.
                # :data:`SEEK_JUMP_EAST` drops the ``drop_x - 1`` cap entirely and
                # leaves the entry row heading east instead. The cap exists so the
                # lane's own ``<`` keeps its cell, which only matters while the man
                # is walking west past it; going east he turns *after* it, so the
                # only real constraint is that the column belong to nobody, and
                # ``struct_east + 1`` belongs to nobody by construction.
                if seek_jump_east and jump_x is not None:
                    jx = jump_x
                else:
                    jx = base if jump_x is None else max(base, min(jump_x, drop_x[row_of[m]] - 1))
                if jx != base:
                    busy: set[int] = set()
                    for other in order:
                        if other is m:
                            continue
                        b2 = slab_base[other]
                        busy |= set(
                            range(
                                b2 - 1,
                                b2
                                + 1
                                + _slab_east_span(
                                    p,
                                    other,
                                    drain_unit_bits,
                                    drain_ops,
                                    seek_taken_drop_east,
                                    seek_jump_gap,
                                    tight_arms,
                                    tight_risers,
                                ),
                            )
                        )
                    if seek_jump_east:
                        # Going east there is nowhere to retreat to — ``e_s`` is the
                        # next column but one and the taken row's ``s`` owns it. If
                        # ``struct_east + 1`` is somebody's, say so rather than
                        # sliding into a column that silently belongs to a slab.
                        # ``busy`` is the *reserved* span, not the drawn one, and
                        # :data:`SEEK_TAIL_WEST` exists precisely to spend the
                        # difference. With the knob on, the guard is the per-cell
                        # occupancy test below plus the collision a deeper slab's
                        # own ``put`` raises when it lands on this drop's ``.`` —
                        # the seek jump is drawn first, so every later slab that
                        # really wants the column says so.
                        if jx in busy and not tail_west:
                            raise MachineError(
                                f"seek jump east column {jx} is inside another slab's span"
                            )
                    else:
                        while jx > base and jx in busy:
                            jx -= 1
                with g.part(f"slab:{m}"):
                    for yy in range(s0 + 1, taken_row):
                        if jx != base and (jx, yy) in g.c:
                            raise MachineError(
                                f"seek jump drop column {jx} is occupied at y={yy}"
                            )
                        g.soft(jx, yy, ".")
                taken_drops.append(jx)
                turn_x[m] = jx
            else:
                with g.part(f"slab:{m}"):
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
                            # The not-taken arms rise past the slab band to the
                            # collector — their own part, or the slab's box would
                            # stretch up over every shallower slab's rows.
                            with g.part(f"riser:{m}", exclusive=True):
                                g.put(arm_cols[arm], row, "^")
                                for yy in range(collector + 1, row):
                                    g.soft(arm_cols[arm], yy, ".")
                            struct_drops.add(arm_cols[arm])
        for m in order:
            s0, dx = slab_at[m], drop_x[row_of[m]]
            base = slab_base[m]
            with g.part(f"entry:{m}"):
                turn = turn_x.get(m, base)
                east = turn > dx
                g.put(dx, s0, ">" if east else "<")
                if p.sem[m] not in _SEEK_SEMS and p.sem[m] in _JUMP_SEMS:
                    # the classic discard loop owns `a<` at base..base+1
                    for x in range(base + 2, dx):
                        g.soft(x, s0, "<")
                elif east:
                    # :data:`SEEK_JUMP_EAST`: the man lands on the entry row at the
                    # lane's drop column and continues **east** to a turn past every
                    # slab, so the taken row's leg east to the request `s` is one
                    # cell instead of the whole band's width.
                    for x in range(dx + 1, turn):
                        g.soft(x, s0, ">")
                    g.put(turn, s0, "v")
                else:
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
        e_s = struct_east + 2 - tail_west
        with g.part("seek:taken"):
            for col in taken_drops:
                g.put(col, t, ">")
            for x in range(min(taken_drops), e_s):
                g.soft(x, t, ".")
        with g.part("seek:send"):
            emit(e_s, t, "s", "cmd")
            g.put(e_s + 1, t, "v")
        # The westbound corridor back to the flush loop's column. This is the walk
        # whose cost the drum's seek latency hides — see :data:`SEEK_DRUM`.
        with g.part("seek:walk"):
            g.put(e_s + 1, t + 1, "<")
            for x in range(4, e_s + 1):
                g.soft(x, t + 1, ".")
        # flush loop: `r` then a sign `X`, both walked southbound. The `v` at the
        # corridor's west end is the loop's *top*, not the walk's last cell — the
        # man re-enters it once a lap and crosses it once on the way in — so it
        # belongs here, and keeping the two boxes column-disjoint is what stops a
        # 5x5 flush box from claiming the first four cells of a 47-cell walk.
        with g.part("seek:flush"):
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
        with g.part("seek:sentinel"):
            emit(4, t + 3, "r", "rom")
            g.put(5, t + 3, "b")
            g.put(6, t + 3, "v")
            g.put(6, t + 4, ".")
            g.put(6, t + 5, "<")
            g.soft(5, t + 5, ".")
            g.soft(4, t + 5, ".")
        with g.part("seek:discard"):
            _discard_loop(g, 2, t + 5, pipe_glyphs)
        # the loop leaves westbound with BP == 0; rise column 1 to the collector
        with g.part("seek:riser"):
            g.put(1, t + 5, "^")
            for yy in range(collector + 1, t + 5):
                g.soft(1, yy, ".")
    else:
        for m in order:
            with g.part(f"slab:{m}"):
                struct_drops |= _slab(
                    g, m, p, slab_at[m], slab_base[m], collector, pipe_glyphs,
                    drain_unit_bits, tight_arms=tight_arms,
                    tight_risers=tight_risers or risers_west,
                    tuck_drain=tuck_drain,
                )

        # Entry rows, drawn last: `soft` leaves every crossing drop's `.` in place
        # and only fills the genuinely free cells with `<`.
        for m in order:
            s0, dx = slab_at[m], drop_x[row_of[m]]
            base = slab_base[m]
            with g.part(f"entry:{m}"):
                g.put(dx, s0, "<")
                if p.sem[m] in _JUMP_SEMS:
                    # The compact discard loop owns `a<` at base..base+1 and is
                    # entered directly from the westbound slab-entry corridor.
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
        # The taken row's send site + its `v` — the floor that *is* the CPU's east
        # wall on a drum machine. Offset by :data:`SEEK_TAIL_WEST` only under
        # :data:`SEEK_TAIL_WALL`: without it the tail walks into space the return
        # path keeps owning and the box does not move, which is what makes the
        # tail move safe to land on its own.
        ret_x = max(ret_x, struct_east + 3 - (tail_west if seek_tail_wall else 0))
    #: The columns a man ever *arrives* on this row, as opposed to merely walks. Two
    #: kinds, and they approach from opposite directions:
    #:
    #: * a **simple lane's drop**, southbound, landing here because this is where its
    #:   descent stops (:func:`_stop`). A slab lane is not one of these — its drop
    #:   falls straight through to ``slab_at`` and needs the ``.`` it already wrote;
    #: * a **slab's riser**, northbound, one per exit column and one per not-taken
    #:   branch arm — exactly what :func:`_slab` hands back in ``struct_drops``.
    #:
    #: Plus column 3, which is not an arrival but the spawn's first step: ``@`` at 2
    #: faces east, so the cell beside him has to turn him back.
    #:
    #: Everything between those is a cell a westbound man crosses, and a westbound
    #: man keeps his heading over a ``.``. See :data:`SPARSE_COLLECTOR`.
    arrivals = set(struct_drops) | {3}
    for r in all_rows:
        if r in halting or r in top_lanes:
            continue
        mm = by_row.get(r)
        if mm is not None and mm in slab_at:
            continue
        if _stop(r, mm) == collector:
            arrivals.add(drop_x[r])
    with g.part("return:collector"):
        for x in range(3, ret_x + 1):
            g.soft(x, collector, "<" if (x in arrivals or not sparse_collector) else ".")
    with g.part("return:riser"):
        g.put(1, collector, "^")
        if fetch_fold:
            # The riser's copy of the folded prologue. The man climbs column 1
            # northbound, so he meets ``centre + 2`` before ``centre + 1``: the
            # opcode ``r`` goes on the lower cell and the ``b`` on the upper one,
            # which is the ``r`` then ``b`` order the fetch row used to have. Both
            # cells were ``.`` he already walked, so the copy is free.
            #
            # Under ``fetch_tuck`` the operand ``r`` comes down here too — the
            # fetch row no longer has one to share — so the column carries the
            # whole ``r b r`` and needs a third cell. The order is unchanged:
            # northbound he meets the opcode first, then ``b``, then the operand.
            need = 4 if fetch_tuck else 3
            if collector - centre < need:
                raise MachineError(
                    f"fetch_fold: the riser is {collector - centre - 1} cell(s) long "
                    f"between the fetch and the collector; it needs {need - 1} for "
                    f"{'`r`, `b` and `r`' if fetch_tuck else '`r` and `b`'}"
                )
            if fetch_tuck:
                g.put(1, centre + 3, "r")
                g.put(1, centre + 2, "b")
                g.put(1, centre + 1, "r")
                pipe_glyphs.append((1, centre + 3, "r", "rom"))
                pipe_glyphs.append((1, centre + 1, "r", "rom"))
            else:
                g.put(1, centre + 2, "r")
                g.put(1, centre + 1, "b")
                pipe_glyphs.append((1, centre + 2, "r", "rom"))
        for yy in range(centre + 1, collector):
            g.soft(1, yy, ".")
    # The man spawns *on* the collector row: he starts facing east, the `<` beside
    # him turns him straight back west, and he joins the return path to the fetch
    # site. A dedicated spawn row below the collector would just walk him into the
    # east wall, since nothing down there steers him.
    with g.part("return:collector"):
        g.put(2, collector, "@")

    # ── the high corridor: the collector's mirror, one row above the fetch ────
    # A lane above the trie root walks *past* the fetch row on its way down, and
    # then climbs back up to it — ``2 * (collector - centre)`` ticks of pure
    # overshoot, on every one of those instructions, forever. The corridor catches
    # the drop the row before the fetch instead: west along ``hi_row``, one step
    # south at column 1, and he is standing on the fetch's own ``>``.
    #
    # Three things make it legal, and all three are geometry rather than luck:
    #
    # * the row is **blank** — it was opened for this out of the stagger's slack
    #   (see ``high_collector`` in the band layout), and the assertion below is
    #   what proves the trie did not put a turn glyph back into it. What the trie
    #   *does* leave there is the odd ``.`` where an ancestor's vertical leg
    #   crosses, and a westbound man keeps his heading over a ``.``;
    # * column 1 above the fetch row is unused in every layout without a top bus,
    #   which ``high_collector`` refuses to coexist with for exactly this reason;
    # * the drops that stop here are simple lanes only. A slab lane keeps falling
    #   to its own entry row far below, so nothing that needs the collector is
    #   diverted (:func:`_stop`).
    if hi_row is not None:
        hi_drops = [drop_x[r] for r in all_rows if r in drop_x and _stop(r, by_row.get(r)) == hi_row]
        if hi_drops:
            # A ``]`` is as safe here as a ``.``, and :data:`TIGHT_TRIE_COLS` puts one
            # here: it does not turn, and the BP it shifts is dead on this path — the
            # returning man's next act is the fetch's ``r`` and ``b``, which loads BP
            # from the opcode word before anything reads it. Anything that *turns*
            # is fatal, so the test is a whitelist, not a blacklist.
            #
            # Under :data:`FETCH_FOLD` that ``r`` and ``b`` move *onto this row*,
            # and the argument survives verbatim because the fold keeps them in the
            # same order relative to the ``]``: westbound the man meets the opcode
            # ``r`` first, then any trie ``]`` west of it, then the ``b`` at column
            # 2. The ``]``s shift a BP that ``b`` is about to overwrite, exactly as
            # they did when ``b`` stood on the fetch row. What the whitelist has to
            # keep out is unchanged: a glyph that *turns*.
            bad = {
                (x, ch)
                for (x, yy), ch in (trie_cells or {}).items()
                if yy == hi_row and ch not in (".", "]")
            }
            if bad:
                raise MachineError(
                    f"high_collector: the trie put {sorted(bad)} on the corridor row "
                    f"{hi_row}; a westbound man would be turned out of it"
                )
            with g.part("return:high"):
                if fetch_tuck:
                    # ── the tuck ──────────────────────────────────────────────
                    # The whole prologue goes **east of the root's up-leg**, on
                    # cells this man already walked as `<`, and he drops onto the
                    # fetch row at column 2 instead of column 1.
                    #
                    # Why the U-turn cannot be shorter than that, and why it can
                    # be exactly that: `x` turns *relative to the heading*
                    # (`cw`/`ccw`), so every path has to enter the root with the
                    # same heading as every other, and the root is entered
                    # **eastbound**. This man arrives from the east, so he must
                    # pass the root and come back — the only free variable is how
                    # far past. He may not turn on the root's own column, which is
                    # its up-leg and has to stay a pass-through for the northbound
                    # men the root sends up it; column 2 is the first he may have,
                    # and `>` on the fetch row beneath it puts him on the root in
                    # one more step. Manhattan would be one step from the up-leg;
                    # this is three; the fold's was five.
                    #
                    # Westbound he must meet opcode `r`, then `b`, then operand
                    # `r`, then the root — so the three go on the three westmost
                    # free cells east of the up-leg, *in reverse*: operand nearest
                    # the root, opcode furthest east. Every hi_row drop is east of
                    # all three (asserted), so no man can enter between them.
                    free = [
                        x
                        for x in range(3, max(hi_drops) + 1)
                        if (x, hi_row) not in g.c
                    ]
                    if len(free) < 3:
                        raise MachineError(
                            f"fetch_tuck: corridor row {hi_row} has {len(free)} free "
                            "cell(s) east of the root's up-leg; the prologue needs three"
                        )
                    operand_x, b_x, opcode_x = free[0], free[1], free[2]
                    if min(hi_drops) <= opcode_x:
                        # A drop landing on or west of the opcode `r` would join the
                        # corridor with part of the prologue already behind it — a
                        # stale opcode, silently, on that lane only.
                        raise MachineError(
                            f"fetch_tuck: a corridor drop at column {min(hi_drops)} is "
                            f"not east of the prologue (opcode `r` at {opcode_x})"
                        )
                    if (2, hi_row) in g.c:
                        raise MachineError(
                            f"fetch_tuck: {g.c[(2, hi_row)]!r} already stands at "
                            f"(2, {hi_row}), where the corridor's `v` has to go"
                        )
                    g.put(opcode_x, hi_row, "r")
                    g.put(b_x, hi_row, "b")
                    g.put(operand_x, hi_row, "r")
                    pipe_glyphs.append((opcode_x, hi_row, "r", "rom"))
                    pipe_glyphs.append((operand_x, hi_row, "r", "rom"))
                    for x in range(3, max(hi_drops) + 1):
                        g.soft(x, hi_row, "<")
                    g.put(2, hi_row, "v")
                elif fetch_fold:
                    # The corridor's copy of the folded prologue, drawn *before* the
                    # ``<`` run so the run's ``soft`` leaves it alone.
                    #
                    # ``b`` goes at column 2, which no trie node can ever reach: the
                    # root is at ``max(3, 2 + elevel)`` and ``place`` only moves
                    # east, so columns 1 and 2 are the prologue's alone.
                    #
                    # The opcode ``r`` goes on the westmost cell of this row that no
                    # trie leg occupies — a blank cell nobody but this corridor
                    # walks. Column 3 is the root's own up-leg (the root stands at
                    # ``(3, centre)`` and ``hi_row`` is ``centre - 1``), so the
                    # search starts there and normally lands on 4.
                    if (2, hi_row) in g.c:
                        raise MachineError(
                            f"fetch_fold: {g.c[(2, hi_row)]!r} already stands at "
                            f"(2, {hi_row}), where the corridor's `b` has to go"
                        )
                    opcode_x = next(
                        (x for x in range(3, max(hi_drops) + 1) if (x, hi_row) not in g.c),
                        None,
                    )
                    if opcode_x is None:
                        raise MachineError(
                            f"fetch_fold: the trie fills corridor row {hi_row} solid "
                            "from column 3; the opcode `r` has nowhere to stand"
                        )
                    g.put(opcode_x, hi_row, "r")
                    g.put(2, hi_row, "b")
                    pipe_glyphs.append((opcode_x, hi_row, "r", "rom"))
                if not fetch_tuck:
                    for x in range(2, max(hi_drops) + 1):
                        g.soft(x, hi_row, "<")
                    g.put(1, hi_row, "v")
                    for yy in range(hi_row + 1, centre):
                        g.soft(1, yy, ".")

    # A simple drop *stops* at the collector, and the collector sits above the slabs,
    # so being west of ``struct_east`` is harmless — that used to be forbidden back
    # when the collector was below the slab band and a drop really did cross one.
    # What must still hold is column disjointness against the drops that pass
    # *through* the collector on their way to a slab: those leave `.` on the collector
    # row, so a simple man sharing the column would sail past his turn west and be
    # swallowed by that slab.
    # A lane the high corridor catches never reaches the collector row, so it cannot
    # be swallowed by a slab there and is not asked about.
    through = {drop_x[row_of[m]] for m in order}
    clash = {
        r: c
        for r, c in drop_x.items()
        if c in through
        and by_row.get(r) not in order
        and _stop(r, by_row.get(r)) == collector
    }
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
    #
    # Every box here is the bounding box of the cells the part actually drew
    # (:meth:`_Grid.part`, :func:`_mark_boxes`), so it cannot over-reach: the box
    # *is* the drawing.  That matters because ``profile._region_of`` gives a cell
    # to the **smallest** box containing it, so a box claiming rows it never
    # draws on quietly absorbs its neighbours' heat.  ``trie``'s hand-written box
    # ran from ``y0`` for the full lane span and so swallowed fourteen blank rows
    # above the first ``x``; ``slab:<seek jump>`` was declared a full pitch wide
    # while holding nothing but a drop column, and owned the *next* slab's exit
    # riser.  Both are counted, not remembered, now.
    #
    # ``lane:<OP>`` stays hand-written: a lane's box runs to its drop column,
    # which is drawn by ``drops`` and not by the lane, and the lane band is the
    # one place where the useful reading is the row rather than the glyph run
    # (``AGENTS.md``: "a ``cpu:lane:*`` region is not that lane").
    regions: dict[str, tuple[int, int, int, int]] = _mark_boxes(g)
    if asc_x:
        regions["return:topbus"] = (1, bus_row, max(asc_x.values()), 1)
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            continue
        end = asc_x.get(r, drop_x.get(r, lane_end[r]))
        regions[f"lane:{m}"] = (lane_x0, r, max(1, end - lane_x0 + 1), 1)

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
        marks=g.marks,
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
    tight_arms: bool = False,
    tight_risers: bool = False,
    tuck_drain: bool = False,
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
    #
    # **The riser is at its floor, and :data:`HIGH_COLLECTOR` cannot help it.** The
    # question is worth answering once because the corridor is worth -6.32% to the
    # *lanes* and it is tempting to reach for it here too. It does not apply: a
    # lane above the trie root drops *past* the fetch row to the collector and
    # climbs back, so it pays ``2 * (collector - centre)`` of pure overshoot and
    # the corridor deletes all of it. A slab is already **below** the collector,
    # so its return is monotone in both axes and has no overshoot to delete.
    # Measured on hires/men-v3 (``collector`` 174, ``centre`` 167, ``hi_row`` 166,
    # the fetch ``>`` at x=9): ``BRN``'s man leaves at (27, 191) and the drawn path
    # is 17 up + 18 west + 7 up = 42, against a Manhattan bound of
    # ``(27 - 9) + (191 - 167) = 42``. Exactly the bound, so nothing is spendable.
    # Routing the same man through ``hi_row`` instead costs 25 + 18 + 1 = **44** —
    # two ticks *worse*, because the corridor is on the wrong side of the fetch.
    # The only thing that shortens a slab riser is a shallower band.
    exit_x = base - 1

    if sem in _JUMP_SEMS:
        with g.part(f"discard:{mnemonic}"):
            if drain_unit_bits:
                ey = _drain_block(g, base, s0, pipe_glyphs, drain_unit_bits)
            else:
                _discard_loop(g, base, s0, pipe_glyphs)
                ey = s0
        # The riser climbs past every shallower slab to the collector, so it gets
        # its own box: inside this slab's it would stretch the slab up over rows
        # that belong to the band above.
        with g.part(f"riser:{mnemonic}", exclusive=True):
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
    if tight_arms:
        # :data:`SLAB_TIGHT_ARMS`. The slot table above sizes every arm as if it
        # were the ``neg`` one; three of the nine columns it reserves are what the
        # *taken* arm walks out and back over, and it holds nothing there. The
        # taken arm's column is a free choice in a way a riser's is not — a riser
        # has to climb clear of every shallower slab, but the taken arm only has
        # to drop three rows to ``turn_row`` and walk back west to ``base + 3``.
        #
        # So it wants the westernmost column that is (a) east of ``base + 3``,
        # where the turn row's own ``v`` sits, and (b) not one of the two risers.
        # The risers are always a subset of ``{+3, +6, +9}``, so ``base + 4`` is
        # free whichever arm is taken, and the rows it crosses on the way down
        # carry only ``.`` there — the arms below reach east as far as their own
        # riser, and a ``.`` is shared by an eastbound and a southbound man alike.
        cols[taken] = base + 4
    if tight_risers:
        # :data:`SLAB_TIGHT_RISERS`. The 3-apart spacing in the slot table above
        # exists for **the taken arm**: its ``v``, the two-wide loop it drops into
        # and its exit column must not land on another arm's drop. With
        # ``tight_arms`` the taken arm is at ``base + 4`` and that reason is spent,
        # yet the two *rising* arms still sit on the slots the table gave them —
        # ``neg`` six cells east of its ``W``, ``zero`` three.
        #
        # A riser needs three things and none of them is spacing: a column of its
        # own, a column that is not the taken arm's, and every cell between its arm
        # row and the collector to be one it may cross. The cells above are the
        # other arms' eastward runs, which are ``.``, and the riser's own ``soft``
        # fills whatever is still blank. So the westernmost pair that satisfies the
        # first two conditions is ``base + 3`` and ``base + 5``.
        #
        # The one invariant kept from the table is **higher arm -> further east**,
        # so a riser never climbs across a shallower arm's ``^``.
        risers = sorted((a for a in rows if a != taken), key=lambda a: rows[a])
        north, south = _SLAB_RISER_SLOTS
        cols[risers[0]] = base + north  # the northern one
        cols[risers[1]] = base + south
    drops: set[int] = set()

    # :data:`SLAB_TUCKED_DRAIN`: the taken arm falls straight down the block's
    # spine and onto the ladder's own ``]``, so there is no ``turn_row`` and no
    # separate row for that ``]``. The arm's drop stops on the *last* arm row —
    # the ladder's first fold spans ``base + 2 .. base + 5`` and must clear every
    # arm's ``W`` and riser, so it cannot start higher than ``s0 + 4``, and the
    # ``]`` above it is a single spine cell that fits on ``s0 + 3`` whichever arm
    # is taken.
    tucked = tuck_drain and drain_unit_bits
    turn_row = s0 + 3 if tucked else s0 + 4
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
            with g.part(f"riser:{mnemonic}", exclusive=True):
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
    if tucked:
        # The arm is already on the spine and already heading south; the block's
        # ``]`` on ``turn_row`` is the whole of the entry. Nothing walks west here.
        from .drain import build_drain

        _spine = build_drain(0, unit_bits=drain_unit_bits, even=True).spine
        if cols[taken] != base + _spine:
            raise MachineError(
                f"tucked drain wants the taken arm on base + {_spine}, not "
                f"base + {cols[taken] - base} (SLAB_TIGHT_ARMS off?)"
            )
        with g.part(f"discard:{mnemonic}"):
            end_row = _drain_block(
                g, base, turn_row, pipe_glyphs, drain_unit_bits, tuck=True
            )
    else:
        g.put(cols[taken], turn_row, "<")
        for c in range(base + 2, cols[taken]):
            g.soft(c, turn_row, ".")
        with g.part(f"discard:{mnemonic}"):
            if drain_unit_bits:
                end_row = _drain_block(g, base, turn_row, pipe_glyphs, drain_unit_bits)
            else:
                _discard_loop(g, base, turn_row, pipe_glyphs)
                end_row = turn_row
    with g.part(f"riser:{mnemonic}", exclusive=True):
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

#: The same ladder+loop, for the **classic** slabs of a *seek* build — the short
#: jumps and the two branches the drum does not handle. Keyed on ``(slug, tier)``
#: because a seek machine's registry is, and empty by default so every existing
#: build is byte-identical.
#:
#: This was flatly refused until it was measured (``seek mode replaces the
#: discard entirely; no drain to size``). The refusal conflated two halves of a
#: hybrid band: the *seek* slabs really do have no discard to drain, and the
#: *classic* ones still run :func:`_discard_loop` — on ``deadman-3d_hires``
#: men-v3 that is ``cpu:discard:BRN`` + ``cpu:discard:BRZ`` + ``cpu:slab:JMPF``,
#: 4.70% of the run at 4.06 ticks a word and, uniquely in the discard pool,
#: **0.02% of it blocked**. Everything else in that pool waits on the drum, so it
#: is the only part where making the CPU faster makes the machine faster.
#:
#: 21-round tour, ``store="men-v3"``, ``frame_tiles=(2, 2)``, ``passed=True`` on
#: every row, all at the shipped 594x630 / ``mem_pad`` 9 / ``rom_capacity`` 49:
#:
#: | variant | box | pad | ticks | Δ |
#: |---|---|---|---|---|
#: | shipped | 594x630 | 9 | 111,492,961 | — |
#: | **bits 2, JMPF+BRZ** | **594x630** | **9** | **111,057,278** | **-0.391%** |
#:
#: and the 6-round rows that decide the shape, against 26,779,571:
#:
#: | variant | box | pad | ticks | Δ |
#: |---|---|---|---|---|
#: | bits 2, all three | 597x630 | **29** | 30,812,483 | +15.06% |
#: | bits 0, pad forced to 29 | 597x630 | 29 | 31,052,917 | +15.96% |
#: | bits 2, all three, ``ROM_TOUCH_DROP`` 10 | 594x630 | 9 | 26,729,256 | -0.188% |
#: | **bits 2, JMPF+BRZ** | 594x630 | 9 | **26,667,549** | **-0.418%** |
#:
#: Read rows one and two together: **the drain itself is worth -0.774% and the
#: pad it forces costs +15.96%.** Draining all three slabs is not a bad idea
#: badly tuned, it is a good idea paying a §7.1 toll — which is why
#: :data:`SEEK_CLASSIC_DRAIN_OPS` exists and why row three, which buys BRN back
#: by moving the ROM touch instead of the memory band, still loses: drop 10 is the
#: nearest drop that unties BRN and it lengthens the corridor 49 -> 52, worth
#: +0.699% on its own.
#:
#: ``bits`` above 2 does not build at this pitch: at 3 the block is six columns
#: wide and its east edge lands on ``JMPS``'s drop column
#: (``collision at (17, 41)``). :data:`SEEK_SLAB_PITCH` is already at
#: :data:`_SLAB_PITCH_FLOOR`, so that would need the drops re-solved, not a wider
#: band.
#: **Withdrawn on hires/men-v3, and it is a slack conflict, not a defect.** The
#: ladder was worth -0.391% and it adds six rows to the band; :data:`HIGH_COLLECTOR`
#: needs one row of the same ``LANE_PITCH`` stagger slack and is worth **-6.32%**.
#: They are mutually exclusive, and not marginally: with the drain on, *nothing*
#: binds — swept over ``squash_band`` 4..8 x ``rom_touch_drop`` k..k+1, every pair
#: dies on ``collision at (15, 41): '.' vs ']'``. With it off, ``squash_band`` 6
#: binds and is again the only value that does.
#:
#: Kept as a mechanism with an empty registry rather than reverted: the ladder is
#: correct, it is measured, and it becomes available again the moment the band
#: gains a row from anywhere else. The ``BRN``-sits-easternmost note below is still
#: the open question, and reordering the band would pay for both at once.
#:
#: **Recovered on hires/men-v3 by :data:`PACKED_SLAB_BAND`, and it paid for both at
#: once exactly as the paragraph above guessed.** Packing the staircase walks the
#: whole band seventeen columns west, which moves every drained ``r`` seventeen
#: columns *nearer* the ROM pipe on the west wall and that much further from
#: ``mem_resp`` on the east. Both halves of the old refusal go with it: the pad no
#: longer has to escape to 29, and the ``collision at (15, 41): '.' vs ']'`` that
#: made the ladder and :data:`HIGH_COLLECTOR` mutually exclusive does not occur —
#: they now both build, and ``HIGH_COLLECTOR`` is untouched.
#:
#: 21-round tour, ``store="men-v3"``, ``frame_tiles=(2, 2)``, ``passed=True`` on
#: every row, all on top of ``PACKED_SLAB_BAND``:
#:
#: | variant | box | ticks | Δ |
#: |---|---|---|---|
#: | packed, drain off | 577x630 | 93,901,187 | — |
#: | bits 2, ``JMPF`` + ``BRZ`` (the old restriction) | 579x630 | 93,726,592 | -0.186% |
#: | bits 2, ``BRN`` alone | 577x630 | 93,134,530 | -0.816% |
#: | **bits 2, all three** | **579x630** | **92,913,125** | **-1.052%** |
#: | bits 3, all three | 580x630 | (3-round: +0.68% on bits 2) | — |
#:
#: Note ``BRN`` alone beats ``JMPF`` + ``BRZ`` together by four to one, which is the
#: tick census the note below predicted and the reason the restriction was worth
#: attacking rather than tuning. ``bits 3`` now *builds* (it could not at the
#: shipped pitch — ``collision at (17, 41)``) and is simply worse.
#:
#: **taped takes it too, but only on ``BRN``, and the §7.1 sign is the other way
#: round.** At taped's re-derived :data:`MEM_PAD_FOR` of 4 the ladder binds on
#: ``BRN`` alone and on nothing else — draining ``JMPF`` or ``BRZ`` puts an ``r`` at
#: (28, 151) that ties ``rom`` 29 against ``mem_resp``, and no pad in the sweep
#: separates them. Which is the exact mirror of men-v3's old restriction, on the
#: *western* slabs rather than the eastern one, and a reminder that the tie is a
#: property of one machine's walls and never a rule. 21-round tour, ``store="taped"``,
#: at pad 4:
#:
#: | variant | box | ticks | Δ |
#: |---|---|---|---|
#: | drain off | 626x386 | 175,267,384 | — |
#: | **bits 2, ``BRN`` alone** | **626x392** | **174,497,560** | **-0.439%** |
#: | bits 2, any set containing ``JMPF`` or ``BRZ`` | — | does not bind | — |
#:
#: ── the swept answer, and why ``bits`` was the wrong knob to look at ──
#:
#: Both numbers above were *assumed*, not swept: ``bits`` 2 came from
#: ``little-little-man``'s ROM and the ``BRN``-only restriction from a pad that has
#: since moved twice. Swept at 21 rounds on the merged geometry, both tiers land
#: on the **same** pair — ``bits`` 3 on ``BRN`` + ``BRZ``, and ``JMPF`` left on the
#: counted loop.
#:
#: men-v3, against 88,217,704 at 496x672 (the pad is searched, not pinned, so it
#: is reported rather than set):
#:
#: | variant | pad | ticks | Δ |
#: |---|---|---|---|
#: | shipped: bits 2, all three | 3 | 88,217,704 | — |
#: | bits 3, all three | 3 | 88,196,073 | -0.025% |
#: | bits 2, ``BRN`` alone | 2 | 88,254,115 | +0.041% |
#: | bits 3, ``BRN`` alone | 2 | 87,985,685 | -0.263% |
#: | bits 2, ``BRN`` + ``BRZ`` | 2 | 87,974,935 | -0.276% |
#: | **bits 3, ``BRN`` + ``BRZ``** | **2** | **87,688,021** | **-0.600%** |
#: | bits 3, ``BRN`` + ``BRZ``, pad forced to 3 | 3 | 88,384,619 | +0.189% |
#: | bits 4/5, any set | 17/28 | — | the pad explodes |
#:
#: Read the winning row against the one below it: **the pad column is worth
#: 696,598 ticks (0.79%), and the win is that column minus what buying it cost.**
#: At the same pad 3 this exact drain set is *worse* than the shipped one by
#: 166,915 ticks, because it gives up ``JMPF``'s 351,781 words for nothing. The
#: net -529,683 is 696,598 - 166,915. Nothing here is a faster discard; it is a
#: narrower machine paid for with a slower one.
#:
#: taped, against 147,213,896 at 625x391, at :data:`MEM_PAD_FOR` 1 (0 refuses on
#: every set):
#:
#: | variant | box | ticks | Δ |
#: |---|---|---|---|
#: | drain off | 625x385 | 147,941,842 | +0.494% |
#: | shipped: bits 2, ``BRN`` | 625x391 | 147,213,896 | — |
#: | bits 3, ``BRN`` | 625x396 | 146,977,126 | -0.161% |
#: | bits 2, ``BRN`` + ``BRZ`` | 625x391 | 146,931,368 | -0.192% |
#: | **bits 3, ``BRN`` + ``BRZ``** | **625x396** | **146,672,958** | **-0.368%** |
#: | bits 2, all three (needs pad 2) | 627x391 | 147,395,603 | +0.124% |
#: | bits 3, all three (needs pad 2) | 628x396 | 147,408,608 | +0.132% |
#: | bits 3, ``BRN`` + ``BRZ``, pad forced to 2 | 625x396 | 147,499,984 | +0.194% |
#: | bits 3, ``BRZ`` alone | 625x395 | 147,801,034 | +0.399% |
#:
#: Three things in there are worth keeping, because each of them was believed the
#: other way round:
#:
#: * **``BRZ`` binds on taped now.** The paragraph above says a set containing it
#:   "does not bind, and no pad in the sweep separates them". That was true at the
#:   pad-4 geometry it was measured on; :data:`SEEK_TIGHT_STRUCT_DROPS` walked the
#:   structured drops west, the pad floor fell to 1, and ``BRN`` + ``BRZ`` binds at
#:   the floor. The tie is a property of one machine's walls, exactly as the note
#:   says — which cuts both ways.
#: * **``JMPF`` is the one that costs a pad column, on both tiers.** Its block is
#:   the westernmost, so it is not §7.1 on a slab ``r`` at all: the block is a
#:   column wider than the ``a<`` it replaces, that column walks the whole band
#:   east, and the lane ``r`` at (24, 152) loses ``mem_resp`` to ``in``. men-v3's
#:   floor is 3 with ``JMPF`` drained and **2** without it, and that column is
#:   worth more than ``JMPF``'s own 351,781 words.
#: * **``bits`` 3 is what makes the restriction pay.** At ``bits`` 2, dropping
#:   ``JMPF`` *loses* (+0.041%) — the pad column does not cover it. At ``bits`` 3 the
#:   two remaining slabs give back enough to turn the same trade into -0.600%.
#:
#: The ladder's own model — ``n + 6*(n >> t) + 5*t``, :mod:`.drain` — prices the
#: inside of the block and nothing else, and on this machine the outside is most
#: of the bill. Measured region by region, men-v3 ``bits`` 2 -> 3 at a *fixed* pad
#: of 3 (so the pad is not doing the work):
#:
#: | region | Δ t/instr |
#: |---|---|
#: | ``cpu:discard:BRN`` / ``:BRZ`` / ``cpu:slab:JMPF`` | **-0.631** |
#: | ``cpu:riser:BRZ`` / ``:JMPF`` / ``:BRN`` | +0.270 |
#: | ``cpu:return:collector`` | +0.128 |
#: | ``cpu:lane:BRZ`` / ``:BRN`` / ``:JMPF`` | +0.157 |
#: | ``cpu:slab:JMPS`` + ``cpu:seek:*`` (the tail moved down with the band) | +0.114 |
#: | net | **-0.025** |
#:
#: So a deeper block converts *inside itself* almost perfectly — the discard pool
#: is 0.01% blocked, it is not drum-bound, and it gives up its ticks tick for tick
#: — and then hands back **96% of them** to the geometry it displaces: one tick of
#: exit riser per row of extra depth on every taken discard, two ticks of entry
#: walk plus one of collector per column of extra width on every execution. Price
#: a drain by the box it moves, never by :func:`drain.cost`.
#:
#: The blunt version, and the reason to stop looking here: the whole slab band —
#: every ``cpu:slab|discard|riser|entry:*`` box, ``_region_of``'s attribution —
#: is **10.02 t/instr on men-v3 and 0.07% blocked** before this lever and
#: **10.34 t/instr after it**. The band got *dearer* by 0.28M ticks and the
#: machine got cheaper by 0.53M, because everything that moved is outside the
#: band (``cpu:return:high`` 13.74 -> 13.36, ``cpu:return:collector`` 4.03 ->
#: 3.77, ``cpu:lane:LD`` 10.66 -> 10.43). Taken with the riser's Manhattan floor
#: (:func:`_slab`) and the drop permutation's ceiling (``order``, above), the band
#: has no further tick in it that is not a *footprint* consequence somewhere else.
SEEK_CLASSIC_DRAIN: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "men-v3"): 3,
    ("deadman-3d_hires", "taped"): 3,
}

#: Which classic mnemonics :data:`SEEK_CLASSIC_DRAIN` actually applies to.
#: ``None``/absent means all of them.
#:
#: This exists because the drain's cost on a seek band is **not its depth, it is
#: §7.1**. Measured on hires/men-v3 at ``unit_bits=2``: the block hangs below the
#: entry row, so the *easternmost* classic slab's lowest ``r`` lands at CPU
#: (36, 53) — grid (44, 183) — where ``rom`` and ``mem_resp`` are both 47 cells
#: away. A tie fails, and the only pad that separates them is 29 against the
#: shipped 9. Restricting the drain to the western slabs keeps every added ``r``
#: nearer the ROM and leaves the pad alone.
#: ``BRN`` is the easternmost classic slab and the only one that ties, which is
#: also the annoying part: it is the *biggest* of the three (3,115,539 ticks at 21
#: rounds against ``BRZ``'s 1,544,178 and ``JMPF``'s 1,479,323). Reordering the
#: band so it sits west would collect it for free and is the open question here.
#:
#: **Answered, and without reordering anything.** ``BRN`` is still the easternmost
#: classic slab — the band's order is the lane rows' and none of them moved — but
#: :data:`PACKED_SLAB_BAND` walks the whole staircase seventeen columns west, so
#: "easternmost" is no longer far enough east to tie. ``BRN``'s lowest ``r`` clears
#: ``mem_resp`` at the shipped pad, the restriction has nothing left to protect, and
#: the entry is gone: men-v3 drains all three. It was worth **-0.87%** over the
#: two-slab restriction on the 21-round tour, which is roughly the ratio of ``BRN``'s
#: own ticks to the other two's.
#:
#: Keep the mechanism. The tie is a property of where the deepest slab's block
#: lands, so any future lever that widens the band east — or any pad that moves —
#: can bring it back, and it will come back on whichever slab is easternmost then.
#: **It came back immediately, on the other tier and on the other slabs**: taped, at
#: its re-derived pad of 4, binds the ladder on ``BRN`` and refuses it on ``JMPF``
#: and ``BRZ``. Same mechanism, opposite restriction, same registry.
#:
#: **And it is now on both tiers, on ``JMPF`` alone, and it is a cost rather than a
#: refusal.** Swept (the table on :data:`SEEK_CLASSIC_DRAIN`): every set builds on
#: both tiers at some pad, so the entry is no longer protecting against a tie — it
#: is buying a pad column. ``JMPF`` is the westernmost slab, its block is a column
#: wider than the ``a<`` it replaces, and that column pushes the whole band east
#: until a *lane*'s ``r`` at (24, 152) loses ``mem_resp`` to ``in``. men-v3's pad
#: floor is 3 with ``JMPF`` drained and 2 without; taped's is 2 with and 1 without.
#: The column is worth more than ``JMPF``'s 351,781 words, so both tiers drain the
#: two eastern slabs and leave the western jump on the counted loop.
#:
#: Note the entry is doing the opposite job on the two tiers *at the same time*:
#: on taped it also keeps ``BRZ``, which the note above records as unbindable and
#: which now binds at the floor. Re-sweep this set whenever the band moves; the
#: answer has reversed twice.
SEEK_CLASSIC_DRAIN_OPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("deadman-3d_hires", "men-v3"): ("BRN", "BRZ"),
    ("deadman-3d_hires", "taped"): ("BRN", "BRZ"),
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
#: the short-return drop rule. Under the seek drum the same lever is keyed per
#: ``(slug, tier)`` in :data:`SEEK_TIGHT_STRUCT_DROPS` instead, for the reason the
#: :data:`SLAB_PITCH` / :data:`SEEK_SLAB_PITCH` split already gives.
TIGHT_STRUCT_DROPS: set[str] = {"little-little-man"}

#: :data:`TIGHT_STRUCT_DROPS`'s replacement while the seek drum is on — the same
#: split :data:`MEM_PAD` / :data:`SEEK_MEM_PAD` and :data:`SLAB_PITCH` /
#: :data:`SEEK_SLAB_PITCH` already make, and keyed per ``(slug, tier)`` because
#: the drop columns are a tier's own measurement.
#:
#: ``build_cpu`` used to refuse ``tight_drops`` under the drum outright, on the
#: grounds that "seek slabs are a different shape ... and their entry geometry has
#: not been proved against a tightened column". That was accurate about its own
#: evidence and wrong about the conclusion. :func:`_tight_struct_entry` never
#: reasoned about slab *bodies* — it reserves ``base - 1`` and ``base + 3/6/9``
#: because those columns carry **risers to the collector**, and it floors each
#: entry one cell east of wherever that slab turns its man south. Both statements
#: survive the drum verbatim:
#:
#: * a classic slab under the drum is drawn by the same :func:`_slab`, so its
#:   riser and arm columns are literally the ones the reservation names;
#: * a *seek* branch has no ``base - 1`` riser at all (its two not-taken arms rise
#:   on ``base + 3/6/9``, which are already reserved), so the reservation is
#:   merely conservative there;
#: * the seek tail's shared riser on column 1 is ``base - 1`` of slab 0, which
#:   ``struct_x0 == _STRUCT_X0 == 2`` makes reserved for free;
#: * the tail itself lives strictly *below* the band, on rows no entry column
#:   crosses.
#:
#: What is genuinely new is the **seek jump**, and it is a cost, not a collision.
#: It has no body: the entry row turns him south at ``turn_x`` and he drops to the
#: taken row, where he walks **east** to the request `s` at ``e_s = struct_east +
#: 2`` and sends. :data:`SEEK_TAKEN_DROP_EAST` already puts that turn as far east
#: as the entry column allows — ``turn_x == drop_x - 1`` — so the three legs are
#: ``(drop_x - lane_x0) + 1 + (e_s - drop_x + 1)`` and **the drop column cancels**.
#: A seek jump costs ``e_s + 2 - lane_x0`` wherever its entry sits: 36 ticks here,
#: before and after, to the tick. The win is entirely the classic slabs'.
#:
#: Which is why the guard now demands ``seek_taken_drop_east`` rather than refusing
#: outright. Without it ``turn_x == base``, the cancellation is gone, and a
#: tightened entry hands the man the whole band twice — a real loss, and the one
#: true statement inside the old refusal.
#:
#: Measured on ``deadman-3d_hires`` men-v3, 21 rounds, ``frame_tiles=(2, 2)``,
#: everything else at the shipped values, against a rebuilt baseline of
#: **118,411,196** ticks at 595x630 (``passed``, ``fatal=None``):
#:
#: | lane | slab | ``base`` | ``drop_x`` before | after | ticks saved / exec |
#: |---|---|---|---|---|---|
#: | `JMPS` | seek jump | 2 | 47 | **18** | 0 — the taken row takes it back |
#: | `JMPF` | classic jump | 13 | 48 | **19** | 58 |
#: | `BRZ` | classic branch | 24 | 49 | **25** | 48 |
#: | `BRN` | classic branch | 35 | 50 | **36** | 28 |
#:
#: A column is worth two ticks — east along the lane to the turn, west back along
#: the slab's entry row to its body — the same arithmetic :data:`TUCKED_DROPS` and
#: :data:`FOLDED_LANES` record, except that the return leg here is the entry row
#: rather than the collector, so the slab's own column cancels out of it.
#:
#: **111,492,961 at 594x630, -5.843%**, ``passed``, ``fatal=None``, ``route_lengths``
#: ``adapter->store`` 4 / ``store->cpu`` 6 unchanged. The profile puts the win where
#: the argument put it (native ``FastLittleman``, ``profile=True``,
#: ``profile_stride=17``, same tour, heat summed over ``cpu:*`` — the CPU is one
#: runner, so its samples over ``profile.samples`` is the fraction of the run):
#:
#: | region | %run before | %run after | ticks before | ticks after |
#: |---|---|---|---|---|
#: | `cpu:lane:BRN` | 2.39% | **1.81%** | 2,830,028 | 2,018,023 |
#: | `cpu:lane:BRZ` | 2.32% | **0.81%** | 2,747,140 | 903,093 |
#: | `cpu:lane:JMPF` | 1.42% | **0.35%** | 1,681,439 | 390,225 |
#: | `cpu:lane:JMPS` | 0.82% | **0.24%** | 971,172 | 267,583 |
#: | four lanes | **6.95%** | **3.21%** | 8,229,779 | 3,578,924 |
#:
#: All four are **0.00% blocked** before and after: this is walking, not waiting,
#: which is why it scales with columns at all. `JMPS`' lane shrinks like the rest
#: and its time reappears on the taken row, which is in no ``cpu:*`` region — the
#: 4.65M the lanes give up against the 6.92M the run gives up is the rest of the
#: band and the collector following them west.
#:
#: **It does narrow the CPU, and only by one.** :data:`FOLDED_LANES`' note above is
#: right that ``ret_x`` was the structured drops' — 50, `BRN`'s — and the box does
#: not care (595 is the router wall's). What it becomes is 49, and *not* because a
#: drop is there: under the drum ``ret_x`` is floored at ``struct_east + 3``, the
#: taken row's send site plus its ``v``, and with the drops at 18..36 that floor is
#: what the east wall now stands on. So the next column of CPU has to come from
#: ``e_s`` — which is ``struct_east + 2`` = 48 and needs only to be east of the
#: taken drops (17 here) and of the flush loop's columns 2..6. Every one of those
#: 30 columns is walked on the taken row by every taken seek jump, and shrinking
#: them is the term this change leaves on the table. It belongs to the seek tail's
#: own geometry — where the request `s` binds — and is left alone here.
#:
#: A one-column narrower band also re-runs the ``mem_pad`` search into a closer
#: bind, 10 -> 9, which is why the memory lanes move too and ``cpu->drum`` is
#: 1145 -> 1144.
#:
#: **The taped tier has now been measured, and takes it: -3.227%** on the 21-round
#: tour (181,008,755 -> 175,167,094 at 642x384, ``passed``), measured on top of
#: :data:`STRAIGHT_TRIE` at the re-derived corridor. Against men-v3's -5.84%.
#:
#: The precondition the guard demands holds on taped **structurally, not by
#: luck**: ``build_cpu`` refuses ``tight_drops`` under the drum without
#: ``seek_taken_drop_east``, and taped has been in :data:`SEEK_TAKEN_DROP_EAST`
#: since that lever landed — so ``turn_x == drop_x - 1``, the three legs telescope
#: and the seek jump's drop column cancels here exactly as it does on men-v3. A
#: build that lost the precondition would raise rather than quietly regress.
#:
#: Empty by default, so every machine not named here is byte-identical.
SEEK_TIGHT_STRUCT_DROPS: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: Per-``(slug, tier)`` opt-in for a seek jump that leaves its entry row heading
#: **east**, to a turn column past every slab, instead of west to ``slab_base``.
#:
#: :data:`SEEK_TAKEN_DROP_EAST`'s cancellation identity is ``turn_x == drop_x - 1``:
#: the entry row's westward leg and the taken row's eastward leg telescope, and the
#: drop column falls out of the cost.  **That identity does not survive a packed
#: band.**  ``jump_x`` is capped at ``drop_x - 1`` and then walked *west* out of
#: every other slab's reserved span, and once the band closes up there is no gap
#: left east of ``base`` — measured on hires/men-v3, ``cpu:slab:JMPS`` descends at
#: x=10, which is ``base_JMPS`` exactly, against ``drop_x`` 26.  The man therefore
#: walks 16 cells west along the entry row, drops 22, and walks 29 straight back
#: east along the taken row to reach the request ``s`` at ``e_s`` 39: a U-turn of
#: ``2 * (drop_x - base)`` = 32 ticks that holds nothing, on every taken jump.
#:
#: The fix is to stop capping at ``drop_x - 1``.  A seek jump has **no slab body** —
#: it is one turn south and a bare drop — so its turn column is a free choice in a
#: way a branch's is not, and ``struct_east + 1`` is east of every slab body by
#: construction *and* one west of ``e_s - 1``.  Turning there makes the entry row
#: run east ``struct_east + 1 - drop_x`` cells and the taken row's leg one cell,
#: which is the same telescoping the identity wanted, reached from the other side.
#:
#: It cannot move the east wall: ``ret_x`` is already floored at ``struct_east + 3``
#: whenever the drum is on (the taken row's send site plus its ``v``), so the new
#: drop column at ``struct_east + 1`` is strictly inside a span the return path
#: already owns.  ``MEM_PAD_FOR`` is therefore unaffected — verified by build, not
#: assumed.
#:
#: **This is the CPU half of "issue the seek request early".**  Every tick removed
#: here is a tick the drum starts its notice walk sooner, and the CPU is measured
#: ~927 t/seek *blocked* on exactly that walk — so the saving lands on the critical
#: path rather than in slack.
#:
#: Measured, 21 rounds, ``passed=True`` on both tiers: men-v3 87,688,021 ->
#: 87,424,821 (**-0.300%**) at an unchanged 496x672 and ``mem_pad`` 2; taped
#: 146,672,958 -> 146,411,582 (**-0.178%**) at an unchanged 625x396. The geometry
#: does not move, so ``MEM_PAD_FOR`` is unaffected — checked, not assumed.
SEEK_JUMP_EAST: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
    ("deadman-3d_hires", "taped"),
}

#: Per-``(slug, tier)`` **columns to walk the whole seek tail west**, on top of
#: :data:`SEEK_JUMP_EAST`. Zero (absent) everywhere by default, so every machine
#: that does not name itself here is byte-identical.
#:
#: The tail is four glyph runs that :data:`SEEK_JUMP_EAST` pinned to
#: ``struct_east``, and they only make sense as a unit:
#:
#: * the entry row's turn ``v`` at ``struct_east + 1`` (``jump_x``);
#: * the drop column below it, from the entry row down to the taken row;
#: * the taken row's ``> s v`` — the eastbound landing, the request ``s`` at
#:   ``e_s = struct_east + 2``, and the ``v`` that turns south off it;
#: * the ``<`` on the next row down that starts the westbound flush walk.
#:
#: **Moving the drop alone is worth exactly nothing** — measured, identical to the
#: tick. The man still has to walk back east to a stationary ``s``, so the entry
#: row's shortened eastward run and the taken row's lengthened one cancel. Both
#: endpoints move or neither does; the binding obligation is what the *old* path
#: did that the new one skips.
#:
#: The floor is geometric, **not a §7.1 binding** — the build never reaches
#: binding, it raises a collision while drawing. :func:`_slab_east_span`
#: deliberately reserves nine columns for a ``BR_NEG`` even under
#: :data:`SLAB_TIGHT_ARMS` (see that registry's NOTE — spending them measured
#: +5.76%), and ``BRN``'s easternmost *drawn* glyph is its northern riser. On
#: hires/taped ``struct_east`` is 29, so the reserved-but-undrawn columns are the
#: tail's to take, and how many there are is a property of that riser:
#:
#: | riser slot | free columns | tail floor | first bad build |
#: |---|---|---|---|
#: | ``base + 6`` (slot table) | 25..29 | **5** | ``collision at (24, 43): '.' vs '^'`` |
#: | ``base + 5`` (:data:`SLAB_RISERS_WEST`) | 24..29 | **6** | ``collision at (23, 43): '.' vs '^'`` |
#:
#: Both failures are the same cell in CPU coordinates: row 43 is ``BRN``'s
#: ``zero`` arm row and the column is that arm's riser ``^``, which the drop
#: column would overwrite. Because the span check in the seek-jump branch below is
#: written against the *reserved* span rather than the drawn one, this registry
#: turns that check off and leans on the collision instead: the drop is drawn
#: before the deeper slabs, so any slab that really wants the column raises when
#: its own ``put`` lands on the ``.``.
#:
#: It does **not** move the east wall. ``ret_x`` stays floored at ``struct_east +
#: 3`` (below), so the box and :data:`MEM_PAD_FOR` are untouched — the tail is
#: walking into space the return path already owns. Whether the wall may follow is
#: a separate question; see :data:`SEEK_TAIL_WALL`, where the answer is that it
#: may, by two columns, and that doing so is worth exactly nothing.
#:
#: Measured, hires/taped, 21 rounds, ``passed=True fatal=None``, box unchanged at
#: 625x403 and ``mem_pad`` -1 throughout. Alone (risers on the slot table):
#:
#: | west | ticks | Δ |
#: |---|---|---|
#: | 0 | 132,920,972 | — |
#: | 1 | 132,907,200 | -13,772 |
#: | 2 | 132,901,576 | -19,396 |
#: | 3 | 132,885,260 | -35,712 |
#: | 4 | 132,868,944 | -52,028 |
#: | **5** | **132,852,628** | **-68,344** (-0.0514%) |
#: | 6 | — | collides |
#:
#: With :data:`SLAB_RISERS_WEST` the floor is 6 and the pair lands at
#: **132,807,820** (-113,152, **-0.0851%**, 150.9896 -> 150.8611 t/instr).
#: ~16,300 ticks a column: four columns of entry-row run and one of taken-row leg
#: apiece, on every taken ``JMPS``.
SEEK_TAIL_WEST: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 6,
}

#: Per-``(slug, tier)`` opt-in for letting the **CPU east wall follow**
#: :data:`SEEK_TAIL_WEST`. Empty by default, so every machine that does not name
#: itself here is byte-identical.
#:
#: ``ret_x`` is floored at ``struct_east + 3`` whenever the drum is on, and that
#: floor *is* the wall on this machine — every other candidate (the lane drops,
#: the slab risers, ``lane_x0``) is further west. So once the tail has walked
#: :data:`SEEK_TAIL_WEST` columns west, the floor it justified has walked with it
#: and the wall is free to follow. This is the knob that spends that.
#:
#: It is worth asking because the tail move itself is small change and a narrower
#: CPU is not: the wall is where ``mem_resp`` attaches, and :data:`MEM_PAD_FOR`'s
#: floor is that attachment point against the rival pipes. Every column of pad is
#: walked twice by every memory instruction, so on the recorded arithmetic
#: (:data:`SLAB_TIGHT_RISERS`, +8.437%) the eastern columns of the CPU are "bought
#: pad floor, not air" and a wall that moves west should be worth **columns of
#: pad**, which is an order of magnitude more than the tail itself.
#:
#: **Re-derived on the current geometry, and the prize is gone.** Two independent
#: measurements, hires/taped, everything else shipped:
#:
#: 1. **The wall may follow, but only by two columns.** At ``west`` 3 and beyond
#:    the *input* lane's ``r`` at (18, 152) stops binding ``in``::
#:
#:      'r' at (18, 152) must bind 'in' but distances are
#:      [('mem_resp', 21), ('in', 24), ('rom', 45)]
#:
#:    ``mem_resp`` rides the east wall, so every column the wall moves west is a
#:    column it gains on that ``r``: 26 at ``west`` 0, a tie with ``in`` at 24 by
#:    ``west`` 2, and a loss from 3 on. It is not recoverable through
#:    :data:`MEM_PAD_FOR` — the pad moves the *memory* band, not the input lane's
#:    ``r``, and the failure is identical at every pad in -8..1 — nor by moving
#:    the rival: :data:`INPUT_NORTH_WEST` already has the ``I`` room at ``CX + 1``,
#:    the westernmost legal column.
#:
#: 2. **The two columns it can take are worth nothing, because ``mem_pad`` is
#:    saturated.** At the shipped wall, every pad from -12 to -1 builds the
#:    **byte-identical** grid (sha ``20082384``; pad 0 differs), so the memory band
#:    is already hard against ``lane_x0 + max(prefixes)`` and no amount of extra
#:    room west of it moves a glyph. Letting the wall follow at ``west`` 1
#:    (:data:`SLAB_RISERS_WEST` off, to isolate it) reproduces the fixed-wall tick
#:    count *exactly* — 132,907,200 either way — at 624x403 instead of 625x403,
#:    and pads -6..-2 there are likewise identical to the tick.
#:
#: So what is on offer is **two columns of box for zero ticks** (625x403 ->
#: 623x403 at ``west`` 2). Left unclaimed because this tier is judged on ticks and
#: the shipped tail is at 6, which the wall may not follow.
#:
#: So :data:`MEM_PAD_FOR`'s standing instruction — "revisit when, and only when,
#: the pad stops being a function of this wall" — has come true, and the answer is
#: the disappointing half of it: the pad stopped being a function of the wall by
#: **bottoming out**, not by being freed. The eastern columns are no longer bought
#: pad floor; they are genuinely air, and air is worth zero ticks. What would make
#: this live again is anything that moves ``mem_x`` off ``lane_x0 + max(prefixes)``
#: — a lane order or micro-program change that shortens the longest memory prefix
#: — since only then does the band have somewhere west to go.
SEEK_TAIL_WALL: set[tuple[str, str]] = set()

#: Per-``(slug, tier)`` opt-in for :data:`SLAB_TIGHT_RISERS`' **walk** without its
#: **span**. Empty by default, so every machine that does not name itself here is
#: byte-identical.
#:
#: This is :data:`SEEK_TAIL_WEST`'s mechanism applied to the branch slabs, and it
#: is the reason :data:`SLAB_TIGHT_RISERS` measured +8.437% while this does not:
#: that registry moved the risers *and* narrowed :func:`_slab_east_span`, which
#: walks ``struct_east`` and the CPU east wall west and re-prices
#: :data:`MEM_PAD_FOR`. The two effects are separable. ``tight_risers`` here is
#: passed to :func:`_slab` alone; :func:`_slab_east_span` keeps returning nine, so
#: the staircase, ``struct_east``, the wall and the pad see an unchanged band and
#: the risers simply stop walking columns they were only walking to reach a slot
#: the table reserved.
#:
#: What it is worth is one arm each. Under :data:`SLAB_TIGHT_ARMS` the taken arm
#: is at ``base + 4`` and the two rising arms are still on the slot table:
#: ``BRZ``'s ``neg`` riser at ``base + 9`` moves to ``base + 5`` (four columns,
#: walked out and back on every ``BRZ`` that falls negative) and ``BRN``'s
#: ``zero`` riser at ``base + 6`` moves to ``base + 5`` (one column).
#:
#: ``_SLAB_RISER_SLOTS`` is the floor and was re-swept here rather than trusted,
#: because the slots now share a band with :data:`SEEK_TAIL_WEST`'s drop column.
#: 21 rounds, ``passed=True fatal=None``, everything else shipped, on top of the
#: tail at 5:
#:
#: | slots | box | ticks |
#: |---|---|---|
#: | off (``+9``/``+6``) | 625x403 | 132,852,628 |
#: | **(5, 3)** | **625x403** | **132,824,136** |
#: | (6, 3) | 625x403 | 132,831,844 |
#: | (7, 3) | — | ``collision at (25, 43): '.' vs '^'`` — ``BRN``'s northern riser on the seek tail's own drop column |
#: | (8, 3) | — | ``collision at (15, 39): '.' vs '<'`` |
#: | (5, 4) | — | ``collision at (11, 43): '^' vs ']'`` — the drain ladder |
#:
#: The ``(7, 3)`` row is the interesting one: with the tail at ``struct_east + 1
#: - 5`` the two levers now compete for the same columns, and the riser is what
#: gives. That is also why the tail's own floor is re-swept *after* this, not
#: before.
SLAB_RISERS_WEST: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
}

#: Per-``(slug, tier)`` opt-in for a branch slab whose **taken arm turns where it
#: is, instead of at the arm slot the table reserves for it**.
#:
#: :func:`_slab` sizes all three arms from one table — ``neg`` at ``base + 9``,
#: ``zero`` at ``+6``, ``pos`` at ``+3`` — spaced three apart so no arm's drop
#: lands on another's. That spacing is necessary for the two arms that **rise**:
#: a riser owns its column from the collector all the way down, so two of them may
#: not share. It is not necessary for the one that is **taken**, which only drops
#: three rows to ``turn_row`` and walks back west to ``base + 3``.
#:
#: The cost of pretending otherwise is visible in the band. On hires/men-v3 the
#: two branches are the same hardware with the lanes relabelled (§6), and they are
#: drawn eleven columns apart in width::
#:
#:     BRZ (zero taken)   >XWb..v      7 wide
#:     BRN (neg  taken)   >Wb.....v   11 wide
#:
#: Five of ``BRN``'s dots are nothing at all — the taken man walks east over them
#: and straight back west along ``turn_row``, ``2 * 5`` ticks that hold no state.
#: ``BRZ`` is narrower only because ``zero``'s slot happens to sit further west,
#: which is an accident of the table and not a property of the branch.
#:
#: So the taken arm turns at ``base + 4``: the westernmost column east of the turn
#: row's own ``v`` that is not one of the two risers (they are always a subset of
#: ``{+3, +6, +9}``). The rows it crosses on the way down hold only ``.`` there,
#: and a ``.`` is shared by an eastbound and a southbound man alike — the same
#: "operations ride cells the man already walks" that :data:`FOLDED_LANES` and
#: :data:`TIGHT_TRIE_COLS` spend.
#:
#: It also narrows the slab's **reserved span**, but only for a ``BR_NEG``: with
#: ``neg`` taken the easternmost riser is ``zero``'s at ``+6``, so the span falls
#: 9 -> 6 and :data:`PACKED_SLAB_BAND` can close the band up by three columns. A
#: ``BR_ZERO`` still has ``neg``'s riser at ``+9`` and spans nine as before.
#: Because that moves ``struct_east``, it moves the east wall — **re-sweep**
#: :data:`MEM_PAD_FOR` after turning this on, rather than assuming it held.
#:
#: Measured, 21 rounds, ``passed=True`` on both tiers, **on top of**
#: :data:`SEEK_JUMP_EAST`: men-v3 87,424,821 -> 86,981,643 (**-0.506pp**, -0.806%
#: against the base) and taped 146,411,582 -> 145,970,818 (**-0.301pp**, -0.479%).
#: The two are additive to within a tenth of a point, and neither moves the box.
SLAB_TIGHT_ARMS: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
    ("deadman-3d_hires", "taped"),
}

#: Per-``(slug, tier)`` opt-in for moving the two **rising** arms of a branch slab
#: onto ``base + 3`` / ``base + 5``. Requires :data:`SLAB_TIGHT_ARMS`; empty by
#: default, so every machine that does not name itself here is byte-identical.
#:
#: :data:`SLAB_TIGHT_ARMS` narrowed the *taken* arm and left the risers on the slot
#: table, on the stated grounds that "a riser owns its column down to the
#: collector". That is a claim about a riser needing *a* column, not about needing
#: a column three east of the last one, and the grid says the slots are padding:
#: ``BRZ``'s ``neg`` riser stands six cells east of its ``W`` with nothing between,
#: ``BRN``'s ``zero`` riser three. See :func:`_slab` for what a riser actually
#: constrains.
#:
#: It is the **span**, not the walk, that this is for. Every column of arm width is
#: walked out and back, so the walk pays too, but the arms are only ~11 cells; what
#: moves is :func:`_slab_east_span` 9 -> 6, which walks the whole staircase east of
#: ``BRZ`` west and takes ``struct_east`` with it.
#:
#: **Measured and negative. Kept because the reasoning it replaces was wrong even
#: though the answer was right, and because the price is now a number.**
#:
#: The riser slots are not padding — :func:`_slab_east_span`'s note is what floors
#: them, not the arms. Sweeping ``_SLAB_RISER_SLOTS`` on hires/men-v3:
#:
#: | slots | build | cpu east wall |
#: |---|---|---|
#: | +9/+3 (shipped) | 496x674 | 40 |
#: | +8/+3, +7/+3 | refuses | — |
#: | **+6/+3** | **496x674** | **37** |
#: | +5/+3, +6/+5, +6/+4 | refuses | — |
#:
#: ``+5`` dies on ``collision at (19, 42): '.' vs '<'`` — the *drain block* below
#: the arms, which is five columns wide and hard-puts its ``<`` at ``base + 3`` and
#: ``base + 4``. So how far west a riser may go is a question about the discard
#: block's width, and the answer is ``base + 6``, three west of the slot table's
#: ``+9``. Nothing about "a riser owns its column down to the collector" is doing
#: any work: the two rising arms are genuinely over-spaced, and it still does not
#: pay.
#:
#: Because ``struct_east`` is the CPU's **east wall**, and the §7.1 tie that fixes
#: the pad moves with it. 21 rounds, ``passed=True``, ``fatal=None``:
#:
#: | tier | wall | ``mem_pad`` floor | ticks | Δ |
#: |---|---|---|---|---|
#: | men-v3 shipped | 40 | 3 | 82,530,131 | — |
#: | men-v3 tight | **37** | **10** | 89,493,571 | **+8.437%** |
#: | taped shipped | 40 | 2 | 141,458,930 | — |
#: | taped tight | 37 | **refuses at every pad 0..23** | — | — |
#:
#: **Three columns off the east wall cost seven columns of pad — about 1.2% each.**
#: The pad is monotone above its floor and every binding pad here gives the same
#: 496x674, so 10 is both the floor and the best available: +8.437% *is* the
#: tightened machine's best result, not an unlucky pad. taped will not bind at all
#: (``'s' at (19, 169) must bind 'stream_cmd'``), which is the same wall moving,
#: harder.
#:
#: **Revisit when, and only when, the pad stops being a function of this wall** —
#: if ``mem_x`` is ever pinned by something other than ``lane_x0 + prefixes +
#: pad``, or if the memory block moves off the CPU's east side entirely. Until
#: then the arithmetic is settled: the eastern columns of the CPU are not air, they
#: are bought pad floor, and the same holds for every scheme that pulls
#: ``struct_east`` west (see :data:`PACKED_SLAB_BAND`'s ``seek_jump_gap``, already
#: at 0 on both tiers, and :func:`_slab_east_span`'s **+5.76%** note).
SLAB_TIGHT_RISERS: set[tuple[str, str]] = set()

#: Hang a drained **branch**'s ladder off the taken arm itself, instead of off a
#: ``turn_row`` below the arms. Keyed on ``(slug, tier)``; empty by default, so
#: every machine that does not name itself here is byte-identical.
#:
#: Two rows, and **neither of them is ladder** — which is the point, because the
#: ladder is what does the work. On ``deadman-3d_hires`` men-v3 the ``BRZ`` slab
#: (``base`` 15, ``s0`` 179) is drawn as::
#:
#:     180  neg arm      W . . . . . ^          (riser at base + 9)
#:     181  taken arm    W b v                  (`v` at base + 4)
#:     182  pos arm    > W ^ .                  (riser at base + 3)
#:     183  turn_row       . . <                (walks back west to base + 3)
#:     184..197           the 14-row block, its `]` on 184
#:
#: The taken arm drops onto ``base + 4`` and then walks *back west* to the block's
#: spine, which is one column west of it. Give the block the arm's own column and
#: the walk is nobody's: the man keeps falling, ``turn_row`` stops existing, and
#: the ladder's ``even`` ``]`` — a bare pass-through on the spine, one cell wide —
#: lands on ``s0 + 3`` beside the ``pos`` arm's riser rather than on a row of its
#: own. The block ends on 195 instead of 197, and the taken arm reaches the first
#: fold in 2 cells instead of 5.
#:
#: **The price is a one-column east shift, and this is the direction that is
#: free.** Moving the CPU's east wall *west* has cost twice — :data:`SLAB_TIGHT_RISERS`
#: at +8.437%, ``TRIE_SLACK_ROWS``' ``lane_x0`` at +1.97% — both because
#: ``struct_east`` is the wall and the §7.1 tie that fixes ``mem_pad`` moves with
#: it. Here the block grows east *inside* a slab that already reaches ``base + 9``
#: for its ``neg`` arm, so :func:`_slab_east_span` returns the same 9 it did
#: before and the staircase, ``struct_east``, the pad and the drop solver all see
#: an unchanged band. Verified rather than assumed: the built box is unchanged in
#: width.
#:
#: Requires :data:`SLAB_TIGHT_ARMS` — the taken arm has to already be on
#: ``base + spine``, which is what ``tight_arms`` puts it at — and applies only to
#: branches. A drained *jump* arrives heading west along its entry row with no arm
#: to fall off, so it keeps the ``turn``-and-hang entry and its own column.
#:
#: 21 rounds, ``frame_tiles=(2, 2)``, ``passed=True``, ``fatal=None`` on both:
#:
#: | tier | box | ticks | Δ |
#: |---|---|---|---|
#: | men-v3 before | 496x674 | 81,309,610 | — |
#: | **men-v3 after** | **496x674** | **81,042,708** | **-0.328%** |
#: | taped before | 625x400 | 140,656,599 | — |
#: | **taped after** | **625x398** | **140,379,566** | **-0.197%** |
#:
#: Note *where* the two tiers take it. men-v3's box does not move at all — its
#: height is the display's and its width the ROM's — so every tick is walk: three
#: off each taken branch, and two off the exit riser of every slab and of the seek
#: tail, whose ``taken_row`` is ``bottom + 1`` and rises with the band. taped's
#: box loses the two rows outright, because nothing below the band was holding
#: them. Both are the third category the ``FETCH_TUCK`` round opened: a shorter
#: walk over cells already drawn, moving no wall and owing no pad.
SLAB_TUCKED_DRAIN: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
    ("deadman-3d_hires", "taped"),
}

#: Per-``(slug, tier)`` opt-in for **drop columns floored by operations rather than
#: by cells** — the simple lanes' half of what :data:`TIGHT_STRUCT_DROPS` does for
#: the structured ones.
#:
#: A simple lane returns by turning south at ``drop_x[r]``, and that column crosses
#: every row beneath it, so the default rule floors it at the suffix maximum
#: ``max(lane_end[q] for q >= r) + 1``. ``lane_end`` is the lane's **last cell** —
#: and a lane's cells are not all operations. :func:`_flat_lane` pushes a band's
#: first glyph out to its column and pads the gap with ``.``, so a lane with a
#: memory block at ``mem_x`` is ``>`` ``M`` then a dozen inert cells then
#: ``s r W N s ...``. The suffix maximum sees only the eastmost ``s``; it cannot
#: see the hole in the middle.
#:
#: ``.`` has no direction. A southbound man crosses it unchanged, exactly as the
#: eastbound lane man does, which is the fact ``tests/test_lm1_cpu_trie_pack.py``
#: pins for the decode trie and the drop comment above has always relied on for the
#: descent's own body. So a drop may sit **inside** a lane's padding run: the
#: constraint is per column, not per row, and ``floor`` is only its envelope.
#:
#: Measured on ``deadman-3d_hires``, whose band has exactly two rows with slack —
#: the two the eye picks out of the grid, because every other lane already ends
#: within a column or two of its turn. Both land on column 17, sharing it with the
#: six immediate lanes that were already there:
#:
#: | lane | last op | drop before | drop after | execs / 21 rounds | ticks |
#: |---|---|---|---|---|---|
#: | `IN` (``>rM``) | 15 | 38 | **17** | ~4,700, all boot | **-170,772** (-0.089%) |
#: | `DIVI` (``>W/M``) | 16 | 33 | **17** | ~21,000, per frame | **-757,984** (-0.396%) |
#: | both | | | | | **-928,756** (-0.485%) |
#:
#: Against a 21-round baseline of 191,601,893 ticks at 649x388, both variants
#: ``passed``, and the two are exactly additive because a column is worth precisely
#: two ticks per execution — the walk east to the turn and the walk back west along
#: the collector — and nothing else on the critical path moves. ``IN`` is a boot
#: lever (it reads the whole 4,676-word preamble and then two words a round), so it
#: reads ~3.5x larger on a short tour; ``DIVI`` is per-frame and does not.
#:
#: Nothing else moves geometrically either: ``ret_x`` and therefore the CPU's width
#: are set by the structured drops out at 47..50, which this does not touch, so the
#: box, every pipe binding and every block placed against the CPU stay where they
#: were — 649x388 before and after.
#:
#: What it does **not** relax is the reason simple and structured columns must stay
#: disjoint: a slab entry leaves ``.`` on the collector row and a ``<`` that turns
#: an arriving man west, so a simple man sharing that column sails past his turn and
#: is swallowed. ``struct_cols`` still refuses those columns explicitly, and the
#: ``clash`` check below the drops still fails the build if one ever gets through.
#:
#: **The men-v3 tier takes it too, and it is worth more there**: 134,144,756 ->
#: 133,273,840 on the 21-round tour, **-0.649%**, ``passed``, 595x630 unchanged.
#: Exactly the same two lanes move — ``IN`` (``>rM``) 43 -> 25 and ``DIVI``
#: (``>W/M``) 38 -> 25 — because what creates them is the shape of the *program*,
#: not of the tier: a three-glyph lane sitting inside the memory band's row range,
#: with a dozen columns of nobody's padding to its east. The tier only changes how
#: far they fall. It composes with :data:`FOLDED_LANES` below and the two are very
#: nearly additive, moving disjoint lanes.
#:
#: Empty by default, so every machine not named here is byte-identical. Requires the
#: short-return drop rule; orthogonal to the seek drum and to ``tight_drops``, since
#: it only ever moves a *simple* lane's column.
TUCKED_DROPS: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: Per-``(slug, tier)`` opt-in for lanes that run their micro-program **south**:
#: :func:`_fold_cut`'s vertical fold.
#:
#: A lane has always been drawn as a row — glyph after glyph, east — and then a
#: ``v``, and then a descent of ``.`` to the collector. But a man executes whatever
#: cell he steps on, and he was going to walk that descent anyway, so the glyphs
#: after the last band-anchored one can go **down the drop column instead of along
#: the row** for nothing. Every column that buys back is worth two ticks on every
#: execution of that opcode: the walk east to the turn, and the walk west along the
#: collector — the same arithmetic :data:`TUCKED_DROPS` records.
#:
#: The price is that the column stops being shareable. A tail in it is an
#: *operation*, so a second lane's drop crossing it would execute this lane's
#: micro-program, and this lane's ``v`` may not stand where a neighbour's already
#: is. So a folded column is exclusive and the floor above it is ``c + 1`` rather
#: than ``c``. That is exactly one column, which is why the drop loop folds **only
#: when the fold saves at least one**: at a saving of one the lanes above break even
#: and this lane gains, at two or more everyone gains, and at zero the glyphs go
#: back on the row.
#:
#: On ``deadman-3d_hires`` men-v3, against a rebuilt 134,144,756 at 595x630 (21
#: rounds, ``frame_tiles=(2, 2)``, ``passed``, ``fatal=None``):
#:
#: | lane | micro | ops | ``lane_end`` | ``drop_x`` | folded south |
#: |---|---|---|---|---|---|
#: | `LD` | `s r M` | 3 | 37 -> **36** | 38 -> **37** | `M` at (37, 164) |
#: | `MUL` | `s r * M` | 4 | 38 -> **37** | 39 -> **38** | `M` at (38, 161) |
#: | `MOVA` | `s r W N s W s M` | 8 | 42 -> **41** | 43 -> **42** | `M` at (42, 152) |
#:
#: Three lanes, one column each, and **nothing else in the band moves** — every
#: other simple lane is floored by a neighbour below rather than by its own last
#: glyph, so shortening it buys nothing and the fold declines. ``LD`` is what pays:
#: it is 19% of the ~885,000 instructions on the tour. **133,783,354, -0.269%**
#: alone; with :data:`TUCKED_DROPS` above, **132,912,438, -0.919%**, and the two are
#: very nearly additive (-0.918% predicted) because a column is worth two ticks and
#: they move disjoint lanes.
#:
#: The profile says the win landed where the argument put it (native
#: ``FastLittleman``, ``profile=True``, ``profile_stride=17``, same 21-round tour,
#: heat summed over ``cpu:*`` regions — the CPU is one runner, so its samples over
#: ``profile.samples`` is the fraction of the run it spent there):
#:
#: | region | heat before | heat after | t/instr before | t/instr after |
#: |---|---|---|---|---|
#: | `cpu:return:collector` | 16.91% | **16.61%** | 25.77 | **24.94** |
#: | `cpu:return:riser` | 5.90% | 5.97% | 8.99 | 8.96 |
#:
#: All of it in the collector and none in the riser, which is the check that the
#: model is right rather than the number: the riser is ``collector - centre`` cells
#: and does not know what ``drop_x`` is. Both stay 0.00% blocked throughout — this
#: is walking, not waiting, which is why it scales with columns at all.
#:
#: **It does not narrow the CPU, and the reason is not the lanes.** The box is
#: 595x630 before and after and the collector is 49 cells wide both times, because
#: ``ret_x`` is set by the *structured* drops — floored at ``struct_east + 1``, the
#: seek slab band's east edge — which no lane fold can reach. The tight-entry
#: registry is the lever that would, and ``build_cpu`` refused it under the seek
#: drum when this was written.
#:
#: It does not any more, and both halves of that last sentence turned out to be
#: worth re-reading. The refusal was an untested assumption and is gone
#: (:data:`SEEK_TIGHT_STRUCT_DROPS`, -5.84% on this tier), and the drop columns
#: quoted here as 55..58 are the *last* pad the search tried, not the one it
#: shipped: at the winning ``mem_pad`` of 10 they were 47..50 and ``ret_x`` was 50.
#: The mechanism above is unaffected — read ``build_for(...).regions``, not a
#: traced ``build_cpu`` call, when quoting a column.
#:
#: **The taped tier takes it too, at -0.21%** — 175,167,094 -> 174,808,400 on the
#: 21-round tour, against men-v3's -0.27%. The smallest of the five and the one
#: that transfers most nearly unchanged, because what it folds is a property of the
#: *program*'s micro-programs rather than of the tier.
#:
#: Empty for everything else, so every machine not named here is byte-identical.
#: Requires the short-return drop rule and no top bus.
FOLDED_LANES: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

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
#:
#: **The declines above are still correct and were still the wrong question**: hires
#: takes pitch 11 under the drum, where it is worth -8.85% — see
#: :data:`SEEK_SLAB_PITCH`. What it buys there is not a column of box but the
#: ``mem_pad`` floor, which the classic build does not have a problem with (it binds
#: at 15 either way) and the seek build does (35 at pitch 13). The two registries stay
#: apart for exactly the reason the split exists.
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
#:
#: **``deadman-3d_hires`` is the largest thing in this registry and it is not a width
#: knob at all.** :data:`SLAB_PITCH`'s note above declines the same slug twice on the
#: grounds that hires' width is the router wall's, not its CPU's, so there is nothing
#: to trade CPU columns *into*. That is still true and still the wrong question, which
#: only became visible under the drum: the payoff is not the column, it is the
#: ``mem_pad`` **floor**. A seek hires at the default pitch 13 cannot bind below 35
#: (the classic build binds at 15), and every one of those twenty columns is walked
#: twice by every memory instruction. Pitch 12 -> pad 28, pitch 11 -> pad 18, which
#: with :data:`INPUT_NORTH_WEST` reaches 15. On the 21-round tour, everything else at
#: the shipped values: 256,325,066 at pitch 13 against **233,658,800** at 11,
#: **-8.85%** — an order of magnitude more than the -1.44% the registry was built for,
#: and 87% of everything hires' seek build gains beyond the bare drum.
#:
#: Which is the *opposite* sign to the reasoning ``deadman-3d`` needed above, where a
#: narrower CPU was a closer rival and pitch 11 could not bind at ``MEM_PAD`` 17 at
#: all. Both readings are real; the rival that binds is not the same one on the two
#: machines (hires is :data:`INPUT_NORTH`-fed with a 2x2 router wall east of
#: everything), so this is a per-slug measurement and not a rule.
SEEK_SLAB_PITCH: dict[str, int] = {"deadman-3d": 11, "deadman-3d_hires": 11}

#: Step the slab staircase by **what each slab draws** instead of by one uniform
#: :data:`SEEK_SLAB_PITCH`. Keyed on ``(slug, tier)``; empty by default, so every
#: machine that does not name itself here is byte-identical.
#:
#: The pitch is floored at :data:`_SLAB_PITCH_FLOOR` = 11, "the eleven columns a
#: branch slab actually occupies". That floor is exactly right and it is a floor on
#: the wrong thing: it is uniform, and the band is not. On ``deadman-3d_hires`` the
#: four structured opcodes are two branches and two jumps, and the jumps are almost
#: free (:func:`_slab_east_span`):
#:
#: | slab | kind | columns it draws | pitch it was given |
#: |---|---|---|---|
#: | ``JMPS`` | seek jump | **none** — :data:`SEEK_TAKEN_DROP_EAST` turns it south at ``struct_east + 1`` | 11 |
#: | ``JMPF`` | classic jump | 3 (``base - 1`` riser, ``a<``) | 11 |
#: | ``BRZ`` | classic branch | 11 | 11 |
#: | ``BRN`` | classic branch | 11 | — |
#:
#: So the shipped band spends 22 columns on two slabs that draw 3 between them, and
#: the whole staircase east of ``JMPS`` sits 17 columns further east than the
#: geometry needs. Packed, ``struct_x0`` 2 gives bases 2 / 4 / 7 / 18 against the
#: shipped 2 / 13 / 24 / 35, and ``struct_east`` 29 against 46.
#:
#: Which is worth ticks in three places, all of them walks:
#:
#: * the **seek send site** ``e_s = struct_east + 2`` and the westbound corridor
#:   back from it are each one ``struct_east`` long, walked by every seek
#:   instruction;
#: * a structured lane's **entry row**, from its drop column west to ``base`` —
#:   under :data:`SEEK_TIGHT_STRUCT_DROPS` the drop follows ``base`` west, and
#:   without it the drop is floored at ``struct_east + 1`` and follows that;
#: * the **collector** back to the riser, which is the same column, twice.
#:
#: It is not free of §7.1: it walks the deepest slab's discard ``r`` west, which is
#: the CPU's east wall, and :data:`SEEK_SLAB_PITCH`'s note is the record of how
#: sharply that cuts both ways on the two machines. Re-derive ``mem_pad`` per tier.
#:
#: **The value is the seek jump's gap** (:func:`_slab_east_span`), and it is the one
#: number here that had to be swept rather than derived. Packing the band flat costs
#: ``JMPS`` its :data:`SEEK_TAKEN_DROP_EAST` column: the turn has to be free all the
#: way down to the taken row, so it may not be inside another slab, and a flat band
#: leaves only ``base``. Two columns beside it are enough to land the turn near the
#: drop again, and they are not paid twice — the whole staircase east of ``JMPS``
#: moves with them.
#:
#: **Both tiers want 0 anyway, and the reason it is still a parameter is that the
#: 3-round triage says otherwise.** men-v3, everything else at the shipped values:
#:
#: | gap | box | 3-round | 21-round |
#: |---|---|---|---|
#: | **0** | **577x630** | 10,087,912 | **93,901,187** |
#: | 1 | 578x630 | 10,107,480 | 94,195,567 |
#: | 2 | 579x630 | **10,037,298** (-0.50%) | 94,126,993 (+0.24%) |
#: | 3 | 580x630 | 10,110,374 | 94,775,023 (+0.93%) |
#: | 4 | 581x630 | 10,112,640 | — |
#: | 6 | 583x630 | 10,232,532 | — |
#: | 9 (a branch's own span) | 586x630 | 10,288,652 | — |
#:
#: Gap 2 wins the triage by half a point and loses the tour by a quarter of one.
#: Three rounds is boot plus the title plus one corridor frame, and the U-turn this
#: buys back is paid per taken ``JMPS`` — a rate the early frames do not sample. On
#: taped the sweep is flat-to-monotone the other way (187,002,731 at 0 against
#: 190,456,633 at 3) because taped's ``JMPS`` never lost its column: its drop sits
#: east of the whole band, so ``jump_x`` is reachable at any gap and every column
#: added is dead width.
PACKED_SLAB_BAND: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "men-v3"): 0,
    ("deadman-3d_hires", "taped"): 0,
}

#: Draw the collector's ``<`` only where a man **arrives** on it, and ``.``
#: everywhere else. Keyed on ``(slug, tier)``; empty by default, so every machine
#: that does not name itself here is byte-identical.
#:
#: The row is a solid ``<`` run from column 3 to ``ret_x`` — 43 of them on
#: ``deadman-3d_hires`` men-v3, in four runs with ``.`` holes punched at the four
#: columns a slab lane's drop passes through. Only about a quarter of those turn
#: anybody: a westbound man keeps his heading over a ``.`` just as well, so the
#: cells that have to be ``<`` are the ones where a man arrives *facing another
#: way* — a simple lane's drop coming south, a slab riser coming north, and the
#: spawn's first step east out of ``@``.
#:
#: **It is worth zero ticks and that is the point.** ``<`` and ``.`` are both one
#: tick to walk over, so nothing on the return path changes; what changes is that
#: the row stops *claiming* forty-odd columns. A ``<`` turns anything that steps on
#: it, which is why a drop crossing the collector needs its ``.`` — and the builder
#: already gets that right by drawing the drops first and the ``<``\ s ``soft``, so
#: the relaxation unlocks nothing the current drop solver was fighting. What it
#: removes is the *reason* it would have to fight: any future descent through this
#: row is legal by construction rather than by draw order.
SPARSE_COLLECTOR: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
    ("deadman-3d_hires", "taped"),
}


def _drained(mnemonic: str, unit_bits: int, ops: tuple[str, ...] | None) -> bool:
    """Does this classic slab get the ladder+loop rather than the counted loop?

    ``ops is None`` means every classic slab, which is what the non-seek build
    has always done. A tuple restricts it, because on a seek band the choice is
    forced by §7.1 rather than by ticks: see :data:`SEEK_CLASSIC_DRAIN`.
    """
    return bool(unit_bits) and (ops is None or mnemonic in ops)


def _drain_block(
    g: _Grid,
    base: int,
    y: int,
    pipe_glyphs: list[tuple[int, int, str, str]],
    unit_bits: int,
    tuck: bool = False,
) -> int:
    """Place a :mod:`.drain` ladder+loop in a slab. Returns the row it leaves on.

    Same contract as :func:`_discard_loop`: the man arrives heading **west** along
    ``y`` and leaves heading **west** with ``BP == 0``, on the caller's riser
    column ``base - 1``. He just leaves further down, because the block hangs
    below the entry row instead of beside it.

    ``tuck`` is the *branch* entry instead, and it arrives heading **south**. The
    ladder's first cell under ``even`` is a bare ``]`` on the spine, which is a
    pass-through for a southbound man — so a taken arm that drops down the spine
    column walks onto it without a turn, and the block starts on row ``y`` rather
    than on ``y + 1`` below a ``turn_row`` that no longer exists. Two rows, and
    neither of them is ladder: see :data:`SLAB_TUCKED_DRAIN`.

    The price is a **one-column east shift**. The spine has to be the column the
    arm is already falling down (``base + 4`` under :data:`SLAB_TIGHT_ARMS`), and
    the spine is at local ``4``, so local ``0`` lands on ``base`` and the block
    leaves one column east of the riser. The exit row's westward run is extended
    by that one cell — the block leaves local ``0`` empty for exactly this — and
    the caller's ``^`` stays at ``base - 1``, so nothing outside the slab has to
    know. A branch already reaches ``base + 9`` for its ``neg`` arm, so the wider
    block is still nowhere near the slab's east edge and :func:`_slab_east_span`
    is unchanged in practice (it is corrected anyway, so the staircase does not
    depend on that happening to be true).
    """
    from .drain import build_drain

    block = build_drain(0, unit_bits=unit_bits, even=True)
    if tuck:
        # Local column 0 is one east of the riser; the man walks the extra cell.
        ox, oy = base, y
        exit_row = oy + block.exit[1]
        g.soft(ox, exit_row, ".")
    else:
        ox, oy = base - 1, y + 1  # local column 0 is the reserved exit column

        # Turn the westbound man south into the block. On a branch's ``turn_row``
        # the arm's westward run is already drawn, and on a jump's entry row it is
        # drawn afterwards — so this cell is `.` or `<` or empty, all three meaning
        # "the man is walking west here", which is precisely who we want to divert.
        # Anything else is a real collision and must not be papered over.
        turn = (ox + block.spine, y)
        if g.c.get(turn) not in (None, ".", "<"):
            raise MachineError(f"drain entry at {turn} would overwrite {g.c[turn]!r}")
        g.c[turn] = "v"
    for (bx, by), ch in block.cells.items():
        g.put(ox + bx, oy + by, ch)
        if ch == "r":
            pipe_glyphs.append((ox + bx, oy + by, "r", "rom"))
    assert ox + block.exit[0] == base - (0 if tuck else 1), (
        "the block must leave on the riser column"
    )
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
#: The same protocol with the return leg *forked off* instead of walked: 4x8,
#: and — the only thing that buys ticks — two cells shorter on the **forward**
#: leg, the one the CPU is stopped on.
#:
#: The shipped block is a single man who receives, expands the request into two
#: or three words, and then walks all the way home. Only the expansion is on the
#: critical path; the walk home happens inside the next request's idle gap (the
#: adapter man measures 89% blocked, mean 216 ticks between requests). So the
#: fold that pays is not shortening the walk — a previous agent measured that at
#: exactly zero — but deleting the ``M``/``W`` register shuffle that the *one*
#: man needed to hold both the op and the address at once::
#:
#:     shipped read   U X > M 0 s W s      address into the pipe on the 8th tick
#:     forked  read   U X Y v < s          ... on the 6th, and the op on the 5th
#:
#: ``Y`` gives two men who each carry their own copy of the request word, so one
#: says ``0`` and sends it while the other sends the word unmodified. One of the
#: two then walks home and the other ``H``alts, which is what keeps the
#: population stationary: one man in at ``U``, one man out per lap.
#:
#: ``X`` is entered heading **east** exactly as it is in the shipped block —
#: ``U`` turns away from the west wall's pipe — so ``sign(A)`` still puts the
#: **read** arm (``A > 0``, clockwise, south) below the branch row and the
#: **write** arm (``A < 0``, counter-clockwise, north) above it. Both arms land
#: on a ``Y`` one cell off the branch, and the two children of each split divide
#: the shipped man's job between them::
#:
#:     read   (south ``Y``, entered heading south)
#:            west child, born first:  ``0`` ``s`` ``H``   — op 0 into the pipe, done
#:            east child, born second: ``v`` ``<`` ``s``   — the word is already the
#:                                                           address; send it and go home
#:     write  (north ``Y``, entered heading north)
#:            east child, born first:  ``1`` ``s`` ``H``   — op 1 into the pipe, done
#:            west child, born second: ``N`` ``s`` ``r`` ``v`` ``>`` ``s``
#:                                                         — negate to the address, send,
#:                                                           take the value, pass it on
#:
#: **Send order comes out right on both arms for the same reason, and it is not
#: a coincidence to be relied on twice.** SPEC: the order-preserving child is the
#: one born to the *right* of the entry heading and it keeps the parent's
#: creation-order slot, so it executes first within a tick; the other is the
#: newest runner and acts after every existing one. Right-of-south is **west**
#: and right-of-north is **east** — which on both arms is precisely the child
#: holding the op. So when the two ``s`` glyphs fall on the same tick, the op
#: wins the pipe's source cell and the address blocks one tick behind it, which
#: is the order the tape's wire protocol wants.
#:
#: Man accounting is stationary by construction: one man enters at ``U``, ``Y``
#: makes two, exactly one of the two walks into an ``H``, and exactly one comes
#: back to ``U``. ``H`` halts *that little man* and not the program (SPEC
#: §Control flow), and ``Y`` is the sanctioned way to have two men in one room —
#: "at most one ``@`` per room" is a rule about the *glyph* at load time.
#:
#: Neither return leg passes back through ``X``, so nothing has to zero ``A`` to
#: stop a returning man being deflected by the address or the value he carries:
#: the read arm comes up column 3 and the write arm along row 1, both arriving at
#: ``U`` from a side ``X`` is not on. The ``0`` at (3,2) is the read arm's *op*,
#: which the returning man merely walks over.
#:
#: The height is unchanged at 4, so ``floor_y = AY + ADAPTER_H + 1`` does not
#: move and the store's request drop below it is untouched, cell for cell.
_ADAPTER_FORK = [
    "vrsNY1sH",  # write arm: <- N s r v | Y | 1 s H ->
    ">s@UX   ",  # spawn, receive, branch on sign; the write arm's value passes here
    " Hs0Yv  ",  # read arm:  <- H s 0   | Y | v ->
    "   ^s<  ",  # the read arm's send-and-return leg, back up column 3 into ``U``
]
#: The **v4** adapter: the CPU's one word in, **one** word out.
#:
#: The CPU already sends a single word that carries both facts — ``+addr`` for a
#: read, ``-addr`` for a write. The two-word adapter's whole job was to throw
#: that encoding away and expand it; this one re-encodes it instead, into the
#: store's own one-word wire ``2*addr - op`` (see
#: :data:`~..memory_taped.TAPE_PROTOCOLS`). Nothing downstream has to expand it
#: back until the bank's own doorstep.
#:
#: The read arm is **one cell** and no fork::
#:
#:     shipped read   U X > M 0 s W s     op on tick 5, address on tick 8
#:     forked  read   U X Y v < s         op on tick 5, address on tick 6
#:     v4      read   U * X s             the whole request on tick 4
#:
#: ``*`` with ``2`` parked in B is the doubling, one glyph — which is why B is
#: parked at all.
#:
#: **It doubles before the branch rather than on the read arm, and that is a
#: whole tick off every access the machine makes.** ``X`` splits on ``sign(A)``
#: and ``*`` by a positive constant does not touch a sign, so the doubling is
#: legal on the spine: the CPU's ``+a``/``-a`` becomes ``+2a``/``-2a`` and the
#: branch is the branch it always was. What that deletes is not the ``*`` — both
#: arms still pay for it — but the read arm's **turn**: with the multiply on the
#: spine the read's ``s`` is the cell ``X`` drops him onto, where before he had
#: to land on a ``>``, walk east to the ``*`` and only then send. The write arm
#: is unchanged glyph for glyph (``M 1 + N`` still turns ``-2a`` into ``2a - 1``)
#: and it is not the leg the CPU is stopped on.
#:
#: The return floor reloads the ``2`` (``2`` then ``M``, met in that order walking
#: west), and because the spawn stands *on* the return floor the very first
#: request already finds it there. **Only the write arm walks the reload**, which
#: is the other half of why this shape is cheaper: ``*`` leaves B alone, and a
#: read's whole lap — ``U * X s`` and four cells home — never clobbers the parked
#: constant, so it need never re-park it.
#:
#: The write arm pays for the odd word: ``* M 1 + N`` is ``-a`` -> ``-2a`` ->
#: ``1 - 2a`` -> ``2a - 1``. That is two cells more than the fork's write arm
#: and it is on the leg the CPU is **not** stopped on — it blocks on reads only.
#:
#: ``X`` is entered heading east (``U`` turns away from the west wall's pipe), so
#: ``sign(A)`` puts the read arm (``A > 0``, clockwise) below the branch row and
#: the write arm above it, exactly as in the shipped block. Neither return leg
#: passes back through ``X``.
#:
#: The read's return drops out of the arm one cell after its ``s`` rather than
#: walking the block's width, which puts the whole read lap at twelve ticks
#: against the fork's eight — the fork gets its short lap by ``H``alting one of
#: its two men, and one word needs no second man to halt. That is **+5.12 ticks
#: per access of service time** and it costs nothing: the adapter is 89%
#: blocked, and lengthening or shortening this leg leaves the tour on the same
#: tick either way (both shapes were built and run). What is on the wire is the
#: forward leg, and that is five ticks for one word instead of five and six for
#: two.
_ADAPTER_V4 = [
    "..>M1+Nsrsv",  # write: B=-2a; A=1; A=1-2a; A=2a-1; send; pass the value
    "U*X.......v",  # receive, double, branch on sign — `*` cannot change one
    "..s.......v",  # read: the wire word is already in A, so this is the whole arm
    "^.<M2....@<",  # return leg; only the write walks past the reloaded 2
]
ADAPTER_W = len(_ADAPTER[0])
ADAPTER_H = len(_ADAPTER)
ADAPTER_IN_ROW = 2  # west wall: the request pipe from the CPU
ADAPTER_OUT_ROW = 2  # east wall: the expanded request out to the tape

#: The adapter shapes :func:`adapter_rows` will hand out. ``wide`` is the shipped
#: 12x4 block and stays the default; ``fork`` is :data:`_ADAPTER_FORK` and
#: ``v4`` is :data:`_ADAPTER_V4`, which speaks a **different wire** and is
#: therefore only legal against a ``v4`` store block.
ADAPTER_FORMS = ("wide", "fork", "v4")


def adapter_rows(*, address_first: bool = False, form: str = "wide") -> list[str]:
    """The adapter's interior, as text rows.

    ``form`` selects the shape. ``fork`` and ``v4`` have no ``address_first``
    variant: only ``men-y`` wants the address first and only ``taped`` forks or
    packs.
    """
    if form not in ADAPTER_FORMS:
        raise MachineError(f"unknown adapter form {form!r}; expected {ADAPTER_FORMS!r}")
    if form in ("fork", "v4"):
        if address_first:
            raise MachineError(f"no address-first variant of the {form} adapter")
        return _ADAPTER_FORK if form == "fork" else _ADAPTER_V4
    return _Y_ADAPTER if address_first else _ADAPTER


def adapter_cells(
    *, address_first: bool = False, form: str = "wide"
) -> dict[tuple[int, int], str]:
    """The adapter's interior cells, local (1,1)-based."""
    out: dict[tuple[int, int], str] = {}
    rows = adapter_rows(address_first=address_first, form=form)
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

#: How far :func:`tape_block`'s ``west_grow`` may carry the worker's west wall, and
#: the number is exact rather than cautious. The wall stands at ``_TAPE_WX - 1``, the
#: stub is the two cells west of it, and a caller's pipe has to have a **source**
#: cell east of the caller's own east wall — which for
#: :func:`~..memory_taped.taped_store_block` is the block's column 0. At 4 the wall
#: is at column 3 and the stub is columns 1 and 2, so the whole feed is the block's
#: own two-cell stub and the pipe between the rooms draws nothing at all; at 5 the
#: stub would start on column 0 and overdraw the feed room's east wall.
_TAPE_WEST_GROW_MAX = 4


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


def _tape_worker_spec(skip_batch: int, protocol: str = "v3", rotate: bool = False):
    """Return the worker and wall anchors for one tape skip implementation.

    ``protocol="v4"`` on the **batch-1** worker returns that body's own room and
    its own four wall anchors: it stands three columns west of the v3 body it
    used to share a room with, and the request stub and answer riser move with it
    (``memory_tape.V2_V4_SHIFT`` says why none of the four is fixed). Batch 2 is
    untouched, and so is every v3 caller.

    ``rotate`` selects the **third** body, ``memory_tape.worker_v2_rot`` — the
    batched ring that skips the rotational delta instead of the address, and
    whose head is kept one room upstream in ``memory_taped.feed_rotate``. It
    returns the batched body's room and all four of its anchors unchanged, which
    is the whole point: the shell, both ring pipes, the request stub and the
    answer riser are byte-identical, so the only cells in the machine that move
    are inside the worker's own walls. It is packed-wire and batch-2 only.
    """
    if rotate:
        if skip_batch != 2 or protocol != "v4":
            raise ValueError(
                "the rotating worker is the batched packed-wire body only, not "
                f"skip_batch={skip_batch} protocol={protocol!r}"
            )
        from ..memory_tape import (
            V2_IN_ROW,
            V2_JUMP_FWD_ROW,
            V2_JUMP_RET_COL,
            V2_OUT_COL,
            V2_ROT_IH,
            V2_ROT_IW,
            worker_v2_rot,
        )

        return (
            worker_v2_rot,
            V2_ROT_IW,
            V2_ROT_IH,
            V2_IN_ROW,
            V2_OUT_COL,
            V2_JUMP_FWD_ROW,
            V2_JUMP_RET_COL,
        )
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
        if protocol == "v4":
            from ..memory_tape import (
                V2_V4_IN_ROW,
                V2_V4_IW,
                V2_V4_OUT_COL,
                v4_ret_col,
            )

            # ... and the return column is the one anchor this body chooses for
            # itself: see ``memory_tape.V4_P1_RING_RET_COL``, where moving the
            # wall two columns west is what lets the ring keep the full shift.
            return (
                worker_v2,
                V2_V4_IW,
                V2_IH,
                V2_V4_IN_ROW,
                V2_V4_OUT_COL,
                V2_FWD_ROW,
                v4_ret_col(),
            )
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
        if protocol == "v4":
            from ..memory_tape import (
                V2_JUMP_V4_IH,
                WORKER_JUMP_V4_POST_PAD,
                _JUMP_V4_RETURN_LEFT,
            )

            return (
                worker_v2_jump,
                V2_JUMP_IW,
                (V2_JUMP_V4_IH + WORKER_JUMP_V4_POST_PAD)
                if _JUMP_V4_RETURN_LEFT
                else V2_JUMP_IH,
                V2_IN_ROW,
                V2_OUT_COL,
                V2_JUMP_FWD_ROW,
                V2_JUMP_RET_COL,
            )
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
    park_const: bool = False,
    west_grow: int = 0,
    protocol: str = "v3",
    rotate: bool = False,
) -> tuple[Circuit, tuple[int, int], tuple[int, int]]:
    """The worker room and the two CPU-facing pipe stubs — the part no ring changes.

    Shared by both ring layouts so neither can drift from the other. What fixes every
    ``r``/``s`` binding *inside* the worker is the worker's four wall anchors, not the
    shape of the ring: a ring may be routed any way at all so long as it uses the
    selected worker's forward-row and return-column anchors. That is the licence the
    serpentine uses.

    ``west_grow`` moves the worker room's **west wall** that many columns further
    west and carries the request stub with it, so the block's ``in_cell`` lands at
    ``WX - 3 - west_grow``. Nothing else moves by one cell: the art, the three other
    walls, both ring anchors and the response stub are all measured from ``WX``/``WY``
    and the block's width is set by its east edge. See :func:`tape_block` for what it
    is for and why 4 is the ceiling.

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
    ) = _tape_worker_spec(skip_batch, protocol, rotate)

    if not 0 <= west_grow <= _TAPE_WEST_GROW_MAX:
        raise ValueError(
            f"west_grow {west_grow} is not in 0..{_TAPE_WEST_GROW_MAX}: at "
            f"{_TAPE_WEST_GROW_MAX} the request stub already starts on the block's "
            f"first column"
        )
    g = Circuit(400, 200)
    if protocol != "v3":
        wk = worker(n, park_const=True, protocol=protocol)
    elif park_const:
        wk = worker(n, park_const=True)
    else:
        wk = worker(n)
    WX, WY = _TAPE_WX, _TAPE_WY
    west = -1 - west_grow  # the west wall's column, relative to WX
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(west, worker_width + 1):
        g.set(WX + x, WY - 1, "+" if x in (west, worker_width) else "-")
        g.set(
            WX + x,
            WY + worker_height,
            "+" if x in (west, worker_width) else "-",
        )
    for y in range(worker_height):
        g.set(WX + west, WY + y, "|")
        g.set(WX + worker_width, WY + y, "|")

    # request stub: two cells pointing east into the worker's left wall. It stays
    # on ``V2_IN_ROW`` under every protocol, and the v4 body's MAIN sitting a row
    # south of it is deliberate rather than an oversight — see
    # ``memory_tape.V2_V4_MAIN_ROW``, where moving the *stub* as well is what
    # breaks P1.
    iy = WY + input_row
    g.set(WX + west - 2, iy, ">")
    g.set(WX + west - 1, iy, ">")
    # response stub: two cells climbing north out of the worker's top wall
    ox = WX + output_col
    g.set(ox, WY - 2, "^")
    g.set(ox, WY - 3, "^")
    return g, (WX + west - 2, iy), (ox, WY - 3)


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


#: How far west the return pipe's middle leg turns back before it runs out to the
#: worker's own return column. It is a **fold**, so a bigger one is a *shorter*
#: ring, and the ring's capacity is its cell count (``SPEC.md``: a pipe is a FIFO
#: whose capacity equals its length). ``0`` — the longest — is what
#: :func:`tape_block` has always taken, because it searches in this order and
#: stops at the first fold with room for ``n + 1`` values, and fold 0 always has
#: the most room. Every other entry was therefore unreachable; ``tight_ring``
#: is what makes them mean something, and the list runs out to 22 because that is
#: where both workers' folds bottom out (batch 1 at a 82-cell ring, batch 2 at
#: 110, against 108 and 154 unfolded).
_TAPE_FOLDS = (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22)


def tape_block(
    n: int,
    *,
    skip_batch: int | None = 1,
    jump_threshold: int = 128,
    relay_size: tuple[int, int] | None = None,
    park_const: bool = False,
    tight_ring: bool = False,
    protocol: str = "v3",
    west_grow: int = 0,
    rotate: bool = False,
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

    ``west_grow`` moves the worker room's **west wall** that many columns west and
    carries the two-cell request stub with it, so the block's ``in_cell`` moves from
    column 5 to ``5 - west_grow``. Nothing else in the block moves by one cell: the
    art, the relay, both ring pipes and the response stub are all measured from the
    worker's own corner, and the block's width comes off its east edge.

    **The point is that those columns are empty and the pipe that ends on the stub is
    not.** A banked caller (:func:`~..memory_taped.taped_store_block`) parks a
    forwarder room in the corridor west of each bank and runs a pipe from its east
    wall across the block's west margin to the stub — six cells of pure transit that
    every access pays in full, and a pipe cell is a tick of latency the CPU is
    stopped for. Growing the wall west swallows them: at ``west_grow=4`` the feed is
    the block's own two-cell stub and the drawn pipe is empty.

    Why the caller cannot simply grow its *forwarder* east instead, which is the
    same four cells: the forwarder spans the whole corridor **vertically**, from the
    bank's stub row down to the gate strip, so its east wall would cross the ring's
    relay room at block columns 1..6 (see :data:`TAPED_FEED_TUCK`). The worker room
    is only rows 7..26 of
    the block and the relay starts at row 29, so growing *this* wall west crosses
    nothing. That is the whole trick: the same four cells, taken from the end of the
    corridor that is free.

    Bindings do not move, and they cannot: growing the wall only makes the request
    pipe **further** from every ``r`` in the room, so the four receives that must
    take the request keep it (the tightest, the WRITE value's, goes 10 -> 14 against
    19 to the ring return) and the ring-facing ones are only more strongly bound.
    The two outgoing pipes are on the north and east walls and do not move at all.
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
    ) = _tape_worker_spec(skip_batch, protocol, rotate)

    WX, WY = _TAPE_WX, _TAPE_WY
    best: tuple[int, Circuit, tuple[int, int], tuple[int, int]] | None = None
    for fold in _TAPE_FOLDS:
        g, in_cell, out_cell = _tape_shell(
            n,
            skip_batch=skip_batch,
            park_const=park_const,
            west_grow=west_grow,
            protocol=protocol,
            rotate=rotate,
        )

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
        if not tight_ring:
            return _tape_of(g, in_cell, out_cell, n_fwd + n_ret)
        if best is None or n_fwd + n_ret < best[0]:
            best = (n_fwd + n_ret, g, in_cell, out_cell)
    if best is not None:
        return _tape_of(best[1], best[2], best[3], best[0])
    return _serpentine_tape(
        n,
        skip_batch=skip_batch,
        relay_art=relay_art,
        park_const=park_const,
        west_grow=west_grow,
        protocol=protocol,
        rotate=rotate,
    )


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
    park_const: bool = False,
    west_grow: int = 0,
    protocol: str = "v3",
    rotate: bool = False,
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
    ) = _tape_worker_spec(skip_batch, protocol, rotate)

    WX, WY = _TAPE_WX, _TAPE_WY
    bottom_y = WY + worker_height
    fy = WY + forward_row
    ret_col = WX + return_col_offset
    east = WX + worker_width + 2
    top = bottom_y + _SNAKE_TOP

    # Seventeen rows carry the ~420 slots a little-little-man interpreter wants; the
    # last tier here holds 1976 values, at which point the block is 112 rows tall.
    for rows in range(5, 82, 2):
        g, in_cell, out_cell = _tape_shell(
            n,
            skip_batch=skip_batch,
            park_const=park_const,
            west_grow=west_grow,
            protocol=protocol,
            rotate=rotate,
        )
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
    """Assert every ``r``/``s`` binds the pipe it is meant to use.

    ``s`` targets the nearest *outgoing* pipe and ``r`` the nearest *incoming* one,
    Manhattan, ties by reading order — and *nearest*, not nearest-that-can-proceed
    (``ARCH.md`` §7.1). Getting this wrong is invisible until a program silently
    reads the wrong pipe, so it is checked here and again with
    ``tools/route-check.mjs`` on the real grid.

    **This used to refuse a tie outright — even one the intended pipe wins — and
    that was a floor of our own making.** The old second clause was::

        if rivals[want] != best or sum(1 for d in rivals.values() if d == best) > 1:

    ``SPEC.md:183`` is explicit that "ties break by **reading order** (top to
    bottom, left to right)", and both engines implement exactly that:
    :meth:`fast_littleman._Program._bind_pipe_ops` picks
    ``min(candidates, key=(distance, attach_y, attach_x))``, and the reference
    WASM ``route()`` agrees cell for cell. So a tie is a *decidable* binding, not
    an ambiguous one, and refusing it made the builder strictly stronger than the
    machine it builds for. The rule below is the engines' key, verbatim.

    Note it is the **attachment point** that is compared, not the pipe's far end
    (that distinction already cost a re-derivation once — see
    :data:`ROM_TOUCH_DROP`).

    What it bought, measured rather than argued: ``deadman-3d_hires`` men-v3's
    ``mem_pad`` floor was 3 because ``'r' at (22,163)`` sat at an exact 30-30 tie
    against ``rom``. ``mem_resp``'s attach reads first, so pad 2 is legal, and
    every one of the 54.16% of instructions that carry a MEM band gets a column
    off its lane and off its walk back west: 81,042,708 -> 80,342,861 at 21
    rounds, **-0.864%**, ``passed=True`` and the grid the same 496x674. Three
    other recorded floors were also exactly ties and are worth re-deriving
    against this: :data:`TRIE_SLACK_ROWS` (+1.97%), ``SEEK_CLASSIC_DRAIN``
    excluding BRN (``rom`` and ``mem_resp`` both 47 away) and
    :data:`SLAB_TIGHT_RISERS` (+8.437%).

    A tie is still a **one-cell margin**: any geometry move can flip which attach
    reads first, and the failure mode is a wrong frame rather than an exception.
    That is what the 21-round frame gate is for; a 3-round screen will not see it.
    """
    incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP, "spill_resp"}
    for x, y, glyph, band in glyphs:
        if band == Band.MEM:
            want = "mem_req" if glyph == "s" else "mem_resp"
        elif band == Band.SPILL:
            want = "spill_req" if glyph == "s" else "spill_resp"
        else:
            want = band  # "rom", Band.IN == "in", Band.OUT == "out"
        rivals = {
            name: abs(px - x) + abs(py - y)
            for name, (px, py) in touches.items()
            if (name in incoming) == (glyph == "r")
        }
        if want not in rivals:
            raise MachineError(f"{glyph!r} at {(x, y)} wants pipe {want!r}, which is absent")
        # the engines' key: distance, then the attachment cell in reading order.
        winner = min(rivals, key=lambda n: (rivals[n], touches[n][1], touches[n][0]))
        if winner != want:
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
            "cpu:fetch": "opcode into BP, then the operand into A (fixed-width 2 words)",
            "cpu:trie": f"depth-{self.plan.k} backpack trie; leaves are bit-reversed",
            "cpu:return:collector": "every lane funnels west along here",
            "cpu:return:riser": (
                "up to the fetch row — paid once per instruction; under "
                "FETCH_FOLD it also carries this path's `r`/`b` prologue"
            ),
            "cpu:return:high": (
                "the second collector, one row above the fetch; under FETCH_FOLD it "
                "also carries this path's `r`/`b` prologue"
            ),
            "cpu:drops": (
                "the drop-column band: every lane's descent from its row to the "
                "collector or to its slab. One band, not a box per column — a drop "
                "crosses the lane rows below it, and the crossing is shared, so this "
                "box is deliberately larger than every lane's and claims only what "
                "nothing tighter does"
            ),
            "cpu:seek:taken": "seek: the taken jump/branch drops land here and run east to the `s`",
            "cpu:seek:send": (
                "seek: `s` — one word, row*K+rem, out the east-wall request pipe to the "
                "drum. The drum notices it the tick it enters the pipe, so everything "
                "after this overlaps the seek"
            ),
            "cpu:seek:walk": (
                "seek: the westbound corridor from the send back to the flush loop. "
                "Hidden latency — the drum is still seeking for the whole of it"
            ),
            "cpu:seek:flush": (
                "seek: `r`/`X` — discard the corridor's in-flight words until the drum's "
                "-1 sentinel. This is where the seek's real latency is paid"
            ),
            "cpu:seek:sentinel": "seek: past the -1, read `rem` and park it in BP",
            "cpu:seek:discard": "seek: the stock 2x4 counted discard for the remainder",
            "cpu:seek:riser": "seek: back up to the collector once the target row is at the head",
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
            elif not note and name.startswith("cpu:discard:"):
                note = (
                    f"{name.rsplit(':', 1)[1]}: the 2x4 counted ROM discard — two `r`s a "
                    f"lap, blocking on the ROM corridor"
                )
            elif not note and name.startswith("cpu:riser:"):
                note = f"{name.rsplit(':', 1)[1]}: not-taken / BP==0 exits, rising to the collector"
            elif not note and name.startswith("cpu:entry:"):
                note = f"{name.rsplit(':', 1)[1]}: the westbound slab-entry corridor"
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
    spill: Mapping[str, int] | None = None,
    store_offset: tuple[int, int] = (0, 0),
    in_north: bool = False,
    store_teleport: bool = False,
    store_answer_west: bool = False,
    store_request_teleport: bool = False,
    store_chain_reach: bool = False,
    store_chain_pad: int = 0,
    store_feed_teleport: bool = False,
    store_feed_share_riser: bool = False,
    store_bank_lift: int = 0,
    store_feed_tuck: int = 0,
    store_bank_west_grow: int = 0,
    store_rotate_banks: tuple[int, ...] = (),
    store_request_reach: bool = False,
    store_request_tuck: bool = False,
    adapter_form: str = "wide",
    store_protocol: str = "v3",
    store_request_west: bool = False,
    store_riser_lift: int = 0,
    store_compact_gate: bool = False,
    store_collector_fast: bool = False,
    store_tight_gate: bool = False,
    store_gate_return_slack: int | None = None,
    store_gate_park_const: bool = False,
    store_gate_south_reuse_b: bool = False,
    store_tape_park_const: bool = False,
    store_tape_tight_ring: bool = False,
    store_bank_order: tuple[int, ...] | None = None,
    trim_dead: bool = False,
    top_bus: bool = False,
    store_shape: tuple[int, int] | None = None,
    seek: bool = False,
    seek_threshold: int = SEEK_THRESHOLD,
    seek_ops: Sequence[str] = SEEK_OPS,
    seek_teleport: bool = False,
    seek_attach_low: bool = False,
    seek_taken_drop_east: bool = False,
    seek_twin_station: bool = False,
    in_west: int = 0,
    doom_loop_row: int | None = None,
    doom_leaf_cols: tuple[int, ...] | None = None,
    doom_cluster_lift: int = 0,
    doom_north_up: int = 0,
    doom_north_west: bool = False,
    lane_pitch: int = 2,
    rom_touch_drop: int = 0,
    squash_band: bool | int = False,
    straight_trie: bool = False,
    high_collector: bool = False,
    trie_slack_rows: tuple[int, ...] = (),
    tight_trie_cols: bool = False,
    lean_trie: bool | str = False,
    high_drops_free: bool = False,
    tuck_drops: bool = False,
    fold_lanes: bool = False,
    fetch_fold: bool = False,
    fetch_tuck: bool = False,
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
            words, seek_layout = seek_words(
                program, p, rows=seek_fold + extra, twin_station=seek_twin_station
            )
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
                    tape_jump_threshold=tape_jump_threshold,
                    tape_relay_size=tape_relay_size,
                    in_north=in_north,
                    store_teleport=store_teleport,
                    store_answer_west=store_answer_west,
                    store_request_teleport=store_request_teleport,
                    store_chain_reach=store_chain_reach,
                    store_chain_pad=store_chain_pad,
                    store_feed_teleport=store_feed_teleport,
                    store_feed_share_riser=store_feed_share_riser,
                    store_bank_lift=store_bank_lift,
                    store_feed_tuck=store_feed_tuck,
                    store_bank_west_grow=store_bank_west_grow,
                    store_rotate_banks=store_rotate_banks,
                    store_request_reach=store_request_reach,
                    store_request_tuck=store_request_tuck,
                    adapter_form=adapter_form,
                    store_protocol=store_protocol,
                    store_request_west=store_request_west,
                    store_riser_lift=store_riser_lift,
                    store_compact_gate=store_compact_gate,
                    store_collector_fast=store_collector_fast,
                    store_tight_gate=store_tight_gate,
                    store_gate_return_slack=store_gate_return_slack,
                    store_gate_park_const=store_gate_park_const,
                    store_gate_south_reuse_b=store_gate_south_reuse_b,
                    store_tape_park_const=store_tape_park_const,
                    store_tape_tight_ring=store_tape_tight_ring,
                    store_bank_order=store_bank_order,
                    trim_dead=trim_dead,
                    top_bus=top_bus,
                    store_shape=store_shape,
                    seek_layout=seek_layout,
                    seek_teleport=seek_teleport,
                    seek_attach_low=seek_attach_low,
                    seek_taken_drop_east=seek_taken_drop_east,
                    in_west=in_west,
                    doom_loop_row=doom_loop_row,
                    doom_leaf_cols=doom_leaf_cols,
                    doom_cluster_lift=doom_cluster_lift,
                    doom_north_up=doom_north_up,
                    doom_north_west=doom_north_west,
                    lane_pitch=lane_pitch,
                    rom_touch_drop=rom_touch_drop,
                    squash_band=squash_band,
                    straight_trie=straight_trie,
                    high_collector=high_collector,
                    trie_slack_rows=trie_slack_rows,
                    tight_trie_cols=tight_trie_cols,
                    lean_trie=lean_trie,
                    high_drops_free=high_drops_free,
                    tuck_drops=tuck_drops,
                    fold_lanes=fold_lanes,
                    fetch_fold=fetch_fold,
                    fetch_tuck=fetch_tuck,
                    spill=spill,
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
                    tape_jump_threshold=tape_jump_threshold,
                    tape_relay_size=tape_relay_size,
                    in_north=in_north,
                    store_teleport=store_teleport,
                    store_answer_west=store_answer_west,
                    store_request_teleport=store_request_teleport,
                    store_chain_reach=store_chain_reach,
                    store_chain_pad=store_chain_pad,
                    store_feed_teleport=store_feed_teleport,
                    store_feed_share_riser=store_feed_share_riser,
                    store_bank_lift=store_bank_lift,
                    store_feed_tuck=store_feed_tuck,
                    store_bank_west_grow=store_bank_west_grow,
                    store_rotate_banks=store_rotate_banks,
                    store_request_reach=store_request_reach,
                    store_request_tuck=store_request_tuck,
                    adapter_form=adapter_form,
                    store_protocol=store_protocol,
                    store_request_west=store_request_west,
                    store_riser_lift=store_riser_lift,
                    store_compact_gate=store_compact_gate,
                    store_collector_fast=store_collector_fast,
                    store_tight_gate=store_tight_gate,
                    store_gate_return_slack=store_gate_return_slack,
                    store_gate_park_const=store_gate_park_const,
                    store_gate_south_reuse_b=store_gate_south_reuse_b,
                    store_tape_park_const=store_tape_park_const,
                    store_tape_tight_ring=store_tape_tight_ring,
                    store_bank_order=store_bank_order,
                    trim_dead=trim_dead,
                    top_bus=top_bus,
                    store_shape=store_shape,
                    seek_layout=seek_layout,
                    seek_teleport=seek_teleport,
                    seek_attach_low=seek_attach_low,
                    seek_taken_drop_east=seek_taken_drop_east,
                    in_west=in_west,
                    doom_loop_row=doom_loop_row,
                    doom_leaf_cols=doom_leaf_cols,
                    doom_cluster_lift=doom_cluster_lift,
                    doom_north_up=doom_north_up,
                    doom_north_west=doom_north_west,
                    lane_pitch=lane_pitch,
                    rom_touch_drop=rom_touch_drop,
                    squash_band=squash_band,
                    straight_trie=straight_trie,
                    high_collector=high_collector,
                    trie_slack_rows=trie_slack_rows,
                    tight_trie_cols=tight_trie_cols,
                    lean_trie=lean_trie,
                    high_drops_free=high_drops_free,
                    tuck_drops=tuck_drops,
                    fold_lanes=fold_lanes,
                    fetch_fold=fetch_fold,
                    fetch_tuck=fetch_tuck,
                    spill=spill,
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
    tape_jump_threshold: int = 128,
    tape_relay_size: tuple[int, int] | None = None,
    in_north: bool = False,
    store_teleport: bool = False,
    store_answer_west: bool = False,
    store_request_teleport: bool = False,
    store_chain_reach: bool = False,
    store_chain_pad: int = 0,
    store_feed_teleport: bool = False,
    store_feed_share_riser: bool = False,
    store_bank_lift: int = 0,
    store_feed_tuck: int = 0,
    store_bank_west_grow: int = 0,
    store_rotate_banks: tuple[int, ...] = (),
    store_request_reach: bool = False,
    store_request_tuck: bool = False,
    adapter_form: str = "wide",
    store_protocol: str = "v3",
    store_request_west: bool = False,
    store_riser_lift: int = 0,
    store_compact_gate: bool = False,
    store_collector_fast: bool = False,
    store_tight_gate: bool = False,
    store_gate_return_slack: int | None = None,
    store_gate_park_const: bool = False,
    store_gate_south_reuse_b: bool = False,
    store_tape_park_const: bool = False,
    store_tape_tight_ring: bool = False,
    store_bank_order: tuple[int, ...] | None = None,
    trim_dead: bool = False,
    top_bus: bool = False,
    store_shape: tuple[int, int] | None = None,
    seek_layout=None,
    seek_teleport: bool = False,
    seek_attach_low: bool = False,
    seek_taken_drop_east: bool = False,
    in_west: int = 0,
    doom_loop_row: int | None = None,
    doom_leaf_cols: tuple[int, ...] | None = None,
    doom_cluster_lift: int = 0,
    doom_north_up: int = 0,
    doom_north_west: bool = False,
    lane_pitch: int = 2,
    rom_touch_drop: int = 0,
    squash_band: bool | int = False,
    straight_trie: bool = False,
    high_collector: bool = False,
    trie_slack_rows: tuple[int, ...] = (),
    tight_trie_cols: bool = False,
    lean_trie: bool | str = False,
    high_drops_free: bool = False,
    tuck_drops: bool = False,
    fold_lanes: bool = False,
    fetch_fold: bool = False,
    fetch_tuck: bool = False,
    spill: Mapping[str, int] | None = None,
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
        drain_unit_bits=(
            SEEK_CLASSIC_DRAIN.get((program.name, store), 0)
            if seek
            else DRAIN_UNIT_BITS.get(program.name, 0)
        ),
        drain_ops=SEEK_CLASSIC_DRAIN_OPS.get((program.name, store)) if seek else None,
        tight_drops=(
            (program.name, store) in SEEK_TIGHT_STRUCT_DROPS
            if seek
            else program.name in TIGHT_STRUCT_DROPS
        ),
        slab_pitch=(SEEK_SLAB_PITCH if seek else SLAB_PITCH).get(
            program.name, _SLAB_PITCH
        ),
        packed_band=(program.name, store) in PACKED_SLAB_BAND,
        seek_jump_gap=PACKED_SLAB_BAND.get((program.name, store), 0),
        seek_jump_east=seek and (program.name, store) in SEEK_JUMP_EAST,
        seek_tail_west=(
            SEEK_TAIL_WEST.get((program.name, store), 0)
            if seek and (program.name, store) in SEEK_JUMP_EAST
            else 0
        ),
        seek_tail_wall=seek and (program.name, store) in SEEK_TAIL_WALL,
        risers_west=(program.name, store) in SLAB_RISERS_WEST,
        tight_arms=(program.name, store) in SLAB_TIGHT_ARMS,
        tight_risers=(program.name, store) in SLAB_TIGHT_RISERS,
        tuck_drain=(program.name, store) in SLAB_TUCKED_DRAIN,
        sparse_collector=(program.name, store) in SPARSE_COLLECTOR,
        trim_dead=trim_dead,
        top_bus=top_bus,
        seek=seek,
        seek_taken_drop_east=seek_taken_drop_east,
        lane_pitch=lane_pitch,
        squash_band=squash_band,
        straight_trie=straight_trie,
        high_collector=high_collector,
        trie_slack_rows=trie_slack_rows,
        tight_trie_cols=tight_trie_cols,
        lean_trie=lean_trie,
        high_drops_free=high_drops_free,
        tuck_drops=tuck_drops,
        fold_lanes=fold_lanes,
        fetch_fold=fetch_fold,
        fetch_tuck=fetch_tuck,
        spill_col=spill["col"] if spill else 0,
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
    # ``rom_touch_drop`` moves BOTH the corridor's east leg and the touch point
    # below, together — they are the same row by definition and splitting them
    # would bind the checker against geometry the engine does not have.
    fetch_y = CY + cpu.centre + rom_touch_drop
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
        # The forked adapter is two columns narrower and one row shorter, and both
        # dimensions are load-bearing downstream — the store's request column has
        # to sit inside ``AX+1..AX+adapter_w`` and the request drop hangs off
        # ``AY+adapter_h+1``. So the shape is read once, here, and every use below
        # is of these locals rather than the module constants, which stay put for
        # :mod:`~.ram_machine` and :mod:`~.ram_machine2`.
        # The wire format is a property of the whole chain, so the adapter that
        # emits it and the block that decodes it are one decision, not two: a
        # mismatched pair does not fail to build, it answers from the wrong bank.
        from ..memory_taped import TAPE_PROTOCOLS

        if store_protocol not in TAPE_PROTOCOLS:
            raise MachineError(
                f"unknown store protocol {store_protocol!r}; expected {TAPE_PROTOCOLS!r}"
            )
        if store_protocol in ("v4", "v5"):
            if store != "taped":
                raise MachineError(
                    f"the {store_protocol} wire is the taped tier's, not {store!r}"
                )
            if adapter_form != "v4":
                raise MachineError(
                    f"a {store_protocol} store needs the v4 adapter to feed it, "
                    f"not {adapter_form!r}"
                )
        elif adapter_form == "v4":
            raise MachineError(
                f"the v4 adapter emits the packed wire; store protocol is {store_protocol!r}"
            )
        _arows = adapter_rows(address_first=store == "men-y", form=adapter_form)
        adapter_w, adapter_h = len(_arows[0]), len(_arows)
        # A narrower adapter hands its columns back to the **corridor**, not to the
        # store: ``TX`` is ``AX + width + gap + dx`` and every constraint recorded
        # against it — :data:`STORE_ANSWER_WEST`'s ``tx <= CX+W+2``, the request
        # roof's window, :data:`TIER_LAYOUT`'s pinned ``store_offset`` — is a
        # statement about ``TX`` itself, with ``dx`` merely the spelling. Widening
        # the gap by exactly what the fold saved holds ``TX`` fixed, so nothing
        # east of the adapter's east wall moves by a single cell and the tick
        # measurement is of the adapter and nothing else.
        adapter_gap = adapter_tape_gap(program.name, store) + (ADAPTER_W - adapter_w)
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
        g.room(AX, AY, AX + adapter_w + 1, AY + adapter_h + 1)
        g.blit(
            AX, AY, adapter_cells(address_first=store == "men-y", form=adapter_form)
        )
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
        # Only the taped tier has a collector to widen, so only it can set this.
        answer_exit_west = False
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
                tape = v3_store_grid_block(
                    v3_cols,
                    v3_rows,
                    ops=v3_ops,
                    request_west=store_request_west,
                    riser_lift=store_riser_lift,
                )
            else:
                if store_request_west:
                    # The one-column block is a different function with a different
                    # floor plan, and its router is entered on its west wall
                    # already. Say so rather than ignoring the flag and then drawing
                    # a leg to a coordinate the roof stub named.
                    raise MachineError(
                        "store_request_west needs the multi-column men-v3 grid; the "
                        "one-column block already enters its router on a west wall"
                    )
                tape = v3_store_block(tape_n, ops=v3_ops)
        elif store == "taped":
            from ..memory_taped import COLLECTOR_ROW, taped_store_block

            # The block's own placement does not depend on the block, so its
            # origin is known before it is built — which is what lets the answer
            # collector be widened to a column named in *machine* coordinates.
            tx_pre = AX + adapter_w + adapter_gap + store_dx
            # ... and, for the same reason, which of the collector's own rows the
            # CPU's response row lands on. A caller *below* the widened collector
            # takes the answer out of its south wall and walks up; a caller level
            # with the collector's first interior row cannot — its riser would
            # climb back around the outside of the room to reach a cell one column
            # from where it started, which is the geometry that kept this machine
            # on two forwarding rooms. Level with it, the answer leaves **west**
            # instead, straight onto the response attachment cell.
            answer_exit_west = (
                store_answer_west
                and (CY + mem_dy + store_dy) + COLLECTOR_ROW + 1 == resp_row_check
            )
            tape = taped_store_block(
                tape_n,
                TAPED_BANKS.get(program.name, 4),
                skip_batch=(
                    tape_skip_batch
                    if tape_skip_batch != 1
                    else TAPED_SKIP_BATCH.get(program.name, 1)
                ),
                # ... and, when that is ``None``, the size at which each bank
                # picks batch 2 for itself (:data:`TAPED_JUMP_THRESHOLD`).
                jump_threshold=TAPED_JUMP_THRESHOLD.get(
                    program.name, tape_jump_threshold
                ),
                # Land the collector's west wall on ``CX + W + 3``: the column
                # the deleted teleport U used to occupy, one clear of the
                # response pipe's own attachment cell. A west exit stub owns that
                # clear column instead, so the wall stands one further east — and
                # what is left is teleport U's own two-cell hand-off, cell for
                # cell, which is why nothing rebinds. (A pipe is two cells or it
                # is not a pipe: ``fast_littleman._parse_pipes``.)
                answer_west=(
                    None
                    if not store_answer_west
                    else (CX + W + 5 - tx_pre) if answer_exit_west
                    else (CX + W + 4 - tx_pre)
                ),
                answer_exit_west=answer_exit_west,
                compact_gate=store_compact_gate,
                collector_fast=store_collector_fast,
                tight_gate=store_tight_gate,
                gate_return_slack=store_gate_return_slack,
                gate_park_const=store_gate_park_const,
                gate_south_reuse_b=store_gate_south_reuse_b,
                tape_park_const=store_tape_park_const,
                tape_tight_ring=store_tape_tight_ring,
                order=store_bank_order,
                chain_reach=store_chain_reach,
                chain_pad=store_chain_pad,
                feed_teleport=store_feed_teleport,
                feed_share_riser=store_feed_share_riser,
                bank_lift=store_bank_lift,
                feed_tuck=store_feed_tuck,
                bank_west_grow=store_bank_west_grow,
                rotate_banks=store_rotate_banks,
                # Land the first gate's roof one row under the adapter's floor,
                # so its west wall stands beside the adapter and the request is
                # a drop, not a corridor. Same trick as ``answer_west``: the
                # block's origin is known before the block is.
                request_roof=(
                    (AY + adapter_h + 2) - (CY + mem_dy + store_dy)
                    if store_request_reach
                    else None
                ),
                request_tuck=store_request_tuck,
                protocol=store_protocol,
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
        TX = AX + adapter_w + adapter_gap + store_dx
        TY = CY + mem_dy + store_dy
        if TY < 0 or TX < 0:
            raise MachineError(f"STORE placement leaves the grid: ({TX}, {TY})")
        g.blit(TX, TY, tape.cells)

        # adapter east wall -> the tape's request stub
        tin_x, tin_y = TX + tape.in_cell[0], TY + tape.in_cell[1]
        ax_out = AX + adapter_w + 2
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
        if store_request_tuck and not store_request_reach:
            raise MachineError(
                "store_request_tuck moves the grown gate's entry one row north; "
                "there is no grown gate without store_request_reach"
            )
        if (store_request_reach or store_chain_reach) and store != "taped":
            raise MachineError(
                f"only the taped tier has gate rooms to grow, not {store!r}"
            )
        if store_riser_lift and store != "men-v3":
            raise MachineError(
                f"only the men-v3 grid block has an answer riser to lift, not {store!r}"
            )
        if store_request_west and store != "men-v3":
            raise MachineError(
                f"only the men-v3 tier's router strip is entered on a wall, not {store!r}"
            )
        if store_request_west and (store_request_reach or store_request_teleport):
            raise MachineError(
                "store_request_west already *is* the short request: a straight leg "
                "onto the strip's corner. Do not also grow a gate or add a forwarder."
            )
        if store_request_west:
            # The mirror of ``answer_exit_west`` on the leg the request walks, and
            # it is the same discovery: a block level with its caller needs no
            # route. The men-v3 store used to be entered over its **roof** — the
            # request left the adapter east, climbed eight rows up the free column
            # west of the block, ran east along the corridor above it and dropped
            # into the router strip's north wall — because the block's only named
            # touch point was that roof stub. It was not level-ness that was
            # missing; ``store_offset``'s dy already puts the strip's **south wall
            # on the adapter's own output row**. What was missing was a touch point
            # on that wall.
            #
            # So the leg is the cells between the adapter's east wall and the
            # block's south-west corner — four of them on hires — and the corner is
            # where it attaches:
            # ``ARCH.md`` §7.4b, a plain room may be attached at a corner (only
            # displays forbid it), verified by :func:`_check_pipe_count` — the same
            # counter that catches a leg running *alongside* a corner and being
            # read as a second pipe. Here the leg terminates on the corner with an
            # arrowhead pointing into it, so there is nothing alongside.
            #
            # The strip owns exactly one incoming pipe (``memory_men_grid``'s
            # ``build_grid``), so moving where that pipe attaches rebinds nothing:
            # "nearest" only picks *which* pipe, and there is still only one.
            if store_in[1] != adapter_out[1]:
                raise MachineError(
                    f"the store's request wall is on row {store_in[1]} and the "
                    f"adapter's request leaves on row {adapter_out[1]}: a straight "
                    "leg needs them level (TIER_LAYOUT's store_offset dy)"
                )
            if store_in[0] <= adapter_out[0]:
                raise MachineError(
                    f"the store's request corner {tin_x} leaves no leg east of the "
                    f"adapter's outlet {adapter_out[0]}: a pipe is two cells or it is "
                    "not a pipe, so the corner has to stand at least two columns clear"
                )
            route_lengths["adapter->store"] = g.draw_pipe([adapter_out, store_in])
        elif store_request_reach:
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
            floor_y = AY + adapter_h + 1
            if not AX + 1 <= tin_x <= AX + adapter_w:
                raise MachineError(
                    f"the store's request column {tin_x} is not under the adapter's "
                    f"floor ({AX + 1}..{AX + adapter_w}); the drop has nowhere to start"
                )
            if tin_y - 1 < floor_y + 1:
                raise MachineError(
                    f"the store's request row {tin_y} leaves no drop below the "
                    f"adapter's floor at {floor_y}"
                )
            # ... down to one cell short of the block's own ``>``, which is the
            # cell that turns the drop into the gate's west wall.
            #
            # With ``store_request_tuck`` the block's ``>`` is on the gate roof's
            # *first* interior row rather than its second, so this drop is the one
            # cell the wall itself demands and ``draw_pipe`` has no leg to walk: a
            # polyline whose ends coincide has no direction to take an arrowhead
            # from. Draw the source cell directly. The result is still a pipe —
            # two cells, ``v`` under the adapter's floor and the block's ``>``
            # turning into the gate's west wall — which is the minimum the
            # language allows (SPEC.md §Pipes).
            if tin_y - 1 == floor_y + 1:
                g.put(tin_x, floor_y + 1, "v")
                g.drawn.add((tin_x, floor_y + 1))
                route_lengths["adapter->store"] = 1
            else:
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

            floor_y = AY + adapter_h + 1  # the adapter's south wall
            rx0 = store_in[0] - 1  # west wall, one clear of the exit column
            rx1 = rx0 + _TELE_W + 1
            ry0, ry1 = floor_y + 3, tin_y - 4
            # The roof column has to be under the adapter's floor *and* inside
            # the room's north wall; the west end of the room is west of the
            # adapter, so the shared column is the east one.
            drop = max(rx0 + 1, AX + 1)
            if drop > min(rx1 - 1, AX + adapter_w):
                raise MachineError(
                    "the store request teleport's roof does not reach the adapter's "
                    f"floor: columns {rx0 + 1}..{rx1 - 1} miss {AX + 1}..{AX + adapter_w}"
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
            if answer_exit_west:
                # Level with the collector's first interior row there is nothing
                # to climb and nothing to route: the block's own west exit stands
                # on ``CX + W + 3`` and the response is the two cells from there
                # into the CPU's east wall — teleport U's hand-off exactly, minus
                # the room. The block already drew the first of the two, so this
                # is idempotent on it.
                if (tout_x, tout_y) != (CX + W + 3, resp_row):
                    raise MachineError(
                        f"the collector's west exit is at {(tout_x, tout_y)}, not "
                        f"beside the response attachment cell "
                        f"{(CX + W + 2, resp_row)}"
                    )
                route_lengths["store->cpu"] = g.draw_pipe(
                    [(tout_x, tout_y), (CX + W + 2, resp_row)]
                )
            else:
                route_lengths["store->cpu"] = g.draw_pipe(
                    [(tout_x, tout_y + 1), (tout_x, resp_row), (CX + W + 2, resp_row)]
                )
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
            doom_cluster_lift=doom_cluster_lift,
            doom_north_up=doom_north_up,
            doom_north_west=doom_north_west,
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
            route_lengths["cpu->drum"], seek_regions, cmd_attach_y = _seek_teleport(
                g,
                cmd_y=cmd_y,
                src_x=CX + W + 2,
                x_e=x_e,
                rom_east=rom_east,
                ry=ry,
                y_b=y_b,
                cpu_bottom=(CY + H if seek_attach_low else None),
            )
        else:
            cmd_attach_y = cmd_y
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

    # ── the SPILL block, in the pocket east of the CPU ───────────────────────
    # Two cells of interior width is all a one-word LIFO needs, because the man
    # *is* the storage: ``R`` takes the pushed word and ``s`` parks it in the
    # outgoing pipe, where it waits until the CPU's ``POP`` reads it. The pipe is
    # the latch and the rendezvous both — ``r`` blocks until a word is ready and
    # ``s`` blocks until there is room — so the discipline is enforced by the
    # hardware rather than trusted from the program.
    #
    # ``R`` rather than ``r``: the room has exactly one incoming pipe, so "any
    # ready pipe" and "the nearest pipe" are the same thing, and ``R`` says the
    # binding does not depend on where the pipe was attached.
    #
    # Drawn last, so every cell it takes is a cell nothing else wanted: a
    # collision here is :meth:`_Grid.put` raising, not a silent overlap.
    spill_touches: dict[str, tuple[int, int]] = {}
    spill_regions: dict[str, tuple[int, int, int, int]] = {}
    if spill is not None:
        sx0 = CX + W + 2  # the touch column: one east of the CPU's east wall
        sy0, sh = spill["room_y"], spill["room_h"]
        sy1 = sy0 + sh - 1
        req_y, resp_y = spill["req_row"], spill["resp_row"]
        if sh < 6:
            raise MachineError("the SPILL loop needs 4 interior rows (room_h >= 6)")
        if not (min(req_y, resp_y) + 2 <= sy0 <= sy1 <= max(req_y, resp_y) - 2):
            raise MachineError(
                f"the SPILL block {sy0}..{sy1} must stand between its two attach "
                f"rows (req {req_y}, resp {resp_y}) and clear of both by two: the "
                "pipes climb opposite sides of it, and a pipe is two cells or it "
                "is not a pipe"
            )
        # Which pipe comes down from above is a §7.1 outcome, not a choice: the
        # measured window puts the request row north of the response row on this
        # machine and there is no reason the next one has to agree, so the block
        # is drawn either way up. ``R``'s "any incoming pipe" is what makes that
        # free — the receiving side does not care which wall its word arrives at.
        req_above = req_y < resp_y
        # Two interior columns: the east one rises and *receives*, the west one
        # descends and *sends*. Which way round matters, because a man spawns
        # facing east (``SPEC.md``) and ``@`` is a nop once he is walking: putting
        # the spawn on the descending column at a row whose eastern neighbour is
        # a ``^`` sends him up the receiving side first, so the very first thing
        # this block ever does is take a word — never park an empty ``A`` in the
        # answer pipe, which the first ``POP`` would then read as a zero.
        L, R = sx0 + 1, sx0 + 2
        with g.part("spill"):
            g.room(sx0, sy0, sx0 + 3, sy1)
            g.put(L, sy0 + 1, "v")
            g.put(L, sy0 + 2, "s")
            g.put(R, sy0 + 1, "<")
            g.put(R, sy0 + 2, "R")
            for y in range(sy0 + 3, sy1 - 1):
                g.put(L, y, "@" if y == sy0 + 3 else ".")
                g.put(R, y, "^")
            g.put(L, sy1 - 1, ">")
            g.put(R, sy1 - 1, "^")
            route_lengths["cpu->spill"] = g.draw_pipe(
                [(sx0, req_y), (R, req_y), (R, sy0 - 1 if req_above else sy1 + 1)]
            )
            route_lengths["spill->cpu"] = g.draw_pipe(
                [(L, sy1 + 1 if req_above else sy0 - 1), (L, resp_y), (sx0, resp_y)]
            )
        spill_touches = {"spill_req": (sx0, req_y), "spill_resp": (sx0, resp_y)}
        spill_regions["spill"] = (sx0, sy0, 4, sh)

    rows = g.rows()

    # ── name every block in grid coordinates ─────────────────────────────────
    regions: dict[str, tuple[int, int, int, int]] = {
        f"cpu:{n}": (CX + x, CY + y, w, h) for n, (x, y, w, h) in cpu.regions.items()
    }
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)
    if hot is None:
        regions["adapter"] = (AX, AY, adapter_w + 2, adapter_h + 2)
        regions["tape"] = (TX, TY, tape.width, tape.height)
        regions.update(tele_regions)
        regions.update(req_tele_regions)
    else:
        regions.update(extra_regions)
    regions.update(seek_regions)
    regions.update(spill_regions)
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
        "rom": (CX - 1, CY + cpu.centre + rom_touch_drop),
        "mem_req": (CX + W + 2, req_row),
        "mem_resp": (CX + W + 2, resp_row),
        **dsp_touches,
        **stream_touches,
        **spill_touches,
    }
    if cpu.has_in:
        touches["in"] = (in_x, CY - 1) if in_north else (CX - 1, iy)
    if cpu.has_out:
        touches["out"] = (CX + cpu.out_col, CY + H + 2)
    if seek:
        # ... at the row the pipe was actually drawn from, which is not always the
        # send row: see ``_seek_teleport``'s ``attach_y`` and :data:`SEEK_ATTACH_LOW`.
        touches["cmd"] = (CX + W + 2, cmd_attach_y)
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


#: **An instrument, not a knob.** Stops :func:`_seek_teleport`'s wall-raising loop
#: this many rows early, so the CPU's dive into room H is exactly that many cells
#: longer and **nothing else in the grid moves** — H is a teleport and is crossed
#: in one instruction whatever its height, so its own body does not care. It is
#: how the ``cpu->drum`` leg's tick derivative gets measured on a real machine
#: rather than argued from the pipe length. 0 in every build.
SEEK_DIVE_PAD = 0


def _seek_teleport(
    g: _Grid,
    *,
    cmd_y: int,
    src_x: int,
    x_e: int,
    rom_east: int,
    ry: int,
    y_b: int,
    cpu_bottom: int | None = None,
) -> tuple[int, dict[str, tuple[int, int, int, int]], int]:
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
    while hy0 - 1 > cmd_y + 2 + SEEK_DIVE_PAD and clear(hx0, hy0 - 1, hx1, hy0 - 1):
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

    # ── where the pipe leaves the CPU ────────────────────────────────────────
    # The send cell and the attachment cell are two different things. ``s`` takes
    # the nearest outgoing pipe wherever the man happens to stand, so the pipe may
    # leave the east wall on **any** interior row — and the row it should leave on
    # is H's own, because everything between is a dive the word makes at one cell a
    # tick with the CPU stopped at the far end of it.
    #
    # ``cpu_bottom`` is the CPU's last interior row; below that there is no wall to
    # attach to. The stub east of the wall has to be clear on the chosen row, which
    # is why this is a test and not an assumption: on ``deadman-3d_hires`` the
    # column immediately east of the CPU is shared with the seek adapter's own room
    # for eight rows, and only the last two are free.
    # ``hy0 - 2``, not ``hy0 - 1``: the last cell has to be an arrowhead pointing
    # **south** into H's north wall, so the dive needs one real cell of its own —
    # a pipe that arrives on H's row could only point east, into whatever stands
    # beside it. Two cells is the floor a pipe has anyway (SPEC.md).
    attach_y = cmd_y
    if cpu_bottom is not None:
        cand = min(hy0 - 2, cpu_bottom)
        if cand > cmd_y and clear(src_x, cand, hx0 + 1, cand):
            attach_y = cand
    # ... the CPU's own attachment cell, east two and down into H's north wall ...
    n1 = g.draw_pipe([(src_x, attach_y), (hx0 + 1, attach_y), (hx0 + 1, hy0 - 1)])
    # ... H hands it up the through-column into V's south wall ...
    n2 = g.draw_pipe([(thru, hy0 - 1), (thru, vy1 + 1)])
    # ... and V hands it west onto the drum's own attachment cell.
    n3 = g.draw_pipe([(vx0 - 1, ry), (rom_east + 1, ry)])
    return (
        n1 + n2 + n3,
        {
            "seek:H": (hx0, hy0, hx1 - hx0 + 1, hy1 - hy0 + 1),
            "seek:V": (vx0, vy0, vx1 - vx0 + 1, vy1 - vy0 + 1),
        },
        attach_y,
    )

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
    doom_cluster_lift: int = 0,
    doom_north_up: int = 0,
    doom_north_west: bool = False,
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
        blk = d3_router.build_packed_wall(
            loop_row=doom_loop_row, leaf_cols=doom_leaf_cols,
            lift=doom_cluster_lift, north_up=doom_north_up,
            north_west=doom_north_west,
        )
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
#: **``deadman-3d_hires`` is absent by measurement, and the reason is a coupling
#: rather than a number.** Its :data:`OPCODE_SLOTS` map is a *rank-preserving*
#: relabelling of ``plan``'s default order, and ``build`` enforces that — every
#: candidate order below is rejected outright with "opcode slot map must preserve
#: the lanes' north-to-south rank order". The two registries are one decision
#: here, exactly as :data:`TAPED_BANKS` and :data:`TAPED_BANK_ORDER` are.
#: (Not to be confused with :data:`TAPED_TIGHT_RING`, which is about the ring's
#: *geometry* at a fixed size and measured **exactly zero**.)
#:
#: Dropping the slot map to price the order alone, against hires' own execution
#: profile (per gameplay frame: ``LD`` 7,632 = 20.65%, ``ST`` 6,866 = 18.58%,
#: ``MULI`` 3,838, ``LDI`` 3,820 … and ``NEG``/``INCM``/``IN`` never executing at
#: all), on the 3-round tour against the shipped 22,902,559:
#:
#: | order | result |
#: |---|---|
#: | default (none) | 22,902,559 |
#: | cold-first (ascending frequency) | **fails to bind** — `'r' at (39, 168)` |
#: | cold-first + ``JMPS`` | **fails to bind** — `'r' at (39, 169)` |
#: | hot memory pulled south of the structured lanes | **fails to bind** — `'s' at (38, 174)` |
#: | ``deadman-3d``'s own order | 22,922,622, **+0.088%** |
#:
#: Three of four do not survive §7.1: moving a lane moves the CPU's east-wall
#: ports, and the memory-response pipe stops being the nearest rival. The one
#: that does build is a small loss. So the space is binding-constrained rather
#: than tick-constrained, and there is no free order to take.
#:
#: This is **not** a proof that no better order exists — a real evaluation has to
#: re-run the ``OPCODE_SLOTS`` DP per candidate order, which is the same joint
#: search M21 left open. It is a proof that the order cannot be moved on its own.
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
    #
    # **This number no longer describes the shipped hires machine**, which now
    # seeks: `SEEK_TIER_LAYOUT[("deadman-3d_hires", "taped")]` overrides it with
    # 119, and 88 does not even build under the drum (a seek row addresses its
    # words as `row*128 + offset` and at 88 rows the first row holds 152). It is
    # kept because it is still the classic build's crossing, which is what every
    # counterfactual in the tests rebuilds and what this whole comment derives.
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
    #
    # **Both components are now pinned by the answer collapse, not chosen.**
    # ``dx`` is the west end of the roof window because :data:`STORE_ANSWER_WEST`
    # needs the collector's interior to start at block column 2 or more (the west
    # exit stub owns the column outside the wall), which wants ``tx <= CX+W+2``,
    # which wants ``dx <= -20``. The roof wants ``dx >= -20``. One value satisfies
    # both, and the "five columns of margin either side" above is spent.
    # ``dy = -1`` lifts the block one row so ``resp_row`` lands on the collector's
    # **first interior row** instead of its north wall — the difference between
    # an exit that can attach beside the CPU and one that cannot attach at all.
    ("deadman-3d_hires", "taped"): {"store_offset": (-20, 9)},
    # The men tier's own offset. Here dy is a *tick* knob, not a packing one: it
    # shortens the request leg, the only length-sensitive term left once the answer
    # path is distance-free (the collector's ``R`` and the ``teleport_v`` riser both
    # have no distance term). ~-0.16%/row: dy 5 is -1.03%, dy 10 is -1.62%. dy 12,
    # dy 20 and dx -10 all fail on ``collision at (93, 146): '+' vs '-'`` — a pipe
    # collision, not a wall, so the remaining ~1.6% is recoverable by re-routing
    # rather than blocked by geometry.
    # dx -2 is the hard floor: at -3 "the store's request corner leaves no leg
    # east of the adapter's outlet". Worth -0.634% alone, and additive with the
    # narrower :data:`STORE_SHAPE` (-0.543% alone, -1.150% together).
    #
    # dy is **not** free to sweep: it is one half of the level equation the
    # binding gate actually enforces, `adapter_out_row = 164 - squash_band`
    # against `store_in_row = 147 + store_dy`. Every squash k in 0..15 binds if
    # dy follows as `17 - k`; hold dy fixed and the adapter walks off the store
    # wall. See `scratch/deadman3d-opt/menprobe/hz_geom.py`.
    ("deadman-3d_hires", "men-v3"): {"store_offset": (-2, 10)},
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

#: ``(slug, tier)`` pairs whose CPU lane band is laid at **pitch 1** — one row per
#: lane, no gap row between them — instead of the historic pair.
#:
#: The gap row was never the lanes' to spend. Every trie cell lives in columns
#: ``5 .. lane_x0 - 1`` and every micro-program starts at ``lane_x0``, so the two
#: never contend for a cell; what needed the row was the ``x`` **node**, which
#: always turns and so has to sit somewhere its own subtree's men do not walk.
#: :func:`_uneven_trie` puts a node one row above its down-half's first lane, and
#: at pitch 1 that row is the up-half's **last lane row** — which is safe for a
#: reason that holds structurally rather than by luck:
#:
#:     a node at level ``L`` sits in column ``3 + 2L``, and *every* lane in its
#:     subtree is entered from a node at level ``> L``, hence at column
#:     ``>= 5 + 2L``. A node's column, and its two legs' column, are therefore
#:     strictly **west of every lane entry below it** — so no lane man ever walks
#:     onto them and no leg ever lands inside a lane's shift run.
#:
#: The node rows are distinct for free, too: a node's row is fixed by its split
#: slot, there are 21 split points between 22 lanes, and single-child chains are
#: contracted away — so 21 nodes land on 21 different lane rows with nothing left
#: over.
#:
#: What it buys is the whole of the band's height, three times over, because the
#: trie descent, the drop to the collector and the riser back up are all vertical
#: travel inside it. Opt-in per ``(slug, tier)``; absent pairs keep pitch 2 and
#: stay byte-identical. Requires :data:`TRIM_DEAD_LANES` (see :func:`build_cpu`).
#: How many rows **south of the fetch row** the ROM corridor turns east and
#: attaches, per ``(slug, tier)``. Absent (0) keeps the historical behaviour: the
#: corridor attaches *on* the fetch row, which is where ``touches["rom"]`` had
#: been hard-coded since the corridor existed.
#:
#: It was hard-coded rather than chosen, and that is the whole reason this exists.
#: Nothing holds the attachment to the fetch row — the descent is in column 1 and
#: the columns between it and the CPU's west wall are blank for the entire height
#: of the CPU below that row. The fetch ``r`` does not need the pipe adjacent
#: either; §7.1 binds by distance, and it sits three cells from the touch with
#: ~57 cells of slack against its nearest rival.
#:
#: **What it buys: :data:`LANE_PITCH` on ``deadman-3d_hires``.** The staggered
#: band is worth -4.351% there and could not be taken, because the BRN slab's
#: discard ``r`` at (43,188) came out 58 from the ROM touch against 54 from the
#: memory response and §7.1 gave it the wrong pipe. The stagger moves the ROM
#: touch 5 rows south by itself (161 -> 166) but moves ``mem_resp`` 10 (142 ->
#: 152), and since both sit *above* the slab, south means nearer — a net 5-cell
#: swing that turns a 1-cell win into a 4-cell loss:
#:
#: | | rom touch | mem_resp touch | the slab's `r` sees |
#: |---|---|---|---|
#: | pitch 2 | 161 | 142 | rom 63 < mem_resp 64 — by **one cell** |
#: | pitch 1 | 166 | 152 | rom 58 > mem_resp 54 |
#:
#: So pitch 2 was never robust; it was surviving on one cell and the stagger
#: spent five. Five more rows of drop hands them back.
#:
#: ``scratch/deadman3d-opt/rom_touch_probe.py`` swept the drop against the real
#: :func:`check_bindings` over all **twelve** ``r`` glyphs that want rom (the
#: fetch plus every slab discard):
#:
#: | drop | outcome |
#: |---|---|
#: | 0..3 | fails, rom 58..55 against mem_resp 54 |
#: | 4 | fails — rom **ties** at 54, and :func:`check_bindings` fails ties too |
#: | **5..14** | every one of the twelve binds |
#:
#: Ten rows of freedom, not a knife-edge. The direction is not a coin flip: the
#: fetch has ~57 cells of slack and the deepest slab has one, so moving the touch
#: south spends slack where it is abundant to buy it where it is scarce.
#:
#: Two things this deliberately does **not** do. It does not move the memory
#: response (that is ``mem_pad``, and every column of it is paid twice by every
#: memory instruction — the 28-pad fallback costs +6.274% to return the stagger's
#: 5.99%, which is why the lever was withdrawn rather than paid for). And it does
#: not move the memory *block*: :data:`MEM_PLACE` is structurally inert here,
#: because both touch cells are on the CPU's own walls, so translating the block
#: moves the pipe's far end and not its attachment — as :data:`ROM_BUFFER`'s
#: docstring already said, ``check_bindings`` measures the attachment point and
#: not the route.
#: **Measured, and it pays more than the binding fix it was built for.** Sweeping
#: the drop on the 21-round tour (ticks to frame 20, against the shipped
#: 204,117,437 at pitch 2):
#:
#: | drop | pitch | pad | box | ticks | Δ |
#: |---|---|---|---|---|---|
#: | 0 | 2 | 15 | 649x495 | 204,117,437 | — (was shipped) |
#: | 5 | 1 | 15 | 649x495 | 189,595,223 | -7.115% |
#: | 14 | 1 | 15 | 649x495 | 189,363,519 | -7.228% |
#: | 18 | 1 | 15 | 649x495 | 189,197,308 | -7.310% |
#: | **22** | **1** | **15** | **649x495** | **189,164,256** | **-7.326%** |
#: | 26 | 1 | 15 | 649x495 | 189,163,513 | -7.326% (flat) |
#: | 32 | 1 | 15 | — | — | fails to bind |
#: | 0 | 1 | 28 | 658x495 | 204,806,792 | +0.338% (the rejected fallback) |
#:
#: The box does not move: 649x495 at every drop that builds. The corridor's
#: descent is in column 1 and the rows it extends through were already blank.
#:
#: -7.326% against the -4.351% the stagger was worth on its own, and the excess is
#: not the binding — that is fixed at drop 5, which is only -7.115%. The extra
#: 0.2pp from 5 to 22 is **corridor capacity**: a pipe is a FIFO whose capacity is
#: its length (``SPEC.md``), so a longer descent holds more ROM words in flight and
#: the CPU waits less. Note this runs *against* :data:`ROM_BUFFER`'s finding, where
#: deliberately lengthening the corridor under a seek drum was a large loss because
#: ``seekrom`` flushes it to the ``-1`` sentinel on every seek. Twenty-two rows is
#: apparently below the length where that flush outweighs the buffering; the curve
#: is flat by 26 and unbuildable by 32, so there is no room to push it and find out.
#:
#: 22 rather than 26 because they are identical to five figures and 22 leaves four
#: more rows of binding margin before the wall.
#: ``(slug, tier)`` pairs whose staggered lane band is **top-aligned**, so the
#: rows :data:`LANE_PITCH` frees come out of the room's height instead of being
#: left blank above the band.
#:
#: Empty by default because the two are not the same optimisation and the second
#: one is not free. Bottom-aligning (the default) keeps the collector, the
#: structures band and the room's height exactly where they were, so **nothing
#: outside the CPU moves** — every tick the stagger wins is internal vertical
#: travel, and all three terms measure from the collector. Top-aligning moves the
#: collector and everything below it up, which shrinks the room and pulls every
#: block placed against it north: adapter, tape, teleports, the seek ladders and
#: the store. That shortens the pipes between them, and it moves every touch point
#: — so it is a §7.1 problem, not a height one.
#:
#: On ``deadman-3d`` it was measured at a further **-0.93%** and declined, because
#: the shrunk room needs the STORE to follow north (``store_offset`` dy -5) and
#: that offset re-breaks whichever counterfactual build has a differently-shaped
#: block. hires is a different case on both counts: its store is eleven banks at
#: batch 2 rather than four at batch 1, and :data:`ROM_TOUCH_DROP` now exists to
#: absorb the binding shift that the height change causes.
#:
#: **Measured on hires and declined — it works, and it is not worth what it costs.**
#: The squash builds (649x485, ten rows off the box) and is worth **-0.243%**, close
#: to ``deadman-3d``'s -0.93% once the metric is the same. But it cannot coexist
#: with :data:`SEEK_TELEPORT`, and that is worth six times more:
#:
#: | variant | box | ticks | Δ |
#: |---|---|---|---|
#: | shipped | 649x495 | 189,164,256 | — |
#: | no ``SEEK_TELEPORT`` | 649x495 | 192,066,009 | +1.534% |
#: | **squashed**, no ``SEEK_TELEPORT`` | 649x485 | 191,600,156 | **+1.288%** |
#:
#: The conflict is **placement, not binding**, and not what the ``deadman-3d`` note
#: predicted. ``_seek_teleport``'s room H is *wide* — ``x 62..648``, 587 columns —
#: and needs a full-width clear strip between the store's bottom and whatever hangs
#: below the CPU. Squashing pulls the CPU twelve rows north, the store follows, and
#: the strip stops being clear: ``seek teleport: no clear band below the store for
#: room H``. No ``ROM_TOUCH_DROP`` helps, because nothing here is a distance.
#:
#: So the open question is not the squash, it is **whether room H can park
#: elsewhere**. If it can, -0.243% is free. That is a placement search under a
#: hard full-width constraint — layout-manager work, not a sweep.
#:
#: **Reclaiming the rows from the CPU's *top* instead of its bottom does not help
#: either, and cannot.** The obvious repair for the room-H collision is to leave
#: everything below the band where it is and take the twelve rows off the room's
#: top wall — hand them back to ``cpu_gap`` so ``CY + H`` never moves. Probed, and
#: it fails two ways at once:
#:
#: 1. **It still breaks room H.** The store is anchored to ``CY``, not to
#:    ``CY + H``, so raising ``cpu_gap`` pushes ``CY`` — and the adapter and store
#:    with it — twelve rows *down*, squeezing H's band from the other side. Both
#:    variants collide with the same room for mirror-image reasons.
#: 2. **There is nothing to win even if it built.** With ``CY + H`` fixed and
#:    ``rom_bottom`` fixed, *no pipe changes length* — the twelve blank rows simply
#:    move from inside the room to the gap above it, and the machine's height is
#:    unchanged. A win would need the ROM block to descend into the freed space,
#:    which shortens the ROM corridor — and :data:`ROM_TOUCH_DROP` measured that
#:    corridor as wanting to be **longer**, worth 0.2pp from drop 5 to 22.
#:
#: So the twelve rows are only worth anything if they come off the *bottom*, which
#: is the variant above, which needs room H rehoused. The probe was reverted rather
#: than left as a dead path.
#:
#: Beware the tour length here: at 3 rounds the squash reads -0.854%, three and a
#: half times its 21-round value, because a short tour is boot-heavy. Confirm this
#: one at 21.
#:
#: ────────────────────────────────────────────────────────────────────────────
#: **Re-measured, and most of the above is wrong.** Three of the four conclusions
#: recorded here do not survive being reproduced. ``squash_band`` is now a **row
#: count** rather than a flag (see :func:`build_cpu`), which is what made the
#: difference: the squash was only ever all-or-nothing because the parameter was.
#:
#: **1. It does coexist with :data:`SEEK_TELEPORT`.** Room H needs four rows
#: between the store's underside and the STREAM unit's top, and a *full* squash
#: leaves it two. Taking k of the ten rows builds for **k<=8** with the teleport
#: on. Room H never needed rehousing — it is bottom-anchored and grows into
#: whatever band it is given, and its height comes out exactly ``12 - k``:
#:
#: | k | box | max drop that binds |
#: |---|---|---|
#: | 0 | 649x495 | 28 |
#: | 3 | 649x492 | 26 |
#: | 7 | 649x488 | 22 |
#: | 8 | 649x487 | 18 |
#: | 9 | — | none: room H is down to 3 rows |
#:
#: What blocks ``k>8`` is that the **store cannot follow the CPU north**, and
#: ``store_offset`` dy is not the lever: on hires dy -1..-20 every one fails to
#: route (``collision at (64, 128)``), and dy -2 fails identically with the squash
#: *off* — an independent obstruction, not a squash interaction.
#:
#: **2. The -0.243% was 86% ROM corridor, not the squash.** The squash moves
#: ``cpu.centre``, and ``fetch_y = CY + cpu.centre + rom_touch_drop``, so a squash
#: of k *shortens the ROM corridor by k* — it is a negative
#: :data:`ROM_TOUCH_DROP`. The two rows differenced above do not share a corridor:
#: the unsquashed row is drop 22, and the squashed row only builds at **drop 5**
#: (solved interval ``[5, 17]`` at full squash — drop 22 cannot bind, at any pad).
#: Holding the corridor fixed instead, on the 21-round tour:
#:
#: | variant | eff. corridor | box | ticks | Δ |
#: |---|---|---|---|---|
#: | no ``SEEK_TELEPORT``, drop 22 | 22 | 649x495 | 192,066,009 | — |
#: | no ``SEEK_TELEPORT``, drop 7 | 7 | 649x495 | 191,951,785 | -0.059% |
#: | **squashed 10, drop 17** | **7** | 649x485 | 191,889,112 | **-0.033%** |
#: | squashed 10, drop 10 | 0 | 649x485 | 191,636,313 | -0.224% |
#: | squashed 10, drop 5 | -5 | 649x485 | 191,600,156 | -0.243% |
#:
#: Matched corridor to matched corridor the squash is worth **-0.033%**, and the
#: remaining -0.210% is corridor. That corridor reduction is nonetheless only
#: reachable *through* the squash, because §7.1 floors the drop at 5 and the squash
#: is the only thing that can push the effective length below it.
#:
#: **3. On the shipped machine it is worth exactly nothing.** With
#: :data:`SEEK_TELEPORT` on, the corridor's tick derivative has the **opposite
#: sign** — longer is better — so the squash only ever spends. Compensate it with
#: ``drop = 22 + k`` and it is free, to the digit, at 3 rounds and at 21:
#:
#: | variant | box | 21-round ticks | Δ |
#: |---|---|---|---|
#: | shipped (k 0, drop 22) | 649x495 | 189,164,256 | — |
#: | **k 3, drop 25** | **649x492** | **189,164,256** | **+0.000%** |
#: | k 7, drop 22 | 649x488 | 189,327,282 | +0.086% |
#: | k 8, drop 18 | 649x487 | 189,540,535 | +0.199% |
#:
#: ``k=3``/``drop=25`` is the deepest §7.1 allows (k=4 needs drop 26, which ties
#: the fetch ``r`` against ``in``). So the whole lever on the shipped machine is
#: **three blank rows off the bounding box for zero ticks** — and since the rows
#: were blank, it removes no runners either. Nothing here is worth -0.243%.
#:
#: **4. What *is* confirmed** is that the ROM corridor wants to be longer on this
#: machine — measured directly rather than inferred, and monotonically in
#: ``drop - k``. But it is not machine-independent: without the teleport the sign
#: reverses, so "the corridor wants to be longer" is a fact about the shipped
#: machine and not about corridors.
#:
#: The 3-round distortion is real for this pair (-0.855% against -0.243%, 3.52x
#: reproduced) but it is **not a property of the tour**: the same two tours price
#: removing :data:`SEEK_TELEPORT` at +1.511% and +1.534%, a ratio of 0.99. It
#: inflates boot-weighted effects and leaves per-frame ones alone.
#:
#: ``scratch/deadman3d-opt/squash_{h_probe,grid,compensate,tour}.py`` and
#: ``scratch/layout2/`` (which solves the drop interval instead of sweeping it).
#: **Shipped at k=12 — the band fully packed, zero blank rows.** A deliberate
#: choice to bank the structural change now and buy the ticks back through routing
#: later, on the grounds that routing is the tractable half and CPU packing is not.
#:
#: With the cluster lift also in (:data:`DOOM_CLUSTER_LIFT`), the depths price out
#: on the 3-round tour as:
#:
#: | k | drop | teleport | box | blank rows | Δ |
#: |---|---|---|---|---|---|
#: | 3 | 25 | yes | 649x471 | 9 | — |
#: | 7 | 22 | yes | 649x467 | 5 | +0.199% |
#: | 8 | 18 | yes | 649x466 | 4 | +0.359% |
#: | 10 | 5 | no | 649x464 | 2 | +0.641% |
#: | **12** | **5** | **no** | **649x464** | **0** | **+0.643%** |
#:
#: k<=8 keeps :data:`SEEK_TELEPORT`; zero blank rows needs k>=12, which forces it
#: off, and that is where the 0.6% goes — not into the squash itself. Note k=10 and
#: k=12 share a box: the last two rows are free, so taking the band all the way
#: costs 0.002% over stopping short of it.
#:
#: To go back to the tick-optimal geometry: set this to 3, restore
#: ``("deadman-3d_hires", "taped")`` to :data:`SEEK_TELEPORT`, and set
#: :data:`ROM_TOUCH_DROP` to 25.
#: **The men tier's 7 is not a footprint pick, it is what makes
#: :data:`STRAIGHT_TRIE` bind.** ``d`` takes the band from 32 rows to 22, and the
#: band is bottom-aligned, so by default all ten freed rows appear blank *above*
#: it and every lane — including the nine that decide ``mem_out_row`` — moves ten
#: rows south. The men tier's store request is a straight leg onto the router
#: strip's corner (``store_request_west``), so the adapter's output row and the
#: store's request wall have to be level to the row, and ``mem_out_row`` 20 -> 27
#: breaks it: *"the store's request wall is on row 157 and the adapter's request
#: leaves on row 164"*.
#:
#: ``squash_band`` moves the opposite way — it takes ``take`` of the freed rows out
#: of the room, pulling the band, the collector and the machine's height north by
#: exactly that many — so ``mem_out_row = 6 + slack - take`` and there is exactly
#: one ``take`` that lands back on 20. Swept 0..21, build-only, and it is a knife
#: edge: the adapter's row falls one per ``take`` and **7 is the only value that
#: binds** with the store where it is. Everything else needs ``store_offset`` dy to
#: follow it, which is a second knob and its own measurement.
#:
#: The tick model is *indifferent* to ``take`` — band and collector move together,
#: so every ``collector - row`` is unchanged — which is why this can be spent
#: entirely on binding without costing the decode anything.
#:
#: **The men tier is 6, not 7, because :data:`HIGH_COLLECTOR` spends a row here.**
#: The corridor is taken out of this same slack, so the slack is 20 instead of 21
#: and the knife edge moves with it: ``mem_out_row`` is back on 20 at ``take = 6``,
#: and the band grows one row *downward* instead of the memory lanes rising one row
#: — which is what keeps ``store_offset`` at its measured ``(0, 10)``. Re-swept
#: build-only over ``take`` 5..9 x dy 8..12, and 6/10 is again the only pair that
#: binds with the store where it is (7/9 and 8/8 also build, and both move the
#: store). The box is unchanged at 594x630 either way.
#: **The taped tier is 13, and the number stopped being the interesting part.**
#: With :data:`STRAIGHT_TRIE` on, the tour depends on ``squash_band`` and
#: :data:`ROM_TOUCH_DROP` *only through their difference* — the effective ROM
#: corridor ``drop - squash`` — provided the store is levelled to follow. Measured
#: on the 21-round taped tour with all five dispatch levers in, ``store_offset``
#: dy tracking the squash, ticks are **identical to the tick** at ``(13, 12)``,
#: ``(14, 13)``, ``(15, 14)`` and ``(16, 15)`` — 162,827,800 every time — and only
#: the box moves (642x385 down to 642x382). So the squash is spent on footprint,
#: which this family does not score, and 13 is chosen because it is the value the
#: **shipped** ``store_offset`` dy of -1 already levels: taped's own level equation
#: is ``store_dy = 12 - squash_band``, derived the same way men-v3's was, and 13 is
#: the one row of it that costs no store movement at all.
SQUASH_BAND: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 7,
    ("deadman-3d_hires", "men-v3"): 6,
}

ROM_TOUCH_DROP: dict[tuple[str, str], int] = {
    # 5 at k=12: a squash of k is a negative drop of k, so the full pack pushes the
    # effective corridor well below the unsquashed machine's and §7.1 floors the
    # drop at 5 (the BRN slab's discard `r` against `mem_resp`). The note below is
    # the k=3 reasoning, kept because it is the identity that made k=3 free.
    #
    # 25, not 22, because :data:`SQUASH_BAND` k=3 moves ``cpu.centre`` three rows
    # north and ``fetch_y = CY + cpu.centre + rom_touch_drop`` — so a squash of k
    # is a **negative drop of k**, and 22 + 3 restores the effective corridor the
    # unsquashed machine had. That is why k=3/drop=25 ties 189,164,256 exactly:
    # nothing about the ROM path changed. 26 is the §7.1 ceiling at k=3 (it ties
    # the fetch `r` against `in`), which is also why k=4 is unreachable.
    # **12 now, and 5 was never a free choice — it was the only drop a fully
    # packed band could bind.** ``STRAIGHT_TRIE`` does not build at all at
    # ``(12, 5)``: `'r' at (43, 176) must bind 'rom' but distances are
    # [('mem_resp', 48), ('rom', 50), ...]`. Raising the drop is what separates
    # them, and once it does the tour depends only on ``drop - squash``:
    #
    #     eff = drop - squash   ticks (21 rounds, all five levers)
    #     -4                    162,932,xxx
    #     -2                    162,858,xxx
    #     **-1**                **162,827,800**   <- the pick
    #      0                    +2.8%             <- a cliff, not a slope
    #
    # -1 is the last value before the cliff and the tick optimum, and the cliff at
    # 0 is sharp in both directions (swept squash 8..16 x drop 5..20). So the pin
    # in ``tests/test_seekrom.py`` asserts the *difference*, which is the invariant,
    # rather than either number, which is not.
    # **16, not 12, because :data:`FETCH_FOLD` moved every glyph this was measured
    # against two columns west.** The drop is not free to re-pick: at 12 the
    # folded machine's ``mem_pad`` floors at 3, and the column that buys it back
    # only exists at 13 and above. The window under the fold is **13..17** —
    # narrower than the ten rows this registry was written about, because the
    # fold's corridor copy of the opcode ``r`` sits one row *north* of the fetch
    # and is the first glyph to lose the ROM as the touch walks south (18 fails
    # with `'r' at (12, 155) must bind 'rom'`). Swept at 21 rounds, ``mem_pad`` 2
    # throughout: 13 -> 143,631,709, 14 -> 143,590,497, **16 -> 143,513,651**,
    # 17 -> 143,634,141. The drop is worth -0.109% on the *unfolded* machine
    # (145,970,818 -> 145,811,785 at pad 1), so it is not carrying the fold.
    ("deadman-3d_hires", "taped"): 21,
    # **The men tier's 7 is that same identity, and it is the difference between
    # :data:`STRAIGHT_TRIE` being a 9.7% win and a 4.2% loss.** ``SQUASH_BAND`` 7
    # pulls ``cpu.centre`` seven rows north, so without this the corridor between
    # the ROM's touch point and the fetch site is seven cells shorter than the
    # shipped machine's. That corridor is a FIFO whose *length is its capacity*,
    # and this family has measured wanting it longer — so the decode saving was
    # being handed straight back. Swept at 3 rounds against the shipped
    # 13,825,442, with ``STRAIGHT_TRIE`` and ``SQUASH_BAND`` 7 in:
    #
    #     drop   box       ticks        Δ
    #     0      601x630   14,404,232   +4.19%
    #     4      598x630   13,936,427   +0.80%
    #     **7**  595x630   12,485,229   **-9.69%**   <- the identity, and the pick
    #     10     595x630   12,669,645   -8.36%
    #
    # Note the *width* moving with it: ``build_for`` picks ``mem_pad`` by taking
    # the smallest feasible footprint, and a shorter corridor makes the narrow
    # pads infeasible. At the identity value the box returns to the shipped
    # 595x630 exactly, which is the tell that nothing outside the band has moved.
    # **9, not 7, for the same reason the taped tier moved: :data:`FETCH_FOLD`.**
    # The identity argument above still holds — 7 is what makes the ROM corridor
    # the length the shipped machine measured — but the fold re-prices what that
    # corridor is competing with. At 7 the folded machine's ``mem_pad`` floors at
    # 4; at 9 it floors at 3, and the column is worth more than the two rows of
    # corridor. 21 rounds: drop 7/pad 4 = 84,937,580, **drop 9/pad 3 =
    # 83,979,347**, drop 12/pad 3 = 84,007,933. Both 9 and 12 clear the floor, so
    # this is a plateau rather than an edge, and 9 is the near end of it.
    # **11, and the same trade one more click along — found by SMT, not by sweep.**
    # Once `check_bindings` stopped refusing decidable ties the pad floor became a
    # question worth asking properly, so §7.1 went into Z3: glyph positions fixed,
    # every pipe attachment an integer variable, and for each glyph against each
    # rival `lex_lt((d_w, ty_w, tx_w), (d_q, ty_q, tx_q))` — the engines' key,
    # verbatim and strict. Relaxing all six attachments to anywhere on the grid
    # says `mem_pad` 1 and even 0 are *satisfiable*, so the pad was never a binding
    # floor; freed one at a time, only `mem_resp` and `rom` unlock 1. This knob is
    # the one that moves `rom` — in y only, x is pinned at 7 — and the constrained
    # question (`tx_rom == 7`, y free) is SAT too. Confirmed on real captured
    # geometry rather than on the model: drops 5..10 are unsat at pad 1, **11..17
    # are sat**, so 11 is the near end of that plateau exactly as 9 was of the last.
    #
    # It pays for the two rows again, and by more than last time — 21 rounds,
    # `passed=True`, box unchanged at 496x674: drop 9/pad 2 = 80,342,861, **drop
    # 11/pad 1 = 79,341,770 (-1.246%)**, 91.26 -> 90.13 t/instr.
    #
    # Raising :data:`SQUASH_BAND` with it to hold the corridor difference constant
    # is the obvious next click and does **not** build as-is: at squash 8 the
    # store's request wall lands on row 159 while the adapter's request leaves on
    # row 157, and a straight leg needs them level. It needs `store_offset` dy
    # moved with it — men-v3 has its own level equation, the way taped's is
    # `store_dy = 12 - squash_band`. Untested, and the pad is already at 1.
    #
    # **13, and the rate is exact: two drop rows buy one pad column.** A pad column
    # moves the band's westmost `r` one cell nearer `rom` and one further from
    # `mem_resp`, so the drop gives back two rows to restore the margin — which makes
    # the frontier a line rather than a cliff, and the solver reads it off directly.
    # Pad 0 needs drop >= 13. 21 rounds, `passed=True`, 496x674: drop 11/pad 1 =
    # 79,341,770, **drop 13/pad 0 = 78,674,318 (-0.841%)**, 90.13 -> 89.37 t/instr.
    # The column is worth 2.436 and the two corridor rows cost 0.649 each, so the
    # margin is thin, and the next click is not a click at all: `_flat_lane` advances
    # with ``while x < target``, so a `mem_x` below a lane's natural column is
    # silently ignored *for that lane*. Pad -1 moves six of nine MEM lanes and pad -2
    # is byte-identical to it; pads -2..-4 all "build" and are no-ops.
    ("deadman-3d_hires", "men-v3"): 13,
}

LANE_PITCH: dict[tuple[str, str], int] = {
    ("deadman-3d", "taped"): 1,  # band 43 -> 31 rows, riser 22 -> 16
    # hires transfers, and is worth **more** than the family it came from
    # (-4.351% against -4.04%) — but only on a machine whose store has already
    # been fixed, which is the whole lesson of measuring it twice:
    #
    #   | machine                          | pitch 2     | pitch 1     | Δ       |
    #   |----------------------------------|-------------|-------------|---------|
    #   | before the 11-bank cut (c51a748) | 990,895,368 | —           | -0.401% |
    #   | after it                         | 365,333,921 | 349,439,532 | -4.351% |
    #
    # Same builder change, same program, an order of magnitude apart. On the old
    # store the CPU was *blocked* on a 223-slot ring for ~68% of the run
    # (METRICS M14 measured exactly that), so shortening its walk bought idle
    # time and nothing else. The absolute saving is not even conserved — it grew
    # 396,380 -> 15,894,389 — because the dispatch walk only became the critical
    # path once the store stopped being it.
    #
    # Box unchanged at 514x451: the eleven rows the stagger frees are left blank
    # *above* the band, so the collector, the structures band and every block
    # outside the CPU stay where they are.
    #
    # **Withdrawn when the seek drum landed, then recovered by ROM_TOUCH_DROP.**
    # The history is worth keeping because the withdrawal was correct at the time
    # and the recovery did not come from re-measuring it — it came from removing
    # the constraint that made it unaffordable. Pitch 1 moves the CPU's east-wall ports
    # twelve rows north, and on a seek build that breaks the memory-response
    # binding: `'r' at (43, 189) must bind 'rom' but distances are
    # [('mem_resp', 54), ('rom', 58), ...]` — §7.1 decides by distance to rivals,
    # and the stagger makes the wrong rival closer. The build does not fail
    # gracefully at one pad either; the whole floor moves, 15 -> 28 (swept
    # 15..35, nothing below 28 binds).
    #
    # Every column of pad is paid twice by every memory instruction, so the two
    # very nearly cancel — and the pad wins. 21-round tour, ticks to frame 20:
    #
    #   | pitch | pad | box | ticks | Δ |
    #   |---|---|---|---|---|
    #   | **2** | **15** | **517x496** | **254,446,307** | — |
    #   | 1 | 28 | 526x496 | 255,106,862 | +0.260% |
    #   | 1 | 31 | 528x496 | 258,144,151 | +1.453% |
    #   | 2 | 28 | 526x496 | 270,409,329 | +6.274% |
    #
    # Read the last row against the first: the pad alone is worth 6.27%, and the
    # stagger recovers 5.99% of it. A -4.351% lever is now a +0.260% loss, and
    # nothing about the lever changed — the machine under it did. This is listed
    # in `revalidate.py`'s DECLINED table so it is re-checked rather than
    # remembered; if the drum's pad floor ever comes down, it goes straight back.
    #
    # It did come down, and by a route nobody had looked at: the pad was never the
    # only way to move a distance. :data:`ROM_TOUCH_DROP` moves the *ROM* touch
    # south instead of pushing the memory band east, which costs no lane walk at
    # all — and the whole conflict was five cells. With drop 22 the band staggers,
    # the pad stays at 15, and the pair measures **-7.326%** (204,117,437 ->
    # 189,164,256, box unchanged at 649x495).
    ("deadman-3d_hires", "taped"): 1,
    # The men tier needs its own key, and this is the trap in porting a machine
    # between stores: every hires lever in this file is keyed on ``"taped"``, so a
    # men build silently forfeits all of them. Pitch 1 alone is **-10.55%** there
    # (153,217,464 -> 137,064,987), so the men store's advantage over the drum was
    # measured against a CPU handicapped by a tenth — 17.9% before this key, 26.6%
    # after. Nothing about the pitch is tier-specific; only the registry was.
    ("deadman-3d_hires", "men-v3"): 1,
}

#: ``(slug, tier)`` pairs whose decode trie draws its row-forcing nodes as ``d``
#: instead of ``x`` — the last thing standing between :data:`LANE_PITCH`'s
#: staggered band and a band with no gap rows at all.
#:
#: :func:`_uneven_gaps` explains why every remaining gap exists: ``x`` **always
#: turns**, so a node whose up half is one lane cannot sit on that lane's row —
#: the lane's entry ``>`` would overwrite it and every opcode routed through would
#: walk east into the wrong lane, silently and with every pipe still bound.
#: ``SPEC.md`` gives ``d`` the property ``x`` refuses: *turn clockwise if BP > 0,
#: **else go straight***. A ``d`` on the lane's own row therefore **is** that
#: lane's entry, and the row is not needed twice.
#:
#: The equivalence is exact and narrow, and :func:`_uneven_gaps` carries the
#: derivation: after the ``L - 1`` shifts a level-``L`` node owes, ``BP`` is the
#: slot's offset inside that node's dyadic interval, so ``BP == 0`` picks the up
#: half iff the up half is exactly ``{lo}``. Under the contiguous packing every
#: single-lane up half *is* ``{lo}``, so all ten of ``deadman-3d_hires``' gaps go
#: and the band falls **32 rows -> 22**, ten rows the profile could see: ten of the
#: band's 55 rows carried nothing but ``>]x``.
#:
#: Why that is a tick lever and not a footprint one: the band is bottom-aligned
#: (:func:`build_cpu`, ``squash_band`` off), so the rows it saves are left blank
#: *above* the band and every lane moves **south, toward the collector**. A lane's
#: drop is ``collector - row`` and the trie's descent is ``|centre - row|``, so ten
#: rows come off both at once. Measured, 21-round men-v3 tour: see the table in
#: the commit.
#:
#: Off by default and keyed by ``(slug, tier)``, so every other machine's
#: checked-in grid is byte-identical. It is also inert at ``lane_pitch = 2`` — the
#: rows are two apart there and the ``d`` never fires — so the pair is
#: :data:`LANE_PITCH` **and** this, never this alone.
#: **The taped tier takes it, and it does not transfer as a single key.** At
#: taped's shipped :data:`SQUASH_BAND` 12 / :data:`ROM_TOUCH_DROP` 5 it does not
#: build; the pair has to be re-derived for this tier (12/5 -> 13/12, effective
#: corridor -1), and at the re-derived pair it is **-4.643%** on the 21-round tour:
#: 189,821,916 -> 181,008,755 at 643x384, ``passed``. The pair *alone*, without the
#: trie, is worth -0.047% — so effectively all of it is this lever.
#:
#: It reads smaller here than men-v3's -10.37% and the absolute saving is smaller
#: too (8.81M against ~13.9M), which is what a dispatch lever does on a machine
#: whose memory stall is ~135 ticks an access against men-v3's ~42: the walk it
#: deletes is a smaller share of a longer instruction.
STRAIGHT_TRIE: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: ``(slug, tier)`` pairs that run a **second collector one row above the fetch**,
#: so a lane above the trie root stops there instead of falling past the fetch row
#: to the collector under the band and climbing back up to it.
#:
#: ## The overshoot, and why it is exactly ``2 * (collector - centre)``
#:
#: An instruction's return walk is a rectangle: east to the drop column, south to
#: the collector, west to column 1, north up the riser to the fetch row. Its
#: vertical half is
#:
#:     (collector - centre) + |centre - row| + (collector - row)
#:
#: — riser, trie descent, drop. For a lane **below** the root that collapses to
#: ``2 * collector - 2 * centre``, a constant, and there is nothing to win. For a
#: lane **above** it, it collapses to ``2 * collector - 2 * row``, against a
#: Manhattan distance of ``centre - row``: the man walks past the cell he is trying
#: to reach, continues to the bottom of the band, and then climbs back through the
#: rows he just fell through. The waste is ``2 * (collector - centre)`` — **14
#: ticks on ``deadman-3d_hires`` men-v3**, paid by every instruction above the
#: root, forever, and invisible in any per-region reading because the three legs
#: are three different boxes.
#:
#: A corridor anywhere strictly between the lane and the fetch row removes all of
#: it at once: ``(hi - row) + (centre - hi)`` telescopes to ``centre - row`` **for
#: any ``hi``**. So one corridor is as good as one per lane, and the only question
#: is where to put it — as low as possible, to catch the most lanes. That is the
#: row directly above the fetch, and the fetch row is the last lane of the root's
#: up half, so the corridor serves *every* lane the root sends north.
#:
#: ## What it costs
#:
#: One band row, taken out of :data:`LANE_PITCH`'s stagger slack rather than out of
#: the room — the band simply starts a row higher, so the collector, the structures
#: band, the seek tail and the room's own height do not move. What it does move is
#: the trie: the root's up leg now spans one more row, which is ``+1`` on every
#: lane in the up half. The net is ``13 * P(above the root) - 1 * P(up half)``.
#:
#: ## Measured, and measured twice
#:
#: ``scratch/deadman3d-opt/menprobe/dispatch_bench.py`` builds this loop and
#: nothing else — the real :func:`_uneven_trie`, the shipped lane lengths and drop
#: columns, a synthetic ROM streaming the measured opcode mix — and runs in under a
#: second:
#:
#:   | corridor rows | t/instr | delta |
#:   |---|---|---|
#:   | 0 (shipped) | 78.976 | — |
#:   | **1** | **70.739** | **-8.238** |
#:   | 2 | 72.231 | -6.746 |
#:
#: Two rows is what this needs if :func:`_uneven_trie` insists on standing an
#: inline ``d`` at ``slot_rows[min(down)] - 1``; anchoring it to its up child's row
#: instead (which is what ``d`` going *straight* means) brings it back to one, and
#: the second row is worth 1.5 ticks. The arithmetic above predicts ``-9.043``
#: before the trie tax and the bench lands 0.8 under it, which is that tax.
#:
#: Off by default and keyed by ``(slug, tier)``, so every other machine's grid is
#: byte-identical. It requires ``trim_dead`` and ``lane_pitch = 1`` (the corridor is
#: paid for out of the stagger) and refuses :data:`TOP_RETURN_BUS`, which wants the
#: same column 1 above the fetch for the opposite heading.
#: **The taped tier takes it, and what stood in the way was its opcode map.**
#: The corridor row is the row the trie root's up half leaves blank, and the
#: assertion above it is that the trie parked nothing that *turns* there. Under
#: taped's tuned :data:`OPCODE_SLOTS` it always did, at every squash from 10 to 14:
#: `the trie put [(9, '>'), (11, 'x')] on the corridor row`. men-v3 has no
#: ``OPCODE_SLOTS`` entry, so it falls through to the contiguous packing 0..20,
#: every up-half node lands on a lane row, and the corridor is clean by accident of
#: the default.
#:
#: The cause is one hole: taped's map left **slot 15 unused**, the only gap in the
#: root's up half, so the node splitting ``{12..15}`` had nowhere to stand but the
#: row above lane 14 — exactly the row this opens. Filling 15 fixes it, and a
#: build-free screen over every rank-preserving map within three slots of the
#: shipped one (848 candidates) finds **18 that clear the corridor**, of which
#: ``MODI 13 -> 14, NEG 14 -> 15`` is the only one that also keeps the shipped
#: ``lane_x0`` of 12 under :data:`TIGHT_TRIE_COLS` *and* the shipped drum cost. The
#: map change is worth +3,291 ticks on its own — 0.002%, i.e. free.
#:
#: Worth **-5.34%** as the marginal inside the full five-lever stack (172,012,101
#: -> 162,827,800 on the 21-round tour), against -6.32% on men-v3.
HIGH_COLLECTOR: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: ``(slug, tier)`` -> lane **ranks** after which the band opens one blank row,
#: solely to give a decode edge the vertical slack :func:`_trie_columns` prices.
#:
#: **This is the answer to "the two zero-slack penalty columns", and the premise
#: it was filed under was wrong.** The standing note said the columns exist
#: because "an inline ``d`` is pinned to its lane's row, and its parent is pinned
#: between two adjacent inline ``d``s". On the current geometry **no node in
#: either tier's trie is ``inline``** (dumped from the live build: 21 nodes,
#: men-v3, all ``inline=False``), so the ``inline and sign < 0`` branch of the
#: slack rule never fires. The zero slack is the *other* branch — two nodes on
#: adjacent rows, ``|crow - prow| - 1 == 0`` — and that is a property of the lane
#: rows, not of inlining.
#:
#: That distinction is the whole lever. :data:`LEAN_TRIE` provably cannot open
#: these edges (it moves a node toward its parent, so it can only shrink the
#: distance), and that was read as "nothing can". But a non-inline node is pinned
#: to ``slot_rows[min(down half)] - 1``, i.e. to the **lanes**, so moving a lane
#: moves the node — and one blank row in the right place opens the edge.
#:
#: Searched exhaustively over the shaping pre-pass (the pure ``_trie_shape`` ->
#: ``_trie_columns`` composition, greedy lean included, so it is what the build
#: would compute), up to three added rows:
#:
#: | tier | rows | ranks | ``lane_x0`` |
#: |---|---|---|---|
#: | men-v3 | **1** | ``(20,)`` | 10 -> **9** |
#: | taped | **2** | ``(8, 13)`` | 10 -> **9** |
#:
#: 9 is the floor for both: no assignment of three or fewer rows reaches 8, and
#: men-v3's rank 20 is the *unique* single-row solution.
#:
#: **Empty because it measures a loss, and the loss is the pad.** men-v3,
#: ``ranks=(20,)``, 21 rounds, gated ``passed=True fatal=None``:
#:
#: | build | box | ``lane_x0`` | ticks | Δ |
#: |---|---|---|---|---|
#: | shipped | 496x674 | 10 | 82,530,131 | — |
#: | ``(20,)`` + ``store_offset`` dy -1 | 496x673 | **9** | 84,153,261 | **+1.97%** |
#:
#: The column is genuinely bought — ``lane_x0`` 9 is read off the live build, and
#: the room even loses a row (674 -> 673). It still loses, for the reason
#: :data:`MEM_PAD_FOR` is a standing trap: the row walks the CPU's east wall
#: against ``mem_resp``, and men-v3's pad floor rises **3 -> 4** (pads 2 and 3
#: both refuse with ``'r' at (22, 153) must bind 'mem_resp'``). A pad column costs
#: more than a ``lane_x0`` column saves, so the trade is negative by about the
#: difference. Price this against a pad re-sweep, never against columns saved.
#:
#: The added row also de-levels the store's request leg — it lands the adapter's
#: request on 158 against the store's wall on 159 — and ``store_offset`` dy **-1**
#: is the unique compensation (dy -3..+3 swept; every other value misses by the
#: same amount it moves).
#:
#: **Revisit condition, arithmetically:** this pays the moment men-v3's pad floor
#: stops rising with the row — i.e. when a build with ``ranks=(20,)`` binds at
#: ``mem_pad`` 3. Any lever that moves the CPU's east wall *east* relative to
#: ``mem_resp``, or that moves the ``in`` room, changes that. The mechanism itself
#: is geometry-independent and stays here ready: it is the only lever that can
#: open a zero-slack decode edge, because a non-inline node is pinned to the
#: lanes and :data:`LEAN_TRIE` can only ever shorten an edge.
#:
#: **That revisit condition was met, and it still loses — the condition was the
#: wrong one.** Once :func:`check_bindings` stopped refusing decidable ties and
#: :data:`INPUT_NORTH_WEST` was keyed for men-v3, the floor fell to 2 without the
#: row and to **3 with it** — exactly the "binds at ``mem_pad`` 3" this asked for.
#: Re-gated at 21 rounds (``ranks=(20,)``, ``INPUT_NORTH_WEST`` 8 — it is a
#: distance *west of* ``lane_x0`` and has to give back the same column — and
#: ``store_offset`` dy 10 -> 9, the unique value in 7..13 that keeps the adapter's
#: request leg level): 496x673, ``lane_x0`` 9, ``passed=True``, **81,711,788
#: against 80,342,861 — +1.704%**.
#:
#: The reason it survives its own revisit condition is that the condition counted
#: pad *columns* instead of ``mem_x``. What every MEM lane walks is
#: ``mem_x = lane_x0 + max(prefixes) + mem_pad``, and that is **21 either way**
#: (18+1+2 shipped, 17+1+3 with the row): the trie hands the column straight to
#: the pad, so the 54.16% of instructions that carry a MEM band gain *nothing* and
#: only the other 45.8% gain a column — about 0.92 cells/instr — against a whole
#: extra band row. Restate the condition on the quantity that moves:
#: **this pays when a ``ranks=(20,)`` build's ``mem_x`` comes out below 21**, not
#: when its pad does.
TRIE_SLACK_ROWS: dict[tuple[str, str], tuple[int, ...]] = {}

#: ``(slug, tier)`` pairs whose decode trie prices its columns **per node** instead
#: of two per level. See :func:`_trie_columns` for the rule and why one column a
#: level flat is infeasible.
#:
#: What it buys is not the trie: it is ``lane_x0``. The band's origin is the deepest
#: node's column plus one, so every column the trie saves is a column off the trie
#: walk (paid once an instruction), off ``mem_x`` and every lane's length, off every
#: drop column, and off the walk back west (paid again). ``deadman-3d_hires``
#: men-v3 goes ``lane_x0`` **14 -> 12**.
#: **The taped tier takes it, at -1.64% — within a hundredth of men-v3's -1.65%,
#: which is the tell that this one is pure geometry.** It moves ``lane_x0`` 14 ->
#: 12 on both tiers.
#:
#: Two taped-only couplings, neither of which men-v3 could have shown:
#:
#: * :data:`INPUT_NORTH_WEST` is keyed on taped and not on men-v3, and it is a
#:   distance *west of* ``lane_x0``. Narrowing the trie walks the I room off the
#:   north wall — `in_west 13 puts the input pipe off the CPU north wall` — so the
#:   two move together: 13 -> 11, which is again ``CX + 1``, the westernmost legal
#:   column.
#: * it does **not** compose with :data:`SEEK_TIGHT_STRUCT_DROPS` if ``lane_x0``
#:   goes below 12. The tightened structured drops are floored at ``lane_x0``, and
#:   at 11 a simple lane's descent lands on a slab entry's ``<``
#:   (`collision at (14, 34)`). That is why the opcode map above is chosen for
#:   keeping ``lane_x0`` at 12 rather than for the narrowest trie: the 11-column
#:   maps exist and are 2.7% *worse*, because they cost the whole of
#:   ``SEEK_TIGHT_STRUCT_DROPS``.
TIGHT_TRIE_COLS: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: ``(slug, tier)`` pairs that **fold the fetch prologue onto the return path**.
#:
#: The fetch row is ``>rbr``: arrive, read the opcode, park it in BP, read the
#: operand. Four columns, and :func:`_trie_columns` starts the trie east of them —
#: so the prologue's width is a term in ``lane_x0``, and ``lane_x0`` is the
#: anchor of one rigid body (fetch, trie, lanes, drops, corridor and riser all
#: translate with it). A column off ``lane_x0`` measured **2.436 t/instr** on
#: ``deadman-3d_hires`` men-v3.
#:
#: The ``r b r`` order is a data dependency and cannot be reordered, but nothing
#: says the three glyphs have to share a row. The man arrives at the fetch along
#: cells he is walking anyway, so ``r`` and ``b`` can stand **on the approach**
#: and the fetch row keeps only ``>r`` — the arrival turn and the operand. Root
#: column 5 -> 3, ``lane_x0`` 12 -> 10.
#:
#: The approach is not one path, and that is the whole difficulty. After
#: :data:`HIGH_COLLECTOR` about 70% of instructions come west along the corridor
#: and 30% climb the riser, the two converge **only** at the fetch's own ``>``,
#: and the prologue has to run on every instruction. So it is drawn **twice**:
#:
#: * the corridor copy, on ``hi_row`` — ``b`` at column 2 (no trie node can reach
#:   it: the root is the westmost at column 3 and ``place`` only moves east) and
#:   the opcode ``r`` on the westmost cell of that row the trie left blank,
#:   normally column 4;
#: * the riser copy, in column 1 at ``centre + 2`` and ``centre + 1`` — cells that
#:   were ``.`` on a path the man already walks.
#:
#: **Duplication is free here.** Neither copy adds a walked cell to either path,
#: and neither man ever crosses the other's copy: the corridor turns south at
#: ``(1, hi_row)`` and stops at the fetch, the riser climbs column 1 from below
#: and stops at the fetch. So exactly one opcode ``r`` fires per instruction —
#: the failure mode that would matter (two fetches, or a stale opcode) is ruled
#: out by the geometry rather than by a count.
#:
#: Three ``r``\ s now want the ROM pipe instead of two, and §7.1 is *nearest*, ties
#: fail. Both new ones are hard against the west wall the ROM touches, which is
#: the best possible place for them: the riser copy is **nearer** the touch than
#: either old fetch ``r``, and the corridor copy ties the old operand ``r``'s
#: distance rather than beating it.
#:
#: ## Measured, 21-round tour, ``res.frame_ticks[-1]``
#:
#: | tier | | ``rom_touch_drop`` | ``mem_pad`` | box | ticks | Δ |
#: |---|---|---|---|---|---|---|
#: | men-v3 | baseline | 7 | 2 | 496x672 | 86,981,643 | — |
#: | men-v3 | fold | 7 | 4 | 496x672 | 84,937,580 | -2.350% |
#: | **men-v3** | **fold** | **9** | **3** | **496x672** | **83,979,347** | **-3.452%** |
#: | taped | baseline | 12 | 1 | 625x396 | 145,970,818 | — |
#: | taped | fold | 12 | 3 | 625x396 | 144,189,717 | -1.220% |
#: | **taped** | **fold** | **16** | **2** | **625x396** | **143,513,651** | **-1.683%** |
#:
#: ``lane_x0`` **12 -> 10** on both tiers; neither box moves. men-v3 is 98.805 ->
#: 95.395 t/instr over 880,332 instructions, i.e. **-3.41 t/instr**, against the
#: 2.436 a single ``lane_x0`` column was measured at — so the two columns arrive
#: at about 70% of face value and the rest is the pad, below.
#:
#: **Two columns off the trie is not two columns off the machine, and the
#: difference is ``mem_pad``.** The fold walks the CPU's whole rigid body west,
#: which drags the memory band toward the pipes that were measured against its old
#: position, and the pad's floor rises: men-v3 2 -> 4 and taped 1 -> 3 at the
#: shipped :data:`ROM_TOUCH_DROP`. That tax is about a third of the gross win, and
#: most of it comes back by re-sweeping the drop, which is what that registry's
#: "ten rows of freedom" is for — the rows are still there, the fold just moved
#: which of them work. Both new drops were checked against a **baseline** at the
#: same drop, so none of the win above is the drop's: taped's baseline at drop 16
#: is 145,811,785, i.e. the drop is worth -0.109% on its own and the fold is
#: -1.576% marginal to it.
#:
#: The fold also *narrows* the drop's window from above, and for a reason worth
#: knowing: the corridor's copy of the opcode ``r`` sits at ``(4, hi_row)``, one
#: row **north** of the fetch, so it is the first ``r`` to lose the ROM as the
#: touch point walks south. On taped the window is 13..17 and 18 fails with
#: ``'r' at (12, 155) must bind 'rom'`` — which is this glyph. Re-sweep the drop
#: and the pad together after anything that moves either.
FETCH_FOLD: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: ``(slug, tier)`` pairs that take the **tuck** on top of :data:`FETCH_FOLD`:
#: the operand ``r`` comes off the fetch row too, so the row is ``>>`` — pure
#: steering — and each return path carries its own whole ``r b r`` on cells it
#: already walked.
#:
#: ## Why the corridor's U-turn exists at all, and what its floor is
#:
#: ``x`` turns **relative to the man's heading** (``cw``/``ccw`` of the incoming
#: direction, not an absolute row), so a trie node's up/down halves are only the
#: halves the generator drew if every man enters it with the same heading. The
#: root is entered **eastbound**. The riser's man arrives from the west and gets
#: that for free; the corridor's man arrives from the **east**, so he must walk
#: past the root and come back, and no re-routing removes that — it is the ISA,
#: not the layout. This is why "converge before the prologue" and "merge the
#: corridor and fetch rows" both died: they were trying to delete a U-turn that
#: the glyph requires.
#:
#: What *is* free is how far past. The fold sends him to column 1: past the root
#: (3), past the ``b`` he needs (2), then south and **two** east. He cannot turn
#: on column 3 — that is the root's own up-leg, and it has to stay a
#: pass-through for the northbound men the root sends up it — but he can turn on
#: column 2, and a ``>`` under it puts him on the root in one more step. Five
#: cells from the up-leg becomes three; Manhattan is one.
#:
#: The prologue then has to move east of the up-leg, because column 2 is now the
#: turn. It goes on the three westmost cells of ``hi_row`` no trie leg occupies,
#: **in reverse order** — operand ``r`` nearest the root, then ``b``, then the
#: opcode ``r`` — because westbound is the order he meets them in. All three were
#: ``<`` he already walked, so they are free, and the build asserts every
#: corridor drop is strictly east of the opcode ``r`` so no lane can join the row
#: with half a prologue behind it.
#:
#: The riser's copy grows by one cell (``r b r`` instead of ``r b``, the operand
#: no longer being shared on the fetch row) but not by one **tick**: the cell it
#: takes is a ``.`` he was walking anyway, and both ``>``s on the fetch row are
#: on his path either way. The riser is unchanged at 9 steps from the collector.
#:
#: **The invariant, and it is the silent one:** exactly one opcode ``r`` fires
#: per instruction on every path. Two is a double fetch, none is a stale opcode,
#: and both bind cleanly and survive a short screen. See
#: ``tests/test_lm1_cpu_trie_pack.py``.
#:
#: ## Measured, 21-round tour, ``frame_tiles=(2, 2)``, ``passed=True fatal=None``
#:
#: | tier | before | after | | size |
#: |---|---|---|---|---|
#: | men-v3 | 82,530,131 | **81,309,610** | **-1.479%** | 496x674, unchanged |
#: | taped | 141,458,930 | **140,656,599** | **-0.567%** | 625x400, unchanged |
#:
#: men-v3 is the arithmetic almost exactly: two ticks off the 69.6% of the
#: 880,332 instructions that arrive along the corridor is 1.225M, against 1.221M
#: measured. Neither wall moves and ``lane_x0`` is untouched, so there is no pad
#: to re-sweep — this is the rare change that is neither a deleted cell nor a
#: narrower band, but a shorter *walk* over the cells that were already there.
FETCH_TUCK: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

#: ``(slug, tier)`` pairs whose decode nodes **lean toward their parent** instead
#: of standing on the boundary row between their two halves. :func:`_lean_row`
#: carries the proof that the boundary is never the cheap end, and that the cheap
#: end needs no frequency table: a node's own traffic is exactly the sum of its
#: two children's, so the weighted median of the three legs sits on the parent,
#: and the parent always lies strictly outside the interval the node may move in.
#:
#: **It is not a re-run of frequency-shaping the trie.** Nothing here moves a
#: lane, a slot or an opcode number: the leaves, the ROM image, the band, the
#: drops, the collector and the room are all untouched, and only the ``x``/``d``
#: cells inside the trie's own columns change row. That is also why it composes
#: with :data:`TIGHT_TRIE_COLS` rather than fighting it: ``lane_x0`` is the one
#: thing a lean could spoil, and no mode below is allowed to move it.
#:
#: It moves very few nodes and that is not a disappointment. The band is pitch-1
#: packed and most of its nodes are inline ``d``s pinned to a lane's row, so
#: nearly every node already stands on the only row it can. The ones that move are
#: the interior ones with an unbalanced split, and they are exactly the ones the
#: whole band descends through: on ``deadman-3d_hires`` men-v3 the root's up child
#: carries about 68% of the executed instructions and stood **nine** rows above
#: the fetch while its own down child was four rows below it.
#:
#: The value is a mode rather than a flag. ``"safe"`` leaves every leaned edge the
#: vertical slack :func:`_trie_columns` needs to keep its child at ``parent + 1``,
#: so it *provably* cannot widen the band. ``"greedy"`` leans the whole way and
#: keeps a node only when :func:`build_cpu`'s pre-pass shows that node's extra
#: column is one the deepest branch was going to spend anyway — a strictly larger
#: set of moves, each of them a further reduction in the same weighted walk, and
#: none of them able to move ``lane_x0`` either.
#:
#: **They split by tier, and the split is measured, not reasoned.** 21-round tour,
#: both gated ``passed=True``, boxes unmoved at 496x672 / 625x391:
#:
#: | tier | before | ``"safe"`` | ``"greedy"`` |
#: |---|---|---|---|
#: | men-v3 | 91,671,374 | 89,443,340 | **88,217,704** |
#: | taped | 150,075,022 | **147,213,896** | 147,704,008 |
#:
#: (Both columns also carry :data:`HIGH_DROPS_FREE`, which is on for both tiers.)
#: Greedy is worth a further -1.37% on men-v3 and **+0.33% on taped**, which is
#: the whole reason this is a mode and not a flag: the two tiers do not run the
#: same trie — they have their own ``OPCODE_SLOTS`` and their own band — so the
#: extra leans land on different nodes and the ``lane_x0`` guard clears a
#: different set of them. The guard is about columns and says nothing about the
#: seek drum the taped tier drives, so "does not widen the band" is necessary and
#: not sufficient, and the tour is what settles it. Do **not** carry the men-v3
#: mode across on the argument that the lever is geometric.
LEAN_TRIE: dict[tuple[str, str], str] = {
    ("deadman-3d_hires", "taped"): "safe",
    ("deadman-3d_hires", "men-v3"): "greedy",
}

#: ``(slug, tier)`` pairs where a drop the **high corridor** catches is exempted
#: from the slab columns, and from the band below the corridor.
#:
#: The drop discipline is a suffix walk from the bottom of the band: a column has
#: to clear every lane below the one taking it, and it may not be a slab's entry
#: column, because a slab entry leaves ``.`` on the collector row and a simple man
#: sharing that column would sail past his turn west into the wrong slab.
#:
#: Both halves of that stop being true for a lane :data:`HIGH_COLLECTOR` catches.
#: Such a drop is a few rows long and stops on ``hi_row``, which sits above the
#: collector, above every slab and above most of the band — so it never crosses
#: the lanes the suffix walk was protecting it from, and it never reaches the
#: collector row where a slab's column would swallow it. The slab's own descent
#: occupies that column on strictly **lower** rows, so the two share a column and
#: never a cell. :func:`build_cpu` checks the one precondition (``hi_free``: every
#: slab lane below the corridor) instead of assuming it.
#:
#: This is what the reserved columns were costing on ``deadman-3d_hires`` men-v3:
#: ``BRN`` and ``BRZ`` sit on the two columns immediately east of the hot memory
#: lanes' last glyph, so ``ST``, ``ADD``, ``SUB``, ``DIV`` and ``LDA`` were each
#: pushed one or two columns east of what their own micro-program needed — and a
#: drop column is paid **twice**, once walking out to it and once walking the
#: corridor back west from it. Afterwards **every** corridor-bound drop on men-v3
#: except three sits on ``lane_end + 1``, which is the floor, and the three that do
#: not are held there by a neighbour's last glyph rather than by a reservation.
#:
#: 21-round tour, alone, both tiers gated ``passed=True`` with the boxes unmoved:
#: men-v3 91,671,374 -> 90,851,744 (**-0.89%**), taped 150,075,022 -> 148,827,290
#: (**-0.83%**). It composes additively with :data:`LEAN_TRIE` — men-v3's two
#: levers are -2.87% and -0.89% alone against -3.77% together — which is what a
#: pair that touches disjoint cells should do, and is the check that neither is
#: quietly buying the other's win.
HIGH_DROPS_FREE: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
    ("deadman-3d_hires", "men-v3"),
}

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
#:
#: ## ``deadman-3d_hires``, and why it is worth twice what the parent got
#:
#: hires had never been measured here — the set had exactly two historical values,
#: ``set()`` and ``{"deadman-3d"}`` — so this is a first reading, not a reversal.
#: It is the largest single win the slug has taken: **-36.04%** of the tour,
#: against the parent's -11.0% on the same tier.
#:
#: The reason is scale, and it is the one thing about the seek drum that *does*
#: transfer as an argument rather than as a number. A taken long jump discards
#: ``2 * ((t - k - 1) mod n)`` words of the ROM man's lap (:func:`rom_words`), so
#: the classic drum's cost per long jump is linear in ``P`` while the seek
#: ladder's is logarithmic in the fold. hires is P=9,225 against the parent's
#: ~4,300: the thing being deleted is twice as large, and what replaces it is
#: not.
#:
#: Nothing else transferred. Every coupled registry had to be re-derived, and
#: three of them landed nowhere near ``deadman-3d``'s values:
#:
#: * **the fold.** :data:`ROM_ROWS`' 88 does not build under the drum at all —
#:   a seek row addresses its words as ``row*K + offset`` with ``K = 128``, and
#:   at 88 rows the first row holds 152. 110 is the shallowest that builds, 111
#:   the shallowest that *runs* (110 and 121..123 pack a literal whose reverse
#:   reading leaves signed 64 bits — the hazard :data:`SEEK_TIER_LAYOUT` records
#:   for the parent, in a different place on this program). See
#:   :data:`SEEK_TIER_LAYOUT` for the curve; 119 is the pin.
#: * **the pitch.** :data:`SEEK_SLAB_PITCH` is worth **8.85%** here against the
#:   parent's fraction of a percent, because on hires it is not a width knob at
#:   all — it is what unblocks the ``mem_pad`` floor, 35 -> 18.
#: * **the pad.** :data:`MEM_PAD_FOR` plus :data:`INPUT_NORTH_WEST` take it the
#:   rest of the way, 18 -> 15, which is where the *classic* build already sat.
#:
#: The whole table, native ``fast_littleman``, 21-round tour (frames 1..20),
#: ``frame_tiles=(2, 2)``, every row ``fatal is None and passed is True``.
#: Baseline is HEAD-at-``add1e25``'s classic hires machine:
#:
#: | build | box | pad | tour ticks | Δ |
#: |---|---|---:|---:|---:|
#: | classic (baseline) | 514x451 | 15 | 365,333,921 | |
#: | seek alone, fold 120 | 531x497 | 35 | 258,146,477 | -29.35% |
#: | + :data:`SEEK_SLAB_PITCH` 11 | 517x496 | 18 | 236,380,143 | -35.30% |
#: | + :data:`INPUT_NORTH_WEST` 13 / :data:`MEM_PAD_FOR` 15 | 517x496 | 15 | 234,324,256 | -35.86% |
#: | + :data:`SEEK_TAKEN_DROP_EAST` | 517x496 | 15 | 233,851,301 | -35.99% |
#: | + :data:`SEEK_TELEPORT` (**shipped**) | 517x496 | 15 | **233,658,800** | **-36.04%** |
#:
#: Each row is the shipped build with exactly one registry taken back off, so
#: every entry is proved load-bearing rather than assumed. Note the ordering: the
#: pad is 87% of everything the four extra registries are worth, and the two
#: routing knobs together are 0.28%.
#:
#: Footprint moves 514x451 -> 517x496 and that is not a cost anyone pays:
#: ``deadman-3d_hires`` is out of contest scope (``AGENTS.md``), so ticks are the
#: only metric and ``max(w, h)**2 * ticks`` does not apply to it.
#:
#: :data:`ROM_BUFFER` stays empty. It is antagonistic to this by construction
#: (``ROM-RECIRCULATION.md`` §170): the buffer's whole value is draining a
#: pre-filled queue during the discard loop, and seeking is the deletion of that
#: loop.
SEEK_DRUM: set[str] = {"deadman-3d", "deadman-3d_hires"}

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
#:
#: **``deadman-3d_hires``' fold is not a re-pick of :data:`ROM_ROWS`' 88, it is a
#: different feasible region.** 88 does not build under the drum at all: a seek row
#: addresses its words as ``row * K + offset`` with ``K = 128``, so no row may hold
#: 128 words, and at 88 rows the first holds 152. The fold has to *deepen* until every
#: row fits — the opposite direction to the classic trade, where 88 was the shallowest
#: fold whose drum was no wider than the 494-column router wall and every row past it
#: was pure height.
#:
#: 110 is the shallowest that builds; 111 the shallowest that **runs**. 110 and
#: 121, 122, 123 build and then fail ``FastLittleman`` with "numeric literal does
#: not fit signed 64 bits" — the same reverse-reading hazard the parent's curve
#: records, landing in a different place because it is a property of *this*
#: program's packed words.
#:
#: The box does not move across the whole range (517 wide, the router wall's, and
#: ``mem_pad`` 15 throughout), so this is a pure tick pick. 21-round tour, shipped
#: registries otherwise:
#:
#: ::
#:
#:     rom_rows   box        tour ticks
#:     111        517x488    235,095,528
#:     115        517x491    234,590,527
#:     118        517x495    234,768,727
#:     **119**    517x496    **233,658,800**   <- the pin
#:     120        517x497    234,200,405
#:     124        517x502    235,121,255
#:     128        517x506    234,237,503
#:     130        517x507    234,888,187
#:
#: The whole span is 0.63%, and it is not monotone in either direction — the fold
#: sets how the words pack into rows and the ladder's depth at once, and neither
#: is smooth in the row count. 119 is the minimum of a flat, jagged curve, so
#: treat it as a pin to re-sweep rather than a crossing to reason from.
SEEK_TIER_LAYOUT: dict[tuple[str, str], dict[str, object]] = {
    ("deadman-3d", "men-v3"): {"rom_rows": 60},
    ("deadman-3d", "taped"): {"rom_rows": 84},
    ("deadman-3d_hires", "taped"): {"rom_rows": 119},
    # Without this key the men path fell through to ``ROM_ROWS`` 88 and died with
    # ``row 0 holds 158 words >= K=128`` — which reads exactly like the tier being
    # broken for hires, and is the likeliest reason nobody re-tried it for a month.
    # 119 is the floor that builds (88 and 100 fail the fold); 130 is +0.92%.
    ("deadman-3d_hires", "men-v3"): {"rom_rows": 119},
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
#: even in principle: its rank order is ``plan``'s length-descending default
#: rather than a tuned :data:`LANE_ORDER`, so the *ranks* the DP assigns to
#: differ too. Its
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
#:
#: **``JMPS`` is named in the hires map and is not part of the DP.** It was added
#: when hires joined :data:`SEEK_DRUM`: a seek build grows a 22nd lane and a map
#: that does not name every used opcode is a hard error, so without it
#: ``build_for(..., seek=True)`` does not build at all. It is inert for a classic
#: build — :func:`_relabel_slots` filters names the build does not use, which is
#: exactly what "one registered map has to serve ``seek=True`` and ``seek=False``
#: alike" is for — so the classic hires grid is byte-identical with it. The slot
#: is *not* re-derived: the twenty-one DP assignments are untouched and ``JMPS``
#: takes a free slot in the only gap rank preservation leaves it, between
#: ``JMPF``'s 24 and ``SND``'s 28. All three candidates (25, 26, 27) bit-reverse
#: to a two-digit opcode, so the drum pays the same 345 cells whichever is
#: picked, and the 21-round tour is **identical to the tick** at all three
#: (233,658,800) — the trie cannot tell them apart either, because they share a
#: descent. 25 is chosen for sitting next to the ``JMPF`` it is split from.
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
        "ADD": 8, "LDA": 9, "MUL": 10, "DIVI": 11, "LD": 12,
        # ``MODI`` 13 -> 14 and ``NEG`` 14 -> 15, and the pair is a **third**
        # objective this map has to solve: the corridor :data:`HIGH_COLLECTOR`
        # opens. Slot 15 was the only hole in the root's up half, which left the
        # node splitting ``{12..15}`` standing its ``x`` on the one row the
        # corridor needs blank; filling 15 puts every up-half node back on a lane
        # row. Rank order is untouched — ``MODI`` and ``NEG`` keep their places and
        # every lane's row, micro-program and drop column are where they were — and
        # the drum pays the same, because 14 and 15 bit-reverse to two-digit
        # opcodes exactly as 13 and 14 did.
        #
        # Chosen out of the 18 rank-preserving repairs within three slots that
        # clear the corridor: it is the only one that also leaves ``lane_x0`` at 12
        # under :data:`TIGHT_TRIE_COLS`, which :data:`SEEK_TIGHT_STRUCT_DROPS`
        # needs (see there). Worth +3,291 ticks on the 21-round tour on its own —
        # 0.002%, against the -5.34% it unblocks.
        "MODI": 14, "NEG": 15, "SUBI": 16, "ADDI": 17, "MULI": 18, "LDI": 20,
        "BRN": 21, "BRZ": 22, "JMPF": 24, "JMPS": 25, "SND": 28,
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
#:
#: Keyed by **slug**, and :data:`STORE_ANSWER_WEST` is keyed by ``(slug, tier)``
#: and wins, so both DOOM slugs are listed here and neither taped machine builds
#: a room: the pair survives only on the tiers whose collector cannot be widened
#: (``deadman-3d``'s canonical men-v3, whose collector is at the block's floor).
#: Which is why removing an entry here is the wrong way to delete a teleport —
#: it falls through to the long pipe. The right way is to give the store's own
#: collector the job, which is what that registry does.
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
#: ``deadman-3d_hires`` took four sweeps to get here and was three times
#: recorded as structurally impossible, so the history stays: every entry below
#: is a correct reading of a machine that no longer exists, and the last one is
#: the reading that was wrong about *why*.
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
#: **And that third reading is where it went wrong, so read points 1-3 again.**
#: All three describe *routing a pipe from the collector's south stub to the
#: response row*, and all three are true. None of them is about the collector:
#: they are about the **exit stub's direction**, which was south because the
#: only caller that had ever wanted one was below the room. hires is level with
#: it. A room level with its caller does not need a route at all — the answer
#: leaves through the **west wall**, on the interior row the caller is already
#: standing on, and the one cell beyond that wall *is* ``(CX + W + 2,
#: resp_row)``. Nothing is routed, so there is nothing for the CPU, the adapter
#: or ``_keepout``'s halo to enclose, and no ``r`` binding moves because the
#: response pipe is the same single cell teleport U used to hand over on.
#:
#: That is :func:`~..memory_taped.taped_store_block`'s ``answer_exit_west``, and
#: :func:`build` picks it on geometry — ``resp_row`` equal to the collector's
#: first interior row — rather than on a registry key, because it is the only
#: condition under which it is correct. Two things had to move to meet it, both
#: in :data:`TIER_LAYOUT`: ``store_offset`` dx to -20 (the west end of the roof
#: window, so the interior starts at block column 2 and the stub has its column)
#: and dy to -1 (so ``resp_row`` is the interior row and not the north wall).
#:
#: Result on the 21-round tour: three forwarders and a six-cell response become
#: **one forwarder and one cell**, teleports L and U are gone, and the machine
#: is 649x388 -> 643x387. Probes: ``scratch/deadman3d-opt/hires_answer.py`` and
#: ``hires_answer_pads.py``; arithmetic in ``METRICS.md`` H2.
STORE_ANSWER_WEST: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

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
#:
#: ``deadman-3d_hires`` keeps it. It was briefly traded away for
#: :data:`SQUASH_BAND` (`48c85ce`) on the belief that room H could not survive a
#: shorter room; that was wrong — room H is bottom-anchored and its height comes
#: out ``12 - k``, so the pair coexists for ``k <= 8``. The trade never needed
#: making, and it cost +1.288% until it was undone.
#:
#: It is worth far more than the -0.08% it went in at, which is the point of
#: keeping it here:
#:
#: It went in at 233,851,301 -> 233,658,800, **-0.08%** on the 21-round tour. Every
#: later landing — the 11-bank cut, the seek drum, ``lap_via_jump``, the staggered
#: band — made everything *else* faster, and removing it now costs **+1.534%**:
#: 189,164,256 -> 192,066,009. Nothing about the room pair changed. A fully
#: serialised ~70-seeks-a-frame path simply became a much larger share of a much
#: faster run, which is the same lesson as ``AGENTS.md`` §"the measurement is not
#: separable" and the sharpest example of it in the tree.
#:
#: **``deadman-3d_hires`` can have it back, and :data:`TAPED_BANK_LIFT` is why.**
#: The 0.6% was lost to a *space* argument, not a tick one — ``SQUASH_BAND`` k=12
#: leaves room H no band, and the refusal is ``seek teleport: no clear band below
#: the store for room H``. ``TAPED_BANK_LIFT`` frees five rows under the store
#: without touching the band, and the pair binds again: **-1.069%** on the
#: 21-round tour (185,004,449 -> 183,026,898 at 643x386, ``store->cpu`` 2,
#: ``passed``), i.e. -1.941% against ``70684d9`` once the lift is counted.
#:
#: **Taken, and it concedes the squash nothing.** The refusal was a *space*
#: argument and the space now exists, so the trade the note above anticipated
#: never has to be made: re-measured with ``squash_band`` left at its shipped 12,
#: the grid is **643x386 either way**, ``store->cpu`` is 2 either way, and both
#: gate ``passed``. The k<=8 rule recorded below was derived on the *unlifted*
#: geometry and no longer binds.
SEEK_TELEPORT: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs whose ``cpu->drum`` pipe leaves the CPU's east wall on
#: **room H's own row** rather than on the row the man sends from.
#:
#: The send cell and the attachment cell are two different things and only the
#: first one is about the man: ``s`` takes the nearest outgoing pipe wherever he
#: stands, so the pipe may leave the wall anywhere. On ``deadman-3d_hires`` it was
#: leaving at the send row and then **diving eight rows** down the one free column
#: east of the CPU to reach room H — eight cells the word crosses at one a tick
#: with the CPU already waiting at the far end of the round trip.
#: :func:`_seek_teleport` already raised H's north wall as far as it would go
#: (the seek adapter's room blocks the rows above 206); what was left was to drop
#: the *attachment* instead, and the dive becomes the two cells a pipe is required
#: to be and cannot shorten again. ``cpu->drum`` **67 -> 60**.
#:
#: **Every ``s`` in the CPU room re-ranks when this moves, and that is the risk
#: rather than the pipe.** The seek attach is one of the rivals for the store
#: request and the display send, and a wrong bind is silent. What makes it safe is
#: that :func:`check_bindings` already gates the whole CPU against ``touches``, so
#: the attach row is passed *out* of :func:`_seek_teleport` and into that gate
#: rather than being assumed to still equal ``cmd_y`` — the one line that would
#: otherwise let this pass a build and answer from the wrong pipe.
#:
#: Measured on the 21-round hi-res tour, same process, same moment, control
#: reproducing to the tick: **88,774,561 -> 88,754,090, -0.023%**, box unchanged
#: at 614x403.
#:
#: **The response is strongly asymmetric and that is the finding, not the
#: -0.023%.** The same leg was priced from the other side with
#: :data:`SEEK_DIVE_PAD`, which lengthens the dive and changes nothing else: the
#: first four cells beyond the shipped length are free to the tick, and the next
#: four cost **+0.082%** — ~18k ticks a cell. Taking seven cells *off* recovers
#: ~2.9k ticks a cell, a sixth of that. So this leg sits just inside a knee: the
#: CPU hides most of a jump's round trip behind the seek walk and the fetch
#: riser, and there are only a few cells of hidden slack left. Anyone tempted to
#: spend grid on shortening ``cpu->drum`` further should read that asymmetry as
#: the ceiling it is — the remaining 60 cells are two teleport rooms and three
#: minimum-length stubs, and the CPU **never blocks on the send at all** (0
#: ticks on ``cpu->room49`` over the whole tour; all of its waiting is on
#: ``store:collector->cpu`` 34.0M, ``rom->cpu`` 0.99M and ``input->cpu`` 0.21M).
SEEK_ATTACH_LOW: set[tuple[str, str]] = {("deadman-3d_hires", "taped")}

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
#:
#: ``deadman-3d_hires`` takes it: 234,324,256 -> 233,851,301 on the 21-round tour,
#: **-0.20%**. Worth rather more than on the parent in relative terms and for a
#: mechanical reason — under :data:`SEEK_SLAB_PITCH` 11 the slab band is narrower, so
#: ``e_s - 1 - base`` is a shorter U-turn, but hires takes many more of them.
SEEK_TAKEN_DROP_EAST: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
    # men-v3 wants it for the same reason taped did, and it was simply never
    # keyed here. Worth **-0.914%** on a 3-round tour, and additive with
    # :data:`TUCKED_DROPS` to the third decimal (-1.593 + -0.914 = -2.507 against
    # -2.507 measured) — not luck, but because they touch disjoint columns: a
    # *simple* lane's drop against a *seek jump*'s taken row, so neither can
    # absorb the other's win.
    #
    # Note where the win is **not**: the taken row lives at ``bottom + 1``, inside
    # no ``cpu:*`` region, so neither ``cpu:return:collector`` nor
    # ``cpu:return:riser`` can see it in a profile. It is still a return cost —
    # a taken jump's walk back to the fetch site, paid on the 4.1% of
    # instructions that are ``JMPF``.
    ("deadman-3d_hires", "men-v3"),
}


#: Per-``(slug, tier)`` opt-in for the **twin-station seek drum** — the notice
#: path rebuilt so it stops walking the room.  See :func:`seekrom._twin_top` for
#: the topology; this is why it exists and what it is worth.
#:
#: The drum is one man on a 421x123 boustrophedon ROM who notices a jump request
#: only at a row-transition gadget.  Granularity is a row and that is cheap; the
#: **walk to the station** is not.  Per taken seek, measured on
#: ``deadman-3d_hires``/men-v3 at ``b115339``:
#:
#: | leg | ticks | blocked |
#: |---|---|---|
#: | station + feeders | 372.8 | 96.5 |
#: | cascade collector row | 289.7 | 0 |
#: | seek riser | 123.0 | 0 |
#: | west + east cascade | 200.4 | 0 |
#: | west + east ladder | 93.1 | 0 |
#: | **total** | **1,079** | 96.5 |
#:
#: Essentially none of it blocked — it is pure travel — while the CPU sits
#: **927 t/seek blocked waiting for it**.  That is why all four CPU-side seek
#: levers measured 0.00%: the CPU's ~350 ticks of work sit inside a ~1,900-tick
#: window the drum sets.  The conversion is measured, not modelled: a temporary
#: ``SEEK_NOTICE_PAD`` of N idle cells between the drum reading the request and
#: emitting the sentinel is linear to four significant figures across N =
#: 50/100/200 and reproduces the baseline exactly at N = 0, at **5,812 ticks of
#: run per tick of notice path** (0.0066% each).
#:
#: Two of those legs are defects with per-cell evidence:
#:
#: * the **riser reads 2,744 on every one of its 123 rows** — every taken seek
#:   walks the full height unconditionally, because the cascade collects at the
#:   *bottom* while the station sits at the *top*: 123 t = **0.82%**;
#: * the **collector row reads 2,744 at column 1 but 1,819 at columns
#:   100..430** — 66.3% of seeks cross the full 431-cell row to reach the single
#:   station in the west corner: 289.7 t = **1.92%**.
#:
#: Both are the same root cause, and it is not the pipe: a **421x123 room with
#: exactly one station, in one corner, that both the arrival and the departure
#: path have to reach**.  The user's framing was a teleporting Send from the CPU,
#: and the arithmetic of it is right — but the request *value* already teleports.
#: The drum room has one incoming pipe, so ``q`` and ``r`` bind it from anywhere
#: in the room and the station is under **no** binding constraint at all; what
#: costs 1,079 ticks is the man walking to the one place the layout put a reader.
#: Nor does this want ``S``: broadcasting the request into two pipes would put
#: **two** values in the room, one gadget would fire on each, and the drum would
#: seek twice.  One pipe and two stations cannot double-fire — there is one man
#: and one value, and whichever station his cascade reaches consumes it.
#:
#: What is left after the rebuild is the **departure** crossing, and it is a
#: floor rather than an oversight: a packed row is enterable only from the end
#: it is packed from, so a target row of the far parity must be reached across
#: the room whatever the topology.  0.5 crossings a seek is the minimum, and the
#: one-station drum already pays exactly that on its feeder — which is why the
#: ~1.38% the feeder looks worth is not additive with the rest, and the
#: identified ~4.1% is really ~2.7% plus a small gain on the cascade.
#:
#: **Measured, 21-round tour, ``passed=True``/``fatal=None`` on both tiers:**
#:
#: | tier | grid | ticks | |
#: |---|---|---|---|
#: | men-v3 | 496x672 -> 496x674 | 86,981,643 -> **85,515,686** | **-1.685%** |
#: | taped | 625x396 -> 625x400 | 145,970,818 -> **143,888,528** | **-1.427%** |
#:
#: That is 252 ticks a seek at men-v3's 5,812, against ~470 on paper, and the
#: gap is paid in **width**: the east station is the only glyph run that reaches
#: past the wrap riser, so the room grows eleven columns, ``build``'s fold loop
#: buys them back in rows, and ``rom_rows`` goes 123 -> 126 (men-v3) / 127
#: (taped).  A deeper drum is a longer ladder *and* a longer cascade, and the
#: ROM room two rows taller moved men-v3's swept ``mem_pad`` from 15 to 2.
#: **The eleven columns are the thing to attack next**: ``STATION`` is 17 glyphs
#: because ``K = 128`` is built in digits, and a ```` `128` ```` literal would
#: make it 15 — worth checking against the backtick rule, since the drum's
#: vertical traffic (the east ladder's drop at ``L0+2``) crosses the east
#: station's column range on other rows.  Reclaiming column 0, which twin mode
#: leaves empty where the riser was, is a third.
#:
#: Neither tier needed a ``MEM_PAD_FOR`` re-sweep: taped bound at its pinned
#: floor of 1 and men-v3's own ``range(0, 40)`` search found 2.
SEEK_TWIN_STATION: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
    ("deadman-3d_hires", "taped"),
}

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
#:
#: ``deadman-3d_hires`` takes the same 13 and for the same second-order reason: on its
#: seek build the pad floors at 18 with the room on the IN lane's own ``r`` and at
#: **15** with it at the CPU's north-west corner, which is where the *classic* build
#: already binds. Worth 236,380,143 -> 234,324,256 on the 21-round tour, **-0.87%**,
#: all of it the three columns of memory band. Note this registry is **not**
#: seek-gated — ``build_for`` passes ``in_west`` unconditionally — so it moves a
#: classic hires build too; that is safe here because hires commits no grid (its
#: program is IWAD-derived, ``DEADMAN-3D.md``) and it now ships as a seek build.
#: **hires/taped is 11, not 13, because :data:`TIGHT_TRIE_COLS` moved the wall the
#: 13 was measured against.** This is a distance *west of* ``lane_x0``, and the
#: tighter trie takes ``lane_x0`` 14 -> 12, so the shipped 13 walks the room off
#: the CPU's north wall entirely (`in_west 13 puts the input pipe off the CPU north
#: wall`). 11 is the same room in the same place — ``CX + 1``, the westernmost
#: legal column, which is what the 13 was always naming — and ``mem_pad`` still
#: floors at 15, so nothing this registry exists to buy has changed.
#: **and 9, not 11, once :data:`FETCH_FOLD` takes ``lane_x0`` 12 -> 10.** Same
#: identity a second time: this is a distance west of ``lane_x0``, the room wants
#: to be at ``CX + 1``, and each column the trie gives back is a column this must
#: give back too or the room walks off the wall (`in_west 11 puts the input pipe
#: off the CPU north wall`). The room does not move; only the number does.
#: **men-v3 takes the same 9, and only becomes worth taking once
#: :func:`check_bindings` stops refusing ties.** With the I room on the IN lane's
#: own ``r`` the men-v3 pad floor is 3, and the thing holding it there is ``in``:
#: at pad 2, ``'r' at (22,154)`` sees ``mem_resp`` 21 and ``in`` 21, and ``in``'s
#: attach ``(18,137)`` reads before ``mem_resp``'s ``(43,154)`` — so that tie is
#: genuinely **lost** and moving the room is the only way out of it. Moved to
#: ``CX + 1`` the rival becomes ``rom`` instead, at another exact tie (30-30 on
#: ``'r' at (22,163)``) — but this one ``mem_resp`` *wins* on reading order, so it
#: only binds under the relaxed clause. The two changes are worth nothing apart
#: and **-0.864%** together (81,042,708 -> 80,342,861 at 21 rounds, ``passed``,
#: 496x674 unchanged), all of it the one column of memory band that 54.16% of
#: instructions walk twice.
#:
#: Any value 1..9 reaches pad 2; 9 is ``CX + 1``, the westernmost legal column,
#: which leaves ``in`` 39 cells away instead of 31 and so the largest margin
#: before the rival changes back. 10 walks the room off the north wall.
INPUT_NORTH_WEST: dict[tuple[str, str], int] = {
    ("deadman-3d", "taped"): 13,
    ("deadman-3d_hires", "taped"): 9,
    ("deadman-3d_hires", "men-v3"): 9,
}

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
#:
#: ``deadman-3d_hires`` has no :data:`MEM_PAD` entry at all, so both its builds fall
#: through to ``build``'s own ``range(0, 40)`` search. That search is *correct* here —
#: hires' box is the router wall's at every pad, so every binding pad ties on
#: footprint and the first, i.e. smallest, wins — and 15 is what it finds. This entry
#: pins the answer rather than changing it: it makes a seek build deterministic and
#: skips up to fifteen doomed ``_assemble`` passes, which on a P=9,225 program is most
#: of the build. The floor is a joint property of :data:`SEEK_SLAB_PITCH` (35 at the
#: default pitch, 18 at 11) and :data:`INPUT_NORTH_WEST` (18 -> 15); re-sweep it if
#: either moves.
#:
#: **It moved: :data:`PACKED_SLAB_BAND` takes hires/taped from 15 to 4.** The floor
#: is the CPU's *east wall* against ``mem_resp``, and packing the staircase walks
#: that wall eight columns west — so the rival recedes and the band may follow it in.
#: The pin is not a tuning value, it is the floor, and it was 3 rows of the sweep
#: away from binding at all; 21-round tour, everything else at the shipped values:
#:
#: | pad | box | ticks |
#: |---|---|---|
#: | 0..3 | — | ``'r' at (24, 138) must bind 'mem_resp'`` |
#: | **4** | **626x386** | **175,267,384** |
#: | 5 | 626x386 | 176,134,710 |
#: | 6 | 626x386 | 177,002,036 |
#: | 8 | 628x386 | 178,777,989 |
#: | 15 (the old pin) | 635x386 | 187,002,731 |
#: | 21 | 640x386 | 191,350,051 |
#:
#: Monotone above the floor at ~0.5% a column — every column of pad is walked twice
#: by every memory instruction — so the rule here is simply "take the floor", and
#: the only thing worth re-deriving when the CPU's width moves is where the floor is.
#:
#: **men-v3's floor moved the same way and is deliberately not pinned here.** It
#: falls 9 -> 3 under the same lever (0..2 die on ``'r' at (22, 152) must bind
#: 'mem_resp'``, and 3..12 are monotone at ~0.45% a column), and the search finds 3
#: on its own. Leaving it searched costs three doomed passes and buys the tier the
#: ability to re-floor itself when the next lever moves its east wall, which is
#: exactly what this note is a record of not happening automatically for taped.
MEM_PAD_FOR: dict[tuple[str, str], int] = {
    ("deadman-3d", "taped"): 16,
    # 1, and the history of this one number is the argument for never carrying a
    # pad across a geometry change. It was 15 when the slab band was loose; the
    # band packed and the floor fell to 4; then `SEEK_TIGHT_STRUCT_DROPS` walked
    # the structured drops west and the east wall with them, and the floor fell
    # again. Re-swept on the merged geometry: 0 refuses (`'r' at (22,..)`), **1 is
    # the floor**, and it is monotone above — 2 is +0.322%, 4 is +1.379%, 8 is
    # +2.966%. Carrying the 4 would have cost 1.4% invisibly.
    #
    # The pad is a *consequence* of where the CPU's east wall lands against
    # `mem_resp`, so it moves whenever anything moves that wall. Re-sweep it after
    # any band, drop or trie-width change rather than trusting this literal.
    #
    # **And it moved: 1 -> 2 under :data:`FETCH_FOLD`**, which is the trie-width
    # change that note was written against. The fold walks the CPU's rigid body
    # two columns west while the pipes it is measured against stay put, so 0 and 1
    # now lose the memory response — 0 to the `in` room (`('in', 21),
    # ('mem_resp', 23)`) and 1 to a **tie** with it at 22, which
    # :func:`check_bindings` fails as hard as a loss. Neither is recoverable by
    # moving the `in` room: :data:`INPUT_NORTH_WEST` already has it at ``CX + 1``,
    # the westernmost legal column, so there is nowhere further to put it. 2 is
    # the floor, and it needed :data:`ROM_TOUCH_DROP` 16 to reach — see there.
    #
    # **"a tie, which check_bindings fails as hard as a loss" is no longer true** —
    # see :func:`check_bindings` — so this was re-derived rather than trusted, and
    # **2 survives, on the other half of the clause.** Pad 1 loses `'r' (21,148)`
    # and pad 0 loses `'r' (20,148)`, both to `in`, at every :data:`ROM_TOUCH_DROP`
    # in 16/18/20 — so unlike men-v3 the ROM pipe is not the rival here and sliding
    # it does nothing. The `in` room is the rival and :data:`INPUT_NORTH_WEST`
    # already has it at ``CX + 1``, the westernmost legal column. The conclusion
    # was right; the reason recorded for it was not.
    ("deadman-3d_hires", "taped"): -1,
    # 1, and the pad stopped being a floor at all: with ties decidable, §7.1 in Z3
    # says 0 is satisfiable too and only `rom` or `mem_resp` can unlock it. This is
    # what :data:`ROM_TOUCH_DROP` 11 buys — 21 rounds, `passed=True`, 496x674,
    # 80,342,861 -> **79,341,770 (-1.246%)**. Pinned rather than swept: `build_for`
    # would find it anyway (0 refuses, 1 binds, and every binding pad ties on
    # footprint here so the first wins), but the pin skips a doomed `_assemble`
    # pass, which on a P=9,237 program is most of the build.
    ("deadman-3d_hires", "men-v3"): 0,
}
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
#: ``(-20, -1)`` pulls the block into the window — the window's west end, and
#: no longer an arbitrary point in it: :data:`STORE_ANSWER_WEST` needs the same
#: knob and only that one value satisfies both. **-0.469% net** on the 21-round
#: hi-res tour, measured when the offset was still free at ``(-14, 0)``
#: (1,090,194,166 -> 1,085,082,598 over frames 1..20), which is the roof's own
#: -0.550% less the +0.081% the offset costs on its own. Not the -1.478%
#: ``deadman-3d`` got, and the reason is the same one that shrinks every store
#: lever on this machine: at 128x96 the frame is four times the pixels but the
#: store is the same store, so a request leg is a smaller share of the frame.
STORE_REQUEST_REACH: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs whose grown gate takes the request on its **first**
#: interior row rather than its second, which is what makes the drop below the
#: adapter's floor two cells instead of three.
#:
#: :data:`STORE_REQUEST_REACH` pulls the gate's roof to one row under the
#: adapter's floor and then attaches the block's own ``>`` at ``roof + 2``. The
#: ``+ 2`` was never a constraint, only the row the ungrown block's entry
#: happened to sit on: the stub stands *outside* the west wall, so it may take
#: any interior row the wall has, and the gate's ``U`` receives from any incoming
#: pipe with **no distance term** (``SPEC.md`` §Nearest) — the same permission
#: slip that let the entry move 33 rows north of the man in the first place.
#:
#: What is left is the language's own floor: ``v`` under the adapter's south
#: wall, then the block's ``>`` turning into the gate's west wall. Two cells is
#: the minimum a pipe may be, so this leg cannot be shortened again.
#:
#: One cell off a leg every store access walks while the CPU is stopped on the
#: answer. It is **not** the same row as :data:`ADAPTER_FORM`'s fork: that one
#: raises ``floor_y`` and the gate roof under it *together*, so the drop
#: translates a row north at unchanged length. Two independent rows.
#:
#: **Measured on the current tree**, 21-round hi-res tour, four endpoints in one
#: process: 106,770,604 shipped, 106,465,874 with the tuck alone (**-0.2854%**),
#: 106,446,235 with :data:`ADAPTER_FORM`'s fork alone (-0.3038%), and
#: 106,141,505 with both (-0.5892%). -304,730 and -324,369 sum to -629,099,
#: which is the pair's measured total **to the tick** — two pure length terms on
#: the same serial leg, neither shadowing the other. (An earlier reading of
#: -0.233% was taken against a tree with a different ``store_offset``; the
#: absolute saving is what travels, not the percentage.)
STORE_REQUEST_TUCK: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs built with an adapter other than the shipped 12x4 one.
#:
#: Keyed rather than global because ``deadman-3d``'s artifacts are byte-pinned
#: and :mod:`~.ram_machine`/:mod:`~.ram_machine2` read :data:`ADAPTER_W` and
#: :data:`ADAPTER_H` as module constants. See :data:`_ADAPTER_FORK` for what the
#: ``fork`` shape is and why it is two ticks shorter on a read.
#:
#: **Measured, 21-round hi-res tour, same process, same moment:** 106,770,604
#: shipped against 106,446,235 forked — **-324,369 ticks, -0.3038%** —
#: ``passed=True``, ``fatal=None``, box 620x403 either way and every
#: ``route_lengths`` entry identical (``cpu->adapter`` 2, ``adapter->store`` 2,
#: ``store->cpu`` 2). The saving is **one** tick of mean read latency, not the
#: two the forward leg loses: 324,369 against the machine's measured 324,588
#: ticks of run per tick of read latency is that constant to four digits. The
#: address arrives two ticks early but the gate downstream can only take it one
#: tick early, so the second tick is absorbed. Nothing else in the machine moves,
#: which is what makes the attribution clean.
ADAPTER_FORM: dict[tuple[str, str], str] = {
    ("deadman-3d_hires", "taped"): "v4",
}

#: ``(slug, tier)`` pairs on the **v4** store wire: one word per request instead
#: of two (``2*addr - op``), from the adapter to each bank's own feed forwarder.
#:
#: It is a version number rather than a knob because what changes is the
#: *format*, not a tuning value: the adapter, every gate and every feed room are
#: different rooms, and a v3 adapter in front of a v4 chain builds fine and
#: answers from the wrong bank. The two halves are therefore checked against
#: each other at build time, and this registry moves with
#: :data:`ADAPTER_FORM`'s ``v4`` entry.
#:
#: The banks are **not** on it. The wire is unpacked in the feed forwarder
#: (``memory_taped.feed_unpack``), which is one ``/`` — so every ring worker in
#: :mod:`~..memory_tape` keeps the protocol it was verified on and does not move
#: by one cell, and ``matmul``/``sudoku``, which share those workers, cannot be
#: touched by this at all.
#:
#: What it attacks is the read's **address-independent floor** — the per-stage
#: word handling that no routing lever reaches, because it is paid once per word
#: per room whatever the address is.
#:
#: **Measured, 21-round hi-res tour, same process, same moment:** 105,152,308
#: against 101,523,077 — **-3,629,231 ticks, -3.451%** — ``passed=True``,
#: ``fatal=None``, box 620x403 -> 614x403 and every ``route_lengths`` entry
#: identical (``cpu->adapter`` 2, ``adapter->store`` 1, ``store->cpu`` 2).
#:
#: Where it comes from, per-stage on the 3-round tour (exact ``heat``-minus-
#: ``wait`` per room, and exact per-pipe word counts):
#:
#: =========  ==============  ======================================
#: stage      words per read  work
#: =========  ==============  ======================================
#: adapter    2 -> 1          the whole request is in the pipe on
#:                            tick 5, and there is one word of it
#: gate hop   2 -> 1          **-6.63 ticks**, x 3.01 hops per access
#: feed room  2 -> 1          +9.26 ticks of *service*, all of it
#:                            absorbed: the room is 94-99% blocked
#: bank       2 -> 2          untouched, to the cell
#: =========  ==============  ======================================
#:
#: Read latency 151.89 -> 141.15 mean, floor 73 -> 69, over the identical
#: 28,227 reads. 10.74 ticks x 28,227 = 303,004 against 303,004 ticks of run
#: measured, so the conversion is 1:1 and nothing else in the machine moved.
#:
#: **The floor moved by 4 and the mean by 10.7, and the difference is the
#: point**: the floor is one gate hop, and the mean is 3.01 of them.
#: **v5 takes the unpack out of the forwarder and puts it in the bank**, which
#: is where it is free. The room stays — it is the corridor, and deleting it
#: restores the 45-cell climb ``feed_teleport`` was built to kill — but its body
#: becomes a bare ``R``/``s`` relay (:func:`~..memory_taped.feed_relay`) and each
#: ring worker takes the packed word apart itself
#: (``memory_tape.TAPE_WORKER_PROTOCOLS``).
#:
#: The obstacle this had to clear was that the worker's parked constant looked
#: like it had to become ``2`` for a ``/``, evicting ``n`` and forcing a walked
#: literal that only the smallest banks could afford. **It dissolves: the unpack
#: never needs A at all.** ``b`` parks the word in BP, ``]`` shifts BP right —
#: which is P1's count — and ``x`` branches on BP's low bit, which is the op. The
#: parked constant is still one number (``2n + 1``), A is still free for the
#: signed remaining distance, and MAIN goes from ``r X r b - N M`` plus a stall
#: to ``r b ] - M``.
#:
#: What that costs is the arms: recovering ``n - 1 - addr`` from the packed
#: remainder is ``N b ] m`` rather than ``b m``, so it moves to **after** the
#: answer's ``S`` — where :data:`TAPED_TIGHT_RING` measured the rest of the lap
#: to be worth exactly 0.000%. The write's ``]`` floors, so P1 moves one word too
#: few and the write arm pays an extra ``r s``; reads are exact and a bank's
#: local addresses start at 1, so the floor never underflows.
#:
#: **Measured, 21-round hi-res tour, same process, control first and last:**
#: 90,852,830 against 94,781,294 — **-3,928,464 ticks, -4.145%** — ``passed=True
#: fatal=None``, box 614x403 unchanged and every ``route_lengths`` entry
#: unchanged (``cpu->adapter`` 2, ``adapter->store`` 1, ``store->cpu`` 2,
#: ``rom->cpu`` 60, ``cpu->drum`` 67). Mean read latency 123.266 -> 111.163 and
#: the floor 63 -> 50, over the identical 324,600 blocked answer-pipe runs:
#: **12.103 ticks x 324,588 = 3,928,489 against 3,928,464 measured**, so the
#: conversion is 1:1 and nothing outside the read's critical path moved.
#:
#: Three measurements, in the order they were taken:
#:
#: ==================================  ===========  ==========  =========
#: .                                   ticks        delta       latency
#: ==================================  ===========  ==========  =========
#: v4 (control)                        94,781,294   .           123.266
#: + the wire                          92,929,495   -1.954%      117.561
#: + batch 2's dispatch                92,761,125   -0.177%      117.042
#: + batch 1's body (``_worker_v2_v4``) 90,852,830  -2.057%      111.163
#: ==================================  ===========  ==========  =========
#:
#: The last one is the largest and the reason is the chain order: it is
#: **hot-first**, and its first banks are the small ones (7, 9, 6 slots), so the
#: batch-1 worker answers roughly **three reads in four**. Its 8 ticks came back
#: as 5.88 of mean latency, which prices the share at 74%.
#:
#: Where the first 5.7 ticks come from, counted before they were measured:
#:
#: * **forwarder** — ``R`` to the last ``s`` was six cells (``/ W s W + s``) and
#:   is now one. Two of those were the gap between the two sends, which the bank
#:   *stalled* in: ``r X`` then ``r`` wanted the address one tick before it
#:   arrived. Worth 2.
#: * **MAIN** — ``r X [stall] r b - N M`` (8 ticks) becomes ``r b ] - M`` (5).
#:   Worth 3.
#: * **the descent** — the v3 arms merged one column *west* of P1's own entry and
#:   walked back east; with one arm there is nothing to merge, so batch 1 drops
#:   the two-cell dogleg and batch 2 runs straight east along MAIN's own row
#:   instead of doubling back to the merge column. Worth 2.
#: * **the dispatch** — ``W X b m`` becomes ``W M b x``, and the ``b ] m`` that
#:   replaces ``b m`` sits behind the ``S``. Even.
#:
#: Batch 1's body is laid out in ``memory_tape._worker_v2_v4``: with one arm
#: instead of two and the unpack in the backpack, P1 moves as far **west** as
#: its own ``s`` binding allows and the dispatch stands on P1's exit row, so
#: ``r`` -> ``S`` goes **27 + 8a -> 18 + 8a**, which is that leg's Manhattan
#: floor. Everything it deletes was a distance the man walked in full.
#:
#: The **0.52 ticks (-0.177%)** in the middle are batch 2's dispatch standing *on* P1's
#: own exit cell. The v3 body dropped three rows below it because its READ arm
#: wanted the row under the ring's own bottom row; with the parked constant
#: chosen so READ turns the other way (``2n`` rather than ``2n + 1`` — the
#: parity of ``w - c`` is what ``x`` reads, so the constant picks the arm) the
#: two arms straddle the exit instead and the descent disappears. Batch 1 has no
#: equivalent: its room is 22 columns and the fill loop stands in the only rows
#: the arms could take.
#:
#: ``matmul``, ``sudoku`` and the byte-pinned ``deadman-3d`` share these workers
#: and are untouched: the v4 body is selected by this registry, not edited into
#: the shared one. Verified by hashing built grids for all three (men-v3, taped
#: and taped+seek), ``snake-ring``, ``brackets`` and the ``tape_block``/worker
#: variants against a checkout of HEAD — every one identical.
TAPED_PROTOCOL: dict[tuple[str, str], str] = {
    ("deadman-3d_hires", "taped"): "v5",
}

#: ``(slug, tier)`` pairs whose **men-v3** STORE is entered on its router strip's
#: south-west **corner**, so the request is the straight leg between the adapter's
#: east wall and the block — instead of climbing over the block's roof.
#:
#: This is :data:`STORE_ANSWER_WEST`'s discovery applied to the other leg, and the
#: same sentence covers it: *a block level with its caller needs no route at all.*
#: The men-v3 block named exactly one request touch point, the two-cell stub into
#: the router strip's **north** wall, whose far end stands eight rows above the
#: adapter's outlet — so the leg left the adapter east, turned north up the free
#: column west of the block, climbed those eight rows, ran east along the corridor
#: above it and dropped back down. Four legs and 14 cells (16 as the engine counts
#: them, stub included) to cross a four-cell gap.
#:
#: The level-ness was never missing. ``TIER_LAYOUT``'s ``store_offset`` dy of 10
#: already lands the strip's **south wall on the adapter's own output row** — the
#: request wall and the request row are the same row and have been all along. What
#: was missing was a touch point on that wall, which is
#: ``memory_men_grid.build_grid(..., request_west=True)``: no stub, and ``in_cell``
#: names the strip's south-west corner instead of the roof.
#:
#: Three things make it legal, and all three are load-bearing:
#:
#: 1. **A plain room may be attached at a corner** (``ARCH.md`` §7.4b — the input
#:    pipe's backward cell is I's top-left ``+``). Only *displays* forbid it, which
#:    is what the ``SPEC.md`` line about corner attachment being a load error is
#:    scoped to. The men strip is a plain room.
#: 2. **The leg terminates on the corner rather than running past it.**
#:    ``ARCH.md`` §7.1's converse hazard — a leg *alongside* a corner read as a
#:    second pipe, silently — is what :func:`_check_pipe_count` exists for, and it
#:    runs on every build. ``draw_pipe`` puts an arrowhead on the last cell, so
#:    ``>`` at the corner's west neighbour is the whole attachment and the block's
#:    own south wall east of the corner is not continuous with it.
#: 3. **The strip owns exactly one incoming pipe**, so *where* that pipe attaches
#:    rebinds nothing: "nearest" only picks which pipe (``SPEC.md`` §Nearest) and
#:    there is still only one to pick. The strip's outgoing column feeds are
#:    unaffected — a send needs a pipe flowing *out*, and this one flows in.
#:
#: ``adapter->store`` **14 -> 4** and the drawn polyline goes from four straight
#: legs (east, north, east, south) to **one**. As the engine sees it the request is
#: **16 cells down to 4**, because the roof stub merged into the same pipe;
#: ``store->cpu`` is untouched at 6, and so is every binding in the machine — 1914
#: pipes and 76 rooms either way, and of 20,946 pipe glyphs exactly the eight that
#: use this pipe change, all of them to the *same* pipe in its new shape (verified
#: against ``littleman``'s own ``route`` oracle, not just :func:`check_bindings`).
#:
#: **-2.937%** on the 21-round hi-res men-v3 tour (138,204,104 -> 134,144,756 at
#: 595x630, ``passed``, and the grid does not move a column). Twelve cells off a
#: leg every one of ~87k store accesses a frame walks in full, which is the shape
#: of the number: the same arithmetic as :data:`TAPED_BANK_LIFT`'s riser, on the
#: request side, and much bigger than the taped tier's own request collapse
#: because men-v3 has no gate rooms to have shortened it already.
#:
#: Keyed by tier because only ``men-v3`` has a router strip, and by slug so every
#: other men-v3 grid keeps its roof stub and its byte-identical block. Notably
#: **absent: ``("deadman-3d", "men-v3")``** — that pair is the canonical
#: hash-pinned grid and is deliberately not in :data:`TIER_LAYOUT` either, so its
#: strip is nowhere near level with its adapter and the build would refuse.
STORE_REQUEST_WEST: set[tuple[str, str]] = {
    ("deadman-3d_hires", "men-v3"),
}

#: How many rows to lift the men-v3 block's **answer riser**, per ``(slug, tier)``.
#: Absent (0) keeps the shipped block byte-for-byte.
#:
#: This is a pipe-length lever on the *read latency*, and it is worth stating why
#: that is the only kind that matters here. Measured on ``deadman-3d_hires``
#: men-v3, 21 rounds, with ``OpcodeTags(hist_pipe=store:collector->cpu)``: the
#: CPU's blocked run on the answer pipe is **49 ticks on all 324,600 reads** — one
#: bucket, min = max = mean. There is no queueing and no phase jitter in this
#: store, so every tick of the path is paid by every read, and the exchange rate
#: is exact: **1 tick of latency = 324,600 ticks of run = 0.421%**.
#:
#: The read crosses nine rooms on ten pipes. Nine of those pipes are two cells —
#: the engine's minimum, "a pipe is two cells or it is not a pipe". The tenth, the
#: riser's exit stub, was **six**, and not for any reason on the answer path:
#: :func:`memory_men_grid.build_grid` puts the strips at ``router_y = 6`` to leave
#: five rows above them for the standalone form's ``I`` room, and the block form —
#: which never draws that room — was walking its answer through the hole. Lifting
#: the riser's north wall into it takes those cells back one for one.
#:
#: Measured, 21-round tour, ``store="men-v3"``, ``frame_tiles=(2, 2)``,
#: ``passed=True``/``fatal=None``, 496x674 **unchanged** (the lift moves nothing
#: but the riser's own wall, inside rows the block already owned):
#:
#: | lift | read latency | ticks | Δ |
#: |---|---|---|---|
#: | 0 (was) | 49 | 77,067,979 | — |
#: | 3 | 46 | 76,094,293 | -1.263% |
#: | **4 (shipped)** | **45** | **75,770,083** | **-1.684%** |
#:
#: The two rows are the same measurement twice, which is the point: the saving is
#: ``lift * reads`` and nothing else. Predicted 4 x 324,600 = 1,298,400 ticks
#: against 1,297,896 measured — 0.04% out, and the residue is the last frame's
#: own boundary. 4 is the bound; see :data:`memory_men_grid.MAX_RISER_LIFT` for
#: why 5 measures identical and 6 refuses.
#:
#: **Absent: ``("deadman-3d", "men-v3")``** — the canonical hash-pinned grid, for
#: the same reason it is absent from :data:`STORE_REQUEST_WEST`.
STORE_RISER_LIFT: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "men-v3"): 4,
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
#: **hires was measured here once, declined, and the decline is now void.** At
#: -0.020% it was the weakest lever on the pre-``c51a748`` machine, and the
#: reason given was correct for that machine: hires' bank order put the bank
#: taking 90.79% of reads at chain position 0, so the links carried ~4% of
#: accesses instead of ``deadman-3d``'s 80%, and shortening them bought nothing.
#:
#: The 11-bank cut removed exactly that condition. Traffic is spread across
#: eleven banks now, most of them behind several links, so the links are back on
#: the critical path of most accesses — and the same registry re-measures at
#: **-2.678%**, a 134-fold swing on an unchanged builder.
#:
#: Two levers were re-litigated against the new store at the same time and the
#: other two stay declined, which is what makes this one a finding rather than a
#: reflex: ``lap_via_jump`` is still a wash (+0.036%, against -4.47% on the 64x48
#: machine — it has never paid here), and :data:`STORE_REQUEST_TELEPORT` still
#: cannot be taken at all, for a structural reason rather than a measured one —
#: it and :data:`STORE_REQUEST_REACH` are two answers to one question and
#: ``build`` refuses the pair outright.
TAPED_CHAIN_REACH: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    ("deadman-3d_hires", "taped"),
}

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

#: Whether the taped STORE's feed climb and its chain link share one column
#: (``memory_taped.taped_store_block``'s ``feed_share_riser``). Absent pairs keep
#: the two-column corridor, so their grids stay byte-identical.
#:
#: Every corridor between a gate and the next carries two vertical pipe runs: the
#: feed's climb into the forwarder room and the chain link's climb from the
#: downstream arm back to the spine row. They occupy **disjoint rows** — the feed
#: runs from the gate's local row (2) up to the forwarder's floor, the link from
#: the downstream row (6) up to the spine row (4) — and the only reason they were
#: in different columns is that the feed took the forwarder's *second* interior
#: column, the first having been the widest gate's own east wall back when
#: ``lead`` was 0. It is 1 now, so the first column is free, and putting both
#: climbs in it hands the next column back to :data:`TAPED_CHAIN_REACH`.
#:
#: The block's width, pitch, bank columns and every room but the grown gates are
#: untouched; what changes is that **each feed pipe and each chain link is one
#: cell shorter**, which is transit on the read's critical path rather than walk
#: inside a room (a forwarder is 89-99% blocked and its service time is provably
#: free — see :func:`~.memory_taped.feed_unpack`).
#:
#: **-0.841%** on the 21-round hi-res tour, same process, same moment:
#: 101,523,077 -> 100,669,456, ``passed=True``, ``fatal=None``, box 614x403
#: either way and every ``route_lengths`` entry identical. That is 853,621 ticks
#: over 324,588 ticks per tick of mean read latency = **2.63 ticks a read**, for
#: one cell of feed pipe plus one cell of chain link on each of the links a read
#: actually crosses — so the accesses-weighted chain depth is ~1.6, which is what
#: :data:`TAPED_BANK_ORDER`'s hot-first cut is for.
#:
#: It is the clean counterpart to the forwarder's own null result: **pipe cells
#: are on the wire and room walks are not.** SPEC's tick order shifts every pipe
#: value one cell before any man executes, so a cell of pipe is a tick of latency
#: the CPU is stopped for, while a forwarder's walk is spent in the gap before
#: the next request. Both facts are now measured on this same store.
TAPED_FEED_SHARE_RISER: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
}

#: How many rows the taped STORE's bank row is raised toward its answer collector
#: (``memory_taped.taped_store_block``'s ``bank_lift``). Absent pairs keep the
#: shipped bank row, so their grids stay byte-identical.
#:
#: The riser is what is between them. Every bank's answer climbs from the tape's
#: own ``^`` stub to the collector's south wall, seven cells on the shipped grid,
#: and the CPU blocks on every read — the profile has it **~48% blocked on
#: ``store:collector->cpu``** — so those cells are on the critical path of all
#: ~87k reads a frame. Five is the whole riser bar the tape's own two-cell stub,
#: which is the ceiling: past that the stub itself would have to overlap the
#: collector's floor.
#:
#: **-0.882%** on the 21-round hi-res tour (186,649,885 -> 185,004,449), and the
#: block loses five rows with it (60 -> 55), which takes the machine 643x387 ->
#: 643x386. Dead linear in the lift at **-325,873 ticks a row** (1: -0.183%, 2:
#: -0.358%, 3: -0.532%, 4: -0.707%, 5: -0.882%), which is the signature of a leg
#: every access walks in full and nothing else moving; the 3-round tour gives the
#: same slope to three digits, so this one is safe to triage cheaply.
#:
#: Only the first of the five rows shortens the grid, because the machine's
#: height is the stream stack's below 386 and the store's only above it.
#:
#: **The five rows are worth more than the five cells.** They fall between the
#: store and the seek band, which is the space :data:`SEEK_TELEPORT`'s room H was
#: refused for — ``revalidate.py`` goes from ``BUILD FAILED`` to ``<<< NOW A WIN``
#: on that lever the moment this lands, a further **-1.069%**. See
#: :data:`SEEK_TELEPORT`; it is not taken here.
#:
#: Not to be confused with :data:`TIER_LAYOUT`'s ``store_offset`` dy, which moves
#: the block **and** its collector and therefore breaks the geometric test
#: ``build`` selects ``answer_exit_west`` on: ``store_offset=(-20, -6)`` builds a
#: correct grid one row shorter and silently takes ``store->cpu`` from 2 back to
#: 6. This knob moves the banks *inside* the block and leaves ``COLLECTOR_ROW``
#: exactly where it was, which is why it composes.
TAPED_BANK_LIFT: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 5,
}

#: How many columns the taped STORE's banks are tucked west of where the pitch
#: would put them, so the feed forwarder's east wall stands inside the bank's own
#: west margin and the ``reqK->bankK`` stub shortens by that much
#: (``memory_taped.taped_store_block``'s ``feed_tuck``). **Empty, and it is a
#: measured impossibility rather than an unswept knob.**
#:
#: The prize would have been real: four cells off the ``reqK->bankK`` arm **every**
#: hires access walks (the same leg :data:`TAPED_FEED_TELEPORT` bought -0.286% on),
#: and four columns of pitch off each of eleven banks — the block's own width
#: 580 -> 536. Whether the *machine* narrows with it is unmeasured, because the
#: block does not build: the east edge at ``x = 642`` is the drum return wrapping
#: the store rather than the store itself.
#:
#: (An earlier draft of this note said ~91% of hires accesses walk the **bank 0**
#: arm. That is wrong — the head arm carries 35.3%, the first three 74% — but it
#: understated rather than overstated the prize, because every access walks *some*
#: bank's arm and they are all six cells.)
#:
#: **The four cells were taken anyway, off the other end of the same stub**:
#: :data:`TAPED_BANK_WEST_GROW` grows the *bank's* west wall west to meet the feed
#: room instead of carrying the feed room east into the bank, and measured
#: **-1.245%**. What follows is still the reason this knob in particular cannot be
#: the way to do it.
#:
#: What forbids it is the tape block's own art. Only its **first** column is
#: empty; ``lm1.machine.tape_block`` stamps the ring's relay room at ``x = 1``,
#: six columns wide and five rows tall (``+----+`` over ``|>@rv|``), and the feed
#: room has to span the corridor from the bank's stub row down to the gate strip,
#: which contains those five rows at every bank size. So the forwarder's east
#: wall — a solid ``|`` — crosses the relay room at any tuck at all. On hires it
#: is the same cell every time, bank 0's relay roof at block ``(35, 38)`` (grid
#: ``(96, 172)``): ``taped block collision at (35, 38): '+' vs '|'`` at
#: ``feed_tuck=1``, then ``'-' vs '|'`` at 2, 3 and 4 as the wall walks along that
#: roof. The tuck cannot step over the room, because it is contiguous from
#: ``x = 1`` to ``x = 6`` and only ``x = 0`` is clear — which the shipped
#: forwarder's east wall already stands in.
#:
#: Raising the forwarder's floor above the relay instead is arithmetic, not
#: judgement: the relay tops out 13 rows above the gate strip, so the climb pipe
#: the room replaces grows from 2 cells to 15 to buy back 4. Moving the relay is
#: ``tape_block``'s business and it is wired to its ring — ``adj`` is the relay's
#: own wall column and both ring legs attach to it, so the fold and the ring's
#: capacity move with it.
TAPED_FEED_TUCK: dict[tuple[str, str], int] = {}

#: How many columns each taped bank's **worker room grows west**, carrying the
#: block's two-cell request stub with it (``lm1.machine.tape_block``'s
#: ``west_grow``). This is :data:`TAPED_FEED_TUCK`'s prize, taken from the other
#: end of the same stub — and it builds, because the obstacle above is a **row**
#: collision, not a column one.
#:
#: The feed forwarder cannot move east into the bank's margin because it spans the
#: whole corridor vertically and therefore meets the relay at block rows 29..33.
#: The worker room is rows 7..26. Block columns 1..6 are empty over exactly those
#: rows — the only thing in them is the request stub itself, on row 10 — so the
#: wall walks west across nothing and the stub lands on columns 1 and 2, one clear
#: of the feed room's own east wall. Four is the ceiling and it is exact: at 5 the
#: stub's first cell would be that wall (:data:`~lm1.machine._TAPE_WEST_GROW_MAX`).
#:
#: What it deletes is transit. The ``reqK->bankK`` leg is six cells, every one of
#: them a tick the CPU is stopped for, and every access walks one; at 4 the leg is
#: the block's own stub and ``taped_store_block`` draws no pipe at all.
#:
#: Bindings cannot move and the argument is one line: growing the wall only makes
#: the request pipe **further** from every ``r`` in the worker, so the receives
#: that must take the request keep it — the tightest is the WRITE value's, 10 -> 14
#: against 19 to the ring return — and the ring-facing ones are only more strongly
#: bound. Both outgoing pipes are on the other two walls. The 901-address readback
#: (``tests/test_memory_taped.py``) is what actually checks this, and does.
#:
#: **Measured, 21-round hi-res tour, same process, same moment:** 96,280,186 ->
#: 95,081,140, **-1.245%**, ``passed=True``, ``fatal=None``, box 614x403 unchanged
#: and every ``route_lengths`` entry identical. Dead linear in the grow — 2 gives
#: -0.624%, half of 4's to three digits — which is the signature of a leg every
#: access walks in full with nothing else moving. It is the same **0.313% per cell**
#: the forwarder's own forward leg prices at (a nop between its ``W`` and its first
#: ``s``: 4 give +1.250%, 8 give +2.504%), so a pipe cell here and a walked cell
#: there cost exactly the same thing — a tick of read latency, and one of those is
#: 324,588 ticks of run.
#:
#: The block's own width does not move: it is set by the east edge, and these four
#: columns come out of the west margin, which was empty.
TAPED_BANK_WEST_GROW: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 4,
}

#: Banks — **in address order**, the index into :data:`TAPED_BANKS` — whose ring
#: worker skips the *rotational delta* instead of the address, and whose feed
#: forwarder therefore carries that ring's head in its off hand. See
#: ``memory_tape.worker_v2_rot`` for the body and ``memory_taped.feed_rotate``
#: for where the head lives.
#:
#: **Per-bank selection is the mechanism, not a refinement of it.** The ring turns
#: one way, so a *backwards* delta costs a near-full lap where the old skip cost
#: the address; a bank whose accesses walk backwards often, and whose ring is
#: short enough that the old skip was cheap anyway, loses outright. Over the
#: 21-round trace (471,189 accesses), per bank, with ``ROT_v1`` today's skip and
#: ``ROT_v2`` the delta:
#:
#: | bank | ring | accesses | ROT_v1 | ROT_v2 | v2>v1 | pre-send ticks saved |
#: |---|---|---|---|---|---|---|
#: | **5** | 442 | 3,850 | **327.0** | **18.4** | 4.2% | **6,889,762** |
#: | 2 | 53 | 10,490 | 24.4 | 7.3 | 13.7% | 1,039,540 |
#: | 8 | 7 | 59,916 | 2.7 | 1.2 | 17.1% | 735,168 |
#: | 1 | 53 | 6,238 | 27.1 | 7.5 | 14.2% | 709,004 |
#: | 10 | 8 | 165,181 | 2.8 | 2.6 | 33.1% | 215,664 |
#: | 0 | 115 | 343 | 93.1 | 2.3 | 1.7% | 180,624 |
#: | 4 | 8 | 11,107 | 5.3 | 4.0 | 50.3% | 116,936 |
#: | 9 | 10 | 121,890 | 2.9 | 2.8 | 27.7% | 116,432 |
#: | 3 | 135 | 6,668 | 13.1 | **23.0** | 17.1% | **-382,823** |
#: | 7 | 22 | 54,218 | 3.5 | **6.8** | 30.9% | **-1,030,005** |
#: | 6 | 59 | 31,288 | 4.8 | **12.1** | 20.5% | **-1,328,745** |
#:
#: Applied to all eleven that is **+8.31%** — a regression. Applied to
#: ``{0, 1, 2, 5}`` it is 21,921 accesses and 8.82M pre-send ticks, and those
#: four are also the ones with the most tolerance for whatever the rotation
#: machinery itself costs (bank 5 answers at ~1,789 ticks an access; bank 8 at
#: 12.3, which is why the small hot rings stay where they are even where the
#: delta is nominally shorter).
#:
#: **The four are exactly the batch-2 banks worth having.** Rotation is a
#: property of the batched body only — the narrow worker has no room for it and
#: no bank that wants it — and 115/53/53/442 are four of the five rings over
#: :data:`TAPED_JUMP_THRESHOLD`. The fifth, bank 3 at 135, is the one whose
#: delta is *longer* than its address, so the registry and the worker's own
#: precondition agree by construction.
TAPED_ROTATE_BANKS: dict[tuple[str, str], tuple[int, ...]] = {
    ("deadman-3d_hires", "taped"): (0, 1, 2, 5),
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
#: ``(slug, tier)`` pairs whose taped store gives its **answer collector**
#: :func:`~..memory_men.teleport`'s latency art — ``R`` and ``s`` adjacent, an
#: 8-cell lap — instead of the 6-cell one every other caller wants.
#:
#: A 6-cell lap is a 3x2 rectangle; a 3x2 rectangle has four corners and exactly
#: two straight cells, and those two are **diagonally opposite**. ``R`` and ``s``
#: cannot double as a corner, so they must stand on the two straight cells and
#: the walk between them is three moves — not by accident but by the shape, and
#: no amount of moving the room changes it. Four columns instead of three put
#: them side by side.
#:
#: The trade is a lap two cells longer for an answer two ticks earlier, and it is
#: worth taking exactly when the room is on the critical path and idle enough
#: that its lap is free. The collector is both: it is the last room on every
#: read, and it is 97.7% idle. There is no binding question — a collector has one
#: outgoing pipe, so its ``s`` has nothing to choose between, and ``R`` has no
#: distance term at all.
#:
#: **Measured, 21-round hi-res tour, same process, same moment, control
#: reproducing 85,522,204 to the tick: -0.706%** (85,522,204 -> 84,918,154),
#: ``passed=True``, box 614x403, ``route_lengths`` identical. Keyed by pair
#: because ``deadman-3d``'s taped store is pinned to a checked-in ``.man``.
TAPED_COLLECTOR_FAST: set[tuple[str, str]] = {
    ("deadman-3d_hires", "taped"),
}

TAPED_COMPACT_GATE: set[tuple[str, str]] = {
    ("deadman-3d", "taped"),
    # Same tier, same gate chain, and the spacer rows are a property of the gate
    # room rather than of the program: the store block goes 224x63 -> 224x58 for
    # hires exactly as it does for `deadman-3d`, and those five rows come
    # straight off the machine's height (see `ROM_ROWS["deadman-3d_hires"]`,
    # whose fold is chosen against that height).
    ("deadman-3d_hires", "taped"),
}

#: ``(slug, tier)`` pairs whose bank gates pull their **return column** in to the
#: longest arm instead of standing at :func:`memory_taped.bank_gate`'s flat
#: ``cx + 13`` (its ``tight_return``). Absent means the shipped width, which is
#: what holds ``deadman-3d``'s checked-in ``deadman-3d_taped.man`` byte-identical
#: — the only reason this is a registry and not simply the new constant.
#:
#: **The gate room is an out-and-back loop, so it costs about twice its width per
#: access, and four of those columns were empty.** ``cx + 13`` is the *low* gate's
#: longest arm plus two spare columns; the **high** form's arms are two cells
#: shorter on each side, so it was carrying four — and the high form is what every
#: bank at the head of the chain uses (:data:`TAPED_BANK_ORDER` peels from the
#: top). The man walks each spare column twice, east onto the descent and west
#: along the floor, on every request the gate passes through.
#:
#: **Empty, and the way it emptied is the point.** On the eleven-bank chain as it
#: stood before :data:`TAPED_BANK_ORDER`'s address-order re-cut, this measured
#: **-8.64%** (3-round tour, 12,248,581 -> 11,190,732, box 625x403 -> 624x403,
#: mean store read latency 221.44 -> 184.04). Re-measured **same-moment** against
#: the re-cut chain, on one process with one build of each, it is **+0.892%**:
#:
#: | | ticks | mean read latency |
#: |---|---|---|
#: | **off (shipped)** | **11,091,760** | **180.53** |
#: | on | 11,190,732 | 184.04 |
#:
#: The -8.64% was never real. It was measured across a window in which the bank
#: re-cut landed underneath it in a shared file, and it credited that landing to
#: this lever — the exact trap AGENTS.md names, walked into anyway. **Only a
#: same-moment A/B says anything about a shared tree.**
#:
#: The mechanism that flipped it is worth keeping, because it says what the gate
#: chain is now worth. Narrowing a gate takes six walked cells off its *service*
#: time, all of them after its ``s`` has already sent — pure occupancy, nothing on
#: the critical path. But ``bx`` is measured from ``gate_w``, the **max** over the
#: gates, and only the low form sets that max: shrinking the high form by three
#: moves its east wall three columns west while the bank row moves one, so every
#: high gate's feed pipe grows **two cells that are on the critical path**. That
#: trade wins while the chain is deep and loses once it is shallow, and the re-cut
#: took mean hops 2.83 -> 1.874. Occupancy stopped being worth latency.
#:
#: To make it pay, the gate would have to keep its east wall where it is and move
#: only the return column — the two are the same column today. The ``s`` bindings
#: would not notice either way: both outgoing pipes attach to that one wall, so
#: the return column enters all ten distances as a common term and cancels.
TAPED_TIGHT_GATE: set[tuple[str, str]] = set()

#: ``(slug, tier)`` -> how many columns east of its longest arm each bank gate's
#: **return column** stands, with the east wall held where it shipped
#: (:func:`memory_taped.bank_gate`'s ``return_slack``). Absent means the shipped
#: flat ``cx + 13``, which for the high form is three columns of air.
#:
#: This is :data:`TAPED_TIGHT_GATE`'s mechanism with its side effect removed, and
#: the side effect is the entire reason that entry ships empty. ``tight_return``
#: moves the east **wall** in with the column, ``gate_w`` is a max over the gates,
#: and ``bx`` comes off ``gate_w`` — so pulling the narrower high gates in three
#: columns moved the bank row only one and grew every high gate's feed pipe by two
#: cells *on the critical path*. Holding the wall costs those two cells nothing.
#:
#: What it buys is service time, measured exactly by focusing the opcode profiler
#: on one gate's man (he is the only one in the room, so his non-blocked ticks are
#: that gate's service time) and dividing by its access count. The gate at chain
#: position 1, per access, shipped::
#:
#:     spine        11.016   U b r M ` 8 8 5 ` - X
#:     arm:north     2.956
#:     arm:south     6.647
#:     pad:east      4.691   <- air between the arm's last glyph and the descent
#:     descent       3.903
#:     floor:east    1.000
#:     floor:west   21.999
#:     climb         3.000
#:     ---------------------
#:     total        55.212
#:
#: The room is an out-and-back, so each column of air is walked twice — once east
#: onto the descent and once west along the floor. Three columns is six ticks, and
#: all six fall **after** the arm's ``s`` has already sent, so none of it is on the
#: read's critical path. It is occupancy, which is what this store's queueing term
#: is made of: mean read latency is 188.5 against a hard floor of 88.
#: Measured same-moment on the 21-round tour, both endpoints built in one
#: process: 114,847,979 -> 114,381,949, **-0.406%**, mean read latency
#: 185.151 -> 183.716, box 625x403 unchanged, ``passed=True fatal=None``.
#:
#: That is the whole of what pure occupancy is worth here, and it is the number
#: to price the next one against: six walked cells off five gates and two off
#: four moved the mean read latency 1.435 ticks. The gate is **blocked 85% of the
#: time** (9,400,695 ticks parked on its ``U`` against 1,690,629 walking, over an
#: 11.09M 3-round run), so a tick removed from behind its ``s`` is mostly
#: absorbed. A tick removed from in *front* of its ``s`` is not — see
#: :data:`TAPED_GATE_PARK_CONST`, which is ten times larger for the same idea.
TAPED_GATE_RETURN_SLACK: dict[tuple[str, str], int] = {("deadman-3d_hires", "taped"): 0}

#: ``(slug, tier)`` pairs whose bank gates keep their range constant **parked in
#: B between accesses** instead of reloading it on the spine
#: (:func:`memory_taped.bank_gate`'s ``park_const``). Absent means the shipped
#: spine, which holds ``deadman-3d``'s checked-in grid byte-identical.
#:
#: The spine is the gate's whole critical path — a request is not forwarded until
#: the arm's second ``s``, and everything before that is ``U b r`` plus the range
#: test. The shipped test spends five to seven of its cells fetching a constant
#: that never changes::
#:
#:     low   UbrM`103`W-X   12 cells   ->  Ubr-X     5
#:     high  UbrM`885`-X    11 cells   ->  Ubr-NX    6
#:
#: A gate man keeps A, B and BP across laps, so the constant can live in B, and
#: the reload is written into the **return floor** — cells he already walks, and
#: cells that were nops. It therefore costs nothing at all: the room's service
#: time falls by the same five to seven cells *again*, because the floor is
#: measured from the spine's ``X``.
#:
#: Two details make it work. The floor is walked **west**, and the engine keys a
#: literal by the direction the man arrives from — the closing mark carries the
#: forward value and the opening mark the digits reversed — so the digits are
#: stamped reversed and the westmost mark is what fires. And the high form uses
#: ``W`` rather than ``-N``: both leave the same A, but ``W`` also leaves ``addr``
#: in B, which is where the two south arms have always looked for it, so all four
#: arms are the shipped ones. ``-N`` leaves the constant in B instead and costs
#: those arms two cells each to rebuild the address — measured same-moment,
#: 10,461,012 against 10,417,535 on the 3-round tour, **-0.416%** for ``W``.
#: Measured same-moment, one process, one build of each. On the 3-round tour,
#: against the shipped gate::
#:
#:     ship                      11,091,760   180.534 mean read latency
#:     park (alone)              10,706,704   166.884   -3.472%
#:
#: and on the 21-round tour with the whole store's three entries on::
#:
#:     ship                     114,847,979   185.151   625x403
#:     the three entries        106,770,604   160.266   620x403   -7.033%
#:
#: ``passed=True fatal=None``, suite 2865/68. Five columns come off the machine
#: because ``gate_w`` is the low form's width and that form loses seven cells.
#: The gate's service time falls twice over — once on the spine, which is
#: **critical path**, and once on the return floor, whose length is measured from
#: the spine's ``X``. The first is what pays: the gate is 85% idle, so its
#: occupancy converts at about 0.2 latency ticks per tick
#: (:data:`TAPED_GATE_RETURN_SLACK`), and its spine at about 1.0 per crossing.
TAPED_GATE_PARK_CONST: set[tuple[str, str]] = {("deadman-3d_hires", "taped")}

#: ``(slug, tier)`` pairs whose **high** gates drop the ``W``/``M`` pair from
#: their two **south** (forwarding) arms.
#:
#: The shipped south arms are ``WM0sWs`` / ``WM1sWsrs``, and the comment beside
#: them — "addr is already in B" — is the whole proof that the first two glyphs
#: do nothing. On arrival A holds ``const - addr`` and B holds the raw ``addr``;
#: the arm fetches it into A with ``W`` and immediately parks it back into B with
#: ``M`` so the op digit can have A. Leaving it where it already is costs
#: nothing::
#:
#:     WM0sWs   W: A=addr B=c-a | M: B=addr | 0: A=0 | s | W: A=addr | s
#:       0sWs                   |           | 0: A=0 | s | W: A=addr | s
#:
#: Same two words, same order, same pipe. The sends only move **west along their
#: own row**, which shifts their distance to *both* of the east wall's source
#: cells by the same amount, so §7.1's nearest-pipe choice cannot flip
#: (``test_every_gate_send_still_binds_to_the_pipe_it_means`` checks it anyway).
#: B is left holding 0 instead of the address, which is what the shipped arms
#: leave too, and the return floor rebuilds B from the literal every lap.
#:
#: **Two cells off the forward leg of every forwarded request**, which is the
#: part a read's caller is stopped for. Measured on the 21-round hi-res tour,
#: both endpoints in one process, on top of the fork adapter and the request
#: tuck: 106,141,505 -> **105,152,308**, ``passed=True``, ``fatal=None``, box
#: 620x403 unchanged — **-989,197 ticks, -0.9320%**, three times the adapter
#: fork on its own.
#:
#: This is a **register-liveness** change, so it is exactly the class that builds
#: green and hands a read to the wrong bank. It is pinned by a readback of all
#: 901 hires addresses through both arm forms, not by the tour
#: (``test_the_reused_b_south_arms_route_every_hires_address_the_same``).
#:
#: The north arms cannot have the same treatment: their outgoing address is the
#: *rebased* ``addr - const``, which exists in neither register on arrival, so
#: ``N`` computes it and something has to hold it across the op digit.
TAPED_GATE_SOUTH_REUSE_B: set[tuple[str, str]] = {("deadman-3d_hires", "taped")}

#: ``(slug, tier)`` pairs whose **ring workers** keep the tape size in B between
#: accesses (``memory_tape.worker_v2``/``worker_v2_jump``'s ``park_const``), the
#: same move :data:`TAPED_GATE_PARK_CONST` makes inside a gate. MAIN's two arms
#: each fetch N to build the signed remaining distance::
#:
#:     read   rbM`N`-M   ->  rb-NM
#:     write  rbM`N`-NM  ->  rb-M
#:
#: and the reload rides the return gutter, which every lap already walks. Both
#: arms are ahead of P1, so what they save is the read's **critical path**, not
#: occupancy. Absent means the shipped MAIN, which is what holds ``deadman-3d``,
#: ``matmul`` and ``sudoku`` byte-identical — they share this worker.
#: Measured same-moment on the 21-round tour, on top of the two gate entries::
#:
#:     + gate entries only             10,692,372   166.376   (3-round)
#:     + tape park                     10,461,012   158.177   **-2.164%**
#:
#: and the three together take the 21-round tour 114,847,979 -> 106,770,604,
#: **-7.033%**, ``passed=True fatal=None``, 620x403.
#:
#: The arms alone are worth **nothing** — the descent to P1 stood at a fixed
#: column 15, chosen for the widest literal, so a shorter arm only turned glyphs
#: into blanks the man still walked. Moving that column to follow the arms (11,
#: P1's entry, at every N now that the arms no longer depend on it) is what pays,
#: and it pays twice: once walking east onto the descent and once walking west
#: along row 4 to P1's turn. Measured 3-round: -0.000% for the arms, **-2.164%**
#: with the descent.
TAPED_TAPE_PARK_CONST: set[tuple[str, str]] = {("deadman-3d_hires", "taped")}

#: Whether each bank's ring is routed to the **shortest** fold that still holds
#: its values, rather than the first one tried (:func:`tape_block`'s
#: ``tight_ring``; the fold list is :data:`_TAPE_FOLDS`). Absent pairs keep the
#: shipped ring, so ``matmul``, ``sudoku`` and the byte-pinned ``deadman-3d``
#: grids do not move by a cell.
#:
#: A bank's ring is a **perimeter**, not a function of its slot count: every
#: batch-1 bank gets 108 cells and every batch-2 one 154, whether it holds six
#: values or a hundred. ``tape_block`` has always searched folds from 0 upward
#: and returned the first with capacity for ``n + 1`` — and fold 0 is the
#: *longest* ring, so it always won and no other fold was ever built. Reversing
#: the preference costs nothing: the folds are the same two L-shaped pipes with
#: the return's middle leg turning back further west, the worker's wall anchors
#: do not move, and ``_Tape.slots`` still reports the real cell count.
#:
#: Why it should matter at all, when a previous agent measured a ring worker
#: **6.29 ticks mean blocked** and concluded rotation was a myth: that measures
#: the worker's *own* stalls, and the values do bunch at the head of a slack
#: pipe. What a long ring still costs is the lap — a value the worker sends must
#: travel the whole perimeter before it can be read again — and the hot banks
#: here hold 6..9 values in a ring sized for a hundred, so ~100 of those cells
#: are pure re-circulation delay between one access and the next.
#:
#: **It is worth exactly zero, and the zero is the finding.** Built same process,
#: same moment on the 21-round hi-res tour with the eleven rings going
#: ``108,108,108,154,154,154,154,294,108,154,366`` ->
#: ``82,82,82,110,110,110,110,294,82,138,366`` — every hot bank's perimeter cut
#: by a quarter — the tour came out on the **identical tick, 100,669,456**, and
#: the 3-round read-latency histogram was identical bucket for bucket (mean
#: 138.878, floor 68). So the re-circulation genuinely does happen inside the gap
#: before the next request, and the head-of-line delay behind the value in front
#: is not a term in read latency at this traffic.
#:
#: **Re-run at 105.6 mean read latency and it is still exactly zero.** The store
#: has since been roughly halved, which is precisely the condition under which a
#: "costs nothing because it happens in the gap" result is supposed to expire —
#: and the bank worker's post-send *walk* did expire (``memory_tape``'s
#: ``WORKER_V4_POST_PAD``: 0.019% a tick where it used to be 0.000%). This did
#: not, and the difference is the point: a long ring delays a **value** in a
#: pipe, which the man overlaps with his own walking, while a long tail delays
#: the **man**, who is the only thing the next request can queue behind. Same
#: process, same moment, identical tick both times.
#:
#: Kept, off, for two reasons: it makes :data:`_TAPE_FOLDS` reachable rather than
#: dead, and headroom that is free at 87k reads a frame is not free at four times
#: that. It is also what would *license* moving the relay room east — the ring
#: would grow and that now costs nothing — which is the one way the six-cell
#: ``reqK->bankK`` stub gets shorter (see :data:`TAPED_FEED_TUCK`).
TAPED_TIGHT_RING: set[tuple[str, str]] = set()

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
#: Rows the packed wall's panel cluster — and the south blocks hanging off it —
#: are lifted toward the north blocks, per ``(slug, tier)``. Absent (0) keeps the
#: geometry :data:`d3_router.PACK_CLUSTER_Y` and :data:`d3_router.PACK_ROW_S`
#: state outright, which is what the default (uncompacted) unit needs.
#:
#: The north blocks end around wall row 85 and the cluster began at 110, so
#: twenty-five rows sat between them unclaimed. The constraint on
#: ``PACK_CLUSTER_Y`` says where the cluster may sit *relative to* the two block
#: rows; nothing pinned the pair's absolute position, so the whole group can slide
#: north. Swept on the real build, 21-round tour, ticks to frame 20:
#:
#: | lift | box | ticks | Δ |
#: |---|---|---|---|
#: | 0 | 649x492 | 189,164,256 | — |
#: | **21** | **649x471** | **189,163,815** | **-441** |
#: | 22 | — | — | ``collision at (352, 85)``: T1's south wall |
#:
#: **Twenty-one rows off the machine, and 441 ticks faster** — free in both
#: directions. The tick delta is exactly -3 a row at every lift from 4 to 21, so
#: it is mechanism and not noise: the router's legs down to T2/T3 shorten one cell
#: per row. It is small because those legs are a *pipeline* — the CPU sends a
#: command word and walks on, so their length is latency the units absorb rather
#: than throughput the CPU pays. Which is why nobody had looked: there is no tick
#: pressure here at all, only height, and height is free for this family.
#:
#: **Why this is a registry and not just smaller constants.** The lift is only
#: legal with the *compacted* unit (:data:`DOOM_LOOP_ROW` / :data:`DOOM_LEAF_COLS`).
#: Lowering the constants outright collides at ``(488, 88)`` for callers that take
#: ``build_packed_wall``'s defaults — which two tests in
#: ``tests/test_deadman3d_hires.py`` do, and they caught it. Keyed per
#: ``(slug, tier)``, the default path stays byte-identical.
DOOM_CLUSTER_LIFT: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 24,
}

#: Rows the **north** block row rises by, off :data:`d3_router.PACK_ROW_N`.  The
#: lift above moves the cluster and the south blocks together and tops out at 21,
#: where the cluster's north wall meets T1's south wall (``collision at (352,
#: 85)``); raising this by the same amount first buys the lift that much more
#: room, one row of screen per row of block.
#:
#: It buys **three**, and the router's own fan is what ends it.  The four legs
#: leave the router's south wall at row 11, and the north row's two legs turn east
#: at ``row_n - 2`` and ``row_n - 3``.  At ``north_up = 3`` those lanes are rows 13
#: and 12, the last pair clear of the outlet row; at 4 the outer lane *is* row 11.
#:
#: **And four builds.**  Every pipe binds, ``build_for`` returns a 649x460 grid,
#: and the frame gate then fails — ``fatal='wrong-frame'``, ``passed=False``, at
#: tick 3,083,064.  Nothing short of comparing frames catches it, so every value
#: here is gated over the tour rather than trusted because it built.  Measured,
#: 3 rounds, ticks to the last frame:
#:
#: | ``north_up`` / lift | box | screens | ticks | |
#: | --- | --- | --- | --- | --- |
#: | 0 / 21 | 649x464 | y286 | 21,659,879 | shipped before |
#: | 1 / 22 | 649x463 | y285 | 21,659,876 | |
#: | 2 / 23 | 649x462 | y284 | 21,659,873 | |
#: | **3 / 24** | **649x461** | **y283** | **21,659,870** | 21 rounds: 191,599,652 |
#: | 4 / 25 | 649x460 | y282 | — | ``wrong-frame`` |
#:
#: The ticks are flat to within 3 a row — the legs shorten one cell per row and
#: they are a pipeline, so this is height, not speed.  See
#: ``scratch/deadman3d-opt/hires_screenlift.py``.
DOOM_PACK_NORTH_UP: dict[tuple[str, str], int] = {
    ("deadman-3d_hires", "taped"): 3,
}

#: Whether the packed wall stands **both north blocks west of the cluster**
#: instead of above it, per ``(slug, tier)``.  Absent (``False``) keeps the
#: sandwich and the two levers above; ``True`` takes a different placement
#: entirely (:func:`d3_router._packed_wall_north_west`) and ignores them, which
#: is why it is a flag rather than another row count.
#:
#: :data:`DOOM_PACK_NORTH_UP` is the end of the sandwich: the cluster can rise
#: only until the router's fan runs out of lanes, and that is three rows.  What
#: it could not do is stop stacking a block row *above* the screens at all,
#: because the east block's pipes come back west underneath their own block —
#: into the strip a risen cluster wants — and returning over the top crosses
#: that block's own leg, an L that separates the quadrant its ADDR has to cross.
#: A **west** block has no such L: its command column is west of its ports.  So
#: both north blocks go west, side by side, on the cluster's own rows, and the
#: block row comes off the wall's height outright.
#:
#: **Seventy-three rows of screen**, measured on the real build, ticks to the
#: last frame over the tour:
#:
#: | arrangement | box | wall | screens | 3 rounds | 21 rounds |
#: | --- | --- | --- | --- | --- | --- |
#: | sandwich, ``north_up=3`` | 649x461 | 365x264 | y283 | 21,659,870 | 191,599,652 |
#: | **north-west** | **649x388** | **475x191** | **y210** | **21,660,329** | **191,601,893** |
#:
#: Seventy-three rows off the machine for +459 ticks at 3 rounds and +2,241 at
#: 21 — **+0.001%**, which is flat.  It is not quite nothing and it is not noise
#: either: the twelve port pipes get longer (T1's now cross the whole wall) and a
#: pipe's length is its latency, so each frame's paint arrives a little later.
#: That is a per-frame constant, which is why the 21-round delta is seven times
#: the 3-round one on seven times the frames.  This is a height lever, as the two
#: above it were; there is no tick pressure on this family at all.
#:
#: The wall goes 365 to 475 columns wide, which the machine does not feel: the
#: grid STORE sets the box at 649 and the wall had ~284 spare columns.  Height is
#: the objective (``AGENTS.md`` § deadman-3d is out of contest scope).
#:
#: The floor is ``d3_router.PACK_NW_CLUSTER_Y`` = 13 and it is one leg's, not the
#: fan's — see there, including the ``wrong-frame`` a row lower that only the
#: gate catches.
#:
#: Keyed, like both levers above, because ``build_packed_wall``'s default path
#: has to stay byte-identical for the two tests in
#: ``tests/test_deadman3d_hires.py`` that call it bare.
DOOM_PACK_NORTH_WEST: dict[tuple[str, str], bool] = {
    ("deadman-3d_hires", "taped"): True,
}

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
    # Re-derived a second time, with the cut it belongs to (the six-bank re-cut
    # of 1..800 recorded under `TAPED_BANKS`). Under that cut the traffic is
    # monotone *down* the address space for the first time — 111,321 reads in
    # 895..901, then 80,088, 44,657, 33,015, 20,165, and everything below 801 in
    # single digits of thousands — so the cheapest chain is the plain top-down
    # peeling, every gate taking the high end. Measured against the alternative
    # that keeps the old cut's shape (`(10, 9, 8, 7, 6, 0, 5, 4, 3, 2, 1)`, which
    # peels cold bank 0 out of turn to reach 5 from the top): 96,280,186 against
    # 96,935,246, so the order alone is worth **-0.65%** on the 21-round tour, or
    # ~2.0 ticks of mean read latency for the two extra gate traversals it saves
    # the 353..800 traffic.
    ("deadman-3d_hires", "taped"): (10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
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
                                           # does, at the same 60-row fold.
                                           #
                                           # 840 stopped covering it: the level's own
                                           # monster count sets TAPE_SIZE and id's E1M1
                                           # wants **902**. 18x51 = 918 does, and the
                                           # shape is chosen on `max(w, h)` rather than
                                           # slots — the grid store is address-flat, so
                                           # depth is nearly free (51 -> 76 moves the
                                           # tour 0.56%) while every extra *column*
                                           # costs ~0.42 t/access. Shallow-and-wide is
                                           # therefore the wrong trade; 18 columns is
                                           # the fewest that reach 902 at a depth the
                                           # fold still takes.
                                           #
                                           # **That depth limit was assumed, not tested.**
                                           # 14 columns at 65 rows folds fine, and it is
                                           # worth -0.543% on its own; 15x61 ties it. The
                                           # column term (~0.42 t/access) beats the depth
                                           # term (51 -> 76 moves the tour 0.56%), so
                                           # narrow-and-deep is the right trade and 18 was
                                           # the wrong end of it.
                                           "deadman-3d_hires": (14, 65)}

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
#: **Re-validated after ``a15b200``** (which landed :data:`LANE_PITCH` and
#: :data:`TAPED_CHAIN_REACH`, taking the machine to 340,720,837). The count is a
#: trade of ring length against gate hops, and both sides of it moved — so it was
#: re-swept rather than assumed. Eleven holds, and the curve *sharpened* around
#: it (3-round tour):
#:
#: | banks | ticks | Δ | (was, pre-``a15b200``) |
#: |---|---|---|---|
#: | 10 | 31,224,062 | +0.112% | +0.10% |
#: | **11** | **31,189,000** | — | — |
#: | 12 | 31,559,311 | +1.187% | +0.69% |
#: | 13 | 31,829,636 | +2.054% | — |
#:
#: The 12-bank penalty nearly doubled. A faster CPU makes a gate hop relatively
#: more expensive than the ring it saves, which pushes the optimum toward *fewer*
#: banks — eleven is now further from twelve than it was, not closer.
#:
#: The order below is **not separable from this**: ``(3, 0, 1, 2)`` was read off
#: the uniform quarters' traffic, and re-cutting the banks rewrites the traffic it
#: was read off. Priced on the DP-4 cut, the stale order gives -53.6% where the
#: matching one gives -56.4%. Any change here needs a new order beside it.
#:
#: **hires' eleven-way cut is a measured local optimum, and a static per-read
#: cost model will not find a better one.** The obvious objective — minimise
#: ``reads * (ring service + 23 * gate hops)`` over every contiguous 11-way cut
#: of ``1..901``, which is an exact DP, not a search — was run at hop costs 0,
#: 10, 23, 40, 60 and 100 and its answers all measure **worse** on the 21-round
#: tour than the shipped ``(114, 52, 52, 134, 7, 441, 58, 21, 6, 9, 7)``:
#:
#: | H | DP-optimal cut | E[service] | measured |
#: |---|---|---|---|
#: | — | **shipped** | 211.3 | **96,280,186** |
#: | 0 / 10 | ``(154,73,125,7,441,7,51,21,7,8,7)`` | 199.5 | 140.04 mean lat, +9.5% |
#: | 23 | ``(122,60,53,117,7,441,7,51,21,11,11)`` | 210.1 | 102,601,408, +6.57% |
#: | 40 | ``(114,64,53,121,7,441,7,51,21,10,12)`` | 210.9 | 102,540,559, +6.50% |
#: | 60 | ``(114,64,53,121,7,441,7,51,21,9,13)`` | 212.3 | 104,914,027, +8.97% |
#:
#: The H=0 answer is the already-measured ``P3`` cut, and it has *lower* expected
#: service than the shipped one and loses by 9.5%. What the DP cannot see is that
#: a bank is a **single-server ring**: the quantity that tracks these numbers is
#: the share of reads whose immediately preceding request hit the same bank
#: (shipped 0.271, every DP answer 0.320..0.582), because such a read waits out
#: the previous one's whole lap. Any objective that only prices a read against
#: its *own* bank will keep proposing concentration, and concentration is what
#: costs. See ``deadman3d._HIRES_SCALARS``, where the same trap cost ten builds.
TAPED_BANKS: dict[str, int | tuple[int, ...]] = {
    "deadman-3d": (352, 164, 15, 69),
    # **The last five are a re-ordering, not a re-cut**, and they move with
    # ``deadman3d._HIRES_SCALARS`` — see there for the derivation and the
    # measurement. Same five sizes, same five rings, same eleven gates; what
    # changed is which addresses land in them. The cut used to run
    # ``6, 9, 7, 58, 21`` up the address space, which put 58 cold slots
    # (``TMP2``..``WBAND``) between the two hot clusters — and because a gate can
    # only peel a bank off an **end**, that cold bank took chain position 1 and
    # pushed the hottest ring to position 3. Reversed, the chain peels
    # ``7, 9, 6, 21, 58`` and the scalars are renumbered to match, so the hot set
    # is at the front and still in the small rings. -12.01% on the 21-round tour.
    #
    # **The first six were then re-cut, and the reason is that the old cut was
    # fitted to a four-frame trace.** ``hires_bankcut.py`` traced
    # ``hires.WALK[:4]``, and in four frames the player has barely moved — the
    # DDA's map working set was 21 slots wide, so it landed inside the 21-slot
    # bank at 103..123 and the 229-slot bank above it looked stone cold (0.07%
    # of traffic). Over the twenty gameplay frames the gate actually scores, the
    # player walks: the map reads spread over ``MAPB+110..MAPB+250`` at stride 4
    # (one packed word per 16 columns, one row per four words), 6.6% of all
    # reads land in that 229-slot ring, and it was doing **42.3% of the store's
    # ring work**. Re-traced stride-1 exact over the 21-round tour (471,189
    # accesses, 324,600 of them reads) and re-cut against that.
    #
    # What the cut is actually minimising is **not** ring length. Ring sends are
    # exactly ``accesses * (slots + 1)`` in every bank — the worker laps the whole
    # tape once per access — but the lap finishes *after* the answer leaves, so it
    # is off the CPU's critical path, which is why :data:`TAPED_TIGHT_RING`
    # measured 0.000% and why a service-time cut (the ``80 + 8.6*slots`` /
    # ``122 + 5.8*slots`` fit under :data:`TAPED_JUMP_THRESHOLD`) mispredicts by
    # 4x. Regressing measured mean read latency on thirteen built-and-run cuts
    # gives, to within 0.7 ticks RMS on leave-one-out,
    #
    #     latency = 93.4 + 3.91 * E[offset in bank] + 5.03 * E[rotation]
    #
    # where the rotation is ``(slot - previous slot in that bank) mod slots``.
    # Both terms are about **where a bank's boundary falls relative to the hot
    # addresses**, and neither is about how big the bank is — a bank is cheap
    # when its hot cluster sits near its base *and* consecutive accesses to it
    # walk forwards a short way. That is the same rule the history states as "hot
    # traffic in small rings, small rings at the front", but stated in the
    # variable that is actually load-bearing, which is why "small" was never the
    # right knob: the cold ``MONB``/``SPRB``/``DIGB``/``ZBUF`` span is **merged**
    # here into one 441-slot ring — the largest this family has ever built — to
    # buy a fourth boundary for the map, and that is a win, not a cost.
    #
    # Swept by coordinate descent on the ten boundaries against that model, then
    # measured same-process, control first and last, on the 21-round tour:
    # **100,669,456 -> 96,280,186, -4.36%**, mean read latency 141.47 -> 127.88
    # (the run moves by exactly ``latency * 324,600 reads``). Runners-up, same
    # sweep: the map in four banks at ``(138, 52, 45, 117)`` -3.79%, the map in
    # three at ``(154, 73, 125)`` -1.71%, and -0.65% for the same cut behind the
    # old end-peeling order. What does *not* work is optimising either term
    # alone: the offset-only optimum ``(110, 52, 52, 138, 253, 195, ...)`` folds
    # ``POSX..PLANEY`` into a 253-slot ring, and measured **+1.82%**.
    "deadman-3d_hires": (114, 52, 52, 134, 7, 441, 58, 21, 6, 9, 7)}

#: Ring-worker batch for the taped tier's banks. ``2`` is the two-word counted
#: worker (~5 ticks per skipped word against batch 1's 8): +12 columns per bank
#: and measured -13% on the frame gate; the machine still fits the 307 width.
#:
#: **Declined for ``deadman-3d_hires``, and :data:`TAPED_BANKS` is the reason.**
#: The absent entry is deliberate, not an oversight: hires falls through to
#: ``.get(name, 1)`` on purpose. The lever pays per *slot walked*, so its value is
#: a function of ring length — and the eleven-bank cut above already spent that
#: length. Measured on the 21-round tour over frames 1..20
#: (``scratch/deadman3d-opt/hires_batchrun.py``), against the shipped cut's
#: **365,333,921 ticks at 514x451**:
#:
#: | variant | banks | batch | box | ticks | Δ |
#: |---|---|---|---|---|---|
#: | **shipped** | 11 | **1** | **514x451** | **365,333,921** | **—** |
#: | naive port | 11 | 2 | 641x447 | 374,110,911 | **+2.402%** |
#: | re-cut for batch 2 | 9 | 2 | 541x451 | 373,783,008 | **+2.313%** |
#: | | 11 | 4 | 806x451 | 397,592,042 | **+8.830%** |
#:
#: Batching is a *regression* here, and re-deriving the cut for it does not
#: rescue it — the best batch-2 configuration over the whole DP family is still
#: +2.31%. Nothing is shipped.
#:
#: **The lever did not vanish, it was already collected.** Priced on the same
#: 3-round tour across cuts, batch 2's worth is monotone in ring length and
#: changes sign before it reaches the shipped one:
#:
#: | cut | longest ring | batch 1 | batch 2 | Δ |
#: |---|---|---|---|---|
#: | uniform quarters (pre-cut) | 226 | 98,744,653 | 71,793,271 | **-27.29%** |
#: | DP 4 | 673 | 43,032,952 | 39,046,675 | **-9.26%** |
#: | DP 8 | 438 | 35,341,671 | 35,440,614 | +0.28% |
#: | DP 9 | 438 | 33,817,184 | 34,565,029 | +2.21% |
#: | **DP 11 (shipped)** | 306 | **33,625,138** | 34,813,816 | **+3.54%** |
#:
#: On the 223-slot ring hires ran before ``c51a748`` this was worth -27%, and
#: batch 4 there was worth -44.5% (54,754,680). Both are moot: the cut alone
#: takes the same 3-round tour to 33,625,138, which no batch-and-quarters
#: combination comes near. The two levers are substitutes, the cut is the
#: stronger one, and stacking them is worse than the cut alone because the
#: batched worker's per-access fixed cost is now paid on rings too short to
#: amortise it.
#:
#: **The cut cannot be re-derived to suit the batch either**, and this is
#: structural rather than empirical. ``hires_bankcut.dp`` minimises the ring term
#: alone, and that term is *linear* in its ``RING`` constant — so for a fixed bank
#: count the split it returns is invariant to the batch, and only the count can
#: move against the fixed ``HOP``. ``scratch/deadman3d-opt/hires_batchdp.py``
#: sweeps it: eleven banks stay optimal at ``RING=8`` (batch 1), ``5`` (batch 2)
#: and ``3.5``, and the count does not move off eleven until ``RING~2.0`` — far
#: below anything a real worker charges. The measured batch-2 optimum does drift
#: one notch coarser than the model's (nine banks, not eleven), which is the
#: predicted direction for an unmodelled per-access fixed cost, and is worth
#: 0.09pp — it does not change the decision.
#:
#: **Re-validated after ``a15b200``, and the margin is now thin enough to be worth
#: re-checking rather than treating as settled.** That commit landed
#: :data:`LANE_PITCH` and :data:`TAPED_CHAIN_REACH` for hires, both of which take
#: time out of the *store-adjacent* path this lever competes with, so the decline
#: was re-measured rather than inherited (3-round tour):
#:
#: | variant | box | ticks | Δ |
#: |---|---|---|---|
#: | shipped, batch 1 | 514x451 | 31,189,000 | — |
#: | batch 2 | 641x447 | 31,246,826 | **+0.185%** |
#: | batch 4 | 806x451 | 32,458,584 | +4.071% |
#:
#: Batch 2's penalty fell from **+3.54% to +0.185%** on an unchanged builder —
#: an order of magnitude, from the CPU-side work alone. It is still a regression
#: and stays declined, but it is now close enough that the next landing which
#: shortens the store path could flip it. Re-run this before assuming otherwise;
#: the same mistake in the other direction is what made
#: :data:`TAPED_CHAIN_REACH`'s -0.020% decline void.
#: **And then the seek drum landed and the decline reversed a third time.** The
#: full history of this one entry is the clearest evidence in the repo that a
#: lever's value is a property of the machine, not of the lever:
#:
#: | machine | batch 2 |
#: |---|---|
#: | uniform quarters, no drum | **-27.29%** (moot — the cut beats it outright) |
#: | 11-bank cut | +3.54% |
#: | + ``LANE_PITCH`` / ``TAPED_CHAIN_REACH`` | +0.185% |
#: | + seek drum + ``lap_via_jump`` (**now**) | **-1.567%** |
#:
#: Nothing about the batched worker changed across those four rows. What changed
#: is how much of the run is spent in the store: the bank cut took the store from
#: ~68% of the run to a small share, which is why batching stopped paying — and
#: the drum then took ~40% out of everything *else*, which raised the store's
#: share back and made it pay again. Shipped at 207,366,882 -> **204,117,437**,
#: 21-round tour, ticks to frame 20. Batch 4 remains a loss at +2.773%.
#: **And then it stopped being one decision.** ``None`` hands the choice to each
#: bank separately (:func:`memory_taped.taped_store_block`'s ``jump_threshold``),
#: and that beats either uniform answer outright, because the shipped eleven-bank
#: cut straddles the two workers' crossover: its rings run 6, 7, 7, 9, 21, 21,
#: 58, 102, 135, 229, 306 slots. Batch 2 trades ~42 ticks of extra setup for
#: ~2.8 ticks a slot, so it wins above ~15 slots and loses below it — and the
#: banks under that carry most of the store's traffic, so hires was paying that
#: setup where it could never be earned back. Same-moment against the current
#: chain (one process, one build each, 3-round tour): uniform batch 2
#: 11,211,493 -> per-bank **11,091,760**, **-1.068%**, and uniform batch 1 is
#: +3.654%. Neither uniform answer is within 1% of mixing them.
TAPED_SKIP_BATCH: dict[str, int | None] = {
    "deadman-3d": 2,
    "deadman-3d_hires": None,
}

#: Ring size at which a ``TAPED_SKIP_BATCH`` of ``None`` switches a bank from the
#: plain worker to the batched one; unlisted slugs fall through to ``build``'s own
#: ``tape_jump_threshold`` (128, the whole-tape default, which is about the size a
#: *single*-ring tape has to reach before batching is worth its width).
#:
#: **16 is the middle of a plateau, not a fitted edge.** Per-bank service time was
#: measured directly — focus the opcode profiler on one bank's worker room, he is
#: the only man in it, so his non-blocked ticks divided by the lap count his ring
#: pipe reports *is* that bank's cost per access — and it fits ``80 + 8.6*slots``
#: for batch 1 against ``122 + 5.8*slots`` for batch 2, crossing at ~15. The
#: shipped cut has no ring between 10 and 22 slots, so every threshold in 11..22
#: builds the identical grid. Swept same-moment — one process, one build per row,
#: 3-round tour, everything else at its shipped value:
#:
#: | threshold | banks on batch 1 | ticks | Δ | mean read latency |
#: |---|---|---|---|---|
#: | all batch 2 | — | 11,211,493 | — | 184.78 |
#: | 8 | 6 | 11,184,216 | -0.243% | 183.81 |
#: | 10 | 6, 7, 7 | 11,126,328 | -0.760% | 181.76 |
#: | **11..22** | **6, 7, 7, 9** | **11,091,760** | **-1.068%** | **180.53** |
#: | 23..59 | + both 21s | 11,106,384 | -0.937% | 181.05 |
#: | 128 (``tape_block``'s own) | everything under 128 | 11,337,427 | +1.123% | 189.24 |
#: | all batch 1 | all | 11,620,819 | +3.654% | 198.83 |
#:
#: The two ends of that table are the two answers this registry used to be able to
#: give, and both are worse than mixing.
#:
#: **This is also what moved the read-latency floor**, which is a separate thing
#: from the mean and worth naming: min latency over every read goes **100 -> 88**
#: ticks. The bank at chain position 0 is a short ring, so per-bank selection puts
#: the *plain* worker there, and it answers ~12 ticks sooner than the batched one
#: — batch 2's extra setup is all in front of the send. The mean moves for the
#: ordinary reason (less occupancy, so less head-of-line behind writes); the floor
#: moves because the fastest possible read got shorter.
#:
#: Swept twice, months of machine apart in effect: the first sweep was against the
#: pre-re-cut chain and gave -1.643% with the same plateau and the same winner.
#: The bank *multiset* is what this threshold reads, and re-ordering the cut did
#: not change it.
TAPED_JUMP_THRESHOLD: dict[str, int] = {"deadman-3d_hires": 16}


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


#: The SPILL block: where its two pipes meet the CPU's east wall, where the block
#: itself stands, and how far east ``PUSH``/``POP``'s pipe glyph is pushed inside
#: its lane. Absent means no block and no ``PUSH``/``POP`` lanes — which is every
#: entry in this registry except the one being measured, and is why adding it
#: changes nothing anywhere else.
#:
#: ``col`` is the whole design freedom the block has, and it exists because §7.1
#: gives a lane no other lever: both spill glyphs are their lane's *first* band
#: glyph, so without a column they sit at ``lane_x0``, where the ROM pipe on the
#: west wall beats anything hanging off the east one. Walking them east trades a
#: couple of ticks of lane walking for the binding.
SPILL_LAYOUT: dict[tuple[str, str], dict[str, int]] = {}


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
    lane_pitch: int | None = None,
    rom_touch_drop: int | None = None,
    squash_band: bool | int | None = None,
    straight_trie: bool | None = None,
    high_collector: bool | None = None,
    trie_slack_rows: tuple[int, ...] | None = None,
    tight_trie_cols: bool | None = None,
    fetch_fold: bool | None = None,
    fetch_tuck: bool | None = None,
    lean_trie: bool | str | None = None,
    high_drops_free: bool | None = None,
    tuck_drops: bool | None = None,
    fold_lanes: bool | None = None,
    spill: Mapping[str, int] | None = None,
    middle_order: Sequence[str] | None = None,
    opcode_slots: Mapping[str, int] | None = None,
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
        middle_order=middle_order if middle_order is not None else LANE_ORDER.get(slug),
        opcode_slots=(OPCODE_SLOTS.get((slug, store)) if opcode_slots is None else opcode_slots),
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
        store_feed_share_riser=(slug, store) in TAPED_FEED_SHARE_RISER,
        store_bank_lift=TAPED_BANK_LIFT.get((slug, store), 0),
        store_feed_tuck=TAPED_FEED_TUCK.get((slug, store), 0),
        store_bank_west_grow=TAPED_BANK_WEST_GROW.get((slug, store), 0),
        store_rotate_banks=TAPED_ROTATE_BANKS.get((slug, store), ()),
        store_request_reach=(slug, store) in STORE_REQUEST_REACH,
        store_request_tuck=(slug, store) in STORE_REQUEST_TUCK,
        adapter_form=ADAPTER_FORM.get((slug, store), "wide"),
        store_protocol=TAPED_PROTOCOL.get((slug, store), "v3"),
        store_request_west=(slug, store) in STORE_REQUEST_WEST,
        store_riser_lift=STORE_RISER_LIFT.get((slug, store), 0),
        store_compact_gate=(slug, store) in TAPED_COMPACT_GATE,
        store_collector_fast=(slug, store) in TAPED_COLLECTOR_FAST,
        store_tight_gate=(slug, store) in TAPED_TIGHT_GATE,
        store_gate_return_slack=TAPED_GATE_RETURN_SLACK.get((slug, store)),
        store_gate_park_const=(slug, store) in TAPED_GATE_PARK_CONST,
        store_gate_south_reuse_b=(slug, store) in TAPED_GATE_SOUTH_REUSE_B,
        store_tape_park_const=(slug, store) in TAPED_TAPE_PARK_CONST,
        store_tape_tight_ring=(slug, store) in TAPED_TIGHT_RING,
        store_bank_order=TAPED_BANK_ORDER.get((slug, store)),
        trim_dead=(slug in TRIM_DEAD_LANES) if trim_dead is None else trim_dead,
        seek=_seek,
        seek_teleport=_seek and (slug, store) in SEEK_TELEPORT,
        seek_attach_low=(slug, store) in SEEK_ATTACH_LOW,
        in_west=INPUT_NORTH_WEST.get((slug, store), 0),
        seek_taken_drop_east=_seek and (slug, store) in SEEK_TAKEN_DROP_EAST,
        seek_twin_station=_seek and (slug, store) in SEEK_TWIN_STATION,
        seek_ops=SEEK_OPS_FOR.get(slug, SEEK_OPS),
        top_bus=(slug in TOP_RETURN_BUS) if top_bus is None else top_bus,
        store_shape=STORE_SHAPE.get(slug),
        doom_loop_row=DOOM_LOOP_ROW.get((slug, store)),
        doom_leaf_cols=DOOM_LEAF_COLS.get((slug, store)),
        doom_cluster_lift=DOOM_CLUSTER_LIFT.get((slug, store), 0),
        doom_north_up=DOOM_PACK_NORTH_UP.get((slug, store), 0),
        doom_north_west=DOOM_PACK_NORTH_WEST.get((slug, store), False),
        rom_touch_drop=(
            ROM_TOUCH_DROP.get((slug, store), 0)
            if rom_touch_drop is None
            else rom_touch_drop
        ),
        tuck_drops=(
            (slug, store) in TUCKED_DROPS if tuck_drops is None else tuck_drops
        ),
        fold_lanes=(
            (slug, store) in FOLDED_LANES if fold_lanes is None else fold_lanes
        ),
        squash_band=(
            SQUASH_BAND.get((slug, store), 0) if squash_band is None else squash_band
        ),
        lane_pitch=(
            LANE_PITCH.get((slug, store), 2) if lane_pitch is None else lane_pitch
        ),
        straight_trie=(
            (slug, store) in STRAIGHT_TRIE if straight_trie is None else straight_trie
        ),
        high_collector=(
            (slug, store) in HIGH_COLLECTOR if high_collector is None else high_collector
        ),
        trie_slack_rows=(
            TRIE_SLACK_ROWS.get((slug, store), ())
            if trie_slack_rows is None
            else trie_slack_rows
        ),
        tight_trie_cols=(
            (slug, store) in TIGHT_TRIE_COLS if tight_trie_cols is None else tight_trie_cols
        ),
        fetch_fold=((slug, store) in FETCH_FOLD if fetch_fold is None else fetch_fold),
        fetch_tuck=((slug, store) in FETCH_TUCK if fetch_tuck is None else fetch_tuck),
        lean_trie=(
            LEAN_TRIE.get((slug, store), False) if lean_trie is None else lean_trie
        ),
        high_drops_free=(
            (slug, store) in HIGH_DROPS_FREE if high_drops_free is None else high_drops_free
        ),
        spill=SPILL_LAYOUT.get((slug, store)) if spill is None else spill,
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
