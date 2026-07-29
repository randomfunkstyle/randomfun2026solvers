# A layout manager: declare the blocks, solve the placement and the routes

Today a machine's geometry is about thirty hand-tuned registries in
`lm1/machine.py` — `MEM_PAD`, `ROM_ROWS`, `TIER_LAYOUT`, `SEEK_TIER_LAYOUT`,
`SLAB_PITCH`, `store_offset`, `BLOCK_X0` and the rest — each found by a manual
sweep over one or two parameters. The M9–M14 log is largely a record of those
sweeps. This document proposes replacing the *search*, not the knowledge.

The goal: **declare blocks and constraints, and have placement and routing
solved per build.**

## This is not a new idea here — it is already working at small scale

`lm1/d3_router.py` solves, on **every build**, the panel cluster's row and the
west-to-east order of its twelve channel columns (a topological sort of "P may
sit west of Q only if Q's entry row misses P's span and P's exit row misses
Q's"), and rejects a cluster row whose relation has a cycle with a message
rather than a crash. `scratch/deadman3d-opt/panel_place.py` enumerates the
arrangements around it and scores each by the bounding box its parts force.

That is a layout manager for one subsystem. The proposal is to generalise it.

## Four properties any solver must respect

**1. Binding is adversarial, so placement and routing do not separate.**
`ARCH.md` §7.1 decides which pipe an `s`/`r` binds to by *relative distance to
rivals*. Moving block A can silently rebind block B's `s`. This is not
hypothetical — it has bitten repeatedly:

* `SEEK_MEM_PAD` sat four columns above its floor because the **input room**
  was one of the three rivals every memory `r` is weighed against (M11c);
* narrowing the CPU made the memory-response pipe a *closer* rival and broke
  the classic build entirely (`SEEK_SLAB_PITCH`'s docstring);
* the taped gate's tightest `s` has **three cells of margin** before the north
  write arm binds to the downstream pipe and reads come back from the wrong
  bank **silently** (M13).

So `check_bindings` must run **inside** the search loop, on every candidate. A
place-then-route design will emit wrong machines that pass every geometric
check.

**2. A room is a routing primitive, not an obstacle.** `R` receives with no
distance term (`SPEC.md`), so a room *replaces* a pipe. The solver must be able
to insert one — and to know when it pays, which needs a cost model, not
geometry.

**3. Translation only.** Little-man glyphs are direction-semantic — `x` branch
chirality, the trie's clockwise turn, the collector walk — so a reflected or
rotated block is a **regenerated** unit, not a transformed one.

**4. Cost is length x frequency, never length.** `cpu->drum` is the longest pipe
in the machine at 437 cells and worth **0.019%**; `adapter->store` was 60 cells
and worth **5.92%**. A solver minimising total pipe length optimises the wrong
thing.

## The cost function, measured

    1,112,472 tour ticks per accesses-weighted pipe cell

From `chain_pad` (M13): +5 cells -> +4,187,905 ticks, +15 -> +12,564,227 —
linear to five figures, and within 5% of M12's independently measured
1,060,929. This is what makes "route optimally" an objective rather than a
slogan.

Two corrections it encodes, both of which cost real work before they were
understood:

* **`q` counts values anywhere in a pipe, not just at its destination**, so a
  consumer can work concurrently with transit. A seek costs
  `max(pipe, cascade)`: padding pays the full rate, shortening recovers only
  the overhang.
* **A forwarder's floor is a six-cell loop**, because a long pipe is a deep FIFO
  that pipelines a multi-word request while one man emits one word per six
  ticks. So *removing* a forwarder is worth more than shortening one, and a
  chain of forwarders is worse than one room plus a stub.

  Its re-serialisation cost is **not a single number**, which Phase 1 found by
  backing it out of `STORE_ANSWER_WEST`'s four measured builds: **1.48 / 0.70 /
  3.71** cells, against the 5.2 this document first claimed. 5.2 was measured
  re-serialising a multi-word *request*; an answer is one word. The constant
  still ranks all four builds exactly as the tour did — including that deleting
  the last forwarder is a **+4.1% regression** — so it is sound for a solver
  choosing *whether* to place a room, and unsound for one choosing *how many*.

## The interface

    blocks : rect + ports(side, fixed | free offset); translation only
    pipes  : src port -> dst port, min_length, traffic weight
    rooms  : the solver may insert a forwarder where it beats a pipe
    cost   : sum(cells x weight) at the rate above
    verify : check_bindings on every candidate, not just the winner

## Phase 1 result: viable (2026-07-29)

Told only the blocks, the free space, the measured traffic weights and the tick
rate — and nothing about which primitive to use anywhere — the prototype in
`scratch/layout1/` **picks the same primitive the hand build picked on all seven
store request legs**, and its predicted saving lands within **1.4%** of the
measured one (91,039,146 against 92,374,814 ticks).

Six ladder rungs, each answered on paper first, all reproduced
(`uv run python -m scratch.layout1.run`; `pytest scratch/layout1` → 11 passed).
Rung 3 is the one that kills a naive objective: minimising *length* ties across
43 placements, and only the weighted objective is unique. Rung 5 is the one that
proves the design — two candidates identical in cost, area and every geometric
check, separated only by §7.1's arithmetic, and the wrong one refused with
`'r' at (13,13) must bind 'rom' but distances are [('mem_resp', 8), ('rom', 9)]`.

Three results beyond agreement:

* **The ablation reproduces the history.** Forbid rooms and reaches → 58 cells.
  Allow a room → a teleport in the corridor. Allow the reach → gate 0's roof
  grown by **exactly 31 rows**, which is `memory_taped`'s own `north_grow[0]`,
  arrived at independently.
* **"A room can reach its caller but not its callee" is a conclusion, not an
  input.** Offered every climb from 0 to 31 rows, 30 of 33 candidates are thrown
  out by the production `check_bindings`.
* **It reproduces the gate's three-cell margin and is one cell stricter,
  correctly.** M13 found the north write arm misbinds at L=4; through
  `check_bindings`, L=3 is already refused, because at L=3 the distances are
  *equal* and the checker will not let reading order decide.

One thing the model **cannot** express, named rather than bodged: widening the
answer collector (`STORE_ANSWER_WEST`) carries its `s` west across the wall,
which is a *regenerated* block rather than a translated one — property 3. The
prototype's `Block` forbids growing a glyph-carrying side.

## Phases

**Phase 1 — falsifiable prototype.** Build the solver against a problem whose
answer is already known and measured: the store's **request legs**, where three
teleports were placed by hand and priced at −0.52%, −5.92% and −7.48%. Feed it
the blocks and constraints and ask whether it **rediscovers them**. Start with
small routing cases whose correct output is known by construction, then scale
up to the real subsystem. A negative result here is cheap and conclusive.

**Phase 2 — point it at `deadman-3d_hires`.** That is where the hand-tuning
debt is largest and the geometry least explored — it is currently chasing the
64x48 machine's registries one at a time, by hand.

**Phase 3 — only then** consider which of `machine.py`'s registries a solver
should own. Most probably stay: they encode facts a solver cannot derive, such
as which bank the traffic favours or which fold a program's literals survive.

## Honest scoping

As a **tick** optimiser for 64x48 DOOM this is late: the layout seam is nearly
exhausted — deleting *every* store pipe would still leave **27.88%** of the run
blocked (M14/OPCODES), and the remaining levers are program-level. As
**engineering leverage** it is clearly worth it: it removes the manual sweep
that has dominated this work, and lets a second machine inherit a first one's
geometry without a person in the loop.
