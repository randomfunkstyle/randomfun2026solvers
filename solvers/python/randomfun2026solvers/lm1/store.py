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
    "DoomWall",
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

    One command word per request, ``arg * 2**trie_bits + code``: exactly what the
    real unit's decode trie reads, down to using *floored* division for the
    argument so a negative one survives (``SPEC.md``'s ``/``) and a raw low-bit
    test for the code.

    ``trie_bits`` is a property of *this instance* — of how the unit was built —
    not of the word it is handed, because that is what the real hardware's decode
    trie is: fixed depth, wired in once. ``matmul`` was built against a depth-3
    trie (``8 * arg + code``, the original eight arms, codes 0..7) and must see
    that forever; this machine's training loop needs four more arms and is built
    against a depth-4 trie (``16 * arg + code``, codes 0..11, four spare leaves).
    There is deliberately no single decode that serves both widths at once: at 3
    bits, ``word % 8`` is always < 8, so codes 8..15 are structurally unreachable
    (``PUSHA`` with ``arg=42`` encodes to word 344, which a 3-bit trie reads back
    as ``EMIT(43)``); at 4 bits, every *existing* word built from an odd ``arg``
    aliases into a different arm (``FILLA`` with ``arg=35`` is word 283 — ``(35,
    3)`` at mod 8, ``(17, 11)`` at mod 16 — and ``matmul``'s shipped cases exercise
    odd ``arg`` for exactly this reason). No residue-sniffing hybrid rescues
    either direction, so the width is chosen once, at construction, like the real
    trie is.

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
    #: leaf and this has to move with it. Codes 0..7 are the original eight — the
    #: only ones a depth-3 (``trie_bits=3``) unit, i.e. ``matmul``'s, ever sees.
    #: Codes 8..11 are new, reachable only from a depth-4 (``trie_bits=4``) unit;
    #: 12..15 are spare leaves on that trie, deliberately left unassigned.
    #: ``UPDB``'s shift when a program does not declare one.
    DEFAULT_LR_SHIFT = 12

    CODES = {
        "EMIT": 0,
        "FILLB": 1,
        "ZEROC": 2,
        "FILLA": 3,
        "FWD": 4,
        "DRAINB": 5,
        "MAC": 6,
        "RDIN": 7,
        "PUSHA": 8,
        "ROTB": 9,
        "RDP": 10,
        "UPDB": 11,
    }

    def __init__(
        self,
        read_input: Callable[[], int],
        emit: Callable[[int], None],
        trie_bits: int = 3,
        lr_shift: int | None = None,
    ) -> None:
        self._read_input = read_input
        self._out = emit
        self.trie_bits = trie_bits
        self.ring_a: deque[int] = deque()  # A, row-major, drained
        self.ring_b: deque[int] = deque()  # B, row-major, rotated
        self.p1: deque[int] = deque()  # accumulator: ADDER -> unit
        self.p2: deque[int] = deque()  # accumulator: unit -> ADDER
        self.words = 0  # words across the CPU's two pipes (the tick model bills these)
        self.macs = 0  # multiply-accumulates performed inside the unit
        self.high_a = 0
        self.high_b = 0
        self.high_c = 0
        #: ``UPDB``'s shift. It is a property of the *built* unit and not a field of
        #: any command word, so a program that needs a particular one has to declare
        #: it (``asm.STREAM_LR_SHIFT_EQU``) and the builder has to honour it —
        #: running a program against a unit shifted differently is wrong arithmetic
        #: with nothing to catch it.
        self.lr_shift = self.DEFAULT_LR_SHIFT if lr_shift is None else lr_shift
        self._scalar_a = 0  # the scalar most recently pushed by PUSHA, for UPDB
        self._replies: deque[int] = deque()
        #: The full twelve-leaf table. A ``trie_bits=3`` unit only keeps the codes
        #: that fit in 3 bits (0..7) — the same eight arms it has always had — so a
        #: word decoded on it can never resolve to a code outside that set.
        all_arms: dict[int, Callable[[int], int | None]] = {
            self.CODES["RDIN"]: self._rdin,
            self.CODES["FILLA"]: self._filla,
            self.CODES["FILLB"]: self._fillb,
            self.CODES["DRAINB"]: self._drainb,
            self.CODES["ZEROC"]: self._zeroc,
            self.CODES["MAC"]: self._mac,
            self.CODES["FWD"]: self._fwd,
            self.CODES["EMIT"]: self._emit_row,
            self.CODES["PUSHA"]: self._pusha,
            self.CODES["ROTB"]: self._rotb,
            self.CODES["RDP"]: self._rdp,
            self.CODES["UPDB"]: self._updb,
        }
        width = 1 << trie_bits
        self._arms = {code: fn for code, fn in all_arms.items() if code < width}

    # ── wire protocol ────────────────────────────────────────────────────────
    def command(self, word: int) -> int | None:
        """Decode and run one command word against *this unit's* trie width.

        The decode is ``arg, code = divmod(word, 2**trie_bits)`` — floored, so a
        negative argument survives, exactly as the hardware's ``/`` does. This is
        the single decode path; :meth:`send` (``matmul``'s entry point) delegates
        here rather than duplicating it.
        """
        self.words += 1
        arg, code = divmod(word, 1 << self.trie_bits)
        result = self._dispatch(code, arg)
        self.high_a = max(self.high_a, len(self.ring_a))
        self.high_b = max(self.high_b, len(self.ring_b))
        self.high_c = max(self.high_c, len(self.p1), len(self.p2))
        return result

    def _dispatch(self, code: int, arg: int) -> int | None:
        """Run one already-decoded ``(code, arg)`` pair, or refuse a code this trie lacks.

        A well-formed word can never *decode* to a code this wide's ``divmod``
        cannot produce, so this branch is unreachable from :meth:`command` alone —
        that is exactly the point proven in this task's design notes (a 3-bit
        trie's ``word % 8`` is always < 8; there is no word that means "code 8" to
        it). This guard exists for the seam itself: any caller — direct dispatch,
        a future Task 4 helper — that hands a decoded code the unit's trie was
        never built to carry gets a clear refusal instead of a silent wrong arm.
        """
        if code not in self._arms:
            raise StoreError(
                f"STREAM: code {code} has no arm on a {self.trie_bits}-bit trie"
            )
        return self._arms[code](arg)

    def send(self, word: int) -> None:
        """``matmul``'s entry point: unchanged behaviour, delegating to :meth:`command`."""
        self.command(word)

    def recv(self) -> int:
        if not self._replies:
            raise StoreError("STREAM: response pipe empty (RCV would block forever)")
        self.words += 1
        return self._replies.popleft()

    # ── arms (the original eight) ───────────────────────────────────────────
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

    # ── arms (the four new ones: push, rotate-tap, read-partial, update-in-place) ──
    def _pusha(self, v: int) -> None:
        """Put one CPU-computed scalar on ring A and remember it for a following UPDB."""
        self.ring_a.append(v)
        self._scalar_a = v

    def _rotb(self, n: int) -> None:
        """Rotate ring B by ``n`` without touching the accumulator — a tap-offset move."""
        for _ in range(n):
            self.ring_b.append(self._pop(self.ring_b, "ring B"))

    def _rdp(self, _arg: int) -> int:
        """Pop one partial sum off P1 and answer it, matching :meth:`_rdin`'s shape.

        The reply queue is the authoritative path — that is what a real program's
        ``SND`` + ``RCV`` pair reads from, and the only thing ``RCV`` ever reads
        from (:meth:`recv`, unchanged). The direct return is additionally
        convenient for a test calling :meth:`command` straight, and harmless,
        because it is the same value.
        """
        value = self._pop(self.p1, "the accumulator")
        self._replies.append(value)
        return value

    def _updb(self, n: int) -> None:
        """A rank-one weight update: ``W[j] -= (a * g[j]) >> lr_shift``, ``n`` times.

        ``g`` circulates onto P2 rather than being consumed, because the same
        gradient ring feeds every weight row in the training loop, not just this
        one pass over ring B.
        """
        a = self._scalar_a
        for _ in range(n):
            b = self._pop(self.ring_b, "ring B")
            g = self._pop(self.p1, "the accumulator")
            self.p2.append(g)
            self.ring_b.append(b - ((a * g) >> self.lr_shift))

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

    Five commands (codes read off the trie; ``tests/test_deadman3d.py`` pins them
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
        RUN    count*16 + colour: ``count`` bare DATA writes of ``colour`` at
               the panel's own cursor (row-major auto-advance) — the title
               screen is ~1k of these, one word per RLE run; the live HUD is
               a CURS plus ~14 of them per frame.
        CURS   addr: reposition the panel cursor (one bare ADDR write) — what
               lets the CPU paint arbitrary RLE (the HUD strip, the bars)
               with RUN words.
        GUN    0   the baked idle pistol sprite (one ADDR + DATA run per
               sprite row), bottom-centre over the finished columns.
        GUNF   0   the recoil variant: the pistol a row higher with the
               muzzle flash blooming above it (V1's bare diamond, retired).
        COMMIT 0   SWAP 0 — commit the frame, clear ``next``, reset the cursor.

    The models mirror ``deadman3d.GUN_IDLE``/``GUN_FIRE`` by construction —
    the unit bakes both sprites, which is the whole point.
    """

    #: arm -> command code, read off the 3-bit trie's geometry (a west branch
    #: is a set bit; ``d3_unit.arm_codes`` derives these and the tests pin the
    #: two tables). COL is 0 so the CPU's per-column send is a bare
    #: ``MULI 8; SND`` with no ``ADDI`` for the code; 5 and 2 are the two
    #: spare leaves. RUN lives on an eastern leaf (its arm's ``r`` must beat
    #: the cmd pipe — rule 2), the write-only sprite arms fill the west —
    #: GUN on leaf 1 since V5 (code 3): the Freedoom-derived sprite's 12 runs
    #: outgrew leaf 2's ten columns of headroom before CURS.
    CODES = {"COL": 0, "CURS": 1, "RUN": 4, "GUN": 3, "GUNF": 6, "COMMIT": 7}

    #: One-line contract per arm, quoted into the generated asm's ``.equ C_*`` notes.
    ARM_NOTES = {
        "COL": "arg=((top*64+col)*16+colour-1024)*64 + (bot-top+1): wall, then floor",
        "RUN": "arg=count*16+colour: count pixels at the panel's own cursor",
        "CURS": "arg=addr: reposition the panel cursor (the RLE painter's ADDR)",
        "GUN": "arg=0: the baked idle pistol sprite (rows 30..39)",
        "GUNF": "arg=0: the recoil pistol + muzzle flash (rows 25..38)",
        "COMMIT": "arg=0: SWAP 0 — commit the frame, clear next, reset the cursor",
    }

    #: The pistol sprites, (row, first column, hex colours) per contiguous
    #: run — duplicated from ``deadman3d.GUN_IDLE``/``GUN_FIRE`` (the tests
    #: pin the tables equal) to keep this module free of the display model.
    #: V5: derived from Freedoom's pisga0/pisfa0 (see deadman3d's credits).
    GUN_IDLE = (
        (30, 32, "7"), (31, 31, "770"), (32, 30, "77770"),
        (33, 29, "7700007"), (34, 29, "710101"), (34, 35, "7"),
        (35, 29, "7777770"), (36, 28, "77000077"), (37, 28, "33088033"),
        (38, 27, "0333333338"), (39, 27, "033333388"), (39, 36, "0"),
    )
    GUN_FIRE = (
        (25, 31, "9bb9"), (26, 30, "bffffb"), (27, 29, "3bffff"),
        (27, 35, "b3"), (28, 31, "9ff9"),
        (29, 32, "7"), (30, 31, "770"), (31, 30, "77770"),
        (32, 29, "7700007"), (33, 29, "710101"), (33, 35, "7"),
        (34, 29, "7777770"), (35, 28, "77000077"), (36, 28, "33088033"),
        (37, 27, "0333333338"), (38, 27, "033333388"), (38, 36, "0"),
    )

    #: ``display.py``'s port numbers, repeated rather than imported to keep this
    #: module free of the display model.
    ADDR, DATA, SWAP = 0, 1, 2

    WIDTH, H3D = 64, 40  # panel columns; viewport rows 0..39 (HUD below)
    FLOOR = 8

    def __init__(
        self,
        write_display: Callable[[int, int], None],
        *,
        floor_row: int = H3D - 1,
    ) -> None:
        # ``floor_row`` is the last panel row COL's floor run fills — the two-digit
        # literal baked into the real arm (``d3_unit.FLOOR_ROW``). It is 39 on the
        # 64x48 panel and per-tile-row on the tiled wall, where the 128x96 viewport
        # ends inside the bottom tiles rather than at the panel's own bottom.
        self._floor_row = floor_row
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
        elif code == self.CODES["RUN"]:
            self._run(arg)
        elif code == self.CODES["CURS"]:
            self._write(self.ADDR, arg)
        elif code == self.CODES["GUN"]:
            self._sprite(self.GUN_IDLE)
        elif code == self.CODES["GUNF"]:
            self._sprite(self.GUN_FIRE)
        elif code == self.CODES["COMMIT"]:
            self._write(self.SWAP, 0)
            self.frames += 1
        else:
            raise StoreError(f"DOOM: no arm for command code {code}")

    def recv(self) -> int:
        raise StoreError("DOOM: the unit answers nothing; a program with RCV cannot bind")

    # ── arms ─────────────────────────────────────────────────────────────────
    #: The banded wall loop's mask ring (V3): every 4th painted row is ANDed
    #: down to the dark shade — the horizontal seam of a wall panel. The real
    #: unit circulates these four masks through its second value ring, reseeded
    #: per command so the seam phase anchors at the wall run's top row.
    MASKS = (7, 15, 15, 15)

    def _col(self, arg: int) -> None:
        """One viewport column, computed exactly as the unit's own loops do.

        The wall loop circulates ``v = addr*16 + colour`` and adds 1024 (one row
        of 64 cells, times 16) per lap *before* painting; each painted colour is
        ANDed with the lap's mask from :data:`MASKS` (the banding seam); the
        floor loop then continues from the last wall address with the baked
        colour 8.
        """
        n_wall = arg % 64  # floored like the unit's `/ 64`: 0..63 even for seed < 0
        v = arg // 64  # seed = first wall pixel's addr*16+colour, one lap early
        if n_wall < 1:
            raise StoreError(f"DOOM: COL with an empty wall run (arg {arg})")
        addr = 0
        for i in range(n_wall):
            v += 1024
            addr, colour = v // 16, v % 16
            if not 0 <= addr < self.WIDTH * (self._floor_row + 1):
                raise StoreError(f"DOOM: COL wall pixel {addr} is outside the viewport")
            self._paint(addr, colour & self.MASKS[i % 4])
        for _ in range(self._floor_row - addr // self.WIDTH):
            addr += self.WIDTH
            self._paint(addr, self.FLOOR)

    def _run(self, arg: int) -> None:
        """One RLE run at the panel's own cursor, exactly as the unit's loop
        does it: BP = count laps of one bare DATA write of the colour."""
        count, colour = arg // 16, arg % 16
        if count < 1:
            raise StoreError(f"DOOM: RUN with an empty run (arg {arg})")
        for _ in range(count):
            self._write(self.DATA, colour)
            self.pixels += 1

    def _sprite(self, runs: tuple[tuple[int, int, str], ...]) -> None:
        """A baked pistol sprite: one ADDR reposition per contiguous row run,
        then its colours at the cursor's own auto-advance."""
        for row, col, colors in runs:
            self._write(self.ADDR, row * self.WIDTH + col)
            for ch in colors:
                self._write(self.DATA, int(ch, 16))
                self.pixels += 1

    # ── helpers ──────────────────────────────────────────────────────────────
    def _paint(self, cell: int, colour: int) -> None:
        self._write(self.ADDR, cell)
        self._write(self.DATA, colour)
        self.pixels += 1


class DoomWall:
    """A model of ``d3_router.py``'s tiled wall — four :class:`DoomUnit`s, one lane.

    The LM-75's interior stops at 64x64 (``SPEC.md``), so a 128x96 framebuffer has
    to be four panels.  The wall is the unmodified DOOM unit instantiated four
    times behind a 1-of-4 router, and the wire format is the unit's own with a
    selector in the low three bits::

        router word = 8 * (unit word) + sel = 8 * (8 * arg + code) + sel

    :data:`SEL` names the destinations.  ``T0..T3`` are the tiles, ``ALL`` is the
    router's broadcast leaf — an ``S``, which ``SPEC.md`` defines as *"send A into
    every outgoing pipe at once ... never writes to only some"*.

    Why the broadcast matters.  Each panel commits on its own SWAP, so four
    separate COMMITs would leave the wall showing a frame half-old.  ``S`` makes
    the four COMMIT words leave on **one tick**, which guarantees the property the
    composed image actually depends on: every panel has committed exactly the same
    number of frames in the same order, so tile frame *N* always belongs to
    logical frame *N* (:func:`display.tiled_frames_from_writes` composes by
    index).  Tick-level alignment is weaker and separate — see that module and the
    router's own notes.
    """

    #: Destination -> selector, read off the router's trie (``d3_router.SEL``);
    #: repeated here so the emulator model stays free of the generator, and pinned
    #: equal by the tests exactly as ``CODES`` is against ``d3_unit.arm_codes``.
    SEL = {"T2": 7, "T3": 3, "T0": 5, "T1": 1, "ALL": 6}

    #: The logical framebuffer, and the tile grid it is cut into.
    WIDTH, HEIGHT = 128, 96
    TILE_W, TILE_H = DoomUnit.WIDTH, 48

    #: The 3D viewport is logical rows 0..79 and the HUD is 80..95, so COL's floor
    #: run ends at a different panel row on a top tile than on a bottom one; pinned
    #: equal to ``d3_router.TILE_FLOOR_ROW`` by the tests.
    FLOOR_ROW = (47, 47, 31, 31)

    def __init__(self, write_display: Callable[[int, int, int], None]) -> None:
        self._write = write_display
        self.units = [
            DoomUnit(
                lambda port, value, t=tile: write_display(t, port, value),
                floor_row=row,
            )
            for tile, row in enumerate(self.FLOOR_ROW)
        ]
        self.words = 0
        self._by_sel = {v: k for k, v in self.SEL.items()}

    @property
    def pixels(self) -> int:
        return sum(u.pixels for u in self.units)

    @property
    def frames(self) -> int:
        """Commits per tile — one number, because the broadcast keeps them equal."""
        counts = {u.frames for u in self.units}
        if len(counts) != 1:
            raise StoreError(f"DOOM wall: the tiles have committed {counts} frames each")
        return counts.pop()

    # ── wire protocol ────────────────────────────────────────────────────────
    def send(self, word: int) -> None:
        """One router word: ``8 * unit_word + sel``, floored so a negative COL
        seed survives (the unit's own ``/`` is floored for the same reason)."""
        self.words += 1
        sel, payload = word % 8, word // 8
        dest = self._by_sel.get(sel)
        if dest is None:
            raise StoreError(f"DOOM wall: no leaf for selector {sel}")
        if dest == "ALL":
            for unit in self.units:
                unit.send(payload)
        else:
            self.units[int(dest[1])].send(payload)

    def recv(self) -> int:
        raise StoreError("DOOM wall: the units answer nothing; a program with RCV cannot bind")
