"""Op-level DSL + simulator for the pathfinder dataflow machine.

One worker little man (A, B, BP), a long RING fifo (the board state), a short
SPILL fifo (temporaries), the input pipe IN and the painter pipe PAINT.

Every op maps 1:1 onto a littleman glyph, so the op trace is the tick estimate
and the layout step is mechanical.
"""
from __future__ import annotations

from collections import deque

MASK = (1 << 64) - 1


def s64(v: int) -> int:
    v &= MASK
    return v - (1 << 64) if v >> 63 else v


class Prog:
    """A structured op list with labels; jumps are by label name."""

    def __init__(self) -> None:
        self.ops: list[tuple] = []
        self.labels: dict[str, int] = {}

    # -- emit ---------------------------------------------------------------
    def op(self, *o) -> None:
        self.ops.append(o)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise KeyError(f"duplicate label {name}")
        self.labels[name] = len(self.ops)
        self.ops.append(("nop",))

    def lit(self, n: int) -> None:
        self.op("lit", n)

    def jmp(self, name: str) -> None:
        self.op("jmp", name)

    def brs(self, neg: str | None, zero: str | None, pos: str | None) -> None:
        """X: three-way branch on sign(A). None = fall through."""
        self.op("brs", neg, zero, pos)

    def bpz(self, name: str) -> None:
        """d/a: if BP > 0 jump to name, else fall through."""
        self.op("bpz", name)

    def resolve(self) -> None:
        for i, o in enumerate(self.ops):
            if o[0] == "jmp" and o[1] not in self.labels:
                raise KeyError(f"op {i}: unknown label {o[1]}")
            if o[0] == "brs":
                for t in o[1:]:
                    if t is not None and t not in self.labels:
                        raise KeyError(f"op {i}: unknown label {t}")
            if o[0] == "bpz" and o[1] not in self.labels:
                raise KeyError(f"op {i}: unknown label {o[1]}")


class Halt(Exception):
    pass


class VM:
    def __init__(self, prog: Prog, inp: list[int], *, trace: bool = False) -> None:
        prog.resolve()
        self.p = prog
        self.A = 0
        self.B = 0
        self.BP = 0
        self.ring: deque[int] = deque()
        self.spill: deque[int] = deque()
        self.inp = deque(inp)
        self.paint: list[int] = []
        self.pc = 0
        self.ops = 0
        self.trace = trace

    def recv(self, pipe: str) -> int:
        q = {"ring": self.ring, "spill": self.spill, "in": self.inp}[pipe]
        if not q:
            raise RuntimeError(f"deadlock: {pipe} empty at pc={self.pc}")
        return q.popleft()

    def send(self, pipe: str, v: int) -> None:
        if pipe == "paint":
            self.paint.append(v)
        else:
            {"ring": self.ring, "spill": self.spill}[pipe].append(v)

    def step(self) -> None:
        o = self.p.ops[self.pc]
        k = o[0]
        self.pc += 1
        self.ops += 1
        A, B = self.A, self.B
        if k == "nop":
            self.ops -= 1
        elif k == "lit":
            self.A = o[1]
        elif k == "M":
            self.B = A
        elif k == "W":
            self.A, self.B = B, A
        elif k == "N":
            self.A = s64(-A)
        elif k == "+":
            self.A = s64(A + B)
        elif k == "-":
            self.A = s64(A - B)
        elif k == "*":
            self.A = s64(A * B)
        elif k == "&":
            self.A = s64((A & MASK) & (B & MASK))
        elif k == "|":
            self.A = s64((A & MASK) | (B & MASK))
        elif k == "~":
            self.A = s64((A & MASK) ^ (B & MASK))
        elif k == "{":
            self.A = 0 if not (0 <= B <= 63) else s64(A << B)
        elif k == "}":
            if B < 0:
                self.A = 0
            else:
                self.A = s64(A >> min(B, 63))
        elif k == "b":
            self.BP = A
        elif k == "m":
            self.BP -= 1
        elif k == "r":
            self.A = self.recv(o[1])
        elif k == "s":
            self.send(o[1], A)
        elif k == "jmp":
            self.pc = self.p.labels[o[1]]
        elif k == "brs":
            neg, zero, pos = o[1], o[2], o[3]
            t = neg if A < 0 else (zero if A == 0 else pos)
            if t is not None:
                self.pc = self.p.labels[t]
        elif k == "bpz":
            if self.BP > 0:
                self.pc = self.p.labels[o[1]]
        elif k == "H":
            raise Halt()
        else:
            raise KeyError(f"bad op {o}")
        if self.trace:
            print(f"{self.pc-1:4} {o!s:24} A={self.A} B={self.B} BP={self.BP} "
                  f"ring={len(self.ring)} spill={len(self.spill)}")

    def run(self, limit: int = 40_000_000) -> None:
        try:
            while self.ops < limit:
                self.step()
        except Halt:
            return
        raise RuntimeError("op limit")


class Display:
    """LM-75 model driven by the painter protocol (see painter())."""

    def __init__(self, w: int = 16, h: int = 16) -> None:
        self.w, self.h = w, h
        self.cur = [[0] * w for _ in range(h)]
        self.nxt = [[0] * w for _ in range(h)]
        self.cx = self.cy = 0
        self.frames: list[list[str]] = []

    def addr(self, v: int) -> None:
        if not 0 <= v < self.w * self.h:
            raise ValueError(f"addr {v}")
        self.cy, self.cx = divmod(v, self.w)

    def data(self, v: int) -> None:
        if not 0 <= v <= 15:
            raise ValueError(f"data {v}")
        self.nxt[self.cy][self.cx] = v
        self.cx += 1
        if self.cx == self.w:
            self.cx = 0
            self.cy += 1
            if self.cy == self.h:
                self.cy = 0

    def swap(self, v: int) -> None:
        self.cur = [row[:] for row in self.nxt]
        if v == 0:
            self.nxt = [[0] * self.w for _ in range(self.h)]
            self.cx = self.cy = 0
        self.frames.append(["".join("%x" % c for c in row) for row in self.cur])


def painter(stream: list[int], raw: int = 256) -> Display:
    """The painter man: `raw` DATA values, then (addr, colour) pairs with a
    negative value meaning SWAP<-1."""
    d = Display()
    it = iter(stream)
    for _ in range(raw):
        d.data(next(it))
    for v in it:
        if v < 0:
            d.swap(1)
        else:
            d.addr(v)
            d.data(next(it))
    return d
