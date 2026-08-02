"""The taped STORE tier: banked pipe tapes behind a chain of range gates.

The tier exists for the little-man census (a bank is two men, a gate one,
against the man-memory's ~two per slot), so the census is pinned here along
with the semantics: every address lands in the right bank, rebased right, and
comes back with the right value through the collector.

The probes stream requests, and the tier's ordering contract is the machine's
(the CPU blocks on every read; only one answer is ever in flight), so reads
are grouped per bank per run — two banks' rings answer at different speeds, so
*streamed* cross-bank reads can legally come home out of order. The machine
gate in ``test_deadman3d.py`` covers the serial cross-bank mix for real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.memory_taped import (  # noqa: E402
    COMPACT_GATE_H,
    COMPACT_GATE_IN_ROW,
    V4_GATE_DOWN_ROW,
    bank_gate,
    gate_chain,
    gate_rows,
    taped_plan,
    taped_store_block,
)

#: deadman-3d's shipped plan: hot high addresses in small rings.
PLAN = (128, 128, 40, 33)


def _standalone(block) -> FastLittleman:
    """The block as a complete program: an I room on the request stub, an O
    room on the answer stub — the same wrapper the men-v3 grid block test uses."""
    sx, sy = 6, 4
    grid = {(x + sx, y + sy): ch for (x, y), ch in block.cells.items()}
    ix, iy = block.in_cell[0] + sx, block.in_cell[1] + sy
    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ix - 4 + i, iy - 1 + j)] = ch
    grid[(ix - 1, iy)] = ">"
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ox - 1 + i, j)] = ch
    for y in range(3, oy):
        grid[(ox, y)] = "^"
    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return FastLittleman("\n".join("".join(grid.get((x, y), " ") for x in range(w)) for y in range(h)))


def test_the_plan_covers_the_tape_and_rejects_gaps() -> None:
    assert sum(taped_plan(330, PLAN)) >= 329
    assert taped_plan(330, 4) == [83, 83, 83, 80]
    with pytest.raises(ValueError):
        taped_plan(330, (100, 100))  # 129 addresses uncovered
    with pytest.raises(ValueError):
        taped_plan(330, 1)


def test_the_census_is_the_point() -> None:
    """Two men per bank plus one per gate — that is the tier's whole reason."""
    b = taped_store_block(330, PLAN, skip_batch=2)
    men = sum(1 for ch in b.cells.values() if ch == "@")
    assert men == 2 * len(PLAN) + (len(PLAN) - 1) + 1  # workers+relays, gates, collector
    assert b.pipes == 4 * len(PLAN) + (len(PLAN) - 2)


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_every_address_reads_back_what_was_written(skip_batch: int) -> None:
    """All 329 slots, written through the whole chain then read one bank per
    run (see the module docstring for why reads are grouped per bank)."""
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=skip_batch))
    writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(330, PLAN):
        bounds.append(bounds[-1] + m)
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, 330)
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
        assert res.fatal is None and res.output == want, (
            lo,
            hi,
            res.fatal or res.reason,
            res.output[:5],
        )


# ── the compact gate: the same four arms with the nop spacers deleted ────────
def test_the_compact_gate_is_opt_in_and_five_rows_shorter() -> None:
    """Off by default, so every existing caller's grid is byte-identical."""
    for kwargs in ({}, {"skip_batch": 2}):
        shipped = taped_store_block(330, PLAN, **kwargs)
        default_off = taped_store_block(330, PLAN, compact_gate=False, **kwargs)
        assert default_off.cells == shipped.cells
        compact = taped_store_block(330, PLAN, compact_gate=True, **kwargs)
        # the gate strip is the block's floor, so the block loses the rows too
        assert compact.height == shipped.height - 5
        assert compact.width == shipped.width
        # ... and nothing else: same census, same pipe inventory
        assert compact.pipes == shipped.pipes
        assert sum(1 for c in compact.cells.values() if c == "@") == sum(
            1 for c in shipped.cells.values() if c == "@"
        )
    assert gate_rows(True)[0] == gate_rows(False)[0] - 5


def test_every_gate_send_still_binds_to_the_pipe_it_means() -> None:
    """The gate's whole binding argument is that no ``s`` needs an argument:
    two outgoing pipes on one wall, and the *row* decides which is nearest
    (SPEC.md, "Which pipe do I talk to?"). Compacting moves every arm row, so
    the margins shrink — this is the check that they never cross.

    The pipes' source segments are the cells just east of the east wall, at the
    local / downstream rows; ties break by reading order. The rule needs no arm
    arithmetic: an ``s`` **above** the spine is a local arm and must reach the
    local pipe, one **below** it is a downstream arm and must reach the other.
    """
    for compact in (False, True):
        _h, in_row, local_row, down_row = gate_rows(compact)
        # every bank size the literal's width can produce, and then some —
        # in both gate forms, because `high` changes every arm's text and so
        # every send's column
        for m in (1, 5, 64, 85, 195, 256, 999, 12345):
            for high in (None, m + 1, m + 7, 4 * m, 99999):
                g, w = bank_gate(m, compact=compact, high=high)
                src = {local_row: (w, local_row), down_row: (w, down_row)}
                sends = [(x, y) for (x, y), ch in g.items() if ch == "s"]
                assert len(sends) == 10, (m, high, compact, len(sends))
                assert all(y != in_row for _x, y in sends)
                for x, y in sends:
                    want = local_row if y < in_row else down_row
                    dist = {r: abs(px - x) + abs(py - y) for r, (px, py) in src.items()}
                    nearest = min(src, key=lambda r: (dist[r], r))
                    assert nearest == want, (
                        f"m={m} high={high} compact={compact}: the `s` at {(x, y)} "
                        f"binds to the row-{nearest} pipe, not row {want} ({dist})"
                    )


def test_the_low_gates_send_bindings_are_unchanged() -> None:
    """The original, narrower assertion, kept as its own case."""
    for compact in (False, True):
        _h, in_row, local_row, down_row = gate_rows(compact)
        for m in (1, 5, 64, 85, 195, 256, 999, 12345):
            g, w = bank_gate(m, compact=compact)
            src = {local_row: (w, local_row), down_row: (w, down_row)}
            sends = [(x, y) for (x, y), ch in g.items() if ch == "s"]
            assert len(sends) == 10, (m, compact, len(sends))
            assert all(y != in_row for _x, y in sends)  # the spine sends nothing
            for x, y in sends:
                want = local_row if y < in_row else down_row
                dist = {row: abs(px - x) + abs(py - y) for row, (px, py) in src.items()}
                nearest = min(src, key=lambda row: (dist[row], row))
                assert nearest == want, (
                    f"m={m} compact={compact}: the `s` at {(x, y)} binds to the "
                    f"row-{nearest} pipe, not row {want} ({dist})"
                )


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_the_compact_gate_routes_every_address_the_same(skip_batch: int) -> None:
    """Correctness first: deleting a corridor cell must not lose a request.
    Same probe as the shipped body's, one bank per run."""
    engine = _standalone(
        taped_store_block(330, PLAN, skip_batch=skip_batch, compact_gate=True)
    )
    writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(330, PLAN):
        bounds.append(bounds[-1] + m)
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, 330)
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
        assert res.fatal is None and res.output == want, (
            lo,
            hi,
            res.fatal or res.reason,
            res.output[:5],
        )


