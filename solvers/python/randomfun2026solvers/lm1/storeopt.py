"""Find an LM-1 CPU's STORE seam and reattach another memory backend.

The registered CPU generator owns the hard part: adapter placement, request and
response routing, nearest-pipe binding checks, and pipe-count validation.  This
helper turns that seam into one command, measures the replacement on all public
cases, and writes it only when the complete problem objective improves.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path

from .. import optimize, scoring
from ..manast import Ast, Refine, RoomNode, parse_ast
from ..manparse import parse_program
from . import machine, programs


@dataclass(frozen=True)
class MemoryTopology:
    program_slug: str
    tape_n: int
    memory_ops: tuple[str, ...]
    source_rooms: int
    source_pipes: int
    generator_match: bool
    adapter_region: tuple[int, int, int, int]
    store_region: tuple[int, int, int, int]
    source_seam: StoreSeam


@dataclass(frozen=True, order=True)
class BoundaryRoute:
    direction: str
    outside_role: tuple[str, int, int, int]
    pipe_id: int
    outside_room_id: int
    stub_cell: tuple[int, int]
    heading: tuple[int, int] | None


@dataclass(frozen=True)
class StoreSeam:
    """The removable AST subgraph and its remembered outside attachments."""

    room_ids: tuple[int, ...]
    internal_pipe_ids: tuple[int, ...]
    boundary_routes: tuple[BoundaryRoute, ...]


@dataclass(frozen=True)
class DetachedStore:
    """AST with the old store and its routes removed, plus remembered stubs."""

    ast: Ast
    seam: StoreSeam


@dataclass(frozen=True)
class StoreResult:
    topology: MemoryTopology
    baseline_objective: float
    candidate_objective: float
    baseline_avg_ticks: float
    candidate_avg_ticks: float
    candidate: machine.Machine
    candidate_seam: StoreSeam

    @property
    def improved(self) -> bool:
        return self.candidate_objective < self.baseline_objective


_MEMORY_SEMS = frozenset(machine.MEMORY_SEMS)


def _shape(rows: list[str] | tuple[str, ...]) -> tuple[int, int]:
    return max((len(row.rstrip()) for row in rows), default=0), len(rows)


def _objective(width: int, height: int, avg_ticks: float, problem_slug: str) -> float:
    factor = max(width, height) ** 2
    kind = scoring.load_problem(problem_slug).get("scoring", "footprint-tick")
    return float(factor) if kind == "footprint" else factor * avg_ticks


def _room_signature(room: RoomNode) -> tuple:
    """Translation-invariant room content used to find a moved store block."""
    cells = tuple(
        sorted((x - room.x, y - room.y, glyph) for (x, y), glyph in room.paint().items())
    )
    return room.kind, room.size, cells


def _room_role(room: RoomNode) -> tuple[str, int, int, int]:
    live = sum(len(child.paint()) for child in room.children)
    width, height = room.size
    return room.kind, width, height, live


def _rooms_in_region(ast: Ast, region: tuple[int, int, int, int]) -> tuple[int, ...]:
    x, y, width, height = region
    right, bottom = x + width, y + height
    ids = []
    for room in ast.rooms:
        room_right = room.x + room.size[0]
        room_bottom = room.y + room.size[1]
        if room.x >= x and room.y >= y and room_right <= right and room_bottom <= bottom:
            ids.append(room.id)
    if not ids:
        raise ValueError(f"no AST rooms found inside STORE region {region}")
    return tuple(sorted(ids))


def _match_store_rooms(
    source: Ast,
    reference: Ast,
    reference_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Find a rigidly moved copy of the registered store-room group."""
    refs = [next(room for room in reference.rooms if room.id == ident) for ident in reference_ids]
    source_by_signature: dict[tuple, list[RoomNode]] = {}
    for room in source.rooms:
        source_by_signature.setdefault(_room_signature(room), []).append(room)
    anchor = refs[0]
    for candidate in source_by_signature.get(_room_signature(anchor), []):
        dx, dy = candidate.x - anchor.x, candidate.y - anchor.y
        matched: list[int] = []
        for ref in refs:
            found = next(
                (
                    room
                    for room in source_by_signature.get(_room_signature(ref), [])
                    if (room.x, room.y) == (ref.x + dx, ref.y + dy)
                ),
                None,
            )
            if found is None:
                break
            matched.append(found.id)
        if len(matched) == len(refs):
            return tuple(sorted(matched))
    raise ValueError(
        "could not find the registered STORE room group in the source AST; "
        "the memory was internally rearranged rather than moved as one drop-in block"
    )


