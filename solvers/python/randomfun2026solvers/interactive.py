"""Interactive ("chatty") solver protocol over stdio (contracts.md §3.2).

The solver never opens a socket to the contest server; it speaks a synchronous
newline-delimited JSON protocol to the worker over stdio:

- stdout carries protocol frames only (one compact JSON object per line);
- stderr is free-form solver logs.

Strictly synchronous: write exactly one request frame, then read exactly one
response frame, before the next request.

The core loop (:func:`run_interactive_solver`) takes explicit input/output text
streams plus a log callback, so a test can drive it with scripted worker frames
without touching ``sys.stdin``/``sys.stdout``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, TextIO

from randomfun2026solvers.dispatch import SolverError, decode_task_payload

# A log sink: free-form solver progress messages routed to stderr.
LogFn = Callable[[str], None]


class InteractiveError(RuntimeError):
    """A fatal transport/adapter problem during an interactive session.

    Raised when the worker sends a fatal ``error`` frame or closes the stream
    unexpectedly. The CLI maps this to a nonzero exit code.
    """


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class Session:
    """Worker-proxied interactive session: one request out, one response in.

    Wraps the JSON-Lines transport so solver logic reads/writes opaque bytes
    and stays year-agnostic. Frames are written compactly, one per line, with an
    explicit flush so the worker can proxy each request immediately.
    """

    def __init__(self, infile: TextIO, outfile: TextIO, log: LogFn) -> None:
        self._in = infile
        self._out = outfile
        self._log = log

    def _write(self, frame: dict[str, Any]) -> None:
        self._out.write(json.dumps(frame, separators=(",", ":")) + "\n")
        self._out.flush()

    def _read(self) -> dict[str, Any]:
        line = self._in.readline()
        if not line:
            raise InteractiveError("worker closed the stream before a response")
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InteractiveError(f"worker sent invalid JSON frame: {exc}") from exc
        if not isinstance(frame, dict):
            raise InteractiveError("worker frame must be a JSON object")
        if frame.get("t") == "error":
            msg = frame.get("msg", "unknown error")
            if frame.get("fatal"):
                raise InteractiveError(f"fatal worker error: {msg}")
            raise InteractiveError(f"worker error: {msg}")
        return frame

    def step(self, action: bytes) -> dict[str, Any]:
        """Send a ``step`` and return the ``observation`` frame."""
        self._write({"t": "step", "action_b64": _b64(action)})
        frame = self._read()
        if frame.get("t") != "observation":
            raise InteractiveError(f"expected observation, got {frame.get('t')!r}")
        return frame

    def guess(self, answer: bytes) -> dict[str, Any]:
        """Send a ``guess`` and return the ``verdict`` frame."""
        self._write({"t": "guess", "answer_b64": _b64(answer)})
        frame = self._read()
        if frame.get("t") != "verdict":
            raise InteractiveError(f"expected verdict, got {frame.get('t')!r}")
        return frame

    def done(self, solution: bytes | None = None) -> None:
        """End the session. No response follows; the solver then exits 0."""
        frame: dict[str, Any] = {"t": "done"}
        if solution is not None:
            frame["solution_b64"] = _b64(solution)
        self._write(frame)


def _probe(envelope: dict[str, Any], session: Session, log: LogFn) -> None:
    """Reconstruct a hidden D-digit number for the demo_probe contest.

    Probes each index 0..D-1 for its digit, then guesses the assembled number.
    """
    payload = decode_task_payload(envelope)
    kind = payload.get("kind")
    if kind != "reconstruct-number":
        raise SolverError(f"probe solver expects kind=reconstruct-number, got {kind!r}")
    try:
        digits = int(payload["digits"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SolverError(f"probe payload must contain integer digits: {exc}") from exc
    if digits <= 0:
        raise SolverError(f"probe payload digits must be positive, got {digits}")

    log(f"probe: reconstructing {digits}-digit number")
    collected: list[str] = []
    for i in range(digits):
        obs = session.step(json.dumps({"index": i}).encode())
        try:
            raw = json.loads(base64.b64decode(obs["raw_b64"]).decode())
            digit = str(raw["digit"])
        except Exception as exc:  # noqa: BLE001 - one clear error for a malformed observation
            raise SolverError(f"malformed observation at index {i}: {exc}") from exc
        collected.append(digit)
        log(f"probe: index {i} -> {digit}")

    number = "".join(collected)
    log(f"probe: guessing {number}")
    verdict = session.guess(json.dumps({"number": number}).encode())
    log(f"probe: verdict correct={verdict.get('correct')} score={verdict.get('score')}")
    session.done()
    log("probe: done")


InteractiveSolverFn = Callable[[dict[str, Any], Session, LogFn], None]

_INTERACTIVE_SOLVERS: dict[str, InteractiveSolverFn] = {
    "probe": _probe,
}


def run_interactive_solver(
    name: str,
    envelope: dict[str, Any],
    infile: TextIO,
    outfile: TextIO,
    log: LogFn,
) -> None:
    """Drive one interactive solver over the given text streams.

    ``infile``/``outfile`` are the JSON-Lines transport (worker responses in,
    solver requests out). ``log`` receives free-form progress messages. Kept free
    of ``sys.stdin``/``sys.stdout`` so tests can feed scripted frames.
    """
    solver = _INTERACTIVE_SOLVERS.get(name)
    if solver is None:
        known = ", ".join(sorted(_INTERACTIVE_SOLVERS))
        raise SolverError(f"unknown interactive solver {name!r}; known solvers: {known}")
    session = Session(infile, outfile, log)
    solver(envelope, session, log)
