#!/usr/bin/env python3
"""Systolic matmul for littleman — a P-stage MAC chain over the contraction index.

WHY THIS SHAPE (read this before touching anything)
===================================================

`matmul` scores ``max(w,h)^2 * avgTicks``.  The serial machine on ``main``
(``matmul_packed.man``) is 85x92 / 29,097 avg ticks = 2.463e8, and the previous
agent proved that splitting *that* architecture across two men costs 1.7x rather
than saving: on a band-based build every extra man needs its own spill,
accumulator and rings, and a ring is a band, and a band is charged on the side.
See ``littleman/ARCH.md`` §8.2.

A systolic array fixes that because every operand hop is between two *adjacent
rooms*, not through a shared ring.  But the obvious systolic shape —
output-stationary N x K PEs with `a` flowing east and `b` south — needs
N*K <= 256 processing elements.  That will never fit.

**The shape that does fit is a chain over the contraction index `t`.**

    C[i][j] = sum_t A[i][t] * B[t][j]

Put one PE per `t`.  M <= 16, so the chain is **16 stages, fixed**, independent
of the case; stages with t >= M are fed a zero weight and contribute nothing, so
the machine is completely *oblivious* — no data-dependent control flow anywhere
in the array, every loop count is either a literal or a value read from a pipe.

Per stage p:

  * ``weight`` = A[i][p], the current row's p-th entry.  It changes once per
    output row (N times over the whole run) and lives in the man's **B** hand.
  * ``B[p][0..K-1]`` recirculates forever in a per-stage **ring** (a pipe loop
    through a turnaround room).  The ring's first value is **K itself**, so the
    stage re-reads its own trip-count every row and never needs a literal for it
    — this is what lets the machine avoid padding K to 16 and paying 16/K on
    small cases.
  * the partial sum flows p -> p+1 through a chain of adder rooms.  That chain
    is what enforces *order*: an `R`-based fan-in would let a fast stage put two
    products into one sum.

Throughput is one output value per adder-loop revolution (~12-16 ticks), so a
16x16x16 case is ~256 beats ~= 4k ticks against the serial machine's 123,254.

THE FOUR ROOMS
==============

Every stage p (F := P-1-p is how many values it must pass along) is four rooms:

``LOADF_p`` — feeder.  Owns the one incoming *chain* pipe, so **every `r` in it
is unambiguous**.  Two phases::

    INIT   r          A = K            (first value of this stage's b-block)
           s          -> ring          (K goes into the ring first)
           M          B = K
           b          BP = K
           LA: K x { r ; s }           -> ring: B[p][0..K-1]
           1 ; + ; M                   A = B = K+1        (B survived LA)
           <F-part>                    A = F*(K+1)        (see `_times_const`)
           b
           LB: x { r ; s }             -> chain: forward the other stages' blocks

    MAIN   r          A = A[i][p]
           s          -> MUL_p         (this row's weight)
           <F> ; b
           LC: F x { r ; s }           -> chain: rest of row i
           loop

``MUL_p`` — the multiplier.  Holds the weight in B for the whole row::

           r          A = weight        (from LOADF_p)
           M          B = weight
           r          A = K             (ring)
           s          -> ring
           b          BP = K
           L4: K x { r ; s ; * ; s }    ring-read, ring-writeback, A=b*w, product
           loop

``TURN_p`` — 5x2 interior, ``>@Rsv`` / ``^...<``.  `R` takes from *either*
incoming pipe (LOADF's initial fill, or MUL's writeback) and `s` has one
outgoing pipe, so it cannot mis-bind.  The TURN->MUL leg must hold K+1 <= 17
values while the array is still loading, so it is drawn long on purpose.

``ADD_p`` — ``r ; M ; r ; + ; s`` (p >= 1) or ``r ; s`` (p = 0, nothing to add
to yet).  Both incoming mouths are on the north wall (psum from the band above,
product from MUL directly overhead) and split purely by column; psum leaves
south.  ADD_{P-1} feeds `O`.

THE INPUT STREAM THE ARRAY WANTS
================================

    [block_0][block_1]...[block_{P-1}]   block_p = K, B[p][0], ..., B[p][K-1]
    [row_0][row_1]...[row_{N-1}]         row_i   = A[i][0..P-1], zero-padded

Note the blocks come **first** and A comes second, while the *problem* delivers
N M K, then A, then B.  So a loader has to hold A back while B loads.  That is
the one genuinely expensive thing in the front end and it is why this file
carries `stream_for` (the reference ordering) and why the loader is specified —
but not yet built — at the bottom of this docstring.

PIPE BINDING IS SOLVED, NOT GUESSED
===================================

`s`/`r` bind to the *nearest* pipe mouth by Manhattan distance with ties broken
in reading order, and in a machine of small rooms that is the dominant hazard: a
mis-bound op is invisible in the grid and invisible in the answer's shape and
wrong only in its value.  So this module never hand-places a mouth.  Every room
declares which target each `s`/`r` cell must reach and `solve_ports` searches
wall positions until **every** op binds its target with a strict margin.  If no
placement exists the build raises rather than emitting a plausible grid.

`check_mouths` additionally counts arrowheads-against-a-wall the way the
*runtime* does (`lm.mjs analyze` under-reports these) and refuses a surprise.

WHAT IS BUILT AND WHAT IT MEASURED  (2026-07-27)
================================================

`probe(1, [[3],[4]], [[5,6]])` builds a **complete one-stage machine** — SRC,
LOADF, TURN, ring, MUL, ADD, O, seven pipes, every binding solved — and the
reference engine runs it to ``15 18 20 24``, which is exactly
``[[3],[4]] x [[5,6]]``.  So the mechanism is proven end to end: the K-prefixed
ring, the `R` turnaround, the weight handoff, the multiply with the weight
parked in B, and the psum relay.

Measured on that grid (45 x 68, 29% dense):

    output 1 at tick 163   output 2 at 175   output 3 at 307   output 4 at 319

**The beat is 12 ticks** (163->175 and 307->319).  That is the number the whole
projection rests on and it is better than the 12-16 that was assumed.  The
132-tick gap between rows is *not* architectural: the probe's TURN->MUL leg is
serpentined to ~60 cells, and with only K+1 = 3 values in it the multiplier
waits a full revolution at every row boundary.  A production ring wants
``L ~= K + 4`` (>= 18 cells, so 17 values fit while loading, and no longer),
which makes a row cost ``max((K+1)*12, L)`` = ``(K+1)*12`` for every K >= 2.

Projected for the real 16-stage machine:

    compute   N*K beats x 12 ticks         16x16x16 -> 3,072
    b-load    LOADF_0 relays (P-1)(K+1)    16x16x16 -> ~1,500
    total                                  16x16x16 -> ~5,000 ticks

against the serial machine's 123,254 on that case — a **25x tick win** — and an
average across the seven local cases somewhere near 2,000-3,000 against 29,097.

That is what makes the area budget generous.  At avg 3,000 ticks the machine
beats the 197,437,831 live score (at the measured 1.506 judged ratio) for any
side under **209**; at side 100 it lands at 3.0e7 local / 4.5e7 judged, 4.4x
better than the bar.  Summed room area is ~9,000 cells (LOADF ~250 avg x 16,
MUL 132 x 16, TURN 28 x 16, ADD 40 x 15, rings 20 x 16, front end ~1,500), so a
side of 100-130 is the realistic target and the build is worth finishing.

WHAT BLOCKED P >= 2, EXACTLY
============================

The stage rooms and their bindings are all solved and general in `p`.  What is
not solved is the **floor plan**, and the obstruction is specific:

1. MUL's north-wall column order is *forced* to ``a_in < ring_out < ring_in``.
   MUL's first `r` (the weight) sits west of its second (the ring), so the
   a-mouth must be the western one; the L4 body's `r` sits further east still,
   which pins ring_in east of both.  Verified by exhaustion in `solve_ports`.
2. Therefore the ring partner (TURN) has to sit **east** of the stage: its two
   pipes then nest *outside* the weight pipe, which drops straight down from
   LOADF.  Put TURN west and the serpentine has to cross the weight column at a
   row where that column is live, and no row assignment exists.
3. But LOADF's `s` cells put the ring writes (INIT head, LA) at the **west** end
   of its INIT row and the chain writes (LB, LC) at the **east** end, so
   `solve_ports` can only ever put ``ring_out`` north/west and ``chain_out``
   east.  The ring-fill pipe then has to travel east *over* LOADF to reach TURN
   while the chain pipe also has to leave eastward, and the two cannot be
   nested: whichever turns higher is crossed by the other's riser.  Every row
   assignment tried collides, and the collision is a hard `Circuit.set` error,
   not a silent one.

THE FIX, FOR WHOEVER PICKS THIS UP
==================================

Make LOADF's INIT row run **right to left**.  Lay it with ``Circuit.run(..., d=W)``
and a mirrored counted loop so that LA (the ring writes) ends up at the *east*
end and LB/LC (the chain writes) at the *west* end.  `solve_ports` will then put
``ring_out`` on the east wall facing TURN and ``chain_out`` on the west wall
facing a chain channel, and the two pipes never share a corridor.
`Circuit.counted_loop` only builds an east-entered loop, so add a westward
variant (it is the same five glyphs mirrored) or place those twelve cells by
hand.

The cheaper alternative, if the mirror is awkward: **split LOADF into two
rooms** — LOAD (INIT only: chain in, chain out, ring out) and FEED (MAIN only:
chain in, chain out, mul out).  Each then has at most two outgoing pipes and the
three-way `s` split disappears entirely.  It costs 16 more rooms but they are
small, and it also shortens the widest room (the f=15 INIT row is 26 wide, which
is what sets `lw` and therefore the strip pitch).

After that the remaining work is: the front end (see the stream section above —
GATE / A-padder / B-blocker plus a ~256-cell delay line, because A arrives
before B but B is what has to be resident), and packing the 16 stages into a
4x4 or 2x8 arrangement instead of the probe's single column.
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass, field

from randomfun2026solvers.circuit import GLYPH, Circuit, Collision, E, W, N, S

# ── little helpers ───────────────────────────────────────────────────────────

WALLS = ("N", "S", "W", "E")


def digits(n: int) -> str:
    """Glyphs that leave A = n **without touching B**.  n must be 0..9."""
    if not 0 <= n <= 9:
        raise ValueError(f"{n} is not a single digit; B is live here")
    return str(n)


def const_free(n: int) -> str:
    """Glyphs that leave A = n when **B is scratch** (0 <= n <= 18).

    Backticks are avoided everywhere in this file: they pair per column as well
    as per row, so a literal in one room can pair vertically with a literal in
    another and swallow a wall glyph, which is a load error nobody sees coming.
    """
    if 0 <= n <= 9:
        return str(n)
    if n <= 18:
        return f"9M{n - 9}+"           # A=9, B=9, A=n-9, A=(n-9)+9
    raise ValueError(f"{n} needs a literal")


def _times_const(f: int) -> str:
    """Glyphs that turn (A = B = x) into A = f*x, leaving B alone-ish.

    ``<d> *`` gives d*x for d <= 9; the remainder is added on with `+`, which
    is legal because `*` leaves B = x.  f = 0 is the empty program and the
    caller must skip the loop entirely.
    """
    if f <= 0:
        raise ValueError("f=0 has no loop")
    if f == 1:
        return ""                      # A is already x
    d = min(f, 9)
    return digits(d) + "*" + "+" * (f - d)


# ── port placement ───────────────────────────────────────────────────────────


@dataclass
class PortSpec:
    """One pipe mouth: which walls it may sit on, and the ops that must reach it."""
    name: str
    kind: str                       # "in" | "out"
    walls: tuple[str, ...] = WALLS


def _mouth_xy(wall: str, off: int, w: int, h: int) -> tuple[int, int]:
    """Interior-relative coordinates of the pipe cell just outside `wall`."""
    return {"N": (off, -1), "S": (off, h), "W": (-1, off), "E": (w, off)}[wall]


def _cands(wall: str, w: int, h: int) -> range:
    return range(w) if wall in "NS" else range(h)


def solve_ports(
    w: int,
    h: int,
    specs: list[PortSpec],
    ops: list[tuple[tuple[int, int], str, str]],
    *,
    margin: int = 1,
    sep: int = 3,
) -> dict[str, tuple[str, int]]:
    """Choose a wall+offset for every port so each op binds its declared target.

    `ops` is [( (x,y), 'r'|'s', target_port_name )].  Returns {port: (wall,off)}.
    Searches incoming and outgoing groups independently (they never compete).
    """
    out: dict[str, tuple[str, int]] = {}
    for kind in ("in", "out"):
        group = [s for s in specs if s.kind == kind]
        want = [o for o in ops if (o[1] in "rRUq") == (kind == "in")]
        if not group:
            if want:
                raise Collision(f"{len(want)} {kind} ops but no {kind} port")
            continue
        choices = [
            [(wall, off) for wall in s.walls for off in _cands(wall, w, h)
             if (wall, off) not in out.values()]
            for s in group
        ]
        best = None
        for combo in itertools.product(*choices):
            placed = list(zip([s.name for s in group], combo)) + list(out.items())
            if any(a[1][0] == b_[1][0] and abs(a[1][1] - b_[1][1]) < sep
                   for a, b_ in itertools.combinations(placed, 2)):
                continue                    # two mouths crowding one wall
            mouths = {
                s.name: _mouth_xy(wall, off, w, h)
                for s, (wall, off) in zip(group, combo)
            }
            if len(set(mouths.values())) != len(mouths):
                continue
            worst = 1 << 30
            ok = True
            for (x, y), _g, target in want:
                d = {
                    n: abs(x - mx) + abs(y - my) for n, (mx, my) in mouths.items()
                }
                mine = d.pop(target)
                slack = min(d.values()) - mine if d else 1 << 30
                if slack < margin:
                    ok = False
                    break
                worst = min(worst, slack)
            if ok and (best is None or worst > best[0]):
                best = (worst, combo)
                if worst >= 4:          # comfortable; stop searching
                    break
        if best is None:
            raise Collision(
                f"no {kind} port placement in {w}x{h} satisfies "
                + ", ".join(f"{o[0]}->{o[2]}" for o in want)
            )
        for s, wo in zip(group, best[1]):
            out[s.name] = wo
    return out


# ── a room: interior circuit + solved ports ──────────────────────────────────


@dataclass
class Room:
    name: str
    w: int
    h: int
    cells: dict[tuple[int, int], str]
    ports: dict[str, tuple[str, int]]        # name -> (wall, offset)
    x: int = 0                               # placed box origin (set by Layout)
    y: int = 0

    def rows(self) -> list[str]:
        body = []
        for y in range(self.h):
            body.append("".join(self.cells.get((x, y), " ") for x in range(self.w)))
        top = "+" + "-" * self.w + "+"
        return [top] + ["|" + r + "|" for r in body] + [top]

    @property
    def bw(self) -> int:
        return self.w + 2

    @property
    def bh(self) -> int:
        return self.h + 2

    def wall_cell(self, port: str) -> tuple[int, int]:
        """Absolute grid coordinate of the *wall* cell the pipe attaches to."""
        wall, off = self.ports[port]
        if wall == "N":
            return (self.x + 1 + off, self.y)
        if wall == "S":
            return (self.x + 1 + off, self.y + self.bh - 1)
        if wall == "W":
            return (self.x, self.y + 1 + off)
        return (self.x + self.bw - 1, self.y + 1 + off)

    def mouth(self, port: str) -> tuple[int, int]:
        """Absolute coordinate of the first pipe cell outside the wall."""
        wall, _off = self.ports[port]
        wx, wy = self.wall_cell(port)
        d = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[wall]
        return (wx + d[0], wy + d[1])

    def outward(self, port: str) -> tuple[int, int]:
        return {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[self.ports[port][0]]


def build_room(name, w, h, lay, specs, ops, *, margin=1) -> Room:
    """`lay(c)` draws the interior on a Circuit; `ops` declare the bindings.

    Mouths are kept `sep` apart on a shared wall so the pipes leaving them do
    not have to squeeze past each other; the requirement is relaxed only if no
    placement exists at all.
    """
    c = Circuit(w, h)
    lay(c)
    for sep in (3, 2, 1):
        try:
            ports = solve_ports(w, h, specs, ops, margin=margin, sep=sep)
        except Collision:
            continue
        return Room(name, w, h, dict(c.cell), ports)
    raise Collision(f"{name}: no port placement in {w}x{h}")


# ── the four stage rooms ─────────────────────────────────────────────────────


def turn_room(name: str) -> Room:
    """Ring turnaround: `R` from either source, `s` to the one outgoing pipe."""
    def lay(c: Circuit) -> None:
        c.run(0, 0, ">@Rsv")
        c.set(0, 1, "^")
        c.set(4, 1, "<")

    return build_room(
        name, 5, 2, lay,
        [PortSpec("fill", "in", ("N",)), PortSpec("back", "in", ("W",)),
         PortSpec("out", "out", ("S",))],
        [],                                   # `R` and a lone `s` cannot mis-bind
    )


def add_room(name: str, first: bool) -> Room:
    """psum chain stage.  `first` has nothing to add to, so it just relays."""
    body = "rs" if first else "rMr+s"
    w = len(body) + 3

    def lay(c: Circuit) -> None:
        c.set(0, 0, ">")
        c.set(1, 0, "@")
        c.run(2, 0, body)
        c.set(w - 1, 0, "v")
        c.set(w - 1, 1, "<")
        c.set(0, 1, "^")

    specs = [PortSpec("psum_out", "out", ("S",))]
    ops: list[tuple[tuple[int, int], str, str]] = []
    if first:
        specs.append(PortSpec("prod_in", "in", ("N",)))
    else:
        # Both incoming mouths are on the north wall and split purely by column,
        # and the *product* is read first on purpose: `+` is commutative, so
        # reading it first puts prod_in west and psum_in east.  That is what lets
        # the product drop straight down from MUL (which is directly overhead and
        # west) while the psum arrives from an east channel — and it is what frees
        # the west channel for the chain.  Read psum first and both channels end
        # up on the same side, where the chain's riser and the psum's riser cross
        # at every band and no row assignment exists.
        specs += [PortSpec("prod_in", "in", ("N",)),
                  PortSpec("psum_in", "in", ("N",))]
        ops = [((2, 0), "r", "prod_in"), ((4, 0), "r", "psum_in")]
    return build_room(name, w, 2, lay, specs, ops)


def mul_room(name: str) -> Room:
    """weight in B, ring in/out, product out.  Loop count K comes off the ring."""
    W_, H_ = 10, 9

    def lay(c: Circuit) -> None:
        c.run(0, 0, ">@rMrsb")
        ex, _ = c.counted_loop(7, 0, "rs*s")          # cols 7,8; rows 0..5
        c.route((ex, 0), E, [(ex, H_ - 1), (0, H_ - 1)], (0, 0), E)

    # a_in, ring_in and ring_out all face the band above (LOADF and TURN);
    # the product drops south into ADD.  `a_in` on the west is unsolvable here:
    # the second `r` (ring) sits east of the first, so the ring mouth has to be
    # the one further east, which only works if both are on the north wall.
    specs = [
        PortSpec("a_in", "in", ("N",)), PortSpec("ring_in", "in", ("N",)),
        PortSpec("ring_out", "out", ("N",)), PortSpec("prod_out", "out", ("S",)),
    ]
    ops = [
        ((2, 0), "r", "a_in"),
        ((4, 0), "r", "ring_in"),
        ((5, 0), "s", "ring_out"),
        ((8, 1), "r", "ring_in"),
        ((8, 2), "s", "ring_out"),
        ((8, 4), "s", "prod_out"),
    ]
    return build_room(name, W_, H_, lay, specs, ops)


def counted_loop_west(c: Circuit, x: int, y: int, body: str) -> tuple[int, int]:
    """`counted_loop` mirrored: entered heading **west** at (x,y), exits west.

    The peel glyph has to be `a`, not `d`: clockwise from west is north, which
    walks the man into the ceiling.  Counter-clockwise from west is south, into
    the body, which is what the eastward loop gets from `d`.

        (x,y)=`<`  (x-1,y)=`a`          a: BP>0 -> CCW/south into the body
        (x-1,y+1..)= body                  BP==0 -> straight west, out
        (x-1,y+k+1)=`>`  (x,y+k+1)=`^`
        (x,y+1)=`m`
    """
    k = len(body)
    c.set(x, y, "<")
    c.set(x - 1, y, "a")
    c.run(x - 1, y + 1, body, d=S)
    c.set(x - 1, y + k + 1, ">")
    c.set(x, y + k + 1, "^")
    c.set(x, y + 1, "m")
    c.blanks(x, y + 2, k - 1, d=S)
    return x - 2, y


def loadf_room(name: str, f: int) -> Room:
    """Feeder, laid **right to left**.

    Running the code westward is the whole point: it puts the ring writes (the
    INIT head and LA) at the *east* end and the chain writes (LB, LC) at the
    *west* end, so `solve_ports` can put ``ring_out`` on the east wall — facing
    TURN, which the floor plan forces to sit east — and ``chain_out`` on the
    west wall, facing the chain channel.  Laid the other way round the two
    pipes both have to leave eastward and cannot be nested; that is what
    blocked P >= 2.

    The man spawns facing east by definition, so row 0 is two cells (`@v`) that
    turn him south then west onto the INIT row.

        row 0   .. @ v          spawn, turn down
        row 1   [LB] <tail> [LA] b M s r <     INIT, running west
        row 5   transit back to the east edge
        row 6   [LC] .... b <F> s r <          MAIN, running west
        row 10  return leg
    """
    tail = ("1+M" + _times_const(f) + "b") if f else ""
    lt = len(tail)
    cf = const_free(f) if f else ""
    W_ = max(10, lt + 12)
    H_, MAIN_Y = 11, 6
    la_x = W_ - 6                      # LA entry; its body column is la_x-1
    init_end = W_ - 8                  # first free cell west of LA
    lb_x = init_end - lt               # LB entry; body column lb_x-1
    loop_col = lb_x - 1

    def lay(c: Circuit) -> None:
        c.set(W_ - 2, 0, "@")
        c.set(W_ - 1, 0, "v")
        c.set(W_ - 1, 1, "<")
        c.run(W_ - 2, 1, "rsMb", d=W)
        ex, _ = counted_loop_west(c, la_x, 1, "rs")
        if f:
            ex, _ = c.run(ex, 1, tail, d=W)
            ex, _ = counted_loop_west(c, lb_x, 1, "rs")
        c.route((ex, 1), W, [(0, 1), (0, 5), (W_ - 1, 5)], (W_ - 1, MAIN_Y), W)
        ex, _ = c.run(W_ - 2, MAIN_Y, "rs" + (cf + "b" if f else ""), d=W)
        if f:
            ex, _ = counted_loop_west(c, lb_x, MAIN_Y, "rs")
        c.route((ex, MAIN_Y), W, [(0, MAIN_Y), (0, H_ - 1), (W_ - 1, H_ - 1)],
                (W_ - 1, MAIN_Y), W)

    specs = [
        PortSpec("chain_in", "in", ("N",)),
        PortSpec("chain_out", "out", ("W",)), PortSpec("ring_out", "out", ("E",)),
        PortSpec("mul_out", "out", ("S",)),
    ]
    ops = [
        ((W_ - 3, 1), "s", "ring_out"),
        ((la_x - 1, 3), "s", "ring_out"),
        ((W_ - 3, MAIN_Y), "s", "mul_out"),
    ]
    if f:
        ops += [((loop_col, 3), "s", "chain_out"),
                ((loop_col, MAIN_Y + 2), "s", "chain_out")]
    else:
        specs = [s for s in specs if s.name != "chain_out"]
    return build_room(name, W_, H_, lay, specs, ops)


def lit_free(n: int) -> str:
    """Glyphs leaving A = n for any |n| <= 99, using B as scratch and **no
    backticks** (a backtick pairs by column as well as by row, so one in a
    generated room can pair with one in another room and swallow a wall)."""
    if n < 0:
        return lit_free(-n) + "N"
    if n <= 9:
        return str(n)
    if n <= 18:
        return f"9M{n - 9}+"          # A=9, B=9, A=n-9, A=(n-9)+9
    for a in range(9, 1, -1):
        for b in range(18, 1, -1):
            c = n - a * b
            if 0 <= c <= 9:
                # A=b, B=b, A=a, A=a*b, B=a*b, A=c, A=c+a*b
                return lit_free(b) + f"M{a}*" + (f"M{c}+" if c else "")
    raise ValueError(n)


def src_room(name: str, vals: list[int], width: int = 44) -> Room:
    """Test-only stand-in for the front end: emit `vals` in order, then halt.

    Snaked across rows, because a 16-stage probe stream is several hundred
    values and a single row would be wider than the machine.
    """
    body = "".join(lit_free(v) + "s" for v in vals) + "H"
    span = width - 2
    chunks = [body[i:i + span] for i in range(0, len(body), span)] or [""]
    h = len(chunks)

    def lay(c: Circuit) -> None:
        for y, chunk in enumerate(chunks):
            east = y % 2 == 0
            if y == 0:
                c.set(0, 0, "@")
                c.run(1, 0, chunk)
            elif east:
                c.set(0, y, ">")
                c.run(1, y, chunk)
            else:
                c.set(width - 1, y, "<")
                c.run(width - 2, y, chunk, d=W)
            if y + 1 < h:
                c.set(width - 1 if east else 0, y, "v")

    return build_room(name, width, h, lay, [PortSpec("out", "out", ("S",))], [])


def io_room(name: str, glyph: str, kind: str) -> Room:
    def lay(c: Circuit) -> None:
        c.set(0, 0, glyph)

    return build_room(name, 1, 1, lay, [PortSpec("p", kind)], [])


# ── the reference stream ─────────────────────────────────────────────────────


def stream_for(a: list[list[int]], b: list[list[int]], p: int) -> list[int]:
    """The exact sequence the chain's head must receive, for a P-stage chain."""
    n, m = len(a), len(a[0])
    k = len(b[0])
    out: list[int] = []
    for t in range(p):
        row = b[t] if t < m else [0] * k
        out += [k] + list(row)
    for i in range(n):
        out += [a[i][t] if t < m else 0 for t in range(p)]
    return out


