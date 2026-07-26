#!/usr/bin/env python3
"""`brackets` as a three-man pipeline whose stack is one register.

Depth is at most 32 and there are three opener types, so the whole stack is a
base-3 number in one 64-bit word.  Digits are **1..3** (not 0..2), which buys
the empty test for free: `v = 0` is empty, `v = 3*v + d` is push, and no
sentinel is needed.  The deepest legal stack is 32 threes, i.e. `3**32 - 1`
= 1.85e15, comfortably inside 2**63.

    push   v' = 3v + d                    `+++` with B = v
    pop    w  = v - d;  valid iff w >= 0 and w % 3 == 0;  v' = w / 3

The pop test is one sign test plus one remainder test: `w < 0` is exactly the
underflow-or-mismatch case (`v = 0`, or a shallow stack whose digit is smaller
than the closer's), and `w % 3 != 0` is the mismatch case.

**The stack lives in B, not A.**  `r` clobbers only A, so the loop never has to
spill: B carries the stack across the read, A holds the incoming character, and
BP holds its bits.  Classification is a pure backpack decision tree — `b` then
`]`/`x` — which touches neither hand:

    bit0 0 -> bit1 0 `(`            1 EOS
         1 -> bit1 1 -> bit5 0 `[`  1 `{`
                   0 -> bit2 0 `)`
                             1 -> bit5 0 `]`  1 `}`

That leaves the 1-based position, a fourth quantity, which is why the machine
is three men rather than one.  `FEEDER` reads the length prefix, forwards that
many characters and appends the sentinel `2` (bit0 = 0, bit1 = 1 — a free leaf
of the same tree, so end-of-string costs the worker no extra test).  `COUNTER`
counts the acknowledgements the worker emits after each *successfully* consumed
character, so at a failure it holds exactly `i - 1`; the worker's verdict word
tells it which answer to print (`-1` -> `count + 1`, `-2` -> `0`).
"""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = ["COUNTER", "FEEDER", "MAIN", "reference", "simulate"]

Block = tuple[list[str], "dict[str, str] | str | None"]

# ── the worker: stack in B, character in A, its bits in BP ────────────────────
MAIN: dict[str, Block] = {
    "INIT": (["R", "s", "L0", "M"], "LOOP"),
    "LOOP": (["R", "b", "x"], {"zero": "B1Z", "one": "B1O"}),
    "B1Z": (["]", "x"], {"zero": "PUSH1", "one": "EOS"}),
    "B1O": (["]", "x"], {"one": "OPEN23", "zero": "B2"}),
    "OPEN23": (["]", "]", "]", "]", "x"], {"zero": "PUSH2", "one": "PUSH3"}),
    "B2": (["]", "x"], {"zero": "POP1", "one": "CLOS23"}),
    "CLOS23": (["]", "]", "]", "x"], {"zero": "POP2", "one": "POP3"}),

    "PUSH1": (["L1", "+", "+", "+", "s", "M"], "LOOP"),
    "PUSH2": (["L2", "+", "+", "+", "s", "M"], "LOOP"),
    "PUSH3": (["L3", "+", "+", "+", "s", "M"], "LOOP"),

    "POP1": (["L1", "N"], "POPW"),
    "POP2": (["L2", "N"], "POPW"),
    "POP3": (["L3", "N"], "POPW"),
    # A = -d, B = v  ->  w = v - d
    "POPW": (["+", "X"], {"neg": "FAIL", "zero": "POPZ", "pos": "POPD"}),
    "POPZ": (["s", "M"], "LOOP"),
    "POPD": (["M", "L3", "W", "/", "W", "X"],
             {"zero": "POPQ", "pos": "FAIL", "neg": "FAIL"}),
    "POPQ": (["W", "s", "M"], "LOOP"),

    "EOS": (["W", "X"], {"zero": "BAL", "pos": "FAIL", "neg": "FAIL"}),
    "BAL": (["L2", "N", "s", "H"], None),
    "FAIL": (["L1", "N", "s", "H"], None),
}

# ── the counter: the length prefix, then one ack a character, then the answer ─
# `remaining` lives in BP and `count` in B, so A stays free.  When the countdown
# runs out the counter hands the worker the sentinel `2` — the worker is by then
# blocked on `R` with a dry input pipe, so the sentinel is the only value that
# can reach him and end-of-string needs no extra state on his side.
COUNTER: dict[str, Block] = {
    "CINIT": (["r", "b", "L0", "M"], "CTEST"),
    "CTEST": (["d"], {"pos": "CLOOP", "zero": "CEND"}),
    "CLOOP": (["r", "X"], {"pos": "CINC", "zero": "CINC", "neg": "CTERM"}),
    "CINC": (["L1", "+", "M", "m"], "CTEST"),
    "CEND": (["L2", "sw"], "CLOOP"),
    "CTERM": (["b", "x"], {"one": "COUT1", "zero": "COUT0"}),
    "COUT1": (["L1", "+", "so", "H"], None),
    "COUT0": (["L0", "so", "H"], None),
}

