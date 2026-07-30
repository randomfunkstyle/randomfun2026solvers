"""The ladder: take a list of candidates down through build, load, screen, gate.

    hz_run.py <spec> [spec ...] [--rounds N] [--screen N]

A spec is comma-separated ``field=value`` against the shipped vector, e.g.
``squash_band=12,store_dy=5``.  ``dy=auto`` asks :func:`hz_geom.repair_dy` for
the ``store_dy`` that makes the vector level, which is the whole point of the
model: a candidate that used to be rejected is now repaired and measured.

Every rung is cheaper than the one below it and rejects for a different reason,
which is the lesson of the frequency-shaping search — four of its nine *bound*
candidates then died at load with a literal that did not fit signed 64 bits, so
binding is necessary and nowhere near sufficient.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hz_core as H  # noqa: E402
import hz_geom as G  # noqa: E402

BASE_TICKS = 111_492_961  # 52dbadf men-v3, 21 rounds, re-measured


def parse(spec: str, base: H.P, anchor: G.Anchor) -> H.P:
    """``field=value`` against the shipped vector; ``dy=auto`` asks for the repair.

    ``lane_order`` takes ``|``-separated mnemonics and ``opcode_slots`` takes
    ``MNEMONIC:slot`` pairs, both of which contain no commas, so the two survive
    the comma split that separates fields.
    """
    auto = False
    kw = {}
    for part in spec.split(","):
        if not part:
            continue
        k, eq, v = part.partition("=")
        if not eq:
            continue  # a bare ``MNEMONIC:slot``, collected below
        if k in ("dy", "store_dy") and v == "auto":
            auto = True
            continue
        if k == "lane_order":
            # The registry takes the **unpinned** lanes only: ``plan`` pins ``IN``
            # to the top and the display lane to the bottom, so naming them is
            # rejected outright.  The trie model permutes the full north-to-south
            # list, so the spec is projected here rather than at the model.
            kw[k] = tuple(m for m in v.split("|") if m not in ("IN", "SND"))
            continue
        if k == "opcode_slots":
            kw.setdefault(k, [])
            continue
        f = {"dy": "store_dy", "dx": "store_dx", "k": "squash_band",
             "drop": "rom_touch_drop", "rom": "rom_rows"}.get(k, k)
        cur = getattr(base, f)
        kw[f] = (v == "1" or v.lower() == "true") if isinstance(cur, bool) else \
            (int(v) if v.lstrip("-").isdigit() else v)
    if "opcode_slots" in kw:
        # every ``M:slot`` token in the whole spec, in the order written
        pairs = [t for t in spec.replace("=", ",").split(",") if ":" in t]
        kw["opcode_slots"] = tuple((m, int(s)) for m, s in (t.split(":") for t in pairs))
    p = H.bump(base, **kw)
    if not auto:
        return p
    q, n = G.repair_measured(anchor, p)
    if q.store_dy != G.repair_dy(anchor, p).store_dy:
        print(f"    repair: model said dy={G.repair_dy(anchor, p).store_dy}, "
              f"builder says dy={q.store_dy} ({n} captures)", flush=True)
    return q


def rung(p: H.P, base: H.P, anchor: G.Anchor, screen=3, rounds=21, cap=None):
    tag = p.label(base) or "shipped"
    pr = G.predict(anchor, p)
    if pr is not None and not pr[0]:
        print(f"  {tag:52.52s} MODEL: no bind ({pr[1][:70]})", flush=True)
        return None
    b = H.build(p)
    if not b.ok:
        print(f"  {tag:52.52s} BUILD: {b.err[:90]} ({b.secs:.0f}s)", flush=True)
        return None
    ok, err = H.loads(b.rows)
    if not ok:
        print(f"  {tag:52.52s} LOAD: {err[:90]}", flush=True)
        return None
    if pr is not None:
        agree = "model=bind" if pr[0] else "MODEL WRONG (said no-bind)"
    else:
        agree = "model=abstain"
    s = H.gate(b.rows, screen)
    if not s.passed or s.fatal is not None or s.err:
        print(f"  {tag:52.52s} SCREEN{screen}: fatal={s.fatal} passed={s.passed} "
              f"{s.err or ''} ({s.secs:.0f}s)", flush=True)
        return None
    print(f"  {tag:52.52s} {b.box[0]}x{b.box[1]}  screen{screen}={s.ticks:,}  "
          f"{agree}  ({b.secs + s.secs:.0f}s)", flush=True)
    return dict(p=p, box=b.box, rows=b.rows, screen=s.ticks, routes=b.routes)


def main():
    argv = list(sys.argv[1:])
    rounds, screen = 21, 3
    for flag, name in (("--rounds", "rounds"), ("--screen", "screen")):
        if flag in argv:
            i = argv.index(flag)
            v = int(argv[i + 1]); del argv[i:i + 2]
            rounds, screen = (v, screen) if name == "rounds" else (rounds, v)
    base = H.shipped()
    anchor = G.Anchor.take(base, wall=157, adapter=157)
    print(f"anchor: {len(anchor.pads)} pads, good at "
          f"{[p for p in sorted(anchor.pads) if H.verdict(*anchor.pads[p])[0]][:3]}...",
          flush=True)
    print(f"screening at {screen} rounds, gating at {rounds}\n", flush=True)

    ref = rung(base, base, anchor, screen, rounds)
    best = []
    for spec in argv:
        r = rung(parse(spec, base, anchor), base, anchor, screen, rounds)
        if r and (ref is None or r["screen"] < ref["screen"]):
            best.append(r)
    if ref:
        print(f"\nreference screen{screen} = {ref['screen']:,}", flush=True)
    best.sort(key=lambda r: r["screen"])
    for r in best[:3]:
        d = 100 * (r["screen"] - ref["screen"]) / ref["screen"] if ref else 0
        print(f"\n=== gating {r['p'].label(base)} (screen {d:+.3f}%) ===", flush=True)
        g = H.gate(r["rows"], rounds)
        print(f"  {rounds} rounds: ticks={g.ticks:,} passed={g.passed} "
              f"fatal={g.fatal} box={r['box'][0]}x{r['box'][1]} "
              f"vs base {BASE_TICKS:,} = "
              f"{100 * (g.ticks - BASE_TICKS) / BASE_TICKS:+.3f}%  ({g.secs:.0f}s)",
              flush=True)


if __name__ == "__main__":
    main()
