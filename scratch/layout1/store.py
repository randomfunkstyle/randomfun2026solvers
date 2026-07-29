"""The falsifiable test: DOOM's store **request legs**, where the answers are measured.

Three moves were placed by hand this session, on the taped ``deadman-3d`` store,
and priced on the real tour:

    answer path, three rooms to one   STORE_ANSWER_WEST                  -0.52%
    adapter->store teleported         STORE_REQUEST_TELEPORT             -5.92%
    gate rooms reach + arm forwarders STORE_REQUEST_REACH / CHAIN / FEED -7.48%

The question is not whether a solver can be made to agree.  It is whether, told
only the blocks, the free space, the traffic weights and the cost rate, it
**picks the same primitive on each leg** — and, on the one leg where the hand
build deliberately did *not* reach, whether it refuses for the right reason.

What is given to the solver, and what is not
--------------------------------------------
Given, all measured (``scratch/deadman3d-opt/METRICS.md`` M12/M13/M13b):

* the plain leg lengths — 58 cells ``adapter->gate0``, 25 a chain link, 43/43/42/95
  drawn on the four ``reqK->bankK`` arms;
* the traffic weights — chain links 0.6574 and 0.0955 of accesses; feed arms
  0.3426 / 0.5619 / 0.0645 / 0.0310, from ``TAPED_BANKS`` (352,164,15,69) under
  ``TAPED_BANK_ORDER`` (3,2,0,1);
* the free space each leg runs through, including the feed risers that stop a
  chain reach landing flush;
* the taped gate's **actual body** — its east wall on local column 25, its local
  pipe on row 1 and its downstream pipe on row 6, and all ten of its ``s``
  glyphs at their real cells.

Not given: which primitive to use anywhere, or that a reach is ever illegal.

The corridors are sized so each *plain* leg matches its measured length, because
that length is a fact about the machine and not something a corridor cartoon
should be allowed to invent.  Everything downstream of it — which primitive wins,
by how much, and what the tick delta is — is the solver's own.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    FORWARDER_CELLS,
    TICKS_PER_WEIGHTED_CELL,
    Block,
    Pipe,
    Port,
    Problem,
)
from .solve import Report, solve

# ── the taped gate, from memory_taped.py ─────────────────────────────────────
#: The compact gate body: 26 wide (east wall on local column 25) and 7 tall.
GATE_W, GATE_H = 26, 7
#: ``COMPACT_GATE_LOCAL_ROW`` / ``COMPACT_GATE_DOWN_ROW`` — the two outgoing pipes
#: share the east wall, which is the whole reason the arms cannot reach.
LOCAL_ROW, DOWN_ROW = 1, 6
#: The north write arm's ``s`` glyphs (rows 1-2) and the south path's (rows 5-6).
#: Ten glyphs, and it is the *tightest* that decides whether the block may move —
#: (19, 2), 8 from the local pipe and 11 from the downstream one, margin 3.
LOCAL_GLYPHS = ((15, 1), (17, 1), (15, 2), (17, 2), (19, 2))
DOWN_GLYPHS = ((16, 5), (18, 5), (20, 5), (16, 6), (18, 6))

#: Accesses-weighted traffic, from ``machine.py``'s read/write census.
LINK_W = (0.6574, 0.0955)
FEED_W = (0.3426, 0.5619, 0.0645, 0.0310)

#: What the machine actually shipped, per leg (drawn cells).
MEASURED = {
    "adapter->gate0": ("reach", 2, 58),
    "gate0->gate1": ("reach", 7, 25),
    "gate1->gate2": ("reach", 7, 25),
    "req0->bank0": ("room", 10, 43),
    "req1->bank1": ("room", 10, 43),
    "req2->bank2": ("room", 9, 42),
    "req3->bank3": ("room", 64, 95),
}


@dataclass
class Case:
    name: str
    problem: Problem
    #: leg -> (primitive the machine shipped, its drawn cells, the plain baseline)
    measured: dict[str, tuple[str, int, int]]
    note: str = ""


# ── S1: adapter -> gate 0, the leg every access pays ─────────────────────────
def s1_request_head(gy: int = 49) -> Problem:
    """``adapter->store``: 58 plain cells, on 100% of accesses.

    The adapter sits above the store block's west end.  Its request may leave the
    **east** wall (the shipped route, which walks the corridor round the banks) or
    the **south** one; a room may hang in the corridor between its floor and the
    gates' roof, which is empty for its whole height; and gate 0's roof may be
    grown north, because a gate is a room and ``U`` has no distance term.

    Three answers, and the solver is told none of them:

        plain pipe              58 cells
        room in the corridor    2 + 4 stubs + 5.2 floor  = 11.2
        the roof reaches        2 cells
    """
    # The corridor is uniform in x, so one representative drop column stands for
    # all of them: every column between the adapter's floor and the gates' roof is
    # free for its whole height, which is the fact the teleport exploited.
    adapter = Block(
        "adapter", 14, 6,
        ports=(Port("out_e", "E", 2), Port("out_s", "S", 2)),
        xs=(10,), ys=(10,),
    )
    gate0 = Block(
        "gate0", GATE_W, GATE_H,
        ports=(Port("req_w", "W", 3), Port("req_n", "N", 2)),
        xs=(10,), ys=(gy,),
        grow=frozenset({"N"}), grow_max=gy - 18,
    )
    banks = Block("banks", 50, gy - 16, xs=(40,), ys=(17,))
    floor = Block("floor", 80, 8, xs=(10,), ys=(gy + GATE_H + 1,))
    return Problem(
        (adapter, gate0, banks, floor),
        (Pipe("adapter->gate0", ("adapter", "out_e"), ("gate0", "req_w"),
              "req", weight=1.0),),
        bounds=(100, gy + 24), name="s1",
    )


def s1_south() -> Problem:
    """The same, with the request leaving the adapter's **south** wall.

    ``ARCH.md`` §7.1's escape hatch — ``Container.variants`` — is offering the
    solver several equivalent block layouts and letting it pick one that routes.
    Which wall a port sits on is exactly such a variant, so it is a second problem
    rather than a free choice inside one.
    """
    p = s1_request_head()
    return Problem(
        p.blocks,
        (Pipe("adapter->gate0", ("adapter", "out_s"), ("gate0", "req_n"),
              "req", weight=1.0),),
        bounds=p.bounds, name="s1-south",
    )


# ── S2: the gate chain, where a reach is legal ───────────────────────────────
def s2_chain(pitch: int = 48) -> Problem:
    """``gate0->gate1`` and ``gate1->gate2``: 25 plain cells each, at 66% and 10%.

    A gate may grow **west** until its wall stands beside the previous gate's.
    It cannot land flush: the previous bank's feed riser owns a column in between,
    so the hop is over it.  The riser is modelled as the obstacle it is.
    """
    gates = []
    risers = []
    for k in range(3):
        x = 10 + k * pitch
        # The request entry is a *room* port: `U` receives from any incoming pipe
        # with no distance term, so which row of the west wall it takes is free.
        ports = [Port("req_w", "W", None)]
        if k < 2:
            ports.append(Port("down", "E", DOWN_ROW, "s", f"link{k}", cells=DOWN_GLYPHS))
        gates.append(
            Block(
                f"gate{k}", GATE_W, GATE_H,
                ports=tuple(ports),
                xs=(x,), ys=(40,),
                grow=frozenset({"W"}) if k else frozenset(),
                grow_max=pitch - GATE_W if k else 0,
            )
        )
        if k < 2:
            # The previous bank's feed riser.  It stops the gate *body* passing —
            # which is what puts a floor under the reach — while leaving the link's
            # own row clear, so the link hops it rather than going round.
            risers.append(Block(f"riser{k}", 4, 28, xs=(x + GATE_W + 3,), ys=(14,)))
    return Problem(
        (*gates, *risers),
        (
            Pipe("gate0->gate1", ("gate0", "down"), ("gate1", "req_w"),
                 "link0", weight=LINK_W[0]),
            Pipe("gate1->gate2", ("gate1", "down"), ("gate2", "req_w"),
                 "link1", weight=LINK_W[1]),
        ),
        bounds=(10 + 3 * pitch + 20, 80), name="s2",
    )


# ── S3: the feed arms, where a reach is *illegal* and only binding says so ────
def s3_feed(k: int, plain: int, weight: float) -> Problem:
    """``reqK->bankK``: the leg that runs to the gate's **callee**.

    This is the sharp one.  The gate has two outgoing pipes on one east wall, so
    its ``s`` glyphs bind by distance; the local feed's attachment may in principle
    climb the roof the gate grew, and the arm wants it to climb **31 rows**.  The
    solver is offered every climb from 0 to 31 and is told nothing about which are
    legal.  ``check_bindings`` is the only thing standing between it and a machine
    whose reads come back from the wrong bank with no error at all.

    A bank is a rotating pipe tape, not a room, so it cannot grow to meet the gate
    either.  That leaves a plain pipe or a forwarder.
    """
    # The gate sits this far below the bank, chosen so the *plain* arm is exactly
    # the length it is on the real block.  That length is a measured fact about the
    # machine; a corridor cartoon does not get to invent it.
    lift = plain - 2
    gate = Block(
        f"gate{k}", GATE_W, GATE_H,
        ports=(
            Port("local", "E", LOCAL_ROW, "s", f"feed{k}", cells=LOCAL_GLYPHS,
                 choices=tuple(range(LOCAL_ROW, -32, -1))),
            Port("down", "E", DOWN_ROW, "s", f"down{k}", cells=DOWN_GLYPHS),
            Port("req_w", "W", 3),
        ),
        xs=(10,), ys=(10 + lift,),
        grow=frozenset({"N"}), grow_max=31,
    )
    # The bank ring the arm climbs to, and the downstream gate the second pipe
    # feeds.  Neither is a room: a tape cannot grow a wall toward its caller.
    bank = Block("bank", 20, 6, ports=(Port("in", "S", 10),), xs=(24,), ys=(4,))
    downstream = Block("next", 8, 6, ports=(Port("in", "W", 3),), xs=(60,), ys=(10 + lift,))
    # The corridor the arm has to climb is six columns wide -- which is exactly
    # what TAPED_FEED_TELEPORT had to widen the bank pitch by two columns to get.
    wall = Block("wall", 14, plain + 10, xs=(40,), ys=(10,))
    return Problem(
        (gate, bank, downstream, wall),
        (
            Pipe(f"req{k}->bank{k}", (f"gate{k}", "local"), ("bank", "in"),
                 f"feed{k}", weight=weight),
            Pipe(f"down{k}", (f"gate{k}", "down"), ("next", "in"),
                 f"down{k}", weight=0.0, allow_room=False),
        ),
        bounds=(90, 20 + plain + 20), name=f"s3-{k}",
    )


# ── S4: the answer path, which the cost model gets *wrong* ───────────────────
#: ``STORE_ANSWER_WEST``'s four builds, measured on the checked-in 115-frame tour:
#: ``(label, forwarders, drawn cells, tour ticks)``.  Commit ``12ac19c``.
ANSWER_BUILDS = (
    ("three rooms / 10 cells", 3, 10, 1_113_752_187),
    ("two rooms   / 10 cells", 2, 10, 1_112_107_549),
    ("one room    /  7 cells", 1, 7, 1_107_995_954),
    ("zero rooms  / 57 cells", 0, 57, 1_159_488_639),
)


def answer_path_probe() -> tuple[bool, list[str]]:
    """Does the cost model rank ``STORE_ANSWER_WEST``'s four builds correctly?

    This one needs no corridor at all: all four builds are measured, so the model
    can be asked the only question that matters — does ``cells + 5.2 per
    forwarder`` put them in the order the tour did?

    It also backs the forwarder floor out of each adjacent pair, which is where
    the interesting part is.
    """
    scored = [
        (label, n, cells, ticks, cells + n * FORWARDER_CELLS)
        for label, n, cells, ticks in ANSWER_BUILDS
    ]
    by_model = [s[0] for s in sorted(scored, key=lambda s: s[4])]
    by_tour = [s[0] for s in sorted(scored, key=lambda s: s[3])]
    lines = [f"    {'build':<24} {'charged':>8} {'ticks':>16}"]
    for label, _n, _c, ticks, charged in scored:
        lines.append(f"    {label:<24} {charged:>8.1f} {ticks:>16,}")
    lines.append(f"    model order: {' < '.join(v.split('/')[0].strip() for v in by_model)}")
    lines.append(f"    tour  order: {' < '.join(v.split('/')[0].strip() for v in by_tour)}")
    # Back the forwarder's cost out of each adjacent pair: dTicks = (dCells +
    # dRooms * F) * RATE.
    lines.append("    forwarder floor implied by each adjacent pair"
                 f" (model assumes {FORWARDER_CELLS}):")
    for (la, na, ca, ta, _x), (lb, nb, cb, tb, _y) in zip(scored, scored[1:], strict=False):
        d_rooms, d_cells, d_ticks = na - nb, ca - cb, ta - tb
        if d_rooms:
            f = (d_ticks / TICKS_PER_WEIGHTED_CELL - d_cells) / d_rooms
            lines.append(
                f"      {la.split('/')[0].strip()} -> {lb.split('/')[0].strip()}: "
                f"{f:>6.2f} cells"
            )
    return by_model == by_tour, lines


# ── running it ───────────────────────────────────────────────────────────────
def _pick(rep: Report, leg: str) -> tuple[str, int, float]:
    r = rep.best.routes[leg]
    grown = any(v for _k, v in rep.best.growth.items() if v)
    kind = "room" if r.room is not None else ("reach" if grown else "pipe")
    return kind, r.drawn, r.cells


def _ungrown(problem: Problem) -> Problem:
    """The same problem with every reach forbidden — the M12 ablation."""
    return Problem(
        tuple(
            Block(b.name, b.w, b.h, b.ports, b.xs, b.ys, frozenset(), 0)
            for b in problem.blocks
        ),
        problem.pipes,
        problem.bounds,
        problem.name + "-noreach",
    )


def _noroom(problem: Problem) -> Problem:
    """...and with every forwarder forbidden too: the plain-pipe baseline."""
    p = _ungrown(problem)
    return Problem(
        p.blocks,
        tuple(
            Pipe(q.name, q.src, q.dst, q.band, q.weight, q.min_length, allow_room=False)
            for q in p.pipes
        ),
        p.bounds,
        problem.name + "-plain",
    )


def _leg(label: str, rep: Report, leg: str, weight: float) -> tuple[str, int, float] | None:
    if rep.best is None:
        print(f"        {label:<14} NO FEASIBLE LAYOUT — {rep.summary()}")
        return None
    kind, drawn, charged = _pick(rep, leg)
    print(f"        {label:<14} {kind:<6} {drawn:>3} drawn, {charged:>5.1f} charged"
          f"  = {charged * weight:>6.2f} weighted cells")
    return kind, drawn, charged


def run_store() -> int:
    """Solve every request leg and print it beside what the machine actually did."""
    bad = 0
    print("\n" + "=" * 78)
    print("THE FALSIFIABLE TEST — DOOM's store request legs")
    print("=" * 78)
    saved: list[tuple[str, float, float]] = []  # leg, plain weighted, solved weighted

    # ── S1: adapter -> gate 0, and the three-rung ablation that produced it ──
    print("\n  adapter->gate0   (weight 1.0000 — every access pays it)")
    print(f"        {'':<14} machine: reach, 2 drawn (STORE_REQUEST_REACH)")
    base = solve(_noroom(s1_request_head()))
    _leg("pipe only", base, "adapter->gate0", 1.0)
    room = solve(_ungrown(s1_south()))
    _leg("+forwarder", room, "adapter->gate0", 1.0)
    best, variant = None, ""
    for prob in (s1_request_head(), s1_south()):
        rep = solve(prob)
        if rep.best is not None and (best is None or rep.best_cost < best.best_cost):
            best, variant = rep, prob.name
    got = _leg("+reach", best, "adapter->gate0", 1.0)
    if got is None or base.best is None:
        bad += 1
    else:
        kind, drawn, charged = got
        want = MEASURED["adapter->gate0"][0]
        ok = kind == want
        bad += not ok
        print(f"        [{'OK' if ok else 'XX'}] solver chose {kind!r}, "
              f"machine chose {want!r}   (variant {variant})")
        saved.append(("adapter->gate0", base.best.routes["adapter->gate0"].cells, charged))

    # ── S2: the two chain links ──────────────────────────────────────────────
    print("\n  gate chain links (TAPED_CHAIN_REACH: machine reached, 25 -> 7 cells)")
    plain = solve(_noroom(s2_chain()))
    full = solve(s2_chain())
    for i, leg in enumerate(("gate0->gate1", "gate1->gate2")):
        w = LINK_W[i]
        print(f"    {leg}  (weight {w:.4f})")
        _leg("pipe only", plain, leg, w)
        got = _leg("+room +reach", full, leg, w)
        if got is None or plain.best is None:
            bad += 1
            continue
        kind, _d, charged = got
        want = MEASURED[leg][0]
        ok = kind == want
        bad += not ok
        print(f"        [{'OK' if ok else 'XX'}] solver {kind!r} vs machine {want!r}")
        saved.append((leg, plain.best.routes[leg].cells * w, charged * w))

    # ── S3: the four feed arms — the legs a reach provably cannot take ───────
    print("\n  reqK->bankK feed arms (TAPED_FEED_TELEPORT: machine put a room on each)")
    for k, (plainlen, w) in enumerate(zip((43, 43, 42, 95), FEED_W, strict=False)):
        leg = f"req{k}->bank{k}"
        prob = s3_feed(k, plainlen, w)
        p0 = solve(_noroom(prob))
        rep = solve(prob)
        print(f"    {leg}  (weight {w:.4f})")
        _leg("pipe only", p0, leg, w)
        got = _leg("+room +reach", rep, leg, w)
        if got is None or p0.best is None:
            bad += 1
            continue
        kind, _d, charged = got
        want = MEASURED[leg][0]
        ok = kind == want
        bad += not ok
        print(f"        [{'OK' if ok else 'XX'}] solver {kind!r} vs machine {want!r}; "
              f"{rep.rejected_by_bindings}/{rep.candidates} candidates refused by "
              f"check_bindings")
        saved.append((leg, p0.best.routes[leg].cells * w, charged * w))

    # ── S4: the answer path — measured builds, no corridor needed ───────────
    print("\n  answer path (STORE_ANSWER_WEST, -0.52%): all four builds measured")
    ok, lines = answer_path_probe()
    for line in lines:
        print(line)
    print(f"        [{'OK' if ok else 'XX'}] the cost model "
          f"{'ranks them as the tour did' if ok else 'ranks them WRONGLY'}")
    bad += not ok

    tot_plain = sum(a for _n, a, _b in saved)
    tot_got = sum(b for _n, _a, b in saved)
    print("\n" + "-" * 78)
    print(f"  weighted cells over these legs: plain {tot_plain:.1f} -> solved {tot_got:.1f}")
    print(f"  predicted saving: {(tot_plain - tot_got) * TICKS_PER_WEIGHTED_CELL:,.0f} ticks")
    print("  measured, same legs (METRICS.md M12 / M13 / M13b):")
    print("      request leg, 58 -> 6 + room       -49,631,147")
    print("      request room -> roof reach        -11,425,101")
    print("      feed arms, forwarder on each      -31,318,566")
    print("      (chain links folded into M13's roof+chain step)")
    print("-" * 78)
    return bad
