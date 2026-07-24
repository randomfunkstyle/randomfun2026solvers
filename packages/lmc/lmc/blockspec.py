"""Abstract, coordinate-free description of a little-man program.

The frontend emits a `BlockGraph`; the trail builder + Z3 router turn it into a
concrete ASCII grid. Nothing here knows about coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# sides of a room, as compass directions
N, S, E, W = "N", "S", "E", "W"
SIDES = (N, S, E, W)

PIPE_SENDS = {"s", "S"}
PIPE_RECVS = {"r", "R", "U", "q"}
PIPE_OPS = PIPE_SENDS | PIPE_RECVS


@dataclass
class Instr:
    """One cell of the CPU man's trail."""

    char: str  # a single little-man instruction glyph
    pipe: str | None = None  # target pipe id, for s/S/r/R/U/q

    @property
    def is_send(self) -> bool:
        return self.char in PIPE_SENDS

    @property
    def is_recv(self) -> bool:
        return self.char in PIPE_RECVS

    @property
    def is_pipe_op(self) -> bool:
        return self.char in PIPE_OPS


@dataclass
class Pipe:
    """A one-way pipe from src_room to dst_room, attaching to given sides."""

    id: str
    src_room: str
    src_side: str
    dst_room: str
    dst_side: str

    def cpu_side(self, cpu: str) -> str | None:
        """Which side of the CPU room this pipe touches (or None)."""
        if self.src_room == cpu:
            return self.src_side
        if self.dst_room == cpu:
            return self.dst_side
        return None

    def cpu_dir(self, cpu: str) -> str | None:
        """'out' if the CPU sends into it, 'in' if the CPU receives from it."""
        if self.src_room == cpu:
            return "out"
        if self.dst_room == cpu:
            return "in"
        return None


@dataclass
class BlockGraph:
    cpu: str  # id of the CPU room
    rooms: dict[str, str] = field(default_factory=dict)  # id -> kind
    pipes: list[Pipe] = field(default_factory=list)
    trail: list[Instr] = field(default_factory=list)  # CPU instruction sequence

    def cpu_pipes(self, direction: str) -> list[Pipe]:
        """CPU pipes with a given direction ('in' or 'out')."""
        return [p for p in self.pipes if p.cpu_dir(self.cpu) == direction]

    def pipe(self, pid: str) -> Pipe:
        for p in self.pipes:
            if p.id == pid:
                return p
        raise KeyError(pid)


def ring_io_graph() -> BlockGraph:
    """The R0 target: echo input through a 1-word ring.

    I --(W)--> CPU --(N up)--> BUF --(N down)--> CPU --(E)--> O
    trail: read from I, push up, read back down, emit to O.
    """
    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "BUF": "buf", "I": "input", "O": "output"}
    g.pipes = [
        Pipe("in", "I", E, "CPU", W),  # I -> CPU (CPU receives on W)
        Pipe("up", "CPU", N, "BUF", S),  # CPU -> BUF (CPU sends on N)
        Pipe("down", "BUF", S, "CPU", N),  # BUF -> CPU (CPU receives on N)
        Pipe("out", "CPU", E, "O", W),  # CPU -> O (CPU sends on E)
    ]
    g.trail = [
        Instr("@"),
        Instr("r", "in"),  # read input
        Instr("s", "up"),  # push into ring
        Instr("r", "down"),  # read back from ring
        Instr("s", "out"),  # emit
        Instr("H"),
    ]
    return g
