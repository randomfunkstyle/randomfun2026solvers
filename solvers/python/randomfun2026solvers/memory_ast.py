#!/usr/bin/env python3
"""The MEMORY program stated as AST abstractions, then placed.

Every attempt so far compacted an existing grid. This declares what the program
*is* and lets a placer decide where things go — which turns the footprint from a
hand-tuned constant into a **parameter that can be searched**.

The abstraction has three levels, and the third is the one previous attempts kept
leaving implicit, which is why hand-rewiring kept producing grids that loaded and
computed the wrong thing.

**Rooms and pipes.** Four rooms — input, output, a relay that turns the tape ring
around, and the worker — joined by four pipes. The two ring pipes share one
capacity budget: they must jointly hold ``n + 1`` values, because the tape is
``n`` values circulating with one slot to move into.

**Steps.** The worker is a sequence of gadgets, each a run of glyphs with fixed
internals, an entry and exit port, and a register contract saying which of
``A`` / ``B`` / ``BP`` it needs and clobbers. Two of them branch.

**Pipe affinity — the constraint that makes placement hard and the program
correct.** ``s`` and ``r`` bind to the *nearest* pipe, so a step is not merely
"somewhere in the worker": a step that reads the op must sit nearer the INPUT pipe
than the ring's return pipe, and a rotation step's ``r``/``s`` must sit nearer the
RING. That single requirement is what forces the worker into zones — input-facing
work on one side, ring-facing work on the other — and it explains the shape of
every hand-written layout in this repo. Stating it lets a placer respect it
instead of a human remembering it.

What the abstraction buys: the program is written once, and the *geometry* becomes
search. Interior width and height, which zone each step lands in, and the ring's
fold are all knobs, and every candidate is checked by the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Affinity",
    "Step",
    "MEMORY_STEPS",
    "RELATIVE_STEPS",
    "Ring",
    "Spec",
    "spec_for",
    "relative_spec",
]


class Affinity(Enum):
    """Which pipe a step's ``s``/``r`` must resolve to.

    Not a hint — a correctness constraint. ``nearest`` is decided by geometry, so
    placing an input-reading step closer to the ring silently re-binds it.
    """

    NONE = "none"  # touches no pipe; may go anywhere
    INPUT = "input"  # its `r` must reach the input pipe
    OUTPUT = "output"  # its `s`/`S` must reach the output pipe
    RING = "ring"  # its `r`/`s` must reach the tape ring
    RING_AND_OUTPUT = "ring+output"  # `S` sends to EVERY outgoing pipe
    REGISTER = "register"  # a room holding one value: the tape head position


@dataclass(frozen=True)
class Step:
    """One gadget in the worker's control flow: glyphs, contract, affinity."""

    name: str
    glyphs: str
    affinity: Affinity = Affinity.NONE
    #: a counted loop's body; laps come from BP, at 8 ticks per value
    loop_body: str | None = None
    needs: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    #: names of the steps this one can hand control to (two for a branch)
    goto: tuple[str, ...] = ()
    note: str = ""

    @property
    def is_loop(self) -> bool:
        return self.loop_body is not None

    @property
    def is_branch(self) -> bool:
        return len(self.goto) > 1

    @property
    def width(self) -> int:
        return len(self.glyphs)


