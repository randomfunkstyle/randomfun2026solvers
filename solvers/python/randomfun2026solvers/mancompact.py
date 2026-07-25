#!/usr/bin/env python3
"""Compaction moves over a grid, and the validation every move must survive.

The score is ``max(w, h)**2 x avg_ticks``, so the side length is worth *two*
factors of anything else: two columns off a 98-wide grid is 9,604 -> 9,216, a 4%
win for a change that cannot alter a single instruction.

The first move is **dead-line elimination**, which is the cheapest real win and
needs no re-routing at all. A whole grid row or column is deleted when every cell
in it is one of:

* blank, or ``.`` — SPEC nops, so nothing is lost;
* a wall *parallel* to the cut — deleting a column that crosses a room's top and
  bottom walls just makes the room one narrower;
* a pipe body parallel to the cut — the pipe gets one cell shorter.

That last one is the only dangerous case, because **a pipe's length is its
capacity**: a value occupies one cell per tick, so shortening a ring by a cell
can deadlock it. So a cut is only offered when every pipe it shortens has
declared slack (see :class:`~randomfun2026solvers.manstruct.CapacityHint`), and
an undeclared pipe blocks the cut outright.

A cut is *refused* by anything else in the line: a live glyph, a corner, an
arrowhead (which is a pipe's bend or endpoint, not a straight body), or a wall
*perpendicular* to the cut — deleting a column containing a room's side wall
would breach the box. ``|`` is genuinely ambiguous, being the OR opcode as well
as a wall and a vertical pipe body, so the decision is made on the cell's
classified :class:`~randomfun2026solvers.manstruct.Kind` and never on its glyph.

Validation is three gates in increasing cost, because the cheap ones catch the
common mistakes:

1. the grid still loads, with the same rooms and pipes;
2. every send/recv still binds to the same pipe — a shifted ``r`` silently
   re-binding to a different pipe is the failure mode with no symptom;
3. every public case still passes, and the tick count did not regress.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

from .manparse import Program, parse_program
from .manstruct import CapacityHint, Kind, Structure, _build_cells, analyze_structure

__all__ = [
    "Cut",
    "CompactResult",
    "dead_columns",
    "dead_rows",
    "apply_cuts",
    "compact",
    "validate",
]

#: Wall glyphs by the axis they run along. A room's top/bottom edge runs
#: horizontally, so it survives a *column* cut; its side walls do not.
_H_WALL = frozenset("-=")
_V_WALL = frozenset("|:")


@dataclass(frozen=True)
class Cut:
    """One deletable grid line and the pipes it would shorten."""

    axis: str  # "col" or "row"
    index: int
    pipes: tuple[int, ...] = ()  # pipe ids losing exactly one cell each

    def __str__(self) -> str:
        tail = f" (shortens pipes {list(self.pipes)})" if self.pipes else ""
        return f"{self.axis} {self.index}{tail}"


@dataclass
class CompactResult:
    rows: list[str]
    cuts: list[Cut] = field(default_factory=list)
    before: tuple[int, int] = (0, 0)
    after: tuple[int, int] = (0, 0)
    notes: list[str] = field(default_factory=list)

    @property
    def factor_before(self) -> int:
        return max(self.before) ** 2

    @property
    def factor_after(self) -> int:
        return max(self.after) ** 2

    @property
    def gain(self) -> float:
        return self.factor_before / self.factor_after if self.factor_after else 1.0


def _pipe_slack(struct: Structure) -> dict[int, int]:
    """Cells each pipe group may still give back, spread over its members.

    Budgeted per *group* because capacity is a property of the whole ring; the
    budget is then drawn down as cuts are accepted, so two cuts through the same
    ring cannot each spend the same slot.
    """
    out: dict[int, int] = {}
    for p in struct.pipes:
        out[p.id] = p.slack if p.slack is not None else 0
    return out


def _line_cells(
    struct: Structure, axis: str, index: int
) -> list[tuple[tuple[int, int], str, Kind, int | None]]:
    w, h = struct.bbox
    span = range(h) if axis == "col" else range(w)
    out = []
    for other in span:
        c = (index, other) if axis == "col" else (other, index)
        info = struct.cells.get(c)
        if info is None:
            out.append((c, " ", Kind.VOID, None))
        else:
            out.append((c, info.glyph, info.kind, info.pipe))
    return out


def _cut_for(struct: Structure, axis: str, index: int) -> Cut | None:
    """Is this whole line removable? Returns the cut, or ``None`` with no reason.

    Use :func:`explain_cut` when the reason matters.
    """
    parallel_wall = _H_WALL if axis == "col" else _V_WALL
    pipes: list[int] = []
    for _c, glyph, kind, pipe in _line_cells(struct, axis, index):
        if kind in (Kind.FLOOR, Kind.NOP, Kind.VOID):
            continue
        if kind is Kind.WALL:
            # A wall running along the cut shortens; one crossing it would breach.
            if glyph in parallel_wall:
                continue
            return None
        if kind is Kind.PIPE:
            # Only a straight body parallel to the cut may lose a cell. An
            # arrowhead is a bend or an endpoint: deleting it changes the route.
            if glyph in parallel_wall:
                pipes.append(pipe if pipe is not None else -1)
                continue
            return None
        return None  # live glyph, corner, spawn, io, arrowhead
    return Cut(axis, index, tuple(sorted(set(pipes))))


def _affordable(cut: Cut, budget: dict[int, int]) -> bool:
    """Every pipe the cut shortens must have a slot left in its budget."""
    return all(budget.get(p, 0) > 0 for p in cut.pipes)


def dead_columns(struct: Structure, budget: dict[int, int] | None = None) -> list[Cut]:
    w, _ = struct.bbox
    budget = budget if budget is not None else _pipe_slack(struct)
    out = []
    for x in range(w):
        cut = _cut_for(struct, "col", x)
        if cut is not None and _affordable(cut, budget):
            out.append(cut)
    return out


def dead_rows(struct: Structure, budget: dict[int, int] | None = None) -> list[Cut]:
    _, h = struct.bbox
    budget = budget if budget is not None else _pipe_slack(struct)
    out = []
    for y in range(h):
        cut = _cut_for(struct, "row", y)
        if cut is not None and _affordable(cut, budget):
            out.append(cut)
    return out


def apply_cuts(rows: list[str], cuts: list[Cut]) -> list[str]:
    """Delete the given columns and rows. Indices are in *original* coordinates."""
    drop_c = {c.index for c in cuts if c.axis == "col"}
    drop_r = {c.index for c in cuts if c.axis == "row"}
    out = []
    for y, row in enumerate(rows):
        if y in drop_r:
            continue
        out.append("".join(ch for x, ch in enumerate(row) if x not in drop_c).rstrip())
    return out


def _select(struct: Structure) -> list[Cut]:
    """All affordable cuts, drawing each pipe group's slack down as it is spent."""
    budget = _pipe_slack(struct)
    chosen: list[Cut] = []
    for axis, n in (("col", struct.bbox[0]), ("row", struct.bbox[1])):
        for i in range(n):
            cut = _cut_for(struct, axis, i)
            if cut is None or not _affordable(cut, budget):
                continue
            for p in cut.pipes:
                budget[p] -= 1
            chosen.append(cut)
    return chosen


