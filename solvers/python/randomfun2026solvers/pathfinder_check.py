"""Verify and measure a display-judged ``.man`` against a problem's public cases.

Written for ``pathfinder`` (16x16 LM-75 panel, ``footprint-tick``, tickCap
15,000,000) but the module is problem-agnostic: it works on any problem whose
rounds carry ``frames`` — ``plotter``, ``palette``, ``snake``, ``little-little-man``.

What it answers, per public case:

* did **every** committed frame equal the next expected frame, in order
  (that is the judge's streaming compare, SPEC.md);
* how many frames matched before the first bad one, and which index that was;
* at which tick the **final** expected frame was committed — the number the
  contest scores (`GRADING.md`: "until your final frame matches");
* whether the program wrote to program output at all, which is an error on a
  display problem.

Rounds and withheld input
-------------------------
A test case is a list of rounds and *round N+1's input is not available until
round N's output has been received* (GRADING.md § Rounds); for a display problem
the committed frames are what unlocks the next round. This module never feeds a
case's input as one flat stream. It hands the expected frames to the engine
**nested per round** — ``[[rows...], ...]`` per round, the shape
``rounds[i]["frames"]`` already has — and both engines then do the gating
themselves:

* ``FastLittleman`` → the native runner seeds only round 0's input and releases
  round ``r+1`` when the cumulative matched-frame count reaches round ``r``'s
  total (``fast_littleman_native.cpp``: ``release_satisfied``);
* ``Littleman.judge`` → the same rule inside the Go/WASM reference engine
  (``--frames``), which is the engine the contest judge is built from.

A round that expects no frames therefore unlocks the next round immediately,
exactly as the rules say. ``gated=False`` is available for debugging only: it
collapses the case to a single round so all input is released up front. Use it
to find out whether a stall is a gating problem or a logic problem — it is *not*
what the judge does, so never trust a ``gated=False`` pass.

Backends
--------
``backend="fast"`` (default, or ``LM_VALIDATOR``) runs the in-process native
engine: no Node, milliseconds per case. ``backend="reference"`` runs the
Node/WASM oracle via ``Littleman.judge``. They have been checked to give
identical verdicts, identical frame-mismatch indices and identical tick counts
(see ``tests/test_pathfinder_check.py`` and AGENTS.md).

The two differ in one respect: the reference engine reports its matched count and
first mismatch directly (``Snapshot.frame_judge``), while the native runner only
reports pass/fail. For the fast backend the matched count is recovered by binary
search over the expected-frame prefix — running with only the first ``k`` frames
expected is behaviourally identical to the full run up to frame ``k``, because
the run stops the moment the ``k``-th frame matches and nothing an engine does
after that can affect frames already committed.

CLI::

    uv run python -m randomfun2026solvers.pathfinder_check grid.man
    uv run python -m randomfun2026solvers.pathfinder_check grid.man -p plotter --backend reference
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import optimize, scoring
from .fast_littleman import FastLittleman, FastLittlemanError
from .littleman import DisplayRun, Littleman, LittlemanError

__all__ = [
    "DEFAULT_PROBLEM",
    "BACKENDS",
    "CaseResult",
    "CheckResult",
    "Runner",
    "resolve_backend",
    "expected_frames",
    "flat_frames",
    "case_input",
    "truncate_frames",
    "run_case",
    "check_all",
    "committed_frames",
    "main",
]

DEFAULT_PROBLEM = "pathfinder"
BACKENDS = ("fast", "reference")


# ── case helpers (pure — no engine) ───────────────────────────────────────────
def resolve_backend(backend: str | None = None) -> str:
    """``"fast"`` or ``"reference"``; ``None`` reads ``LM_VALIDATOR`` (default fast)."""
    name = (backend or os.environ.get("LM_VALIDATOR") or "fast").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; expected one of {BACKENDS}")
    return name


def expected_frames(case: dict[str, Any]) -> list[list[list[str]]]:
    """The case's expected frames **nested per round** — the engines' gating shape."""
    return optimize._expected_frames(case)


def flat_frames(case: dict[str, Any]) -> list[list[str]]:
    """Every expected frame of a case, in commit order, rounds concatenated."""
    return [frame for round_frames in expected_frames(case) for frame in round_frames]


def case_input(case: dict[str, Any], *, gated: bool = True) -> str:
    """Whitespace input for the engine.

    ``gated`` (the judge's model) separates rounds with ``/`` so the engine can
    withhold each round until the previous one's frames are committed. Ungated
    collapses everything into one round, releasing all input at tick 0.
    """
    if gated:
        return scoring._case_input(case)
    return " ".join(
        str(token) for r in scoring._rounds(case) for token in (r.get("in") or [])
    )


def truncate_frames(
    per_round: Sequence[Sequence[Sequence[str]]], keep: int
) -> list[list[list[str]]]:
    """The first ``keep`` frames of ``per_round``, with the round nesting preserved."""
    out: list[list[list[str]]] = []
    taken = 0
    for round_frames in per_round:
        take = max(0, min(len(round_frames), keep - taken))
        out.append([list(f) for f in round_frames[:take]])
        taken += take
    return out


# ── results ───────────────────────────────────────────────────────────────────
@dataclass
class CaseResult:
    """One public case replayed. ``ticks`` is the final-frame tick when passing."""

    name: str
    passed: bool
    frames_expected: int
    frames_matched: int | None
    first_mismatch_index: int | None
    ticks: int
    error: str | None = None
    backend: str = "fast"
    rounds: int = 0

    def as_tuple(self) -> tuple[bool, int | None, int | None, int, str | None]:
        """``(passed, n_frames_matched, first_mismatch_index, ticks, error)``."""
        return (
            self.passed,
            self.frames_matched,
            self.first_mismatch_index,
            self.ticks,
            self.error,
        )


@dataclass
class CheckResult:
    """Every public case of a problem, plus the footprint/score footer."""

    problem: str
    backend: str
    tick_cap: int
    width: int
    height: int
    area2: int
    cases: list[CaseResult] = field(default_factory=list)
    avg_ticks: float | None = None
    score: float | None = None
    scoring_mode: str = "footprint-tick"

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(c.passed for c in self.cases)

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    def table(self) -> str:
        """The per-case table plus the footer, as printed by the CLI."""
        width = max([len(c.name) for c in self.cases] + [4])
        head = f"{'case'.ljust(width)}  verdict  frames        ticks  error"
        lines = [head, "-" * len(head)]
        for c in self.cases:
            matched = "?" if c.frames_matched is None else str(c.frames_matched)
            frames = f"{matched}/{c.frames_expected}"
            err = c.error or ""
            if c.first_mismatch_index is not None:
                err = f"frame #{c.first_mismatch_index} differs; {err}".rstrip("; ")
            lines.append(
                f"{c.name.ljust(width)}  {'PASS' if c.passed else 'FAIL':^7}  "
                f"{frames:<9}{c.ticks:>9}  {err}"
            )
        lines.append("-" * len(head))
        lines.append(
            f"{self.n_passed}/{len(self.cases)} cases passed   "
            f"backend={self.backend}  tickCap={self.tick_cap:,}"
        )
        lines.append(
            f"w x h = {self.width} x {self.height}   area2 = max(w,h)^2 = {self.area2}"
        )
        avg = "n/a" if self.avg_ticks is None else f"{self.avg_ticks:,.2f}"
        score = "n/a (not every case passes)" if self.score is None else f"{self.score:,.0f}"
        lines.append(f"avg_ticks = {avg}")
        lines.append(f"score = area2 x avg_ticks = {score}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "backend": self.backend,
            "tickCap": self.tick_cap,
            "width": self.width,
            "height": self.height,
            "area2": self.area2,
            "scoring": self.scoring_mode,
            "passed": self.passed,
            "avgTicks": self.avg_ticks,
            "score": self.score,
            "cases": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "framesExpected": c.frames_expected,
                    "framesMatched": c.frames_matched,
                    "firstMismatchIndex": c.first_mismatch_index,
                    "ticks": c.ticks,
                    "error": c.error,
                }
                for c in self.cases
            ],
        }


# ── program handling ──────────────────────────────────────────────────────────
def _source_text(program: str | os.PathLike[str] | Sequence[str]) -> str:
    if isinstance(program, os.PathLike):
        return Path(os.fspath(program)).read_text(encoding="utf-8")
    if isinstance(program, str):
        if "\n" not in program and Path(program).is_file():
            return Path(program).read_text(encoding="utf-8")
        return program
    return "\n".join(str(row) for row in program)


def _program_path(program: str | os.PathLike[str] | Sequence[str]) -> tuple[Path, Any]:
    """``(path, cleanup)``. ``Littleman`` treats a plain ``str`` as inline source,
    so a real file is always materialised here."""
    if isinstance(program, os.PathLike):
        return Path(os.fspath(program)), None
    if isinstance(program, str) and "\n" not in program and Path(program).is_file():
        return Path(program), None
    import tempfile

    tmp = tempfile.NamedTemporaryFile("w", suffix=".man", delete=False, encoding="utf-8")
    try:
        tmp.write(_source_text(program))
    finally:
        tmp.close()
    path = Path(tmp.name)
    return path, lambda: path.unlink(missing_ok=True)


# ── the runner ────────────────────────────────────────────────────────────────
class Runner:
    """A program bound to one backend, reusable across every case of a problem.

    The fast backend parses the grid once here; the reference backend keeps a
    real file on disk for the Node CLI. Use as a context manager (or call
    :meth:`close`) so an inline-source temp file is removed.
    """

    def __init__(
        self,
        program: str | os.PathLike[str] | Sequence[str],
        *,
        backend: str | None = None,
        engine: Any | None = None,
    ) -> None:
        self.backend = resolve_backend(backend)
        self.source = _source_text(program)
        self.load_error: str | None = None
        self._path: Path | None = None
        self._cleanup: Any = None
        self._engine = engine
        if engine is not None:
            return
        if self.backend == "fast":
            try:
                self._engine = FastLittleman(self.source)
            except FastLittlemanError as exc:
                self.load_error = f"load: {exc}"
        else:
            self._path, self._cleanup = _program_path(program)
            self._engine = Littleman()

    # lifecycle -----------------------------------------------------------------
    def close(self) -> None:
        if self._cleanup is not None:
            self._cleanup()
            self._cleanup = None

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # measuring -----------------------------------------------------------------
    def run_case(
        self,
        case: dict[str, Any],
        *,
        cap: int = scoring.DEFAULT_TICK_CAP,
        gated: bool = True,
        locate: bool = True,
    ) -> CaseResult:
        """Replay one public case and judge every committed frame.

        ``locate`` recovers the matched-frame count on the fast backend when the
        run fails. A mismatch is cheap to locate (each probe stops at the bad
        commit); a *stalled* program is not — each probe then burns the full
        ``cap`` — so pass ``locate=False`` to skip it and get ``None``.
        """
        name = str(case.get("name", "?"))
        per_round = expected_frames(case)
        total = sum(len(r) for r in per_round)
        base = dict(
            name=name,
            frames_expected=total,
            backend=self.backend,
            rounds=len(scoring._rounds(case)),
        )
        if self.load_error is not None:
            return CaseResult(passed=False, frames_matched=None, first_mismatch_index=None,
                              ticks=0, error=self.load_error, **base)
        if total == 0:
            return CaseResult(passed=False, frames_matched=0, first_mismatch_index=None,
                              ticks=0, error="case expects no frames — not a display case",
                              **base)
        if not gated:
            per_round = [[frame for r in per_round for frame in r]]
        inp = case_input(case, gated=gated)

        if self.backend == "fast":
            return self._run_fast(case, per_round, inp, total, base, cap=cap, locate=locate)
        return self._run_reference(per_round, inp, total, base, cap=cap)

    # fast (in-process native engine) -------------------------------------------
    def _run_fast(
        self,
        case: dict[str, Any],
        per_round: list[list[list[str]]],
        inp: str,
        total: int,
        base: dict[str, Any],
        *,
        cap: int,
        locate: bool,
    ) -> CaseResult:
        try:
            res = self._engine.run(inp, frames=per_round, max_ticks=cap)
        except FastLittlemanError as exc:
            return CaseResult(passed=False, frames_matched=None, first_mismatch_index=None,
                              ticks=0, error=f"engine: {exc}", **base)
        # ``passed`` is only *known* on the settled path: the native runner leaves
        # it None when the run hit the cap or every man halted, and those are the
        # runs that committed too few frames. Treating "no fatal" as a pass would
        # green-light a stalled program.
        if res.passed is True and not res.output:
            return CaseResult(passed=True, frames_matched=total, first_mismatch_index=None,
                              ticks=res.step, error=None, **base)

        if res.output:
            # A display problem that emits anything is an error, even if every
            # frame matched (SPEC.md).
            error = f"emitted {len(res.output)} output value(s): {res.output[:8]}"
        elif res.fatal == "wrong-frame":
            error = "committed a frame the judge did not expect"
        elif res.fatal:
            error = f"fatal: {res.fatal}"
        elif res.step >= cap:
            error = f"tick cap: too few frames committed within {cap:,} ticks"
        else:
            error = f"stopped after {res.step} ticks ({res.reason}) with frames outstanding"

        matched: int | None = None
        if res.fatal is None and res.output:
            matched = total  # frames were fine; the output is the offence
        elif locate:
            matched = self._locate_fast(inp, per_round, total, cap=cap)
        mismatch = matched if (res.fatal == "wrong-frame" and matched is not None) else None
        return CaseResult(passed=False, frames_matched=matched, first_mismatch_index=mismatch,
                          ticks=res.step, error=error, **base)

    def _locate_fast(
        self,
        inp: str,
        per_round: list[list[list[str]]],
        total: int,
        *,
        cap: int,
    ) -> int | None:
        """Largest ``k`` whose first-``k``-frames run passes — the matched count.

        Truncating the expectation cannot change the run before frame ``k``: the
        engine stops the instant the ``k``-th frame matches, so the only thing
        truncation moves (an earlier input release for a later round) happens
        strictly after the run has already ended.
        """

        def ok(k: int) -> bool:
            try:
                probe = self._engine.run(inp, frames=truncate_frames(per_round, k), max_ticks=cap)
            except FastLittlemanError:
                return False
            return probe.passed is True and not probe.output

        if not ok(0):
            return None
        lo, hi = 0, total  # ok(lo) is True, ok(hi) is False
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo

    # reference (Node/WASM oracle) ----------------------------------------------
    def _run_reference(
        self,
        per_round: list[list[list[str]]],
        inp: str,
        total: int,
        base: dict[str, Any],
        *,
        cap: int,
    ) -> CaseResult:
        assert self._path is not None
        try:
            snap = self._engine.judge(self._path, input=inp, frames=per_round, max_ticks=cap)
        except LittlemanError as exc:
            return CaseResult(passed=False, frames_matched=None, first_mismatch_index=None,
                              ticks=0, error=f"engine: {exc}", **base)
        judged = snap.frame_judge
        matched = judged.matched if judged is not None else 0
        mismatch = judged.mismatch if judged is not None else None
        index = mismatch.get("index") if isinstance(mismatch, dict) else None
        if snap.fatal is not None:
            error = f"fatal: {snap.fatal.reason}"
        elif snap.output:
            error = f"emitted {len(snap.output)} output value(s): {list(snap.output)[:8]}"
        elif judged is None:
            error = "engine reported no frame verdict"
        elif index is not None:
            error = "committed a frame the judge did not expect"
        elif matched < total:
            error = f"only {matched}/{total} frames committed within {cap:,} ticks"
        else:
            error = None
        passed = error is None and judged is not None and judged.passed
        return CaseResult(passed=passed, frames_matched=matched, first_mismatch_index=index,
                          ticks=snap.step, error=error, **base)


# ── one-shot helpers ──────────────────────────────────────────────────────────
def run_case(
    program: str | os.PathLike[str] | Sequence[str],
    case: dict[str, Any],
    *,
    backend: str | None = None,
    cap: int = scoring.DEFAULT_TICK_CAP,
    gated: bool = True,
    locate: bool = True,
    engine: Any | None = None,
) -> CaseResult:
    """Replay a single public case. See :meth:`Runner.run_case`.

    ``CaseResult.as_tuple()`` is
    ``(passed, n_frames_matched, first_mismatch_index, ticks, error)``.
    """
    with Runner(program, backend=backend, engine=engine) as runner:
        return runner.run_case(case, cap=cap, gated=gated, locate=locate)


def check_all(
    program: str | os.PathLike[str] | Sequence[str],
    problem: str | os.PathLike[str] | dict[str, Any] = DEFAULT_PROBLEM,
    *,
    backend: str | None = None,
    cap: int | None = None,
    gated: bool = True,
    locate: bool = True,
    names: Sequence[str] | None = None,
    engine: Any | None = None,
) -> CheckResult:
    """Replay every public case of ``problem`` and score the program.

    ``cap`` defaults to the problem's own ``tickCap`` (15,000,000 for
    ``pathfinder``), *not* the 5,000,000 global default. ``names`` filters to a
    subset of cases (the average then covers only those, so it is a debugging
    aid, not a score).
    """
    prob = scoring.load_problem(problem)
    cases = list(prob.get("publicTestData") or [])
    if names is not None:
        wanted = set(names)
        cases = [c for c in cases if str(c.get("name", "?")) in wanted]
    tick_cap = cap if cap is not None else (prob.get("tickCap") or scoring.DEFAULT_TICK_CAP)
    source = _source_text(program)
    width, height, area2 = scoring.footprint(source)

    with Runner(program, backend=backend, engine=engine) as runner:
        results = [
            runner.run_case(case, cap=tick_cap, gated=gated, locate=locate) for case in cases
        ]
        backend_name = runner.backend

    result = CheckResult(
        problem=str(prob.get("slug") or prob.get("name") or problem),
        backend=backend_name,
        tick_cap=tick_cap,
        width=width,
        height=height,
        area2=area2,
        cases=results,
        scoring_mode=prob.get("scoring", "footprint-tick"),
    )
    if results:
        result.avg_ticks = sum(c.ticks for c in results) / len(results)
    # The formula itself lives in optimize/scoring — never restated here.
    result.score = optimize.score_grid(
        source,
        prob,
        result=optimize.VerifyResult(
            passed=result.passed,
            cases=[optimize.CaseVerdict(c.name, c.passed, c.ticks, c.error or "") for c in results],
            avg_ticks=result.avg_ticks,
        ),
    )
    return result


def committed_frames(
    program: str | os.PathLike[str] | Sequence[str],
    case: dict[str, Any],
    *,
    cap: int = scoring.DEFAULT_TICK_CAP,
) -> DisplayRun:
    """What the program actually drew, frame by frame (reference engine only).

    The verdict says *that* a frame was wrong; this says *what* was on the panel.
    Rounds are gated exactly as in :meth:`Runner.run_case` — the expected frames
    go to the engine, which withholds later rounds' input.
    """
    path, cleanup = _program_path(program)
    try:
        runs = Littleman().display_frames(path, [case], max_ticks=cap)
    finally:
        if cleanup is not None:
            cleanup()
    if not runs:
        raise LittlemanError("display-frames returned no case")
    return runs[0]


def frame_diff(got: Sequence[str], want: Sequence[str]) -> str:
    """A row-by-row diff of two frames, marking the rows that differ."""
    lines = []
    for y in range(max(len(got), len(want))):
        g = got[y] if y < len(got) else ""
        w = want[y] if y < len(want) else ""
        lines.append(f"  {y:>2} {'!' if g != w else ' '} got {g}   want {w}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="randomfun2026solvers.pathfinder_check",
        description="Verify a display-judged .man frame by frame and score it.",
    )
    parser.add_argument("program", help="path to a .man file")
    parser.add_argument("-p", "--problem", default=DEFAULT_PROBLEM,
                        help="problem slug, .json path (default: pathfinder)")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="fast (in-process native, default) or reference (Node/WASM)")
    parser.add_argument("--cap", type=int, default=None,
                        help="tick cap (default: the problem's own tickCap)")
    parser.add_argument("--case", action="append", dest="names", default=None,
                        help="only this case (repeatable)")
    parser.add_argument("--ungated", action="store_true",
                        help="debug: release all rounds' input at tick 0 (NOT the judge)")
    parser.add_argument("--no-locate", action="store_true",
                        help="skip locating the matched-frame count on a failure")
    parser.add_argument("--diff", action="store_true",
                        help="on the first failing case, print got-vs-want for the bad frame")
    parser.add_argument("--cross-check", action="store_true",
                        help="also run scoring.score_program (reference engine) and compare")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    try:
        res = check_all(
            args.program,
            args.problem,
            backend=args.backend,
            cap=args.cap,
            gated=not args.ungated,
            locate=not args.no_locate,
            names=args.names,
        )
    except (scoring.ScoringError, LittlemanError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(res.to_json(), indent=2))
        return 0 if res.passed else 1

    print(res.table())
    if args.ungated:
        print("WARNING: --ungated released every round's input up front; the judge does not.")

    if args.cross_check and res.passed:
        try:
            ref = scoring.score_program(args.program, args.problem, tick_cap=res.tick_cap)
        except (scoring.ScoringError, LittlemanError) as exc:
            print(f"cross-check unavailable: {exc}")
        else:
            print(
                f"cross-check scoring.score_program: area2={ref.area2} "
                f"avg_ticks={ref.avg_ticks:,.2f} score={ref.score:,.0f}"
                + ("  (approx)" if ref.approx else "")
            )
            if res.avg_ticks is not None and ref.avg_ticks is not None:
                delta = abs(ref.avg_ticks - res.avg_ticks)
                if delta > 1e-6:
                    print(f"  !! disagrees with the measured avg by {delta:,.2f} ticks")

    if args.diff and not res.passed:
        bad = next((c for c in res.cases if not c.passed), None)
        cases = scoring.load_problem(args.problem).get("publicTestData") or []
        case = None
        if bad is not None:
            case = next((c for c in cases if str(c.get("name", "?")) == bad.name), None)
        if case is not None and bad is not None and bad.first_mismatch_index is not None:
            i = bad.first_mismatch_index
            try:
                run = committed_frames(args.program, case, cap=res.tick_cap)
            except LittlemanError as exc:
                print(f"diff unavailable: {exc}")
            else:
                want = flat_frames(case)
                got = run.frames[i] if i < len(run.frames) else []
                print(f"\n{bad.name}: frame #{i} of {len(want)}")
                print(frame_diff(got, want[i]))
    return 0 if res.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
