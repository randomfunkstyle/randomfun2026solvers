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

**The stack lives in B, not A.**  `r` clobbers only A, so the worker never has
to spill: B carries the stack across every read.  That leaves his A free for the
incoming token and his BP untouched, but it also means he can never classify a
character himself — `M` would overwrite the stack — so the machine is three men.

* `CLASS` is stateless per character, so all three of its registers are scratch.
  `c >> 5` is exactly the type 1..3 for all six codes, and a closer is exactly
  `bit0 = 1, bit1 = 0`, so `b` parks the raw code in the backpack before the
  shift destroys it and two `x` tests recover the sign.  Thirteen cells.
* `WORK` holds the stack and does the arithmetic above.
* `COUNT` holds `remaining` in BP and the 1-based position in B.  It counts the
  acknowledgements the worker emits after each *successfully* consumed
  character, so at a failure it holds exactly `i - 1`, and the worker's verdict
  word says which answer to print (`-1` -> `count + 1`, `-2` -> `0`).  When the
  countdown runs out it hands the worker a `0`, which is the end-of-string
  sentinel: the worker is by then blocked on `R` with a dry token pipe, so the
  sentinel is the only value that can reach him.
"""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = ["CLASS", "COUNT", "WORK", "reference", "simulate"]

Block = tuple[list[str], "dict[str, str] | str | None"]

# ── the classifier: the character in A, its bits in BP, the token out ────────
CLASS: dict[str, Block] = {
    "PINIT": (["r", "s"], "PLOOP"),
    "PLOOP": (["r", "b", "M", "L5", "W", "}", "x"], {"zero": "PSEND", "one": "P1"}),
    "P1": (["]", "x"], {"one": "PSEND", "zero": "PNEG"}),
    "PNEG": (["N"], "PSEND"),
    "PSEND": (["s"], "PLOOP"),
}

# ── the worker: A takes the token, B is the stack, BP is never touched ────────
WORK: dict[str, Block] = {
    "QINIT": (["R", "s", "L0", "M"], "QLOOP"),
    "QLOOP": (["R", "X"], {"pos": "QPUSH", "neg": "QPOP", "zero": "QEOS"}),
    "QPUSH": (["+", "+", "+", "s", "M"], "QLOOP"),
    "QPOP": (["+", "X"], {"neg": "QFAIL", "zero": "QZERO", "pos": "QDIV"}),
    "QZERO": (["s", "M"], "QLOOP"),
    "QDIV": (["M", "L3", "W", "/", "W", "X"],
             {"zero": "QQUOT", "pos": "QFAIL", "neg": "QFAIL"}),
    "QQUOT": (["W", "s", "M"], "QLOOP"),
    "QEOS": (["W", "X"], {"zero": "QBAL", "pos": "QFAIL", "neg": "QFAIL"}),
    "QBAL": (["L2", "N", "s", "H"], None),
    "QFAIL": (["L1", "N", "s", "H"], None),
}

# ── the counter: `remaining` in BP, the position in B ────────────────────────
COUNT: dict[str, Block] = {
    "CINIT": (["r", "b", "L0", "M"], "CTEST"),
    "CTEST": (["d"], {"pos": "CLOOP", "zero": "CEND"}),
    "CLOOP": (["r", "X"], {"pos": "CINC", "zero": "CINC", "neg": "CTERM"}),
    "CINC": (["L1", "+", "M", "m"], "CTEST"),
    "CEND": (["L0", "sw"], "CLOOP"),
    "CTERM": (["b", "x"], {"one": "COUT1", "zero": "COUT0"}),
    "COUT1": (["L1", "+", "so", "H"], None),
    "COUT0": (["L0", "so", "H"], None),
}

MEN = {"CLASS": CLASS, "WORK": WORK, "COUNT": COUNT}
ENTRY = {"CLASS": "PINIT", "WORK": "QINIT", "COUNT": "CINIT"}
#: man -> (incoming wires in nearest order, token -> outgoing wire)
WIRES = {
    "CLASS": (["in"], {"s": "tok"}),
    "WORK": (["tok", "term"], {"s": "ack"}),
    "COUNT": (["ack"], {"sw": "term", "so": "out"}),
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
        "tok": deque(),
        "term": deque(),
        "ack": deque(),
        "out": deque(),
    }
    men = [_Man(n) for n in ("CLASS", "WORK", "COUNT")]
    steps = 0

    while any(not m.halted for m in men):
        steps += 1
        if steps > max_steps:  # pragma: no cover - runaway guard
            raise RuntimeError(f"did not settle on {text!r}")
        moved = False
        for m in men:
            if m.halted:
                continue
            toks, succ = m.blocks[m.block]
            if m.pc >= len(toks):
                if succ is None:
                    m.halted = True
                else:
                    m.block = succ if isinstance(succ, str) else succ[m.branch]
                    m.pc = 0
                moved = True
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
            moved = True
        if not moved:  # every live man is blocked on a pipe that will stay dry
            break

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
