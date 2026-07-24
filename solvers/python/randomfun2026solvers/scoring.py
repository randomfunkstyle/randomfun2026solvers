"""Score a ``.man`` program against a littleman problem.

Given a program and a problem (its JSON, or a slug that resolves to
``tasks/problems/<slug>.json``), compute the contest score:

* ``footprint-tick`` → ``max(width, height)² × avg ticks over all test cases``
* ``footprint``      → ``max(width, height)²`` (speed irrelevant)

See ``littleman/GRADING.md`` / ``littleman/DETAILS.md`` §5 for the rules.

Per the caller's contract we **assume every test case passes** — we do not
compare emitted output against the expected values. We only measure, for each
case, the tick at which the program has emitted as many output values as the
case expects (the tick of the final correct output, since output only grows).
That, squared-footprint, is the score.

Tick measurement uses the exact reference engine via :class:`Littleman`:

* ``run`` gives a chunk-granular upper bound on the settle tick.
* ``tick(n)`` steps *exactly* ``n`` ticks; output length is monotonic in ``n``,
  so a binary search finds the precise tick the final value was emitted.

Display problems (``plotter``/``palette``: rounds carry ``frames`` and emit no
program output) can't be measured this way — there is no output to count and
the CLI does not surface a committed-frame tick. For those we fall back to the
``run`` settle/halt tick and flag it ``approx`` on the result.

Footprint is read from the source grid the way ``lm.mjs`` reads it: drop a
single trailing newline, split on ``\\n``; ``width`` = longest row length,
``height`` = row count. (DETAILS §9: exact judge measurement is unconfirmed —
validate a local ``area2`` against a real submission before trusting it.)
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .littleman import Littleman, LittlemanError

__all__ = [
    "ScoringError",
    "CaseScore",
    "ProgramScore",
    "footprint",
    "load_problem",
    "score_program",
]

# The grading step cap (``tickCap: null`` on every current problem → default).
DEFAULT_TICK_CAP = 5_000_000

# Where slugs resolve: scoring.py -> randomfun2026solvers -> python -> solvers
#   -> <repo root> / tasks / problems / <slug>.json
_PROBLEMS_DIR = Path(__file__).resolve().parents[3] / "tasks" / "problems"


class ScoringError(RuntimeError):
    """Raised when a program cannot be scored (load fatal, too little output, timeout)."""


# ── footprint ────────────────────────────────────────────────────────────────
def _source_text(program: str | os.PathLike[str]) -> str:
    """Return the program's grid source (a file's text, or an inline string as-is)."""
    if isinstance(program, os.PathLike):
        return Path(os.fspath(program)).read_text(encoding="utf-8")
    # A str with no newline that names an existing file → read it; else inline source.
    if "\n" not in program and Path(program).is_file():
        return Path(program).read_text(encoding="utf-8")
    return program


def footprint(program: str | os.PathLike[str]) -> tuple[int, int, int]:
    """``(width, height, area2)`` for a program, mirroring ``lm.mjs`` row reading.

    Drops one trailing newline, splits on ``\\n``; width = longest row, height =
    row count, ``area2 = max(width, height) ** 2``.
    """
    text = _source_text(program)
    if text.endswith("\n"):
        text = text[:-1]
    rows = text.split("\n")
    width = max((len(r) for r in rows), default=0)
    height = len(rows)
    return width, height, max(width, height) ** 2


# ── problem / test-case handling ──────────────────────────────────────────────
def load_problem(problem: str | os.PathLike[str] | dict[str, Any]) -> dict[str, Any]:
    """Return a problem dict from a dict, a ``.json`` path, or a slug."""
    if isinstance(problem, dict):
        return problem
    p = Path(os.fspath(problem))
    if not p.is_file():
        # Treat as a slug.
        p = _PROBLEMS_DIR / f"{os.fspath(problem)}.json"
    if not p.is_file():
        raise ScoringError(f"problem not found: {problem!r}")
    return json.loads(p.read_text(encoding="utf-8"))


def _rounds(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a public-test case to a list of ``{in, out, frames}`` rounds."""
    if "rounds" in case:
        return case["rounds"]
    return [{"in": case.get("in", []), "out": case.get("out", [])}]


def _case_input(case: dict[str, Any]) -> str:
    """Whitespace input for the engine; rounds separated by ``/`` (the frame sep)."""
    parts = [" ".join(str(t) for t in r.get("in", []) or []) for r in _rounds(case)]
    return " / ".join(parts)


def _expected_output_count(case: dict[str, Any]) -> int:
    return sum(len(r.get("out", []) or []) for r in _rounds(case))


def _is_display_case(case: dict[str, Any]) -> bool:
    return any(r.get("frames") for r in _rounds(case))


# ── tick measurement ──────────────────────────────────────────────────────────
def _output_len_at(lm: Littleman, path: Path, n: int, inp: str, cache: dict[int, int]) -> int:
    if n not in cache:
        snap = lm.tick(path, n, input=inp)
        if snap.fatal is not None:
            raise ScoringError(f"fatal at tick {n}: {snap.fatal.reason}")
        cache[n] = len(snap.output)
    return cache[n]


