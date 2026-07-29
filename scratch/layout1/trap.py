"""Price the *rejected* twin, to prove the rejection was not a cost preference.

Rung 5's whole claim is that two candidates are indistinguishable to a packer and
distinguishable only to ``check_bindings``.  Asserting that the solver picked one
is not enough — the other has to be shown to cost the same.  So this builds the
trap candidate by hand, routes it with the same router, prices it, and then
reports the ``MachineError`` the production checker raises on it.
"""

from __future__ import annotations

from .bind import MachineError, check_layout
from .geom import Layout, Placed
from .model import Leg, Problem, Route, Solution
from .route import Field, NoRoute, route


def price_trap(problem: Problem) -> tuple[float, str | None]:
    """Route rung 5 with ``I`` at x=16 and column 17 on the CPU's north wall.

    Returns ``(weighted cells, the binding error)``.  A ``None`` error would mean
    the trap is not a trap and the rung proves nothing.
    """
    placed = {
        "CPU": Placed(problem.block("CPU"), 10, 10, (0, 0, 0, 0)),
        "MEM": Placed(problem.block("MEM"), 26, 10, (0, 0, 0, 0)),
        "I": Placed(problem.block("I"), 16, 5, (0, 0, 0, 0)),
    }
    offsets = {
        ("CPU", "memresp"): 3,
        ("CPU", "memreq"): 9,
        ("CPU", "input"): 7,  # column 10 + 7 = 17, straight under I at x=16
        ("MEM", "req"): 9,
        ("MEM", "resp"): 3,
        ("I", "out"): 1,
    }
    layout = Layout(problem, placed, offsets)
    rects = [pl.rect for pl in placed.values()]
    taken: set[tuple[int, int]] = set()
    routes: dict[str, Route] = {}
    for pipe in sorted(problem.pipes, key=lambda p: -p.weight):
        fld = Field(problem.bounds, rects, taken)
        s, sd = layout.touch(pipe.src), layout.heading(pipe.src)
        g = layout.touch(pipe.dst)
        gd = tuple(-c for c in layout.heading(pipe.dst))
        try:
            cells = route(fld, s, sd, g, gd, min_length=pipe.min_length)
        except NoRoute:
            return float("inf"), "unroutable"
        routes[pipe.name] = Route(pipe.name, (Leg(cells),))
        taken |= set(cells)
    sol = Solution(
        placement={n: (p.px, p.py) for n, p in placed.items()},
        growth={},
        offsets=offsets,
        routes=routes,
        problem=problem,
    )
    try:
        check_layout(layout)
    except MachineError as exc:
        return sol.weighted_cells, str(exc)
    return sol.weighted_cells, None