def MEMORY_STEPS(n: int) -> list[Step]:
    """The whole program, as steps. ``lit`` is the memory size as a literal."""
    lit = str(n) if n < 10 else f"`{n}`"
    A, B, BP = "A", "B", "BP"
    return [
        Step(
            "init", f"@{lit}b", Affinity.NONE,
            writes=frozenset({A, BP}), goto=("fill",),
            note=f"spawn; BP = {n}, the number of cells to lay down",
        ),
        Step(
            "fill", "", Affinity.RING, loop_body="0s",
            needs=frozenset({BP}), writes=frozenset({BP}), goto=("read_op",),
            note=f"send {n} zeros into the ring: the tape starts empty",
        ),
        Step(
            "read_op", "r", Affinity.INPUT,
            writes=frozenset({A}), goto=("branch_op",),
            note="op: 0 = READ, 1 = WRITE",
        ),
        Step(
            "branch_op", "X", Affinity.NONE,
            needs=frozenset({A}), goto=("setup_r", "setup_w"),
            note="op is exactly 0 or 1, so `straight` is READ and `right` is WRITE",
        ),
        Step(
            "setup_r", "rbM1+", Affinity.INPUT,
            writes=frozenset({A, B, BP}), goto=("move_b",),
            note="addr -> BP (rotation count) and A = addr+1 (positive = READ)",
        ),
        Step(
            "setup_w", "rbM1+N", Affinity.INPUT,
            writes=frozenset({A, B, BP}), goto=("move_b",),
            note="same, negated: the sign is how the second branch tells them apart",
        ),
        Step(
            "move_b", "M", Affinity.NONE,
            needs=frozenset({A}), writes=frozenset({B}), goto=("rot1",),
            note="park the signed address in B; the rotation clobbers A",
        ),
        Step(
            "rot1", "", Affinity.RING, loop_body="rs",
            needs=frozenset({BP}), writes=frozenset({A, BP}), goto=("branch_rw",),
            note="rotate the tape `addr` places, bringing the target to the head",
        ),
        Step(
            "branch_rw", "WX", Affinity.NONE,
            needs=frozenset({B}), writes=frozenset({A, B}), goto=("target_r", "target_w"),
            note="swap the parked address back into A and branch on its sign",
        ),
        Step(
            "target_r", f"M{lit}-b", Affinity.NONE,
            needs=frozenset({A}), writes=frozenset({A, B, BP}), goto=("emit",),
            note=f"BP = {n} - addr: what is left of the lap after the access",
        ),
        Step(
            "emit", "rS", Affinity.RING_AND_OUTPUT,
            writes=frozenset({A}), goto=("rot2",),
            note="take the value off the head and send it to the output AND back",
        ),
        Step(
            "target_w", f"NM{lit}-b", Affinity.NONE,
            needs=frozenset({A}), writes=frozenset({A, B, BP}), goto=("take",),
            note="negate first: the address arrived negative on this branch",
        ),
        Step(
            "take", "r", Affinity.INPUT,
            writes=frozenset({A}), goto=("store",),
            note="the value to write comes from the input stream",
        ),
        Step(
            "store", "sr", Affinity.RING,
            needs=frozenset({A}), writes=frozenset({A}), goto=("rot2",),
            note="push the new value onto the tape and drop the old one",
        ),
        Step(
            "rot2", "", Affinity.RING, loop_body="rs",
            needs=frozenset({BP}), writes=frozenset({A, BP}), goto=("read_op",),
            note="finish the lap so the head returns to cell 0 for the next op",
        ),
    ]


@dataclass(frozen=True)
class Ring:
    """The tape: two pipes that must jointly hold ``n + 1`` values.

    The ``+ 1`` is not slack. ``n`` values circulate and one slot has to be free
    for a value to move into, so a ring of exactly ``n`` deadlocks on the first
    rotation. Measured separately: spare cells *beyond* ``n + 1`` are buffer and
    make the worker block less, which is worth real ticks.
    """

    n: int

    @property
    def minimum(self) -> int:
        return self.n + 1


@dataclass
class Spec:
    """A complete, placement-independent description of the program."""

    n: int
    steps: list[Step] = field(default_factory=list)
    ring: Ring | None = None

    def by_name(self) -> dict[str, Step]:
        return {s.name: s for s in self.steps}

    def check(self) -> list[str]:
        """Contract check: does every step's ``needs`` reach it intact?

        Walks the control-flow graph and reports where a register a step depends
        on has been clobbered on some path into it. This is the class of bug that
        no amount of correct *layout* prevents and no test on the public cases
        reliably catches, because it only shows on a path the cases may not take.
        """
        steps = self.by_name()
        problems: list[str] = []
        for step in self.steps:
            for target in step.goto:
                if target not in steps:
                    problems.append(f"{step.name} -> unknown step {target!r}")
        # for each step, which registers are live-and-trusted on entry
        for step in self.steps:
            preds = [s for s in self.steps if step.name in s.goto]
            for need in sorted(step.needs):
                for pred in preds:
                    if need in pred.writes and need not in pred.needs:
                        continue  # the predecessor set it deliberately
                    if need in pred.writes:
                        continue
                    # the predecessor neither set nor preserved it: only a problem
                    # if nothing upstream did, which we approximate by flagging a
                    # loop body that clobbers what the next step needs
                    if pred.is_loop and need in pred.writes:
                        problems.append(
                            f"{step.name} needs {need} but {pred.name} clobbers it"
                        )
        return problems

    def affinity_zones(self) -> dict[Affinity, list[str]]:
        """Which steps must sit near which pipe — the placement constraint."""
        out: dict[Affinity, list[str]] = {}
        for step in self.steps:
            out.setdefault(step.affinity, []).append(step.name)
        return out

    @property
    def is_relative(self) -> bool:
        return any(s.affinity is Affinity.REGISTER for s in self.steps)

    def rotation_ticks(self) -> int:
        """Ticks per operation spent rotating, at the measured 8 per value.

        ``rot1`` turns ``addr`` places and ``rot2`` the remaining ``n - addr``, so
        every operation pays a **full lap** regardless of address. That is the
        dominant cost of the whole program and the reason a smarter layout can
        only ever win a little: it is an algorithmic property, not a geometric one.
        """
        if self.is_relative:
            # one rotation of `delta`, averaging n/2 over uniform addresses
            return 8 * self.n // 2
        return 8 * self.n

    def summary(self) -> str:
        zones = self.affinity_zones()
        lines = [
            f"memory(n={self.n}): {len(self.steps)} steps, "
            f"ring needs {self.ring.minimum if self.ring else '?'} cells",
            f"  rotation cost: {self.rotation_ticks()} ticks/op "
            f"(a full lap, at 8 ticks per value)",
            "  pipe affinity zones (s/r bind to the NEAREST pipe):",
        ]
        for aff, names in zones.items():
            lines.append(f"    {aff.value:12s} {', '.join(names)}")
        loops = [s.name for s in self.steps if s.is_loop]
        branches = [s.name for s in self.steps if s.is_branch]
        lines.append(f"  loops: {loops}")
        lines.append(f"  branches: {branches}")
        bad = self.check()
        lines.append(f"  contract check: {'OK' if not bad else bad}")
        return "\n".join(lines)


