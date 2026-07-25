"""Python wrapper around the ``littleman/lm.mjs`` Node CLI.

The CLI drives the reference wasm interpreter (``littleman.wasm``) for the ICFP
2026 "littleman" language. This module shells out to it with ``--json`` and
parses the snapshot into pydantic models, giving a typed Python API plus a
mirrored ``python -m littleman_tools.runner run/tick ...`` CLI.

The wrapper does no interpretation itself — it is a thin, typed front-end over
the exact same engine and the exact same ``run`` / ``tick`` semantics as the CLI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Littleman",
    "LittlemanError",
    "Vec2",
    "Runner",
    "Pipe",
    "PipeValue",
    "Room",
    "Display",
    "FrameJudge",
    "Box",
    "PipeSeg",
    "PipeGeom",
    "Analysis",
    "Entities",
    "Fatal",
    "Snapshot",
    "DisplayRun",
    "render_ascii",
    "summarize",
    "main",
]

# Default location of the Node CLI, relative to this file.
_DEFAULT_SCRIPT = Path(__file__).resolve().parents[1] / "littleman" / "lm.mjs"


# ── models ──────────────────────────────────────────────────────────────────
class _Model(BaseModel):
    """Base: allow snake_case attrs with camelCase aliases, ignore unknown keys."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Vec2(_Model):
    x: int
    y: int

    @model_validator(mode="before")
    @classmethod
    def _from_seq(cls, value: Any) -> Any:
        # The engine emits coordinates as ``[x, y]`` arrays.
        if isinstance(value, (list, tuple)):
            return {"x": value[0], "y": value[1]}
        return value

    def as_tuple(self) -> tuple[int, int]:
        return (self.x, self.y)


class Runner(_Model):
    id: int
    pos: Vec2
    dir: Vec2
    halted: bool = False
    a: int = 0
    b: int = 0
    backpack: int = 0


class PipeValue(_Model):
    """A value in transit inside a pipe: its buffer slot ``index`` and ``value``."""

    index: int
    value: int


class Pipe(_Model):
    id: int
    path: list[Vec2] = Field(default_factory=list)
    values: list[PipeValue] = Field(default_factory=list)
    src: int | None = None
    dst: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("path", "values"))


class Room(_Model):
    id: int
    min_: Vec2 = Field(alias="min")
    max_: Vec2 = Field(alias="max")
    runners: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("runners",))


class Display(_Model):
    """An LM-75 panel: both buffers, the cursor, and how many frames it committed.

    ``front`` is the *current* buffer (what the judge compares), ``back`` the
    *next* one being composed; both are ``w * h`` colour values in row-major
    order. ``frames`` is the **commit counter**, not a list of frames — the engine
    keeps no history (see :class:`FrameJudge` for the streaming verdict).
    """

    id: int
    min_: Vec2 = Field(alias="min")
    max_: Vec2 = Field(alias="max")
    w: int = 0
    h: int = 0
    front: list[int] = Field(default_factory=list)
    back: list[int] = Field(default_factory=list)
    cursor: int | None = None
    frames: int = 0

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("front", "back"))

    def rows(self, *, buffer: str = "front") -> list[str]:
        """One hex digit per pixel, one string per row — the JSON frame format."""
        flat = self.front if buffer == "front" else self.back
        return [
            "".join(f"{p:x}" for p in flat[y * self.w : (y + 1) * self.w]) for y in range(self.h)
        ]


class FrameJudge(_Model):
    """The engine's streaming display verdict, when ``judge`` was given frames.

    ``matched`` counts committed frames that equalled the next expected one, in
    order; the run passes when ``matched == total``. ``mismatch`` reports the
    first offending frame (``index`` plus the ``got`` buffer).
    """

    matched: int = 0
    total: int = 0
    mismatch: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.matched == self.total


class Box(_Model):
    """A bare rectangle ``[min, max]`` (inclusive), as ``analyze`` reports rooms."""

    min_: Vec2 = Field(alias="min")
    max_: Vec2 = Field(alias="max")