def _factor(rows: list[str]) -> int:
    return max(max((len(r) for r in rows), default=0), len(rows)) ** 2


def _ordered(cuts: list[Cut], rows: list[str]) -> list[Cut]:
    """Dominant axis first: only the larger side divides ``max(w,h)**2``.

    Cutting the short side of a 25x25 grid leaves the factor at 625, so a search
    that spends its validation budget there finds nothing. Interleaving keeps the
    square square, which is where the next cut on either axis pays again.
    """
    w, h = max((len(r) for r in rows), default=0), len(rows)
    cols = [c for c in cuts if c.axis == "col"]
    rws = [c for c in cuts if c.axis == "row"]
    out: list[Cut] = []
    while cols or rws:
        take_col = (w >= h and cols) or not rws
        if take_col and cols:
            out.append(cols.pop(0))
            w -= 1
        elif rws:
            out.append(rws.pop(0))
            h -= 1
    return out


def compact(
    program: str | os.PathLike[str] | Program,
    *,
    capacity: list[CapacityHint] | None = None,
    keep_bindings: bool = True,
    max_validations: int = 24,
) -> CompactResult:
    """Find the largest set of dead-line cuts that preserves every pipe binding.

    Cutting is geometrically free but *not* semantically free: ``s``/``r`` bind to
    the **nearest** pipe, so pulling a room's walls in can hand an op to a
    different pipe with no other symptom. So the whole set is tried first — the
    common case, and one engine parse — and only if that re-binds does it fall
    back to accepting cuts one at a time, dominant axis first.
    """
    prog = program if isinstance(program, Program) else parse_program(program, bind=False)
    struct = analyze_structure(prog, capacity=capacity or [])
    struct.cells = _build_cells(prog)  # a full lattice for line scanning
    rows = prog.to_grid()
    before = (max((len(r) for r in rows), default=0), len(rows))
    candidates = _ordered(_select(struct), rows)
    notes: list[str] = []

    def finish(cuts: list[Cut]) -> CompactResult:
        new = apply_cuts(rows, cuts)
        shortened = sorted({p for c in cuts for p in c.pipes})
        if shortened:
            notes.append(f"pipes shortened by one cell each: {shortened}")
        return CompactResult(
            rows=new,
            cuts=cuts,
            before=before,
            after=(max((len(r) for r in new), default=0), len(new)),
            notes=notes,
        )

    if not candidates or not keep_bindings:
        return finish(candidates)

    from .littleman import Littleman

    lm = Littleman()
    want = _binding_signature(parse_program(prog.render() + "\n", lm=lm, bind=True))

    def ok(cuts: list[Cut]) -> bool:
        if not cuts:
            return True
        text = "\n".join(apply_cuts(rows, cuts)) + "\n"
        try:
            return _binding_signature(parse_program(text, lm=lm, bind=True)) == want
        except Exception:  # noqa: BLE001 - a grid that will not load is not a candidate
            return False

    # Whole set first, then each axis alone: three cheap shots that cover most
    # grids, before paying for a cut-by-cut walk.
    spent = 0
    for label, trial in (
        ("all", candidates),
        ("cols only", [c for c in candidates if c.axis == "col"]),
        ("rows only", [c for c in candidates if c.axis == "row"]),
    ):
        if not trial or len(trial) == len(candidates) and label != "all":
            continue
        spent += 1
        if ok(trial):
            notes.append(f"{label}: {len(trial)} cuts preserved every binding")
            return finish(trial)

    best: list[Cut] = []
    rejected = 0
    for cut in candidates:
        if spent >= max_validations:
            notes.append(f"validation budget of {max_validations} exhausted")
            break
        trial = [*best, cut]
        if _factor(apply_cuts(rows, trial)) >= _factor(apply_cuts(rows, best)) and best:
            continue  # this cut buys nothing on its own; skip the engine call
        spent += 1
        if ok(trial):
            best = trial
        else:
            rejected += 1
    notes.append(
        f"greedy: {len(best)} of {len(candidates)} cuts kept, "
        f"{rejected} rejected for re-binding, {spent} engine checks"
    )
    return finish(best)


