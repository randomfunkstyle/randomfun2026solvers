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

__all__ = ["Store", "StoreError", "DictStore", "SpillRing"]

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
