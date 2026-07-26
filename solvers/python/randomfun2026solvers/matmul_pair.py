#!/usr/bin/env python3
"""`matmul` as **two** little men -- the multiply and the accumulate in parallel.

`matmul_cfg` proved the spill irreducible for one man.  A MAC has three live
values -- `a`, the packed `B` word and the accumulator -- against two hands and
a write-only backpack, and `*` writes `A` while `M` writes `B`, so one operand
must come off a pipe every step.  Three of its twelve hot glyphs (`rs ss M`) are
that spill, and twelve glyphs carry three MACs: **4.0 cells a MAC**.

Two men in two rooms delete the spill outright, because `a` can simply *live* in
a man's `B` for the whole inner loop:

    man M   rb sb * sp m d      the packed word, the product, the group count
    man C   rp M rc + sc m d    the product, the accumulator

`*` writes `A` and leaves `B` alone, so man M's `a` survives every multiply; he
never touches it again until the next `t`.  Man C never sees `a` at all.  The
two run on the same clock, so a group costs `max(6, 6) + 1` rather than `12`.

## The `t` boundary is free because man M owns it

The bigger win is not the spill.  In the one-man build the `t` countdown and the
group count ride as two header words in the accumulator ring, and `TTAIL`/
`TNEXT` cost eleven glyphs at every one of the `N*M` boundaries -- 11% of a full
case.  Here **man M owns the whole `t` loop** and man C never learns that `t`
exists: man M's outer loop is un-counted (he blocks on an empty `a` ring at the
end of the case, which is exactly when the machine is done), and man C simply
runs `M*G` accumulate steps per output row against a header-free ring of `G`
accumulators that wraps on its own.

That balances the two men almost exactly.  Per `t` at 16x16x16 (`G = 6`):

    man M   5 + 6*G = 41        rf sf b . ra M . then the group loop
    man C       7*G = 42        one straight loop, no boundary at all

Man M is one glyph a `t` ahead, so he waits on the product pipe and man C is the
clock.  Nothing else has to be balanced: whatever man C does alone (the load,
the unpacking, the emit) man M sits out, and whatever man M does alone (moving
`A` and `B` into his rings) man C is packing `B` for anyway.

## Who owns what

Man C owns the outside world and the small rings: `q` the per-row file, `k` the
constants, `c` the `G` accumulators, `s` the one-word spill the unpacking needs.
Man M owns the two big ones: `a`, the `N*M` entries of `A`, and `b`, the `M*G`
packed `B` words that turn one lap per output row.  They are joined by exactly
two one-way pipes -- `x` southbound carrying a three-word header and then every
value man M will ever need, and `p` northbound carrying products.

Because `A` arrives before `B` on the input but man M wants his `b` ring loaded
first, man M -- not man C -- does the buffering: man C forwards each value the
moment he has it and man M files it, `rx sa` then `rx sb`.  The 1,408 glyphs
that costs at full size are free, because man C is packing `B` throughout.

## What it costs

Cells a case (the tick floor a layout starts from), against the one-man ring:

    see `report()` -- run this module.

The packing, the biasing and the base-2^21 argument are unchanged; see
:mod:`randomfun2026solvers.matmul_cfg`.
"""

from __future__ import annotations

from collections import deque

from randomfun2026solvers.matmul_cfg import (
    BIAS,
    BIAS3,
    LANE,
    LANES,
    cell_cost,
    matmul_reference,
)

__all__ = [
    "WORKER_C",
    "WORKER_M",
    "matmul_reference",
    "simulate_pair",
]

_ = (BIAS, BIAS3, LANE, LANES)


# ══════════════════════════════════════════════════════════════ man M, the mill ═
#
# Rings `a` (the entries of `A`) and `b` (the packed `B` words); `rx` the header
# and load channel from man C, `sp` the product channel back.  `f` is a two-word
# file holding the `B` count and then `G` for ever.
WORKER_M: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # Header: `count_a`, `G`, `count_b` -- in the order man C can compute them.
    "MHEAD": (["rx", "b", "rx", "sf", "rx", "sf"], "MLA"),
    "MLA": (["rx", "sa", "m", "d"], {"pos": "MLA", "zero": "MLB0"}),
    # `f` is [G, count_b]; take the count out and leave `G` alone for ever.
    "MLB0": (["rf", "sf", "rf", "b"], "MLB"),
    "MLB": (["rx", "sb", "m", "d"], {"pos": "MLB", "zero": "MOUT"}),
    # One `t`.  Un-counted: the `ra` blocks for good once `A` runs dry, which is
    # the moment man C has every product he will ever need.
    "MOUT": (["rf", "sf", "b", "ra", "M"], "MMAC"),
    "MMAC": (["rb", "sb", "*", "sp", "m", "d"], {"pos": "MMAC", "zero": "MOUT"}),
}


