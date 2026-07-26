from __future__ import annotations

import base64
import io
import json
import subprocess
from pathlib import Path


def _envelope(
    payload: dict,
    *,
    solver: str = "shell-smoke",
    contest_key: str = "demo_sudoku",
    external_id: str = "sudoku-000",
) -> dict:
    return {
        "solver": solver,
        "run_id": 1,
        "solver_commit": {"ref": "main", "hash": "local"},
        "task": {
            "contest_key": contest_key,
            "external_id": external_id,
            "payload_b64": base64.b64encode(json.dumps(payload).encode()).decode(),
            "content_type": "application/json",
        },
    }


def _run_solve(tmp_path: Path, solver: str, payload: dict) -> subprocess.CompletedProcess[str]:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "nested" / "output.json"
    input_path.write_text(json.dumps(_envelope(payload, solver=solver)), encoding="utf-8")
    return subprocess.run(
        ["./solve", "--solver", solver, "--input", str(input_path), "--output", str(output_path)],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def test_shell_smoke_writes_output_envelope(tmp_path: Path) -> None:
    proc = _run_solve(tmp_path, "shell-smoke", {"unused": True})
    assert proc.returncode == 0, proc.stderr
    out = json.loads((tmp_path / "nested" / "output.json").read_text(encoding="utf-8"))
    assert json.loads(base64.b64decode(out["solution_b64"]).decode()) == {"smoke": True}
    assert out["meta"]["solver"] == "shell-smoke"
    assert out["meta"]["entrypoint"] == "bash-cli"


def test_sudoku_solves_puzzle(tmp_path: Path) -> None:
    givens = "530070000600195000098000060800060003400803001700020006060000280000419005000080079"
    proc = _run_solve(tmp_path, "sudoku", {"size": 9, "givens": givens})
    assert proc.returncode == 0, proc.stderr
    out = json.loads((tmp_path / "nested" / "output.json").read_text(encoding="utf-8"))
    assert json.loads(base64.b64decode(out["solution_b64"]).decode()) == {
        "grid": (
            "534678912672195348198342567859761423426853791713924856961537284287419635345286179"
        )
    }
    assert out["meta"]["solver"] == "sudoku"


def _decode_b64_json(value: str) -> dict:
    return json.loads(base64.b64decode(value).decode())


def test_probe_interactive_reconstructs_secret() -> None:
    """Drive the probe solver with a scripted worker over in-memory streams."""
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "solvers" / "python"))
    from randomfun2026solvers.interactive import run_interactive_solver

    secret = "314159"
    envelope = _envelope(
        {"kind": "reconstruct-number", "digits": len(secret)},
        solver="probe",
        contest_key="demo_probe",
        external_id="probe-000",
    )

    # Scripted worker responses: one observation per probe, then a verdict.
    responses = []
    for i, digit in enumerate(secret):
        raw = base64.b64encode(json.dumps({"digit": digit}).encode()).decode()
        responses.append(
            {
                "t": "observation",
                "raw_b64": raw,
                "query_count": i + 1,
                "penalty": i + 1,
                "score": None,
                "done": False,
            }
        )
    responses.append({"t": "verdict", "correct": True, "score": 1.0, "raw": {}})

    infile = io.StringIO("".join(json.dumps(r) + "\n" for r in responses))
    outfile = io.StringIO()
    logs: list[str] = []

    run_interactive_solver("probe", envelope, infile, outfile, logs.append)

    emitted = [json.loads(line) for line in outfile.getvalue().splitlines()]

    # 6 steps (indices 0..5) + 1 guess + 1 done.
    assert len(emitted) == len(secret) + 2
    steps = emitted[: len(secret)]
    for i, frame in enumerate(steps):
        assert frame["t"] == "step"
        assert _decode_b64_json(frame["action_b64"]) == {"index": i}

    guess = emitted[len(secret)]
    assert guess["t"] == "guess"
    assert _decode_b64_json(guess["answer_b64"]) == {"number": secret}

    assert emitted[-1] == {"t": "done"}
    assert logs, "solver should log progress to the log sink"


def test_probe_interactive_fatal_error_raises() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "solvers" / "python"))
    from randomfun2026solvers.interactive import InteractiveError, run_interactive_solver

    envelope = _envelope(
        {"kind": "reconstruct-number", "digits": 6},
        solver="probe",
        contest_key="demo_probe",
        external_id="probe-000",
    )
    infile = io.StringIO(json.dumps({"t": "error", "msg": "boom", "fatal": True}) + "\n")
    outfile = io.StringIO()

    try:
        run_interactive_solver("probe", envelope, infile, outfile, lambda _m: None)
    except InteractiveError:
        pass
    else:
        raise AssertionError("expected InteractiveError on a fatal error frame")


def test_probe_interactive_end_to_end_via_solve(tmp_path: Path) -> None:
    """Run ./solve --solver probe --mode interactive with scripted stdin frames."""
    secret = "271828"
    envelope = _envelope(
        {"kind": "reconstruct-number", "digits": len(secret)},
        solver="probe",
        contest_key="demo_probe",
        external_id="probe-001",
    )
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(envelope), encoding="utf-8")

    responses = []
    for i, digit in enumerate(secret):
        raw = base64.b64encode(json.dumps({"digit": digit}).encode()).decode()
        responses.append(
            {
                "t": "observation",
                "raw_b64": raw,
                "query_count": i + 1,
                "penalty": i + 1,
                "done": False,
            }
        )
    responses.append({"t": "verdict", "correct": True, "score": 1.0, "raw": {}})
    stdin_text = "".join(json.dumps(r) + "\n" for r in responses)

    proc = subprocess.run(
        ["./solve", "--solver", "probe", "--mode", "interactive", "--input", str(input_path)],
        cwd=Path(__file__).parents[1],
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    emitted = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(emitted) == len(secret) + 2
    assert _decode_b64_json(emitted[len(secret)]["answer_b64"]) == {"number": secret}
    assert emitted[-1] == {"t": "done"}


def test_bad_solver_name_fails_without_output(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(
        json.dumps(_envelope({"unused": True}, solver="missing")),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            "./solve",
            "--solver",
            "missing",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "unknown solver" in proc.stderr
    assert not output_path.exists()
