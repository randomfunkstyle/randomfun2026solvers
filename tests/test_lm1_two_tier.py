"""The second store seam: a hot man-memory tier beside the tape.

``little-little-man``'s heat map says 61 % of its CPU time is store round trips,
and a tape read costs ``8.0 * N`` = 3,416 ticks at its N=427 while a man-memory
cell answers in ~200. The fix is not to swap the store — a grid spends 81 cells a
word against a folded tape's 3.7, so holding all 427 costs more area than the
whole machine — but to add a **second tier** holding only the hot scalars.

Three things have to be true and each is checked here on the reference engine:

* the grid packages as a placeable ``STORE`` block, at any shape and at any
  address base (``memory_men_grid_store``);
* the adapter routes an expanded request to the *right pipe* — this is the gate,
  and it is measured at the word level, because a machine whose hot reads all
  went to the tape would still produce correct output;
* a whole machine built with ``build(hot=...)`` round-trips values from both
  sides of the threshold, and is *faster* on the hot side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402
from randomfun2026solvers.memory_men_grid_store import grid_block  # noqa: E402


# ── the block ────────────────────────────────────────────────────────────────
def _wrapped(cols: int, rows: int, base: int) -> str:
    """``grid_block`` with ``I``/``O`` rooms bolted back on, so it can be run."""
    blk = grid_block(cols, rows, base=base)
    ox, oy = 6, 12
    cells = {(x + ox, y + oy): c for (x, y), c in blk.cells.items()}
    ix, iy = blk.in_cell[0] + ox, blk.in_cell[1] + oy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            cells[(ix - 5 + i, iy - 1 + j)] = ch
    for x in range(ix - 2, ix):
        cells[(x, iy)] = ">"
    mx, my = blk.out_cell[0] + ox, blk.out_cell[1] + oy
    for y in range(my - 3, my):
        cells[(mx, y)] = "^"
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            cells[(mx - 1 + i, my - 6 + j)] = ch
    w = max(x for x, _ in cells) + 1
    h = max(y for _, y in cells) + 1
    return "\n".join("".join(cells.get((x, y), " ") for x in range(w)).rstrip() for y in range(h))


def test_a_grid_store_block_needs_two_columns() -> None:
    # One column is `memory_men_addr`, which has no strips at all — and the
    # placement here hangs the request and answer stubs off them.
    with pytest.raises(ValueError, match=">= 2 columns"):
        grid_block(1, 8)


@pytest.mark.parametrize(("cols", "rows"), [(2, 3), (3, 4), (2, 5)])
def test_the_block_draws_exactly_the_pipes_it_counts(cols: int, rows: int) -> None:
    blk = grid_block(cols, rows, base=1)
    # per column: one feed down, one answer out, two per cell (repeater to
    # decoder, decoder to cell); plus the request and answer stubs.
    assert blk.pipes == 2 + cols * (2 + 2 * rows)
    assert len(Littleman().analyze(_wrapped(cols, rows, 1)).pipes) == blk.pipes
    assert blk.slots == cols * rows


@pytest.mark.parametrize(("cols", "rows", "base"), [(2, 3, 0), (2, 3, 5), (3, 4, 171)])
def test_a_based_block_decodes_global_addresses(cols: int, rows: int, base: int) -> None:
    """The whole reason the seam needs no address translation.

    A column's decoders are handed a base as a *literal* and count up from it, so
    a block built at ``base`` answers ``base .. base + n - 1`` — which are the
    CPU's own slot numbers. The adapter then only picks a pipe.
    """
    n = cols * rows
    stream: list[int] = []
    want: list[int] = []
    for i in range(n):
        stream += [1, base + i, 1000 + i]
    for i in reversed(range(n)):
        stream += [0, base + i]
        want.append(1000 + i)
    snap = Littleman().judge(
        _wrapped(cols, rows, base),
        input=" ".join(map(str, stream)),
        expected=want,
        max_ticks=400_000,
    )
    assert snap.output == want


# ── the adapter, at the word level ───────────────────────────────────────────
def _adapter_probe(hot_top: int) -> str:
    """The adapter alone, with a tagging sink on each outgoing pipe.

    Correct *machine* output cannot prove the routing — both tiers hold every
    address physically, so a request sent to the wrong one still round-trips. So
    the two sinks tag what they see: the hot one adds 1,000, the cold one adds
    nothing, and a merger funnels both into the single ``O`` room.
    """
    a = machine.two_tier_adapter(hot_top)
    g = machine._Grid()
    ax, ay = 6, 5
    g.room(ax, ay, ax + a.width + 1, ay + a.height + 1)
    g.blit(ax, ay, a.cells)
    iy = ay + a.in_row
    g.room(0, iy - 1, 2, iy + 1)
    g.put(1, iy, "I")
    g.draw_pipe([(3, iy), (ax - 1, iy)])

    ew = ax + a.width + 1
    hot_y, cold_y = ay + a.hot_row, ay + a.cold_row
    tag = (">.rM`1000`+v", "^@<.......s<")
    pass_through = (">.r........v", "^@<.......s<")
    for rows_, y in ((tag, hot_y), (pass_through, cold_y)):
        g.room(ew + 3, y - 1, ew + 16, y + 2)
        for j, row in enumerate(rows_):
            g.text(ew + 4, y + j, row)
        g.draw_pipe([(ew + 1, y), (ew + 2, y)])

    mx, my = ew + 24, ay + 5
    g.room(mx, my, mx + 5, my + 3)
    g.text(mx + 1, my + 1, "@>Rv")
    g.text(mx + 1, my + 2, " ^s<")
    top, bot = min(hot_y, cold_y), max(hot_y, cold_y)
    g.draw_pipe([(ew + 17, top + 1), (mx - 3, top + 1), (mx - 3, my + 1), (mx - 1, my + 1)])
    g.draw_pipe([(ew + 17, bot + 1), (mx - 2, bot + 1), (mx - 2, my + 2), (mx - 1, my + 2)])
    g.draw_pipe([(mx + 6, my + 2), (mx + 7, my + 2)])
    g.room(mx + 8, my + 1, mx + 10, my + 3)
    g.put(mx + 9, my + 2, "O")
    return "\n".join(g.rows())


def test_the_adapter_sends_every_word_to_the_right_tier() -> None:
    hot_top = 42
    src = _adapter_probe(hot_top)
    # +a reads, -a writes and the value word follows; both sides of the seam,
    # including both boundary addresses.
    reqs = [
        (3, None),
        (-3, 77),
        (42, None),
        (-42, 55),
        (43, None),
        (-43, 66),
        (200, None),
        (1, None),
    ]
    stream: list[int] = []
    want: list[int] = []
    for word, value in reqs:
        stream.append(word)
        addr = abs(word)
        words = [0 if word > 0 else 1, addr] + ([value] if value is not None else [])
        if value is not None:
            stream.append(value)
        tag = 1000 if addr <= hot_top else 0
        want += [x + tag for x in words]
    snap = Littleman().judge(
        src, input=" ".join(map(str, stream)), expected=want, max_ticks=200_000
    )
    assert snap.output == want, snap.output


def test_the_boundary_address_is_hot_and_the_next_one_is_cold() -> None:
    """``X`` is three-way, and this is where the third way goes.

    The test is ``A = a - (hot_top + 1)``, so ``a == hot_top + 1`` is the *zero*
    case and walks straight on. With the hot range low that is the first cold
    address and it merges with the clockwise arm in two cells; a high hot range
    would put the zero on the seam and cost a permanently dead slot.
    """
    src = _adapter_probe(7)
    want = [1000, 1007, 0, 8]
    snap = Littleman().judge(src, input="7 8", expected=want, max_ticks=100_000)
    assert snap.output == want


# ── a whole two-tier machine ─────────────────────────────────────────────────
def _probe_machine(slots: list[int], hot: tuple[int, int] | None):
    src = ["; two-tier probe"]
    for s in slots:
        src += ["        IN", f"        ST {s}"]
    for s in slots:
        src += [f"        LD {s}", "        OUT"]
    src.append("        HALT")
    prog = assemble("\n".join(src), name="two-tier-probe")
    return machine.build(prog, tape_n=60, hot=hot)


@pytest.mark.slow
def test_a_two_tier_machine_round_trips_both_sides_of_the_seam() -> None:
    """The gate. Values must come back from the tier *and* from the tape."""
    hot = (2, 3)  # slots 1..6 are the tier's; 7 and up are the tape's
    m = _probe_machine([1, 2, 6, 7, 12, 23], hot)
    want = [11, 22, 33, 44, 55, 66]
    snap = Littleman().judge(
        "\n".join(m.rows), input=" ".join(map(str, want)), expected=want, max_ticks=900_000
    )
    assert snap.output == want, snap.output


@pytest.mark.slow
def test_the_tier_is_actually_faster_where_it_is_meant_to_be() -> None:
    """Both stores hold every address, so only the *ticks* say which answered.

    Reads inside the tier's range get much cheaper; reads outside it get slightly
    dearer, because the cold request is routed the long way round to leave the
    corridor clear. Both directions are asserted — the second is what proves the
    first is not an artefact.
    """
    lm = Littleman()

    def ticks(slots: list[int], hot: tuple[int, int] | None) -> int:
        m = _probe_machine(slots, hot)
        want = list(range(11, 11 + len(slots)))
        snap = lm.judge(
            "\n".join(m.rows), input=" ".join(map(str, want)), expected=want, max_ticks=900_000
        )
        assert snap.output == want
        return snap.step

    hot_only = [1, 2, 3, 4, 5, 6]
    cold_only = [40, 41, 42, 43, 44, 45]
    assert ticks(hot_only, (2, 3)) < ticks(hot_only, None) / 2
    assert ticks(cold_only, (2, 3)) > ticks(cold_only, None)
