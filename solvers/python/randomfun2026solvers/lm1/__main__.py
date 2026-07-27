"""CLI: ``python -m randomfun2026solvers.lm1`` — assemble, list and grade programs.

Commands (all prefixed by ``python -m randomfun2026solvers.lm1``):

- ``list`` — the ISA table (with micro-programs) and every shipped program
- ``asm <slug|path>`` — assemble and print the word ring plus a listing
- ``grade [slug ...]`` — run against the problems' public test data (default: all)
- ``run <slug|path> --input "4"`` — one ad-hoc run
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .asm import assemble_file
from .emulator import Emulator, Round
from .isa import DEFAULT_ISA, LM1_V1
from .programs import DEMOS, available, load, problem_of, rounds_for_problem

MAX_INSTRUCTIONS = 3_000_000


def _grade(stem: str) -> tuple[int, int, list[int], int]:
    """(passed, total, per-case ticks, skip ticks) for one program."""
    prog = load(stem)
    cases = rounds_for_problem(problem_of(stem))
    passed, ticks, skipped = 0, [], 0
    for name, rounds in cases:
        res = Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS)
        expected = tuple(v for r in rounds for v in r.expected)
        if res.output == expected:
            passed += 1
            ticks.append(res.ticks)
            skipped += res.words_skipped * 8
        else:
            print(f"    FAIL {name}: got {res.output[:12]} want {expected[:12]} ({res.reason})")
    return passed, len(cases), ticks, skipped


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="randomfun2026solvers.lm1")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the ISA table and the shipped programs")
    p_asm = sub.add_parser("asm", help="assemble a program and print its listing")
    p_asm.add_argument("program", help="a program slug or a path to a .asm file")
    p_grade = sub.add_parser("grade", help="run programs against the public test data")
    p_grade.add_argument("program", nargs="*", help="slugs (default: all)")
    p_run = sub.add_parser("run", help="run one program on ad-hoc input")
    p_run.add_argument("program")
    p_run.add_argument("--input", default="", help="whitespace-separated integers")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        print(
            f"ISA {DEFAULT_ISA.name} ({len(DEFAULT_ISA)} opcodes, "
            f"decode depth {DEFAULT_ISA.decode_bits}; "
            f"{len(LM1_V1)} of them are ARCH.md v1)"
        )
        for op in DEFAULT_ISA:
            tag = "ext" if op.ext else "v1 "
            micro = " ".join(op.micro) or "-"
            print(f"  {op.code:3d} {tag} {op.mnemonic:<5} {op.operands}  {op.description}")
            print(f"           micro: {micro}")
        print("\nprograms:")
        for stem in available():
            print(f"  {load(stem).report()}")
        return 0

    if args.cmd == "asm":
        prog = load(args.program) if args.program in available() else assemble_file(args.program)
        print(prog.report())
        print(prog.listing())
        print("words:", " ".join(str(w) for w in prog.words))
        return 0

    if args.cmd == "run":
        prog = load(args.program) if args.program in available() else assemble_file(args.program)
        values = [int(v) for v in args.input.split()]
        res = Emulator(prog).run([Round(input=tuple(values))], max_instructions=MAX_INSTRUCTIONS)
        print(" ".join(str(v) for v in res.output))
        print(f"# {res.instructions} instructions, ~{res.ticks} ticks ({res.reason})")
        return 0

    # Demos have no public cases under their borrowed slug (see programs.DEMOS);
    # grading one against them is meaningless, so the default list skips them.
    # Naming a demo explicitly still grades it, garbage-in and all.
    stems = args.program or [s for s in available() if s not in DEMOS]
    failures = 0
    for stem in stems:
        passed, total, ticks, skip_ticks = _grade(stem)
        prog = load(stem)
        avg = sum(ticks) / len(ticks) if ticks else 0
        share = 100 * skip_ticks / sum(ticks) if ticks else 0
        print(
            f"{stem:<16} {passed}/{total} cases  P={prog.P:<5} "
            f"ring={prog.ring_capacity[0]}..{prog.ring_capacity[1]:<5} "
            f"avg~{avg:.0f} ticks  max~{max(ticks, default=0)}  "
            f"jump overhead {share:.0f}%  ext={','.join(prog.ext_ops) or '-'}"
        )
        failures += total - passed
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
