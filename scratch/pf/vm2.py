"""Op-level model of the pathfinder dataflow machine.

Tokens are one glyph each unless noted.  The machine is one little man with
hands A/B and backpack BP, plus four pipes:

    ring   `rr` / `sr`   the 16-word board ring
    spill  `rf` / `sf`   the spill FIFO (the "third register")
    scrat  `rg` / `sg`   a short scratch FIFO (the "fourth register")
    input  `ri`          the input pipe
    paint  `sp`          the painter's command pipe

`Lnnn` loads a literal (one cell for 0..9, len+2 cells otherwise).

A block is (tokens, successor) where successor is either a block name or a
dict keyed by the lane a branch token selected.
"""
from __future__ import annotations

from collections import deque

MASK = (1 << 64) - 1


def s64(v: int) -> int:
    v &= MASK
    return v - (1 << 64) if v >> 63 else v


class Machine:
    def __init__(self, prog, inputs, *, panel=16, trace=False):
        self.prog = prog
        self.inp = deque(inputs)
        self.ring: deque[int] = deque()
        self.fifo: deque[int] = deque()
        self.scr: deque[int] = deque()
        self.a = self.b = self.bp = 0
        self.panel = panel
        self.nxt = [[0] * panel for _ in range(panel)]
        self.frames: list[list[str]] = []
        self.cursor = 0
        self.trace = trace
        self.ops = 0
        self.pstate = 0      # painter protocol: 0 want n, 1 want addr, 2 want colour
        self.prun = 0
        self.maxfifo = 0
        self.maxscr = 0
        self.maxring = 0

    # -- painter (protocol v2: n, addr, c*n ... ; n == 0 commits) ------------
    def paint(self, v: int) -> None:
        if self.pstate == 0:
            if v == 0:
                self.frames.append(
                    ["".join(f"{c:x}" for c in row) for row in self.nxt])
            else:
                self.prun = v
                self.pstate = 1
        elif self.pstate == 1:
            self.cursor = v
            self.pstate = 2
        else:
            self.nxt[self.cursor // self.panel][self.cursor % self.panel] = v
            self.cursor += 1
            self.prun -= 1
            if self.prun == 0:
                self.pstate = 0

    def step_tokens(self, toks):
        lane = None
        for t in toks:
            self.ops += 1
            if t[0] == "L":
                self.a = int(t[1:])
            elif t == "ri":
                if not self.inp:
                    return "DRY"
                self.a = self.inp.popleft()
            elif t == "rr":
                if not self.ring:
                    raise RuntimeError("ring underflow")
                self.a = self.ring.popleft()
            elif t == "sr":
                self.ring.append(self.a)
                self.maxring = max(self.maxring, len(self.ring))
            elif t == "rf":
                if not self.fifo:
                    raise RuntimeError("fifo underflow")
                self.a = self.fifo.popleft()
            elif t == "sf":
                self.fifo.append(self.a)
                self.maxfifo = max(self.maxfifo, len(self.fifo))
            elif t == "rg":
                if not self.scr:
                    raise RuntimeError("scratch underflow")
                self.a = self.scr.popleft()
            elif t == "sg":
                self.scr.append(self.a)
                self.maxscr = max(self.maxscr, len(self.scr))
            elif t == "sp":
                self.paint(self.a)
            elif t == "M":
                self.b = self.a
            elif t == "W":
                self.a, self.b = self.b, self.a
            elif t == "N":
                self.a = s64(-self.a)
            elif t == "+":
                self.a = s64(self.a + self.b)
            elif t == "-":
                self.a = s64(self.a - self.b)
            elif t == "*":
                self.a = s64(self.a * self.b)
            elif t == "&":
                self.a = s64((self.a & MASK) & (self.b & MASK))
            elif t == "|":
                self.a = s64((self.a & MASK) | (self.b & MASK))
            elif t == "~":
                self.a = s64((self.a & MASK) ^ (self.b & MASK))
            elif t == "{":
                self.a = s64(self.a << self.b) if 0 <= self.b <= 63 else 0
            elif t == "}":
                if self.b < 0:
                    self.a = 0
                elif self.b > 63:
                    self.a = 0 if self.a >= 0 else -1
                else:
                    self.a = self.a >> self.b
            elif t == "b":
                self.bp = self.a
            elif t == "m":
                self.bp -= 1
            elif t == "]":
                self.bp >>= 1
            elif t == "X":
                lane = "zero" if self.a == 0 else ("pos" if self.a > 0 else "neg")
            elif t == "x":
                lane = "one" if self.bp & 1 else "zero"
            elif t == "d":
                lane = "pos" if self.bp > 0 else "zero"
            else:
                raise AssertionError(f"bad token {t!r}")
        return lane

    def run(self, start="INIT", limit=4_000_000):
        block = start
        while block != "HALT":
            if self.ops > limit:
                raise RuntimeError("op limit")
            toks, succ = self.prog[block]
            lane = self.step_tokens(toks)
            if lane == "DRY":
                return self.frames
            if self.trace:
                print(block, "->", lane, "A", self.a, "B", self.b)
            block = succ if isinstance(succ, str) else succ[lane]
        return self.frames


def cells(toks) -> int:
    """Grid cells the token stream occupies (literals expand)."""
    n = 0
    for t in toks:
        if t[0] == "L" and len(t) > 2:
            n += len(t) - 1 + 2
        elif t in ("ri", "rr", "sr", "rf", "sf", "rg", "sg", "sp"):
            n += 1
        else:
            n += 1
    return n
