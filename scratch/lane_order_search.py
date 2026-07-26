#!/usr/bin/env python3
"""Search a program's lane order for a better score, and print it for ``LANE_ORDER``.

Why there is anything to search. A lane's *row* is a tick cost. At the end of its
micro-program the man walks east to his drop column, south to the collector row below
the lane band, west along it, then up the riser to the fetch cell — so ignoring the
riser, which every lane pays alike:

    walk(lane) = (drop_x - lane_end) + (collector - row) + (drop_x - 1)

and ``drop_x`` is the running suffix maximum of ``lane_end + 1`` over the rows at or
below this one, because a drop may only cross cells that are clear of glyphs (§2.9).
That coupling is the whole problem: a **hot** lane wants to sit low, since both the
``- row`` and the ``2 * drop_x`` terms improve at once, while a **long** lane wants to
sit high, because every lane above it pays for its extent. ``machine.plan``'s default
sorts by length descending — it gets the second force right and cannot see the first,
because length does not know how often an opcode runs.

So: weight each lane by its measured execution count and minimise the weighted walk.

Three stages, cheapest filter first, and the last two are not optional:

1. **model** — hill-climb on the formula above. Cheap, and it ranks candidates in the
   same order the engine does (verified on `brackets`).
2. **build** — assemble each candidate and keep only those whose footprint does not
   grow. The lane order picks ``mem_pad``, which sets the memory lanes' length, which
   sets the CPU's width, which is *squared* in the score. Skipping this stage finds
   "wins" that lose.
3. **engine** — verify and time the survivors. Ranking is not scoring.

**A public-case pass is not proof.** `matmul`'s best candidate passed all seven public
cases on the reference engine and computes the wrong product for an identity matrix.
Put a program's stress cases into `--extra-problem` before trusting a result for it.

Usage:
    python scratch/lane_order_search.py brackets
    python scratch/lane_order_search.py snake-ring --problem snake --seeds 3,19,41
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine as M  # noqa: E402
from randomfun2026solvers.lm1 import programs  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.machine import Band  # noqa: E402
from randomfun2026solvers.scoring import _rounds  # noqa: E402


# ── weights: how often each opcode actually runs ──────────────────────────────
def frequencies(slug: str, problem: str) -> collections.Counter[str]:
    """Executed count per mnemonic, summed over the problem's public cases.

    From the *emulator*, not a static count: a loop body runs as many times as the
    loop says, and that is the entire point of weighting by frequency.
    """
    prog = programs.load(slug)
    data = json.loads((REPO / "tasks" / "problems" / f"{problem}.json").read_text())
    total: collections.Counter[str] = collections.Counter()
    for case in data["publicTestData"]:
        rounds = [Round(input=tuple(int(t) for t in (r.get("in") or []))) for r in _rounds(case)]
        em = Emulator(prog)
        seen: collections.Counter[str] = collections.Counter()
        inner = em.step

        def step(_inner=inner, _seen=seen):
            op = _inner()
            if op is not None:
                _seen[op.mnemonic] += 1
            return op

        em.step = step  # type: ignore[method-assign]
        try:
            em.run(rounds, max_instructions=4_000_000)
        except Exception as exc:  # a case that faults still tells us about the rest
            print(f"  ({case.get('name')}: {type(exc).__name__})", file=sys.stderr)
        total += seen
    return total


# ── the model ─────────────────────────────────────────────────────────────────
def geometry(slug: str):
    """``lane_end`` per mnemonic — row-independent, which is what makes this cheap."""
    prog = programs.load(slug)
    p = M.plan(prog)
    lane_x0 = 5 + p.k
    flat = {m: M.hw_micro(p.sem[m]) for m in p.number if p.sem[m] in M._HW}
    prefixes = [
        next((i for i, (_, b) in enumerate(mc) if b == Band.MEM), len(mc))
        for mc in flat.values()
        if any(b == Band.MEM for _, b in mc)
    ]
    band_x = {Band.MEM: lane_x0 + (max(prefixes) if prefixes else 0) + M.build_for(slug).mem_pad}
    dsp = [b for b in M.DSP_BANDS if any(b == bb for mc in flat.values() for _, bb in mc)]
    band_x.update({b: lane_x0 + 1 + i * M._DSP_PITCH for i, b in enumerate(dsp)})
    stream = [b for b in M.STREAM_BANDS if any(b == bb for mc in flat.values() for _, bb in mc)]
    band_x.update({b: lane_x0 + 1 + i * M._STREAM_PITCH for i, b in enumerate(stream)})

    ends = {}
    for m in p.number:
        if m in flat:
            cells = M._flat_lane(flat[m], lane_x0, band_x, 0)
            ends[m] = max((x for x, _ in cells), default=lane_x0 - 1)
        else:
            ends[m] = lane_x0  # a structured lane is only its preamble
    structured = {m for m in p.number if p.sem[m] in M._JUMP_SEMS | M._BRANCH_SEMS}
    return prog, p, ends, structured, lane_x0


def walk(p, ends, structured, lane_x0, slots, freq) -> tuple[int, int]:
    """Weighted walked cells, and the widest drop column the layout needs."""
    n = len(slots)
    collector = 2 * n
    struct_east = M._STRUCT_X0 + max(1, len(structured)) * M._SLAB_PITCH
    drop: dict[int, int] = {}
    assigned: set[int] = set()
    floor = lane_x0
    total = 0
    for i in range(n - 1, -1, -1):
        m, row = slots[i], 2 * i + 1
        end = ends.get(m, lane_x0 - 1) if m else lane_x0 - 1
        floor = max(floor, end + 1)
        if m is None or p.sem[m] is M.Sem.HALT:
            continue
        if m in structured:
            col = max(floor, struct_east + 1)
            while col in assigned:
                col += 1
        else:
            col = floor
            if col > struct_east:
                col = struct_east + 1
                while col in assigned:
                    col += 1
        drop[row] = col
        assigned.add(col)
        # a structured lane drops past the collector into its slab, then rises back
        depth = (collector + 1 - row) if m in structured else (collector - row)
        total += freq.get(m, 0) * ((col - end) + depth + (col - 1))
    return total, (max(drop.values()) if drop else 0)


# ── search ────────────────────────────────────────────────────────────────────
def base_slots(p) -> list[str | None]:
    slots: list[str | None] = [None] * p.lanes
    for m, row in p.row.items():
        slots[(row - 1) // 2] = m
    return slots


def movable(p) -> list[int]:
    """Slots we may permute: unpinned *and* occupied.

    IN is pinned beside the north pipe, OUT beside the south one, the display and
    STREAM lanes beside the wall their pipes leave from — and an *empty* slot cannot
    move either, because ``plan`` always leaves the unused rows at the end of the
    group it fills.
    """
    slots = base_slots(p)
    pinned = set()
    for m in p.row:
        sem = p.sem[m]
        if sem is M.Sem.INPUT or sem is M.Sem.OUTPUT:
            pinned.add((p.row[m] - 1) // 2)
        elif sem in M.DSP_SEM_BAND or sem in M.STREAM_SEM_BAND:
            pinned.add((p.row[m] - 1) // 2)
    return [i for i in range(p.lanes) if i not in pinned and slots[i] is not None]


def build(slug, order):
    return M.build(
        programs.load(slug),
        tape_n=M.TAPE_SIZE[slug],
        rom_rows=M.ROM_ROWS.get(slug),
        display=M.display_for(slug),
        stream=M.STREAM_SIZE.get(slug),
        middle_order=order,
    )


def search(slug, problem, *, seed, candidates, builds, engine_checks, extra_problem=None):
    freq = frequencies(slug, problem)
    prog, p, ends, structured, lane_x0 = geometry(slug)
    slots0 = base_slots(p)
    free = movable(p)
    rng = random.Random(seed)

    def cost(slots):
        return walk(p, ends, structured, lane_x0, slots, freq)[0]

    baseline = build(slug, None)
    fp0 = baseline.footprint
    res0 = optimize.verify(baseline.rows, str(REPO / "tasks" / "problems" / f"{problem}.json"))
    print(f"{slug}: baseline fp={fp0} ticks={res0.avg_ticks:.0f} "
          f"score={fp0 * res0.avg_ticks:,.0f} walk={cost(slots0):,}")

    seen, cands = set(), []
    for restart in range(candidates):
        cur = list(slots0)
        if restart:
            vals = [cur[i] for i in free]
            rng.shuffle(vals)
            for i, v in zip(free, vals, strict=True):
                cur[i] = v
        c = cost(cur)
        for _ in range(300):
            i, j = rng.sample(free, 2)
            cur[i], cur[j] = cur[j], cur[i]
            c2 = cost(cur)
            if c2 <= c:
                c = c2
            else:
                cur[i], cur[j] = cur[j], cur[i]
        if (key := tuple(cur)) not in seen:
            seen.add(key)
            cands.append((c, list(cur)))
    cands.sort(key=lambda t: t[0])

    kept = []
    for c, slots in cands[:builds]:
        order = [s for i, s in enumerate(slots) if i in free and s is not None]
        try:
            m = build(slug, order)
        except M.MachineError:
            continue
        if m.footprint <= fp0:
            kept.append((c, m.footprint, order, m))
    print(f"  {len(kept)}/{min(builds, len(cands))} built candidates keep the footprint")

    best = None
    for c, fp, order, m in kept[:engine_checks]:
        res = optimize.verify(m.rows, str(REPO / "tasks" / "problems" / f"{problem}.json"))
        if not res.passed:
            print(f"  walk={c:,} fp={fp} FAILED public cases")
            continue
        if extra_problem:
            extra = optimize.verify(m.rows, extra_problem)
            if not extra.passed:
                print(f"  walk={c:,} fp={fp} passed public cases and FAILED the extra set")
                continue
        score = fp * res.avg_ticks
        print(f"  walk={c:,} fp={fp} ticks={res.avg_ticks:.0f} score={score:,.0f}")
        if best is None or score < best[0]:
            best = (score, order, fp, res.avg_ticks)
    if best and best[0] < fp0 * res0.avg_ticks:
        print(f"\nWIN {best[0] / (fp0 * res0.avg_ticks):.4f}x")
        print(f'    "{slug}": {tuple(best[1])!r},')
    else:
        print("\nno improvement; keep the default order")
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("slug", choices=sorted(M.TAPE_SIZE))
    ap.add_argument("--problem", help="problem slug, when it differs (snake-ring -> snake)")
    ap.add_argument("--seeds", default="11", help="comma-separated restart seeds")
    ap.add_argument("--candidates", type=int, default=250)
    ap.add_argument("--builds", type=int, default=70)
    ap.add_argument("--engine-checks", type=int, default=14)
    ap.add_argument(
        "--extra-problem",
        help="a second problem JSON every candidate must also pass; use this wherever "
        "publicTestData is thin (see matmul)",
    )
    args = ap.parse_args(argv)
    for seed in (int(s) for s in args.seeds.split(",")):
        search(
            args.slug,
            args.problem or args.slug,
            seed=seed,
            candidates=args.candidates,
            builds=args.builds,
            engine_checks=args.engine_checks,
            extra_problem=args.extra_problem,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
