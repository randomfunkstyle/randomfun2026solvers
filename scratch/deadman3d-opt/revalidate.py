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

The metric is **ticks until the last frame is on the wall** — the whole run,
boot and title included.  That is the ungameable one: the older "walk" figure
(frame 1 to frame N) can be improved by moving work into boot, where nothing in
the gates would see it.  Both are written to ``measurements.jsonl``, because every
number recorded before this change is a walk and they differ by boot's ~7.8%.

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
    # SHIPPED at -1.567% once the seek drum landed.  Declined three times before
    # that (-27.29% but beaten outright by the cut, then +3.54%, then +0.185%).
    # Batching pays in proportion to the store's share of the run, and that share
    # has now gone down and back up twice.  Reads ~0.000% while shipped.
    "skip_batch=2(shipped)": {"kind": "dict", "reg": "TAPED_SKIP_BATCH", "value": 2},
    "skip_batch=4": {"kind": "dict", "reg": "TAPED_SKIP_BATCH", "value": 4},
    # SHIPPED at -18.503% once the seek drum landed.  Declined twice before that
    # (+0.006%, then +0.036%) and both readings were correct: it converts a
    # backward-branch lap into a forward JMPF, and SEEK_OPS seeks JMPF and
    # nothing else, so without a drum the rewrite does nothing at all.  Kept here
    # reading ~0.000% as the second worked example — this one went from declined
    # twice to the largest single program lever this family has.
    "lap_via_jump(shipped)": {"kind": "prog", "key": "lap_via_jump"},
    # Structural, not numeric: it and STORE_REQUEST_REACH are two answers to one
    # question and `build` refuses the pair.  Expected to BUILD FAILED — that is
    # the correct result, and it is here so the day it stops failing is visible.
    "req_teleport": {"kind": "set", "reg": "STORE_REQUEST_TELEPORT"},
    # SHIPPED again, at -7.326%, once ROM_TOUCH_DROP removed the constraint that
    # made it unaffordable — reads ~0.000% while shipped.  Its full history:
    # -4.351% shipped, +0.260% withdrawn under the seek drum, -7.326% recovered.
    # Nothing about the lever changed at any point; the machine under it did.
    # Original withdrawal note follows.
    # Shipped at -4.351%, then withdrawn at +0.260% when the seek drum landed:
    # pitch 1 breaks the memory-response binding and drives the pad floor 15->28,
    # and the pad costs more than the stagger saves.  The single best argument
    # for this whole script — a lever can go from "shipped, worth 4%" to
    # "declined" without anybody touching it.  Alone it will BUILD FAILED at the
    # shipped pad; that is the correct result and the day it stops is the day to
    # look again.
    "lane_pitch=1(shipped)": {"kind": "dict2", "reg": "LANE_PITCH", "value": 1},
    # the knob that recovered it; reads ~0.000% while shipped
    "rom_drop=0": {"kind": "dict2", "reg": "ROM_TOUCH_DROP", "value": 0},
    # SHIPPED by direction, as a ticks-for-rows trade: -0.243% on its own but it
    # forces SEEK_TELEPORT off, and that costs +1.534%.  Reads ~0.000% while
    # shipped.  The row that matters now is `seek_teleport` below: if room H is
    # ever rehoused so the pair can coexist, this trade becomes a pure win.
    "squash_band(shipped)": {"kind": "setmember", "reg": "SQUASH_BAND"},
    # What the squash cost us.  BUILD FAILS while SQUASH_BAND is on — room H has
    # no full-width clear strip — and the day it stops failing is the day to take
    # back +1.534%.  This is the most valuable row in the table.
    "seek_teleport": {"kind": "setmember", "reg": "SEEK_TELEPORT"},
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
                 lap_via_jump=True)
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
            if spec["kind"] == "setmember":
                reg = getattr(M, spec["reg"])
                saved.append((reg, None, KEY in reg))
                reg.add(KEY)
            elif spec["kind"] == "set":
                reg = getattr(M, spec["reg"])
                saved.append((reg, None, KEY in reg))
                reg.add(KEY)
            elif spec["kind"] == "dict":
                reg = getattr(M, spec["reg"])
                saved.append((reg, SLUG, reg.get(SLUG, KeyError)))
                reg[SLUG] = spec["value"]
            elif spec["kind"] == "dict2":  # keyed by (slug, tier)
                reg = getattr(M, spec["reg"])
                saved.append((reg, KEY, reg.get(KEY, KeyError)))
                reg[KEY] = spec["value"]
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
            ft = list(res.frame_ticks)
            # THE metric: ticks until frame N is on the wall — the whole run,
            # boot and title included.  The walk (`ft[-1] - ft[0]`) is what every
            # older number in this repo is, so it is kept beside it rather than
            # replaced: the two differ by boot's ~7.8% and silently switching
            # units would make every recorded figure incomparable.
            #
            # Total is the honest one because it cannot be gamed.  The walk can:
            # move work into boot — precompute a table, unroll differently — and
            # per-frame time falls while total work rises, with nothing in the
            # gates to notice.
            total, walk = ft[-1], ft[-1] - ft[0]
            per = [ft[i + 1] - ft[i] for i in range(len(ft) - 1)]
            cfg.record(log, label, feats, rounds=n, outcome="ok",
                       ticks_to_last_frame=total, walk=walk, boot=ft[0],
                       mean_frame=sum(per) // len(per) if per else None,
                       width=m.width, height=m.height)
            return total, m, time.time() - t0, feats
        finally:
            for reg, key, old in saved:
                if isinstance(reg, set):
                    reg.add(KEY) if old else reg.discard(KEY)
                elif old is KeyError:
                    reg.pop(key, None)
                else:
                    reg[key] = old

    got = run("shipped", None)
    if not got:
        print("  shipped machine does not build or gate — fix that first")
        return 1
    base, m, dt, base_f = got
    print(f"  {'shipped':>22}: {m.width}x{m.height} ticks={base:,}  ({dt:.0f}s)",
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
        print(f"  {name:>22}: {m.width}x{m.height} ticks={walk:,}  "
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
