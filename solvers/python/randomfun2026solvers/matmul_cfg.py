#!/usr/bin/env python3
"""`matmul` as a dataflow ring machine -- no CPU, no ISA, no tape.

The CPU build (`tasks/solutions/matmul_cpu.man`, 86x86, 153,786 ticks/case) does
at most ``N*M*K = 4096`` multiply-accumulates, so it pays **~37 ticks per MAC**.
Almost none of that is the MAC: on `snake` the instruction doing the work
measured as 0.18% of the critical path and the rest was fetch, decode trie,
branch slabs and the return walk.  This module deletes the interpreter.

Two ideas carry the whole design.

## 1.  The outer-product order needs no transpose and no seeking

``C[i][j] = sum_t A[i][t] * B[t][j]`` is usually written i-j-t, which walks `B`
**down a column** -- against a FIFO's grain, so it would force either a
transpose or a rotation per MAC.  Reassociated as i-t-j it becomes an outer
product:

    for i:  for t:  a = A[i][t];  for j:  acc[j] += a * B[t][j]

and now *every* stream is read in exactly the order it arrived:

* `A` is consumed strictly front-to-back, one entry per `t`, never pushed back;
* `B[t][*]` is a contiguous run of the input, so one `i` pass is **one lap** of
  the `B` ring and lap `i+1` starts aligned with no seek;
* the accumulator ring turns one full lap per `t`.

Each of the three rings advances by exactly one word per MAC step.  Nothing is
addressed, nothing is searched, no operand is ever walked against the grain --
which is why transposing `B` at load time would be a pure loss here: it buys an
inner-product order that costs the *same* per step and 256 entries to build.

## 2.  Three MACs per multiply, because a 21-bit lane holds a whole C entry

`|C[i][j]| <= 16*99*99 = 156,816 < 2^18`, and `-99..99` products are
`|a*b| <= 9801`.  So if a word carries three `B` entries in base ``2^21``

    P = b0 + b1*2^21 + b2*2^42

then ``a*P = (a b0) + (a b1) 2^21 + (a b2) 2^42`` *exactly* -- one `*` is three
multiplies -- and summing over `t` keeps every lane inside its field, because a
lane never exceeds 156,816 in magnitude.  One `+` is three accumulations.

Signs are handled by **biasing the accumulator, not the entries**: each lane
starts at ``D = 2^19`` and stays in ``[D-156816, D+156816] subset (0, 2^21)``,
so no lane ever borrows from its neighbour and extraction is two bare ``/``
divisions -- quotient in `A`, remainder in `B`, which is the only reason a
packed word is affordable at all.  ``S <= 681,104 * (1+2^21+2^42) ~ 3.0e18``
stays inside a signed 64-bit word.

The convolution packing (`A` forward, `B` reversed, one `*` for a whole dot
product) is *not* used: its middle coefficient needs ``19*(2M-1)`` bits, 589 at
`M = 16`.

## The twelve-glyph MAC

Two hands and a write-only backpack cannot hold `a`, `b` and `acc` at once, and
`*` clobbers `A` while `M` clobbers `B` -- so exactly one of the three must come
from a pipe each step.  `a` is the one that is *fixed* across the inner loop, so
it lives in a one-word **spill ring** and is re-established each step:

    rs ss M    a back in B, restored to its own ring
    rb sb      the packed B word, restored to the B ring
    *          A = a*P,  B = a
    M          B = a*P
    rc + sc    acc += product

Twelve glyphs -- ten above plus the `m`/`d` that close the counted loop -- for
three MACs, so the hot loop is **4.0 cells a MAC** where the CPU build pays 37
ticks.  Every counted loop in the module is a do-while for the same reason: the
trip count is >= 1 by the constraints, so an entry test would be a block
boundary the layout has to route 1,536 times a case for nothing.

## What it costs

Cells (glyph runs with literals written out in digits -- the tick floor a layout
starts from), against the CPU build's 153,786 ticks a case:

    2x2x2        471      16x2x16    7,716
    2x3x2        563      5x6x4      2,238
    4x4x4      1,452      7x5x9      3,564
    16x16x16  29,976      mean       6,569   (23.4x under the CPU build)

At full size that is 7.32 cells a MAC all-in: 62% of it is the hot loop, 11% the
unpacking, 11% the `t` boundary, 10% the load.  The same CFG with `LANES = 1`
(no packing) models at ~13.6, and the inner-product order with `B` transposed at
load models at the same ~12 glyphs a *single* MAC plus a 256-entry transpose --
which is the whole argument for the layout chosen here.

## The register file is the accumulator ring

`T` (the `t` countdown) and `G` (the group count) ride as **two header words at
the tail of the accumulator ring**, which turns exactly once per `t` -- so they
arrive under the man's hand at precisely the moment he needs them and the
five-slot rotation a separate register file would need disappears.  `Q` is left
holding only the three per-row values ``[I, M, K]``.

Constants ride the same way: `KONST` is a six-word ring
``[LANE, D, LANE, D, D, BIAS3]`` and one output group consumes exactly one lap.

## Pipes

``ri``/``so`` input and output; ``rx``/``sx`` the `A` entries; ``rb``/``sb`` the
packed `B` words; ``rc``/``sc`` the accumulators; ``rs``/``ss`` the one-word
spill; ``rq``/``sq`` the per-row file; ``rk``/``sk`` the constants.
"""