# ═════════════════════════════════════════════════════════ man C, the clerk ═════
#
# `ri`/`so` the outside world, `sx` the channel to man M, `rp` his products, and
# the four small rings `q`, `k`, `c`, `s`.
WORKER_C: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ the three dimensions, the two counts and G, then A straight through ════
    "HEAD": ([
        "ri", "sq", "ri", "sq", "ri", "sq",   # Q = [N, M, K]
        "rq", "sq", "M",                       # B = N            Q = [M, K, N]
        "rq", "sq", "*",                       # A = N*M          Q = [K, N, M]
        "sx",                                  # -> man M: count_a
        "rq", "sq",                            # A = K            Q = [N, M, K]
        "M", "L2", "W", "+",                   # A = K + 2
        "M", "L3", "W", "/",                   # A = ceil(K/3) = G
        "sq",                                  # Q = [N, M, K, G]
        "sx",                                  # -> man M: G
        "rq", "sq",                            # N
        "rq", "sq", "M",                       # B = M
        "rq", "sq",                            # K
        "rq", "sq", "*",                       # A = M*G          Q = [N, M, K, G]
        "sx",                                  # -> man M: count_b
        # BP = N*M again, for the forwarding loop below.
        "rq", "sq", "M", "rq", "sq", "*", "b",  # Q = [K, G, N, M]
        "rq", "sq", "rq", "sq",                 # Q = [N, M, K, G]
        # KONST during the load is just [LANE, LANE^2] -- one lap per group.
        "L21", "M", "L1", "{", "sk",
        "L42", "M", "L1", "{", "sk",
    ], "LOADA"),
    "LOADA": (["ri", "sx", "m", "d"], {"pos": "LOADA", "zero": "BHEAD"}),

    # ══ B arrives row-major, which is exactly the packing order ═══════════════
    # Q = [N, M, K, G] -> [ROWS, K, ...] with ROWS = M, as in the one-man build.
    # Q = [N, M, K, G] -> [ROWS, K, G, N, M] with ROWS = M, a five-word lap that
    # brings ROWS back under the hand after `BROW_GO` has read `K`.
    "BHEAD": ([
        "rq", "sq",                            # N to the back    Q=[M,K,G,N]
        "rq", "sq", "sq",                      # M twice: the copy is ROWS
        "rq", "sq", "rq", "sq", "rq", "sq", "rq", "sq",  # head -> ROWS
    ], "BROW"),
    "BROW": (["rq", "X"], {"pos": "BROW_GO", "zero": "BDONE", "neg": "BDONE"}),
    "BROW_GO": ([
        "M", "L1", "W", "-", "sq",             # ROWS - 1
        "rq", "b", "sq",                       # BP = K entries left in this row
        "rq", "sq", "rq", "sq", "rq", "sq",    # G, N, M -- head back to ROWS
    ], "BGRP"),
    "BGRP": (["ri", "m", "ss"], "BL1"),
    "BL1": (["d"], {"pos": "BL1_R", "zero": "BL1_Z"}),
    "BL1_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "BL2"),
    "BL1_Z": (["rk", "sk"], "BL2"),
    "BL2": (["d"], {"pos": "BL2_R", "zero": "BL2_Z"}),
    "BL2_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "BGRP_END"),
    "BL2_Z": (["rk", "sk"], "BGRP_END"),
    "BGRP_END": (["rs", "sx", "d"], {"pos": "BGRP", "zero": "BROW"}),

    # ══ the real constants and the G-word accumulator ring ════════════════════
    "BDONE": ([
        "rq", "sq", "rq", "sq",                # Q = [K,G,N,M] -> [N,M,K,G]
        "rk", "rk",                            # drop the load-phase pair
        "L21", "M", "L1", "{", "sk",           # LANE
        "L19", "M", "L1", "{", "sk",           # D
        "L21", "M", "L1", "{", "sk",           # LANE
        "L19", "M", "L1", "{", "sk",           # D
        "L19", "M", "L1", "{", "sk",           # D
        "L42", "M", "L1", "{", "ss",           # S = LANE^2
        "L21", "M", "L1", "{", "M", "rs", "+",  # A = LANE^2 + LANE
        "M", "L1", "+",                        # A = ONES
        "ss",
        "L19", "M", "L1", "{", "M", "rs", "*",  # A = D * ONES = BIAS3
        "sk",                                   # KONST slot 5
        "ss",                                   # S = BIAS3, for the fill below
        "rq", "sq", "rq", "sq", "rq", "sq",     # rotate round to G
        "rq", "sq", "b",                        # BP = G     Q = [N, M, K, G]
    ], "CFILL"),
    "CFILL": (["rs", "ss", "sc", "m", "d"], {"pos": "CFILL", "zero": "CGO"}),
    "CGO": (["rs"], "ROW"),                     # drop BIAS3 from the spill

    # ══ one output row ════════════════════════════════════════════════════════
    # Q = [I, M, K, G]; the accumulator ring is `G` words and nothing else, so it
    # wraps on its own and there is no `t` boundary in this man at all.
    "ROW": (["rq", "X"], {"pos": "ROW_GO", "zero": "FIN", "neg": "FIN"}),
    "ROW_GO": ([
        "M", "L1", "W", "-", "sq",             # I - 1
        "rq", "sq", "M",                       # B = M
        "rq", "sq",                            # K
        "rq", "sq", "*",                       # A = M*G
        "b",                                   # BP = M*G accumulate steps
    ], "CACC"),
    "CACC": (["rp", "M", "rc", "+", "sc", "m", "d"],
             {"pos": "CACC", "zero": "EMIT_SET"}),
    "EMIT_SET": ([
        "rq", "sq", "rq", "sq",                # I, M
        "rq", "b", "sq",                       # BP = K values to emit
        "rq", "sq",                            # G -- head back to I
    ], "GRP"),

    # ══ unpack one accumulator word, emit up to three entries ═════════════════
    # KONST turns exactly one lap per group: LANE, D, LANE, D, D, BIAS3.
    "GRP": ([
        "rk", "sk", "M",                       # B = LANE
        "rc", "/",                             # A = high lanes    B = lane 0
        "ss", "W", "M",                        # spill quotient,   B = lane 0
        "rk", "sk", "W", "-",                  # A = lane 0 - D
        "so", "m",
    ], "GG1"),
    "GG1": (["rk", "sk", "M", "rs", "/", "ss", "W", "M", "rk", "sk", "W", "-"],
            "LN1"),
    "LN1": (["d"], {"pos": "E1", "zero": "GG2"}),
    "E1": (["so", "m"], "GG2"),
    "GG2": (["rs", "M", "rk", "sk", "W", "-"], "LN2"),
    "LN2": (["d"], {"pos": "E2", "zero": "GEND"}),
    "E2": (["so", "m"], "GEND"),
    # `rk sk` lands on BIAS3, and `sc` is the accumulator's reset for the next row.
    "GEND": (["rk", "sk", "sc", "d"], {"pos": "GRP", "zero": "ROW"}),

    "FIN": (["H"], "FIN"),
}


