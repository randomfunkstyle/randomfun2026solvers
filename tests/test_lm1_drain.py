"""The DRAIN ladder: it discards exactly ``BP`` words, at ~one read per tick.

Two oracles, deliberately different in kind. :func:`drain.walk` implements six
glyphs and nothing else, which makes an *exhaustive* sweep of every count cheap
— and correctness here is entirely about counts, since one word off silently
executes the wrong instruction (``machine.rom_words``, §5.3). The reference
interpreter then proves the same grid actually loads, binds and runs; it is the
authority on the machine, but 2**9 runs of node is minutes, so it samples.

The cost assertions are the point of the component and are pinned as tightly as
the design allows: a counted loop cannot beat 2 ticks a word and today's ships 4,
so a ladder that drifts above ~1.3 has stopped being worth its area.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers import littleman
from randomfun2026solvers.lm1 import drain

BITS = 9  # 0..511 words — the range `little-little-man`'s backward jumps live in


@pytest.fixture(scope="module")
def lm() -> littleman.Littleman:
    return littleman.Littleman()


# ── counts ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bits", [1, 2, 3, 4, 6, 9])
@pytest.mark.parametrize("even", [False, True])
def test_every_count_discards_exactly_that_many_words(bits: int, even: bool) -> None:
    """Exhaustive over the whole range, because off-by-one is the failure mode."""
    if even and bits < 2:
        pytest.skip("an even ladder needs a stage above bit 0")
    block = drain.build_drain(bits, even=even)
    for n in block.counts():
        reads, _ticks = drain.walk(block, n)
        assert reads == n, f"BP={n} discarded {reads}"


@pytest.mark.parametrize("max_width", [None, 32, 16, 8, 4, 2, 1])
def test_folding_the_ladder_narrower_does_not_change_what_it_reads(max_width: int | None) -> None:
    """``max_width`` is a footprint knob; it must be invisible to the semantics."""
    block = drain.build_drain(BITS, max_width=max_width)
    assert [drain.walk(block, n)[0] for n in block.counts()] == list(block.counts())


def test_the_ladder_leaves_bp_at_zero() -> None:
    """``walk`` raises if it does not — the caller's next ``a``/``d`` depends on it."""
    block = drain.build_drain(BITS)
    for n in (0, 1, 2, 255, 256, block.capacity):
        drain.walk(block, n)  # raises DrainError on a non-zero BP


def test_a_count_the_ladder_cannot_hold_is_refused() -> None:
    block = drain.build_drain(4)
    with pytest.raises(drain.DrainError):
        drain.walk(block, block.capacity + 1)


# ── cost ─────────────────────────────────────────────────────────────────────
def test_the_hairpin_ladder_costs_n_plus_five_per_stage() -> None:
    """The design's whole claim, as an equality rather than a bound.

    A stage is ``x``, the fold, the merge ``v`` and the ``]``: ``2**j + 5`` ticks
    taken and 5 skipped, so the total is ``n + 5 * bits`` for *every* count — the
    cost is affine in n with slope exactly 1. That slope is what "one read per
    tick" means here, and pinning it catches a fold that has started walking
    spare cells.

    Bit 0 is the one exception: a single read cannot be halved into a hairpin, so
    its stage takes the long way round and costs one extra cell. That is exactly
    the stage ``even=True`` deletes.
    """
    block = drain.build_drain(BITS)
    for n in (0, 1, 7, 128, 255, 256, block.capacity):
        assert drain.cost(block, n) == n + 5 * BITS + (n & 1)


def test_dropping_the_bit_zero_stage_is_cheaper_and_narrower() -> None:
    """``even`` is worth a flag: every ROM discard count is a multiple of two.

    ``rom_words`` scales every jump by two because the image is fixed-width, so
    stage 0 can only ever be skipped — five ticks and two columns spent on every
    jump to step around a run that is never taken.
    """
    plain = drain.build_drain(BITS)
    even = drain.build_drain(BITS, even=True)
    assert drain.cost(even, 510) < drain.cost(plain, 510)
    assert even.height < plain.height
    for n in even.counts():
        assert drain.cost(even, n) == n + 1 + 5 * (BITS - 1)


def test_an_even_ladder_refuses_an_odd_count() -> None:
    """It would discard one word too few and land the CPU on an operand."""
    even = drain.build_drain(BITS, even=True)
    with pytest.raises(drain.DrainError):
        drain.walk(even, 7)


def test_it_beats_the_counted_loop_it_replaces() -> None:
    """``machine._discard_loop`` is 8 cells a lap for 2 words: 4 ticks a word."""
    block = drain.build_drain(BITS)
    for n in (32, 64, 128, 256, block.capacity):
        assert drain.cost(block, n) < 4 * n
    # Where the money is: `little-little-man`'s backward edges discard most of the
    # program, and there the ladder is better than 3x.
    assert drain.cost(block, block.capacity) * 3 < 4 * block.capacity


@pytest.mark.parametrize(
    ("max_width", "ceiling"),
    [(None, 1.10), (32, 1.13), (16, 1.18), (8, 1.30), (4, 1.55)],
)
def test_narrower_folds_cost_more_but_stay_under_the_counted_loop(
    max_width: int, ceiling: float
) -> None:
    """The trade this knob exists for, measured.

    Folding trades width for two extra turn cells per pair of legs, so the rate
    degrades as ``1 + 2/width``; at width 1 it reaches 3.0 and the ladder stops
    being worth its area against a 2-ticks-a-word counted loop. These ceilings
    are the measured values, and they are what makes the knob a *choice* rather
    than a default.
    """
    block = drain.build_drain(BITS, max_width=max_width)
    rate = drain.cost(block, block.capacity) / block.capacity
    assert rate <= ceiling, f"{rate:.3f} ticks/word at max_width={max_width}"
    assert rate < 2.0, "a counted loop's floor — below this the ladder has no reason to exist"


