#!/usr/bin/env python3
"""Deterministic optimisation audit for Little Man solution grids.

This is the decision layer above :mod:`manast`, :mod:`manfree`, and
:mod:`manopt`.  It answers two questions without editing a single glyph:

* What did the archived score frontier actually improve: footprint, ticks, or
  both?
* Which AST-level move family should be tried next on the current grid?

The archive score and the footprint are enough to recover the judge's implied
average tick count (``score / max(width, height)^2``).  That makes historical
classification evidence-based even when a submission note is missing.
Recommendations are structural and deterministic.  They never special-case a
public input/output pair; public cases are an acceptance gate for the generated
candidate, not the source of a hand tweak.
"""

from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .manast import Refine, parse_ast, round_trip_ok
from .manfree import (
    merge_candidates,
    near_merges,
    pipe_roles,
    scan,
    squash_report,
)
from .manparse import parse_program
from .scoring import footprint

REPO = Path(__file__).resolve().parents[3]

__all__ = [
    "SubmissionPoint",
    "Transition",
    "Recommendation",
    "GridAudit",
    "archive_frontier",
    "classify_transition",
    "audit_grid",
    "render_audit",
]


@dataclass(frozen=True)
class SubmissionPoint:
    path: str
    server_score: int
    width: int
    height: int
    factor: int
    implied_ticks: float
    rooms: int
    pipes: int
    live_cells: int


@dataclass(frozen=True)
class Transition:
    before: str
    after: str
    score_ratio: float
    factor_ratio: float
    tick_ratio: float
    rules: tuple[str, ...]


@dataclass(frozen=True)
class Recommendation:
    priority: int
    rule: str
    evidence: str
    command: str


@dataclass
class GridAudit:
    path: str
    slug: str | None
    width: int
    height: int
    factor: int
    ast_round_trip: bool
    rooms: int
    pipes: int
    strays: int
    ast_nodes: dict[str, int] = field(default_factory=dict)
    history: list[SubmissionPoint] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)


def _score_from_name(path: Path) -> int | None:
    head = path.name.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def _live_cells(path: Path) -> int:
    return sum(ch not in " \n\r\t" for ch in path.read_text(encoding="utf-8"))


def _submission_point(path: Path, score: int) -> SubmissionPoint:
    width, height, factor = footprint(path)
    program = parse_program(path, bind=False)
    return SubmissionPoint(
        path=str(path),
        server_score=score,
        width=width,
        height=height,
        factor=factor,
        implied_ticks=score / factor,
        rooms=len(program.rooms),
        pipes=len(program.pipes),
        live_cells=_live_cells(path),
    )


def classify_transition(before: SubmissionPoint, after: SubmissionPoint) -> Transition:
    """Classify one worse→better point on the archived score frontier."""
    factor_ratio = after.factor / before.factor
    tick_ratio = after.implied_ticks / before.implied_ticks
    rules: list[str] = []
    if (before.rooms, before.pipes) != (after.rooms, after.pipes):
        rules.append("topology-rewrite")
    if factor_ratio < 0.995:
        rules.append("footprint-compaction")
    if tick_ratio < 0.995:
        rules.append("execution-shortening")
    if factor_ratio > 1.005 and tick_ratio < 1 / factor_ratio:
        rules.append("speed-for-space-trade")
    if after.live_cells < before.live_cells:
        rules.append("code-density")
    if not rules:
        rules.append("phase-or-server-effect")
    return Transition(
        before=before.path,
        after=after.path,
        score_ratio=after.server_score / before.server_score,
        factor_ratio=factor_ratio,
        tick_ratio=tick_ratio,
        rules=tuple(rules),
    )


def archive_frontier(slug: str, root: Path = REPO / "solutions") -> list[SubmissionPoint]:
    """Archived submissions ordered from worst score to best score.

    The archive has score-bearing filenames but no reliable submission
    timestamp.  This is therefore a *quality frontier*, not a claim about wall
    clock chronology.
    """
    points = [
        _submission_point(path, score)
        for path in (root / slug).glob("*.man")
        if (score := _score_from_name(path)) is not None
    ]
    return sorted(points, key=lambda p: p.server_score, reverse=True)