# ═══════════════════════════════════════════════════════ the two-man simulator ═
_BIN = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "{": lambda a, b: a << b if 0 <= b <= 63 else 0,
    "}": lambda a, b: a >> b if b >= 0 else 0,
}

#: Rings that live inside each man's room.  `x` and `p` are the two one-way
#: pipes joining the rooms and belong to neither.
OWNED_C = ("q", "k", "c", "s")
OWNED_M = ("a", "b", "f")

#: Words each pipe has to hold.  A pipe's capacity is its cell count, and one
#: that cannot hold its contents deadlocks *silently*.
RING_WORDS = {"x": 4, "p": 4, "a": 256, "b": 96, "f": 2,
              "q": 5, "k": 6, "c": 6, "s": 1}


class _Man:
    """One walker: two hands, a write-only backpack and a program counter."""

    __slots__ = ("worker", "block", "pc", "a", "b", "bp", "branch", "walk",
                 "cells", "stalls", "done")

    def __init__(self, worker: dict, entry: str) -> None:
        self.worker, self.block, self.pc = worker, entry, 0
        self.a = self.b = self.bp = 0
        self.branch: str | None = None
        self.walk = 0
        self.cells = self.stalls = 0
        self.done = False


def simulate_pair(values: list[int], *, cap: dict[str, int] | None = None,
                  max_ticks: int = 20_000_000) -> dict[str, object]:
    """Run both men on one clock; return the output and the tick model.

    Each token occupies ``cell_cost`` cells and so ``cell_cost`` ticks of
    walking -- the same floor :func:`matmul_cfg.simulate` reports, but with the
    two men advancing together and blocking on their shared pipes.  Reads are
    tested against the state at the *start* of a tick, so a value can never be
    produced and consumed in the same tick.
    """
    caps = dict(RING_WORDS)
    caps.update(cap or {})
    q: dict[str, deque[int]] = {k: deque() for k in caps}
    high = dict.fromkeys(caps, 0)
    inp: deque[int] = deque(values)
    out: list[int] = []

    men = {"C": _Man(WORKER_C, "HEAD"), "M": _Man(WORKER_M, "MHEAD")}
    runs: dict[str, dict[str, int]] = {"C": {"HEAD": 1}, "M": {"MHEAD": 1}}
    lanes: dict[str, dict[tuple[str, str], int]] = {"C": {}, "M": {}}
    reads = {"C": {"p": "p", **{r: r for r in OWNED_C}},
             "M": {"x": "x", **{r: r for r in OWNED_M}}}
    writes = {"C": {"x": "x", **{r: r for r in OWNED_C}},
              "M": {"p": "p", **{r: r for r in OWNED_M}}}

    ticks = 0
    while True:
        ticks += 1
        if ticks > max_ticks:  # pragma: no cover - a runaway guard
            raise RuntimeError("pair did not settle: "
                               + repr([(k, m.block) for k, m in men.items()]))
        avail = {k: len(v) for k, v in q.items()}
        for who, man in men.items():
            if man.done:
                continue
            if man.walk:
                man.walk -= 1
                continue
            toks, succ = man.worker[man.block]
            t = toks[man.pc]
            rd, wr = reads[who], writes[who]
            # -- blocking, tested against the start of the tick ----------------
            if t[0] == "r" and t[1:] in rd and not avail[rd[t[1:]]]:
                man.stalls += 1
                continue
            if t[0] == "s" and t[1:] in wr and len(q[wr[t[1:]]]) >= caps[wr[t[1:]]]:
                man.stalls += 1
                continue
            if t == "ri" and not inp:  # pragma: no cover - never over-reads
                raise RuntimeError("input exhausted")
            man.pc += 1
            cost = cell_cost([t])
            man.cells += cost
            man.walk = cost - 1
            if t == "H":
                man.done = True
                return {"out": out, "ticks": ticks,
                        "cells": {k: m.cells for k, m in men.items()},
                        "stalls": {k: m.stalls for k, m in men.items()},
                        "runs": runs, "lanes": lanes, "high": high}
            if t.startswith("L") and t != "L":
                man.a = int(t[1:])
            elif t == "ri":
                man.a = inp.popleft()
            elif t == "so":
                out.append(man.a)
            elif t[0] == "r" and t[1:] in rd:
                man.a = q[rd[t[1:]]].popleft()
            elif t[0] == "s" and t[1:] in wr:
                name = wr[t[1:]]
                q[name].append(man.a)
                high[name] = max(high[name], len(q[name]))
            elif t == "M":
                man.b = man.a
            elif t == "W":
                man.a, man.b = man.b, man.a
            elif t == "N":
                man.a = -man.a
            elif t == "/":
                man.a, man.b = man.a // man.b, man.a % man.b
            elif t in _BIN:
                man.a = _BIN[t](man.a, man.b)
            elif t == "b":
                man.bp = man.a
            elif t == "m":
                man.bp -= 1
            elif t == "X":
                man.branch = ("zero" if man.a == 0
                              else ("pos" if man.a > 0 else "neg"))
            elif t == "d":
                man.branch = "pos" if man.bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
            if man.pc >= len(toks):
                key = (man.block, man.branch if isinstance(succ, dict) else "straight")
                lanes[who][key] = lanes[who].get(key, 0) + 1
                man.block = succ if isinstance(succ, str) else succ[man.branch]
                man.pc = 0
                runs[who][man.block] = runs[who].get(man.block, 0) + 1


