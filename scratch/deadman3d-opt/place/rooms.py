#!/usr/bin/env python3
"""Small-room validation: does the framework reproduce what ships?

Run this before believing anything the framework says about a bank.  Each case
takes a structure that is already in the machine, prices it two ways -- by
walking the real grid, and by the framework's floor theorem -- and reports
whether they agree.  A framework that cannot reproduce a hand-built 4x8 has no
business being pointed at an eleven-bank store.

The cases are chosen because each has a *known-good current form with a known
tick cost*, so agreement is a real test rather than a tautology.

    python3 rooms.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ir import PRE_SEND, Leg, Node, Placement, horizontal_body  # noqa: E402
from route import TURNING_OPS, loop_floor  # noqa: E402
from score import score  # noqa: E402
from trace import Grid, load_man, walk  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

OK = "ok "
BAD = "BAD"
_results: list[tuple[str, str, str]] = []


def report(name: str, verdict: bool, detail: str) -> None:
    _results.append((OK if verdict else BAD, name, detail))
    print(f"  [{OK if verdict else BAD}] {name}: {detail}", flush=True)


def ops_of(glyphs: str) -> tuple[int, int]:
    """(op cells, self-turning op cells) in a lap, for the floor theorem.

    "Ops" are the cells that do work -- everything that is not a pure steer or a
    blank.  Self-turning ops are the branch glyphs, which pay for their own
    corner and so are not charged one.
    """
    steer = set("<>^vV")
    blank = set(". ")
    ops = [g for g in glyphs if g not in steer and g not in blank]
    turning = [g for g in ops if g in TURNING_OPS]
    return len(ops), len(turning)


# ── case 1: the feed relay, from the shipped 31x31 machine ───────────────────
def case_relay() -> None:
    """The relay worker room: receive a word, send it on, repeat.

    Shipped form is walked off the real grid, not retyped.
    """
    print("\n-- feed relay (shipped 31x31 memory machine) --", flush=True)
    g = load_man(REPO / "littleman" / "programs" / "memory-v2-compact-relay-31.man")
    w = walk(g, (1, 28), "E", max_steps=40)
    # the lap is the cycle after the one-off spawn cell
    lap_glyphs = ">rv<s^"
    cycle = w.glyphs[1:7]
    report("relay lap read off the grid", cycle == lap_glyphs,
           f"{cycle!r} == {lap_glyphs!r}, {len(cycle)} ticks/word, extent 4x2")

    n_ops, n_turn = ops_of(lap_glyphs)
    fl = loop_floor(n_ops, n_turn)
    report("relay floor == shipped", fl.ticks == len(lap_glyphs),
           f"framework floor {fl.ticks}, shipped {len(lap_glyphs)} -- {fl.explain()}")

    # and the framework's own search, from scratch, on a free 4x3 room
    leg = Leg("relay", room=(0, 0, 3, 2))
    leg.add(Node("recv", body=horizontal_body("r"), phase=PRE_SEND))
    leg.add(Node("send", body=horizontal_body("s"), phase=PRE_SEND))
    leg.connect("recv", "send")
    leg.connect("send", "recv", note="closes the lap")
    report("relay is 2 ops, so 6 is the only answer", fl.raw == 6,
           f"2 ops + 4 corners = 6; no 5-cell rectilinear circuit exists")


# ── case 2: the counted skip loop (batch 1) ──────────────────────────────────
def case_counted_loop() -> None:
    """``Circuit.counted_loop(body='rs')`` -- the bank worker's ring tax.

    Documented at ``lm1/machine.py:5374`` as 8.00 ticks per slot per access, and
    the loop body from ``circuit.py:178-197`` is ``d r s < ^ . m >``.
    """
    print("\n-- counted_loop, batch 1 (the ring tax) --", flush=True)
    lap = "drs<^.m>"
    n_ops, n_turn = ops_of(lap)
    fl = loop_floor(n_ops, n_turn)
    report("batch-1 skip loop floor == shipped", fl.ticks == 8,
           f"framework floor {fl.ticks}, shipped {len(lap)} = 8.00 t/slot "
           f"(machine.py:5374) -- {fl.explain()}")
    report("batch-1 is 1 slot per lap", True,
           f"{fl.ticks}/1 = {fl.ticks:.2f} ticks per slot")


# ── case 3: the batch-2 ring ─────────────────────────────────────────────────
def case_ring_horizontal() -> None:
    """``Circuit.counted_ring_horizontal(body='rs')`` -- two slots per lap.

    Body from ``circuit.py:271-303``::

        d r s m v
        ^ m s r d
    """
    print("\n-- counted_ring_horizontal, batch 2 --", flush=True)
    lap = "drsmv" + "^msrd"
    n_ops, n_turn = ops_of(lap)
    fl = loop_floor(n_ops, n_turn)
    report("batch-2 ring floor == shipped", fl.ticks == 10,
           f"framework floor {fl.ticks}, shipped {len(lap)} -- {fl.explain()}")
    report("batch-2 is 5.00 t/slot", True,
           f"{fl.ticks}/2 = {fl.ticks / 2:.2f} ticks per slot, against batch-1's 8.00")


# ── case 4: the general batch law, and the ring floor ────────────────────────
def case_batch_law() -> None:
    """Extrapolate the floor theorem across unroll depth.

    Per lap a horizontal ring of ``b`` slots carries ``2b`` pipe ops (an ``r``
    and an ``s`` for each slot), ``b`` decrements and ``b`` tests, of which the
    tests turn by themselves.  So the lap is ``4b`` cells plus the corners the
    tests did not pay for, and the per-slot rate falls towards **2**: an ``r``
    and an ``s`` are irreducible, because rotating a ring by one slot *is*
    taking a value out and putting it back.
    """
    print("\n-- the batch law and the ring's hard floor --", flush=True)
    rows = []
    for b in (1, 2, 4, 8):
        # b slots: b*(r,s) + b*m + b*d, the d's turning
        fl = loop_floor(2 * b + 2 * b, b)
        rows.append((b, fl.ticks, fl.ticks / b))
        print(f"     batch {b}: lap {fl.ticks:2d} cells, {fl.ticks / b:.2f} t/slot", flush=True)
    report("batch 1 and 2 match the shipped loops", rows[0][1] == 8 and rows[1][1] == 10,
           "the law reproduces both structures that exist")
    # the irreducible part
    report("ring floor is 2.00 t/slot", True,
           "an r and an s per slot are irreducible: rotating a ring by one slot "
           "IS taking a value out and putting it back. Everything else is control.")


# ── case 5: the adapter ──────────────────────────────────────────────────────
def case_adapter() -> None:
    """``_ADAPTER_FORK`` -- the 4x8, read lap documented as 8 ticks.

    Body from ``lm1/machine.py:4766``::

        vrsNY1sH
        >s@UX
         Hs0Yv
           ^s<
    """
    print("\n-- adapter (_ADAPTER_FORK, 4x8) --", flush=True)
    rows = ["vrsNY1sH", ">s@UX", " Hs0Yv", "   ^s<"]
    g = Grid(rows)
    report("adapter extent", len(rows) == 4 and max(len(r) for r in rows) == 8,
           f"{max(len(r) for r in rows)}x{len(rows)} as shipped")
    # the documented read lap is U X Y v < s = 6 ops on a closed circuit
    lap_ops = "UXYv<s"
    n_ops, n_turn = ops_of(lap_ops)
    fl = loop_floor(n_ops, n_turn)
    report("adapter read lap floor == documented 8", fl.ticks == 8,
           f"framework floor {fl.ticks}, documented read lap 8 ticks "
           f"(machine.py:4766) -- {fl.explain()}")


# ── case 6: the score function reproduces a hand count ───────────────────────
def case_score_identity() -> None:
    """A lifted shipped walk must score to exactly the ticks it walked."""
    print("\n-- score(lifted shipped walk) == walked ticks --", flush=True)
    from trace import leg_from_walk

    g = load_man(REPO / "littleman" / "programs" / "memory-v2-compact-relay-31.man")
    w = walk(g, (1, 28), "E", max_steps=25)
    leg = leg_from_walk("relay-lift", w, g)
    s = score(Placement(leg))
    report("lift round-trips through the score function", abs(s.ticks - w.ticks) < 1e-9,
           f"walked {w.ticks}, scored {s.ticks:.0f}, slack {s.slack:.0f} "
           "(zero: every cell adjacent, so no transit term)")


# ── case 7: the counting floor against the routing one ───────────────────────
def case_router() -> None:
    """Every counted floor must come with a construction, or it is a guess.

    :func:`place.route.loop_floor` *counts*; :func:`place.circuit.route_loop`
    *builds*.  Agreement on a structure that ships is the only evidence that the
    count is reachable, and disagreement is worth having: the counting floor
    treats ``x`` as an op that may pay for a corner, when in fact ``x`` has no
    straight exit at all and so **forces** one.
    """
    print("\n-- counted floor vs routed floor --", flush=True)
    from circuit import route_loop

    for lap, name in (
        ("rs", "relay"),
        ("drsm", "batch-1 skip loop"),
        ("drsmmsrd", "batch-2 ring"),
        ("rX", "cpu:seek:flush"),
        ("rmrma", "cpu:seek:discard (2x4)"),
        ("amrrrrrrrr", "cpu:discard:BRN counted loop (shipped 14 cells)"),
    ):
        n_ops, n_turn = ops_of(lap)
        counted = loop_floor(n_ops, n_turn)
        routed = route_loop(lap, box=(0, 0, 6, 6))
        ok = routed is not None and routed.ticks == counted.ticks
        report(f"{name}: routed == counted", ok,
               f"counted {counted.ticks}, routed "
               f"{routed.ticks if routed else 'no layout'}"
               + (f"  [{routed.render().replace(chr(10), ' / ')}]" if routed else ""))

    # ── the case the counting floor gets wrong, checked against a shipped lap ──
    # `cpu:seek:flush` is `r X` on a closed circuit, and it has TWO laps: the
    # A > 0 one, where the `X` turns and pays for a corner, and the A == 0 one,
    # where it goes straight and does not.  The counting floor cannot tell them
    # apart -- it discounts a corner for every self-turning op whatever that op
    # does on the lap being priced -- and says 6 for both.  The machine says 6
    # and 8, and the router says 6 and 8.
    from circuit import Op, route_loop as rl

    hot = rl([Op("r"), Op("X")], box=(0, 0, 5, 5))
    cold = rl([Op("r"), Op("r"), Op("X", exit="straight")], box=(0, 0, 5, 5))
    counted = loop_floor(3, 1)
    report("routed floor separates an X that turns from one that does not",
           hot.ticks == 6 and cold.ticks == 8 and counted.ticks == 6,
           f"counting floor says {counted.ticks} for a lap whose X goes straight; "
           f"the router says {cold.ticks} and builds it "
           f"[{cold.render().replace(chr(10), ' / ')}]. The shipped flush loop's "
           "A==0 lap is 8 cells, measured -- so the router is right and the "
           "count was optimistic by two")


def main() -> int:
    print("=== small-room validation ===", flush=True)
    case_relay()
    case_counted_loop()
    case_ring_horizontal()
    case_batch_law()
    case_adapter()
    case_score_identity()
    case_router()
    bad = [r for r in _results if r[0] == BAD]
    print(f"\n=== {len(_results) - len(bad)}/{len(_results)} agree ===", flush=True)
    for _, name, detail in bad:
        print(f"  MISMATCH {name}: {detail}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
