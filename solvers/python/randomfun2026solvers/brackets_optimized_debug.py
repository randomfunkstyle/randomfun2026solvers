#!/usr/bin/env python3
"""Derive debug sidecars for an optimizer-produced ``brackets`` grid."""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manparse import Program, Room, parse_program


def _largest_compute(program: Program) -> Room:
    return max(
        (room for room in program.rooms if room.kind == "compute"),
        key=lambda room: room.width * room.height,
    )


def _mark_run(
    dbg: DebugMap,
    rows: list[str],
    name: str,
    needle: str,
    *,
    note: str,
) -> None:
    for y, row in enumerate(rows):
        if (x := row.find(needle)) >= 0:
            dbg.lane(
                name,
                [(x, y), (x + len(needle) - 1, y)],
                note=note,
                kind="control",
            )
            return
    raise ValueError(f"optimized grid no longer contains {needle!r}")


def debug_map(rows: list[str], program: Program) -> DebugMap:
    dbg = DebugMap("brackets — submitted 63x15 optimizer result")
    worker = _largest_compute(program)
    dbg.region(
        "parser worker",
        worker.min_[0],
        worker.min_[1],
        worker.width,
        worker.height,
        note="Arithmetic classifier, shared push/pop handlers, EOS, and control loop.",
        tags=["compute", "optimized"],
    )

    for room in program.rooms:
        if room.id == worker.id:
            continue
        name = {
            "input": "input",
            "output": "output",
            "compute": "register-ring relay",
        }.get(room.kind, room.kind)
        dbg.region(
            name,
            room.min_[0],
            room.min_[1],
            room.width,
            room.height,
            note=(
                "Persistent [packed stack, position] turnaround."
                if room.kind == "compute"
                else f"{room.kind.title()} room."
            ),
            tags=[room.kind, "optimized"],
        )

    _mark_run(
        dbg,
        rows,
        "arithmetic classifier",
        "rM`32`W/W+N&X",
        note="char/32 identifies family; the remainder expression selects open/close.",
    )
    _mark_run(
        dbg,
        rows,
        "shared pop",
        "WM3W%MrWsWM3W/srsr~XrXr",
        note="Convert family to base-3 tag, compare stack top, and pop.",
    )
    _mark_run(
        dbg,
        rows,
        "shared push",
        "WM3W%MrW+++srM1+sm",
        note="Convert family to tag, push, advance position, and decrement count.",
    )

    compute_ids = {room.id for room in program.rooms if room.kind == "compute"}
    ring_pipes = [
        pipe
        for pipe in program.pipes
        if pipe.src in compute_ids and pipe.dst in compute_ids
    ]
    for index, pipe in enumerate(ring_pipes, start=1):
        dbg.lane(
            f"ring leg {index}",
            [pipe.cells[0], pipe.cells[-1]],
            note=f"Packed-stack state ring, capacity {len(pipe.cells)}.",
            kind="pipe",
        )

    dbg.scenario(
        "maximum balanced depth",
        "64 "
        + " ".join(
            map(
                str,
                [40, 91, 123] + [40] * 29 + [41] * 29 + [125, 93, 41],
            )
        ),
        0,
        8_000,
        watch=["arithmetic classifier", "shared push", "shared pop"],
        note="Exercises the legal depth-32 and n=64 bounds.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = args.man.read_text(encoding="utf-8").rstrip("\n").split("\n")
    program = parse_program(args.man)
    dbg = debug_map(rows, program)
    for path in (args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
