"""``Machine.regions`` is the only record of what a CPU cell means. Pin it.

A generated ``.man`` carries no comments, so the region map is the whole of the
machine's documentation: it feeds ``man_debug.DebugMap``, the ``--html`` /
``--json`` overlays, ``tools/heatmap.mjs`` and :mod:`lm1.profile`. Two failure
modes cost real work before these cases existed, and neither one breaks a build,
renders a wrong pixel, or fails any other test:

* **A hole.** The whole seek tail — the send, the westbound corridor, the flush
  loop, the counted discard — sat inside no ``cpu:*`` region at all, so a profile
  attributed 20% of the run to ``unattributed`` and the only way to read it was
  to hand-slice raw row ranges out of the grid.
* **An over-reaching box.** ``cpu:trie`` was declared from ``y0`` for the full
  lane span while its first ``x`` was fourteen rows lower, and
  ``cpu:slab:<seek jump>`` was declared a whole slab pitch wide while holding
  nothing but a drop column — so it owned the *next* slab's exit riser.
  ``profile._region_of`` hands a cell to the **smallest** box containing it, so
  an over-reaching box does not merely mislabel its own area, it silently
  absorbs its neighbours' heat. That is strictly worse than no box.

So there are three properties, and they are the file:

1. every non-blank cell in the CPU room is inside some ``cpu:*`` region;
2. every box is the tight bounding box of the cells its part drew;
3. no part loses a cell it drew to a *different* part's box — the smallest-wins
   rule must actually hand each part its own cells back.

All of it is pure: :func:`machine.build_cpu` on a checked-in program, no
placement, no simulation, no artifact. It runs in milliseconds, in the fast
tier, in the style of ``tests/test_lm1_cpu_trie_pack.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402

#: Both shapes of structures band, from checked-in programs so the fast tier can
#: reach them. ``deadman-3d`` is the only slug with a seek drum, and the seek
#: tail is the half of the band these cases were written for; ``little-little-man``
#: is a classic build, which is what every other machine emits.
SEEK_SLUG = "deadman-3d"
PLAIN_SLUG = "little-little-man"


def _cpu(slug: str, *, seek: bool):
    program = programs.load(slug)
    if seek:
        program = machine.seek_split(
            program, threshold=machine.SEEK_THRESHOLD, ops=machine.SEEK_OPS
        )
        order = list(machine.LANE_ORDER[slug])
        used = {op.mnemonic for op in program.ops_used}
        at = min(
            (order.index(c) for c in ("JMPF", "BRZ", "BRN") if c in order),
            default=len(order),
        )
        for new in ("JMPS", "BRZS", "BRNS"):
            if new in used and new not in order:
                order.insert(at, new)
                at += 1
        plan = machine.plan(program, middle_order=order)
    else:
        order = None
        plan = machine.plan(program)
    return machine.build_cpu(
        program,
        plan,
        mem_pad=22,
        seek=seek,
        drain_unit_bits=machine.DRAIN_UNIT_BITS.get(slug, 0),
    )


CASES = [pytest.param(SEEK_SLUG, True, id="seek"), pytest.param(PLAIN_SLUG, False, id="classic")]


def _owner(x, y, regions):
    """``profile._region_of``, verbatim: the smallest box containing the cell."""
    best, area = None, None
    for name, (rx, ry, w, h) in regions.items():
        if rx <= x < rx + w and ry <= y < ry + h and (area is None or w * h < area):
            best, area = name, w * h
    return best


@pytest.mark.parametrize("slug,seek", CASES)
def test_every_cpu_cell_is_inside_some_region(slug, seek) -> None:
    """The hole. Not one glyph the CPU builder draws may be anonymous.

    This is the case that would have caught the seek tail: its rows are the
    machine's second-hottest block and they belonged to nothing, which is why an
    agent had to write "the seek taken row lives at ``bottom+1``, inside no
    ``cpu:*`` region" instead of reading it off a profile.
    """
    cpu = _cpu(slug, seek=seek)
    orphans = sorted(
        (x, y, ch)
        for (x, y), ch in cpu.cells.items()
        if ch.strip() and not _owner(x, y, cpu.regions)
    )
    assert not orphans, f"{len(orphans)} unattributed cell(s), first: {orphans[:8]}"


@pytest.mark.parametrize("slug,seek", CASES)
def test_every_box_is_the_tight_bound_of_its_own_cells(slug, seek) -> None:
    """The over-reach, from the other side: no box may contain a blank margin.

    A box with slack is a box that will claim a neighbour the day the neighbour
    moves into the slack — which is precisely how ``cpu:slab:JMPS`` came to own
    ``cpu:slab:JMPF``'s exit riser. ``lane:<OP>`` is exempt and stated: a lane's
    box runs east to its *drop column*, which the lane does not draw.
    """
    cpu = _cpu(slug, seek=seek)
    for name, (rx, ry, w, h) in cpu.regions.items():
        if name.startswith("lane:") or name == "return:topbus":
            continue
        live = [
            (x, y)
            for y in range(ry, ry + h)
            for x in range(rx, rx + w)
            if cpu.cells.get((x, y), " ").strip()
        ]
        assert live, f"{name} is an empty box"
        xs, ys = [c[0] for c in live], [c[1] for c in live]
        assert (min(xs), min(ys), max(xs), max(ys)) == (rx, ry, rx + w - 1, ry + h - 1), (
            f"{name} box {(rx, ry, w, h)} has a blank margin; content is "
            f"x={min(xs)}..{max(xs)} y={min(ys)}..{max(ys)}"
        )


@pytest.mark.parametrize("slug,seek", CASES)
def test_no_part_loses_its_own_cells_to_another_box(slug, seek) -> None:
    """The property the other two only approximate: attribution actually works.

    Tightness is not enough on its own — two tight boxes can still overlap, and
    the smaller one takes the shared cells whether or not it drew them. So replay
    the builder's own record of who drew what and check that ``_region_of`` hands
    each part its cells back. A part is allowed to lose a cell to one of its own
    *children* (``discard:BRZ`` sits inside ``slab:BRZ`` on purpose) and to a
    part that genuinely shares the cell, which is enumerated below rather than
    waved at.
    """
    cpu = _cpu(slug, seek=seek)
    stolen: dict[tuple[str, str], int] = {}
    for name, cells in cpu.marks.items():
        if name not in cpu.regions:  # deduplicated: a child that filled its parent
            continue
        for x, y in cells:
            won = _owner(x, y, cpu.regions)
            # ``lane:<OP>`` is declared, not drawn: its box runs east to the drop
            # column so that "where does this opcode go" is one box, which means
            # it legitimately takes the drop's own head cell off ``drops``.
            if won != name and not str(won).startswith("lane:"):
                stolen[(name, won)] = stolen.get((name, won), 0) + 1
    # A cell may be drawn by more than one part — `soft` runs are how a drop
    # crosses a slab entry row at all — so "drew it" and "owns it" differ by
    # design.  What must not happen is a part losing cells *no* other part drew,
    # or losing them to a box that does not contain the same drawing.
    for (loser, winner), n in sorted(stolen.items()):
        assert winner is not None, f"{loser} lost {n} cell(s) to nothing"
        shared = set(cpu.marks.get(loser, ())) & set(cpu.marks.get(winner, ()))
        assert shared, (
            f"{loser} lost {n} cell(s) to {winner}, which never drew any of them — "
            f"{winner}'s box over-reaches into {loser}"
        )


def test_the_seek_tail_is_named_part_by_part() -> None:
    """The inventory the hole cost us, stated once so it cannot quietly go again.

    Every stage of a taken seek has a name, and the names are in *walk* order:
    the drops land on the taken row, run east to the ``s``, walk back west, flush
    the corridor to the sentinel, read the remainder, discard it, and rise. The
    walk and the flush are the two the tick argument turns on, so they are
    separately nameable or the argument cannot be measured.
    """
    cpu = _cpu(SEEK_SLUG, seek=True)
    want = {
        "seek:taken",
        "seek:send",
        "seek:walk",
        "seek:flush",
        "seek:sentinel",
        "seek:discard",
        "seek:riser",
    }
    assert want <= set(cpu.regions), sorted(want - set(cpu.regions))
    # ...and they are laid out as the docstring in ``build_cpu`` describes:
    # the send is east of the walk's end, and the flush is west of the walk.
    _sx, sy, sw, _sh = cpu.regions["seek:send"]
    wx, wy, ww, _wh = cpu.regions["seek:walk"]
    fx, _fy, fw, _fh = cpu.regions["seek:flush"]
    assert wy == sy + 1, "the walk is the row below the send"
    assert wx + ww - 1 >= _sx, "the walk starts under the send's turn"
    assert fx + fw <= wx, "the flush loop is west of the walk, and disjoint from it"
    assert sw == 2, "the send is the `s` and its turn, nothing else"


def test_a_jump_slabs_box_is_its_discard_loop_and_is_not_named_twice() -> None:
    """Why ``discard:<OP>`` is missing for a jump, and why that is correct.

    A jump slab *is* the 2x4 counted discard — there is no fan-out and no arms —
    so the nested ``discard:`` part fills its parent exactly. Emitting both would
    give one box two names and make ``_region_of``'s tie-break arbitrary, so
    :func:`machine._mark_boxes` keeps the outer one. A branch slab is strictly
    bigger than its loop and keeps both.
    """
    cpu = _cpu(PLAIN_SLUG, seek=False)
    slabs = [n.split(":", 1)[1] for n in cpu.regions if n.startswith("slab:")]
    assert slabs, "vacuous: no slabs"
    deduped = [m for m in slabs if f"discard:{m}" not in cpu.regions]
    assert deduped, "vacuous: nothing was deduplicated, so the rule is untested"
    for m in slabs:
        box = cpu.regions[f"slab:{m}"]
        loop = cpu.marks[f"discard:{m}"]  # the drawing survives the dedup
        xs, ys = [c[0] for c in loop], [c[1] for c in loop]
        inner = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        if f"discard:{m}" in cpu.regions:
            # a branch slab: strictly bigger than its loop, so both names survive
            assert cpu.regions[f"discard:{m}"] == inner != box
            assert box[2] * box[3] > inner[2] * inner[3], (m, box, inner)
        else:
            # a jump slab: the loop *is* the slab, so only the outer name survives
            assert inner == box, (m, box, inner)
