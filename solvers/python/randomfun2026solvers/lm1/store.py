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

__all__ = ["Store", "StoreError", "DictStore", "SpillRing", "StreamUnit", "SnakeUnit"]

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