# ── the chain order: the hot bank first, which is a different gate form ──────
def test_the_chain_peels_banks_off_an_end_and_says_so_when_it_cannot() -> None:
    """``gate_chain`` is where a reordering bug would hide, so the literals it
    implies are derived here from the sizes alone and compared against it.

    A low gate at chain position ``j`` owns ``1..m`` of the space it was handed
    and forwards ``addr - m``; a high gate owns the top ``m`` of ``1..top`` and
    forwards ``addr`` untouched. Either way the space shrinks by ``m``, so the
    invariant that actually matters is that the ranges **tile** the address
    space exactly once — which is what the second loop checks.
    """
    sizes = [256, 195, 64, 85]
    assert gate_chain(sizes) == [(0, None), (1, None), (2, None), (3, None)]
    assert gate_chain(sizes, (3, 0, 1, 2)) == [(3, 600), (0, None), (1, None), (2, None)]
    assert gate_chain(sizes, (3, 2, 1, 0)) == [(3, 600), (2, 515), (1, 451), (0, None)]
    # a bank in the MIDDLE of what is left cannot be peeled: the gate hands on
    # one contiguous rebased space and its test is one-sided
    with pytest.raises(ValueError, match="claim an END"):
        gate_chain(sizes, (1, 0, 2, 3))
    with pytest.raises(ValueError, match="permutation"):
        gate_chain(sizes, (0, 1, 2, 2))

    # ... and every reachable order lands every address in the SAME bank at the
    # SAME local slot, which is the whole safety property. Walked here in
    # arithmetic (the engine walks it for real two tests down).
    def resolve(order: tuple[int, ...] | None, addr: int) -> tuple[int, int]:
        chain = gate_chain(sizes, order)
        for k, high in chain[:-1]:
            m = sizes[k]
            if high is None:  # low gate: mine is 1..m, forward addr - m
                if addr <= m:
                    return k, addr
                addr -= m
            elif addr > high - m:  # high gate: mine is high-m+1..high
                return k, addr - (high - m)
        return chain[-1][0], addr

    want = {a: resolve(None, a) for a in range(1, sum(sizes) + 1)}
    assert want[1] == (0, 1) and want[256] == (0, 256)
    assert want[257] == (1, 1) and want[516] == (3, 1) and want[600] == (3, 85)
    for order in ((3, 0, 1, 2), (3, 2, 1, 0), (0, 3, 1, 2), (3, 0, 2, 1), (0, 1, 3, 2)):
        assert {a: resolve(order, a) for a in want} == want, order


def test_the_hot_first_chain_is_opt_in_and_costs_no_room() -> None:
    """The reorder is free in every dimension that scores: the high gate form is
    the same shape as the low one, so the block does not move a column or a row,
    and the census and pipe inventory are the plan's, not the order's."""
    shipped = taped_store_block(330, PLAN, skip_batch=2, compact_gate=True)
    assert taped_store_block(
        330, PLAN, skip_batch=2, compact_gate=True, order=(0, 1, 2, 3)
    ).cells == shipped.cells
    hot = taped_store_block(330, PLAN, skip_batch=2, compact_gate=True, order=(3, 0, 1, 2))
    assert (hot.width, hot.height) == (shipped.width, shipped.height)
    assert hot.pipes == shipped.pipes
    assert sum(1 for c in hot.cells.values() if c == "@") == sum(
        1 for c in shipped.cells.values() if c == "@"
    )
    assert hot.cells != shipped.cells  # ... but it IS a different chain


@pytest.mark.parametrize("compact", [False, True])
def test_the_hot_first_chain_resolves_every_address_to_the_same_data(compact: bool) -> None:
    """The load-bearing test. Reordering rewrites every gate's literal, and a
    wrong literal routes a read to the **wrong bank** rather than failing — so
    this writes a distinct value into all 329 addresses through both chains and
    compares them address by address, not bank by bank."""

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(330, PLAN, skip_batch=2, compact_gate=compact, order=order)
        )
        writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
        bounds = [1]
        for m in taped_plan(330, PLAN):
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, 330)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [a * 13 + 7 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(None)
    assert len(shipped) == 329
    assert shipped == {a: a * 13 + 7 for a in range(1, 330)}
    assert readback((3, 0, 1, 2)) == shipped


def test_the_shipped_deadman3d_plan_routes_every_one_of_its_600_addresses() -> None:
    """The same load-bearing check, but against the **registry's own** numbers.

    ``PLAN`` above is a scaled-down stand-in; the machine ships a different
    split and a different chain order, and a size or an order that routes at 330
    slots says nothing about one that routes at 601. A wrong gate literal is
    silent — it hands a read to the wrong bank and the bank answers — so this
    reads back every address of the real plan and compares address by address,
    never bank by bank. It takes the numbers from :mod:`lm1.machine` so it
    cannot drift from what ``build_for`` actually places.
    """
    from randomfun2026solvers.lm1 import machine

    n = machine.TAPE_SIZE["deadman-3d"]
    plan = machine.TAPED_BANKS["deadman-3d"]
    order = machine.TAPED_BANK_ORDER[("deadman-3d", "taped")]
    compact = ("deadman-3d", "taped") in machine.TAPED_COMPACT_GATE
    skip = machine.TAPED_SKIP_BATCH["deadman-3d"]

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(n, plan, skip_batch=skip, compact_gate=compact, order=order)
        )
        writes = [x for a in range(1, n) for x in (1, a, a * 13 + 7)]
        bounds = [1]
        for m in taped_plan(n, plan):
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, n)
            if lo >= hi:
                continue
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [a * 13 + 7 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    want = {a: a * 13 + 7 for a in range(1, n)}
    address_order = readback(None)
    assert address_order == want, "the plan itself mis-routes before any reorder"
    hot_first = readback(order)
    assert hot_first == want
    # ... and the two chains agree address for address, which is the property
    # the reorder has to preserve and the one a wrong literal breaks quietly.
    assert hot_first == address_order


def test_grown_gate_rooms_are_opt_in_and_still_route_every_real_address() -> None:
    """Gate rooms grown to their **callers** — off by default, and still exact.

    ``chain_reach`` pulls every gate but the first west until its wall stands
    beside the previous gate's; ``request_roof`` pulls the first gate's roof up
    to whatever hands it the request. Both work for one reason and it is not
    obvious: ``U`` turns away from the **wall** the pipe attaches to, not from
    the direction the pipe comes from, so the entry may sit thirty rows above
    the man who reads it. If that were the other way round the man would turn
    south and the gate would route reads to the wrong bank *without erroring* —
    which is why this reads every address of the live plan back individually
    through all four builds rather than checking a shape.

    What it deliberately cannot do is reach the gate's **callee**. The two
    outgoing pipes share the east wall and ``s`` takes the nearest; the tightest
    of the eight ``s`` glyphs has three cells of margin, so a local attachment
    more than four rows off the body binds to the downstream pipe instead.
    """
    from randomfun2026solvers.lm1 import machine

    n = machine.TAPE_SIZE["deadman-3d"]
    plan = machine.TAPED_BANKS["deadman-3d"]
    order = machine.TAPED_BANK_ORDER[("deadman-3d", "taped")]
    skip = machine.TAPED_SKIP_BATCH["deadman-3d"]
    common = dict(skip_batch=skip, compact_gate=True, order=order)

    shipped = taped_store_block(n, plan, **common)
    men = sum(1 for c in shipped.cells.values() if c == "@")
    # Off by default, to the cell — the knobs are additive for every other caller.
    assert taped_store_block(n, plan, **common, chain_reach=False).cells == shipped.cells
    assert taped_store_block(n, plan, **common, request_roof=None).cells == shipped.cells
    assert taped_store_block(n, plan, **common, feed_teleport=False).cells == shipped.cells

    builds = {
        "chain": dict(chain_reach=True),
        "roof": dict(request_roof=20),
        "both": dict(chain_reach=True, request_roof=20),
        "feed": dict(feed_teleport=True),
        "all": dict(chain_reach=True, request_roof=20, feed_teleport=True),
    }
    want = {a: a * 13 + 7 for a in range(1, n)}
    for name, kw in builds.items():
        block = taped_store_block(n, plan, **common, **kw)
        if kw.get("feed_teleport"):
            # A forwarder a bank: one man and one extra pipe each, and two
            # columns of pitch, because the corridor it hangs in was four
            # columns wide and `teleport_v` is six. The height does not move.
            assert block.height == shipped.height, name
            assert block.width == shipped.width + 2 * (len(plan) - 1), name
            assert block.pipes == shipped.pipes + len(plan), name
            assert sum(1 for c in block.cells.values() if c == "@") == men + len(plan), name
        else:
            # Growing a room west/north costs nothing that scores: the banks, the
            # pitch and therefore the block's own box are computed from the
            # ungrown gates, and no room is added.
            assert (block.width, block.height) == (shipped.width, shipped.height), name
            assert block.pipes == shipped.pipes, name
            assert sum(1 for c in block.cells.values() if c == "@") == men, name
        assert block.cells != shipped.cells, name

        engine = _standalone(block)
        writes = [x for a in range(1, n) for x in (1, a, a * 13 + 7)]
        bounds = [1]
        for m in taped_plan(n, plan):
            bounds.append(bounds[-1] + m)
        got: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, n)
            if lo >= hi:
                continue
            reads = [x for a in range(lo, hi) for x in (0, a)]
            res = engine.run(
                writes + reads,
                expected=[a * 13 + 7 for a in range(lo, hi)],
                max_ticks=400_000_000,
            )
            assert res.fatal is None, (name, lo, res.fatal)
            got.update(zip(range(lo, hi), res.output, strict=False))
        assert got == want, name

    # ``chain_pad`` only ever lengthens: it is the measuring instrument, so it
    # must move the links and nothing else, and it must refuse to run backwards.
    padded = taped_store_block(n, plan, **common, chain_reach=True, chain_pad=10)
    assert (padded.width, padded.height) == (shipped.width, shipped.height)
    assert padded.cells != taped_store_block(n, plan, **common, chain_reach=True).cells
    with pytest.raises(ValueError):
        taped_store_block(n, plan, **common, chain_reach=True, chain_pad=-1)
    with pytest.raises(ValueError):
        bank_gate(8, compact=True, west_grow=-1)