from __future__ import annotations

from collections import deque

__all__ = [
    "BIAS",
    "BIAS3",
    "LANE",
    "LANES",
    "WORKER",
    "cell_cost",
    "matmul_reference",
    "simulate",
]

#: Lanes packed into one word.  Three 21-bit fields fill 63 bits.
LANES = 3
#: Lane width as a power of two -- the divisor that splits a packed word.
LANE = 1 << 21
#: Per-lane bias.  Must exceed 16*99*99 = 156,816 and leave room below `LANE`.
BIAS = 1 << 19
#: The accumulator's reset value: `BIAS` in each of the three lanes.
BIAS3 = BIAS * (1 + LANE + LANE * LANE)


# ═════════════════════════════════════════════════════════════════ the program ═
#
# Tokens: `L<n>` literal, `ri`/`so` input and output, `rx`/`sx` `A`, `rb`/`sb`
# packed `B`, `rc`/`sc` accumulators, `rs`/`ss` spill, `rq`/`sq` file, `rk`/`sk`
# constants, `H` halt, and the plain glyphs.  `X` names three lanes
# (`neg`/`zero`/`pos`), `d` names two (`pos`/`zero`).
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # ══ HEAD: the three dimensions, then A straight into its ring ═════════════
    # `Q` during the load is `[ROWS, K, N, M]`; here it is still `[N, M, K]`.
    "HEAD": ([
        "ri", "sq", "ri", "sq", "ri", "sq",   # Q = [N, M, K]
        "rq", "sq", "M",                       # B = N
        "rq", "sq", "*",                       # A = N*M
        "b",                                   # BP = N*M
        # KONST during the load is just [LANE, LANE^2] -- one lap per group.
        "L21", "M", "L1", "{", "sk",
        "L42", "M", "L1", "{", "sk",
    ], "LOADA_GO"),
    # Every counted loop here is a do-while: `N*M`, `K`, `G` and the emit count
    # are all >= 1 by the constraints, so the entry test is dead weight and the
    # `d` that closes the body is the only branch the layout has to route.
    "LOADA_GO": (["ri", "sx", "m", "d"], {"pos": "LOADA_GO", "zero": "LOADB_HEAD"}),

    # ══ B arrives row-major, which is exactly the packing order ═══════════════
    "LOADB_HEAD": ([
        "rq", "sq", "rq", "sq", "rq", "sq",   # rotate to A = M
        "sq",                                  # ROWS = M
        "rq", "sq", "rq", "sq", "rq", "sq",   # head -> ROWS
    ], "BROW"),
    "BROW": (["rq", "X"], {"pos": "BROW_GO", "zero": "BLOAD_DONE", "neg": "BLOAD_DONE"}),
    "BROW_GO": ([
        "M", "L1", "W", "-", "sq",            # ROWS - 1
        "rq", "b", "sq",                       # BP = K entries left in this row
        "rq", "sq", "rq", "sq",                # N, M -- head back to ROWS
    ], "BGRP_GO"),
    # One group = up to three entries, low lane first.  The tail of a row is
    # zero-padded simply by not adding anything: a zero lane accumulates to the
    # bias and extracts as 0, and it is never emitted.
    "BGRP_GO": (["ri", "m", "ss"], "BL1"),
    "BL1": (["d"], {"pos": "BL1_R", "zero": "BL1_Z"}),
    "BL1_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "BL2"),
    "BL1_Z": (["rk", "sk"], "BL2"),
    "BL2": (["d"], {"pos": "BL2_R", "zero": "BL2_Z"}),
    "BL2_R": (["rk", "sk", "M", "ri", "m", "*", "M", "rs", "+", "ss"], "BGRP_END"),
    "BL2_Z": (["rk", "sk"], "BGRP_END"),
    "BGRP_END": (["rs", "sb", "d"], {"pos": "BGRP_GO", "zero": "BROW"}),

    # ══ rebuild the constants, the accumulator ring and the file ══════════════
    "BLOAD_DONE": ([
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
        "rq", "sq",                             # A = K  (BROW ate the ROWS slot)
        "M", "L2", "W", "+",                    # A = K + 2
        "M", "L3", "W", "/",                    # A = ceil(K/3) = G
        "b", "sq",                              # BP = G ; Q = [N, M, K, G]
        "L0", "sc",                             # accumulator ring header T
        "rq", "sq", "rq", "sq", "rq", "sq",     # rotate back round to G
        "rq", "sq", "sc",                       # header G
    ], "CFILL_GO"),
    "CFILL_GO": (["rs", "ss", "sc", "m", "d"], {"pos": "CFILL_GO", "zero": "QBUILD"}),
    "QBUILD": ([
        "rs",                                   # drop BIAS3 from the spill
        "rq", "sq", "rq", "sq", "rq", "sq",     # N, M, K
        "rq",                                   # drop G -- Q = [I, M, K]
    ], "ROW"),

    # ══ one output row ════════════════════════════════════════════════════════
    # `Q = [I, M, K]`, one lap per row.  The accumulator ring is
    # `[acc_0 .. acc_{G-1}, T, G]` and its two headers are read at the tail of
    # every `t`, which is the moment they are wanted.
    "ROW": (["rq", "X"], {"pos": "ROW_GO", "zero": "FIN", "neg": "FIN"}),
    "ROW_GO": ([
        "M", "L1", "W", "-", "sq",             # I - 1
        "rq", "sq", "M",                        # B = M
        "rc", "W", "sc",                        # T = M
        "rc", "b", "sc",                        # BP = G
        "rq", "sq",                             # K -- head back to I
    ], "TBODY"),
    "TBODY": (["rx", "ss"], "MAC"),
    "MAC": ([
        "rs", "ss", "M",                        # A = a, restored, B = a
        "rb", "sb",                             # A = P, restored
        "*",                                    # A = a*P              B = a
        "M",                                    # B = a*P
        "rc", "+", "sc",                        # acc += a*P
        "m", "d",
    ], {"pos": "MAC", "zero": "TTAIL"}),
    "TTAIL": (["rs", "rc", "M", "L1", "W", "-", "sc", "X"],
              {"pos": "TNEXT", "zero": "EMIT_SET", "neg": "EMIT_SET"}),
    "TNEXT": (["rc", "b", "sc"], "TBODY"),
    "EMIT_SET": ([
        "rc", "sc",                             # step the ring past G
        "rq", "sq", "rq", "sq", "rq", "b", "sq",  # BP = K values still to emit
    ], "GRP_GO"),

    # ══ unpack one accumulator word, emit up to three entries ═════════════════
    # KONST turns exactly one lap per group: LANE, D, LANE, D, D, BIAS3.
    "GRP_GO": ([
        "rk", "sk", "M",                        # B = LANE
        "rc", "/",                              # A = high lanes       B = lane 0
        "ss", "W", "M",                         # spill the quotient, B = lane 0
        "rk", "sk", "W", "-",                   # A = lane 0 - D
        "so", "m",
    ], "GG1"),
    "GG1": ([
        "rk", "sk", "M",
        "rs", "/",
        "ss", "W", "M",
        "rk", "sk", "W", "-",
    ], "LN1"),
    "LN1": (["d"], {"pos": "E1", "zero": "GG2"}),
    "E1": (["so", "m"], "GG2"),
    "GG2": (["rs", "M", "rk", "sk", "W", "-"], "LN2"),
    "LN2": (["d"], {"pos": "E2", "zero": "GEND"}),
    "E2": (["so", "m"], "GEND"),
    "GEND": (["rk", "sk", "sc", "d"], {"pos": "GRP_GO", "zero": "ROW"}),

    "FIN": (["H"], "FIN"),
}


