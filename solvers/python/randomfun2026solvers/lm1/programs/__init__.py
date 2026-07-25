"""LM-1 task programs, plus the glue that runs them against the problem JSONs.

Each ``<slug>.asm`` in this directory is a program for the LM-1 machine, written
against the ISA table in :mod:`randomfun2026solvers.lm1.isa`. The slug matches a
problem in ``tasks/problems/``, so :func:`rounds_for_problem` can feed a program
the exact public test data the judge uses.

``ARCH.md`` §4.1 blocks every problem that needs an *array* on the STORE block, but
``machine.py`` now generates that block (the verified rotating tape), so a program
here may index memory through the ``LDA``/``MOVA`` extensions — ``tcp``, ``brackets``
and ``gradebook`` all do (see the step-2 report / ``isa.LM1_EXT``). What is still
missing is a program, not hardware.

The two display-judged problems (``plotter``, ``palette``) live here too: they emit
no program output, so :func:`frames_for_problem` supplies what
:func:`rounds_for_problem` cannot.
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
    "frames_for_problem",
    "display_size",
    "history_lesson_source",
    "palette_source",
]

PROGRAM_DIR = Path(__file__).resolve().parent
PROBLEM_DIR = Path(__file__).resolve().parents[5] / "tasks" / "problems"

#: Programs whose file name is not the problem slug (alternative solutions).
PROBLEM_OF: dict[str, str] = {"triangle-closed": "triangle", "snake-ring": "snake"}


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


def frames_for_problem(slug: str) -> list[tuple[str, list[list[list[str]]]]]:
    """``[(case_name, [round_frames])]`` — the expected panel frames per round.

    The display size lives in ``io.display``; the frames themselves are rows of
    hex digits, one per pixel, exactly as :func:`~..display.frames_from_writes`
    returns them.
    """
    cases = []
    for index, case in enumerate(problem_json(slug).get("publicTestData") or []):
        name = case.get("name") or f"case-{index}"
        raw_rounds = case.get("rounds") or [case]
        cases.append((name, [[list(f) for f in (r.get("frames") or [])] for r in raw_rounds]))
    return cases


def display_size(slug: str) -> tuple[int, int]:
    """The panel resolution a display-judged problem states."""
    panel = (problem_json(slug).get("io") or {}).get("display")
    if not panel:
        raise KeyError(f"{slug} is not a display problem")
    return int(panel["width"]), int(panel["height"])


def palette_source(slug: str = "palette") -> str:
    """Regenerate ``palette.asm`` from the problem JSON.

    Sixteen frames, each the whole panel in one colour. The loop is *rolled over
    colours* and *unrolled over pixels*, which is the cheap shape here: the DATA
    port advances the cursor itself, so ``width * height`` bare ``DSPD``s paint a
    frame with no counter, no test and no STORE traffic — and ``DSPD`` preserves
    ACC (the ``W``/``s``/``W`` sandwich), so the colour is loaded once per frame.
    Rolling the pixels instead would cost four tape accesses per pixel, i.e. ~64x
    the ticks for ~64 fewer ROM words.

    Regenerate with::

        from randomfun2026solvers.lm1.programs import PROGRAM_DIR, palette_source
        (PROGRAM_DIR / "palette.asm").write_text(palette_source())
    """
    width, height = display_size(slug)
    pixels = width * height
    lines = [
        f"; palette — GENERATED from {slug}.json, do not hand-edit.",
        ";",
        "; Sixteen frames, colour 0 through 15, on the "
        f"{width}x{height} LM-75. Uses all three port",
        "; opcodes: DSPA parks the cursor at (0,0), DSPD paints, DSPS commits.",
        ";",
        "; Writing 0 to SWAP commits *and* clears `next` and resets the cursor, so the",
        "; DSPA is strictly redundant — it is here because a display CPU that cannot",
        "; address the panel is not one, and this is the program the hardware is",
        f"; generated from. The {pixels} DSPD writes are unrolled: the DATA port advances the",
        "; cursor by itself, so painting a frame needs no counter and no STORE traffic.",
        ";",
        "; Address 1, not 0: the generated hardware puts the operation in the *sign* of",
        "; the address word, so slot 0 would be ambiguous.",
        "",
        ".equ COLOUR 1",
        "",
        "        LDI 0",
        "        ST  COLOUR",
        "",
        "frame:  LDI 0",
        "        DSPA                ; cursor -> (0, 0)",
        "        LD  COLOUR",
    ]
    for i in range(pixels):
        note = f"                ; pixel {i}" if i in (0, pixels - 1) else ""
        lines.append(f"        DSPD{note}")
    lines += [
        "",
        "        LDI 0",
        "        DSPS                ; commit the frame, clear `next`, home the cursor",
        "",
        "        LD  COLOUR",
        "        ADDI 1",
        "        ST  COLOUR          ; ST preserves ACC, so the test below sees colour + 1",
        "        SUBI 16",
        "        BRZ done",
        "        JMP frame",
        "done:   HALT",
        "",
    ]
    return "\n".join(lines)


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