def public_cases() -> list[list[int]]:
    """The seven public cases, as flat integer lists."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    prob = json.loads((root / "tasks/problems/matmul.json").read_text())
    return [[int(v) for v in c["rounds"][0]["in"]] for c in prob["publicTestData"]]


def public_traces() -> list[dict[str, object]]:
    """Block and lane counts for both men over each public case."""
    return [simulate_pair(case) for case in public_cases()]


def report() -> None:  # pragma: no cover - the module's self-check
    """Model both builds over the seven public shapes and print the table."""
    import random

    from randomfun2026solvers.matmul_cfg import simulate

    shapes = [(2, 2, 2), (2, 3, 2), (4, 4, 4), (16, 16, 16),
              (16, 2, 16), (5, 6, 4), (7, 5, 9)]
    random.seed(0)
    tot1 = tot2 = 0
    print(f"{'shape':>10} {'one-man':>9} {'two-man':>9} {'ratio':>6} "
          f"{'/MAC 1':>7} {'/MAC 2':>7}")
    for n, m, k in shapes:
        case = [n, m, k] + [random.randint(-99, 99) for _ in range(n * m + m * k)]
        want = matmul_reference(case)
        _, _, one = simulate(case)
        res = simulate_pair(case)
        assert res["out"] == want, (n, m, k)
        two = res["ticks"]
        tot1 += one
        tot2 += two
        macs = n * m * k
        print(f"{n:>3}x{m}x{k:<4} {one:>9,} {two:>9,} {one / two:>6.2f} "
              f"{one / macs:>7.2f} {two / macs:>7.2f}")
    print(f"{'mean':>10} {tot1 / len(shapes):>9,.0f} {tot2 / len(shapes):>9,.0f} "
          f"{tot1 / tot2:>6.2f}")


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    report()
