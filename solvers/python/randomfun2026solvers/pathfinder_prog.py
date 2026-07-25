"""The pathfinder machine as a block graph of straight glyph runs.

Cell (x, y) has display address ``p = 16*y + x``; the plane's bit index is
``g = 255 - p`` -- a 180-degree rotation -- because the setup loop can only
build a word with ``w = 2*w + bit``, which puts the first cell read at the
*top* bit.  Working rotated costs nothing: the board is symmetric and only
the tie-break order flips, which is a different test order and nothing else.

g-word ``j`` holds bits ``64j..64j+63``; ring group ``pos`` (production
order) is g-word ``j = 3 - pos``.

Every plane word is non-negative: bit 63 of word j is g = 64j+63, i.e.
p = 192-64j, i.e. column 0, always a border wall.  So ``}`` is a logical
shift on everything we shift, and an out-of-range shift yields 0 -- which is
what makes "test a bit in whichever word holds it" branch-free.

Ring, 18 words: ``[P, Q, g0, g1, g2, g3]``, group = ``[S1, NB, S2, S3]``.

    P   the robot's display address    Q   the flag's display address
    NB  open cells (path AND unvisited); NB' = NB ^ new is one glyph and
        doubles as the wall mask, so no separate free plane is needed
    S1  newest labelled residue (the wave being expanded)
    S2  the residue about to be written
    S3  the residue one step closer to the flag than S1

``free = NB | S1 | S2 | S3`` always holds, so the board never needs a fifth
plane, and the three label planes rotate for free -- pushing them back in a
different order IS the rotation.

**Every ring touch must be a whole 18-word lap.**  A ring is a FIFO, so
reading two words and pushing them back rotates it by two; a block that peeks
at P without finishing the lap silently shears the whole board.  So the robot
address also rides in ``F`` between laps, and the round dispatch reads it from
there rather than from the ring.

``F`` is the spill FIFO used as a rotating register file, ``G`` the scratch
FIFO.  ``F == [p]`` and ``G == []`` at every block boundary outside a lap.
"""
from __future__ import annotations

from collections import deque


#: Words resident in the ring: [P, Q] plus four groups of [S1, NB, S2, S3].
RING_WORDS = 18
#: High-water marks of the two auxiliary loops (measured by the op-level model).
FIFO_WORDS = 6
SCRATCH_WORDS = 7


def L(v: int) -> str:
    return f"L{v}"


def lap(P, name, nxt, body):
    """A BP-counted four-iteration pass over the ring's four groups."""
    P[name + "T"] = (["d"], {"pos": name + "B", "zero": nxt})
    P[name + "B"] = ([*body, "m"], name + "T")