def test_fresh_slots_read_zero_and_extremes_survive() -> None:
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=2))
    addrs = [1, 128, 129, 256, 257, 296, 297, 329]  # both sides of every seam
    fresh = engine.run(
        [x for a in addrs for x in (0, a)], expected=[0] * len(addrs), max_ticks=10_000_000
    )
    assert fresh.fatal is None and fresh.output == [0] * len(addrs)
    # extremes stay within one bank per pair — cross-bank reads race when
    # streamed (the short last ring answers first); the machine serializes
    edges = engine.run(
        [1, 1, -1000000, 0, 1, 1, 128, 1000000, 0, 128],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert edges.fatal is None and edges.output == [-1000000, 1000000]
    top = engine.run(
        [1, 297, -1000000, 0, 297, 1, 329, 1000000, 0, 329],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert top.fatal is None and top.output == [-1000000, 1000000]


#: ``deadman-3d_hires``'s configuration, and it is nothing like ``PLAN``: its
#: tape is 902 slots, its :data:`machine.TAPED_BANKS` cut is **eleven** banks
#: against ``deadman-3d``'s four (four is the most that family's 300-column
#: ceiling allows; hires has no ceiling, so its count sits at the measured tick
#: optimum), and it has no :data:`machine.TAPED_SKIP_BATCH` entry, so its banks
#: are batch-1 rings.  Read from the registry rather than restated, so that a
#: re-cut cannot leave this file testing a plan the machine no longer builds.
HIRES_N = 902
HIRES_PLAN = tuple(machine.TAPED_BANKS["deadman-3d_hires"])
HIRES_ORDER = machine.TAPED_BANK_ORDER[("deadman-3d_hires", "taped")]


def test_the_hires_bank_plan_covers_its_tape_and_its_order_is_expressible() -> None:
    """The cut and the order are one decision; pin that they agree.

    Eleven banks is not a free parameter that happened to build — it is where
    the tour ticks bottom out (see :data:`machine.TAPED_BANKS`), and the count
    is what makes the order non-obvious: a gate peels a bank off an END of what
    it is handed, so of eleven banks' 39,916,800 permutations only ``2**10``
    exist at all.
    """
    assert len(HIRES_PLAN) == 11
    assert sum(HIRES_PLAN) >= HIRES_N - 1
    assert all(m >= 1 for m in HIRES_PLAN)
    assert sorted(HIRES_ORDER) == list(range(len(HIRES_PLAN)))
    # the reachability check itself — the same one ``build`` makes
    chain = gate_chain(list(HIRES_PLAN), order=list(HIRES_ORDER))
    assert [k for k, _top in chain] == list(HIRES_ORDER)


@pytest.mark.slow
def test_the_hires_hot_first_chain_resolves_every_address_to_the_same_data() -> None:
    """The load-bearing test for ``TAPED_BANK_ORDER["deadman-3d_hires"]``.

    A wrong gate literal does not fail, it routes to the **wrong bank** — so
    this compares address by address rather than bank by bank, over hires' own
    plan and its own batch-1 rings, which no other test in this file exercises.
    """

    def readback(order: tuple[int, ...] | None) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(HIRES_N, HIRES_PLAN, skip_batch=1, compact_gate=True,
                              order=list(order) if order else None)
        )
        writes = [x for a in range(1, HIRES_N) for x in (1, a, (a * 37 + 11) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (order, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    address_order = readback(None)
    assert address_order == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    assert readback(HIRES_ORDER) == address_order


def test_the_parked_constant_gate_routes_every_hires_address_the_same() -> None:
    """The load-bearing test for ``park_const``, and it has to be a readback.

    Parking the range constant in B is a **register-liveness** change: the spine
    stops writing B, so the high form's south arms — which used to find ``addr``
    there, put by the spine's ``M`` — rebuild it with ``N+``. Get any of that
    wrong and no build fails and no binding moves; the gate simply hands a read
    to the wrong bank, and the bank answers. So this writes a distinct value into
    every one of hires' 900 addresses and compares them **address by address**
    against the shipped spine, over the real plan, the real hot-first chain (both
    gate forms appear in it) and the real compact body.

    It also pins the reversed floor literal, which is the other thing that cannot
    be argued from the source: the man walks the return floor **west**, so the
    digits are stamped in reverse and it is the *west* backtick that fires.
    """

    def readback(park: bool) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N,
                HIRES_PLAN,
                skip_batch=1,
                compact_gate=True,
                order=list(HIRES_ORDER),
                gate_park_const=park,
            )
        )
        writes = [x for a in range(1, HIRES_N) for x in (1, a, (a * 37 + 11) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (park, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(False)
    assert shipped == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    assert readback(True) == shipped


def test_the_reused_b_south_arms_route_every_hires_address_the_same() -> None:
    """``south_reuse_b`` is register liveness, so it has to be a readback too.

    The high gate's south arms drop their leading ``W`` and the ``M`` behind it
    on the argument that B already holds the address on arrival. If that is ever
    untrue the arm sends whatever B happens to hold as the address, no build
    fails, no send rebinds, and the bank answers a question nobody asked. So this
    writes a distinct value into every one of hires' 900 addresses and compares
    them address by address against the shipped arms, over the real plan, the
    real hot-first chain and the real compact body — the same shape of proof
    :func:`test_the_parked_constant_gate_routes_every_hires_address_the_same`
    uses, and for the same reason.
    """

    def readback(reuse: bool) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N,
                HIRES_PLAN,
                skip_batch=1,
                compact_gate=True,
                order=list(HIRES_ORDER),
                gate_park_const=True,
                gate_south_reuse_b=reuse,
            )
        )
        writes = [x for a in range(1, HIRES_N) for x in (1, a, (a * 37 + 11) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=400_000_000)
            assert res.fatal is None, (reuse, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(False)
    assert shipped == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    assert readback(True) == shipped


def test_the_reused_b_south_arms_are_opt_in_and_two_cells_shorter() -> None:
    """Off is byte-identical; on, only the two south arms move, and only west."""
    base, w0 = bank_gate(5, compact=True, high=12, park_const=True)
    same, _ = bank_gate(5, compact=True, high=12, park_const=True, south_reuse_b=False)
    assert same == base
    tight, w1 = bank_gate(5, compact=True, high=12, park_const=True, south_reuse_b=True)
    assert w1 == w0  # the room does not move; the return column has slack to spare
    _h, in_row, _local, _down = gate_rows(True)
    # every changed cell is strictly south of the spine
    assert all(y > in_row for (_x, y) in set(base) ^ set(tight)
               | {c for c in set(base) & set(tight) if base[c] != tight[c]})
    # the low gate has no south-arm B to reuse and is untouched
    assert bank_gate(5, compact=True, park_const=True, south_reuse_b=True) == bank_gate(
        5, compact=True, park_const=True
    )


def test_the_parked_constant_gate_is_opt_in_and_shorter_on_both_forms() -> None:
    """Off is byte-identical; on, the spine loses the literal and the room the
    columns that literal was holding — which is the whole point, because the
    return floor is measured from the spine's ``X`` and so shrinks with it."""
    for high in (None, 901):
        shipped, w0 = bank_gate(102, compact=True, high=high)
        parked, w1 = bank_gate(102, compact=True, high=high, park_const=True)
        assert w1 < w0, (high, w0, w1)
        # the spine keeps its `U`, and the constant is gone from it
        row = COMPACT_GATE_IN_ROW
        spine = "".join(
            parked[(x, row)] for x in range(1, w1) if (x, row) in parked
        )
        assert spine.startswith("Ubr") and "`" not in spine, spine
        # ... and turns up on the floor instead, digits reversed
        floor = "".join(
            parked[(x, COMPACT_GATE_H)]
            for x in range(1, w1 - 1)
            if (x, COMPACT_GATE_H) in parked
        )
        const = 103 if high is None else 901 - 102
        assert f"M`{str(const)[::-1]}`" in floor, (floor, const)
        assert "`" not in "".join(
            shipped[(x, COMPACT_GATE_H)]
            for x in range(1, w0 - 1)
            if (x, COMPACT_GATE_H) in shipped
        )


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_the_parked_size_ring_worker_reads_back_what_was_written(skip_batch: int) -> None:
    """``tape_park_const`` is the same liveness bet inside the ring worker, and
    it moves the **descent to P1** as well as MAIN's two arms — so a wrong column
    would route a request into P1 with the wrong count and quietly answer the
    wrong slot. Both workers carry the change, so both are exercised, and the
    comparison is against the shipped worker address by address."""

    def readback(park: bool) -> list[int]:
        engine = _standalone(
            taped_store_block(
                330, PLAN, skip_batch=skip_batch, compact_gate=True, tape_park_const=park
            )
        )
        writes = [x for a in range(1, 330) for x in (1, a, a * 29 + 5)]
        out: list[int] = []
        bounds = [1]
        for m in taped_plan(330, PLAN):
            bounds.append(bounds[-1] + m)
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, 330)
            reads = [x for a in range(lo, hi) for x in (0, a)]
            want = [a * 29 + 5 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=200_000_000)
            assert res.fatal is None, (park, lo, res.fatal)
            out.extend(res.output)
        return out

    shipped = readback(False)
    assert shipped == [a * 29 + 5 for a in range(1, 330)]
    assert readback(True) == shipped


# ── v4: the op in the address's low bit, and one word on the wire ────────────
#: The shipped hi-res block, as ``lm1.machine`` builds it, minus the wire.
_HIRES_KW = dict(
    skip_batch=None,
    jump_threshold=machine.TAPED_JUMP_THRESHOLD["deadman-3d_hires"],
    compact_gate=True,
    gate_park_const=True,
    gate_south_reuse_b=True,
    tape_park_const=True,
    order=list(HIRES_ORDER),
    chain_reach=True,
    feed_teleport=True,
    bank_lift=5,
)


def _wire(protocol: str, op: int, addr: int) -> list[int]:
    """One access's request words. ``op`` is 0 for a read, 1 for a write."""
    return [op, addr] if protocol == "v3" else [2 * addr - op]


def test_the_v4_wire_is_opt_in_and_the_default_block_is_byte_identical() -> None:
    """A wire format is not a tuning value, so it gets a version and a default."""
    shipped = taped_store_block(330, PLAN, compact_gate=True, feed_teleport=True)
    assert (
        taped_store_block(
            330, PLAN, compact_gate=True, feed_teleport=True, protocol="v3"
        ).cells
        == shipped.cells
    )
    packed = taped_store_block(
        330, PLAN, compact_gate=True, feed_teleport=True, protocol="v4"
    )
    assert packed.cells != shipped.cells
    # ``v5`` is the same wire with the unpack moved into the bank, so it shares
    # v4's gate rows and gate bodies and differs only in the forwarder and the
    # ring worker -- a different block again, and never the shipped one.
    relayed = taped_store_block(
        330, PLAN, compact_gate=True, feed_teleport=True, protocol="v5"
    )
    assert relayed.cells not in (shipped.cells, packed.cells)
    assert gate_rows(True, "v5") == gate_rows(True, "v4")
    assert bank_gate(8, protocol="v5") == bank_gate(8, protocol="v4")
    # ... and the two ends of the wire are one decision, checked rather than hoped
    for protocol in ("v4", "v5"):
        with pytest.raises(ValueError):
            taped_store_block(330, PLAN, compact_gate=True, protocol=protocol)  # no feed room
    with pytest.raises(ValueError):
        gate_rows(True, "nonesuch")
    with pytest.raises(ValueError):
        bank_gate(8, protocol="nonesuch")
    with pytest.raises(ValueError):
        taped_store_block(330, PLAN, protocol="nonesuch")


def test_the_v4_gate_is_a_seven_row_body_with_two_arms_and_two_tails() -> None:
    """Four arms become two arms plus two ``x`` tails, and the body does not
    grow a row doing it: the elbow's merge cell points straight into the
    downstream arm, which the two-word gate could not do because it still had a
    word to receive after the branch."""
    h, in_row, local_row, down_row = gate_rows(True, "v4")
    # ... and the downstream row a caller draws pipes with is the **spine's own**,
    # not the write tail's, so the chain link is a straight horizontal run
    # (``memory_taped.V4_GATE_DOWN_PIPE_ROW``).
    assert (h, in_row, local_row, down_row) == (7, 4, 2, 4)
    assert down_row == in_row
    assert V4_GATE_DOWN_ROW == 6  # where the tail still stands
    assert h == gate_rows(True)[0]  # ... no taller than the compact v3 body
    for high in (None, 901):
        g, w = bank_gate(102, high=high, park_const=True, protocol="v4")
        assert w == 15, (high, w)  # both forms padded to one width
        spine = "".join(g[(x, in_row)] for x in range(1, w) if (x, in_row) in g)
        assert spine.startswith("UbW-X" if high else "Ub-X"), spine
        assert "r" not in spine and "`" not in spine, spine  # one word, no literal
        # one `x` per side, and exactly one `r` behind each of them (the value)
        assert sum(1 for ch in g.values() if ch == "x") == 2
        assert sum(1 for ch in g.values() if ch == "r") == 2
        assert sum(1 for ch in g.values() if ch == "s") == 4  # was ten
        # the parked constant is twice the v3 one (plus one on the low form)
        floor = "".join(g[(x, h)] for x in range(1, w - 1) if (x, h) in g)
        const = 2 * 102 + 1 if high is None else 2 * (901 - 102)
        assert f"M`{str(const)[::-1]}`" in floor, (floor, const)


def test_every_v4_gate_send_still_binds_to_the_pipe_it_means() -> None:
    """Same argument as the v3 body's, on a different floor plan: two outgoing
    pipes on one wall, the row decides, and an ``s`` above the spine is a local
    arm. The v4 rows are tighter — the downstream arm sits *on* the elbow row —
    so this is the check that the margins never cross.

    **And with the downstream pipe moved onto the spine's own row
    (``V4_GATE_DOWN_PIPE_ROW``) one of those margins is zero.** The mine write
    tail's ``s`` stands on row 3, one row from each attachment, and what decides
    it is SPEC's tie-break — *"ties break by reading order (top to bottom, left
    to right)"* — over two segments in the **same column**, so the northern one
    wins. That is the local pipe, which is the one a write must take. This test
    therefore models the tie explicitly rather than letting a ``min`` happen to
    land the right way, and it asserts that the tie is really there: a build in
    which row 3 stopped being a tie would be a build in which the margin had
    moved, and the readbacks are two rooms away from noticing.
    """
    _h, in_row, local_row, down_row = gate_rows(True, "v4")
    ties = 0
    for m in (1, 5, 64, 85, 195, 256, 999, 12345):
        for high in (None, m + 1, m + 7, 4 * m, 99999):
            for park in (False, True):
                g, w = bank_gate(m, high=high, park_const=park, protocol="v4")
                # Both pipes leave the same column, one cell east of the east
                # wall, so reading order over them is simply the smaller row.
                src = {local_row: (w, local_row), down_row: (w, down_row)}
                sends = [(x, y) for (x, y), ch in g.items() if ch == "s"]
                assert len(sends) == 4, (m, high, park, len(sends))
                assert all(y != in_row for _x, y in sends)
                for x, y in sends:
                    want = local_row if y < in_row else down_row
                    dist = {r: abs(px - x) + abs(py - y) for r, (px, py) in src.items()}
                    best = min(dist.values())
                    nearest = min(r for r in src if dist[r] == best)  # reading order
                    assert nearest == want, (
                        f"m={m} high={high} park={park}: the `s` at {(x, y)} binds "
                        f"to the row-{nearest} pipe, not row {want} ({dist})"
                    )
                    if len({r for r in src if dist[r] == best}) == 2:
                        # ... and the one tie in the gate is the mine write tail,
                        # on the row between the two attachments.
                        assert (y, want) == (local_row + 1, local_row), (x, y)
                        ties += 1
    assert ties == 8 * 5 * 2, ties  # exactly one per gate built


@pytest.mark.parametrize("skip_batch", [1, 2, None])
def test_the_v4_wire_routes_every_hires_address_the_same(skip_batch) -> None:
    """The load-bearing test for the whole protocol, and it has to be a readback
    over every address.

    A wire format is exactly the kind of change that does not fail a build: the
    constants in every gate spine double, the rebasing glyphs stay the same
    glyphs, and the feed forwarder starts dividing. Get any of it wrong — an
    off-by-one in a doubled constant, a floored division that rounds the wrong
    way on the odd (write) word, an ``x`` that turns the wrong way — and no
    build fails and no send rebinds; the store simply answers from the wrong
    bank, or writes to one address and reads another.

    So this writes a distinct value into every one of hi-res' 901 addresses and
    compares them **address by address** against the v3 wire, over the real
    plan, the real hot-first chain (both gate forms appear in it), the real
    compact bodies and each ring worker in turn — including ``None``, which is
    the shipped per-bank pick and therefore the only run in which the two
    workers appear in the same block.
    """

    def readback(protocol: str) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N, HIRES_PLAN, protocol=protocol, **{**_HIRES_KW, "skip_batch": skip_batch}
            )
        )
        writes = [
            x
            for a in range(1, HIRES_N)
            for x in (*_wire(protocol, 1, a), (a * 37 + 11) % 9973)
        ]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in _wire(protocol, 0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=800_000_000)
            assert res.fatal is None, (protocol, skip_batch, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback("v3")
    assert shipped == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    assert readback("v4") == shipped
    # ... and ``v5``, which is the same wire taken apart in the *bank* instead of
    # the forwarder, so the same failure modes are reachable one room further on:
    # ``]`` floors, and a write's word is the odd one, so a wrong arm's ring pass
    # writes the address next door and only a readback of all 901 sees it.
    assert readback("v5") == shipped


@pytest.mark.parametrize("grow", [1, 4])
def test_growing_the_banks_west_answers_every_address_from_the_same_bank(grow) -> None:
    """The load-bearing test for ``machine.TAPED_BANK_WEST_GROW``.

    Moving a room's wall is exactly the kind of change that does not fail a
    build. Every ``r`` in the ring worker takes from the **nearest** incoming
    pipe, and there are two of them — the request on the west wall and the ring's
    own return on the south — so carrying the west wall four columns further out
    moves eight receives' distances at once. If one of them flipped, the worker
    would take a request word off its own tape (or a tape word off the request
    pipe) and the block would answer, in silence, with the wrong thing.

    The argument that none of them can is one-directional and therefore easy to
    get wrong in the other direction: growing west only makes the request pipe
    *further*, so the four receives that must take the request are the ones at
    risk, and the tightest of them is the WRITE value's at 10 -> 14 against 19.
    This checks it rather than repeating it, address by address over all 901,
    against the ungrown block's own answers, on the shipped hires plan, chain,
    protocol and per-bank worker pick.
    """

    def readback(west: int) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N, HIRES_PLAN, protocol="v4",
                **{**_HIRES_KW, "bank_west_grow": west},
            )
        )
        writes = [x for a in range(1, HIRES_N) for x in (*_wire("v4", 1, a),
                                                         (a * 37 + 11) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in _wire("v4", 0, a)]
            want = [(a * 37 + 11) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=800_000_000)
            assert res.fatal is None, (west, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    assert readback(grow) == {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}


def test_the_bank_west_grow_is_opt_in_and_stops_at_the_feed_rooms_wall() -> None:
    """Four is the ceiling because the stub owns the two columns west of the
    wall and column 0 is the feed room's own east wall — and the two knobs that
    shorten this stub from opposite ends cannot both spend it."""
    assert machine.tape_block(8).cells == machine.tape_block(8, west_grow=0).cells
    grown = machine.tape_block(8, west_grow=4)
    assert grown.in_cell == (1, 10) and machine.tape_block(8).in_cell == (5, 10)
    # the block's own box, its ring and its answer stub do not move at all
    plain = machine.tape_block(8)
    assert (grown.width, grown.height, grown.out_cell, grown.slots) == (
        plain.width, plain.height, plain.out_cell, plain.slots)
    with pytest.raises(ValueError, match="west_grow"):
        machine.tape_block(8, west_grow=5)
    with pytest.raises(ValueError, match="opposite ends"):
        taped_store_block(HIRES_N, HIRES_PLAN,
                          **{**_HIRES_KW, "bank_west_grow": 3, "feed_tuck": 2})


def test_the_v4_feed_forwarder_unpacks_with_one_divide() -> None:
    """``2*addr - op`` and floored division are chosen for each other: the
    quotient is short by exactly the remainder, so one ``+`` restores the
    address on **both** arms with no branch at all."""
    from randomfun2026solvers.memory_taped import V4_FEED_H, feed_unpack

    for a in range(1, 400):
        for op in (0, 1):
            w = 2 * a - op
            assert w // 2 + w % 2 == a, (a, op, w)
            assert w % 2 == op, (a, op, w)  # ... and the remainder IS the op
    rows, ports = feed_unpack(V4_FEED_H)
    assert len(rows) == V4_FEED_H and len(ports) == V4_FEED_H
    # The turn into the descent stands BEFORE the `R`, not after it: only the
    # cells between the `R` and the last `s` are on the wire, and the forwarder is
    # already standing on the `R` when the word arrives. Pinned as a column,
    # because that is the property (-0.317% measured) rather than the art.
    descent = [row[2] for row in rows]
    assert rows[0][2] == "v" and descent[1] == "R"
    assert "".join(descent[1:11]) == "R/WsW+sWNX"
    assert sum(row.count("s") for row in rows) == 3  # op, address, and the value
    assert sum(row.count("R") for row in rows) == 2  # the request and the value
    assert "@" in "".join(rows)
    # A taller corridor is the SAME fourteen-row loop with an empty room under it.
    # Letting the loop follow the room instead is the one thing that measured a
    # regression here, so it is pinned rather than described.
    tall, _ = feed_unpack(V4_FEED_H + 7)
    assert len(tall) == V4_FEED_H + 7
    assert tall[:V4_FEED_H] == rows
    assert set("".join(tall[V4_FEED_H:])) == {" "}


# ── the corridor's two riser columns, consolidated into one ──────────────────
#: The shipped hi-res block *with* its request roof, which is what leaves the
#: forwarder room's first interior column free. ``_HIRES_KW`` deliberately has
#: no roof — every other lever is roof-independent — but ``feed_share_riser`` is
#: the one that is not, so it gets its own base.
_HIRES_ROOF_KW = dict(_HIRES_KW, request_roof=15, request_tuck=True, protocol="v4")


def test_the_shared_riser_is_opt_in_and_needs_a_column_to_share() -> None:
    """Off is byte-identical, and the case with no free column raises rather
    than drawing a pipe that leaves its gate heading north."""
    shipped = taped_store_block(HIRES_N, HIRES_PLAN, **_HIRES_ROOF_KW)
    assert (
        taped_store_block(
            HIRES_N, HIRES_PLAN, feed_share_riser=False, **_HIRES_ROOF_KW
        ).cells
        == shipped.cells
    )
    shared = taped_store_block(HIRES_N, HIRES_PLAN, feed_share_riser=True, **_HIRES_ROOF_KW)
    assert shared.cells != shipped.cells
    # ... and it is a forwarder-room column, so there has to be a forwarder room
    with pytest.raises(ValueError):
        taped_store_block(
            HIRES_N, HIRES_PLAN, **{**_HIRES_KW, "feed_teleport": False},
            protocol="v3", feed_share_riser=True,
        )
    # Without the roof there is no ``lead``, so the widest gate's east wall
    # stands where the shared column would be and the feed says so.
    with pytest.raises(ValueError, match="must head east"):
        taped_store_block(HIRES_N, HIRES_PLAN, feed_share_riser=True, protocol="v4",
                          **_HIRES_KW)


def test_the_shared_riser_moves_no_bank_and_shortens_every_link() -> None:
    """The block's own dimensions, its ports and every bank's column stay put;
    what changes is one column of corridor and, with it, one cell off each feed
    pipe and each chain link."""
    shipped = taped_store_block(HIRES_N, HIRES_PLAN, **_HIRES_ROOF_KW)
    shared = taped_store_block(HIRES_N, HIRES_PLAN, feed_share_riser=True, **_HIRES_ROOF_KW)
    assert (shared.width, shared.height) == (shipped.width, shipped.height)
    assert (shared.in_cell, shared.out_cell) == (shipped.in_cell, shipped.out_cell)
    assert shared.pipes == shipped.pipes
    # The banks are rooms and the gates' interiors are rooms; only the corridor
    # between them changes, so no `@` (a man, one per room) moves.
    assert {c for c, ch in shared.cells.items() if ch == "@"} == {
        c for c, ch in shipped.cells.items() if ch == "@"
    }
    # Every vertical pipe cell in the block: the count is one lower per corridor
    # (the climb is the same height, but the horizontal lead-in loses a cell).
    def arrows(cells: dict, ch: str) -> int:
        return sum(1 for v in cells.values() if v == ch)

    assert arrows(shared.cells, ">") == arrows(shipped.cells, ">") - 2 * (len(HIRES_PLAN) - 1)
    assert arrows(shared.cells, "^") == arrows(shipped.cells, "^")


@pytest.mark.parametrize("skip_batch", [1, 2, None])
def test_the_shared_riser_routes_every_hires_address_the_same(skip_batch) -> None:
    """The load-bearing test, and it has to be a readback over every address.

    Moving a climb one column west is exactly the change that builds either
    way: the feed pipe and the chain link end up in the same column, and if
    their row spans ever crossed, one gate's request would climb into the
    *forwarder* and be answered from the wrong bank — in silence, because a
    pipe that parses is a pipe that runs. So this writes a distinct value into
    every one of hi-res' 901 addresses and compares them address by address
    against the two-column corridor, over the real plan, the real hot-first
    chain, the real grown gates and each ring worker in turn.
    """

    def readback(share: bool) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N,
                HIRES_PLAN,
                feed_share_riser=share,
                **{**_HIRES_ROOF_KW, "skip_batch": skip_batch},
            )
        )
        writes = [x for a in range(1, HIRES_N) for x in (*_wire("v4", 1, a), (a * 41 + 7) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in _wire("v4", 0, a)]
            want = [(a * 41 + 7) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=800_000_000)
            assert res.fatal is None, (share, skip_batch, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(False)
    assert shipped == {a: (a * 41 + 7) % 9973 for a in range(1, HIRES_N)}
    assert readback(True) == shipped


# ── the bank ring's perimeter, and why shortening it buys nothing ────────────
def test_the_tight_ring_is_opt_in_and_shortens_only_the_slack_ones() -> None:
    """``tape_block`` searched its folds from 0 up and stopped at the first with
    room, and fold 0 is the *longest* ring — so every other fold was dead code.
    ``tight_ring`` reverses the preference. The block's box and both its ports
    are unchanged, because a fold only moves the return pipe's middle leg."""
    for skip in (1, 2):
        for n in (7, 8, 10, 22, 59, 103):
            a = machine.tape_block(n, skip_batch=skip)
            b = machine.tape_block(n, skip_batch=skip, tight_ring=True)
            assert (b.width, b.height) == (a.width, a.height), (n, skip)
            assert (b.in_cell, b.out_cell) == (a.in_cell, a.out_cell), (n, skip)
            assert n + 1 <= b.slots <= a.slots, (n, skip, a.slots, b.slots)
        # ... and the hot sizes really do shrink, or the knob would be a no-op
        assert machine.tape_block(8, skip_batch=skip, tight_ring=True).slots < 111
    # A ring too big for any fold still falls through to the serpentine, whose
    # capacity scales with area and which `tight_ring` does not touch.
    big = machine.tape_block(400, skip_batch=1)
    assert machine.tape_block(400, skip_batch=1, tight_ring=True).slots == big.slots


@pytest.mark.parametrize("skip_batch", [1, 2, None])
def test_the_tight_ring_reads_back_what_was_written(skip_batch) -> None:
    """A ring is a FIFO whose capacity is its cell count, and one value short
    does not fault — it **stalls**. So the guard is a readback over every hi-res
    address through the real plan and each ring worker, not a build."""

    def readback(tight: bool) -> dict[int, int]:
        engine = _standalone(
            taped_store_block(
                HIRES_N,
                HIRES_PLAN,
                tape_tight_ring=tight,
                **{**_HIRES_ROOF_KW, "skip_batch": skip_batch},
            )
        )
        writes = [x for a in range(1, HIRES_N) for x in (*_wire("v4", 1, a), (a * 43 + 3) % 9973)]
        bounds = [1]
        for m in HIRES_PLAN:
            bounds.append(bounds[-1] + m)
        out: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            reads = [x for a in range(lo, hi) for x in _wire("v4", 0, a)]
            want = [(a * 43 + 3) % 9973 for a in range(lo, hi)]
            res = engine.run(writes + reads, expected=want, max_ticks=800_000_000)
            assert res.fatal is None, (tight, skip_batch, lo, res.fatal)
            out.update(zip(range(lo, hi), res.output, strict=False))
        return out

    shipped = readback(False)
    assert shipped == {a: (a * 43 + 3) % 9973 for a in range(1, HIRES_N)}
    assert readback(True) == shipped


# ── the batch-1 v4 worker's own bindings ─────────────────────────────────────
@pytest.mark.parametrize("west_grow", [0, 1, 4])
@pytest.mark.parametrize("n", [7, 8, 10, 16, 100])
def test_the_v4_worker_reaches_the_pipe_each_receive_means(n: int, west_grow: int) -> None:
    """Every ``r`` in the batch-1 v4 body, against both incoming pipes, with the
    margin stated rather than assumed.

    This is the check that ``memory_tape.V2_V4_MAIN_ROW`` needs and that the
    readbacks are too far away to give. MAIN sits one row south of the request
    stub so the walk to P1 is a cell shorter, and the temptation is to move the
    stub down with it — which makes P1's own ``r`` **equidistant** between the
    ring return and the request wire (15 and 15), a tie SPEC hands to the
    northern segment. P1 then reads the next request as if it were a tape word:
    no build error, no fault, just the wrong answer, and only at ``west_grow``
    small enough that the four columns ``deadman-3d_hires`` ships do not hide it.

    So the margins are enumerated here at ``west_grow=0``, where they are
    tightest, and the assertion is **strict** — a tie is a failure even when the
    intended pipe would happen to win it, because a margin of zero is a margin
    that the next edit crosses without noticing.
    """
    from randomfun2026solvers.memory_tape import (
        V2_FWD_ROW,
        V2_IH,
        V2_RET_COL,
        V2_V4_IN_ROW,
        V2_V4_IW,
        V2_V4_OUT_COL,
        worker_v2,
    )

    art = worker_v2(n, park_const=True, protocol="v4")
    #: The four wall anchors, in worker-local coordinates: the cell of each pipe
    #: that touches this room (``SPEC.md``, "Which pipe do I talk to?"). The v4
    #: body has **its own** four — none of them is fixed, which is the whole
    #: reason the body could move west at all (``memory_tape.V2_V4_SHIFT``).
    request = (-2 - west_grow, V2_V4_IN_ROW)
    ring_in = (V2_RET_COL, V2_IH + 1)
    ring_out = (V2_V4_IW + 1, V2_FWD_ROW)
    answer = (V2_V4_OUT_COL, -2)

    def near(cell, a, b):
        da = abs(a[0] - cell[0]) + abs(a[1] - cell[1])
        db = abs(b[0] - cell[0]) + abs(b[1] - cell[1])
        return da, db

    seen = {"r": 0, "s": 0, "S": 0}
    for (x, y), ch in art.cell.items():
        if ch == "r":
            # The only receives that mean the request wire are MAIN's own and the
            # write's value word, both against the west wall; everything else in
            # the body is talking to the ring.
            want_request = x <= 2
            d_req, d_ring = near((x, y), request, ring_in)
            if want_request:
                assert d_req < d_ring, (f"the `r` at {(x, y)} means the request "
                                        f"wire but is {d_req} from it and {d_ring} "
                                        f"from the ring return")
            else:
                assert d_ring < d_req, (f"the `r` at {(x, y)} means the ring return "
                                        f"but is {d_ring} from it and {d_req} from "
                                        f"the request wire")
            seen["r"] += 1
        elif ch == "s":
            d_ring, d_ans = near((x, y), ring_out, answer)
            assert d_ring < d_ans, (f"the `s` at {(x, y)} means the ring but is "
                                    f"{d_ring} from it and {d_ans} from the answer")
            seen["s"] += 1
        elif ch == "S":
            seen["S"] += 1  # writes every outgoing pipe; it has nothing to bind
    assert seen["S"] == 1 and seen["r"] >= 6 and seen["s"] >= 5, seen


def test_the_v4_anchors_are_the_ones_that_licence_the_shift() -> None:
    """P1's ``r`` is the tightest binding in the body, and its margin is a
    property of **where the four wall anchors were put**, not of the layout.

    This is the correction to a conclusion that was recorded and wrong. With the
    request stub on ``V2_IN_ROW`` — where it sat because nobody had chosen
    otherwise — P1's ``r`` is 15 to the ring return against 16 to the request,
    and *any* westward shift ties or loses it, which read as "the body cannot
    move". The stub is a two-cell stub on a wall and every interior row takes it:
    moved to the last one, the same cell is 15 against 20 and three columns of
    travel are available. The answer riser moves for the same reason and buys
    P1's ``s`` the room to follow.

    So this pins the *margins*, not the coordinates, at both ``west_grow`` 0 and
    the shipped 4 — and it fails a tie even when the intended pipe would win it,
    because a margin of zero is a margin the next edit crosses without noticing.
    """
    from randomfun2026solvers.memory_tape import (
        V2_FWD_ROW,
        V2_IH,
        V2_IN_ROW,
        V2_RET_COL,
        V2_V4_IN_ROW,
        V2_V4_IW,
        V2_V4_MAIN_ROW,
        V2_V4_OUT_COL,
        V2_V4_SHIFT,
        worker_v2,
    )

    assert V2_V4_SHIFT == 3
    assert V2_V4_MAIN_ROW == V2_IN_ROW + 1  # MAIN is still a row south of V2_IN_ROW
    art = worker_v2(8, park_const=True, protocol="v4")
    p1 = next((x, y) for (x, y), ch in art.cell.items() if ch == "r" and x > 2 and y < 10)
    assert p1 == (10 - V2_V4_SHIFT, 6), p1

    ring_in = (V2_RET_COL, V2_IH + 1)
    ring_out = (V2_V4_IW + 1, V2_FWD_ROW)
    answer = (V2_V4_OUT_COL, -2)
    d = lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1])  # noqa: E731
    for west_grow, want in ((0, 2), (4, 6)):
        request = (-2 - west_grow, V2_V4_IN_ROW)
        assert d(p1, request) - d(p1, ring_in) == want, (west_grow, p1)
    # ... and the shipped stub row would have made every one of those negative:
    for west_grow in (0, 4):
        old = (-2 - west_grow, V2_IN_ROW)
        assert d(p1, old) - d(p1, ring_in) < 0, west_grow
    # P1's `s` is the other wall, and the answer riser is what keeps it clear.
    p1_s = (p1[0], p1[1] + 1)
    assert d(p1_s, answer) - d(p1_s, ring_out) == 2


# ── the rotating bank: skip the delta, not the address ──────────────────────
def _rot_kw() -> dict:
    """The hi-res block as ``lm1.machine`` builds it, minus the machine-geometry
    knobs the standalone wrapper cannot supply."""
    return dict(
        _HIRES_KW,
        gate_return_slack=0,
        request_roof=20,
        feed_share_riser=True,
        bank_west_grow=machine.TAPED_BANK_WEST_GROW[("deadman-3d_hires", "taped")],
        protocol="v5",
    )


def test_the_rotating_worker_stands_in_the_batched_body_s_own_room() -> None:
    """The third worker body, and the constraint that makes it cheap to trust.

    ``bank_w``/``bank_h`` are maxima over the banks and the batched worker sets
    both, so a narrower rotating body would still leave the block's pitch alone —
    but it would move the **ring**, because ``tape_block`` measures the fold and
    the serpentine from the worker's own bottom wall. Identical room means the
    shell, both ring pipes, the request stub and the answer riser are
    byte-identical and the only cells that differ anywhere in the machine are
    inside the worker's own walls.
    """
    from randomfun2026solvers import memory_tape as mt
    from randomfun2026solvers.lm1.machine import _tape_worker_spec

    assert (mt.V2_ROT_IW, mt.V2_ROT_IH) == (mt.V2_JUMP_IW, mt.V2_JUMP_V4_IH)
    rot = _tape_worker_spec(2, "v4", True)
    wide = _tape_worker_spec(2, "v4")
    assert rot[1:] == wide[1:], "the rotating body must keep all four wall anchors"
    assert rot[0] is mt.worker_v2_rot and wide[0] is mt.worker_v2_jump
    # ... and it is the batched packed-wire body only; there is no narrow form
    # and no two-word form, so ask for one and be told rather than built.
    for batch, proto in ((1, "v4"), (2, "v3"), (4, "v3")):
        with pytest.raises(ValueError):
            _tape_worker_spec(batch, proto, True)
    with pytest.raises(ValueError):
        mt.worker_v2_rot(53, protocol="v3")
    with pytest.raises(ValueError):
        mt.worker_v2_rot(53, park_const=False)
    # the room really is the same size, at every ring size the cut uses
    for n in (22, 53, 59, 115, 135, 442):
        c = mt.worker_v2_rot(n)
        assert (c.w, c.h) == (mt.V2_JUMP_IW, mt.V2_JUMP_V4_IH), n
        rows = c.rows()
        # P2 is gone: the batched body's second ring occupied rows 14 and 15.
        assert all(r.strip() == "" for r in rows[10:]), n
        # ... and the ring size is parked directly above MAIN, not four rows up
        assert rows[4].startswith("vM"), rows[4]
        assert rows[5].startswith(">r%Mb]"), rows[5]


def test_every_rotating_worker_send_still_binds_the_pipe_it_means() -> None:
    """The silent failure this body could have is a wrong *pipe*, not a crash.

    ``r`` takes the nearest incoming and ``s`` the nearest outgoing, Manhattan,
    ties by reading order — so the two receives that must take the **request**
    wire (MAIN's word and the write's value) and the four that must take the
    **ring** are decided by geometry alone. Enumerated strictly at ``west_grow``
    0 **and** 4: the shipped 4 pushes the request stub four columns further west
    and hides a mis-bind that breaks every other caller, which is exactly how
    ``V2_V4_MAIN_ROW`` was nearly landed wrong.
    """
    from randomfun2026solvers import memory_tape as mt

    iw, ih = mt.V2_ROT_IW, mt.V2_ROT_IH
    c = mt.worker_v2_rot(53)
    want = {
        (1, 5): "request",       # MAIN's own word
        (13, 2): "request",      # the write's value, on the request stub's row
        (20, 6): "ring-return",  # P1
        (21, 6): "ring-forward",
        (21, 7): "ring-forward",
        (22, 7): "ring-return",
        (16, 8): "ring-forward",  # the write's swap
        (17, 8): "ring-return",
        (27, 9): "ring-return",  # the read's target
        (31, 4): "ring-forward",  # INIT's fill
    }
    seen = {
        (x, y) for (x, y), ch in c.cell.items() if ch in "rs"
    }
    assert seen == set(want), sorted(seen ^ set(want))
    for west_grow in (0, 4):
        ports = {
            "request": ("in", (-2 - west_grow, mt.V2_IN_ROW)),
            "ring-return": ("in", (mt.V2_JUMP_RET_COL, ih + 1)),
            "ring-forward": ("out", (iw + 1, mt.V2_JUMP_FWD_ROW)),
            "answer": ("out", (mt.V2_OUT_COL, -2)),
        }
        for (x, y), name in want.items():
            side = "in" if c.cell[(x, y)] == "r" else "out"
            ranked = sorted(
                (abs(x - px) + abs(y - py), nm)
                for nm, (sd, (px, py)) in ports.items()
                if sd == side
            )
            assert ranked[0][1] == name, ((x, y), west_grow, ranked)
            # a tie is decidable but it is still a one-cell margin, and this
            # body has none: the tightest is the write's `s` at four.
            assert ranked[1][0] - ranked[0][0] >= 4, ((x, y), west_grow, ranked)


def test_rotating_banks_are_opt_in_and_the_default_block_is_byte_identical() -> None:
    """Off is the shipped grid to the cell; on, only those banks' rooms move."""
    kw = _rot_kw()
    shipped = taped_store_block(HIRES_N, HIRES_PLAN, **kw)
    assert taped_store_block(HIRES_N, HIRES_PLAN, **kw, rotate_banks=()).cells == (
        shipped.cells
    )
    rot = taped_store_block(HIRES_N, HIRES_PLAN, **kw, rotate_banks=(0, 1, 2, 5))
    assert rot.cells != shipped.cells
    # the block's own box, man census and pipe inventory do not move at all: the
    # rotating worker stands in the batched body's room and the rotating
    # forwarder in the relay's, so nothing is added and nothing is resized.
    assert (rot.width, rot.height) == (shipped.width, shipped.height)
    assert rot.pipes == shipped.pipes
    assert sum(1 for c in rot.cells.values() if c == "@") == sum(
        1 for c in shipped.cells.values() if c == "@"
    )
    # ... and the refusals, which are the pairs that cannot be built rather than
    # tuning values: no packed wire, no forwarder, a narrow bank, a bad index.
    for bad in (
        dict(protocol="v3"),
        dict(feed_teleport=False),
    ):
        with pytest.raises(ValueError):
            taped_store_block(HIRES_N, HIRES_PLAN, **{**kw, **bad},
                              rotate_banks=(5,))
    with pytest.raises(ValueError):
        taped_store_block(HIRES_N, HIRES_PLAN, **kw, rotate_banks=(4,))  # 8 slots
    with pytest.raises(ValueError):
        taped_store_block(HIRES_N, HIRES_PLAN, **kw, rotate_banks=(11,))


@pytest.mark.slow
def test_a_rotating_bank_reads_back_in_ascending_descending_and_random_order()  -> None:
    """The test without which a green suite means nothing for this body.

    A rotating bank keeps no "the ring comes home" invariant — it keeps "the head
    is at ``addr + 1``", and **every** access has to leave it there exactly,
    writes included. One off-by-one desynchronises the bank permanently and
    nothing errors: it answers the wrong slot.

    The shipped 901-address readback cannot see that, because it **ascends**: the
    rotational delta is then 1 at every step and the wraparound is never walked.
    So this reads all 901 back three more ways — descending (delta ``n-1``, the
    backwards case, every time), and two independent shuffles — and then streams
    interleaved reads and writes inside each bank, which is the case where the
    head has to survive a write that also moved it.
    """
    import random

    kw = _rot_kw()
    engine = _standalone(
        taped_store_block(HIRES_N, HIRES_PLAN, **kw, rotate_banks=(0, 1, 2, 5))
    )
    val = {a: (a * 37 + 11) % 9973 for a in range(1, HIRES_N)}
    writes = [w for a in range(1, HIRES_N) for w in (2 * a - 1, val[a])]
    bounds = [1]
    for m in HIRES_PLAN:
        bounds.append(bounds[-1] + m)
    rng = random.Random(20260802)

    for name, key in (
        ("ascending", lambda xs: xs),
        ("descending", lambda xs: xs[::-1]),
        ("random", lambda xs: rng.sample(xs, len(xs))),
    ):
        got: dict[int, int] = {}
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            hi = min(hi, HIRES_N)
            addrs = key(list(range(lo, hi)))
            res = engine.run(
                writes + [2 * a for a in addrs],
                expected=[val[a] for a in addrs],
                max_ticks=900_000_000,
            )
            assert res.fatal is None, (name, lo, res.fatal)
            got.update(zip(addrs, res.output, strict=False))
        assert got == val, name

    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, HIRES_N)
        if hi - lo < 4:
            continue
        addrs = rng.sample(range(lo, hi), min(40, hi - lo))
        stream: list[int] = []
        want: list[int] = []
        cur = dict(val)
        for i, a in enumerate(addrs):
            if i % 3 == 2:
                cur[a] = (a * 91 + 5) % 7919
                stream += [2 * a - 1, cur[a]]
            else:
                stream += [2 * a]
                want.append(cur[a])
        res = engine.run(writes + stream, expected=want, max_ticks=900_000_000)
        assert res.fatal is None and res.output == want, (lo, res.fatal)