MEN = {"MAIN": MAIN, "COUNTER": COUNTER}
ENTRY = {"MAIN": "INIT", "COUNTER": "CINIT"}
#: man -> (incoming wires in nearest order, token -> outgoing wire)
WIRES = {
    "MAIN": (["in", "term"], {"s": "ack"}),
    "COUNTER": (["ack"], {"sw": "term", "so": "out"}),
}


# ── reference model ───────────────────────────────────────────────────────────
TYPE = {"(": 1, "[": 2, "{": 3, ")": 1, "]": 2, "}": 3}
OPEN = set("([{")


def reference(text: str) -> int:
    """The problem statement, verbatim."""
    stack: list[int] = []
    for i, ch in enumerate(text, 1):
        if ch in OPEN:
            stack.append(TYPE[ch])
        elif not stack or stack.pop() != TYPE[ch]:
            return i
    return 0 if not stack else len(text) + 1


# ── an op-level simulator for the three men ──────────────────────────────────
_BIN = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "~": lambda a, b: a ^ b,
    "{": lambda a, b: a << b if 0 <= b <= 63 else 0,
    "}": lambda a, b: a >> b if b >= 0 else 0,
}


class _Man:
    def __init__(self, name: str) -> None:
        self.name = name
        self.blocks = MEN[name]
        self.block = ENTRY[name]
        self.pc = 0
        self.a = self.b = self.bp = 0
        self.branch: str | None = None
        self.halted = False
        self.glyphs = 0


def simulate(text: str, *, max_steps: int = 200_000) -> tuple[int | None, dict[str, int]]:
    """Run the three men over one case; return the emitted value and glyph counts.

    A cooperative round-robin: every man executes one token per turn unless a
    pipe op blocks him.  That is not the real tick order, but it exercises the
    same control graph and the same blocking discipline.
    """
    wire: dict[str, deque[int]] = {
        "in": deque([len(text)] + [ord(c) for c in text]),
        "term": deque(),
        "ack": deque(),
        "out": deque(),
    }
    men = [_Man(n) for n in ("MAIN", "COUNTER")]
    steps = 0

    while any(not m.halted for m in men):
        steps += 1
        if steps > max_steps:  # pragma: no cover - runaway guard
            raise RuntimeError(f"did not settle on {text!r}")
        for m in men:
            if m.halted:
                continue
            toks, succ = m.blocks[m.block]
            if m.pc >= len(toks):
                if succ is None:
                    m.halted = True
                    continue
                m.block = succ if isinstance(succ, str) else succ[m.branch]
                m.pc = 0
                continue
            t = toks[m.pc]
            ins, outs = WIRES[m.name]
            if t == "r":
                if not wire[ins[0]]:
                    continue
                m.a = wire[ins[0]].popleft()
            elif t == "R":
                ready = [w for w in ins if wire[w]]
                if not ready:
                    continue
                m.a = wire[ready[0]].popleft()
            elif t in outs:
                wire[outs[t]].append(m.a)
            elif t.startswith("L"):
                m.a = int(t[1:])
            elif t == "M":
                m.b = m.a
            elif t == "W":
                m.a, m.b = m.b, m.a
            elif t == "N":
                m.a = -m.a
            elif t == "/":
                m.a, m.b = m.a // m.b, m.a % m.b
            elif t in _BIN:
                m.a = _BIN[t](m.a, m.b)
            elif t == "b":
                m.bp = m.a
            elif t == "m":
                m.bp -= 1
            elif t == "]":
                m.bp >>= 1
            elif t == "X":
                m.branch = "zero" if m.a == 0 else ("pos" if m.a > 0 else "neg")
            elif t == "x":
                m.branch = "one" if m.bp & 1 else "zero"
            elif t == "d":
                m.branch = "pos" if m.bp > 0 else "zero"
            elif t == "H":
                m.halted = True
                continue
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
            m.pc += 1
            m.glyphs += 1

    out = wire["out"]
    counts = {m.name: m.glyphs for m in men}
    return (out[0] if len(out) == 1 else None), counts


def glyph_cells(worker: dict[str, Block]) -> int:
    """Grid cells the blocks need, counting literals as written."""
    return sum(
        1 if not t.startswith("L") or int(t[1:]) <= 9 else len(t[1:]) + 2
        for toks, _ in worker.values()
        for t in toks
    )


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    import itertools

    bad = 0
    for size in range(8):
        for chars in itertools.product("()[]{}", repeat=size):
            s = "".join(chars)
            got, counts = simulate(s)
            if got != reference(s):
                bad += 1
                if bad < 6:
                    print(f"  {s!r}: got {got}, want {reference(s)}")
    print("mismatches:", bad)
    for name, w in MEN.items():
        print(f"{name}: {len(w)} blocks, {glyph_cells(w)} glyph cells")
