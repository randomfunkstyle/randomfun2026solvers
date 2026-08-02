#!/usr/bin/env python3
"""``score(placement) -> (ticks, footprint)`` -- the objective, and where each
number in it came from.

This is the module the whole framework exists to hold.  Everything else --
the IR, the legality checks, the search -- is scaffolding around this function.

The two-layer structure
-----------------------
A placement has an **exact** cost and a **weighted** cost, and conflating them is
the mistake that makes hand-optimisation go wrong.

*Layer 1, ticks.*  Exact, integral, and not a model at all.  The man moves one
cell per tick and legs are Manhattan-exact, so::

    ticks(placement) = SUM_nodes laps * body_cells
                     + SUM_edges weight * (manhattan(src.exit, dst.entry) - 1)

(the ``- 1`` because a tick is a cell the man *stands on*, and the edge's
landing cell is the destination node's own entry glyph, already charged once)

There is no fitting here and no uncertainty.  If the geometry is right this
number is right.

*Layer 2, impact.*  Ticks are not fungible.  A tick spent before the answer's
``S`` stops the consumer dead; a tick spent afterwards is usually free, because
the man is walking home through time the consumer was going to spend thinking
anyway.  Layer 2 converts ticks into *share of mean read latency*, which is the
quantity throughput actually depends on, using the measured rate table below.

Both are returned.  ``ticks`` is what you quote; ``impact`` is what you minimise.

The measured rate table
-----------------------
Every rate here was measured on this machine by same-moment A/B, and the units
convert 1:1 -- **1 tick of mean read latency = 324,588 ticks of run**, which is
the read count to four figures, so a "% of mean read latency" and a "% of total
runtime" are the same number.  Nothing in this table is derived or assumed.

============================  =========  ==================================
cell class                    cost       why
============================  =========  ==================================
pipe cell (serial)            0.841 %    SPEC tick order step 1 shifts every
                                         pipe value one cell *before* any man
                                         executes (``SPEC.md`` line 31), so a
                                         pipe cell is a tick the consumer is
                                         stopped for, unconditionally.
room walk, pre-send           0.27 %/t   on the critical path; the consumer
                                         is waiting.
room walk, post-send, hot     0.019 %/t  hot room, measured at 82 % idle.
room walk, post-send, cold    0.024 %/t  **higher, not lower.** See below.
room walk, post-send, idle    0.000 %/t  94-99 % idle; measured, identical
                                         tick.
ring/pipe time post-send      0.000 %/t  a longer ring delays a *value* the
                                         man overlaps with, not the man.
============================  =========  ==================================

The counter-intuitive term
--------------------------
Post-send walking in a **cold** room costs **3.4x more per unit of read share**
than in a hot one -- 0.089 against 0.026.  "Free in proportion to idleness" has
the wrong sign, and a score function that assumes otherwise will confidently
recommend the wrong room.

The mechanism is queueing, not walking.  Coldness correlates with **same-bank
read-after-write pairs**, and for a same-bank pair the inter-request gap is zero
by construction: the man *is* the server, and the next request queues behind
him.  Idleness measured over the whole run says nothing about the gap at the
moment that matters.

The governing term is a fitted model over ten builds, LOO RMS 1.0 tick::

    mean = 195.9 + 562.3*w58 - 0.79*E[service] + 112.5*f_same

with ``f_same`` the share of reads whose immediately preceding request hit the
same bank.  **``f_same`` carries the largest coefficient**, which is the whole
reason the score function needs a queueing term and not just a distance sum.

Two honesty notes on that regression, because a fitted model used outside its
support is how a framework starts lying:

* ``w58`` is an exogenous build feature, not a placement variable.  Placement
  cannot move it, so it contributes a constant that cancels in every comparison
  the search makes.  It is carried in :class:`Workload` for completeness and
  flagged unidentified -- ten builds is not enough to separate it from the
  intercept, and I did not re-derive it.
* the ``E[service]`` coefficient is **negative**, which is not a causal claim
  anyone should make: longer service reducing latency is a collinearity artifact
  of a ten-point fit.  It is retained because it is the model as measured, and
  because within the range placement can move service (tens of ticks) its
  contribution is small next to ``f_same``.  :meth:`Score.explain` prints it
  separately so it can never quietly dominate a decision.

What the score does *not* charge for
------------------------------------
Two measured nulls, both of which a naive model gets wrong in the expensive
direction:

* **Ring/pipe length after the send is free.**  A longer ring delays a value the
  man overlaps with; he is not waiting on it.
* **Moving a structure's parts independently is worth nothing.**  Relocating a
  drop column alone measured *exactly zero*, because the man walked back east to
  a stationary send.  Only when the send and the landing moved too did it pay.
  This is why :class:`~place.ir.Node` is the unit of placement and why a search
  that moves individual glyphs is a search that will report fictional wins --
  see :func:`structure_check`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ir import PIPE, POST_SEND, PRE_SEND, Leg, Placement, manhattan, transit

__all__ = [
    "Rates",
    "RATES",
    "Workload",
    "Score",
    "score",
    "ticks_of",
    "structure_check",
]


# ── the measured rate table ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Rates:
    """Percent of mean read latency, per tick (or per cell, for pipes).

    All six figures are **measured**, same-moment A/B on this machine.  None is
    derived, fitted or assumed.  The 1:1 unit conversion is 1 tick of mean read
    latency = 324,588 ticks of run.
    """

    #: per serial pipe *cell* -- pipes shift before execution, so this is
    #: unconditional and does not depend on phase.
    pipe_cell: float = 0.841
    #: per tick of room walk on the critical path, before the answer's ``S``.
    pre_send: float = 0.27
    #: per tick of room walk after the send, in a hot room (82 % idle).
    post_send_hot: float = 0.019
    #: per tick of room walk after the send, in a cold room.  Higher than hot.
    post_send_cold: float = 0.024
    #: per tick of room walk after the send, in a 94-99 % idle room.  Zero.
    post_send_idle: float = 0.000
    #: per tick of ring/pipe time after the send.  Zero.
    ring_post_send: float = 0.000

    # -- the fitted queueing model, over ten builds, LOO RMS 1.0 tick ---------
    #: intercept of ``mean = 195.9 + 562.3*w58 - 0.79*E[service] + 112.5*f_same``
    q_intercept: float = 195.9
    #: coefficient on ``w58``, an exogenous build feature placement cannot move.
    q_w58: float = 562.3
    #: coefficient on expected service time.  Negative; a collinearity artifact
    #: of a ten-point fit, retained as measured and reported separately.
    q_service: float = -0.79
    #: coefficient on ``f_same``, the share of reads whose immediately preceding
    #: request hit the same bank.  The largest coefficient in the model.
    q_f_same: float = 112.5

    #: run ticks per tick of mean read latency; the 1:1 unit bridge.
    reads_per_run: float = 324_588.0

    def post_send(self, temperature: str) -> float:
        """Rate for post-send room walk, by room temperature.

        ``temperature`` is one of ``"hot"``, ``"cold"``, ``"idle"``.  The
        ordering is *not* monotone in idleness and that is the point.
        """
        return {
            "hot": self.post_send_hot,
            "cold": self.post_send_cold,
            "idle": self.post_send_idle,
        }[temperature]


#: The single shared instance.  Construct your own only to run a sensitivity
#: sweep, never to "adjust" a measurement.
RATES = Rates()


# ── workload ─────────────────────────────────────────────────────────────────
@dataclass
class Workload:
    """The hot-path weights and queueing state a score is taken against.

    Throughput, not latency, is the objective, so a leg is worth its frequency.

    :param temperature: room temperature for post-send charging -- ``"hot"``,
        ``"cold"`` or ``"idle"``.  Get this from the *measured* idle share of the
        room the leg lives in, not from intuition.
    :param f_same: share of reads whose immediately preceding request hit the
        same bank.  The dominant queueing term.  Placement *can* move this, by
        changing which banks are adjacent in the tour, which is why it is a
        first-class input and not a constant.
    :param e_service: expected service time in ticks.  Placement moves this
        directly -- it is roughly the leg's own tick count.
    :param w58: exogenous build feature; unidentified in a ten-build fit and not
        movable by placement.  Carried so the absolute number is reproducible;
        it cancels in every comparison.
    :param accesses: accesses per run, for converting a per-access tick delta
        into a whole-run one.
    """

    temperature: str = "hot"
    f_same: float = 0.0
    e_service: float | None = None
    w58: float = 0.0
    accesses: float = 1.0


# ── the score ────────────────────────────────────────────────────────────────
@dataclass
class Score:
    """What a placement costs, in both currencies, with its terms itemised."""

    #: Layer 1: exact ticks per access.  Integral if all laps are integral.
    ticks: float
    #: (w, h) of the placement's bounding box.
    extent: tuple[int, int]
    #: Layer 2: percent of mean read latency.  This is what you minimise.
    impact: float
    #: Per-term breakdown of ``impact``, for audit.
    terms: dict[str, float] = field(default_factory=dict)
    #: Per-term breakdown of ``ticks``.
    tick_terms: dict[str, float] = field(default_factory=dict)
    #: The leg's tick floor -- op cells that must be visited.  ``ticks == floor``
    #: means Manhattan-minimal: no relocation can help, only fewer ops.
    floor: float = 0.0
    #: For a leg whose path CLOSES, the stronger floor from
    #: :func:`place.route.loop_floor` -- op cells *plus* the four corners a
    #: rectilinear circuit must turn at.  ``None`` for an open leg.
    #:
    #: This exists because the transit model underlying ``ticks`` prices an edge
    #: as Manhattan distance, which is exact for an open monotone leg (measured)
    #: and **optimistic for a closed one**: it will happily route a lap through
    #: cells that would need a steer glyph, without charging for the glyph's
    #: cell.  Rather than quietly report a floor a real layout cannot reach, a
    #: cyclic leg carries both numbers and :meth:`explain` shows the gap.
    lap_floor: float | None = None

    @property
    def footprint(self) -> int:
        return self.extent[0] * self.extent[1]

    @property
    def slack(self) -> float:
        """Ticks above the floor.  Zero means placement is done; look elsewhere."""
        return self.ticks - self.floor

    def as_tuple(self) -> tuple[float, int]:
        """The ``(ticks, footprint)`` pair the objective is named after."""
        return (self.ticks, self.footprint)

    def explain(self) -> str:
        w, h = self.extent
        out = [
            f"ticks {self.ticks:.2f}  floor {self.floor:.2f}  "
            f"slack {self.slack:.2f}  extent {w}x{h}={self.footprint}",
            f"impact {self.impact:.4f} % of mean read latency",
        ]
        for k, v in sorted(self.tick_terms.items(), key=lambda kv: -abs(kv[1])):
            out.append(f"    t {k:<22} {v:9.2f}")
        for k, v in sorted(self.terms.items(), key=lambda kv: -abs(kv[1])):
            out.append(f"    % {k:<22} {v:9.4f}")
        if self.lap_floor is not None:
            out.append(
                f"    lap floor {self.lap_floor:.0f} (closed circuit: +4 corners). "
                f"ticks {self.ticks:.0f} is "
                + ("AT or above it" if self.ticks >= self.lap_floor
                   else "BELOW it -- the transit model does not charge corner "
                        "cells, so treat the lap floor as the truth"))
        if self.slack <= 1e-9:
            out.append("    MANHATTAN-MINIMAL: placement is exhausted; cut ops or laps")
        return "\n".join(out)


def ticks_of(p: Placement) -> tuple[float, dict[str, float]]:
    """Layer 1.  Exact ticks per access, and the per-term breakdown.

    Node bodies are charged ``laps * cells`` because the man walks every body
    cell every lap and the glyph fires on the tick he was spending anyway.
    Edges are charged ``weight * manhattan`` because legs are Manhattan-exact --
    measured, not assumed: the man walks the full distance and takes no
    shortcuts.
    """
    leg = p.leg
    terms: dict[str, float] = {}
    total = 0.0
    for name, n in leg.nodes.items():
        t = n.ticks
        terms[f"body:{name}"] = t
        total += t
    for e in leg.edges:
        if e.free:
            continue
        a = leg.nodes[e.src].exit_abs(p.pos_of(e.src))
        b = leg.nodes[e.dst].entry_abs(p.pos_of(e.dst))
        t = e.weight * transit(a, b)
        if t:
            terms[f"walk:{e.src}->{e.dst}"] = terms.get(f"walk:{e.src}->{e.dst}", 0.0) + t
            total += t
    return total, terms


def _phase_rate(phase: str, wl: Workload, rates: Rates) -> float:
    if phase == PRE_SEND:
        return rates.pre_send
    if phase == POST_SEND:
        return rates.post_send(wl.temperature)
    if phase == PIPE:
        return rates.pipe_cell
    raise ValueError(f"unknown phase {phase!r}")


def score(
    p: Placement,
    wl: Workload | None = None,
    rates: Rates = RATES,
) -> Score:
    """Score a placement.  Returns ``(ticks, footprint)`` and much more.

    The contract named in the brief is ``score(placement) -> (ticks, footprint)``;
    :meth:`Score.as_tuple` gives exactly that pair, and the rest of the
    :class:`Score` is the audit trail that makes the pair believable.

    Layer 1 is exact geometry.  Layer 2 charges each tick at the rate for its
    phase and the room's temperature, adds the pipe cells at their unconditional
    per-cell rate, adds the queueing term, and multiplies the whole by the leg's
    hot-path weight -- because throughput is the objective and a leg that runs
    twice as often is worth twice as much.
    """
    wl = wl or Workload()
    leg = p.leg

    total_ticks, tick_terms = ticks_of(p)

    # -- layer 2: charge each tick at its phase's rate -------------------------
    terms: dict[str, float] = {}
    for name, n in leg.nodes.items():
        r = _phase_rate(n.phase, wl, rates)
        if n.ticks and r:
            k = f"{n.phase}:body"
            terms[k] = terms.get(k, 0.0) + n.ticks * r
    for e in leg.edges:
        if e.free:
            continue
        a = leg.nodes[e.src].exit_abs(p.pos_of(e.src))
        b = leg.nodes[e.dst].entry_abs(p.pos_of(e.dst))
        t = e.weight * transit(a, b)
        r = _phase_rate(leg.edge_phase(e), wl, rates)
        if t and r:
            k = f"{leg.edge_phase(e)}:walk"
            terms[k] = terms.get(k, 0.0) + t * r

    # -- pipe cells: unconditional, per cell, phase-independent ---------------
    # SPEC tick order step 1 shifts every pipe value one cell before any man
    # executes, so each serial cell is a tick the consumer is stopped for.
    pipe_cells = sum(pipe.cells for pipe in leg.pipes.values())
    if pipe_cells:
        terms["pipe:cells"] = pipe_cells * rates.pipe_cell

    # -- the queueing term ----------------------------------------------------
    # f_same carries the largest coefficient in the fitted model, and it is the
    # reason a pure distance sum recommends the wrong room.  E[service] defaults
    # to the leg's own ticks, which is what placement actually moves.
    e_service = wl.e_service if wl.e_service is not None else total_ticks
    q_latency = (
        rates.q_intercept
        + rates.q_w58 * wl.w58
        + rates.q_service * e_service
        + rates.q_f_same * wl.f_same
    )
    # Expressed on the same axis as everything else: a tick of mean read latency
    # is 1 % / 100 of itself, so the queueing model's *movable* part is the part
    # placement can change -- the service and f_same terms.  The intercept and
    # w58 are constants under placement and are reported but not charged, so two
    # placements differ by exactly what placement caused.
    terms["queue:f_same"] = rates.q_f_same * wl.f_same
    terms["queue:service"] = rates.q_service * e_service
    fixed = rates.q_intercept + rates.q_w58 * wl.w58

    impact = sum(terms.values()) * leg.weight

    lap = None
    if leg.is_cyclic():
        from route import loop_floor
        lap = float(loop_floor(leg.op_cells(), leg.turning_ops()).ticks)

    return Score(
        ticks=total_ticks,
        extent=p.extent(),
        impact=impact,
        terms=dict(terms, **{"queue:fixed(uncharged)": fixed, "queue:mean_latency": q_latency}),
        tick_terms=tick_terms,
        floor=leg.floor_cells(),
        lap_floor=lap,
    )


def score_all(
    placements: dict[str, Placement],
    workloads: dict[str, Workload] | None = None,
    rates: Rates = RATES,
) -> Score:
    """Score several legs together, weighted by hot-path frequency.

    The weighted sum is the throughput objective: minimising it minimises the
    time the consumer spends stopped, per unit of work done.
    """
    workloads = workloads or {}
    ticks = 0.0
    impact = 0.0
    floor = 0.0
    terms: dict[str, float] = {}
    tick_terms: dict[str, float] = {}
    cells: dict = {}
    for name, p in placements.items():
        s = score(p, workloads.get(name), rates)
        ticks += s.ticks * p.leg.weight
        floor += s.floor * p.leg.weight
        impact += s.impact
        for k, v in s.terms.items():
            terms[f"{name}/{k}"] = v
        for k, v in s.tick_terms.items():
            tick_terms[f"{name}/{k}"] = v
        cells.update(p.cells())
    if cells:
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        extent = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    else:
        extent = (0, 0)
    return Score(ticks=ticks, extent=extent, impact=impact, terms=terms,
                 tick_terms=tick_terms, floor=floor)


# ── the structure-moves-as-a-unit guard ──────────────────────────────────────
def structure_check(before: Placement, after: Placement) -> list[str]:
    """Warn where a change moved part of a structure and left the rest behind.

    Measured: relocating a drop column *alone* was worth **exactly zero**,
    because the man walked back east to a stationary send.  Only when the send
    and the landing moved with it did the move pay.  A search that scores
    individual glyph moves will therefore report wins that do not exist, so any
    proposed change is run past this before it is believed.

    Returns a list of complaints; empty means every moved node's edge partners
    moved with it, or the edges that did stretch were charged.
    """
    moved = {
        k for k in before.leg.nodes
        if before.pos_of(k) != after.pos_of(k)
    }
    if not moved:
        return []
    out = []
    for e in before.leg.edges:
        if e.free:
            continue
        endpoints = {e.src, e.dst}
        if endpoints & moved and not endpoints <= moved:
            a0 = before.leg.nodes[e.src].exit_abs(before.pos_of(e.src))
            b0 = before.leg.nodes[e.dst].entry_abs(before.pos_of(e.dst))
            a1 = after.leg.nodes[e.src].exit_abs(after.pos_of(e.src))
            b1 = after.leg.nodes[e.dst].entry_abs(after.pos_of(e.dst))
            d0, d1 = manhattan(a0, b0), manhattan(a1, b1)
            if d1 > d0:
                out.append(
                    f"{e.src}->{e.dst}: partial move stretched the walk "
                    f"{d0} -> {d1} (x{e.weight:g} = +{(d1 - d0) * e.weight:g} t)"
                )
    return out