def test_the_batched_v4_body_fetches_its_write_value_where_it_still_binds() -> None:
    """``_JUMP_V4_TIGHT_ARMS``: the entry and the write arm, both pulled in.

    The write's value ``r`` stood beside the west wall for one reason — it has to
    bind the **request** pipe and not the ring return — and on row 9 that holds
    much further east than it was using. This pins where the line actually is,
    at ``west_grow`` 0 **and** 4, because hires ships 4 and 4 is the tight case:
    the grown wall pushes the request stub *further* from the glyph, so a column
    that binds at 0 can silently take a tape word for its value at 4.
    """
    from randomfun2026solvers import memory_tape as mt

    def margin(x: int, y: int, west_grow: int) -> int:
        req = abs(x - (-2 - west_grow)) + abs(y - mt.V2_IN_ROW)
        ring = abs(x - mt.V2_JUMP_RET_COL) + abs(y - (mt.V2_JUMP_V4_IH + 1))
        return ring - req  # positive means the request pipe wins

    # where it stood: column 2 of row 12, three rows further down and five
    # columns further west than it needs to be.
    assert (margin(2, 12, 4), margin(2, 12, 0)) == (7, 11)
    # where it can stand, on the arm's own row 9
    assert [margin(x, 9, 4) for x in (7, 8, 9)] == [3, 1, -1]
    assert [margin(x, 9, 0) for x in (7, 8, 9)] == [7, 5, 3]
    # ... so 9 is a *wrong pipe* at the shipped wall and legal at an ungrown one,
    # which is the shape of every silent mis-bind this family has had; 8 is a
    # one-cell margin. The body takes 7 and keeps three.
    c = mt.worker_v2_jump(53, park_const=True, protocol="v4")
    assert c.cell[(7, 9)] == "r"
    # the ring size parks directly above MAIN rather than four rows up, and the
    # climb home stops on that row instead of running to row 1
    rows = c.rows()
    assert rows[4].startswith("vM`") and rows[5].startswith(">rb]-M")
    assert rows[1].strip() in ("v", "v" + rows[1].strip()[1:])  # only the turn left
    assert "M`" not in rows[1]
    # off is the shipped grid, and only this branch moves: the v3 batched body
    # and both narrow bodies are byte-identical at either setting.
    try:
        mt._JUMP_V4_TIGHT_ARMS = False
        shipped = mt.worker_v2_jump(53, park_const=True, protocol="v4").rows()
        v3 = mt.worker_v2_jump(53, park_const=True).rows()
        narrow = mt.worker_v2(53, park_const=True, protocol="v4").rows()
    finally:
        mt._JUMP_V4_TIGHT_ARMS = True
    assert shipped != c.rows()
    assert mt.worker_v2_jump(53, park_const=True).rows() == v3
    assert mt.worker_v2(53, park_const=True, protocol="v4").rows() == narrow
    # ... and no other caller can reach this branch at all, which is the whole
    # reason `matmul`, `sudoku` and the byte-pinned `deadman-3d` do not move: the
    # batched *v4* body exists only behind a packed wire, and one slug has one.
    assert list(machine.TAPED_PROTOCOL) == [("deadman-3d_hires", "taped")]
    assert machine.TAPED_PROTOCOL[("deadman-3d_hires", "taped")] in ("v4", "v5")