def _seam(ast: Ast, room_ids: tuple[int, ...]) -> StoreSeam:
    inside = set(room_ids)
    by_id = {room.id: room for room in ast.rooms}
    internal: list[int] = []
    boundary: list[BoundaryRoute] = []
    for pipe in ast.pipes:
        src_inside, dst_inside = pipe.src in inside, pipe.dst in inside
        if src_inside and dst_inside:
            internal.append(pipe.id)
        elif src_inside != dst_inside:
            outside_id = pipe.dst if src_inside else pipe.src
            outside = by_id.get(outside_id)
            if outside is None:
                raise ValueError(
                    f"STORE boundary pipe {pipe.id} terminates outside every room"
                )
            boundary.append(
                BoundaryRoute(
                    direction="out" if src_inside else "in",
                    outside_role=_room_role(outside),
                    pipe_id=pipe.id,
                    outside_room_id=outside_id,
                    stub_cell=pipe.path[-1] if src_inside else pipe.path[0],
                    heading=pipe.exit_dir if src_inside else pipe.entry_dir,
                )
            )
    if sorted(route.direction for route in boundary) != ["in", "out"]:
        raise ValueError(
            "STORE is not a two-route drop-in seam: "
            f"{[(route.direction, route.pipe_id) for route in boundary]}"
        )
    return StoreSeam(
        room_ids=tuple(sorted(room_ids)),
        internal_pipe_ids=tuple(sorted(internal)),
        boundary_routes=tuple(sorted(boundary)),
    )


def detach_store(ast: Ast, seam: StoreSeam) -> DetachedStore:
    """Remove the located memory rooms and every attached route from a copy."""
    detached = copy.deepcopy(ast)
    removed_rooms = set(seam.room_ids)
    removed_pipes = set(seam.internal_pipe_ids) | {
        route.pipe_id for route in seam.boundary_routes
    }
    detached.rooms = [room for room in detached.rooms if room.id not in removed_rooms]
    detached.pipes = [pipe for pipe in detached.pipes if pipe.id not in removed_pipes]
    return DetachedStore(ast=detached, seam=seam)


def _machine_seam(built: machine.Machine) -> StoreSeam:
    ast = parse_ast(
        parse_program("\n".join(built.rows) + "\n", bind=False),
        refine=Refine.BLOCKS,
    )
    ids = _rooms_in_region(ast, built.regions["tape"])
    return _seam(ast, ids)


def _logical_routes(seam: StoreSeam) -> tuple[tuple[str, tuple[str, int, int, int]], ...]:
    return tuple(sorted((route.direction, route.outside_role) for route in seam.boundary_routes))


def inspect(grid: Path, *, program_slug: str) -> MemoryTopology:
    """Locate the registered adapter/store seam and fingerprint the source."""
    program = programs.load(program_slug)
    used = tuple(
        sorted(
            {
                instr.sem.name
                for instr in program.instrs
                if instr.sem in _MEMORY_SEMS
            }
        )
    )
    if not used:
        raise ValueError(f"{program_slug!r} does not use the STORE interface")

    source_text = grid.read_text(encoding="utf-8")
    source_ast = parse_ast(parse_program(source_text, bind=False), refine=Refine.BLOCKS)
    registered = machine.build_for(program_slug)
    generated_text = "\n".join(registered.rows) + "\n"
    try:
        adapter_region = registered.regions["adapter"]
        store_region = registered.regions["tape"]
    except KeyError as exc:
        raise ValueError(f"{program_slug!r} generator has no memory seam") from exc
    reference_ast = parse_ast(
        parse_program(generated_text, bind=False),
        refine=Refine.BLOCKS,
    )
    reference_ids = _rooms_in_region(reference_ast, store_region)
    source_ids = (
        reference_ids
        if source_text == generated_text
        else _match_store_rooms(source_ast, reference_ast, reference_ids)
    )
    source_seam = _seam(source_ast, source_ids)
    return MemoryTopology(
        program_slug=program_slug,
        tape_n=registered.tape_n,
        memory_ops=used,
        source_rooms=len(source_ast.rooms),
        source_pipes=len(source_ast.pipes),
        generator_match=source_text == generated_text,
        adapter_region=adapter_region,
        store_region=store_region,
        source_seam=source_seam,
    )


