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

## Who owns what: man M takes the whole front end

**Man M reads the input.**  He files `A` into his `a` ring, packs `B` into his
`b` ring, keeps `G` in his file `f`, and forwards only the three dimensions on
to man C.  Man C never touches the input pipe at all: he reads `N`, `M`, `K`
off the same wire the products arrive on, and has no load phase.

That is worth two things.  The obvious one is ticks -- forwarding `A` cost man C
9,708 walked cells at 16x16x16 and packing `B` about 11,000, and man M is idle
throughout both.  The other is that it leaves **exactly one pipe between the two
rooms**, which is what makes a two-room grid routable at all.  The north band is
planar and every pipe attaches to a north wall, so a run must never pass over a
riser climbing higher than it; with all runs leaving their room eastward,
sorting them by column orders them safely.  Two channels means one of them runs
back westward, and its riser sits inside the other's span whichever way the
rooms are ordered -- a ring's receive column is fixed one east of its send
column, so the spans cannot be made to nest.  One channel, and the one-man
build's row rule generalises to two rooms unchanged.

So man C owns only `q` the per-row file, `k` the constants, `c` the `G`
accumulators, `s` the spill the unpacking needs, and the output.  Both men have
a `k` and an `s`; they are different pipes in different rooms.

## What it costs

Cells a case (the tick floor a layout starts from), against the one-man ring
-- run this module for the table:

    2x2x2       305 / 471      16x16x16   18,859 / 29,976
    2x3x2       365 / 563      16x2x16     6,231 /  7,716
    4x4x4       992 / 1,452    5x6x4       1,463 /  2,238
    7x5x9     2,489 / 3,564    mean        4,386 /  6,569   (1.50x)

At full size that is 4.65 cells a MAC all-in against 7.32, and **2.63 in the
hot loop against 4.50** -- seven glyphs a group where the one man spends twelve.
(The familiar 2.33-against-4.00 is the same ratio with `K` a multiple of three,
where every group carries all three lanes.)

## What a *layout* costs, and the one thing that decides it

Both men compile through :mod:`matmul_grid`'s room builder -- see
``tests/test_matmul_pair.py``, which walks every block of both rooms.  Annealed
against the contest objective, man C lays into **46x47** and man M into
**47x42**, against the one-man build's 64x81.  Priced with
:func:`matmul_grid.estimate_ticks`, which agrees with the engine to 0.02%
(132,360 modelled against 132,330 measured at 16x16x16):

    man C   15,561 cells a case      the clock
    man M   11,546                   under it, with the front end as well
    one man 31,553 (measured)

**2.03x on ticks** -- better than the op-level 1.50x, because the `t` boundary
man M absorbed was 16% of the one-man build's *walk* and only 11% of its
glyphs, and the load loops he took were another 19%.

### The area penalty is structural, and it eats most of that

What is not there is the area.  Every stacked ring needs a turnaround room
straddling its two attach columns, so its band cannot be narrower than seven
columns -- and the split *duplicates* two rings, because both men need
constants (`k`) and a spill (`s`).  The one-man build pays that floor once for
six rings and comes out 64 wide; two rooms pay it for nine, in two rectangles
that must sit side by side:

    man C    io 4 + k 7 + s 7 + p 4 + c 7 + q 7  = 36  ->  ~40 inner
    man M    k 7 + io 4 + s 7 + f 7 + a 4 + b 4 + p 4  = 37  ->  ~41 inner
    grid     1 + 41 + 3 + 40 + 3 + 9 (a coil) + 7 (b coil) = 104 columns

and the rooms are only ~47 rows tall, so the grid is ~104x70 and half its
height is waste.  Annealed it measures **114x71, area^2 12,996**, against the
one-man 88x98 and 9,604; the 104-column figure above is the floor, not a
target.  So the score lands at **2.07e8 against 3.03e8 -- 1.46x**, and even at
the floor it would be ~1.7e8, or 1.75x.

