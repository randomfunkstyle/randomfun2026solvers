#!/usr/bin/env python3
"""Ask the AST questions, then commit: "can this move?", "can this pipe be N long?"

The point of holding a grid as a tree is not that edits are possible — string
surgery can edit — but that edits become **queryable**. Every operation here comes
in two halves: a ``can_*`` that is pure geometry and answers in microseconds with a
*reason*, and a mutator that either applies the whole thing or leaves the AST
exactly as it was. Nothing half-applies, so a search can try a thousand moves and
keep the one that paid.

Two facts govern what the answers can be, and both are cheap to check and
expensive to discover the hard way.

**Path length between two fixed cells has fixed parity.** Any rectilinear path
from A to B has length ``|dx| + |dy| + 2k`` — every detour that leaves the direct
route must come back, so it adds cells in pairs. A pipe pinned between two room
walls therefore cannot be made *one* cell longer, only two. When a ring needs an
exact odd capacity and the geometry offers even, the answer is not "route harder",
it is "move an endpoint", and :meth:`Plan.can_reroute` says so instead of failing
to find a path.

**A pipe's length is its capacity.** One value per cell, so shortening is a
semantic change and lengthening is free latency. Every reroute is therefore
bounded below by a declared minimum, and a pipe with no declared minimum is
refused outright rather than guessed at.

What ``can_*`` deliberately does *not* check is the engine's nearest-pipe
binding: only the engine knows that, so the caller re-parses and diffs after
committing. The split is on purpose — geometry is fast and total, binding is slow
and authoritative.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass, field

from .manast import Ast, PipeNode, RoomNode, render
from .manmoves import MoveError, reglyph

__all__ = ["Verdict", "Occupancy", "Plan", "shortest_path", "pad_to"]

Cell = tuple[int, int]
Dir = tuple[int, int]
DIRS: tuple[Dir, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Verdict:
    """Yes or no, always with a reason, and the geometry cost when it is yes."""

    ok: bool
    reason: str = ""
    factor_before: int = 0
    factor_after: int = 0
    detail: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok

    @property
    def gain(self) -> float:
        if not self.factor_after:
            return 1.0
        return self.factor_before / self.factor_after

    def __str__(self) -> str:
        head = "ok" if self.ok else "no"
        if self.ok and self.factor_after:
            head += f" ({self.factor_before:,} -> {self.factor_after:,}, {self.gain:.4f}x)"
        return f"{head}: {self.reason}" if self.reason else head


@dataclass
class Occupancy:
    """Which cells are taken, and by whom — the index every query runs against."""

    owner: dict[Cell, str] = field(default_factory=dict)

    @classmethod
    def of(cls, ast: Ast, *, ignore: frozenset[int] = frozenset()) -> Occupancy:
        """Index every painted cell. `ignore` drops pipes by id, so a pipe being
        rerouted does not collide with its own old path."""
        owner: dict[Cell, str] = {}
        for room in ast.rooms:
            for c in room.paint():
                owner[c] = f"room{room.id}"
        for pipe in ast.pipes:
            if pipe.id in ignore:
                continue
            for c in pipe.path:
                owner[c] = f"pipe{pipe.id}"
        for stray in ast.strays:
            for c in stray.paint():
                owner[c] = "stray"
        return cls(owner=owner)

    def free(self, c: Cell) -> bool:
        return c not in self.owner and c[0] >= 0 and c[1] >= 0


def shortest_path(
    start: Cell, end: Cell, occ: Occupancy, *, bound: int = 400
) -> list[Cell] | None:
    """Shortest rectilinear path of free cells, inclusive of both ends.

    Plain BFS: a pipe's cost is its cell count, so every step is equal weight and
    there is nothing for a heuristic to beat.
    """
    if not occ.free(start) or not occ.free(end):
        return None
    prev: dict[Cell, Cell | None] = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == end:
            path = [cur]
            while (p := prev[path[-1]]) is not None:
                path.append(p)
            return path[::-1]
        for d in DIRS:
            nxt = (cur[0] + d[0], cur[1] + d[1])
            if nxt in prev or not occ.free(nxt) or nxt[0] > bound or nxt[1] > bound:
                continue
            prev[nxt] = cur
            q.append(nxt)
    return None


def pad_to(path: list[Cell], target: int, occ: Occupancy) -> list[Cell] | None:
    """Lengthen `path` to exactly `target` cells by detouring into free space.

    A detour leaves the route and rejoins it, so it always adds an even number of
    cells: ``(target - len(path))`` must be even, which is the parity rule stated
    in the module docstring. Each two cells cost one sideways bump, so the search
    walks the path looking for a neighbour pair that is free.
    """
    need = target - len(path)
    if need < 0:
        return None
    if need % 2:
        return None  # parity: unreachable without moving an endpoint
    out = list(path)
    taken = set(path)
    while need > 0:
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            d = (b[0] - a[0], b[1] - a[1])
            # bump perpendicular to the current step, both ways
            for p in ((-d[1], d[0]), (d[1], -d[0])):
                a2 = (a[0] + p[0], a[1] + p[1])
                b2 = (b[0] + p[0], b[1] + p[1])
                if a2 in taken or b2 in taken:
                    continue
                if not occ.free(a2) or not occ.free(b2):
                    continue
                out[i + 1 : i + 1] = [a2, b2]
                taken.update((a2, b2))
                need -= 2
                break
            else:
                continue
            break
        else:
            return None  # nowhere left to bump
    return out


class Plan:
    """A queryable, transactional view of one AST.

    Every ``can_*`` is read-only and cheap; every mutator runs on a copy and only
    swaps it in if the whole operation succeeded, so a refused move leaves the
    tree byte-identical.
    """

    def __init__(self, ast: Ast) -> None:
        self.ast = ast

    # -- helpers -------------------------------------------------------------
    def _rooms_by_id(self) -> dict[int, RoomNode]:
        return {r.id: r for r in self.ast.rooms}

    def _attach(self, pipe: PipeNode) -> tuple[Cell, Cell]:
        """The wall cells this pipe hands values between."""
        if pipe.entry_dir is None or pipe.exit_dir is None:
            raise MoveError(f"pipe{pipe.id} has no entry/exit heading recorded")
        a = pipe.path[0]
        b = pipe.path[-1]
        src = (a[0] - pipe.entry_dir[0], a[1] - pipe.entry_dir[1])
        dst = (b[0] + pipe.exit_dir[0], b[1] + pipe.exit_dir[1])
        return src, dst

    def factor(self, ast: Ast | None = None) -> int:
        return (ast or self.ast).geometry_factor

    # -- queries -------------------------------------------------------------
    def can_reroute(
        self, pipe_id: int, *, min_capacity: int | None = None
    ) -> Verdict:
        """Could this pipe take a different path, still holding enough values?

        Answers the "N-long tape" question directly: a ring that must hold N values
        needs N cells, and this reports whether the free space and the **parity**
        of its endpoints allow it.
        """
        pipe = next((p for p in self.ast.pipes if p.id == pipe_id), None)
        if pipe is None:
            return Verdict(False, f"no pipe {pipe_id}")
        need = min_capacity if min_capacity is not None else pipe.min_capacity
        if need is None:
            return Verdict(
                False,
                f"pipe{pipe_id} has no declared capacity; shortening could deadlock a ring",
            )
        src, dst = self._attach(pipe)
        first = (src[0] + pipe.entry_dir[0], src[1] + pipe.entry_dir[1])
        last = (dst[0] - pipe.exit_dir[0], dst[1] - pipe.exit_dir[1])
        occ = Occupancy.of(self.ast, ignore=frozenset({pipe_id}))
        base = shortest_path(first, last, occ)
        if base is None:
            return Verdict(False, f"no free route between {first} and {last}")
        if len(base) > need:
            return Verdict(
                True,
                f"shortest free route is {len(base)} cells, already above the "
                f"{need} needed",
                detail=(f"shortest={len(base)}",),
            )
        if (need - len(base)) % 2:
            return Verdict(
                False,
                f"parity: a path between {first} and {last} can only be "
                f"{len(base)}, {len(base) + 2}, {len(base) + 4}, … cells, so {need} "
                "is unreachable without moving an endpoint",
                detail=(f"shortest={len(base)}", f"need={need}"),
            )
        padded = pad_to(base, need, occ)
        if padded is None:
            return Verdict(
                False, f"not enough free space to pad the route out to {need} cells"
            )
        return Verdict(True, f"routable at exactly {need} cells", detail=(f"len={need}",))

    def can_move_room(self, room_id: int, dx: int, dy: int) -> Verdict:
        """Could this room slide, with every attached pipe still routable?

        This is the "move this section and update everything" query: the room's
        walls move, so each pipe's attach cell moves with them and each pipe must
        be re-routed at no less than its declared capacity.
        """
        trial = copy.deepcopy(self.ast)
        try:
            self._apply_move(trial, room_id, dx, dy)
        except MoveError as exc:
            return Verdict(False, str(exc))
        try:
            render(trial)
        except Exception as exc:  # noqa: BLE001 - unpaintable is simply refused
            return Verdict(False, f"cannot be painted: {exc}")
        return Verdict(
            True,
            f"room{room_id} moves by ({dx},{dy})",
            factor_before=self.factor(),
            factor_after=self.factor(trial),
        )

    # -- mutators ------------------------------------------------------------
    def _apply_move(self, ast: Ast, room_id: int, dx: int, dy: int) -> None:
        rooms = {r.id: r for r in ast.rooms}
        room = rooms.get(room_id)
        if room is None:
            raise MoveError(f"no room {room_id}")
        if room.pinned:
            raise MoveError(f"room{room_id} is pinned: {room.note}")
        plan = Plan(ast)
        attach = {p.id: plan._attach(p) for p in ast.pipes}
        room.translate(dx, dy)

        for pipe in ast.pipes:
            src, dst = attach[pipe.id]
            moved = False
            if pipe.src == room_id:
                src = (src[0] + dx, src[1] + dy)
                moved = True
            if pipe.dst == room_id:
                dst = (dst[0] + dx, dst[1] + dy)
                moved = True
            if not moved:
                continue
            if pipe.min_capacity is None:
                raise MoveError(
                    f"pipe{pipe.id} must be rerouted but has no declared capacity"
                )
            first = (src[0] + pipe.entry_dir[0], src[1] + pipe.entry_dir[1])
            last = (dst[0] - pipe.exit_dir[0], dst[1] - pipe.exit_dir[1])
            occ = Occupancy.of(ast, ignore=frozenset({pipe.id}))
            base = shortest_path(first, last, occ)
            if base is None:
                raise MoveError(
                    f"pipe{pipe.id} has no free route between {first} and {last} "
                    "after the move"
                )
            want = max(pipe.min_capacity, len(base))
            if (want - len(base)) % 2:
                want += 1
            path = base if want == len(base) else pad_to(base, want, occ)
            if path is None:
                raise MoveError(
                    f"pipe{pipe.id} cannot be padded to {want} cells after the move"
                )
            pipe.path = path
            pipe.glyphs = reglyph(path, pipe.entry_dir, pipe.exit_dir)
            pipe.x = min(x for x, _ in path)
            pipe.y = min(y for _, y in path)

    def move_room(self, room_id: int, dx: int, dy: int) -> Verdict:
        """Slide a room and re-route its pipes, or change nothing at all."""
        verdict = self.can_move_room(room_id, dx, dy)
        if not verdict:
            return verdict
        trial = copy.deepcopy(self.ast)
        self._apply_move(trial, room_id, dx, dy)
        self.ast = trial
        return verdict

    def reroute(self, pipe_id: int, *, min_capacity: int | None = None) -> Verdict:
        """Re-lay one pipe at its minimum capacity, or change nothing."""
        verdict = self.can_reroute(pipe_id, min_capacity=min_capacity)
        if not verdict:
            return verdict
        trial = copy.deepcopy(self.ast)
        pipe = next(p for p in trial.pipes if p.id == pipe_id)
        need = min_capacity if min_capacity is not None else pipe.min_capacity
        src, dst = Plan(trial)._attach(pipe)
        first = (src[0] + pipe.entry_dir[0], src[1] + pipe.entry_dir[1])
        last = (dst[0] - pipe.exit_dir[0], dst[1] - pipe.exit_dir[1])
        occ = Occupancy.of(trial, ignore=frozenset({pipe_id}))
        base = shortest_path(first, last, occ)
        assert base is not None  # can_reroute already proved it
        path = base if len(base) >= need else pad_to(base, need, occ)
        if path is None:
            return Verdict(False, "padding failed after the check passed")
        pipe.path = path
        pipe.glyphs = reglyph(path, pipe.entry_dir, pipe.exit_dir)
        pipe.x = min(x for x, _ in path)
        pipe.y = min(y for _, y in path)
        before = self.factor()
        self.ast = trial
        return Verdict(
            True,
            f"pipe{pipe_id} rerouted to {len(path)} cells",
            factor_before=before,
            factor_after=self.factor(),
        )