def replace(
    grid: Path,
    *,
    program_slug: str,
    store: str = "men",
    log=print,
) -> StoreResult:
    """Rebuild and route ``program_slug`` with ``store``, then validate it."""
    topology = inspect(grid, program_slug=program_slug)
    problem_slug = programs.problem_of(program_slug)
    source_rows = grid.read_text(encoding="utf-8").splitlines()
    baseline = optimize.verify(source_rows, problem_slug)
    if not baseline.passed or baseline.avg_ticks is None:
        raise ValueError("source grid does not pass every public case")
    bw, bh = _shape(source_rows)
    baseline_objective = _objective(
        bw, bh, float(baseline.avg_ticks), problem_slug
    )

    log(
        f"found STORE: ops={','.join(topology.memory_ops)} slots={topology.tape_n} "
        f"adapter={topology.adapter_region} store={topology.store_region}"
    )
    log(
        f"remove AST rooms={topology.source_seam.room_ids} "
        f"internal_pipes={topology.source_seam.internal_pipe_ids}; remember "
        f"boundary={[(r.direction, r.pipe_id, r.stub_cell, r.heading, r.outside_role) for r in topology.source_seam.boundary_routes]}"
    )
    detached = detach_store(
        parse_ast(parse_program("\n".join(source_rows) + "\n", bind=False), refine=Refine.BLOCKS),
        topology.source_seam,
    )
    log(
        f"detached AST retains {len(detached.ast.rooms)} rooms and "
        f"{len(detached.ast.pipes)} unrelated pipes"
    )
    if not topology.generator_match:
        log(
            "source is hand-packed: preserving program semantics, but rebuilding "
            "the registered CPU topology to route the replacement store"
        )

    candidate = machine.build_for(program_slug, store=store)
    candidate_seam = _machine_seam(candidate)
    if _logical_routes(candidate_seam) != _logical_routes(topology.source_seam):
        raise ValueError(
            "replacement STORE did not reattach to the remembered logical routes: "
            f"{_logical_routes(topology.source_seam)} -> {_logical_routes(candidate_seam)}"
        )
    result = optimize.verify(candidate.rows, problem_slug)
    if not result.passed or result.avg_ticks is None:
        raise ValueError(f"{store} replacement fails public cases")
    candidate_objective = _objective(
        candidate.width,
        candidate.height,
        float(result.avg_ticks),
        problem_slug,
    )
    log(
        f"baseline {bw}x{bh} avgTicks={baseline.avg_ticks:,.2f} "
        f"objective={baseline_objective:,.2f}"
    )
    log(
        f"{store} {candidate.width}x{candidate.height} "
        f"avgTicks={result.avg_ticks:,.2f} objective={candidate_objective:,.2f}"
    )
    return StoreResult(
        topology=topology,
        baseline_objective=baseline_objective,
        candidate_objective=candidate_objective,
        baseline_avg_ticks=float(baseline.avg_ticks),
        candidate_avg_ticks=float(result.avg_ticks),
        candidate=candidate,
        candidate_seam=candidate_seam,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path, help="current best submitted grid")
    ap.add_argument("--program", required=True, choices=sorted(machine.TAPE_SIZE))
    ap.add_argument("--store", choices=("tape", "men"), default="men")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    try:
        result = replace(
            args.grid,
            program_slug=args.program,
            store=args.store,
        )
    except (ValueError, machine.MachineError) as exc:
        ap.error(str(exc))
    verdict = "IMPROVED" if result.improved else "not improved"
    print(
        f"result {verdict}: {result.candidate.width}x{result.candidate.height} "
        f"objective={result.candidate_objective:,.2f}"
    )
    if args.out and result.improved:
        args.out.write_text(
            "\n".join(result.candidate.rows) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
