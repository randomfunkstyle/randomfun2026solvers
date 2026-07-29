#!/usr/bin/env python3
"""Re-run the shipped machine and every **declined** lever against it.

A decline is a fact about a machine, not about a lever, and this machine keeps
changing.  ``TAPED_CHAIN_REACH`` sat declined at -0.020% while being worth
-2.678%, because the bank cut that landed after it measured removed the exact
condition its decline rested on.  Nothing in the suite could have noticed: the
grid built, every pipe bound, every frame was byte-correct, and the machine was
simply slower than it needed to be.

So this is the thing to run **after anything lands** (``AGENTS.md`` §"Optimisation
work: the measurement is not separable", rule 4).  It is not a gate — declines are
allowed to stay declined — it is a tripwire for the ones that stopped being true.

    python scratch/deadman3d-opt/revalidate.py [rounds] [lever ...]

Three rounds (~30s a variant) is enough to see a sign change; confirm anything
interesting at 21.  Exit status is 0 whatever the numbers say — read the table.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")

#: Every lever measured on this family and NOT shipped, with where its decline is
#: recorded.  Add a row here whenever something is declined — a decline that is
#: not in this table is a decline nobody will ever re-check.
#:
#: ``kind`` is how the lever is applied:
#:   ``set``      — add ``KEY`` to a ``set[tuple[str, str]]`` registry
#:   ``dict``     — set ``registry[SLUG] = value``
#:   ``prog``     — a ``deadman3d_source`` keyword
DECLINED: dict[str, dict] = {
    # -0.020% pre-cut, -2.678% post-cut -> SHIPPED in a15b200.  Kept here as the
    # worked example of why this script exists; it should now read ~0.000%.
    "chain_reach(shipped)": {"kind": "set", "reg": "TAPED_CHAIN_REACH"},
    # +3.54% pre-a15b200, +0.185% after.  The margin fell an order of magnitude
    # from CPU-side work alone; this is the row most likely to flip next.
    "skip_batch=2": {"kind": "dict", "reg": "TAPED_SKIP_BATCH", "value": 2},
    "skip_batch=4": {"kind": "dict", "reg": "TAPED_SKIP_BATCH", "value": 4},
    # +0.006% at first measurement, +0.036% after the bank cut.  Worth -4.47% on
    # the 64x48 machine and never anything here.
    "lap_via_jump": {"kind": "prog", "key": "lap_via_jump"},
    # Structural, not numeric: it and STORE_REQUEST_REACH are two answers to one
    # question and `build` refuses the pair.  Expected to BUILD FAILED — that is
    # the correct result, and it is here so the day it stops failing is visible.
    "req_teleport": {"kind": "set", "reg": "STORE_REQUEST_TELEPORT"},
}


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    n = int(argv[0]) if argv and argv[0].isdigit() else 3
    names = [a for a in argv if not a.isdigit()] or list(DECLINED)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config as cfg

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]

    #: The knobs `hires_source()` passes; not registries, so they are stated.
    KNOBS = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True,
                 lap_via_jump=False)
    log = Path(__file__).resolve().parent / "measurements.jsonl"
    shipped_f = cfg.feature_set(SLUG, "taped", **KNOBS)
    print(f"re-validating {len(names)} declines against config "
          f"{cfg.digest(shipped_f)} ({len(shipped_f)} features), "
          f"{len(rounds)} rounds", flush=True)

    def run(label: str, spec: dict | None) -> int | None:
        """Build and gate one variant, leaving every registry as it was found."""
        saved: list[tuple] = []
        prog_kw: dict = {}
        if spec:
            if spec["kind"] == "set":
                reg = getattr(M, spec["reg"])
                saved.append((reg, KEY in reg))
                reg.add(KEY)
            elif spec["kind"] == "dict":
                reg = getattr(M, spec["reg"])
                saved.append((reg, reg.get(SLUG, KeyError)))
                reg[SLUG] = spec["value"]
            else:
                prog_kw[spec["key"]] = True
        try:
            knobs = {**KNOBS, **prog_kw}
            # captured with the registries mutated, so it describes THIS variant
            feats = cfg.feature_set(SLUG, "taped", **knobs)
            src = d3.deadman3d_source(d3.GEOM128, **knobs)
            program = assemble(src, name=SLUG)
            t0 = time.time()
            try:
                m = M.build_for(SLUG, program=program, store="taped")
            except Exception as exc:  # noqa: BLE001 — a refusal can be the right answer
                cfg.record(log, label, feats, rounds=n, outcome="build-failed",
                           error=type(exc).__name__)
                print(f"  {label:>22}: BUILD FAILED — {type(exc).__name__} "
                      f"[{cfg.digest(feats)}]", flush=True)
                return None
            res = FastLittleman("\n".join(m.rows)).run(
                inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
            if res.fatal or res.passed is not True:
                cfg.record(log, label, feats, rounds=n, outcome="run-failed")
                print(f"  {label:>22}: RUN FAILED — fatal={res.fatal} "
                      f"passed={res.passed}", flush=True)
                return None
            walk = res.frame_ticks[-1] - res.frame_ticks[0]
            cfg.record(log, label, feats, rounds=n, outcome="ok", ticks=walk,
                       width=m.width, height=m.height)
            return walk, m, time.time() - t0, feats
        finally:
            for reg, old in saved:
                if isinstance(reg, set):
                    reg.add(KEY) if old else reg.discard(KEY)
                elif old is KeyError:
                    reg.pop(SLUG, None)
                else:
                    reg[SLUG] = old

    got = run("shipped", None)
    if not got:
        print("  shipped machine does not build or gate — fix that first")
        return 1
    base, m, dt, base_f = got
    print(f"  {'shipped':>22}: {m.width}x{m.height} walk={base:,}  ({dt:.0f}s)",
          flush=True)

    flipped = []
    for name in names:
        got = run(name, DECLINED[name])
        if not got:
            continue
        walk, m, dt, feats = got
        d = 100.0 * (walk - base) / base
        flag = ""
        if d < -0.5:
            flag, _ = "  <<< NOW A WIN", flipped.append((name, d, feats))
        print(f"  {name:>22}: {m.width}x{m.height} walk={walk:,}  "
              f"{walk - base:+,} = {d:+.3f}%{flag}  ({dt:.0f}s)", flush=True)

    if flipped:
        print("\nA decline stopped being true — confirm at 21 rounds before shipping:")
        for name, d, feats in flipped:
            # the whole point of the digest: say what is different, not just that
            # something is
            changed = ", ".join(k for k, _was, _now in cfg.diff(base_f, feats))
            print(f"  {name}  {d:+.3f}%   (adds: {changed})")
    else:
        print("\nEvery decline still holds.")
    print(f"\n{len(names) + 1} measurements appended to {log.name}, "
          f"stamped against config {cfg.digest(base_f)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