Stacking the rooms to use the wasted height does not help: a room's pipes all
attach to one wall, so a second room below needs a second band, and two bands
cost forty rows -- more than the height they recover.  The tick win is real and
large; the area penalty is a property of splitting into rectangles, not a
packing accident, and it takes back more than half of it.

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
    # ══ the three dimensions, forwarded, then A straight into its ring ════════
    # `f` is man M's file, playing the part `q` plays for man C.
    "MHEAD": ([
        "ri", "sp", "sf", "ri", "sp", "sf", "ri", "sp", "sf",  # f = [N, M, K]
        "rf", "sf", "M",                       # B = N          f = [M, K, N]
        "rf", "sf", "*",                       # A = N*M        f = [K, N, M]
        "b",                                   # BP = N*M
        # `k` during the load is just [LANE, LANE^2] -- one lap per group.
        "L21", "M", "L1", "{", "sk",
        "L42", "M", "L1", "{", "sk",
    ], "MLA"),
    "MLA": (["ri", "sa", "m", "d"], {"pos": "MLA", "zero": "MBHEAD"}),

    # ══ B arrives row-major, which is exactly the packing order ═══════════════
    "MBHEAD": ([
        "rf", "sf", "rf", "sf", "rf", "sf",   # rotate to A = M
        "sf",                                  # ROWS = M
        "rf", "sf", "rf", "sf", "rf", "sf",   # head -> ROWS
    ], "MBROW"),
    "MBROW": (["rf", "X"],
              {"pos": "MBROW_GO", "zero": "MBDONE", "neg": "MBDONE"}),
    "MBROW_GO": ([
        "M", "L1", "W", "-", "sf",            # ROWS - 1
        "rf", "b", "sf",                       # BP = K entries left in this row
        "rf", "sf", "rf", "sf",                # N, M -- head back to ROWS
    ], "MBGRP"),
    # One group = up to three entries, low lane first; the tail of a row is
    # zero-padded by not adding anything.
    "MBGRP": (["ri", "m", "ss"], "MBL1"),
    "MBL1": (["d"], {"pos": "MBL1_R", "zero": "MBL1_Z"}),
    "MBL1_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "MBL2"),
    "MBL1_Z": (["rk", "sk"], "MBL2"),
    "MBL2": (["d"], {"pos": "MBL2_R", "zero": "MBL2_Z"}),
    "MBL2_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "MBEND"),
    "MBL2_Z": (["rk", "sk"], "MBEND"),
    "MBEND": (["rs", "sb", "d"], {"pos": "MBGRP", "zero": "MBROW"}),

    # ══ G, and then nothing but the mill ══════════════════════════════════════
    "MBDONE": ([
        "rk", "rk",                            # drop the load-phase pair
        "rf", "sf",                            # A = K          f = [N, M, K]
        "M", "L2", "W", "+",                   # A = K + 2
        "M", "L3", "W", "/",                   # A = ceil(K/3) = G
        "sf", "rf", "rf", "rf",                # f = [G] and nothing else
    ], "MOUT"),
    # One `t`.  Un-counted: the `ra` blocks for good once `A` runs dry, which is
    # the moment man C has every product he will ever need.
    "MOUT": (["rf", "sf", "b", "ra", "M"], "MMAC"),
    "MMAC": (["rb", "sb", "*", "sp", "m", "d"], {"pos": "MMAC", "zero": "MOUT"}),
}


# ═════════════════════════════════════════════════════════ man C, the clerk ═════
#
# `rp` man M's products and the three dimensions ahead of them, `so` the output,
# and the four small rings `q`, `k`, `c`, `s`.  No input pipe, no load, no `B`.
WORKER_C: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ the three dimensions off the wire, then the constants ═════════════
    # Man M reads the input and forwards them, so man C never touches the input
    # pipe: `io` is send-only in his room and he has no load phase at all.
    "HEAD": ([
        "rp", "sq", "rp", "sq", "rp", "sq",     # Q = [N, M, K]
        "rq", "sq", "rq", "sq", "rq", "sq",     # A = K      Q = [N, M, K]
        "M", "L2", "W", "+",                    # A = K + 2
        "M", "L3", "W", "/",                    # A = ceil(K/3) = G
        "sq",                                   # Q = [N, M, K, G]
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

#: Ring letter -> the pipe it names, per man.  Both men have a `k` and an `s`;
#: they are different pipes in different rooms, so the token spelling is shared
#: and the pipe is not.  `p` is the single wire between the two rooms.
OWNED_C = {"q": "q", "k": "ck", "c": "c", "s": "cs", "p": "p"}
OWNED_M = {"a": "a", "b": "b", "f": "f", "k": "mk", "s": "ms", "p": "p"}

#: Words each pipe has to hold.  A pipe's capacity is its cell count, and one
#: that cannot hold its contents deadlocks *silently*.
RING_WORDS = {"p": 4, "a": 256, "b": 96, "f": 5, "mk": 2, "ms": 1,
              "q": 5, "ck": 6, "c": 6, "cs": 1}


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
    reads = {"C": {k: v for k, v in OWNED_C.items()},
             "M": {k: v for k, v in OWNED_M.items() if k != "p"}}
    writes = {"C": {k: v for k, v in OWNED_C.items() if k != "p"},
              "M": dict(OWNED_M)}

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
