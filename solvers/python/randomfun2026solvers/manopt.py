#!/usr/bin/env python3
"""Search the AST for a smaller footprint, reporting *why* each move is possible.

This is the driver that turns the queries in :mod:`manroute` and the edits in
:mod:`manmoves` into a loop: enumerate every move the tree admits, score it on
geometry alone, try the promising ones, and keep what survives validation.

The reporting is the other half of the job and not decoration. Before it moves
anything the driver prints a **dossier** for every block: what the thing is, what
it is holding, what depends on its position, and what would break if it moved.
The reason is that the failure mode here is never a crash — it is a grid that
loads, analyses, and quietly computes the wrong thing — so "what are the
consequences of moving this" has to be answerable *before* the move, not
diagnosed after.

Four move kinds, cheapest first:

``drop-row`` / ``drop-col``
    delete a dead grid line. Free when nothing live sits on it.
``move-room``
    slide a section; every attached pipe is re-routed at no less than its declared
    capacity. This is the one that unlocks lines a cut cannot reach — a blank row
    inside a worker is uncuttable if an IO room's wall happens to lie across it,
    and the fix is to move the room, not to force the cut.
``reroute``
    re-lay one pipe, subject to capacity and the parity rule.

Every accepted move must clear three gates: the grid loads with the same rooms
and pipes, every ``s``/``r`` still binds to the same pipe, and — for the final
candidate only, because it is slow — every public case still passes.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
from pathlib import Path

from .manast import Ast, Refine, parse_ast, render
from .mancompact import _binding_signature
from .manmoves import MoveError, ring_capacity, try_drop, try_squash
from .manparse import parse_program
from .manroute import Plan, Verdict

__all__ = ["Dossier", "dossier", "Move", "search", "describe"]


# ── dossiers: what is this, and what happens if it moves ─────────────────────
@dataclass
class Dossier:
    """Everything a human needs before disturbing one piece of a grid."""

    kind: str  # room | pipe
    ident: str
    what: str  # what it is / what it holds
    where: str
    movable: bool
    consequences: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"{self.ident:9s} {self.kind:5s} {self.where:24s} {self.what}"
        lines = [head]
        for d in self.depends:
            lines.append(f"{'':16s}depends: {d}")
        for c in self.consequences:
            lines.append(f"{'':16s}if moved: {c}")
        return "\n".join(lines)


_ROOM_ROLE = {
    "input": "the one input room; SPEC allows at most one, 3x3, one pipe",
    "output": "the one output room; SPEC allows at most one, 3x3, one pipe",
    "display": "the LM-75 panel; its interior size IS the pixel resolution",
    "compute": "a room a little man walks",
}


def dossier(ast: Ast, *, capacity: dict[tuple[int, ...], int] | None = None) -> list[Dossier]:
    """Build a dossier for every room and pipe in the tree."""
    caps = capacity or {}
    out: list[Dossier] = []
    pipes_of: dict[int, list[int]] = {}
    for p in ast.pipes:
        pipes_of.setdefault(p.src, []).append(p.id)
        pipes_of.setdefault(p.dst, []).append(p.id)

    for room in ast.rooms:
        bw, bh = room.size
        live = sum(len(c.paint()) for c in room.children)
        cons: list[str] = []
        deps: list[str] = []
        attached = pipes_of.get(room.id, [])
        if attached:
            cons.append(
                f"pipes {attached} re-route; each must keep its declared capacity, "
                "and path length between two fixed walls only changes in twos"
            )
        if room.kind in ("input", "output"):
            deps.append(
                "the pipe must still attach with the correct flow direction — SPEC "
                "makes a wrong-direction pipe on an IO room a load error"
            )
            cons.append(
                "every s/r in every OTHER room may re-bind: they take the NEAREST "
                "pipe, so moving this room's pipe changes distances elsewhere"
            )
        if room.kind == "display":
            cons.append("REFUSED: pinned, because its interior size is the resolution")
        if room.kind == "compute" and live:
            cons.append(
                f"{live} live glyphs travel with it; the man's path is preserved "
                "only because every child moves by the same delta"
            )
        out.append(
            Dossier(
                kind="room",
                ident=f"room{room.id}",
                what=f"{_ROOM_ROLE.get(room.kind, room.kind)}; {live} live glyphs",
                where=f"({room.x},{room.y}) {bw}x{bh}",
                movable=not room.pinned,
                consequences=cons,
                depends=deps,
            )
        )

    for pipe in ast.pipes:
        group = next((g for g in caps if pipe.id in g), None)
        need = caps.get(group) if group else pipe.min_capacity
        cons = []
        deps = []
        if pipe.min_capacity is None and need is None:
            cons.append(
                "REFUSED: no declared capacity. Length IS capacity — one value per "
                "cell — so shortening an undeclared pipe could deadlock a ring"
            )
        else:
            # Capacity is a property of the RING, not of one pipe: comparing a
            # single leg against the group's need reports a healthy ring as short.
            have = (
                sum(q.capacity for q in ast.pipes if q.id in group)
                if group
                else pipe.capacity
            )
            scope = f"group {list(group)}" if group else "alone"
            slack = have - (need or 0)
            deps.append(
                f"this leg holds {pipe.capacity}; {scope} holds {have}, needs {need}"
                + (f" — {slack} spare" if slack >= 0 else " — ALREADY SHORT")
            )
            cons.append(
                "shortening past the minimum deadlocks whatever fills it; "
                "lengthening only adds latency"
            )
        if group and len(group) > 1:
            cons.append(
                f"shares a capacity budget with pipes {list(group)}: the ring's "
                f"total must stay >= {caps[group]}, so two cuts cannot both spend "
                "the same slot"
            )
        deps.append(
            f"hands values from room{pipe.src} to room{pipe.dst}; both ends must stay "
            "on those walls with the recorded headings"
        )
        out.append(
            Dossier(
                kind="pipe",
                ident=f"pipe{pipe.id}",
                what=f"{pipe.capacity} cells, room{pipe.src} -> room{pipe.dst}",
                where=f"({pipe.x},{pipe.y})..{pipe.path[-1]}",
                movable=pipe.min_capacity is not None,
                consequences=cons,
                depends=deps,
            )
        )
    return out


def describe(ast: Ast, *, capacity: dict[tuple[int, ...], int] | None = None) -> str:
    w, h = ast.bbox
    lines = [
        f"grid {w}x{h}   factor max(w,h)^2 = {ast.geometry_factor:,}",
        "",
        "BLOCK DOSSIERS — read before moving anything",
    ]
    for d in dossier(ast, capacity=capacity):
        lines.append(d.render())
    return "\n".join(lines)


# ── moves ────────────────────────────────────────────────────────────────────
@dataclass
class Move:
    kind: str
    args: tuple
    verdict: Verdict | None = None

    def __str__(self) -> str:
        return f"{self.kind}{self.args}"


def _try(ast: Ast, move: Move, caps: dict[tuple[int, ...], int]) -> Ast | str:
    """Apply `move` to a copy. Returns the new AST or the refusal reason."""
    trial = copy.deepcopy(ast)
    try:
        if move.kind == "drop-row":
            out, rep = try_drop(trial, "row", move.args[0], capacity=caps)
            if out is None:
                return str(rep)
            return out
        if move.kind == "drop-col":
            out, rep = try_drop(trial, "col", move.args[0], capacity=caps)
            if out is None:
                return str(rep)
            return out
        if move.kind == "move-room":
            plan = Plan(trial)
            v = plan.move_room(*move.args)
            if not v:
                return v.reason
            return plan.ast
        if move.kind == "reroute":
            plan = Plan(trial)
            v = plan.reroute(move.args[0], min_capacity=move.args[1])
            if not v:
                return v.reason
            return plan.ast
        if move.kind == "squash":
            out, report = try_squash(
                trial,
                move.args[0],
                move.args[1],
                move.args[2],
                capacity=caps,
            )
            return out if out is not None else str(report)
        return f"unknown move {move.kind}"
    except (MoveError, Exception) as exc:  # noqa: BLE001 - a refusal is data
        return f"{type(exc).__name__}: {exc}"


def candidates(
    ast: Ast,
    *,
    deltas: int = 3,
    move_set: str = "layout",
) -> list[Move]:
    """Every move worth trying, in rough order of cheapness."""
    w, h = ast.bbox
    out: list[Move] = []
    if move_set in ("layout", "cuts", "all"):
        for y in range(h):
            out.append(Move("drop-row", (y,)))
        for x in range(w):
            out.append(Move("drop-col", (x,)))
    if move_set in ("layout", "all"):
        for room in ast.rooms:
            if room.pinned:
                continue
            for dy in range(-deltas, deltas + 1):
                for dx in range(-deltas, deltas + 1):
                    if (dx, dy) != (0, 0):
                        out.append(Move("move-room", (room.id, dx, dy)))
        for pipe in ast.pipes:
            if pipe.min_capacity is not None:
                out.append(Move("reroute", (pipe.id, pipe.min_capacity)))
    if move_set in ("squash", "all"):
        # The report computes free interior lines without mutating the AST.
        # Each move is still tried by `try_squash`; occupancy is a shortlist,
        # never the verdict.
        from .manfree import squash_report

        for block in squash_report(ast, verify=False):
            room_id = int(block.node.removeprefix("room"))
            for index in block.free_cols:
                out.append(Move("squash", (room_id, "col", index)))
            for index in block.free_rows:
                out.append(Move("squash", (room_id, "row", index)))
    return out


def search(
    ast: Ast,
    *,
    caps: dict[tuple[int, ...], int],
    want_bindings: list,
    rounds: int = 100,
    move_set: str = "layout",
    problem: str | Path | dict | None = None,
    tick_cap: int = 5_000_000,
    log=print,
) -> tuple[Ast, list[Move]]:
    """Hill-climb on geometry or the measured problem objective.

    A move that does not shrink the factor is still kept when it shrinks the
    *bounding box on one axis*, because the factor is ``max(w,h)**2``: trimming the
    short side pays nothing today and everything once the long side comes down.
    With ``problem``, public cases and ``factor × avgTicks`` gate every move, so
    a speed-for-space trade may grow one side only when its measured tick saving
    more than pays for that growth.
    """
    best = ast
    applied: list[Move] = []
    best_metric: float | None = None
    scoring_kind = ""
    if problem is not None:
        from . import optimize, scoring

        baseline = optimize.verify(render(best), problem, tick_cap=tick_cap)
        if not baseline.passed:
            raise MoveError("baseline does not pass every public case")
        scoring_kind = scoring.load_problem(problem).get("scoring", "footprint-tick")
        best_metric = (
            float(best.geometry_factor)
            if scoring_kind == "footprint"
            else best.geometry_factor * float(baseline.avg_ticks or 0)
        )
        log(
            f"  semantic baseline: avgTicks={baseline.avg_ticks} "
            f"objective={best_metric:,.2f}"
        )
    for rnd in range(1, rounds + 1):
        bw, bh = best.bbox
        bf = best.geometry_factor
        improved = False
        # score every candidate on geometry only -- no engine calls yet
        scored: list[tuple[int, int, Move, Ast]] = []
        for mv in candidates(best, move_set=move_set):
            got = _try(best, mv, caps)
            if isinstance(got, str):
                continue
            gw, gh = got.bbox
            if problem is None and (gw > bw or gh > bh):
                continue  # never grow either side
            factor = got.geometry_factor
            area = gw * gh
            semantic_candidate = problem is not None
            if factor < bf or (factor == bf and area < bw * bh) or semantic_candidate:
                scored.append((factor, area, mv, got))
        scored.sort(key=lambda t: (t[0], t[1]))
        for factor, _area, mv, got in scored:
            rows = render(got)
            try:
                sig = _binding_signature(parse_program("\n".join(rows) + "\n"))
            except Exception as exc:  # noqa: BLE001
                log(f"  round {rnd}: {mv} -> does not load ({exc})")
                continue
            if sig != want_bindings:
                nbad = sum(
                    1 for a, b in zip(sig, want_bindings, strict=False) if a != b
                )
                log(f"  round {rnd}: {mv} -> REJECTED, {nbad} op(s) re-bound")
                continue
            metric: float | None = None
            avg_ticks: float | None = None
            if problem is not None:
                from . import optimize

                result = optimize.verify(rows, problem, tick_cap=tick_cap)
                if not result.passed:
                    log(f"  round {rnd}: {mv} -> REJECTED, public cases failed")
                    continue
                avg_ticks = result.avg_ticks
                metric = (
                    float(factor)
                    if scoring_kind == "footprint"
                    else factor * float(avg_ticks or 0)
                )
                assert best_metric is not None
                if metric >= best_metric:
                    log(
                        f"  round {rnd}: {mv} -> REJECTED, objective "
                        f"{metric:,.2f} >= {best_metric:,.2f}"
                    )
                    continue
            gw, gh = got.bbox
            tail = (
                f", avgTicks {avg_ticks:,.2f}, objective {metric:,.2f}"
                if metric is not None and avg_ticks is not None
                else ""
            )
            log(
                f"  round {rnd}: {mv} -> {gw}x{gh} factor {factor:,} "
                f"(was {bf:,}), ring {ring_capacity(got, (2, 3))}{tail}"
            )
            best, improved = got, True
            if metric is not None:
                best_metric = metric
            applied.append(mv)
            break
        if not improved:
            log(f"  round {rnd}: nothing left that shrinks the box")
            break
    return best, applied


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--rounds", type=int, default=100)
    ap.add_argument(
        "--moves",
        choices=("layout", "cuts", "squash", "all"),
        default="layout",
        help="deterministic AST move family (default preserves the original layout search)",
    )
    ap.add_argument("--capacity", action="append", default=[], metavar="PIPES=NEED")
    ap.add_argument("--pipe-min", action="append", default=[], metavar="ID=N")
    ap.add_argument("--problem", help="run every public case before accepting each move")
    ap.add_argument("--tick-cap", type=int, default=5_000_000)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--describe-only", action="store_true")
    args = ap.parse_args()

    caps: dict[tuple[int, ...], int] = {}
    for spec in args.capacity:
        ids, need = spec.split("=")
        caps[tuple(int(i) for i in ids.split(","))] = int(need)
    mins: dict[int, int] = {}
    for spec in args.pipe_min:
        i, n = spec.split("=")
        mins[int(i)] = int(n)

    prog = parse_program(args.grid, bind=False)
    ast = parse_ast(prog, refine=Refine.BLOCKS, capacity=mins)
    print(describe(ast, capacity=caps))
    if args.describe_only:
        return

    want = _binding_signature(parse_program(args.grid))
    print(f"\nSEARCH  up to {args.rounds} rounds")
    best, applied = search(
        ast,
        caps=caps,
        want_bindings=want,
        rounds=args.rounds,
        move_set=args.moves,
        problem=args.problem,
        tick_cap=args.tick_cap,
    )
    w, h = best.bbox
    print(
        f"\nresult {w}x{h} factor {best.geometry_factor:,} after {len(applied)} moves: "
        + ", ".join(str(m) for m in applied)
    )
    if args.out and applied:
        args.out.write_text("\n".join(render(best)) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