def expected(a, b) -> list[int]:
    n, m, k = len(a), len(a[0]), len(b[0])
    return [sum(a[i][t] * b[t][j] for t in range(m)) for i in range(n) for j in range(k)]


# ── grid assembly ────────────────────────────────────────────────────────────


class Grid:
    def __init__(self, w: int, h: int) -> None:
        self.c = Circuit(w, h)
        self.rooms: list[Room] = []

    def place(self, room: Room, x: int, y: int) -> Room:
        room.x, room.y = x, y
        for dy, r in enumerate(room.rows()):
            for dx, ch in enumerate(r):
                if ch != " ":
                    self.c.set(x + dx, y + dy, ch)
        self.rooms.append(room)
        return room

    def pipe(self, pts: list[tuple[int, int]]) -> None:
        from randomfun2026solvers.memory_tape import _draw_pipe
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 != x1 and y0 != y1:
                raise ValueError(f"non-rectilinear pipe leg {(x0,y0)}->{(x1,y1)}")
        _draw_pipe(self.c, pts)

    def link(self, src: Room, sp: str, dst: Room, dp: str,
             waypoints: list[tuple[int, int]] | None = None) -> None:
        pts = [src.mouth(sp)] + (waypoints or []) + [dst.mouth(dp)]
        self.pipe(pts)

    def boxes(self):
        return [(r.x, r.y, r.bw, r.bh) for r in self.rooms]

    def text(self) -> str:
        rows = self.c.rows()
        while rows and not rows[-1].strip():
            rows.pop()
        return "\n".join(r.rstrip() for r in rows)


