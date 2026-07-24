"""LM-1 task programs, plus the glue that runs them against the problem JSONs.

Each ``<slug>.asm`` in this directory is a program for the LM-1 machine, written
against the ISA table in :mod:`randomfun2026solvers.lm1.isa`. The slug matches a
problem in ``tasks/problems/``, so :func:`rounds_for_problem` can feed a program
the exact public test data the judge uses.

Only problems that need no *array* live here. Everything that needs indexed
memory is blocked on the STORE block by design (``ARCH.md`` §4.1) — except that
``tcp`` and ``brackets``, which ``ARCH.md`` does not list as blocked, turned out
to need indexed access too (see the step-2 report / ``isa.LM1_EXT``).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..asm import Program, assemble_file
from ..emulator import Round
from ..isa import DEFAULT_ISA, Isa

__all__ = [
    "PROGRAM_DIR",
    "PROBLEM_DIR",
    "PROBLEM_OF",
    "available",
    "load",
    "problem_of",
    "problem_json",
    "rounds_for_problem",
    "history_lesson_source",
]

PROGRAM_DIR = Path(__file__).resolve().parent
PROBLEM_DIR = Path(__file__).resolve().parents[5] / "tasks" / "problems"

#: Programs whose file name is not the problem slug (alternative solutions).
PROBLEM_OF: dict[str, str] = {"triangle-closed": "triangle"}


def problem_of(stem: str) -> str:
    """The problem slug a program file targets."""
    return PROBLEM_OF.get(stem, stem)


def available() -> dict[str, Path]:
    """``{slug: path}`` for every checked-in program, sorted by slug."""
    return {p.stem: p for p in sorted(PROGRAM_DIR.glob("*.asm"))}


def load(slug: str, *, isa: Isa = DEFAULT_ISA) -> Program:
    """Assemble ``<slug>.asm``."""
    paths = available()
    if slug not in paths:
        raise KeyError(f"no LM-1 program for {slug!r}; have {sorted(paths)}")
    return assemble_file(paths[slug], isa=isa)


def problem_json(slug: str) -> dict:
    return json.loads((PROBLEM_DIR / f"{slug}.json").read_text(encoding="utf-8"))


def rounds_for_problem(slug: str) -> list[tuple[str, list[Round]]]:
    """``[(case_name, rounds)]`` from a problem's ``publicTestData``.

    A case is either round-based (``rounds: [{in, out}, ...]``) or a single
    implicit round (``{in, out}``); both shapes appear in the JSONs.
    """
    cases = []
    for index, case in enumerate(problem_json(slug).get("publicTestData") or []):
        name = case.get("name") or f"case-{index}"
        raw_rounds = case.get("rounds") or [case]
        rounds = [
            Round(
                input=tuple(int(v) for v in (r.get("in") or [])),
                expected=tuple(int(v) for v in (r.get("out") or [])),
            )
            for r in raw_rounds
        ]
        cases.append((name, rounds))
    return cases


def history_lesson_source(slug: str = "history-lesson") -> str:
    """Regenerate ``history-lesson.asm`` from the problem JSON.

    The expected output is 2810 ASCII bytes of fixed text, so the program is
    pure ROM: one ``LDI c`` / ``OUT`` pair per byte, emitted by the assembler's
    ``.ascii`` directive. Kept as a function so the checked-in ``.asm`` can be
    regenerated if the problem text ever changes::

        (PROGRAM_DIR / "history-lesson.asm").write_text(history_lesson_source())
    """
    case = problem_json(slug)["publicTestData"][0]
    rounds = case.get("rounds") or [case]
    text = "".join(chr(int(v)) for r in rounds for v in (r.get("out") or []))
    lines = [
        "; history-lesson — GENERATED, do not hand-edit.",
        "; Regenerate with:",
        ";   from randomfun2026solvers.lm1.programs import PROGRAM_DIR, history_lesson_source",
        ';   (PROGRAM_DIR / "history-lesson.asm").write_text(history_lesson_source())',
        ";",
        "; No input, fixed output: the whole program is a ROM walk. `.ascii` expands",
        "; to `LDI c` / `OUT` per byte, so P = 3 * 2810 + 1 = 8431 words. Footprint-",
        "; scored, so the tick count does not matter — but a 8431-word ROM does, and",
        "; that is the honest cost of solving this on a general-purpose CPU.",
        "",
    ]
    # One directive per record keeps lines readable; records are "; "-separated.
    chunks = text.split("; ")
    for i, chunk in enumerate(chunks):
        body = chunk + ("; " if i < len(chunks) - 1 else "")
        escaped = body.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'.ascii "{escaped}"')
    lines += ["", "HALT", ""]
    return "\n".join(lines)
