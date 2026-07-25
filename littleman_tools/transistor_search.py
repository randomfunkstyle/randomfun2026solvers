"""Bounded local search for the Y-free, bit-serial transistor primitive.

This is intentionally not a general solver or program generator.  It explores
one-cell direction changes from a supplied transistor artifact, asks the
reference engine to prove the controlled-forwarding contract, and retains only
the Pareto-optimal measurements.  It never writes artifacts; a promising source
must be promoted deliberately into ``tasks/solutions/primitives/``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product

from .runner import Littleman, LittlemanError

__all__ = [
    "Candidate",
    "CandidateMeasurement",
    "CandidateResult",
    "STREAM_INPUT",
    "STREAM_OUTPUT",
    "local_turn_variants",
    "measure_candidate",
    "pareto_frontier",
    "search_local_turns",
]


# Four repeated D,C pairs exercise a sustained stream and every truth-table row.
STREAM_PAIRS = ((0, 0), (0, 1), (1, 0), (1, 1)) * 4
STREAM_INPUT = tuple(value for pair in STREAM_PAIRS for value in pair)
STREAM_OUTPUT = tuple(data if control else 0 for data, control in STREAM_PAIRS)
TURN_GLYPHS = "><^v"


@dataclass(frozen=True)
class Candidate:
    """One local source variant, named deterministically for reproducible reports."""

    name: str
    source: str


@dataclass(frozen=True)
class CandidateMeasurement:
    """Minimization dimensions measured by the reference runtime."""

    side: int
    first_result_tick: int
    stream_ticks: int
    walking_ticks: int
    stall_ticks: int
    straight_through_arrows: int

    def key(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.side,
            self.first_result_tick,
            self.stream_ticks,
            self.walking_ticks,
            self.stall_ticks,
            self.straight_through_arrows,
        )


@dataclass(frozen=True)
class CandidateResult:
    """A valid candidate plus its measured objective values."""

    name: str
    source: str
    measurement: CandidateMeasurement


def local_turn_variants(source: str) -> Iterable[Candidate]:
    """Yield the seed and every one-nop-to-turn mutation in source order.

    A turn is the smallest geometry-only local change: it can shorten a route,
    but cannot add arithmetic or disguise a no-op as a state operation.
    """
    yield Candidate("seed", source)
    for index, glyph in enumerate(source):
        if glyph != ".":
            continue
        for replacement in TURN_GLYPHS:
            yield Candidate(
                name=f"turn-{index}-{replacement}",
                source=source[:index] + replacement + source[index + 1 :],
            )


def measure_candidate(
    candidate: Candidate,
    *,
    runner: Littleman | None = None,
    max_ticks: int = 500,
) -> CandidateResult | None:
    """Return a measurement only when every checked transistor stream is exact."""
    runner = runner or Littleman()
    if "Y" in candidate.source:
        return None

    first_result_ticks: list[int] = []
    try:
        for data, control in product((0, 1), repeat=2):
            expected = (data if control else 0,)
            snapshot = runner.judge(
                candidate.source,
                input=(data, control),
                expected=expected,
                max_ticks=max_ticks,
            )
            if snapshot.output != list(expected) or snapshot.output_settled is not True:
                return None
            first_result_ticks.append(snapshot.step)

        stream = runner.judge(
            candidate.source,
            input=STREAM_INPUT,
            expected=STREAM_OUTPUT,
            max_ticks=max_ticks,
        )
        if stream.output != list(STREAM_OUTPUT) or stream.output_settled is not True:
            return None
        profile = runner.activity_profile(
            candidate.source,
            input=STREAM_INPUT,
            expected=STREAM_OUTPUT,
            max_ticks=max_ticks,
        )
    except LittlemanError:
        return None

    lines = candidate.source.splitlines()
    return CandidateResult(
        name=candidate.name,
        source=candidate.source,
        measurement=CandidateMeasurement(
            side=max(len(lines), *(len(line) for line in lines)),
            first_result_tick=max(first_result_ticks),
            stream_ticks=profile.total_ticks,
            walking_ticks=profile.walking_ticks,
            stall_ticks=profile.stall_ticks,
            straight_through_arrows=profile.straight_through_arrows,
        ),
    )


def pareto_frontier(results: Iterable[CandidateResult]) -> tuple[CandidateResult, ...]:
    """Keep the first representative of each non-dominated metric vector."""
    frontier: list[CandidateResult] = []
    seen: set[tuple[int, int, int, int, int, int]] = set()
    for result in results:
        key = result.measurement.key()
        if key in seen:
            continue
        if any(_dominates(existing.measurement, result.measurement) for existing in frontier):
            continue
        frontier = [
            existing
            for existing in frontier
            if not _dominates(result.measurement, existing.measurement)
        ]
        frontier.append(result)
        seen.add(key)
    return tuple(frontier)


def search_local_turns(
    source: str,
    *,
    runner: Littleman | None = None,
    max_ticks: int = 500,
) -> tuple[CandidateResult, ...]:
    """Evaluate every bounded local turn mutation and return its Pareto frontier."""
    runner = runner or Littleman()
    valid = (
        result
        for candidate in local_turn_variants(source)
        if (result := measure_candidate(candidate, runner=runner, max_ticks=max_ticks)) is not None
    )
    return pareto_frontier(valid)


def _dominates(left: CandidateMeasurement, right: CandidateMeasurement) -> bool:
    return left.key() != right.key() and all(
        a <= b for a, b in zip(left.key(), right.key(), strict=True)
    )