def build() -> dict:
    P: dict[str, tuple[list[str], object]] = {}

    # ══ INIT: 256 cells -> four packed words ════════════════════════════════
    # w = 2w + (1-v) with w living in B, so the packing body touches only the
    # input pipe and can be a plain counted loop.
    P["INIT"] = ([L(256), "sp", L(0), "sp", L(4), "sg"], "OUTT")
    P["OUTT"] = (["rg", "X"], {"zero": "AFTERLOAD", "pos": "OUTB", "neg": "HALT"})
    P["OUTB"] = (["M", L(1), "W", "-", "sg", L(64), "b", L(0), "M"], "PACKT")
    lap(P, "PACK", "PACKEND", ["ri", "N", "+", "+", "M", L(1), "+", "M"])
    P["PACKEND"] = (["sr", L(0), "sr", L(0), "sr", L(0), "sr"], "OUTT")

    # ══ paint the board straight out of the four words ══════════════════════
    # colour 7 where the bit is 0 (wall), 0 where it is 1 (path).  The top bit
    # is the sign, so `X` reads it with no shift and without touching B.  The
    # dummy Q pushed first makes this 16-word rotation double as an alignment
    # pass, leaving the ring as [Q, g0..g3].
    P["AFTERLOAD"] = ([L(0), "sr", L(4), "sg"], "POUTT")
    P["POUTT"] = (["rg", "X"], {"zero": "SETROBOT", "pos": "POUTB", "neg": "HALT"})
    P["POUTB"] = ([
        "M", L(1), "W", "-", "sg",
        "rr", "sr", "sf",
        "rr", "sr", "rr", "sr", "rr", "sr",
        L(64), "b", "rf", "M",
    ], "PBITT")
    P["PBITT"] = (["d"], {"pos": "PBITB", "zero": "POUTT"})
    P["PBITB"] = (["M", "X"], {"neg": "PB1", "zero": "PB0", "pos": "PB0"})
    P["PB1"] = ([L(0), "sp", "W", "M", "+", "m"], "PBITT")
    P["PB0"] = ([L(7), "sp", "W", "M", "+", "m"], "PBITT")

    # ══ the robot's start ═══════════════════════════════════════════════════
    # 17 rotations, not 16: the ring is [Q, g0..g3, P] and rotation preserves
    # the cyclic order, so [P, Q, g0..g3] is the only reachable arrangement.
    P["SETROBOT"] = ([
        "ri", "sf",
        "ri", "M", L(4), "W", "{", "M",
        "rf", "+",                     # A = p
        "sr", "sf",                    # ring tail, and F = [p] for good
        L(1), "sp", "rf", "sf", "sp", L(10), "sp", L(0), "sp",
        L(4), "b",
    ], "ALIGNT")
    lap(P, "ALIGN", "ALIGNEND",
        ["rr", "sr", "rr", "sr", "rr", "sr", "rr", "sr"])
    P["ALIGNEND"] = (["rr", "sr"], "MAIN")

    # ══ round dispatch: q == p emits no frames, not even the flag pixel ═════
    P["MAIN"] = ([
        "ri", "sf",                    # F = [p, fx]
        "ri", "M", L(4), "W", "{", "M",   # B = 16*fy
        "rf", "sf", "rf", "+",         # A = q,  F = [p]
        "sg", "sg",                    # G = [q, q]
        "rf", "sf", "M",               # B = p,  F = [p]
        "rg", "-", "X",                # A = q - p,  G = [q]
    ], {"zero": "SKIP", "pos": "ROUND", "neg": "ROUND"})
    P["SKIP"] = (["rg"], "MAIN")

    P["ROUND"] = ([
        L(1), "sp", "rg", "sg", "sp", L(9), "sp",   # flag pixel, uncommitted
        "rg", "sg",                    # A = q,  G = [q]
        "M", L(63), "-", "sf",         # F = [p, k], k = 63 - q
        L(4), "b",
    ], "SEEDPRE")
    P["SEEDPRE"] = (["rr", "sr", "rr", "rg", "sr"], "SEEDT")
    lap(P, "SEED", "SEEDEND", [
        "rr", "M", "rr", "|", "M", "rr", "|", "M", "rr", "|",   # A = free
        "sg",                          # G = [free]
        "rf", "sf", "rf",              # rotate p; A = k, F = [p]
        "sg",                          # G = [free, k]
        "M", L(1), "{",                # A = one-hot (0 outside this word)
        "sr",                          # S1' = one-hot
        "M", "rg", "~", "sr",          # NB' = free ^ one-hot
        L(0), "sr", L(0), "sr",
        "rg", "M", L(64), "+", "sf",   # k += 64  ->  F = [p, k]
    ])
    P["SEEDEND"] = (["rf", "sf", "rf"], "ITERPRE")

    # ══ ITER: one lap building the 33-bit window around the robot ═══════════
    # window bit i = plane bit (s + i) with s = g_robot - 16 = 239 - p, so
    #   up = bit 32, right = bit 15, down = bit 0, left = bit 17
    # -- and that IS the tie-break order.  Per group kw = 47 - p + 64*pos, and
    # (S1 >> kw) | (S1 << -kw) is a genuine 128-bit window shift: at most one
    # term is non-zero, and where both are they agree.
    # F cycles [kw, acc, Q, p] -- kw first.  ``M`` puts kw in B, and B survives
    # every pipe op, so popping kw *before* the ring read lets the whole G run
    # sit between one F run and the next: the body's pipe-zone string collapses
    # from F F F F R R G G F F G G G G F F R*6 (6 transitions) to
    # F F R R G*6 F*6 R*6 (4), and this body runs 420 times a case.
    P["ITERPRE"] = ([
        "rr", "sr",                    # P straight back (A = P, which is p)
        "M", L(47), "-", "sf",         # F = [p, kw]
        L(0), "sf",                    # F = [p, kw, acc]
        "rr", "sr", "sf",              # Q back;  F = [p, kw, acc, Q]
        "rf", "sf",                    # rotate p -> F = [kw, acc, Q, p]
        L(4), "b",
    ], "ITERT")
    lap(P, "ITER", "ITEREND", [
        "rf", "M", L(64), "+", "sf",   # pop kw (B = kw), push kw + 64
        "rr", "sr", "sg", "sg",        # S1 back on the ring, two copies in G
        "rg", "}", "sg",               # t1 = S1 >> kw
        "W", "N", "M",                 # B = -kw
        "rg", "{", "M",                # t2 = S1 << -kw
        "rg", "|", "M",                # t = t1 | t2
        "rf", "|", "sf",               # acc |= t
        "rf", "sf", "rf", "sf",        # rotate Q, p
        "rr", "sr", "rr", "sr", "rr", "sr",
    ])
    P["ITEREND"] = (["rf", "rf", "sg"], "TU")

    for name, bit, hit, miss in (("TU", 32, "MVUP", "TR"),
                                 ("TR", 15, "MVRIGHT", "TD"),
                                 ("TD", 0, "MVDOWN", "TL"),
                                 ("TL", 17, "MVLEFT", "WAVEPRE")):
        toks = ["rg", "sg"]
        if bit:
            toks += ["M", L(bit), "W", "}"]
        P[name] = ([*toks, "b", "x"], {"one": hit, "zero": miss})

    for name, delta in (("MVUP", -16), ("MVRIGHT", 1), ("MVDOWN", 16),
                        ("MVLEFT", -1)):
        step = ["M", L(abs(delta))] + (["N"] if delta < 0 else []) + ["+"]

        # A painter run is `1, addr, colour`, and the two constants clobber A --
        # but not B.  Parking the address in B rather than in the scratch FIFO
        # turns `sg sg 1 sp rg sp 0 sp` into a straight `M 1 sp W sp M 0 sp W`
        # with no pipe op at all between the sends: 11 pipe-zone transitions
        # become 4, in the block that runs once per robot move.
        def paint(colour: int) -> list[str]:
            return ["M", L(1), "sp", "W", "sp", "M", L(colour), "sp", "W"]

        P[name] = ([
            "rg",                      # drop acc
            "rf", "sf", "rf",          # rotate Q; A = p, F = [Q]
            *paint(0),                 # the vacated cell back to path; A = p
            *step,                     # A = p'
            *paint(10),                # the entered cell; A = p'
            "sf",                      # F = [Q, p']
            L(0), "sp",                # commit the frame
        ], "ROTPRE")

    P["ROTPRE"] = ([
        "rr",                          # old P off the ring, dropped
        "rf", "rf",                    # drop F's Q; A = p'
        "sr", "sg",                    # p' onto the ring, parked in G
        "rr", "sr", "M",               # Q back, B = Q
        "rg", "sf",                    # F = [p']
        "-", "X",                      # A = p' - Q
    ], {"zero": "DONE", "pos": "ROTPRE2", "neg": "ROTPRE2"})
    # The robot is standing on the flag: the round is over, but ROTPRE has
    # already turned the ring by two, so the lap still has to be finished.
    P["DONE"] = ([L(4), "b"], "NULLT")
    lap(P, "NULL", "MAIN",
        ["rr", "sr", "rr", "sr", "rr", "sr", "rr", "sr"])
    P["ROTPRE2"] = ([L(4), "b"], "ROTT")
    # walk rotation: S1' = S3, S2' = S1, S3' = S2, NB unchanged.  Uses only G,
    # so the robot address can stay parked in F across the lap.
    # NB is the second word pushed, so it rides in B across the two reads that
    # follow instead of going through G: 19 tokens and 12 zone transitions
    # become 15 and 8.
    lap(P, "ROT", "ITERPRE", [
        "rr", "sg",                    # S1 -> G
        "rr", "M",                     # NB stays in B
        "rr", "sg",                    # S2 -> G
        "rr", "sr",                    # S3 becomes S1'
        "W", "sr",                     # NB unchanged
        "rg", "sr",                    # S1 becomes S2'
        "rg", "sr",                    # S2 becomes S3'
    ])

    # ══ WAVE: lap A stashes the backward carries, lap B expands ═════════════
    # c[j] = (f<<1)|(f>>1)|(f<<16)|(f>>16) | (f[j-1]<<48) | (f[j+1]>>48)
    # f[j-1] is the previous group -- a register carry -- but f[j+1] is the
    # next one, so lap A collects f>>48 for every group and lap B pops them
    # one ahead.  The seam terms are free: row 0 and row 15 are all wall, so
    # the carries that wrap round the ring are identically zero.
    P["WAVEPRE"] = (["rg", "rf", "rr", "sr", "rr", "sr", L(4), "b"], "LAPAT")
    lap(P, "LAPA", "LAPAEND", [
        "rr", "sr", "M", L(48), "W", "}", "sf",
        "rr", "sr", "rr", "sr", "rr", "sr",
    ])
    # lap B needs its own P/Q pass-through: every ring touch is a whole lap.
    P["LAPAEND"] = ([L(0), "sf", "rf", "sf", "rf",
                     "rr", "sr", "rr", "sr", L(0), "sg", L(4), "b"], "LAPBT")
    lap(P, "LAPB", "LAPBEND", [
        "rr",                                # A = f
        "sg", "sg", "sg", "sg", "sg", "sg",  # six copies
        "rg", "sg",                          # rotate the incoming carry back
        "rg", "M", L(1), "W", "{", "sg",     # f << 1
        "rg", "}", "sg",                     # f >> 1   (B is still 1)
        "rg", "M", L(16), "W", "{", "sg",    # f << 16
        "rg", "}", "sg",                     # f >> 16  (B is still 16)
        "rg", "M", L(48), "W", "{", "sg",    # the carry for the next group
        "rg", "sg",                          # park the sixth copy of f
        "rg", "M",                           # B = incoming carry
        "rg", "|", "M", "rg", "|", "M", "rg", "|", "M", "rg", "|",
        "M", "rf", "|",                      # c = self | fwd | bcq
        "sg",
        "rr", "sg", "sg",                    # NB, twice
        "rg", "sg", "rg", "sg",              # rotate the carry and f
        "rg", "M",                           # B = c
        "rg", "&",                           # A = new = NB & c
        "sg", "sg",
        "rr", "M",                           # B = S2
        "rg", "sg", "rg", "sg", "rg", "sg",  # rotate NB, carry, f
        "rg", "|", "sr",                     # S1' = S2 | new
        "rg", "M", "rg", "~", "sr",          # NB' = NB ^ new
        "rr", "sr",                          # S3 becomes S2'
        "rg", "sg", "rg", "sr",              # f becomes S3'
    ])
    P["LAPBEND"] = (["rg"], "ITERPRE")
    return P


# ── op-level model ──────────────────────────────────────────────────────────
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
