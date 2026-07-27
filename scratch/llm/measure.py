#!/usr/bin/env python3
"""Build the LLM machine with the given knobs, verify it in parallel, print the score.

    uv run python scratch/llm/measure.py [--rom-rows N] [--rom-buffer N] [...]

`optimize.verify` runs the 14 public cases serially and this machine costs ~145s
that way; the cases are independent, so one process each takes it to ~20s.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_GRID: str | None = None
_CASES: list | None = None


def _init(grid: str) -> None:
    global _GRID, _CASES
    from randomfun2026solvers import optimize

    _GRID = grid
    prob = optimize.load_problem("little-little-man")
    _CASES = prob.get("publicTestData") or []


def _run_case(i: int) -> tuple[str, bool, int, str]:
    from randomfun2026solvers import optimize, scoring
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = _CASES[i]
    name = case.get("name", "?")
    inp = scoring._case_input(case)
    fast = FastLittleman(_GRID)
    result = fast.run(inp, frames=optimize._expected_frames(case), max_ticks=50_000_000)
    if result.fatal is not None:
        return name, False, result.step, f"fatal: {result.fatal}"
    ok = result.passed is True and not result.output
    return name, ok, result.step, "" if ok else f"output {result.output}"


def verify_parallel(grid: str) -> tuple[bool, float, int, list]:
    from randomfun2026solvers import optimize

    prob = optimize.load_problem("little-little-man")
    n = len(prob.get("publicTestData") or [])
    with ProcessPoolExecutor(max_workers=14, initializer=_init, initargs=(grid,)) as ex:
        out = list(ex.map(_run_case, range(n)))
    ok = all(r[1] for r in out)
    avg = sum(r[2] for r in out) / len(out)
    worst = max(r[2] for r in out)
    return ok, avg, worst, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom-rows", type=int, default=None)
    ap.add_argument("--rom-buffer", type=int, default=None)
    ap.add_argument("--hot", type=str, default=None, help="cols,rows")
    ap.add_argument("--skip", type=int, default=None)
    ap.add_argument("--relay", type=str, default=None, help="cols,rows")
    ap.add_argument("--man", type=Path, default=None)
    ap.add_argument("--grid", type=Path, default=None, help="verify this grid instead")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)

    t0 = time.time()
    if args.grid:
        text = args.grid.read_text()
        rows = text.rstrip("\n").split("\n")
        P = 0
    else:
        from randomfun2026solvers import llm_lm1

        kw = {}
        if args.rom_rows is not None:
            kw["rom_rows"] = args.rom_rows
        if args.rom_buffer is not None:
            kw["rom_buffer"] = args.rom_buffer
        if args.hot:
            kw["hot"] = tuple(int(x) for x in args.hot.split(","))
        if args.skip is not None:
            kw["tape_skip_batch"] = args.skip
        if args.relay:
            kw["tape_relay_size"] = tuple(int(x) for x in args.relay.split(","))
        built, program, _text = llm_lm1.build_machine(**kw)
        rows = built.rows
        P = program.P
        text = "\n".join(rows) + "\n"

    w, h = max(len(r) for r in rows), len(rows)
    area2 = max(w, h) ** 2
    if args.man:
        args.man.write_text(text)
    if args.no_verify:
        print(f"{args.tag} {w}x{h} area2={area2} P={P} build={time.time() - t0:.1f}s")
        return 0

    ok, avg, worst, out = verify_parallel(text)
    print(
        f"{args.tag} {w}x{h} area2={area2} P={P} avg={avg:,.0f} worst={worst:,.0f} "
        f"score={area2 * avg:,.0f} {'PASS' if ok else 'FAIL'} [{time.time() - t0:.0f}s]"
    )
    for name, cok, step, detail in out:
        if not cok:
            print("  fail:", name, step, detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