def _command(module: str, path: Path, *args: str) -> str:
    words = ["uv", "run", "python", "-m", module, str(path), *args]
    return " ".join(shlex.quote(word) for word in words)


def audit_grid(
    path: str | Path,
    *,
    slug: str | None = None,
    history_root: Path = REPO / "solutions",
) -> GridAudit:
    """Build a read-only AST audit and ranked deterministic move shortlist."""
    grid = Path(path)
    # The audit is read-only and does not need one route-oracle subprocess for
    # every r/s in a CPU-sized grid. `manopt` performs the binding pass at the
    # mutation gate; keeping it out of discovery makes portfolio audits scale.
    ast = parse_ast(parse_program(grid, bind=False), refine=Refine.BLOCKS)
    if not round_trip_ok(ast):
        # Display/feed layouts can require the bound parse to classify a shared
        # arrow cell correctly. Pay for the route oracle only on that fallback.
        ast = parse_ast(grid, refine=Refine.BLOCKS)
    width, height, factor = footprint(grid)
    history = archive_frontier(slug, history_root) if slug else []
    transitions = [
        classify_transition(before, after)
        for before, after in zip(history, history[1:])
    ]
    nodes = Counter(
        type(child).__name__
        for room in ast.rooms
        for child in room.children
    )
    recommendations: list[Recommendation] = []
    ast_ok = round_trip_ok(ast)
    quoted_slug = slug or "<slug>"
    if not ast_ok:
        recommendations.append(Recommendation(
            0,
            "ast-round-trip-blocker",
            "The refined AST does not reproduce the source byte-for-byte; mutation is unsafe.",
            _command("randomfun2026solvers.manast", grid, "--refine", "1"),
        ))
    else:
        freedom = scan(ast)
        paying = freedom.paying_lines()
        axis = "row" if height >= width else "col"
        solo_pipe_lines = [
            line for line in freedom.pipe_only_lines()
            if line.axis == axis and len(line.pipes_here) == 1
        ]
        squashes = [
            line
            for block in squash_report(ast)
            for line in block.removable
            if line.ticks > 0
        ]
        folds = merge_candidates(ast, axis)
        near = near_merges(ast, axis, limit=1)
        undeclared = [pipe.id for pipe in ast.pipes if pipe.min_capacity is None]
        role_by_pipe = pipe_roles(ast)
        roles = Counter(role.value for role in role_by_pipe.values())

        if paying:
            recommendations.append(Recommendation(
                10,
                "dead-line",
                f"{len(paying)} proven removable {axis}(s) lower the footprint factor.",
                _command(
                    "randomfun2026solvers.mancompact",
                    grid,
                    "--problem",
                    quoted_slug,
                    "--out",
                    f"tasks/compacted/{grid.stem}.man",
                ),
            ))
        if undeclared:
            recommendations.append(Recommendation(
                15,
                "capacity-contract",
                f"{len(undeclared)} pipes lack minima ({dict(roles)}); annotate capacity before rerouting.",
                _command("randomfun2026solvers.manfree", grid, "--refine", "1"),
            ))
        if squashes:
            saved = sum(line.ticks for line in squashes)
            recommendations.append(Recommendation(
                20,
                "loop-squash",
                f"{len(squashes)} tried interior lines cost {saved} ticks per lap in total.",
                _command(
                    "randomfun2026solvers.manopt",
                    grid,
                    "--moves",
                    "squash",
                    "--problem",
                    quoted_slug,
                    "--rounds",
                    "100",
                    "--out",
                    f"tasks/compacted/{grid.stem}_squashed.man",
                ),
            ))
        if solo_pipe_lines:
            pipe_ids = sorted({
                int(name.removeprefix("pipe"))
                for line in solo_pipe_lines
                for name in line.pipes_here
                if role_by_pipe[int(name.removeprefix("pipe"))].value != "ring"
            })
            if pipe_ids:
                pipe_args = [
                    arg
                    for pipe_id in pipe_ids
                    for arg in ("--pipe-min", f"{pipe_id}=2")
                ]
                recommendations.append(Recommendation(
                    30,
                    "single-pipe-reroute",
                    f"{len(solo_pipe_lines)} binding-axis lines carry exactly one non-ring pipe and no code.",
                    _command(
                        "randomfun2026solvers.manopt",
                        grid,
                        "--moves",
                        "layout",
                        "--problem",
                        quoted_slug,
                        "--rounds",
                        "100",
                        *pipe_args,
                        "--out",
                        f"tasks/compacted/{grid.stem}_ast.man",
                    ),
                ))
        if folds:
            recommendations.append(Recommendation(
                40,
                "disjoint-line-fold",
                f"{len(folds)} pairs of {axis}s have disjoint occupied extents and can share geometry.",
                _command("randomfun2026solvers.manfree", grid, "--refine", "1"),
            ))
        elif near:
            recommendations.append(Recommendation(
                45,
                "shift-then-fold",
                f"The nearest {axis} pair overlaps by only {near[0][2]} cell(s).",
                _command("randomfun2026solvers.manfree", grid, "--refine", "1"),
            ))
        if not recommendations:
            recommendations.append(Recommendation(
                90,
                "structurally-tight",
                "No deterministic cut, squash, reroute, or fold candidate was found.",
                _command("randomfun2026solvers.manfree", grid, "--refine", "1"),
            ))

    return GridAudit(
        path=str(grid),
        slug=slug,
        width=width,
        height=height,
        factor=factor,
        ast_round_trip=ast_ok,
        rooms=len(ast.rooms),
        pipes=len(ast.pipes),
        strays=len(ast.strays),
        ast_nodes=dict(sorted(nodes.items())),
        history=history,
        transitions=transitions,
        recommendations=sorted(recommendations, key=lambda item: item.priority),
    )