# ── the grid, on the engine that judges ──────────────────────────────────────
@pytest.mark.parametrize("bits", [3, 4])
def test_the_reference_interpreter_consumes_exactly_bp_words(
    lm: littleman.Littleman, bits: int
) -> None:
    """Exhaustive on the real engine at a size where node is still cheap.

    The witness is the *next* word: feed ``n 1 2 3 ...`` and the man emits
    whatever survives the ladder, so an output of ``n + 1`` can only happen if
    exactly ``n`` were consumed. Nothing inside the block has to be trusted.
    """
    block = drain.build_drain(bits)
    src = "\n".join(drain.build_probe(block)[0]) + "\n"
    for n in range(block.capacity + 1):
        snap = lm.run(src, input=drain.probe_input(n, block.capacity + 2), max_ticks=20_000)
        assert snap.fatal is None, f"n={n}: {snap.fatal}"
        assert snap.output == [n + 1], f"n={n} left {snap.output}"


def test_the_reference_interpreter_agrees_about_the_even_ladder(
    lm: littleman.Littleman,
) -> None:
    """The variant a jump would actually use, exhaustively, on the real engine.

    Worth its own run rather than a parametrisation of the one above: ``even``
    deletes a stage and shifts ``BP`` before the first ``x``, so it is a different
    grid, and getting it wrong costs one word — which is the difference between
    executing an instruction and executing its operand.
    """
    block = drain.build_drain(5, even=True)
    src = "\n".join(drain.build_probe(block)[0]) + "\n"
    for n in block.counts():
        snap = lm.run(src, input=drain.probe_input(n, block.capacity + 2), max_ticks=20_000)
        assert snap.fatal is None, f"n={n}: {snap.fatal}"
        assert snap.output == [n + 1], f"n={n} left {snap.output}"


def test_the_reference_interpreter_agrees_at_the_size_that_matters(
    lm: littleman.Littleman,
) -> None:
    """A 9-bit ladder, sampled — including both ends and the carry boundaries."""
    block = drain.build_drain(BITS, max_width=16)
    src = "\n".join(drain.build_probe(block)[0]) + "\n"
    for n in (0, 1, 2, 3, 15, 16, 17, 127, 128, 129, 255, 256, 510, block.capacity):
        snap = lm.run(src, input=drain.probe_input(n, block.capacity + 2), max_ticks=20_000)
        assert snap.fatal is None, f"n={n}: {snap.fatal}"
        assert snap.output == [n + 1], f"n={n} left {snap.output}"


def test_the_probe_is_the_shape_the_binding_rule_needs(lm: littleman.Littleman) -> None:
    """One incoming pipe, one outgoing — so the probe proves counts, not routing.

    This is the honest limit of the probe and worth stating in a test rather than
    a comment: a ladder is a *lot* of ``r``, every one of them competes with every
    incoming pipe in the room (§7.1, nearest not nearest-that-can-proceed), and
    with a single incoming pipe that contest cannot be lost. Placing this block in
    the CPU is where binding becomes real, and :attr:`DrainBlock.reads` is what
    that placement has to check.
    """
    block = drain.build_drain(4)
    rows, translate = drain.build_probe(block)
    src = "\n".join(rows) + "\n"
    analysis = lm.analyze(src)
    assert len(analysis.pipes) == 2, "the probe must not add a rival for the reads"

    want = lm.route(src, *translate[block.reads[0]])
    assert want, "the first read binds to no pipe at all"
    for cell in block.reads:
        assert lm.route(src, *translate[cell]) == want, f"read at {cell} binds elsewhere"


# ── geometry ─────────────────────────────────────────────────────────────────
def test_entry_and_exit_share_the_spine() -> None:
    """The contract that makes the block droppable: a southbound man, in and out.

    Every turn the ladder needs is internal, so a caller routes one column and
    never has to know how the stages fold.
    """
    for max_width in (None, 16, 1):
        block = drain.build_drain(BITS, max_width=max_width)
        assert block.entry == (block.spine, 0)
        assert block.exit == (block.spine, block.height)
        assert block.exit not in block.cells, "the exit is the first cell *below* the block"


def test_every_drawn_read_is_reachable() -> None:
    """A read the man never steps on is wasted area, and hides a drawing bug."""
    block = drain.build_drain(BITS)
    walked: set[tuple[int, int]] = set()
    x, y = block.entry
    d = (0, 1)
    bp = block.capacity  # every bit set: the path that visits every stage's fold
    while (x, y) != block.exit:
        walked.add((x, y))
        ch = block.cells[(x, y)]
        if ch == "x":
            d = (-d[1], d[0]) if bp & 1 else (d[1], -d[0])
        elif ch == "]":
            bp >>= 1
        elif ch in "v<>":
            d = {"v": (0, 1), "<": (-1, 0), ">": (1, 0)}[ch]
        x, y = x + d[0], y + d[1]
    assert set(block.reads) <= walked
    assert len(block.reads) == block.capacity