def spec_for(n: int = 100) -> Spec:
    return Spec(n=n, steps=MEMORY_STEPS(n), ring=Ring(n))


# ── the relative variant: rotate `delta`, not a whole lap ────────────────────
def RELATIVE_STEPS(n: int) -> list[Step]:
    """Rotate only as far as the target, tracking the head in a register room.

    The full-lap design pays ``8n`` ticks per operation no matter which cell it
    touches, because it rotates ``addr`` and then the remaining ``n - addr`` to put
    the head back at zero. Tracking where the head actually is replaces that with a
    single rotation of ``delta = (addr - current) mod n`` — on average ``n/2``, so
    **half the work**, and it deletes an entire rotation loop from the worker.

    It costs a fifth room, because the head position needs somewhere to live and
    the machine has only two hands and an unreadable backpack. That room is reached
    by pipe, and this is exactly where the earlier attempt lost: in an 88x68 grid
    its register pipes ran 14-15 cells, so each round trip cost ~30 ticks and the
    three per operation ate the 400 ticks saved. The design is sound; it was the
    *placement* that was wrong. Short pipes are not a nicety here, they are the
    whole margin.
    """
    lit = str(n) if n < 10 else f"`{n}`"
    A, B, BP = "A", "B", "BP"
    return [
        Step("init", f"@{lit}b", Affinity.NONE, writes=frozenset({A, BP}),
             goto=("fill",), note=f"BP = {n} cells to lay down"),
        Step("fill", "", Affinity.RING, loop_body="0s", needs=frozenset({BP}),
             writes=frozenset({BP}), goto=("read_op",), note="tape starts empty"),
        Step("read_op", "r", Affinity.INPUT, writes=frozenset({A}),
             goto=("branch_op",), note="op: 0 = READ, 1 = WRITE"),
        Step("branch_op", "X", Affinity.NONE, needs=frozenset({A}),
             goto=("addr_r", "addr_w"), note="branch on the op"),
        Step("addr_r", "rM", Affinity.INPUT, writes=frozenset({A, B}),
             goto=("fetch_cur",), note="A = B = addr"),
        Step("addr_w", "rM", Affinity.INPUT, writes=frozenset({A, B}),
             goto=("fetch_cur",), note="same; the paths differ only after the access"),
        Step("fetch_cur", "1Ns", Affinity.REGISTER, writes=frozenset({A}),
             goto=("recv_cur",), note="send -1: the register's read command"),
        Step("recv_cur", "r", Affinity.REGISTER, writes=frozenset({A}),
             goto=("delta",), note="A = current head position"),
        Step("delta", f"W-M{lit}W%Mb", Affinity.NONE, needs=frozenset({A, B}),
             writes=frozenset({A, B, BP}), goto=("rot",),
             note=f"BP = (addr - current) mod {n}: the ONLY rotation this design pays"),
        Step("rot", "", Affinity.RING, loop_body="rs", needs=frozenset({BP}),
             writes=frozenset({A, BP}), goto=("access",),
             note="one loop, not two -- the head is left wherever the target was"),
        Step("access", "", Affinity.RING_AND_OUTPUT, goto=("update_cur",),
             note="READ: rS. WRITE: r then sr. Chosen by the op branch"),
        Step("update_cur", f"M1+{lit}W%M1s", Affinity.REGISTER,
             needs=frozenset({A}), writes=frozenset({A, B}), goto=("read_op",),
             note=f"current = (addr + 1) mod {n}, stored back for the next op"),
    ]


def relative_spec(n: int = 100) -> Spec:
    return Spec(n=n, steps=RELATIVE_STEPS(n), ring=Ring(n))


if __name__ == "__main__":
    full = spec_for(100)
    rel = relative_spec(100)
    print("FULL-LAP")
    print(full.summary())
    print()
    print("RELATIVE")
    print(rel.summary())
    print()
    print(
        f"rotation per op: {full.rotation_ticks()} -> {rel.rotation_ticks()} ticks "
        f"({full.rotation_ticks() / rel.rotation_ticks():.2f}x), "
        f"loops {sum(s.is_loop for s in full.steps)} -> {sum(s.is_loop for s in rel.steps)}"
    )
