#!/usr/bin/env python3
"""The value oracle — score a set of passes across the archived solutions.

No rewrite is "done" until it shows an engine-verified objective win on a real
archived solution. :func:`bench` is that measurement: for each slug it loads the
best archived ``.man``, runs the given passes through ``optimize.optimize()``, and
reports the ``max(w,h)² × avgTicks`` delta plus whether the result still verifies.

One :class:`BenchRow` per slug carries the base objective, the optimised
objective, their signed delta (negative is an improvement), and the pass flag.
:func:`format_rows` renders the portfolio as a table with a win/regression
summary; a regression (``delta > 0``) or a failed grid is flagged loudly, because
the ``optimize`` accept gate should make either impossible.

Engine cost is the discipline here. Verification defaults to the in-process
``FastLittleman`` (``optimize.verify``'s default — this module never forces
``LM_VALIDATOR=reference``), and every ``optimize`` run is memoised on
``(source text, slug, passes)`` so re-benchmarking the same grid never re-runs the
engine. The ``fast=True`` knob scores the cheap footprint proxy (``max(w,h)²``, no
tick measurement) for a quick pre-scan; ``fast=False`` reports the full judged
score.
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manrewrite import PassFn
    from .optimize import OptimizeResult

__all__ = ["BenchRow", "bench", "format_rows", "main", "DEFAULT_SLUGS"]

#: repo root: manbench.py -> randomfun2026solvers -> python -> solvers -> <root>
_ROOT = Path(__file__).resolve().parents[3]
#: Per-slug archive directory: ``solutions/<slug>/<zero-padded-score>_<slug>.man``.
_SOLUTIONS_DIR = _ROOT / "solutions"
#: The portfolio the full harness scores; ``bench`` accepts any subset. Ordered
#: CHEAPEST-FIRST (measured empty-pass floor: memory 0.7s, plotter 1.7s, snake
#: 7.2s, sudoku-validity 12.4s) so a progress run's ETA — mean-per-slug so far ×
#: remaining — calibrates on the quick slugs before it reaches the slow ones.
DEFAULT_SLUGS: tuple[str, ...] = (
    "memory",
    "plotter",
    "brackets",
    "tcp",
    "matmul",
    "sudoku-validity",
    "gradebook",
    "snake",
)

#: ``(source text, slug, passes key) -> OptimizeResult``. Module-level so a second
#: ``bench`` of the same grid is free; tests clear it to isolate call counts.
_CACHE: dict[tuple[str, str, tuple[str, ...]], OptimizeResult] = {}


def _score_of(name: str) -> float:
    """Numeric score prefix of an archive filename; ``inf`` for ``unscored_…``."""
    head = name.split("_", 1)[0]
    try:
        return float(head)
    except ValueError:
        return math.inf


def _resolve_man(slug: str) -> Path | None:
    """Best archived ``.man`` for `slug` = lowest-scoring file in ``solutions/<slug>/``.

    Archive names are the zero-padded objective score, so the smallest prefix is
    the best solution. ``unscored_*`` files sort last and are only used as a
    fallback when nothing scored exists.
    """
    d = _SOLUTIONS_DIR / slug
    if not d.is_dir():
        return None
    hits = sorted(d.glob(f"*_{slug}.man"), key=lambda p: (_score_of(p.name), p.name))
    return hits[0] if hits else None


@dataclass
class BenchRow:
    """One slug's benchmark result.

    :param slug: problem slug (``brackets``, ``memory`` …).
    :param path: the archived ``.man`` benchmarked.
    :param base: objective before the passes (``max(w,h)²`` proxy, or full score).
    :param opt: objective after.
    :param delta: ``opt - base`` (negative is an improvement).
    :param passed: the optimised grid still verifies on the engine.
    """

    slug: str
    path: str
    base: float
    opt: float
    delta: float
    passed: bool


def _optimize_cached(
    optimize_mod,
    path: Path,
    slug: str,
    prob: dict,
    passes_list: list,
    passes_key: tuple[str, ...],
) -> OptimizeResult:
    """Run (or reuse a cached) ``optimize.optimize`` for one archived grid.

    Keyed on the grid's source text so identical grids — a repeated slug, or two
    slugs resolving to the same file — cost exactly one engine-driven optimise.
    """
    source = path.read_text(encoding="utf-8")
    key = (source, slug, passes_key)
    if key not in _CACHE:
        _CACHE[key] = optimize_mod.optimize(path, prob, passes=passes_list)
    return _CACHE[key]


def _objective_pair(scoring_mod, res: OptimizeResult, fast: bool) -> tuple[float, float]:
    """``(base, opt)`` objective for a result under the chosen metric.

    ``fast`` scores the footprint proxy ``max(w,h)²`` (pure, no engine); otherwise
    the full judged score ``optimize`` already computed (``None`` → ``nan`` when a
    grid did not pass).
    """
    if fast:
        base = float(scoring_mod.footprint("\n".join(res.base_grid))[2])
        opt = float(scoring_mod.footprint("\n".join(res.grid))[2])
        return base, opt
    base = res.base_score if res.base_score is not None else math.nan
    opt = res.score if res.score is not None else math.nan
    return base, opt


def _fmt_secs(s: float) -> str:
    """Whole-second wall time for a progress line (``12s``, ``0s`` for sub-second)."""
    return f"{s:.0f}s"


def bench(
    passes: Sequence[PassFn],
    slugs: Sequence[str] | None = None,
    *,
    fast: bool = True,
    progress: bool = False,
) -> list[BenchRow]:
    """Score `passes` across `slugs` (:data:`DEFAULT_SLUGS` when ``None``).

    Resolves each slug to its best archived ``.man`` and its problem JSON, runs
    :func:`optimize.optimize` with just these passes, and reports the objective
    before and after. ``fast=True`` (default) reports the cheap footprint proxy
    (``max(w,h)²``) for a quick pre-scan; ``fast=False`` reports the full judged
    ``max(w,h)² × avgTicks`` score.

    Verification uses ``optimize``'s in-process ``FastLittleman`` backend; each
    optimise is memoised (see :data:`_CACHE`). A slug with no archived grid, or one
    whose input does not pass, is reported with ``passed=False`` rather than
    raising, so a portfolio run never aborts on one entry.

    ``progress=True`` streams a per-slug log to **STDERR** (never STDOUT, so it
    never contaminates the ``format_rows`` table): a start block listing the chosen
    slugs and their resolved archive paths, one ``[i/N] <slug> ... done Ns (elapsed
    total Ms, eta Ks)`` line as each slug finishes — the ETA is the mean per-slug
    wall time so far times the remaining slugs, refined after every slug — and a
    final total-wall-time line. Default is off so tests and the fast unit tier stay
    silent.
    """
    from . import optimize as optimize_mod
    from . import scoring
    from .fast_littleman import FastLittlemanError
    from .littleman import LittlemanError

    errs = (
        optimize_mod.OptimizeError,
        scoring.ScoringError,
        LittlemanError,
        FastLittlemanError,
    )
    passes_list = list(passes)
    passes_key = tuple(getattr(p, "__name__", repr(p)) for p in passes_list)
    chosen = list(slugs) if slugs is not None else list(DEFAULT_SLUGS)

    def _log(msg: str) -> None:
        if progress:
            print(msg, file=sys.stderr, flush=True)

    resolved = [(slug, _resolve_man(slug)) for slug in chosen]
    n = len(resolved)
    # ETA weight: optimise cost scales with program size far more than with slug
    # count (the relayout width-sweep and the verify tick-runs both grow with the
    # grid), so a naive count-mean ETA lies badly when a big slug is still queued
    # behind cheap ones. Weight each slug by its archive byte size and spend the
    # ETA against remaining *weight*, not remaining count. Falls back to count when
    # sizes are unavailable (a min weight of 1 keeps every slug counted).
    weights = [max(1, p.stat().st_size) if p is not None else 1 for _, p in resolved]
    total_weight = sum(weights)
    if progress:
        metric = "full judged max(w,h)²×avgTicks" if not fast else "fast footprint proxy max(w,h)²"
        _log(f"bench: {n} slug(s), {len(passes_list)} pass(es), metric={metric}")
        for i, (slug, path) in enumerate(resolved, 1):
            _log(f"  [{i}/{n}] {slug} -> {path if path is not None else '(no archive)'}")

    rows: list[BenchRow] = []
    t0 = time.perf_counter()
    done_weight = 0
    for i, (slug, path) in enumerate(resolved, 1):
        t_slug = time.perf_counter()
        if path is None:
            rows.append(BenchRow(slug, "", math.nan, math.nan, math.nan, passed=False))
        else:
            try:
                prob = scoring.load_problem(slug)
                res = _optimize_cached(optimize_mod, path, slug, prob, passes_list, passes_key)
                base, opt = _objective_pair(scoring, res, fast)
                delta = opt - base if not (math.isnan(base) or math.isnan(opt)) else math.nan
                rows.append(BenchRow(slug, str(path), base, opt, delta, passed=res.passed))
            except errs:
                rows.append(BenchRow(slug, str(path), math.nan, math.nan, math.nan, passed=False))
        elapsed = time.perf_counter() - t0
        done_weight += weights[i - 1]
        # ETA = (time per unit of weight so far) × weight still queued.
        remaining_weight = total_weight - done_weight
        eta = (elapsed / done_weight) * remaining_weight if done_weight else 0.0
        _log(
            f"[{i}/{n}] {slug} ... done {_fmt_secs(time.perf_counter() - t_slug)} "
            f"(elapsed total {_fmt_secs(elapsed)}, eta {_fmt_secs(eta)})"
        )
    _log(f"bench: {n} slug(s) complete in {_fmt_secs(time.perf_counter() - t0)}")
    return rows


# ── reporting ─────────────────────────────────────────────────────────────────
def _classify(row: BenchRow) -> str:
    """One of ``FAIL`` / ``n/a`` / ``WIN`` / ``REGRESSION`` / ``same``."""
    if not row.passed:
        return "FAIL"
    if math.isnan(row.delta):
        return "n/a"
    if row.delta < 0:
        return "WIN"
    if row.delta > 0:
        return "REGRESSION"
    return "same"


def _fmt(x: float) -> str:
    return "-" if math.isnan(x) else f"{x:,.1f}"


def format_rows(rows: Sequence[BenchRow]) -> str:
    """Render a portfolio as an aligned table plus a win/regression summary.

    A regression (``delta > 0``) or a failed grid is flagged loudly — the
    ``optimize`` accept gate should make either impossible, so seeing one is a bug
    signal, not a tuning result.
    """
    header = f"{'slug':<16} {'base':>15} {'opt':>15} {'delta':>15}  status"
    lines = [header, "-" * len(header)]
    wins = regressions = same = fails = 0
    total = 0.0
    for r in rows:
        status = _classify(r)
        if status == "WIN":
            wins += 1
        elif status == "REGRESSION":
            regressions += 1
        elif status == "FAIL":
            fails += 1
        elif status == "same":
            same += 1
        if not math.isnan(r.delta):
            total += r.delta
        lines.append(
            f"{r.slug:<16} {_fmt(r.base):>15} {_fmt(r.opt):>15} {_fmt(r.delta):>15}  {status}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{len(rows)} slugs: {wins} win, {regressions} regression, "
        f"{same} no-change, {fails} fail"
    )
    lines.append(f"total objective delta: {_fmt(total)}")
    if regressions or fails:
        lines.append(
            f"!! {regressions} REGRESSION(S), {fails} FAILURE(S) — "
            "the accept gate should prevent this; investigate"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="randomfun2026solvers.manbench",
        description="Benchmark optimize passes across the archived best solutions.",
    )
    parser.add_argument(
        "--slugs",
        help="comma-separated slugs (default: the 8-archive portfolio)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="full judged score (max(w,h)²×avgTicks) instead of the fast footprint proxy",
    )
    args = parser.parse_args(argv)

    from .optimize import PASSES

    slugs = args.slugs.split(",") if args.slugs else None
    rows = bench(PASSES, slugs, fast=not args.full, progress=True)
    print(format_rows(rows))
    bad = any((not math.isnan(r.delta) and r.delta > 0) or not r.passed for r in rows)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