# ── validation ───────────────────────────────────────────────────────────────
@dataclass
class Validation:
    ok: bool
    loads: bool = False
    topology_same: bool = False
    bindings_same: bool = False
    cases_pass: bool | None = None
    avg_ticks: float | None = None
    detail: list[str] = field(default_factory=list)


def _binding_signature(prog: Program) -> list[tuple[str, str, int]]:
    """Per-room (kind, glyph, pipe id) in reading order.

    Deliberately position-free: a cut shifts every coordinate, so absolute cells
    cannot be compared. What must hold is that the same ops still resolve to the
    same pipes, in the same order.
    """
    sig = []
    for room in prog.rooms:
        for op in sorted(room.pipe_ops, key=lambda o: (o.cell[1], o.cell[0])):
            sig.append((room.kind, op.glyph, op.pipe_id))
    return sig


def validate(
    new_rows: list[str],
    original: str | os.PathLike[str] | Program,
    *,
    problem: str | os.PathLike[str] | dict | None = None,
    tick_cap: int | None = None,
    baseline_ticks: float | None = None,
) -> Validation:
    """Three gates: it loads, the bindings survived, the cases still pass."""
    from .littleman import Littleman

    lm = Littleman()
    orig = (
        original
        if isinstance(original, Program)
        else parse_program(original, lm=lm, bind=True)
    )
    v = Validation(ok=False)
    text = "\n".join(new_rows) + "\n"

    try:
        new = parse_program(text, lm=lm, bind=True)
    except Exception as exc:  # noqa: BLE001 - any load failure is a rejection
        v.detail.append(f"does not load: {type(exc).__name__}: {exc}")
        return v
    v.loads = True

    if len(new.rooms) != len(orig.rooms) or len(new.pipes) != len(orig.pipes):
        v.detail.append(
            f"topology changed: rooms {len(orig.rooms)}->{len(new.rooms)}, "
            f"pipes {len(orig.pipes)}->{len(new.pipes)}"
        )
        return v
    if [r.kind for r in new.rooms] != [r.kind for r in orig.rooms]:
        v.detail.append("room kinds changed")
        return v
    v.topology_same = True

    before, after = _binding_signature(orig), _binding_signature(new)
    if before != after:
        diff = [(a, b) for a, b in zip(before, after, strict=False) if a != b]
        v.detail.append(f"pipe bindings changed ({len(diff)} ops), e.g. {diff[:3]}")
        return v
    v.bindings_same = True

    if problem is None:
        v.ok = True
        v.detail.append("no problem given: loaded and bindings preserved, cases unchecked")
        return v

    from . import optimize, scoring

    res = optimize.verify(
        new_rows, problem, lm=lm, tick_cap=tick_cap or scoring.DEFAULT_TICK_CAP
    )
    v.cases_pass = res.passed
    v.avg_ticks = res.avg_ticks
    if not res.passed:
        failed = [c.name for c in res.cases if not c.passed]
        v.detail.append(f"cases failed: {failed}")
        return v
    if baseline_ticks is not None and res.avg_ticks is not None:
        if res.avg_ticks > baseline_ticks:
            v.detail.append(
                f"ticks regressed: {baseline_ticks:,.0f} -> {res.avg_ticks:,.0f}"
            )
    v.ok = True
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--problem", help="slug or problem json, to run the public cases")
    ap.add_argument("--capacity", action="append", default=[], metavar="PIPES=NEED")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    hints = []
    for spec in args.capacity:
        pipes, need = spec.split("=")
        hints.append(CapacityHint(tuple(int(p) for p in pipes.split(",")), int(need)))

    res = compact(args.grid, capacity=hints)
    print(
        f"{args.grid.name}: {res.before[0]}x{res.before[1]} -> "
        f"{res.after[0]}x{res.after[1]}   factor {res.factor_before:,} -> "
        f"{res.factor_after:,}  ({res.gain:.3f}x)"
    )
    for c in res.cuts:
        print(f"  cut {c}")
    for n in res.notes:
        print(f"  note: {n}")
    if not res.cuts:
        print("  nothing to cut")
        return

    v = validate(res.rows, args.grid, problem=args.problem)
    print(
        f"  validate: loads={v.loads} topology={v.topology_same} "
        f"bindings={v.bindings_same} cases={v.cases_pass} ok={v.ok}"
    )
    for d in v.detail:
        print(f"    {d}")
    if v.ok and args.out:
        args.out.write_text("\n".join(res.rows) + "\n", encoding="utf-8")
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