def check_mouths(grid: Grid, expect: int) -> None:
    from randomfun2026solvers.brackets_men import pipe_mouths, wall_cells
    rows = grid.text().split("\n")
    found = pipe_mouths(rows, wall_cells(grid.boxes()))
    if len(found) != expect:
        listed = ", ".join(f"{ch!r}@{c}" for c, ch in sorted(found))
        raise Collision(f"{len(found)} pipe mouths, wanted {expect}: {listed}")


# ── the probe: a P-stage chain fed by a hard-coded source room ───────────────


GAP = 16                 # rows between LOADF and MUL: the ring serpentine lives here


def build_chain(p: int, stream: list[int] | None) -> tuple[Grid, int]:
    """P stages stacked vertically.  Returns (grid, expected pipe count).

    THE FLOOR PLAN, AND WHY IT IS THE ONLY ONE

    MUL's north wall carries three pipes in a *forced* column order —
    ``a_in`` < ``ring_out`` < ``ring_in`` — because MUL's first `r` (the weight)
    sits west of its second (the ring).  Everything else follows:

      * TURN sits **east**, so its two pipes nest outside the weight pipe, which
        drops straight down from LOADF.
      * the three gap pipes turn on strictly ordered rows, ``r_a`` above
        ``r_ring`` above ``r_ser``, so each crosses the others' columns only
        above where they are live.
      * LOADF is laid right-to-left, so ``ring_out`` is on the east wall facing
        TURN and ``chain_out`` on the west wall facing the **west channel**.
      * ADD reads the product before the psum, which puts ``prod_in`` west (fed
        straight down from MUL) and ``psum_in`` east — so the psum runs down an
        **east channel** and never meets the chain.

    Two north-south channels on the same side cannot both work: each one's
    horizontals cross the other's riser at every band, and the row overlap is
    unavoidable because one spans the top of a band and the other the bottom.
    Putting them on opposite sides is the whole trick.
    """
    loadf = [loadf_room(f"LOADF{i}", p - 1 - i) for i in range(p)]
    muls = [mul_room(f"MUL{i}") for i in range(p)]
    turns = [turn_room(f"TURN{i}") for i in range(p)]
    adds = [add_room(f"ADD{i}", i == 0) for i in range(p)]

    lw = max(r.bw for r in loadf)
    X0, SY = 22, 10 + (0 if stream is None else src_room('probe', stream).bh + 8)
    BANDH = 13 + GAP + 11 + 6 + 4 + 12
    chw = X0 - 4                      # west channel: the a/b chain
    pse = X0 + lw + 23                # east channel: the psum
    g = Grid(max(pse, X0 + 50) + 8, SY + BANDH * p + 24)

    for i in range(p):
        by = SY + BANDH * i
        g.place(loadf[i], X0, by)
        g.place(turns[i], X0 + lw + 10, by + 4)
        g.place(muls[i], X0, by + 13 + GAP)
        g.place(adds[i], X0, by + 13 + GAP + 11 + 6)

    npipe = 0
    for i in range(p):
        by = SY + BANDH * i
        muly = by + 13 + GAP
        addy = muly + 11 + 6
        L, T, M_, A = loadf[i], turns[i], muls[i], adds[i]
        r_a, r_ring, r_ser = muly - 12, muly - 10, muly - 8
        east2 = X0 + lw + 6
        east3 = X0 + lw + 20
        mx0, my0 = L.mouth("mul_out")
        ax, ay = M_.mouth("a_in")
        g.pipe([(mx0, my0), (mx0, r_a), (ax, r_a), (ax, ay)])
        rx, ry = M_.mouth("ring_out")
        bx, byy = T.mouth("back")
        g.pipe([(rx, ry), (rx, r_ring), (east2, r_ring), (east2, byy), (bx, byy)])
        fx, fy = L.mouth("ring_out")
        tx, ty = T.mouth("fill")
        g.pipe([(fx, fy), (tx, fy), (tx, ty)])
        ox, oy = T.mouth("out")
        ix, iy = M_.mouth("ring_in")
        g.pipe([(ox, oy), (ox, r_ser), (east3, r_ser), (east3, r_ser + 2),
                (ix + 1, r_ser + 2), (ix + 1, r_ser + 4), (east3, r_ser + 4),
                (east3, r_ser + 6), (ix, r_ser + 6), (ix, iy)])
        px, py = M_.mouth("prod_out")
        qx, qy = A.mouth("prod_in")
        g.pipe([(px, py), (px, py + 2), (qx, py + 2), (qx, qy)])
        npipe += 5
        if i + 1 < p:
            cx, cy = L.mouth("chain_out")
            nx, ny = loadf[i + 1].mouth("chain_in")
            g.pipe([(cx, cy), (chw, cy), (chw, ny - 4), (nx, ny - 4), (nx, ny)])
            sx0, sy0 = A.mouth("psum_out")
            dx0, dy0 = adds[i + 1].mouth("psum_in")
            g.pipe([(sx0, sy0), (sx0, sy0 + 2), (pse, sy0 + 2),
                    (pse, dy0 - 2), (dx0, dy0 - 2), (dx0, dy0)])
            npipe += 2

    hx, hy = loadf[0].mouth("chain_in")
    if stream is None:
        raise NotImplementedError("front end not built; see the module docstring")
    src = src_room("SRC", stream)
    g.place(src, X0, SY - src.bh - 6)
    sx, sy = src.mouth("out")
    g.pipe([(sx, sy), (sx, hy - 3), (hx, hy - 3), (hx, hy)])
    npipe += 1
    ox, oy = adds[-1].mouth("psum_out")
    o = io_room("O", "O", "in")
    g.place(o, ox - 1, oy + 3)
    g.pipe([(ox, oy), (ox, oy + 2)])
    npipe += 1
    return g, npipe


def probe(p: int, a, b) -> tuple[str, list[int]]:
    g, npipe = build_chain(p, stream_for(a, b, p))
    check_mouths(g, npipe)
    return g.text(), expected(a, b)


if __name__ == "__main__":
    A = [[1, 2], [3, 4]]
    B = [[5, 6], [7, 8]]
    text, exp = probe(2, A, B)
    print(text)
    print("# expect:", exp, file=sys.stderr)
