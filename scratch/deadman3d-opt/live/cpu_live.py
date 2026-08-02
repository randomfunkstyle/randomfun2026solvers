#!/usr/bin/env python3
"""Register liveness over the CPU's walk graph — "is glyph G transparent here?"
answered as a proof instead of a 25-second tour.

The optimisation that gates every other one on this machine is *rerouting a man
over a shorter path*.  A shorter path crosses cells that belong to other code,
and walking a glyph **executes** it, so the question is always the same:

    is glyph G transparent to a man passing over it on path P?

and the rule is liveness:

    G is transparent iff (a) it does not steer, branch, split, halt or touch a
    pipe, and (b) every register it writes is dead at that point on P.

This module builds that as dataflow.

Nodes
-----
A node is ``(x, y, h)`` — the man is *standing* on ``(x, y)`` with heading ``h``,
about to execute the glyph there.  (SPEC.md line 39: "in a snapshot at tick t the
man is standing on a cell whose glyph has not yet fired".)  Successors are: fire
the glyph, which may change the heading, then step one cell along the new
heading.  Branch glyphs (``X d a x``) and ``U`` contribute *every* heading they
could take — an over-approximation, which is the sound direction: extra edges can
only make more registers live, and a register we wrongly believe live only costs
us a reroute we refuse to claim.

The graph is seeded from the CPU man's spawn ``@`` and grown by reachability, so
it is the real walk graph and not a guess about which arms exist.

Liveness
--------
Standard backward may-analysis over that graph::

    LIVE_in(n)  = needs(G_n) | (LIVE_out(n) - writes(G_n))
    LIVE_out(n) = union of LIVE_in(s) for s in succ(n)

Terminals (halt, a wall, a non-instruction) have ``LIVE_out = {}``.  The graph is
strongly cyclic — the fetch/execute loop — so this is a worklist fixpoint, not a
topological sweep.  The fixpoint is what makes the interesting answers fall out
for free: ``BP`` is dead on every return path *because* the fetch prologue's
``b`` reloads it before the trie's ``x`` glyphs read it, and the fixpoint finds
that without being told.

Why not Z3
----------
Liveness here is a monotone fixpoint over a finite lattice of eight elements
(subsets of {A,B,BP}) on ~10k nodes.  Encoding it in Z3 would mean asserting the
same fixpoint as a set of implications and asking for the *least* model, which
SMT does not give you directly (you get *a* model; the greatest one, all-live, is
also a model and is useless).  A worklist computes the least fixpoint in
milliseconds and is the standard tool.  Z3 earns its place one level up — over
the *choice* of reroute, where the search is combinatorial — and
:mod:`live.reroute` uses it there.  See ``LIMITS`` at the bottom of this file for
what the liveness answer does and does not cover.
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.mansem import glyph_effect  # noqa: E402

# ── geometry ─────────────────────────────────────────────────────────────────
#: heading name -> (dx, dy).  Screen coordinates: +y is south.
DIRS: dict[str, tuple[int, int]] = {"E": (1, 0), "S": (0, 1), "W": (-1, 0), "N": (0, -1)}
#: clockwise order, so ``CW[i+1]`` is one clockwise turn from ``CW[i]``.
CW = ("E", "S", "W", "N")
_IDX = {h: i for i, h in enumerate(CW)}
STEER = {">": "E", "<": "W", "^": "N", "v": "S", "V": "S"}


def cw(h: str) -> str:
    return CW[(_IDX[h] + 1) % 4]


def ccw(h: str) -> str:
    return CW[(_IDX[h] - 1) % 4]


#: Glyphs that move values through pipes.  Even when their register writes are
#: dead, crossing one is fatal: ``s``/``S`` inject a word into a pipe another
#: block is protocol-synchronised with, ``r``/``R``/``U`` *consume* one, and any
#: of them can block the man indefinitely.  ``q`` is deliberately **not** here —
#: it only counts, so it is transparent whenever ``BP`` is dead.
PIPE_TRAFFIC = frozenset("sSrRU")

#: Non-instruction glyphs a man may legally stand on: the spawn marker.  Every
#: other non-instruction under a man is a ``bad-op`` or a wall.
SPAWN = "@"


def is_instruction(g: str) -> bool:
    if g == SPAWN:
        return True
    try:
        glyph_effect(g)
    except ValueError:
        return False
    return True


def effect(g: str):
    """:func:`mansem.glyph_effect`, with ``@`` treated as the nop it behaves as.

    Verified against the reference interpreter in ``isa_check.py``: a man spawned
    on ``@`` leaves A, B, BP and his heading untouched on the tick he stands there.
    """
    return glyph_effect("." if g == SPAWN else g)


# ── the walk graph ───────────────────────────────────────────────────────────
Node = tuple[int, int, str]


@dataclass
class WalkGraph:
    """Reachable ``(x, y, heading)`` states of one man, and their liveness."""

    grid: list[str]
    succ: dict[Node, tuple[Node, ...]]
    live_in: dict[Node, frozenset[str]]
    live_out: dict[Node, frozenset[str]]
    #: nodes that ran off the graph — hit a wall, halted, or a non-instruction.
    terminal: set[Node]

    def at(self, x: int, y: int) -> str:
        row = self.grid[y] if 0 <= y < len(self.grid) else ""
        return row[x] if 0 <= x < len(row) else " "

    def headings_on(self, x: int, y: int) -> frozenset[str]:
        """Which headings the man is ever standing on ``(x, y)`` with."""
        return frozenset(h for h in CW if (x, y, h) in self.succ)

    def occupied(self, x: int, y: int) -> bool:
        return bool(self.headings_on(x, y))


def exits(g: str, h: str) -> tuple[str, ...]:
    """Every heading the man may leave glyph ``g`` with, entering it on ``h``.

    Over-approximates the two-way and three-way branches; that is the sound
    direction for a *may*-liveness (more successors, more live registers).
    """
    eff = effect(g)
    if eff.heading == "steer":
        return (STEER[g],)
    if eff.heading == "branch":
        if g == "d":
            return (cw(h), h)          # clockwise if BP > 0, else straight
        if g == "a":
            return (ccw(h), h)         # counter-clockwise if BP > 0, else straight
        if g == "x":
            return (cw(h), ccw(h))     # ALWAYS turns: low bit of BP picks which
        return (cw(h), h, ccw(h))      # X: sign(A), so straight is possible too
    if eff.heading == "halt":
        return ()
    if eff.turns_on_read:              # U: turns away from the side it read from
        return CW
    return (h,)                        # keep


def build(grid: list[str], start: Node) -> WalkGraph:
    """Reachable walk graph from ``start``, then the liveness fixpoint on it."""
    succ: dict[Node, tuple[Node, ...]] = {}
    terminal: set[Node] = set()
    work = deque([start])
    seen = {start}

    def cell(x: int, y: int) -> str:
        row = grid[y] if 0 <= y < len(grid) else ""
        return row[x] if 0 <= x < len(row) else " "

    while work:
        n = work.popleft()
        x, y, h = n
        g = cell(x, y)
        if not is_instruction(g):
            succ[n] = ()
            terminal.add(n)
            continue
        outs = []
        for h2 in exits(g, h):
            dx, dy = DIRS[h2]
            m = (x + dx, y + dy, h2)
            outs.append(m)
            if m not in seen:
                seen.add(m)
                work.append(m)
        succ[n] = tuple(outs)
        if not outs:
            terminal.add(n)

    pred: dict[Node, list[Node]] = defaultdict(list)
    for n, outs in succ.items():
        for m in outs:
            pred[m].append(n)

    live_in: dict[Node, frozenset[str]] = {n: frozenset() for n in succ}
    live_out: dict[Node, frozenset[str]] = {n: frozenset() for n in succ}
    wl = deque(succ)
    inq = set(succ)
    while wl:
        n = wl.popleft()
        inq.discard(n)
        out: set[str] = set()
        for m in succ[n]:
            out |= live_in.get(m, frozenset())
        x, y, _ = n
        g = cell(x, y)
        eff = effect(g) if is_instruction(g) else None
        ins = frozenset(out) if eff is None else frozenset(eff.needs | (out - eff.writes))
        if ins != live_in[n] or frozenset(out) != live_out[n]:
            live_in[n], live_out[n] = ins, frozenset(out)
            for p in pred[n]:
                if p not in inq:
                    inq.add(p)
                    wl.append(p)
    return WalkGraph(grid, succ, live_in, live_out, terminal)


# ── the transparency query ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str

    def __bool__(self) -> bool:
        return self.ok


def transparent(g: str, live: frozenset[str], heading: str | None = None) -> Verdict:
    """Is walking over glyph ``g`` a no-op for a man whose live set is ``live``?

    ``live`` is the set of registers live **after** the crossing — i.e. whose
    current value the rest of the path still needs.  ``heading`` is the direction
    the man is travelling; a steer that points where he is already going is
    idempotent, so ``<`` is transparent to a westbound man.  Pass ``None`` to
    refuse every steer.

    The predicate is symmetric in a way the sweep relies on: "executing this
    glyph changes nothing observable" is the same statement as "*not* executing
    it changes nothing observable", so the same call decides both whether a new
    path may **cross** a cell and whether an old path may **abandon** one.
    """
    if g in ("", " ", "."):
        return Verdict(True, "nop")
    if not is_instruction(g):
        return Verdict(False, f"{g!r} is not an instruction (wall / bad-op)")
    eff = effect(g)
    if eff.heading == "steer":
        if heading is not None and STEER[g] == heading:
            return Verdict(True, f"{g!r} steers {heading}, already heading {heading}")
        return Verdict(False, f"{g!r} steers — derails the man")
    if eff.heading == "branch":
        return Verdict(False, f"{g!r} branches — derails the man")
    if eff.heading == "split":
        return Verdict(False, "Y splits the man")
    if eff.heading == "halt":
        return Verdict(False, "H halts the man")
    if g in PIPE_TRAFFIC:
        return Verdict(False, f"{g!r} moves a value through a pipe")
    clash = eff.writes & live
    if clash:
        return Verdict(False, f"{g!r} writes {','.join(sorted(clash))}, live here")
    return Verdict(True, f"{g!r} writes {','.join(sorted(eff.writes)) or 'nothing'}, "
                         f"live={{{','.join(sorted(live)) or ''}}}")


def walk_back(w: WalkGraph, cells: list[tuple[int, int]],
              live_after: frozenset[str], heading: str | None = None,
              ) -> list[tuple[tuple[int, int], str, frozenset[str], Verdict]]:
    """Check a candidate run of cells, in path order, against ``live_after``.

    ``live_after`` is the live set at the point the run *rejoins* known code —
    normally ``w.live_in[join_node]``.  Liveness is propagated backwards through
    the run (each cell's own reads and writes count), and each cell is tested
    against the live set that holds immediately after it.

    Returns one row per cell, **in path order**.
    """
    live = live_after
    rows = []
    for x, y in reversed(cells):
        g = w.at(x, y)
        v = transparent(g, live, heading)
        rows.append(((x, y), g, live, v))
        if is_instruction(g):
            eff = effect(g)
            live = frozenset(eff.needs | (live - eff.writes))
    rows.reverse()
    return rows


# ── loading the machine ──────────────────────────────────────────────────────
def load(path: str | Path) -> list[str]:
    rows = Path(path).read_text().split("\n")
    while rows and not rows[-1]:
        rows.pop()
    n = max(len(r) for r in rows)
    return [r.ljust(n) for r in rows]


def find_spawn(grid: list[str], box: tuple[int, int, int, int]) -> Node:
    """The ``@`` inside ``box`` = (x, y, w, h).  A man always starts facing east."""
    x0, y0, bw, bh = box
    for y in range(y0, y0 + bh):
        for x in range(x0, x0 + bw):
            if grid[y][x] == "@":
                return (x, y, "E")
    raise LookupError("no @ in box")


#: What this model does **not** decide.  Read before trusting a "proved" verdict.
LIMITS = """
1. **Values, not ticks.**  Liveness proves the registers survive.  It proves
   nothing about *when* the man arrives.  A shortened return path reaches the
   fetch's `r` earlier, and every `r`/`s` in this machine binds by §7.1 nearest —
   so a reroute that is register-perfect can still change which pipe wins a race
   or how long a send blocks.  That is the residual risk in every "proved" row,
   and it is why the tour is still the acceptance test.
2. **One man only.**  The CPU room holds a single runner, so this graph never has
   to reason about collisions.  The seek, stream and router men are *not* in it.
   A reroute inside the CPU is safe from them by construction (different rooms);
   the same prover would need a product construction to say anything about theirs.
3. **`q` is transparent, its pipe is not.**  `q` writes BP from the nearest
   incoming pipe's occupancy and consumes nothing, so crossing it is inert when BP
   is dead.  `s S r R U` are refused outright even when their register writes are
   dead, because they move a word.
4. **Refusals are `maybe`, not `no`.**  Branch glyphs contribute every heading
   they could take, so the live set is an over-estimate.  "Proved transparent" is
   therefore sound, but "not transparent" can be spurious — a register may be live
   only along an arm that is infeasible for the actual BP/ACC values.  A real BP
   prover (:class:`mansem.BPFacts`) would tighten this; nothing here needs it yet.
5. **Rewriting a cell is a separate obligation.**  Turning a `.` into a `^` is
   inert for the rerouted man by construction and inert for anyone already heading
   north, but fatal to anyone crossing it east/west/south.  `reroute.candidates`
   checks `WalkGraph.headings_on` for exactly that, and refuses without it.
6. **Abandoning a cell is a third obligation, and it is the one that binds.**  A
   glyph the old path executed and the new one skips must itself be transparent.
   On this machine that check, not the crossing check, is what stops every one of
   the 21 lane drops — each at its own lane's last real operation.
"""
