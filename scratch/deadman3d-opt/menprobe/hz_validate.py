"""Score the binding model against the builder, and say the number out loud.

    hz_validate.py [n] [--build]

Two confusion matrices, because there are two different claims to check and
conflating them is how a model gets credit it has not earned:

* **model vs §7.1** — :func:`hz_geom.predict` against :func:`hz_core.capture`,
  which poses the *real* forty binding problems.  A disagreement here is a wrong
  geometry and nothing else.
* **§7.1 vs the builder** — :func:`hz_core.capture` against :func:`hz_core.build`.
  This one cannot be perfect by construction: capture stops at ``check_bindings``
  and cannot see a room that will not place or a pipe that will not count, so it
  is an *upper bound* on what builds.  Measuring the gap is the point.

The asymmetry that matters, stated once: a model that says **binds** when the
builder disagrees wastes 21s, and a model that says **no bind** when the builder
would have succeeded silently deletes a candidate from the search.  The second
is the expensive error, so the sampler deliberately over-weights the boundary —
vectors one step either side of a level solution — where a conservative model is
most likely to be caught being wrong.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hz_core as H  # noqa: E402
import hz_geom as G  # noqa: E402


def sample(base: H.P, anchor: G.Anchor, n: int, seed=0):
    """Vectors the model claims to cover, half of them on the level boundary."""
    rng = random.Random(seed)
    out, seen = [], set()
    while len(out) < n:
        p = H.bump(base,
                   squash_band=rng.choice([0, 2, 3, 5, 6, 7, 8, 9, 10, 12, 14, 15]),
                   rom_touch_drop=rng.choice([0, 2, 4, 5, 6, 7, 8, 9, 10, 12, 14]),
                   rom_rows=rng.choice([117, 118, 119, 120, 121]),
                   store_dx=rng.choice([-2, -1, 0, 1, 2]),
                   store_cols=rng.choice([18, 19, 20]),
                   folded_lanes=rng.random() < 0.8,
                   tucked_drops=rng.random() < 0.8)
        if rng.random() < 0.5:                      # on the level solution
            p = G.repair_dy(anchor, p)
        else:                                        # one step either side of it
            p = H.bump(G.repair_dy(anchor, p),
                       store_dy=G.repair_dy(anchor, p).store_dy + rng.choice([-1, 1]))
        if p.key() in seen:
            continue
        seen.add(p.key())
        out.append(p)
    return out


def matrix(rows, a_name, b_name):
    tp = sum(1 for a, b in rows if a and b)
    fp = sum(1 for a, b in rows if a and not b)
    fn = sum(1 for a, b in rows if not a and b)
    tn = sum(1 for a, b in rows if not a and not b)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    print(f"\n  {a_name} vs {b_name}   (n={len(rows)})")
    print(f"                     {b_name}=yes  {b_name}=no")
    print(f"    {a_name}=yes  {tp:11d}  {fp:10d}")
    print(f"    {a_name}=no   {fn:11d}  {tn:10d}")
    print(f"    precision {prec:.3f}   recall {rec:.3f}   "
          f"accuracy {(tp + tn) / len(rows):.3f}")
    if fp:
        print(f"    !! {fp} false positives — the model promised a bind that was not there")
    if fn:
        print(f"    !! {fn} FALSE NEGATIVES — candidates the search would have deleted")
    return dict(tp=tp, fp=fp, fn=fn, tn=tn)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 24
    do_build = "--build" in sys.argv
    base = H.shipped()
    anchor = G.Anchor.take(base, wall=157, adapter=157)
    ps = sample(base, anchor, n)

    mc, cb, disagree = [], [], []
    for i, p in enumerate(ps, 1):
        pr = G.predict(anchor, p)
        c = H.capture(p)
        if pr is None:
            print(f"  [{i:3d}/{n}] abstain  {p.label(base)[:70]}", flush=True)
            continue
        mc.append((pr[0], c.binds))
        flag = "" if pr[0] == c.binds else "   <-- DISAGREE"
        if flag:
            disagree.append((p, pr, c))
        extra = ""
        if do_build:
            b = H.build(p)
            cb.append((c.binds, b.ok))
            extra = f" build={'ok' if b.ok else b.err[:44]}"
        print(f"  [{i:3d}/{n}] model={int(pr[0])} §7.1={int(c.binds)}{extra}  "
              f"{p.label(base)[:64]}{flag}", flush=True)

    matrix(mc, "model", "§7.1")
    if cb:
        matrix(cb, "§7.1", "build")
    for p, pr, c in disagree:
        print(f"\n  disagreement: {p.label(base)}")
        print(f"    model: {pr[0]} {pr[1][:120]}  pads={pr[2][:4]}")
        print(f"    §7.1 : {c.binds} {c.reason[:120]}  pads={c.good[:4]}")


if __name__ == "__main__":
    main()