class PipeSeg(_Model):
    """One cell of a pipe path: its ``pos`` and the flow ``dir`` there."""

    pos: Vec2
    dir: Vec2


class PipeGeom(_Model):
    """A pipe from room index ``src`` to room index ``dst`` (from ``analyze``)."""

    path: list[PipeSeg] = Field(default_factory=list)
    src: int | None = None
    dst: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("path",))


class Analysis(_Model):
    """Structural analysis of a grid: room boxes, pipe geometry+connectivity, displays."""

    type: str | None = None
    rooms: list[Box] = Field(default_factory=list)
    pipes: list[PipeGeom] = Field(default_factory=list)
    displays: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("rooms", "pipes", "displays"))


class Entities(_Model):
    runners: list[Runner] = Field(default_factory=list)
    pipes: list[Pipe] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    displays: list[Display] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _null_lists(cls, data: Any) -> Any:
        return _nulls_to_lists(data, ("runners", "pipes", "rooms", "displays"))


class Fatal(_Model):
    reason: str
    pos: Vec2 | None = None
    cell: str | None = None
    value: int | None = None


class Snapshot(_Model):
    """One engine state (from ``load``/``step``/``stepN`` — i.e. a CLI ``--json`` result)."""

    type: str | None = None
    entities: Entities = Field(default_factory=Entities)
    output: list[int] = Field(default_factory=list)
    halted: bool = False
    reason: str | None = None
    step: int = 0
    cursor: int | None = None
    history: int | None = None
    input_released: int | None = Field(default=None, alias="inputReleased")
    input_read: int | None = Field(default=None, alias="inputRead")
    output_settled: bool | None = Field(default=None, alias="outputSettled")
    frame_committed: bool | None = Field(default=None, alias="frameCommitted")
    frame_judge: FrameJudge | None = Field(default=None, alias="frameJudge")
    fatal: Fatal | None = None

    @model_validator(mode="before")
    @classmethod
    def _null_output(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("output") is None:
            data = {**data, "output": []}
        return data

    @property
    def ok(self) -> bool:
        """True when the program finished cleanly (halted, no fatal error)."""
        return self.halted and self.fatal is None


class DisplayRun(_Model):
    """One display-judged case replayed: the frames it committed, and at which tick.

    Frames are rows of hex digits, one character per pixel — the shape the problem
    JSONs use, so they compare verbatim against a round's ``frames``. ``ticks[i]``
    is the tick frame ``i`` was committed, which is what a display problem is scored
    on (``GRADING.md``: "until your final frame matches").

    :class:`FrameJudge` is the other half of the same story: it is the engine's own
    verdict on whether the frames were *right*, where this carries the frames and
    the ticks so a caller can score them.
    """

    name: str = "?"
    frames: list[list[str]] = Field(default_factory=list)
    ticks: list[int] = Field(default_factory=list)
    output: list[int] = Field(default_factory=list)
    fatal: str | None = None
    width: int = 0
    height: int = 0


def _nulls_to_lists(data: Any, keys: Sequence[str]) -> Any:
    """Replace JSON ``null`` with ``[]`` for the given keys (engine emits null for empties)."""
    if isinstance(data, dict):
        patched = {k: v for k, v in data.items()}
        for k in keys:
            if patched.get(k) is None:
                patched[k] = []
        return patched
    return data


# ── errors ──────────────────────────────────────────────────────────────────
class LittlemanError(RuntimeError):
    """A load/usage error from the CLI (a runtime *fatal* is reported on ``Snapshot.fatal``)."""

    def __init__(self, message: str, *, pos: tuple[int, int] | None = None) -> None:
        super().__init__(message)
        self.pos = pos


_ERR_RE = re.compile(r"^error:\s*(?P<msg>.*?)(?:\s*\(pos\s*(?P<pos>\[[^\]]*\])\s*\))?\s*$")


def _parse_error(stderr: str) -> LittlemanError:
    for line in stderr.splitlines():
        m = _ERR_RE.match(line.strip())
        if m:
            pos: tuple[int, int] | None = None
            if m.group("pos"):
                try:
                    arr = json.loads(m.group("pos"))
                    if isinstance(arr, list) and len(arr) >= 2:
                        pos = (int(arr[0]), int(arr[1]))
                except (ValueError, TypeError):
                    pos = None
            return LittlemanError(m.group("msg") or "littleman error", pos=pos)
    return LittlemanError((stderr.strip() or "littleman CLI failed").splitlines()[-1])


# ── client ──────────────────────────────────────────────────────────────────
class Littleman:
    """Runs ``.man`` programs via the Node CLI, returning :class:`Snapshot` models.

    ``program`` arguments accept either a :class:`pathlib.Path` / ``os.PathLike``
    (an existing ``.man`` file) or a ``str`` (inline program source, run from a
    temp file). ``input`` accepts a whitespace string or a sequence of ints.
    """

    def __init__(self, *, script: str | Path | None = None, node: str | None = None) -> None:
        self.script = Path(script or os.environ.get("LM_SCRIPT") or _DEFAULT_SCRIPT)
        self.node = node or os.environ.get("LM_NODE") or "node"

    # public API ---------------------------------------------------------------
    def run(
        self,
        program: str | os.PathLike[str],
        *,
        input: str | Sequence[int] | None = None,
        max_ticks: int | None = None,
    ) -> Snapshot:
        """Execute to completion; returns the final :class:`Snapshot`."""
        flags: list[str] = []
        if max_ticks is not None:
            flags += ["--max-ticks", str(max_ticks)]
        return self._invoke("run", program, input=input, extra=flags)

    def tick(
        self,
        program: str | os.PathLike[str],
        n: int = 1,
        *,
        input: str | Sequence[int] | None = None,
    ) -> Snapshot:
        """Advance ``n`` ticks from the start; returns the resulting :class:`Snapshot`."""
        return self._invoke("tick", program, input=input, extra=[str(n)])

    def analyze(self, program: str | os.PathLike[str]) -> Analysis:
        """Structural analysis of the grid: rooms, pipes (with connectivity), displays."""
        data = self._run_json("analyze", program)
        return Analysis.model_validate(data)

    def route(self, program: str | os.PathLike[str], x: int, y: int) -> list[Vec2]:
        """The pipe cells a send/recv instruction at ``(x, y)`` binds to (nearest-pipe).

        Returns the ordered cells of the targeted pipe, or ``[]`` if the cell
        binds to no pipe. This is the oracle for proving a layout transform did
        not silently re-bind a send/recv.
        """
        data = self._run_json("route", program, extra=[str(x), str(y)])
        return [Vec2.model_validate(c) for c in (data.get("cells") or [])]

    def judge(
        self,
        program: str | os.PathLike[str],
        *,
        input: str | Sequence[int] | None = None,
        expected: str | Sequence[int] | None = None,
        frames: Any | None = None,
        max_ticks: int | None = None,
    ) -> Snapshot:
        """Run with engine-side round-gating (``expected`` withholds later rounds).

        Returns the settle :class:`Snapshot` — ``step`` is the precise
        final-output tick and ``output`` the emitted values. Rounds in ``input``
        / ``expected`` are separated by ``/``.

        ``frames`` is a display problem's expected frames, **nested per round**:
        ``[[[row, ...], ...], ...]`` — exactly the shape ``rounds[i]["frames"]``
        has in ``tasks/problems/*.json``. The engine then compares each committed
        frame against the next expected one and reports
        :attr:`Snapshot.frame_judge`.
        """
        extra: list[str] = []
        exp = _input_to_str(expected)
        if exp is not None:
            extra += ["--expected", exp]
        if frames is not None:
            extra += ["--frames", json.dumps(frames)]
        if max_ticks is not None:
            extra += ["--max-ticks", str(max_ticks)]
        return self._invoke("judge", program, input=input, extra=extra)

    def display_frames(
        self,
        program: str | os.PathLike[str],
        cases: Sequence[dict[str, Any]],
        *,
        max_ticks: int | None = None,
    ) -> list[DisplayRun]:
        """Replay display-judged ``cases`` and return the frames each one commits.

        :meth:`judge` with ``frames`` answers *did it pass*; this answers *at which
        tick*, which the settle loop cannot: a display problem emits no program
        output, so there is nothing for the loop to count, and a good solver
        typically never halts. This drives ``tools/display-frames.mjs``, which steps
        with the wasm's ``stopOnFrame`` flag and snapshots the panel at every SWAP —
        and hands the expected frames to ``load`` so the engine gates the rounds
        itself, exactly as the judge does.

        ``cases`` are ``publicTestData`` entries; each must carry the ``frames`` it
        expects, since that is how many commits to wait for.
        """
        tool = self.script.parent / "tools" / "display-frames.mjs"
        if not tool.exists():
            raise LittlemanError(f"display-frames.mjs not found beside {self.script}")
        path, cleanup = _resolve_program(program)
        spec = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(list(cases), spec)
            spec.close()
            proc = subprocess.run(
                [self.node, str(tool), str(path), spec.name, str(max_ticks or 5_000_000)],
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            Path(spec.name).unlink(missing_ok=True)
            if cleanup is not None:
                cleanup()
        if not proc.stdout.strip():
            raise _parse_error(proc.stderr)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LittlemanError(f"could not parse display-frames JSON: {exc}") from exc
        return [DisplayRun.model_validate(c) for c in (data.get("cases") or [])]

    # internals ------------------------------------------------------------------
    def _invoke(
        self,
        cmd: str,
        program: str | os.PathLike[str],
        *,
        input: str | Sequence[int] | None,
        extra: Sequence[str],
    ) -> Snapshot:
        return Snapshot.model_validate(self._run_json(cmd, program, input=input, extra=extra))

    def _run_json(
        self,
        cmd: str,
        program: str | os.PathLike[str],
        *,
        input: str | Sequence[int] | None = None,
        extra: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Shell out to a CLI subcommand with ``--json`` and return the parsed object."""
        path, cleanup = _resolve_program(program)
        try:
            argv = [self.node, str(self.script), cmd, str(path), *extra, "--json"]
            in_str = _input_to_str(input)
            if in_str is not None:
                argv += ["--input", in_str]
            proc = subprocess.run(argv, text=True, capture_output=True, check=False)
        finally:
            if cleanup is not None:
                cleanup()

        stdout = proc.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise LittlemanError(
                    f"could not parse CLI JSON: {exc}; stderr: {proc.stderr.strip()}"
                ) from exc

        # No JSON on stdout → a load/usage error (or a missing engine).
        raise _parse_error(proc.stderr)


def _resolve_program(program: str | os.PathLike[str]) -> tuple[Path, Any]:
    """Return (path, cleanup). A str is inline source → temp file; a PathLike is a file."""
    if isinstance(program, str):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".man", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(program)
        finally:
            tmp.close()
        path = Path(tmp.name)
        return path, lambda: path.unlink(missing_ok=True)
    return Path(os.fspath(program)), None


def _input_to_str(input: str | Sequence[int] | None) -> str | None:
    if input is None:
        return None
    if isinstance(input, str):
        return input
    return " ".join(str(int(v)) for v in input)


# ── render helpers (parity with lm.mjs) ──────────────────────────────────────
def _dir_glyph(d: Vec2) -> str:
    if d.x > 0:
        return ">"
    if d.x < 0:
        return "<"
    if d.y > 0:
        return "v"
    if d.y < 0:
        return "^"
    return "?"


def render_ascii(source: str, snap: Snapshot) -> str:
    """Overlay live (non-halted) runners as ``@`` onto the program source grid."""
    text = source[:-1] if source.endswith("\n") else source
    grid = [list(row) for row in text.split("\n")]
    for r in snap.entities.runners:
        if r.halted:
            continue
        x, y = r.pos.x, r.pos.y
        if y < 0 or y >= len(grid):
            continue
        row = grid[y]
        while len(row) <= x:
            row.append(" ")
        row[x] = "@"
    return "\n".join("".join(row) for row in grid)


def summarize(snap: Snapshot) -> str:
    """The ``tick …`` / per-runner / ``output:`` block, mirroring the CLI."""
    lines = [
        f"tick {snap.step}  halted:{str(snap.halted).lower()}"
        + (f"  ({snap.reason})" if snap.reason else "")
    ]
    if snap.fatal:
        f = snap.fatal
        loc = f" at [{f.pos.x},{f.pos.y}]" if f.pos else ""
        cell = f" cell='{f.cell}'" if f.cell is not None else ""
        lines.append(f"FATAL {f.reason}{loc}{cell}")
    for r in snap.entities.runners:
        pos = f"({r.pos.x},{r.pos.y})"
        halted = " HALTED" if r.halted else ""
        lines.append(
            f"runner{r.id}  A={r.a} B={r.b} BP={r.backpack} "
            f"dir={_dir_glyph(r.dir)} pos={pos}{halted}"
        )
    lines.append(f"output: {' '.join(str(v) for v in snap.output)}")
    return "\n".join(lines)


# ── CLI (python -m randomfun2026solvers.littleman) ────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="littleman_tools.runner",
        description="Run littleman .man programs via the Node/wasm engine.",
    )
    parser.add_argument("--node", help="node executable (default: $LM_NODE or 'node')")
    parser.add_argument("--script", help="path to lm.mjs (default: $LM_SCRIPT or bundled)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute to completion")
    p_run.add_argument("file", help="path to a .man file")
    p_run.add_argument("--input", help="whitespace-separated integers for the input room")
    p_run.add_argument("--json", action="store_true", help="print the snapshot JSON")
    p_run.add_argument("--max-ticks", type=int, dest="max_ticks", help="safety cap")

    p_tick = sub.add_parser("tick", help="advance n ticks from the start")
    p_tick.add_argument("file", help="path to a .man file")
    p_tick.add_argument("n", nargs="?", type=int, default=1, help="ticks to advance (default 1)")
    p_tick.add_argument("--input", help="whitespace-separated integers for the input room")
    p_tick.add_argument("--json", action="store_true", help="print the snapshot JSON")

    args = parser.parse_args(argv)
    lm = Littleman(script=args.script, node=args.node)

    try:
        if args.cmd == "run":
            snap = lm.run(Path(args.file), input=args.input, max_ticks=args.max_ticks)
            if args.json:
                print(snap.model_dump_json(indent=2, by_alias=True))
            else:
                if snap.output:
                    print(" ".join(str(v) for v in snap.output))
                why = (
                    f"fatal:{snap.fatal.reason}"
                    if snap.fatal
                    else (snap.reason or ("output-settled" if snap.output_settled else "stopped"))
                )
                print(f"# halted after {snap.step} tick(s) ({why})", file=sys.stderr)
            if snap.fatal:
                f = snap.fatal
                loc = f" at [{f.pos.x},{f.pos.y}]" if f.pos else ""
                print(f"fatal: {f.reason}{loc}", file=sys.stderr)
                return 1
            return 0

        # tick
        source = Path(args.file).read_text(encoding="utf-8")
        snap = lm.tick(Path(args.file), args.n, input=args.input)
        if args.json:
            print(snap.model_dump_json(indent=2, by_alias=True))
        else:
            print(render_ascii(source, snap) + "\n\n" + summarize(snap))
        return 0
    except LittlemanError as exc:
        pos = f" (pos {list(exc.pos)})" if exc.pos else ""
        print(f"error: {exc}{pos}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
