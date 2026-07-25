"""Deterministically reshape a submitted LM-1 CPU's looping ROM.

ROM folding trades one scored axis for the other without changing the emitted
word stream.  This search stays at generator level: it rebuilds the complete
machine for neighboring ROM row counts, validates every public case, and keeps
only a strict improvement in the problem's real objective.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import optimize, scoring
from ..manast import Refine, parse_ast
from ..manparse import parse_program
from . import machine, programs

Shape = Literal["auto", "narrow", "widen"]


@dataclass(frozen=True)
class RomTrial:
    round: int
    rows: int
    width: int
    height: int
    avg_ticks: float
    objective: float
    accepted: bool


@dataclass(frozen=True)
class RomSearch:
    machine: machine.Machine
    baseline_rows: int
    baseline_objective: float
    trials: tuple[RomTrial, ...]

    @property
    def improved(self) -> bool:
        return self.machine.rom_rows != self.baseline_rows


@dataclass(frozen=True)
class HandmadeRomSearch:
    rows: tuple[str, ...]
    rom_rows: int
    baseline_rows: int
    width: int
    height: int
    baseline_objective: float
    objective: float
    trials: tuple[RomTrial, ...]

    @property
    def improved(self) -> bool:
        return self.rom_rows != self.baseline_rows


def neighbor_rows(rows: int, width: int, height: int, shape: Shape = "auto") -> tuple[int, ...]:
    """Return adjacent folds in deterministic, axis-aware order.

    More rows make the ROM narrower and taller; fewer rows make it wider and
    shorter.  ``auto`` tries the direction that attacks the binding axis first,
    then the opposite direction so the heuristic never hides a local win.
    """
    narrower = rows + 1
    wider = rows - 1
    if shape == "narrow":
        ordered = (narrower,)
    elif shape == "widen":
        ordered = (wider,)
    elif width > height:
        ordered = (narrower, wider)
    elif height > width:
        ordered = (wider, narrower)
    else:
        ordered = (narrower, wider)
    return tuple(r for r in ordered if r >= 1)


def _build(program_slug: str, rows: int) -> machine.Machine:
    return machine.build(
        programs.load(program_slug),
        tape_n=machine.TAPE_SIZE[program_slug],
        rom_rows=rows,
        display=machine.display_for(program_slug),
        stream=machine.STREAM_SIZE.get(program_slug),
    )


def _objective(grid: machine.Machine, avg_ticks: float, problem_slug: str) -> float:
    kind = scoring.load_problem(problem_slug).get("scoring", "footprint-tick")
    return float(grid.footprint) if kind == "footprint" else grid.footprint * avg_ticks


def _grid_objective(width: int, height: int, avg_ticks: float, problem_slug: str) -> float:
    factor = max(width, height) ** 2
    kind = scoring.load_problem(problem_slug).get("scoring", "footprint-tick")
    return float(factor) if kind == "footprint" else factor * avg_ticks


def _shape(rows: tuple[str, ...] | list[str]) -> tuple[int, int]:
    return max((len(row.rstrip()) for row in rows), default=0), len(rows)


def _top_room_size(rows: tuple[str, ...] | list[str]) -> tuple[int, int]:
    text = "\n".join(rows) + "\n"
    ast = parse_ast(parse_program(text, bind=False), refine=Refine.BLOCKS)
    room = next((room for room in ast.rooms if room.x == 0 and room.y == 0), None)
    if room is None:
        raise ValueError("grid has no top-left ROM room")
    return room.size


def _replace_top_rom(
    handmade_rows: tuple[str, ...],
    *,
    program_slug: str,
    rom_rows: int,
    old_height: int,
) -> tuple[str, ...]:
    generated = _build(program_slug, rom_rows)
    new_width, new_height = _top_room_size(generated.rows)
    prefix = tuple(row[:new_width] for row in generated.rows[:new_height])
    return tuple(row.rstrip() for row in prefix + handmade_rows[old_height:])


def search(
    submitted: Path,
    *,
    program_slug: str,
    rounds: int = 10,
    shape: Shape = "auto",
    log=print,
) -> RomSearch:
    """Search neighboring ROM folds, refusing non-generator submitted grids."""
    problem_slug = programs.problem_of(program_slug)
    baseline = machine.build_for(program_slug)
    submitted_text = submitted.read_text(encoding="utf-8")
    generated_text = "\n".join(baseline.rows) + "\n"
    if submitted_text != generated_text:
        raise ValueError(
            f"{submitted} is not the exact {program_slug!r} CPU generator output; "
            "ROM reshaping is only safe for a matching submitted CPU solution"
        )

    verified = optimize.verify(baseline.rows, problem_slug)
    if not verified.passed or verified.avg_ticks is None:
        raise ValueError("submitted CPU baseline does not pass every public case")
    best = baseline
    best_objective = _objective(best, float(verified.avg_ticks), problem_slug)
    initial_objective = best_objective
    trials: list[RomTrial] = []
    log(
        f"baseline ROM rows={best.rom_rows} grid={best.width}x{best.height} "
        f"avgTicks={verified.avg_ticks:,.2f} objective={best_objective:,.2f}"
    )

    for rnd in range(1, rounds + 1):
        choices: list[tuple[float, machine.Machine, float]] = []
        for rows in neighbor_rows(best.rom_rows, best.width, best.height, shape):
            candidate = _build(program_slug, rows)
            result = optimize.verify(candidate.rows, problem_slug)
            if not result.passed or result.avg_ticks is None:
                log(f"  round {rnd}: ROM rows={rows} -> REJECTED, public cases failed")
                continue
            objective = _objective(candidate, float(result.avg_ticks), problem_slug)
            choices.append((objective, candidate, float(result.avg_ticks)))

        if not choices:
            log(f"  round {rnd}: no valid neighboring ROM fold")
            break
        objective, candidate, avg_ticks = min(
            choices, key=lambda item: (item[0], item[1].footprint, item[1].height, item[1].width)
        )
        accepted = objective < best_objective
        for trial_objective, trial, trial_ticks in choices:
            trials.append(
                RomTrial(
                    round=rnd,
                    rows=trial.rom_rows,
                    width=trial.width,
                    height=trial.height,
                    avg_ticks=trial_ticks,
                    objective=trial_objective,
                    accepted=accepted and trial is candidate,
                )
            )
            verdict = "ACCEPTED" if accepted and trial is candidate else "rejected"
            log(
                f"  round {rnd}: ROM rows={trial.rom_rows} -> "
                f"{trial.width}x{trial.height}, avgTicks={trial_ticks:,.2f}, "
                f"objective={trial_objective:,.2f} ({verdict})"
            )
        if not accepted:
            log(f"  round {rnd}: local ROM-fold fixed point")
            break
        best = candidate
        best_objective = objective

    return RomSearch(
        machine=best,
        baseline_rows=baseline.rom_rows,
        baseline_objective=initial_objective,
        trials=tuple(trials),
    )


def search_handmade(
    grid: Path,
    *,
    program_slug: str,
    rounds: int = 10,
    shape: Shape = "auto",
    log=print,
) -> HandmadeRomSearch:
    """Refold a generator-identical top ROM while preserving a hand-made tail.

    This is intentionally narrower than arbitrary AST surgery.  The complete
    top-left room must byte-match the registered CPU generator's ROM; everything
    below it is then shifted as one unchanged suffix when the ROM gains or loses
    rows.
    """
    problem_slug = programs.problem_of(program_slug)
    baseline_machine = machine.build_for(program_slug)
    handmade_rows = tuple(grid.read_text(encoding="utf-8").splitlines())
    old_width, old_height = _top_room_size(handmade_rows)
    generated_width, generated_height = _top_room_size(baseline_machine.rows)
    if (old_width, old_height) != (generated_width, generated_height):
        raise ValueError("hand-made top room has a different size from the CPU ROM")
    handmade_prefix = tuple(row[:old_width] for row in handmade_rows[:old_height])
    generated_prefix = tuple(
        row[:generated_width] for row in baseline_machine.rows[:generated_height]
    )
    if handmade_prefix != generated_prefix:
        raise ValueError(
            "hand-made top room is not byte-identical to the registered CPU ROM"
        )

    baseline_result = optimize.verify(handmade_rows, problem_slug)
    if not baseline_result.passed or baseline_result.avg_ticks is None:
        raise ValueError("hand-made baseline does not pass every public case")
    width, height = _shape(handmade_rows)
    best_rows = handmade_rows
    best_rom_rows = baseline_machine.rom_rows
    best_objective = _grid_objective(
        width, height, float(baseline_result.avg_ticks), problem_slug
    )
    initial_objective = best_objective
    trials: list[RomTrial] = []
    log(
        f"baseline handmade ROM rows={best_rom_rows} grid={width}x{height} "
        f"avgTicks={baseline_result.avg_ticks:,.2f} objective={best_objective:,.2f}"
    )

    for rnd in range(1, rounds + 1):
        choices: list[tuple[float, int, tuple[str, ...], int, int, float]] = []
        for rows in neighbor_rows(best_rom_rows, width, height, shape):
            candidate_rows = _replace_top_rom(
                handmade_rows,
                program_slug=program_slug,
                rom_rows=rows,
                old_height=old_height,
            )
            candidate_width, candidate_height = _shape(candidate_rows)
            result = optimize.verify(candidate_rows, problem_slug)
            if not result.passed or result.avg_ticks is None:
                log(f"  round {rnd}: ROM rows={rows} -> REJECTED, public cases failed")
                continue
            objective = _grid_objective(
                candidate_width,
                candidate_height,
                float(result.avg_ticks),
                problem_slug,
            )
            choices.append(
                (
                    objective,
                    rows,
                    candidate_rows,
                    candidate_width,
                    candidate_height,
                    float(result.avg_ticks),
                )
            )

        if not choices:
            log(f"  round {rnd}: no valid neighboring ROM fold")
            break
        chosen = min(choices, key=lambda item: (item[0], item[4], item[3], item[1]))
        accepted = chosen[0] < best_objective
        for objective, rows, _candidate, trial_width, trial_height, avg_ticks in choices:
            is_chosen = accepted and rows == chosen[1]
            trials.append(
                RomTrial(
                    round=rnd,
                    rows=rows,
                    width=trial_width,
                    height=trial_height,
                    avg_ticks=avg_ticks,
                    objective=objective,
                    accepted=is_chosen,
                )
            )
            verdict = "ACCEPTED" if is_chosen else "rejected"
            log(
                f"  round {rnd}: ROM rows={rows} -> {trial_width}x{trial_height}, "
                f"avgTicks={avg_ticks:,.2f}, objective={objective:,.2f} ({verdict})"
            )
        if not accepted:
            log(f"  round {rnd}: local ROM-fold fixed point")
            break
        best_objective, best_rom_rows, best_rows, width, height, _ticks = chosen

    return HandmadeRomSearch(
        rows=best_rows,
        rom_rows=best_rom_rows,
        baseline_rows=baseline_machine.rom_rows,
        width=width,
        height=height,
        baseline_objective=initial_objective,
        objective=best_objective,
        trials=tuple(trials),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path, help="best submitted CPU grid")
    ap.add_argument("--program", required=True, choices=sorted(machine.TAPE_SIZE))
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--shape", choices=("auto", "narrow", "widen"), default="auto")
    ap.add_argument(
        "--handmade-top-rom",
        action="store_true",
        help="refold a generator-identical top ROM and preserve the hand-made suffix",
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    if args.handmade_top_rom:
        handmade = search_handmade(
            args.grid,
            program_slug=args.program,
            rounds=args.rounds,
            shape=args.shape,
        )
        print(
            f"result ROM rows={handmade.rom_rows} "
            f"grid={handmade.width}x{handmade.height} "
            f"factor={max(handmade.width, handmade.height) ** 2:,}"
        )
        if args.out and handmade.improved:
            args.out.write_text("\n".join(handmade.rows) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
    else:
        result = search(
            args.grid,
            program_slug=args.program,
            rounds=args.rounds,
            shape=args.shape,
        )
        best = result.machine
        print(
            f"result ROM rows={best.rom_rows} grid={best.width}x{best.height} "
            f"factor={best.footprint:,}"
        )
        if args.out and result.improved:
            args.out.write_text("\n".join(best.rows) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