def _ticks_for_case(
    lm: Littleman,
    path: Path,
    case: dict[str, Any],
    *,
    tick_cap: int,
) -> tuple[int, bool]:
    """Return ``(ticks, approx)`` = tick of final expected output for one case.

    ``approx`` is True for display cases (measured by run settle/halt, not a
    precise output tick).
    """
    inp = _case_input(case)

    if _is_display_case(case):
        # No program output to count, and the CLI exposes no committed-frame tick;
        # estimate with the run settle/halt tick (may time out → cap).
        try:
            snap = lm.run(path, input=inp, max_ticks=tick_cap)
        except LittlemanError:
            return tick_cap, True
        if snap.fatal is not None:
            raise ScoringError(f"fatal: {snap.fatal.reason}")
        return snap.step, True

    expected = _expected_output_count(case)
    if expected == 0:
        # Nothing to emit → the case is satisfied from the start.
        return 0, False

    # The program need not halt: a good solver often loops forever after emitting.
    # So find the upper bound by exponential search on the tick count (output length
    # is monotonic in ticks), not by running to settle/halt.
    cache: dict[int, int] = {0: 0}
    hi = 1
    while _output_len_at(lm, path, hi, inp, cache) < expected:
        if hi >= tick_cap:
            raise ScoringError(
                f"emitted {cache[hi]} of {expected} expected value(s) within "
                f"{tick_cap} ticks (does not pass — cannot score under assume-pass)"
            )
        hi = min(hi * 2, tick_cap)

    # Binary search the smallest tick with >= expected output values.
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _output_len_at(lm, path, mid, inp, cache) >= expected:
            hi = mid
        else:
            lo = mid + 1
    return lo, False


# ── result models ─────────────────────────────────────────────────────────────
@dataclass
class CaseScore:
    name: str
    ticks: int
    approx: bool = False


@dataclass
class ProgramScore:
    scoring: str
    width: int
    height: int
    area2: int
    cases: list[CaseScore] = field(default_factory=list)
    avg_ticks: float | None = None
    score: float | None = None

    @property
    def approx(self) -> bool:
        """True if any case's ticks were estimated (display problems)."""
        return any(c.approx for c in self.cases)

    def __str__(self) -> str:
        head = (
            f"{self.scoring}  w×h={self.width}×{self.height}  area2={self.area2}"
        )
        if self.avg_ticks is not None:
            head += f"  avg_ticks={self.avg_ticks:.2f}"
        head += f"  score={self.score}"
        if self.approx:
            head += "  (approx: display ticks estimated)"
        lines = [head]
        for c in self.cases:
            tag = " ~" if c.approx else ""
            lines.append(f"  {c.name}: {c.ticks} ticks{tag}")
        return "\n".join(lines)


# ── public API ────────────────────────────────────────────────────────────────
def score_program(
    program: str | os.PathLike[str],
    problem: str | os.PathLike[str] | dict[str, Any],
    *,
    lm: Littleman | None = None,
    tick_cap: int | None = None,
) -> ProgramScore:
    """Total contest score for ``program`` on ``problem`` (assuming all cases pass).

    ``program`` is a ``.man`` path or inline source; ``problem`` is a problem
    dict, a ``.json`` path, or a slug (resolved under ``tasks/problems/``).
    """
    prob = load_problem(problem)
    scoring = prob.get("scoring", "footprint-tick")
    cases = prob.get("publicTestData") or []
    cap = tick_cap if tick_cap is not None else (prob.get("tickCap") or DEFAULT_TICK_CAP)

    width, height, area2 = footprint(program)
    result = ProgramScore(scoring=scoring, width=width, height=height, area2=area2)

    if scoring == "footprint":
        result.score = float(area2)
        return result

    if not cases:
        raise ScoringError("no publicTestData to measure ticks against")

    lm = lm or Littleman()
    # Resolve the program to a single on-disk file so the engine reads it once
    # per invocation (avoids rewriting a temp file on every binary-search probe).
    path, cleanup = _to_path(program)
    try:
        total = 0
        for case in cases:
            ticks, approx = _ticks_for_case(lm, path, case, tick_cap=cap)
            result.cases.append(
                CaseScore(name=case.get("name", "?"), ticks=ticks, approx=approx)
            )
            total += ticks
    finally:
        if cleanup is not None:
            cleanup()

    result.avg_ticks = total / len(result.cases)
    result.score = area2 * result.avg_ticks
    return result


def _to_path(program: str | os.PathLike[str]) -> tuple[Path, Any]:
    """Return ``(path, cleanup)``. Inline source is written to a temp ``.man`` once."""
    if isinstance(program, os.PathLike):
        return Path(os.fspath(program)), None
    if isinstance(program, str) and "\n" not in program and Path(program).is_file():
        return Path(program), None
    import tempfile

    tmp = tempfile.NamedTemporaryFile("w", suffix=".man", delete=False, encoding="utf-8")
    try:
        tmp.write(program)
    finally:
        tmp.close()
    p = Path(tmp.name)
    return p, lambda: p.unlink(missing_ok=True)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="randomfun2026solvers.scoring",
        description="Score a .man program against a littleman problem (assumes all cases pass).",
    )
    parser.add_argument("program", help="path to a .man file (or inline source)")
    parser.add_argument("problem", help="problem slug, .json path, or file")
    parser.add_argument("--tick-cap", type=int, default=None, help="override the step cap")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args(argv)

    try:
        res = score_program(args.program, args.problem, tick_cap=args.tick_cap)
    except (ScoringError, LittlemanError) as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "scoring": res.scoring,
                    "width": res.width,
                    "height": res.height,
                    "area2": res.area2,
                    "avgTicks": res.avg_ticks,
                    "score": res.score,
                    "approx": res.approx,
                    "cases": [
                        {"name": c.name, "ticks": c.ticks, "approx": c.approx}
                        for c in res.cases
                    ],
                },
                indent=2,
            )
        )
    else:
        print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