# ═══════════════════════════════════════════════════════════════ the simulator ═
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

#: Pipe token -> the ring it names.  `ri`/`so` are the outside world.
_RINGS = {"x": "x", "b": "b", "c": "c", "s": "s", "q": "q", "k": "k"}


def cell_cost(toks: list[str]) -> int:
    """Grid cells a token run occupies -- a literal is written out in digits."""
    n = 0
    for t in toks:
        if t.startswith("L") and t != "L":
            v = t[1:]
            n += 1 if len(v) == 1 else len(v) + 2
        else:
            n += 1
    return n


def simulate(values: list[int], *, max_steps: int = 20_000_000) -> tuple[list[int], int, int]:
    """Run :data:`WORKER` over one case's input; return output, tokens, cells.

    An op-level model: `A`, `B`, a write-only `BP`, every ring a deque and the
    input a queue.  `tokens` counts glyph executions, `cells` weights literals
    by the digits they occupy -- the tick floor the layout starts from.
    """
    inp: deque[int] = deque(values)
    ring: dict[str, deque[int]] = {k: deque() for k in _RINGS.values()}
    out: list[int] = []
    a = b = bp = 0
    tokens = cells = 0

    block = "HEAD"
    while True:
        toks, succ = WORKER[block]
        branch: str | None = None
        for t in toks:
            tokens += 1
            cells += cell_cost([t])
            if tokens > max_steps:  # pragma: no cover - a runaway guard
                raise RuntimeError(f"worker did not settle (in {block})")
            if t == "H":
                return out, tokens, cells
            if t.startswith("L") and t != "L":
                a = int(t[1:])
            elif t == "ri":
                if not inp:  # pragma: no cover - the loader never over-reads
                    raise RuntimeError("input exhausted")
                a = inp.popleft()
            elif t == "so":
                out.append(a)
            elif t[0] == "r" and t[1:] in _RINGS:
                a = ring[t[1:]].popleft()
            elif t[0] == "s" and t[1:] in _RINGS:
                ring[t[1:]].append(a)
            elif t == "M":
                b = a
            elif t == "W":
                a, b = b, a
            elif t == "N":
                a = -a
            elif t == "/":
                a, b = a // b, a % b
            elif t == "%":
                a = a % b if b else 0
            elif t in _BIN:
                a = _BIN[t](a, b)
            elif t == "b":
                bp = a
            elif t == "m":
                bp -= 1
            elif t == "]":
                bp >>= 1
            elif t == "X":
                branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
            elif t == "x":
                branch = "one" if bp & 1 else "zero"
            elif t == "d":
                branch = "pos" if bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
        block = succ if isinstance(succ, str) else succ[branch]


def matmul_reference(values: list[int]) -> list[int]:
    """The problem statement, straight, for the tests to check against."""
    n, m, k = values[0], values[1], values[2]
    mat_a = values[3:3 + n * m]
    mat_b = values[3 + n * m:3 + n * m + m * k]
    return [
        sum(mat_a[i * m + t] * mat_b[t * k + j] for t in range(m))
        for i in range(n)
        for j in range(k)
    ]


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    import random

    random.seed(0)
    case = [16, 16, 16] + [random.randint(-99, 99) for _ in range(512)]
    got, tok, cel = simulate(case)
    assert got == matmul_reference(case)
    print(f"{len(WORKER)} blocks; 16x16x16: {tok} tokens, {cel} cells, "
          f"{cel / 4096:.2f} cells/MAC")