def render_audit(audit: GridAudit) -> str:
    lines = [
        f"{audit.path}: {audit.width}x{audit.height}, factor {audit.factor:,}",
        f"AST round-trip: {'OK' if audit.ast_round_trip else 'FAILED'}; "
        f"rooms={audit.rooms}, pipes={audit.pipes}, strays={audit.strays}",
        f"AST nodes: {audit.ast_nodes}",
    ]
    if audit.history:
        lines += ["", f"Archived {audit.slug} score frontier (worst → best):"]
        for point in audit.history:
            lines.append(
                f"  {point.server_score:>14,}  {point.width}x{point.height}  "
                f"factor={point.factor:,} impliedTicks={point.implied_ticks:,.1f}  "
                f"rooms/pipes={point.rooms}/{point.pipes} live={point.live_cells}"
            )
        lines += ["", "Observed transition rules:"]
        counts = Counter(rule for transition in audit.transitions for rule in transition.rules)
        lines.append("  " + ", ".join(f"{rule}={count}" for rule, count in counts.most_common()))
        for transition in audit.transitions:
            lines.append(
                f"  score×{transition.score_ratio:.3f} factor×{transition.factor_ratio:.3f} "
                f"ticks×{transition.tick_ratio:.3f}: {', '.join(transition.rules)}"
            )
    lines += ["", "Ranked deterministic next actions:"]
    for rec in audit.recommendations:
        lines.append(f"  P{rec.priority:02d} {rec.rule}: {rec.evidence}")
        lines.append(f"      {rec.command}")
    lines.append("")
    lines.append(
        "Acceptance rule: render from AST, preserve pipe bindings/capacity, run every "
        "public case, and keep only a lower footprint×ticks score."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("grid", type=Path)
    parser.add_argument("--slug")
    parser.add_argument("--history-root", type=Path, default=REPO / "solutions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    audit = audit_grid(args.grid, slug=args.slug, history_root=args.history_root)
    print(json.dumps(asdict(audit), indent=2) if args.json else render_audit(audit))


if __name__ == "__main__":
    main()
