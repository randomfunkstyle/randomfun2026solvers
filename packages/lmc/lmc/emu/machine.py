"""Tick engine: execute a parsed grid against an input stream.

Follows the tick order in task_docs/language.md "Fine print":
  1. pipes shift   2. I/O   3. execute (+ displays)   4. movement
"""

from __future__ import annotations

from dataclasses import dataclass

from .parse import E, Grid, N, ParseError, S, W, parse_grid

MASK = (1 << 64) - 1


def w64(x: int) -> int:
    x &= MASK
    return x - (1 << 64) if x >> 63 else x


def u64(x: int) -> int:
    return x & MASK


CW = {E: S, S: W, W: N, N: E}
CCW = {v: k for k, v in CW.items()}

DIGITS = set("0123456789")


class ProgramError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


@dataclass
class Man:
    x: int
    y: int
    d: tuple[int, int] = E
    a: int = 0
    b: int = 0
    bp: int = 0
    halted: bool = False


@dataclass
class RunResult:
    output: list[int]
    reason: str  # "halted" | "error" | "stepcap"
    ticks: int
    error: str | None = None
    error_kind: str | None = None


class Machine:
    def __init__(self, grid: Grid, inputs: list[int]):
        self.g = grid
        self.inputs = list(inputs)
        self.in_cursor = 0
        self.output: list[int] = []
        # pipe value slots, parallel to each pipe's cells
        self.slots: list[list[int | None]] = [[None] * len(p.cells) for p in grid.pipes]
        # spawn men
        self.men: list[Man] = []
        for r in grid.rooms:
            if r.spawn is not None:
                self.men.append(Man(r.spawn[0], r.spawn[1]))

    # ---- pipe helpers -------------------------------------------------
    def _shift_pipes(self) -> None:
        for pi in range(len(self.g.pipes)):
            s = self.slots[pi]
            for i in range(len(s) - 2, -1, -1):
                if s[i] is not None and s[i + 1] is None:
                    s[i + 1] = s[i]
                    s[i] = None

    def _io(self) -> None:
        # outputs: consume dest cell of pipes flowing into output rooms
        for pi, p in enumerate(self.g.pipes):
            if self.g.rooms[p.dst_room].kind == "output":
                s = self.slots[pi]
                if s[-1] is not None:
                    self.output.append(s[-1])
                    s[-1] = None
        # inputs: fill source cell of pipes flowing out of input rooms
        for pi, p in enumerate(self.g.pipes):
            if self.g.rooms[p.src_room].kind == "input":
                s = self.slots[pi]
                if s[0] is None and self.in_cursor < len(self.inputs):
                    s[0] = self.inputs[self.in_cursor]
                    self.in_cursor += 1

    def _room_pipes(self, room_idx: int):
        outgoing = [p for p in self.g.pipes if p.src_room == room_idx]
        incoming = [p for p in self.g.pipes if p.dst_room == room_idx]
        return outgoing, incoming

    @staticmethod
    def _nearest(cell, pipes, seg):
        # seg: "src" -> source_cell, "dst" -> dest_cell
        best = None
        for p in pipes:
            sc = p.source_cell if seg == "src" else p.dest_cell
            dist = abs(sc[0] - cell[0]) + abs(sc[1] - cell[1])
            key = (dist, sc[1], sc[0])
            if best is None or key < best[0]:
                best = (key, p)
        return best[1] if best else None

    # ---- execution ----------------------------------------------------
    def _load_literal(self, man: Man) -> bool:
        """If man is on a closing backtick of a literal along his travel axis, load
        it into A and return True; otherwise return False (treat as nop)."""
        dx, dy = man.d
        cx, cy = man.x - dx, man.y - dy
        collected: list[str] = []
        guard = 0
        while guard < 4096:
            guard += 1
            ch = self.g.char(cx, cy)
            if ch == "`":
                digits = [c for c in collected if c in DIGITS]
                if not digits:
                    return False
                s = "".join(reversed(collected)).replace(" ", "")
                man.a = w64(int(s))
                return True
            if ch in DIGITS or ch == " ":
                collected.append(ch)
                cx -= dx
                cy -= dy
                continue
            return False
        return False

    def _execute(self, man: Man) -> bool:
        """Run the instruction under `man`. Returns True if the man is blocked
        (must not move this tick)."""
        # A man that stepped onto a room border last tick errors now: borders are
        # walls regardless of the glyph drawn there (matches the reference).
        if (man.x, man.y) in self.g.walls:
            raise ProgramError("wall", f"({man.x},{man.y})")
        c = self.g.char(man.x, man.y)

        # constants / nops
        if c in DIGITS:
            man.a = int(c)
            return False
        if c in (" ", ".", "@"):
            return False
        if c == "`":
            self._load_literal(man)
            return False

        # hands
        if c == "M":
            man.b = man.a
            return False
        if c == "W":
            man.a, man.b = man.b, man.a
            return False

        # arithmetic
        if c == "+":
            man.a = w64(man.a + man.b)
            return False
        if c == "-":
            man.a = w64(man.a - man.b)
            return False
        if c == "*":
            man.a = w64(man.a * man.b)
            return False
        if c == "%":
            man.a = 0 if man.b == 0 else w64(man.a % man.b)
            return False
        if c == "/":
            if man.b == 0:
                man.a, man.b = 0, man.a
            else:
                q = man.a // man.b
                r = man.a - q * man.b
                man.a, man.b = w64(q), w64(r)
            return False
        if c == "N":
            man.a = w64(-man.a)
            return False

        # bitwise
        if c == "&":
            man.a = w64(u64(man.a) & u64(man.b))
            return False
        if c == "|":
            man.a = w64(u64(man.a) | u64(man.b))
            return False
        if c == "~":
            man.a = w64(u64(man.a) ^ u64(man.b))
            return False
        if c == "{":
            man.a = w64(u64(man.a) << man.b) if 0 <= man.b <= 63 else 0
            return False
        if c == "}":
            if man.b < 0:
                man.a = 0
            elif man.b > 63:
                man.a = -1 if man.a < 0 else 0
            else:
                man.a = w64(man.a >> man.b)
            return False

        # direction
        if c == ">":
            man.d = E
            return False
        if c == "<":
            man.d = W
            return False
        if c == "^":
            man.d = N
            return False
        if c in ("v", "V"):
            man.d = S
            return False
        if c == "X":
            if man.a > 0:
                man.d = CW[man.d]
            elif man.a < 0:
                man.d = CCW[man.d]
            return False

        # control
        if c == "H":
            man.halted = True
            return False

        # backpack
        if c == "b":
            man.bp = man.a
            return False
        if c == "m":
            man.bp = w64(man.bp - 1)
            return False
        if c == "d":
            if man.bp > 0:
                man.d = CW[man.d]
            return False
        if c == "a":
            if man.bp > 0:
                man.d = CCW[man.d]
            return False
        if c == "]":
            man.bp = man.bp >> 1
            return False
        if c == "x":
            man.d = CW[man.d] if (man.bp & 1) else CCW[man.d]
            return False

        # pipes
        if c in ("s", "S", "r", "R", "U", "q"):
            return self._pipe_op(man, c)

        raise ProgramError("bad-op", f"'{c}' at ({man.x},{man.y})")

    def _pipe_op(self, man: Man, c: str) -> bool:
        room_idx = self.g.room_of(man.x, man.y)
        if room_idx is None:
            raise ProgramError("no-pipe", "not in a room")
        outgoing, incoming = self._room_pipes(room_idx)

        if c == "q":
            if not incoming:
                raise ProgramError("no-pipe", "q: no incoming pipe")
            p = self._nearest((man.x, man.y), incoming, "dst")
            pi = self.g.pipes.index(p)
            man.bp = sum(1 for v in self.slots[pi] if v is not None)
            return False

        if c == "s":
            if not outgoing:
                raise ProgramError("no-pipe", "s: no outgoing pipe")
            p = self._nearest((man.x, man.y), outgoing, "src")
            pi = self.g.pipes.index(p)
            if self.slots[pi][0] is None:
                self.slots[pi][0] = man.a
                return False
            return True  # blocked

        if c == "S":
            if not outgoing:
                raise ProgramError("no-pipe", "S: no outgoing pipe")
            idxs = [self.g.pipes.index(p) for p in outgoing]
            if all(self.slots[pi][0] is None for pi in idxs):
                for pi in idxs:
                    self.slots[pi][0] = man.a
                return False
            return True

        if c == "r":
            if not incoming:
                raise ProgramError("no-pipe", "r: no incoming pipe")
            p = self._nearest((man.x, man.y), incoming, "dst")
            pi = self.g.pipes.index(p)
            if self.slots[pi][-1] is not None:
                man.a = self.slots[pi][-1]
                self.slots[pi][-1] = None
                return False
            return True

        if c in ("R", "U"):
            if not incoming:
                raise ProgramError("no-pipe", f"{c}: no incoming pipe")
            ready = []
            for p in incoming:
                pi = self.g.pipes.index(p)
                if self.slots[pi][-1] is not None:
                    dc = p.dest_cell
                    ready.append(((dc[1], dc[0]), pi, p))
            if not ready:
                return True
            ready.sort()
            _, pi, p = ready[0]
            man.a = self.slots[pi][-1]
            self.slots[pi][-1] = None
            if c == "U":
                dc = p.dest_cell
                ddx = man.x - dc[0]
                ddy = man.y - dc[1]
                if abs(ddx) >= abs(ddy):
                    man.d = E if ddx > 0 else W
                else:
                    man.d = S if ddy > 0 else N
            return False

        return False

    # ---- movement -----------------------------------------------------
    def _move(self, blocked: list[bool]) -> None:
        active = [
            i
            for i, m in enumerate(self.men)
            if not m.halted and not blocked[i]
        ]
        targets = {}
        for i in active:
            m = self.men[i]
            targets[i] = (m.x + m.d[0], m.y + m.d[1])

        # collision: two men to same cell, or into a cell occupied by a man
        occupied = {(m.x, m.y): i for i, m in enumerate(self.men) if not m.halted}
        dest_count: dict[tuple[int, int], list[int]] = {}
        for i, t in targets.items():
            dest_count.setdefault(t, []).append(i)

        stop = set()
        for t, movers in dest_count.items():
            if len(movers) > 1:
                stop.update(movers)
            if t in occupied and occupied[t] not in movers:
                stop.update(movers)
                stop.add(occupied[t])

        for i in stop:
            self.men[i].halted = True

        for i in active:
            if i in stop:
                continue
            # Moving onto a wall is allowed; the error fires when the man tries to
            # execute the border cell next tick (see _execute).
            self.men[i].x, self.men[i].y = targets[i]

    # ---- run ----------------------------------------------------------
    def _one_tick(self) -> None:
        self._shift_pipes()
        self._io()
        order = sorted(
            range(len(self.men)),
            key=lambda i: (self.men[i].y, self.men[i].x),
        )
        blocked = [False] * len(self.men)
        for i in order:
            m = self.men[i]
            if m.halted:
                blocked[i] = True
                continue
            blocked[i] = self._execute(m)
        self._move(blocked)

    def snapshot(self) -> list[dict]:
        return [
            {
                "pos": [m.x, m.y],
                "dir": list(m.d),
                "a": m.a,
                "b": m.b,
                "backpack": m.bp,
                "halted": m.halted,
            }
            for m in self.men
        ]

    def step_n(self, n: int) -> list[dict]:
        """Run exactly n ticks (no drain); return runner snapshots. For testing."""
        for _ in range(n):
            if all(m.halted for m in self.men):
                break
            self._one_tick()
        return self.snapshot()

    def run(self, step_cap: int) -> RunResult:
        # load-time validation (catches malformed codegen early, like the reference)
        if not self.g.rooms:
            return RunResult([], "error", 0, "no rooms", "load")
        if not self.men:
            return RunResult([], "error", 0, "no '@' spawn marker", "load")
        ticks = 0
        try:
            while ticks < step_cap:
                if all(m.halted for m in self.men) and self.men:
                    return self._drain(ticks, step_cap)
                self._one_tick()
                ticks += 1
            return RunResult(self.output, "stepcap", ticks)
        except ProgramError as e:
            # the error occurs during the (ticks+1)-th tick's execute phase, which
            # the reference counts as a completed tick.
            return RunResult(self.output, "error", ticks + 1, str(e), e.kind)

    def _drain(self, ticks: int, step_cap: int) -> RunResult:
        out_pipe_idxs = [
            pi
            for pi, p in enumerate(self.g.pipes)
            if self.g.rooms[p.dst_room].kind == "output"
        ]

        def pending() -> bool:
            return any(
                any(v is not None for v in self.slots[pi]) for pi in out_pipe_idxs
            )

        while pending() and ticks < step_cap:
            self._shift_pipes()
            self._io()
            ticks += 1
        return RunResult(self.output, "halted", ticks)


def run(program: str, inputs: list[int], step_cap: int = 1_000_000) -> RunResult:
    grid = parse_grid(program)
    return Machine(grid, inputs).run(step_cap)


__all__ = ["run", "RunResult", "Machine", "ProgramError", "ParseError", "parse_grid"]
