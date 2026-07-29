"""Five routing problems whose right answer is known **by construction**.

The user's requirement, verbatim: *"I would create simple tests with routing,
when we know the outputs."*  So each rung states its answer in a docstring, in
cells, derived on paper before the solver is run — and :func:`run` prints the
known answer beside what the solver found.

The ladder is ordered so that each rung adds exactly one of the four properties
in ``LAYOUT-MANAGER.md``:

    1  adjacency          — can it place and route at all
    2  min_length         — is the length floor a constraint, not a pad
    3  length x frequency — property 4
    4  a room beats a pipe— property 2, with the crossover computed by hand
    5  a binding trap     — property 1, the one that makes this not packing
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import FORWARDER_CELLS, Block, Pipe, Port, Problem, Solution


@dataclass
class Rung:
    name: str
    problem: Problem
    #: What the answer is, worked out on paper.
    known: str
    #: ``(solution, report) -> (ok, what we actually saw)``
    check: object


# ── 1. two blocks, one pipe, free offsets ────────────────────────────────────
def rung1() -> Rung:
    """Two blocks, one pipe, free offsets.  Optimum is the minimum legal gap.

    A is pinned with its east wall on column 6, so its touch cell is column 7.
    B's west wall may stand on columns 8..16, putting its touch cell on column
    ``q-1``, and the pipe spans columns 7..q-1 inclusive: **length = q - 7**.

    ``SPEC.md`` § Pipes: minimum length 2.  ``q = 8`` puts both touch cells on
    column 7 — a one-cell pipe, which is a load error — so the floor is ``q = 9``
    and the answer is **2 cells**, with both free offsets on the same row.
    """
    a = Block(
        "A", 5, 5,
        ports=(Port("out", "E", None, "s", "req"),),
        xs=(2,), ys=(2,),
    )
    b = Block(
        "B", 5, 5,
        ports=(Port("in", "W", None, "r", "req"),),
        xs=tuple(range(8, 17)), ys=(2,),
    )
    prob = Problem((a, b), (Pipe("req", ("A", "out"), ("B", "in"), "req", weight=1.0),),
                   bounds=(30, 12), name="rung1")

    def check(sol: Solution, rep):
        got = sol.routes["req"].drawn
        return got == 2, f"pipe {got} cells, B at x={sol.placement['B'][0]}"

    return Rung("1 adjacency", prob, "pipe = 2 cells (the SPEC minimum)", check)


# ── 2. the same, with a min_length ───────────────────────────────────────────
def rung2(min_length: int = 6) -> Rung:
    """The same pair, with ``min_length = 6``.

    ``ARCH.md`` §7.4: the code ring deadlocks when its pipe is shorter than the
    program, so a router that only shortens is wrong.  The answer is **exactly 6
    cells** — a solver that ignores the constraint gives 2, and one that pads
    lazily gives more.
    """
    a = Block("A", 5, 5, ports=(Port("out", "E", None, "s", "req"),), xs=(2,), ys=(2,))
    b = Block("B", 5, 5, ports=(Port("in", "W", None, "r", "req"),),
              xs=tuple(range(8, 21)), ys=(2,))
    prob = Problem(
        (a, b),
        (Pipe("req", ("A", "out"), ("B", "in"), "req", weight=1.0,
              min_length=min_length, allow_room=False),),
        bounds=(34, 12), name="rung2",
    )

    def check(sol: Solution, rep):
        got = sol.routes["req"].drawn
        return got == min_length, f"pipe {got} cells"

    return Rung("2 min_length", prob, f"pipe = exactly {min_length} cells", check)


# ── 3. three in a chain, weights 100 / 1 ─────────────────────────────────────
def rung3() -> Rung:
    """A chain A->B->C with weights 100 and 1.  The heavy leg must be short.

    A's east touch is column 7; C's west touch is column 58.  B is 6 wide and may
    stand anywhere between.  With B's west wall on ``q``:

        heavy = q - 7          light = 58 - (q + 5) = 53 - q
        heavy + light = 46, **for every q**

    So *total pipe length is constant* and a length-minimising solver cannot tell
    these apart at all — it will return whichever it happened to see first.  The
    weighted objective is ``100*heavy + light`` and is minimised uniquely at the
    floor, ``heavy = 2``.

    Known answer: **heavy = 2, light = 44**, and the *length* objective's minimum
    of 46 cells is attained by many different placements — so it cannot choose,
    while the weighted objective has a unique answer.  This is ``cpu->drum``
    (437 cells, 0.019%) against ``adapter->store`` (60 cells, 5.92%) in miniature.

    (A's port offset is free, so a candidate that puts it off B's row pays one
    extra bend cell.  Those score 47 and are not part of the tie; the tie is the
    46-cell family, one member per position of B.)
    """
    a = Block("A", 5, 5, ports=(Port("out", "E", None, "s", "heavy"),), xs=(2,), ys=(2,))
    b = Block(
        "B", 6, 5,
        ports=(Port("in", "W", 2, "r", "heavy"), Port("out", "E", 2, "s", "light")),
        xs=tuple(range(9, 52)), ys=(2,),
    )
    c = Block("C", 5, 5, ports=(Port("in", "W", 2, "r", "light"),), xs=(59,), ys=(2,))
    prob = Problem(
        (a, b, c),
        (
            Pipe("heavy", ("A", "out"), ("B", "in"), "heavy", weight=100.0, allow_room=False),
            Pipe("light", ("B", "out"), ("C", "in"), "light", weight=1.0, allow_room=False),
        ),
        bounds=(70, 12), name="rung3",
    )

    def check(sol: Solution, rep):
        heavy = sol.routes["heavy"].drawn
        light = sol.routes["light"].drawn
        shortest = min(n for _c, n in rep.samples)
        ties = sum(1 for _c, n in rep.samples if n == shortest)
        cheapest = min(c for c, _n in rep.samples)
        wties = sum(1 for c, _n in rep.samples if c == cheapest)
        ok = heavy == 2 and light == 44 and shortest == 46 and ties > 1 and wties == 1
        return ok, (
            f"heavy {heavy}, light {light}; the length objective's best is "
            f"{shortest} cells and {ties} placements tie on it, while the weighted "
            f"objective's best is unique ({wties} candidate at {cheapest:g})"
        )

    return Rung(
        "3 length x frequency", prob,
        "heavy = 2, light = 44; length ties across many placements, weight does not",
        check,
    )


# ── 4. the forwarder crossover ───────────────────────────────────────────────
def _gap_problem(gap: int, name: str) -> Problem:
    """A pinned pair with exactly ``gap`` free columns between their touch cells."""
    a = Block("A", 5, 7, ports=(Port("out", "E", 3, "s", "req"),), xs=(2,), ys=(4,))
    bx = 6 + gap + 1  # A's east wall is column 6; the pipe spans 7 .. 6+gap
    b = Block("B", 5, 7, ports=(Port("in", "W", 3, "r", "req"),), xs=(bx,), ys=(4,))
    return Problem(
        (a, b),
        (Pipe("req", ("A", "out"), ("B", "in"), "req", weight=1.0),),
        bounds=(bx + 10, 20), name=name,
    )


def rung4(gap: int, expect_room: bool) -> Rung:
    """A leg long enough that a forwarder beats it.  The crossover, by hand.

    A room is crossed for :data:`model.FORWARDER_CELLS` = 5.2 cells **whatever its
    size** — ``R`` receives with no distance term — so a room that spans the whole
    corridor costs only its two stubs plus that floor.  The stubs' floor is
    2 cells each (``ARCH.md`` §7.4b), so:

        room:  2 + 2 + 5.2 = **9.2 charged cells**
        pipe:  the gap, in cells

    The crossover is therefore at a gap of **9.2 cells**: at 9 the pipe wins
    (9 < 9.2) and at 10 the room wins (9.2 < 10).  Both are checked.

    This is the store's request teleport in miniature — there the corridor was 58
    cells and the room turned it into six.
    """
    prob = _gap_problem(gap, f"rung4-gap{gap}")

    def check(sol: Solution, rep):
        r = sol.routes["req"]
        got_room = r.room is not None
        return got_room == expect_room, (
            f"gap {gap}: charged {r.cells:.1f} cells, "
            f"{'room ' + str(r.room) if got_room else 'plain pipe'}"
        )

    crossover = 2 + 2 + FORWARDER_CELLS
    return Rung(
        f"4 forwarder crossover (gap {gap})", prob,
        f"crossover at {crossover} cells -> gap {gap} takes "
        f"{'a room' if expect_room else 'the pipe'}",
        check,
    )


# ── 5. the binding trap ──────────────────────────────────────────────────────
def rung5() -> Rung:
    """Two placements identical in cost; one silently rebinds a third block's ``r``.

    This is M11c on a napkin — ``SEEK_MEM_PAD`` sat four columns above its floor
    because *the input room* was one of the rivals every memory ``r`` is weighed
    against.

    The CPU is 12x14 at (10,10), so its east wall is column 21.  Its memory ``r``
    sits eight columns inside that wall — a ``mem_pad`` — at **(13, 13)**, and the
    response pipe's touch cell is at (22, 13):

        d(memory r -> its own response pipe) = 9, always.

    The input room stands two rows above the CPU and drops a 2-cell pipe onto the
    north wall at column ``c``.  That pipe's touch cell is (c, 9), so

        d(memory r -> the input pipe) = |c - 13| + 4.

    The input pipe is 2 cells for **every** ``c`` the room can reach, so the two
    candidate columns below are *identical in cost, in area and in every geometric
    check*.  Only §7.1's arithmetic separates them:

        c = 17  ->  |17-13| + 4 = 8  < 9   the memory ``r`` reads the input pipe
        c = 19  ->  |19-13| + 4 = 10 > 9   binds correctly

    Known answer: the solver takes ``I`` at x=18 (column 19) and **rejects** x=16,
    and the rejected candidate must price identically.  A place-then-route design
    takes the cheaper-looking one and the machine reads its memory responses out
    of the keyboard, silently.
    """
    cpu = Block(
        "CPU", 12, 14,
        ports=(
            Port("memresp", "E", 3, "r", "resp", depth=8),
            Port("memreq", "E", 9, "s", "req", depth=8),
            Port("input", "N", None, "r", "in", depth=1),
        ),
        xs=(10,), ys=(10,),
    )
    mem = Block(
        "MEM", 6, 14,
        ports=(Port("req", "W", 9), Port("resp", "W", 3)),
        xs=(26,), ys=(10,),
    )
    io = Block("I", 3, 3, ports=(Port("out", "S", 1),), xs=(16, 18), ys=(5,))
    prob = Problem(
        (cpu, mem, io),
        (
            Pipe("req", ("CPU", "memreq"), ("MEM", "req"), "req", weight=1.0, allow_room=False),
            Pipe("resp", ("MEM", "resp"), ("CPU", "memresp"), "resp", weight=1.0,
                 allow_room=False),
            Pipe("in", ("I", "out"), ("CPU", "input"), "in", weight=1.0, allow_room=False),
        ),
        bounds=(40, 30), name="rung5",
    )

    def check(sol: Solution, rep):
        from .trap import price_trap

        x = sol.placement["I"][0]
        trap_cost, trap_err = price_trap(prob)
        ok = (
            x == 18
            and rep.rejected_by_bindings >= 1
            and trap_err is not None
            and abs(trap_cost - sol.weighted_cells) < 1e-9
        )
        return ok, (
            f"I at x={x} (column 19); the x=16 twin prices identically at "
            f"{trap_cost:g} weighted cells and is rejected: {trap_err}"
        )

    return Rung(
        "5 binding trap", prob,
        "I at x=18; the identically-priced x=16 twin is rejected on bindings",
        check,
    )


def ladder() -> list[Rung]:
    return [rung1(), rung2(), rung3(), rung4(9, False), rung4(10, True), rung5()]
