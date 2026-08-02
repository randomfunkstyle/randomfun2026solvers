"""One ``z3.Optimize`` call for taped's best legal ``(pad, squash, drop, store_dy)``.

The frontier sweep in :mod:`tapedfront` reads a table by eye. This states the
same thing as a constrained optimisation and lets the solver answer it:

**Variables** ``pad``, ``squash``, ``drop``, ``store_dy``, all integers.

**Feasibility** is §7.1 verbatim, symbolically. Every glyph's position is affine
in the knobs (R1: ``x + pad-2`` for MEM glyphs; R3: ``y - (squash-13)`` for all of
them) and every touch's is too (R2/R3), so ``check_bindings``'s key
``(distance, attach_y, attach_x)`` becomes a lexicographic comparison over linear
integer terms and the whole gate is ~190 assertions. Nothing is sampled: the
solver proves the pad floor rather than failing to find a counterexample.

**The store gate**, which fires *before* ``check_bindings``, is
``store_dy <= 12 - squash OR store_dy == 16 - squash`` -- two levels a straight
request leg can sit on, not the one the registry records. See
:data:`tapedfront.STORE_DY`.

**The objective** is ``PRICE_COL * mem_x + PRICE_EFF * |eff - EFF_OPT| +
PRICE_STORE_LOW * (store_dy is on the lower branch)``, every price measured on
taped rather than inherited: men-v3's 2.436 t/instr per column and 0.649 per
corridor row are a different box with a different access mix, and taped's own
numbers are nothing like them.

The third term is the one nobody was costing. ``store_dy`` moves no glyph and no
touch -- it is invisible to §7.1 -- so it was being treated as a feasibility
detail and pinned to ``12 - squash``. It is worth **-0.940%** to put it on the
*other* side of the forbidden band instead, and that is more than a whole
``mem_x`` column. The solver finds it because the store gate is stated as a
disjunction rather than as an equation; an equation cannot express a second
branch, which is exactly why the recorded form hid the win.

**A caveat the model cannot carry.** ``pad`` below 0 is outside the encoding, on
purpose: ``_flat_lane``'s ``while x < target`` makes pads -1..-4 one ragged
geometry, so ``mem_x`` stops being a linear function of ``pad`` there and the
solver would optimise a fiction. The ragged case is priced by build instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import INCOMING, geom, load, want_of  # noqa: E402
from tapedfront import BASE, base_of  # noqa: E402

#: Ticks over five steady rounds, per unit, on taped -- measured by build + the
#: native round-gated emulator on the shipped 625x398 machine, not modelled.
#:
#: ``PRICE_COL`` is taken at matched corridor and matched store branch: pad 2 vs
#: pad 3 at squash 13 is +119,349, pad 2 vs pad 1 at squash 11 is -157,742.
#: 150,000 is the middle. (men-v3's 2.436 t/instr per column is roughly four
#: times taped's 0.65, because men-v3's box makes the lane walk a bigger share of
#: a shorter instruction.)
#:
#: ``EFF_OPT`` is not a guess either: ``route_lengths["rom->cpu"] == rom_capacity
#: == 46 + eff`` exactly, over every capture, and ticks are a function of
#: ``rom_capacity`` alone -- squash 6/drop 20 and squash 7/drop 21 are the same
#: ``rom_capacity`` 60 and come out **tick-identical at 29,129,469**, one row of
#: height apart. So machine *height* is tick-free and only the corridor counts.
PRICE_COL = 150_000.0       # one mem_x column, matched corridor and branch
PRICE_EFF = 36_000.0        # one corridor row off the optimum (pad 1, eff 3->6:
#                             29,284,946 -> 29,393,055, 108,109 over three rows)
PRICE_STORE_LOW = 276_750.0  # the lower store branch, measured twice at -0.940%
EFF_OPT = 3                 # rom_capacity 49; the corridor's tick minimum

#: The 6-round steady walk of the shipped taped machine, the denominator for a
#: percentage, and the tour figure a percentage is applied to.
BASE_STEADY = 29_442_688
BASE_TPI = 159.46


def build_model(g0, t0, pads=(0, 5), squashes=(0, 20), drops=(0, 44)):
    """Return ``(opt, vars)`` -- §7.1 + the store gate over the four knobs."""
    import z3

    pad = z3.Int("pad")
    squash = z3.Int("squash")
    drop = z3.Int("drop")
    sdy = z3.Int("store_dy")
    opt = z3.Optimize()
    opt.add(pad >= pads[0], pad <= pads[1])
    opt.add(squash >= squashes[0], squash <= squashes[1])
    opt.add(drop >= drops[0], drop <= drops[1])
    opt.add(sdy >= -20, sdy <= 20)
    # The store level gate, measured: a run up to ``12 - squash`` plus a second,
    # one-row-wide level at ``16 - squash``. Stating it as a disjunction rather
    # than as the recorded equation is what lets the solver find the second level.
    opt.add(z3.Or(sdy <= 12 - squash, sdy == 16 - squash))

    dsq = squash - BASE["squash"]
    dpad = pad - BASE["pad"]
    T = {}
    for n, (x, y) in t0.items():
        if n == "in":
            T[n] = (z3.IntVal(x), z3.IntVal(y))       # pinned to the north wall
        elif n == "rom":
            T[n] = (z3.IntVal(x), y - dsq + (drop - BASE["drop"]))
        else:
            T[n] = (z3.IntVal(x), y - dsq)
    inc = [n for n in t0 if n in INCOMING]
    out = [n for n in t0 if n not in INCOMING]

    def absv(e):
        return z3.If(e >= 0, e, -e)

    def lex_le(a, b):
        """``a`` wins under ``(distance, attach_y, attach_x)`` -- ties included.

        ``check_bindings`` has taken reading order since `c86ef95`, so the
        intended pipe only has to be the *minimum* under this key, and a tie it
        reads first is a legal binding.
        """
        return z3.Or(
            a[0] < b[0],
            z3.And(a[0] == b[0],
                   z3.Or(a[1] < b[1], z3.And(a[1] == b[1], a[2] <= b[2]))),
        )

    n_assert = 0
    for gx, gy, gl, band in g0:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        X = gx + (dpad if band == "mem" else 0)
        Y = gy - dsq

        def key(n):
            return (absv(T[n][0] - X) + absv(T[n][1] - Y), T[n][1], T[n][0])

        for q in pool:
            if q != w:
                opt.add(lex_le(key(w), key(q)))
                n_assert += 1
    return opt, {"pad": pad, "squash": squash, "drop": drop, "store_dy": sdy,
                 "n_assert": n_assert}


def solve(g0, t0, price_col=PRICE_COL, price_eff=PRICE_EFF, eff_opt=EFF_OPT,
          price_store_low=PRICE_STORE_LOW, extra=None, label=""):
    import z3

    opt, v = build_model(g0, t0)
    if extra is not None:
        extra(opt, v)
    mem_x = 19 + v["pad"]
    eff = v["drop"] - v["squash"]
    # |eff - eff_opt|: the corridor's measured tick curve is a V with its floor
    # at rom_capacity 49, not a monotone cost, so the penalty is a distance.
    dev = z3.If(eff - eff_opt >= 0, eff - eff_opt, eff_opt - eff)
    low = z3.If(v["store_dy"] <= 12 - v["squash"], 1, 0)
    cost = (z3.ToReal(mem_x) * price_col + z3.ToReal(dev) * price_eff
            + z3.ToReal(low) * price_store_low)
    opt.minimize(cost)
    if opt.check() != z3.sat:
        print(f"  {label}UNSAT -- no legal setting in the box", flush=True)
        return None
    m = opt.model()
    got = {k: m[v[k]].as_long() for k in ("pad", "squash", "drop", "store_dy")}
    got["mem_x"] = 19 + got["pad"]
    got["eff"] = got["drop"] - got["squash"]
    got["h"] = 398 + BASE["squash"] - got["squash"]
    got["branch"] = "low" if got["store_dy"] <= 12 - got["squash"] else "HIGH"
    got["cost"] = float(m.eval(cost).as_fraction())
    print(f"  {label}pad {got['pad']} squash {got['squash']} drop {got['drop']} "
          f"store_dy {got['store_dy']} ({got['branch']}) -> mem_x {got['mem_x']} "
          f"eff {got['eff']} h {got['h']}  (cost {got['cost']:,.0f})", flush=True)
    return got


if __name__ == "__main__":
    import z3

    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    g0, t0 = geom(base_of(recs))
    opt, v = build_model(g0, t0)
    print(f"§7.1 encoded as {v['n_assert']} lexicographic assertions over "
          f"4 integer knobs", flush=True)

    print("\n=== the pad floor, proved rather than sampled ===", flush=True)
    for p in range(0, 4):
        o, vv = build_model(g0, t0)
        o.add(vv["pad"] == p)
        r = o.check()
        print(f"  pad {p}: {'FEASIBLE' if r == z3.sat else 'INFEASIBLE at every '
              '(squash, drop) in the box'}", flush=True)
    print("  and the corridor optimum is not free at every pad:", flush=True)
    for p in range(0, 3):
        o, vv = build_model(g0, t0)
        o.add(vv["pad"] == p, vv["drop"] - vv["squash"] == EFF_OPT)
        print(f"    pad {p} at eff {EFF_OPT}: "
              f"{'reachable' if o.check() == z3.sat else 'PROVED UNREACHABLE'}",
              flush=True)

    print("\n=== optimum ===", flush=True)
    solve(g0, t0, label="unconstrained: ")
    for p in range(0, 3):
        solve(g0, t0, extra=lambda o, vv, p=p: o.add(vv["pad"] == p),
              label=f"pad pinned {p}: ")

    # The ragged band is outside the pad encoding (R1 does not hold below 0), so
    # it gets its own model off its own capture, with the pad delta pinned to 0.
    neg = next((r for r in recs if r["knobs"] == {"pad": -1}), None)
    if neg is not None:
        print("\n=== the ragged band (mem_pad -1..-4, one geometry) ===", flush=True)
        gn, tn = geom(neg)
        print("   (the pad column below is the pinned dummy: the ragged band's own "
              "columns are 18..24, 26 and no pad moves them)", flush=True)
        solve(gn, tn, extra=lambda o, vv: o.add(vv["pad"] == BASE["pad"]),
              label="ragged: ")
