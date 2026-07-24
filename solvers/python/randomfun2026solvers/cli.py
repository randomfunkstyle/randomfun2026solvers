"""CLI entrypoint for the worker's external solver script contract."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

from randomfun2026solvers.dispatch import SolverError, run_solver
from randomfun2026solvers.interactive import InteractiveError, run_interactive_solver


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./solve",
        description="Run one randomfun2026 solver (batch or interactive).",
    )
    parser.add_argument("--solver", required=True)
    parser.add_argument("--mode", choices=("batch", "interactive"), default="batch")
    parser.add_argument("--input", required=True, type=Path)
    # Required in batch mode only; interactive mode has no output file (§3.2).
    parser.add_argument("--output", type=Path)
    return parser


def _read_input(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SolverError(f"input file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SolverError(f"input file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SolverError("input envelope must be a JSON object")
    return data


def _write_output(path: Path, solution: bytes, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "solution_b64": base64.b64encode(solution).decode(),
        "meta": meta,
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        envelope = _read_input(args.input)
        if args.mode == "interactive":
            if args.output is not None:
                raise SolverError("--output is not allowed in interactive mode")
            run_interactive_solver(args.solver, envelope, sys.stdin, sys.stdout, _log)
        else:
            if args.output is None:
                raise SolverError("--output is required in batch mode")
            solution, meta = run_solver(args.solver, envelope)
            _write_output(args.output, solution, meta)
    except InteractiveError as exc:
        print(f"interactive session failed: {exc}", file=sys.stderr)
        return 1
    except SolverError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - solver bugs should fail the run clearly
        print(f"solver failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
