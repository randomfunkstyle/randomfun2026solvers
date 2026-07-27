"""The STORE block — memory, deliberately abstract (``ARCH.md`` §4.1).

The wire protocol is the ``memory`` problem's protocol *verbatim*, so a correct
``memory`` solution drops straight in with no translation:

===========================  ==========================
request words (in)           response words (out)
===========================  ==========================
``0 addr``          (READ)   one word: ``store[addr]``
``1 addr value``    (WRITE)  none
===========================  ==========================

:class:`Store` is the port contract (1 in, 1 out, one word at a time — exactly
what the CPU's ``s→mem`` / ``r→mem`` glyphs can express). :class:`DictStore` is
the throwaway stub; a delay-line implementation, or the real ``memory`` grid
driven through :mod:`randomfun2026solvers.littleman`, can replace it without the
emulator noticing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable

__all__ = [
    "Store",
    "StoreError",
    "DictStore",
    "SpillRing",
    "StreamUnit",
    "SnakeUnit",
    "PathUnit",
    "DoomUnit",
]

READ = 0
WRITE = 1


class StoreError(RuntimeError):
    """Protocol misuse: a bad opcode word, or a read with nothing to return."""


class Store(ABC):
    """A word-stream memory device with one request port and one response port."""

    @abstractmethod
    def send(self, word: int) -> None:
        """Push one request word into the ``req`` pipe."""

    @abstractmethod
    def recv(self) -> int:
        """Pop one word from the ``resp`` pipe (blocking in hardware)."""

    @property
    @abstractmethod
    def words_exchanged(self) -> int:
        """Total words that crossed either port — the tick model bills these."""


class DictWireMixin:
    """The ``0 addr`` / ``1 addr value`` state machine, over a mapping."""

    def __init__(self) -> None:
        self._pending: list[int] = []
        self._replies: deque[int] = deque()
        self._exchanged = 0

    # subclasses provide storage ------------------------------------------------
    def _read(self, addr: int) -> int:
        raise NotImplementedError

    def _write(self, addr: int, value: int) -> None:
        raise NotImplementedError

    # wire protocol ------------------------------------------------------------
    def send(self, word: int) -> None:
        self._exchanged += 1
        self._pending.append(word)
        head = self._pending[0]
        if head == READ:
            if len(self._pending) == 2:
                self._replies.append(self._read(self._pending[1]))
                self._pending.clear()
        elif head == WRITE:
            if len(self._pending) == 3:
                self._write(self._pending[1], self._pending[2])
                self._pending.clear()
        else:
            self._pending.clear()
            raise StoreError(f"STORE: request opcode must be 0 (read) or 1 (write), got {head}")

    def recv(self) -> int:
        if not self._replies:
            raise StoreError("STORE: response pipe empty (a read would block forever)")
        self._exchanged += 1
        return self._replies.popleft()

    @property
    def words_exchanged(self) -> int:
        return self._exchanged

    @property
    def mid_request(self) -> bool:
        """True when a request is half-sent (useful for assertions)."""
        return bool(self._pending)


class DictStore(DictWireMixin, Store):
    """Dict-backed stub. Unwritten cells read as 0, per ``ARCH.md`` §4.1."""

    def __init__(self, cells: dict[int, int] | None = None, *, size: int | None = None) -> None:
        super().__init__()
        self.cells: dict[int, int] = dict(cells or {})
        self.size = size

    def _check(self, addr: int) -> None:
        if addr < 0:
            raise StoreError(f"STORE: negative address {addr}")
        if self.size is not None and addr >= self.size:
            raise StoreError(f"STORE: address {addr} outside 0..{self.size - 1}")

    def _read(self, addr: int) -> int:
        self._check(addr)
        return self.cells.get(addr, 0)

    def _write(self, addr: int, value: int) -> None:
        self._check(addr)
        self.cells[addr] = value

    def snapshot(self) -> dict[int, int]:
        return dict(self.cells)


class SpillRing:
    """The SPILL pipe: a LIFO scratch slot the CPU can park one word in.

    Not part of ``ARCH.md`` — see the step-2 report. Physically it is a two-room
    ring (or a pipe pair) hanging off the CPU, addressed by ``s→spill`` /
    ``r→spill``. Indirect load/store are impossible without it, because the
    literal glyph that starts a STORE request (``0``/``1``) clobbers ``A`` while
    ``B`` still holds ACC.
    """

    def __init__(self) -> None:
        self._values: list[int] = []
        self.high_water = 0

    def push(self, word: int) -> None:
        self._values.append(word)
        self.high_water = max(self.high_water, len(self._values))

    def pop(self) -> int:
        if not self._values:
            raise StoreError("SPILL: pop from an empty spill ring")
        return self._values.pop()

    def __len__(self) -> int:
        return len(self._values)


class StreamUnit:
    """A model of ``stream.py``'s STREAM block — three rings and an adder.

    One command word per request, ``8 * arg + code``: exactly what the real unit's
    decode trie reads, down to using *floored* division for the argument so a
    negative one survives (``SPEC.md``'s ``/``) and a raw low-bit test for the code.

    The rings are plain deques, because a rotate-only FIFO is nothing more than
    that. What the model does *not* have is the hardware's finite capacity, so it
    records high-water marks instead and the generator sizes the real pipes from
    the problem's maximum; ``tests/test_lm1_matmul.py`` asserts the two agree.

    ``read_input``/``emit`` are the emulator's own hooks. On the real machine the
    unit owns the ``I`` and ``O`` rooms — that is what lets a 256-value fill be one
    command instead of a 256-iteration ROM loop — so program input and output flow
    through here and not through the CPU.
    """

    #: arm -> command code, mirroring :func:`stream.arm_codes`, which derives them
    #: from the trie's geometry. The tests assert the two tables are equal: move a
    #: leaf and this has to move with it.
    CODES = {
        "EMIT": 0,
        "FILLB": 1,
        "ZEROC": 2,
        "FILLA": 3,
        "FWD": 4,
        "DRAINB": 5,
        "MAC": 6,
        "RDIN": 7,
    }

    def __init__(self, read_input: Callable[[], int], emit: Callable[[int], None]) -> None:
        self._read_input = read_input
        self._out = emit
        self.ring_a: deque[int] = deque()  # A, row-major, drained
        self.ring_b: deque[int] = deque()  # B, row-major, rotated
        self.p1: deque[int] = deque()  # accumulator: ADDER -> unit
        self.p2: deque[int] = deque()  # accumulator: unit -> ADDER
        self.words = 0  # words across the CPU's two pipes (the tick model bills these)
        self.macs = 0  # multiply-accumulates performed inside the unit
        self.high_a = 0
        self.high_b = 0
        self.high_c = 0
        self._replies: deque[int] = deque()
        self._arms: dict[int, Callable[[int], None]] = {
            self.CODES["RDIN"]: self._rdin,
            self.CODES["FILLA"]: self._filla,
            self.CODES["FILLB"]: self._fillb,
            self.CODES["DRAINB"]: self._drainb,
            self.CODES["ZEROC"]: self._zeroc,
            self.CODES["MAC"]: self._mac,
            self.CODES["FWD"]: self._fwd,
            self.CODES["EMIT"]: self._emit_row,
        }

    # ── wire protocol ────────────────────────────────────────────────────────
    def send(self, word: int) -> None:
        """One command word: ``8 * arg + code``."""
        self.words += 1
        self._arms[word & 7](word >> 3)
        self.high_a = max(self.high_a, len(self.ring_a))
        self.high_b = max(self.high_b, len(self.ring_b))
        self.high_c = max(self.high_c, len(self.p1), len(self.p2))

    def recv(self) -> int:
        if not self._replies:
            raise StoreError("STREAM: response pipe empty (RCV would block forever)")
        self.words += 1
        return self._replies.popleft()

    # ── arms ─────────────────────────────────────────────────────────────────
    def _rdin(self, _arg: int) -> None:
        self._replies.append(self._read_input())

    def _filla(self, n: int) -> None:
        for _ in range(n):
            self.ring_a.append(self._read_input())

    def _fillb(self, n: int) -> None:
        for _ in range(n):
            self.ring_b.append(self._read_input())

    def _drainb(self, n: int) -> None:
        for _ in range(n):
            self._pop(self.ring_b, "ring B")

    def _zeroc(self, n: int) -> None:
        self.p2.extend([0] * n)

    def _mac(self, n: int) -> None:
        """One row of C, fused: pop the scalar, then ``n`` rotate-multiply-add steps."""
        scalar = self._pop(self.ring_a, "ring A")
        for _ in range(n):
            b = self._pop(self.ring_b, "ring B")
            self.ring_b.append(b)  # rotate: n pops advance B exactly one row
            self.p1.append(self._pop(self.p2, "the accumulator") + scalar * b)
            self.macs += 1

    def _fwd(self, n: int) -> None:
        for _ in range(n):
            self.p2.append(self._pop(self.p1, "the accumulator"))

    def _emit_row(self, n: int) -> None:
        for _ in range(n):
            self._out(self._pop(self.p1, "the accumulator"))

    @staticmethod
    def _pop(ring: deque[int], what: str) -> int:
        if not ring:
            raise StoreError(f"STREAM: {what} is empty; the real unit would block forever")
        return ring.popleft()


class SnakeUnit:
    """A model of ``snake_unit.py``'s body-ring coprocessor — a FIFO and a panel.

    The same ``8 * arg + code`` wire format as :class:`StreamUnit`, and the same
    reason for it: that is literally what the real unit's decode trie reads.

    Two things make this unit unlike the STREAM block, and both are forced rather
    than chosen:

    * **It answers nothing.** ``ARCH.md`` §7.1 makes an incoming pipe a rival for
      every ``r`` in the CPU, so a response pipe on the south wall competes with the
      jump slab's ROM read — measured, in all 4,800 placements of a snake program
      that had an ``RCV``. So the unit cannot report a collision; it has to *act* on
      one, which is why ``STEP`` either moves the snake or ends the game.
    * **It owns the display.** The three LM-75 ports hang off this block rather than
      off the CPU, so the CPU has no display lanes at all. Writes go through the
      emulator's own ``display_writes`` hook, which is what lets
      ``display.frames_from_writes`` grade a machine whose CPU never draws.

    The body is a ``deque`` because a rotate-only FIFO is nothing more than one. What
    the model lacks is the hardware's finite ring capacity, so it records a high-water
    mark for the generator to size the real pipes against (the snake cannot exceed 50
    cells: a growth needs a spawn round *and* a tick round).
    """

    #: arm -> command code. The real unit *reads* these off its decode trie's geometry
    #: (``snake_unit.arm_codes``), so they are not free to choose: ``STEP`` is the
    #: easternmost leaf because it is the only arm that outgrows its columns. The tests
    #: assert the two tables are equal — move a leaf and this has to move with it.
    CODES = {"STEP": 0, "FRUIT": 1, "RED": 2, "GROW": 3}

    #: ``display.py``'s port numbers, repeated rather than imported to keep this
    #: module free of the display model.
    ADDR, DATA, SWAP = 0, 1, 2

    GREEN, RED_, BLACK = 10, 9, 0

    def __init__(self, write_display: Callable[[int, int], None]) -> None:
        self._write = write_display
        self.body: deque[int] = deque()
        self.words = 0  # command words across the CPU's pipe (the tick model bills these)
        self.rotations = 0  # ring rotations, i.e. cells compared or painted
        self.high_water = 0
        self.stopped = False

    # ── wire protocol ────────────────────────────────────────────────────────
    def send(self, word: int) -> None:
        """One command word: ``8 * arg + code``."""
        if self.stopped:
            # The man halted on the losing frame. The real cmd pipe simply fills up;
            # nothing is meant to follow, since the test case has ended.
            return
        self.words += 1
        code, arg = word & 7, word >> 3
        if code == self.CODES["GROW"]:
            self._append(arg)
            self._commit()
        elif code == self.CODES["STEP"]:
            self._step(arg // 256, arg % 256)
        elif code == self.CODES["FRUIT"]:
            self._paint(arg, self.RED_)
            self._commit()
        elif code == self.CODES["RED"]:
            if arg != len(self.body):
                raise StoreError(f"SNAKE: RED {arg} against a body of {len(self.body)}")
            self.rotations += arg
            self._die()
        else:
            raise StoreError(f"SNAKE: no arm for command code {code}")

    def recv(self) -> int:
        raise StoreError("SNAKE: the unit answers nothing; a program with RCV cannot bind")

    # ── arms ─────────────────────────────────────────────────────────────────
    def _step(self, n: int, cell: int) -> None:
        """One ordinary tick: scan, then move or lose.

        The scan skips the **first** value, which is the tail. That is the rule "the
        tail moves before the head" expressed as geometry rather than as a special
        case: the cell it is vacating cannot be a collision.
        """
        if n != len(self.body):
            raise StoreError(f"SNAKE: STEP {n} against a body of {len(self.body)}")
        self.rotations += n
        if cell in list(self.body)[1:]:
            self._die()  # the body does not move: it is drawn where it was
            return
        self._paint(self.body.popleft(), self.BLACK)
        self._append(cell)
        self._commit()

    def _die(self) -> None:
        for cell in self.body:
            self._paint(cell, self.RED_)
        self._commit()
        self.stopped = True

    # ── helpers ──────────────────────────────────────────────────────────────
    def _append(self, cell: int) -> None:
        self.body.append(cell)
        self.high_water = max(self.high_water, len(self.body))
        self._paint(cell, self.GREEN)

    def _paint(self, cell: int, colour: int) -> None:
        self._write(self.ADDR, cell)
        self._write(self.DATA, colour)

    def _commit(self) -> None:
        # 1, never 0: the panel is a persistent framebuffer, so `next` and the cursor
        # survive a commit and only the pixels that changed are ever written.
        self._write(self.SWAP, 1)


class PathUnit:
    """A model of ``path_unit.py``'s board coprocessor — one remembered cell and a panel.

    The same ``8 * arg + code`` wire format as :class:`StreamUnit` and :class:`SnakeUnit`,
    because that is what the real unit's decode trie reads, and the same two structural
    rules ``ARCH.md`` §8.0 draws out of ``snake``:

    * **It answers nothing.** §7.1 makes an incoming pipe a rival for every ``r`` in the
      CPU, the jump slab's ROM read included, so a *replying* unit cannot be placed on a
      machine that has jumps — and this program is nothing but jumps. So ``recv`` raises,
      and every command is given enough authority to finish its own drawing.
    * **It owns the display.** The three LM-75 ports hang off this block, so the CPU has
      no display lanes at all — the difference between ``pathfinder.asm``'s 18 opcodes
      (a depth-5 trie) and ``pathfinder-unit.asm``'s 16 (depth 4).

    The unit's whole state is *the robot's cell*, which is what makes ``MOVE`` one command:
    the CPU sends only where the robot is going, and the unit blacks out where it was. The
    CPU therefore never has to remember the previous cell across a round, and no command
    ever carries two cell indices.

    ``CELL`` is cheaper still — it carries no address at all. The panel's cursor advances
    itself on every DATA write, so the setup round's 256 cells are 256 bare colour writes
    in row-major order, and the 256th wraps the cursor back to the upper-left for free.
    """

    #: arm -> command code. The real unit *reads* these off its decode trie's geometry, so
    #: they are not free to choose; ``tests/test_path_unit_model.py`` pins them against the
    #: ``.equ C_*`` lines in ``pathfinder-unit.asm`` so the two tables cannot drift.
    CODES = {"CELL": 0, "ROBOT": 1, "FLAG": 2, "MOVE": 3}

    #: ``display.py``'s port numbers, repeated rather than imported to keep this
    #: module free of the display model.
    ADDR, DATA, SWAP = 0, 1, 2

    #: the problem's four colours (``pathfinder_sim`` names the same numbers).
    PATH, WALL, FLAG, ROBOT = 0, 7, 9, 10

    def __init__(self, write_display: Callable[[int, int], None]) -> None:
        self._write = write_display
        self.robot: int | None = None  # the unit's only state
        self.words = 0  # command words across the CPU's pipe (the tick model bills these)
        self.cells = 0  # board cells painted at the panel's own cursor
        self.frames = 0  # commits, i.e. SWAP writes

    # ── wire protocol ────────────────────────────────────────────────────────
    def send(self, word: int) -> None:
        """One command word: ``8 * arg + code``."""
        self.words += 1
        code, arg = word & 7, word >> 3
        if code == self.CODES["CELL"]:
            self._cell(arg)
        elif code == self.CODES["ROBOT"]:
            self._robot(arg)
        elif code == self.CODES["FLAG"]:
            self._paint(arg, self.FLAG)  # no commit: the flag is not a frame of its own
        elif code == self.CODES["MOVE"]:
            self._move(arg)
        else:
            raise StoreError(f"PATH: no arm for command code {code}")

    def recv(self) -> int:
        raise StoreError("PATH: the unit answers nothing; a program with RCV cannot bind")

    # ── arms ─────────────────────────────────────────────────────────────────
    def _cell(self, value: int) -> None:
        """One board cell, at wherever the panel's cursor already is."""
        if value not in (0, 1):
            raise StoreError(f"PATH: CELL takes 0 (path) or 1 (wall), got {value}")
        self._write(self.DATA, self.WALL if value else self.PATH)
        self.cells += 1

    def _robot(self, cell: int) -> None:
        """The setup round's one frame: the robot lands, and the board is complete."""
        self._paint(cell, self.ROBOT)
        self.robot = cell
        self._commit()

    def _move(self, cell: int) -> None:
        """One step of the walk: erase, redraw, commit — one frame per move."""
        if self.robot is None:
            raise StoreError("PATH: MOVE before any ROBOT; the unit has no robot to move")
        self._paint(self.robot, self.PATH)
        self._paint(cell, self.ROBOT)
        self.robot = cell
        self._commit()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _paint(self, cell: int, colour: int) -> None:
        self._write(self.ADDR, cell)
        self._write(self.DATA, colour)

    def _commit(self) -> None:
        # 1, never 0: the panel is a persistent framebuffer, which is why a frame is only
        # two pixel writes and why the flag survives until the robot steps onto it (§4.4).
        self._write(self.SWAP, 1)
        self.frames += 1


class DoomUnit:
    """A model of ``d3_unit.py``'s column-painter coprocessor — the deadman-3d panel.

    The same ``8 * arg + code`` wire format as the other units, because that is what
    the real unit's decode trie reads, and the same two structural rules:

    * **It answers nothing.** ``ARCH.md`` §7.1 makes an incoming pipe a rival for
      every ``r`` in the CPU, the jump slab's ROM read included, so a replying unit
      cannot be placed on a machine that has jumps — and deadman-3d is nothing but
      jumps. Every command carries enough to finish its own drawing.
    * **It owns the display.** The three LM-75 ports hang off this block, so the CPU
      has no display lanes at all — its paint loops, FLASH block and 512-pixel HUD
      unroll all collapse into one command word each per column / per frame.

    Four commands (codes read off the trie; ``tests/test_deadman3d.py`` pins them
    against the CPU's ``.equ C_*`` constants so the tables cannot drift)::

        COL    seed*64 + n, where seed = (top*64 + col)*16 + color - 1024 and
               n = bot - top + 1: one viewport column — rows top..bot in
               ``color``, rows bot+1..39 in floor colour 8, each pixel an
               ADDR/DATA pair (stride 64); the ceiling above ``top`` stays black
               because COMMIT clears ``next``. The odd shape is the unit's own
               arithmetic: its wall loop circulates the packed ``addr*16+color``
               through its value ring, adding 1024 (one row) per lap, so the
               argument is that packed word pre-biased by one lap, and one
               floored ``/ 64`` recovers both fields (seed may be negative).
        FLASH  0   the 8-pixel muzzle flash (three ADDR repositions, cursor
               auto-advance inside each run).
        HUD    0   rows 40..47: one ADDR (2560), then 512 row-major DATA writes.
        COMMIT 0   SWAP 0 — commit the frame, clear ``next``, reset the cursor.

    The models mirror :data:`deadman3d.FLASH` and ``deadman3d.hud_rows()`` by
    construction — the unit bakes both patterns, which is the whole point.
    """

    #: arm -> command code. COL is 0 so the CPU's per-column send is a bare
    #: ``MULI 8; SND`` with no ``ADDI`` for the code.
    CODES = {"COL": 0, "FLASH": 1, "HUD": 2, "COMMIT": 3}

    #: One-line contract per arm, quoted into the generated asm's ``.equ C_*`` notes.
    ARM_NOTES = {
        "COL": "arg=((top*64+col)*16+colour-1024)*64 + (bot-top+1): wall, then floor",
        "FLASH": "arg=0: the baked 8-pixel muzzle diamond (rows 35..37)",
        "HUD": "arg=0: the baked 512-pixel HUD strip (rows 40..47)",
        "COMMIT": "arg=0: SWAP 0 — commit the frame, clear next, reset the cursor",
    }

    #: ``display.py``'s port numbers, repeated rather than imported to keep this
    #: module free of the display model.
    ADDR, DATA, SWAP = 0, 1, 2

    WIDTH, H3D = 64, 40  # panel columns; viewport rows 0..39 (HUD below)
    FLOOR = 8

    def __init__(self, write_display: Callable[[int, int], None]) -> None:
        self._write = write_display
        self.words = 0  # command words across the CPU's pipe
        self.pixels = 0  # pixels painted (ADDR/DATA pairs plus HUD runs)
        self.frames = 0  # commits, i.e. SWAP writes

    # ── wire protocol ────────────────────────────────────────────────────────
    def send(self, word: int) -> None:
        """One command word: ``8 * arg + code``."""
        self.words += 1
        code, arg = word & 7, word >> 3
        if code == self.CODES["COL"]:
            self._col(arg)
        elif code == self.CODES["FLASH"]:
            self._flash()
        elif code == self.CODES["HUD"]:
            self._hud()
        elif code == self.CODES["COMMIT"]:
            self._write(self.SWAP, 0)
            self.frames += 1
        else:
            raise StoreError(f"DOOM: no arm for command code {code}")

    def recv(self) -> int:
        raise StoreError("DOOM: the unit answers nothing; a program with RCV cannot bind")

    # ── arms ─────────────────────────────────────────────────────────────────
    def _col(self, arg: int) -> None:
        """One viewport column, computed exactly as the unit's own loops do.

        The wall loop circulates ``v = addr*16 + colour`` and adds 1024 (one row
        of 64 cells, times 16) per lap *before* painting; the floor loop then
        continues from the last wall address with the baked colour 8.
        """
        n_wall = arg % 64  # floored like the unit's `/ 64`: 0..63 even for seed < 0
        v = arg // 64  # seed = first wall pixel's addr*16+colour, one lap early
        if n_wall < 1:
            raise StoreError(f"DOOM: COL with an empty wall run (arg {arg})")
        addr = 0
        for _ in range(n_wall):
            v += 1024
            addr, colour = v // 16, v % 16
            if not 0 <= addr < self.WIDTH * self.H3D:
                raise StoreError(f"DOOM: COL wall pixel {addr} is outside the viewport")
            self._paint(addr, colour)
        for _ in range(self.H3D - 1 - addr // self.WIDTH):
            addr += self.WIDTH
            self._paint(addr, self.FLOOR)

    def _flash(self) -> None:
        """The baked muzzle flash: three cursor runs (deadman3d.FLASH)."""
        for addr, colors in ((2271, (11, 11)), (2334, (11, 15, 15, 11)), (2399, (11, 11))):
            self._write(self.ADDR, addr)
            for c in colors:
                self._write(self.DATA, c)
                self.pixels += 1

    def _hud(self) -> None:
        """The baked HUD strip: ADDR 2560, then 512 row-major DATA writes."""
        self._write(self.ADDR, self.H3D * self.WIDTH)
        mid = [self.FLOOR] * self.WIDTH
        for c in range(4, 13):
            mid[c] = 9  # ammo, bright red
        for c in range(28, 36):
            mid[c] = 11  # face, bright yellow
        for c in range(50, 59):
            mid[c] = 12  # armor, bright blue
        rows = [[7] * self.WIDTH] + [mid] * 6 + [[self.FLOOR] * self.WIDTH]
        for row in rows:
            for c in row:
                self._write(self.DATA, c)
                self.pixels += 1

    # ── helpers ──────────────────────────────────────────────────────────────
    def _paint(self, cell: int, colour: int) -> None:
        self._write(self.ADDR, cell)
        self._write(self.DATA, colour)
        self.pixels += 1
