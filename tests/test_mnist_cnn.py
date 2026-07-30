"""The unrolled LM-1 training program, against the reference model.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §5.

The claim under test is *equality between tiers*: the emulator running the real
assembly must reproduce the Q12 reference model exactly. That is the whole
correctness argument, and it is why no test here asserts an accuracy.

The staged tests in the middle exist for a specific reason: the program is ~5,500
instructions per sample, so "the parameters disagree" is not a debuggable
statement. Each stage pins one section of the sample body against the matching
piece of ``mnist_model``, so a regression names its own section.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from randomfun2026solvers import mnist_cnn, mnist_data, mnist_model
from randomfun2026solvers.lm1 import asm, isa, machine, stream
from randomfun2026solvers.lm1.emulator import Emulator
from randomfun2026solvers.lm1.store import DictStore, StoreError
from randomfun2026solvers.lm1.stream import StreamError

S = mnist_cnn.S
REPO = Path(__file__).resolve().parents[1]

#: Building the machine runs the engine's structural analysis (``_check_pipe_count``
#: is inside ``build``), so every test below that touches the grid needs the bundled
#: Node CLI. They are *not* marked slow: the whole file is under 8s serial, and the
#: binding checks are exactly what has to keep running on every loop.
needs_engine = pytest.mark.skipif(
    shutil.which("node") is None or not (REPO / "littleman" / "lm.mjs").exists(),
    reason="needs node and the bundled littleman engine",
)


@pytest.fixture(scope="module")
def reference():
    """The reference model's view of train sample 0 at init — the oracle."""
    params = mnist_model.init_params(seed=mnist_cnn.SEED)
    pixels, label = mnist_cnn.first_train_sample()
    forward = mnist_model.forward(params, pixels)
    grads = mnist_model.backward(params, pixels, forward, label)
    return params, pixels, label, forward, grads


def _cells(store: dict[int, int], base: int, n: int) -> list[int]:
    return [store[base + k] for k in range(n)]


# ── the frozen tables ────────────────────────────────────────────────────────
def test_the_emitted_tables_equal_the_reference_tables():
    """Both LUTs are float-derived in ``mnist_model`` and literals here.

    Freezing them is what turns "no realistic drift" into a checked fact: the
    reference builds them from ``math.exp``/``math.log``/``round`` at import, and
    the grid will hold this module's literals in ROM.
    """
    assert mnist_cnn.EXP_TABLE == tuple(mnist_model.exp_lut())
    assert mnist_cnn.LN_MANT_TABLE == tuple(mnist_model._LN_MANT)
    assert mnist_cnn.LN2 == mnist_model._LN2


# ── the program ──────────────────────────────────────────────────────────────
def test_program_assembles():
    src = mnist_cnn.emit_source(lr_shift=6)
    program = asm.assemble(src, isa=isa.LM1_EXT)
    assert program.P > 0
    assert all(w >= 0 for w in program.words), "ROM literals are digits only"


def test_the_program_asks_for_the_depth_four_stream_unit():
    """A depth-3 unit cannot decode PUSHA/ROTB/RDP/UPDB at all (spec §4.2a)."""
    program = asm.assemble(mnist_cnn.emit_source(lr_shift=6), isa=isa.LM1_EXT)
    assert program.unit == "stream4"
    assert asm.UNIT_TRIE_BITS[program.unit] == 4


def test_the_assembled_program_needs_no_python_side_setup():
    """Assemble the emitted text, run it on a bare Emulator, still be exact.

    This is the property the whole verification ladder rests on: the ``.asm`` is
    the artifact, and it has to describe its own machine. ``UPDB``'s shift is not a
    command field — it is wired into the unit — so the program declares it with
    ``.equ STREAM_LR_SHIFT`` and the emulator reads it from there. Before that, a
    fresh ``Emulator`` silently used the unit's default 12 and produced wrong
    pixels and wrong weights with nothing raised, which is exactly what this test
    would now catch.
    """
    src = mnist_cnn.emit_source(
        lr_shift=6, epochs_from_input=False, train_samples=1, single_step=True
    )
    program = asm.assemble(src, isa=isa.LM1_EXT)
    assert program.equs[asm.STREAM_LR_SHIFT_EQU] == mnist_model.SHIFT + 6

    # Nothing below configures the unit or the store beyond what the program says.
    emulator = Emulator(program, store=DictStore(size=mnist_cnn.STORE_WORDS))
    assert emulator.stream.lr_shift == mnist_model.SHIFT + 6
    stream = mnist_cnn._boot_stream() + mnist_cnn._dataset_stream(
        epochs=1, train_n=1, val_n=0
    )
    result = emulator.run(input=stream, max_instructions=10**9)
    assert result.halted

    got = mnist_cnn._extract_params(emulator.store, emulator.stream)
    want = mnist_model.init_params(seed=mnist_cnn.SEED)
    pixels, label = mnist_cnn.first_train_sample()
    f = mnist_model.forward(want, pixels)
    mnist_model.sgd_step(want, mnist_model.backward(want, pixels, f, label), 6)
    assert got == want


def test_a_stream4_program_cannot_be_built_on_a_depth_three_unit():
    """The wrong width must fail loudly: it computes nonsense otherwise.

    At mod-8 a ``PUSHA`` word decodes as ``EMIT``, a ``ROTB`` as ``FILLB``, so a
    machine built from ``stream.py``'s depth-3 trie would run this program to
    completion and train nothing. Both widths are now drawn *and placed*, so this is
    no longer a stopgap about the missing drawing — it is the guard it was always
    meant to be: a depth-**3** unit refusing a depth-**4** program.

    The refusal has to live at the already-decoded ``(code, arg)`` pair, because no
    *word* can reach it: ``word % 8`` is always < 8, which is exactly why a depth-3
    unit handed a depth-4 word cannot notice on its own.
    """
    from randomfun2026solvers.lm1.store import StreamUnit

    # A depth-3 unit has no leaf for any of the four new arms, so the codes this
    # program emits do not exist at that width at all.
    assert not ({"PUSHA", "ROTB", "RDP", "UPDB"} & set(stream.arm_codes(3)))
    assert {"PUSHA", "ROTB", "RDP", "UPDB"} <= set(stream.arm_codes(4))

    # And it refuses the decoded pair rather than aliasing it onto an original arm.
    three = StreamUnit(lambda: 0, lambda _v: None, trie_bits=3)
    with pytest.raises(StoreError, match="3-bit"):
        three._dispatch(StreamUnit.CODES["PUSHA"], 0)

    # Both widths build; a width this module does not draw is still refused.
    assert stream.build_stream(a_slots=16, b_slots=64, c_slots=16, trie_bits=3)
    assert stream.build_stream(
        a_slots=16, b_slots=64, c_slots=16, trie_bits=asm.UNIT_TRIE_BITS["stream4"]
    )
    with pytest.raises(StreamError, match="depth 5"):
        stream.build_stream(a_slots=16, b_slots=64, c_slots=16, trie_bits=5)


def test_an_out_of_range_store_access_faults(reference):
    """The 351-word map is enforced, so a stray index cannot read a live table.

    The exp LUT and the log table are adjacent in the STORE, so an off-by-one in
    the clamp would silently interpolate log endpoints as exp entries — a wrong
    answer no differential test can localise. A sized store turns it into a raise.
    """
    store = DictStore(size=mnist_cnn.STORE_WORDS)
    store.send(0)
    with pytest.raises(StoreError):
        store.send(mnist_cnn.STORE_WORDS)


def test_ring_sizes_cover_the_high_water_marks():
    a, b, c = mnist_cnn.RING_SIZES
    assert b >= 180, "ring B holds the dense weights"
    assert c >= 10, "the accumulator holds the ten logits"


def test_ring_sizes_cover_what_one_step_actually_uses():
    """The generator's sizes against the unit's own marks, measured not assumed."""
    run = mnist_cnn.run_emulator(epochs=0, samples=1, report=True)
    a, b, c = mnist_cnn.RING_SIZES
    high_a, high_b, high_c = run.ring_high
    assert high_a <= a
    assert high_b <= b
    assert high_c <= c


def test_dropout_is_refused_rather_than_silently_ignored():
    with pytest.raises(NotImplementedError):
        mnist_cnn.emit_source(lr_shift=6, dropout=True)


# ── the staged tests: one section at a time ──────────────────────────────────
def test_boot_loads_the_parameters_the_reference_starts_from(reference):
    params, *_ = reference
    store, ring = mnist_cnn.probe_one_sample("boot")
    assert _cells(store, S.CONVW, 18) == params.conv_w
    assert _cells(store, S.CONVB, 2) == params.conv_b
    assert _cells(store, S.DENSEB, 10) == params.dense_b
    assert _cells(store, S.EXP, 32) == list(mnist_cnn.EXP_TABLE)
    assert _cells(store, S.LNM, 33) == list(mnist_cnn.LN_MANT_TABLE)
    wi = [
        ring[mnist_cnn.WI0 + i * mnist_cnn.WI_STRIDE + j] for j in range(10) for i in range(18)
    ]
    wj = [
        ring[mnist_cnn.WJ0 + j * mnist_cnn.WJ_STRIDE + i] for j in range(10) for i in range(18)
    ]
    assert wi == params.dense_w, "the feature-major dense layout"
    assert wj == params.dense_w, "the class-major dense layout"


def test_load_sample_unpacks_the_image_into_ring_b(reference):
    _, pixels, label, *_ = reference
    store, ring = mnist_cnn.probe_one_sample("load")
    want = mnist_model.to_q12(pixels)
    assert ring[mnist_cnn.X0 : mnist_cnn.X0 + 64] == want
    assert _cells(store, S.X, 64) == want
    assert store[S.LABEL] == label
    assert len(pixels) == mnist_data.IMG**2


def test_conv_forward_matches_the_reference_pre_activations(reference):
    *_, forward, _ = reference
    store, _ = mnist_cnn.probe_one_sample("conv")
    assert _cells(store, S.PRE, 72) == forward.pre


def test_relu_pool_matches_the_reference(reference):
    *_, forward, _ = reference
    store, _ = mnist_cnn.probe_one_sample("pool")
    assert _cells(store, S.POOLED, 18) == forward.pooled
    got = _cells(store, S.ARGMAX, 18)
    # The argmax is only *read* through the ReLU gate, and the gate is closed
    # exactly when the whole window is non-positive — where the reference's
    # tie-break and this one can name different cells and both mean "no gradient".
    for i, (cell, pooled) in enumerate(zip(forward.argmax, forward.pooled, strict=True)):
        if pooled > 0:
            assert got[i] == cell, f"feature {i}"
        else:
            assert forward.pre[got[i]] <= 0, f"feature {i} must stay gated off"


def test_dense_and_softmax_match_the_reference(reference):
    _, _, label, forward, _ = reference
    store, _ = mnist_cnn.probe_one_sample("dense")
    assert _cells(store, S.LOGITS, 10) == forward.logits
    assert _cells(store, S.PROBS, 10) == forward.probs
    assert store[S.LOSS] == mnist_model.cross_entropy(forward.probs, label)
    assert store[S.PRED] == mnist_model.predict(forward)
    assert store[S.CORRECT] == int(mnist_model.predict(forward) == label)


def test_backward_matches_every_reference_gradient(reference):
    params, pixels, label, forward, grads = reference
    store, ring = mnist_cnn.probe_one_sample("backward")
    assert _cells(store, S.DZ, 10) == grads.dense_b, "dz is the dense bias gradient"
    want_dpooled = [
        sum(grads.dense_b[j] * params.dense_w[j * 18 + i] for j in range(10)) >> mnist_model.SHIFT
        for i in range(18)
    ]
    assert _cells(store, S.DPOOLED, 18) == want_dpooled
    # The section applies the step as it goes, so compare against post-step values.
    after = mnist_model.init_params(seed=mnist_cnn.SEED)
    mnist_model.sgd_step(after, grads, lr_shift=6)
    assert _cells(store, S.CONVW, 18) == after.conv_w
    assert _cells(store, S.CONVB, 2) == after.conv_b
    assert _cells(store, S.DENSEB, 10) == after.dense_b
    wi = [
        ring[mnist_cnn.WI0 + i * mnist_cnn.WI_STRIDE + j] for j in range(10) for i in range(18)
    ]
    assert wi == after.dense_w


@pytest.mark.parametrize("lr_shift", [4, 8])
def test_the_learning_rate_shift_is_a_parameter_not_a_constant(lr_shift):
    """The unit's own shift is ``SHIFT + lr_shift``, so it must move with it.

    ``UPDB`` applies one shift for two callers — the weight update and the ring-B
    write — so a hardcoded 18 would pass at ``lr_shift=6`` and be wrong everywhere
    else, silently: the weights would still update, just by the wrong amount.
    """
    got = mnist_cnn.run_emulator(
        epochs=0, lr_shift=lr_shift, samples=1, return_params=True
    )
    want = mnist_model.init_params(seed=mnist_cnn.SEED)
    pixels, label = mnist_cnn.first_train_sample()
    f = mnist_model.forward(want, pixels)
    mnist_model.sgd_step(want, mnist_model.backward(want, pixels, f, label), lr_shift)
    assert got == want


# ── the gate ─────────────────────────────────────────────────────────────────
def test_emulator_matches_the_reference_on_one_sample():
    """One sample, one step: every parameter must be bit-identical afterwards."""
    got = mnist_cnn.run_emulator(epochs=0, lr_shift=6, samples=1, return_params=True)
    want = mnist_model.init_params(seed=mnist_cnn.SEED)
    pixels, label = mnist_cnn.first_train_sample()
    f = mnist_model.forward(want, pixels)
    g = mnist_model.backward(want, pixels, f, label)
    mnist_model.sgd_step(want, g, lr_shift=6)
    assert got.conv_w == want.conv_w
    assert got.dense_w == want.dense_w
    assert got.conv_b == want.conv_b
    assert got.dense_b == want.dense_b


def test_emulator_matches_the_reference_over_eight_samples():
    """Eight samples and one epoch report: the loop, the metrics, the divides.

    The one-sample test cannot see a body that fails to restore a ring, or a
    metric that divides by the wrong count. Eight samples and a real epoch
    boundary can, and it still runs in the fast tier.
    """
    run = mnist_cnn.run_emulator(epochs=1, lr_shift=6, samples=8, report=True)
    params = mnist_model.init_params(seed=mnist_cnn.SEED)
    want = _reference_epochs(params, epochs=1, samples=8, lr_shift=6)
    assert run.stats == want
    # Metrics alone would pass a drift that happens to round to the same four
    # integers, so the parameters are compared element for element as well.
    assert run.params.conv_w == params.conv_w
    assert run.params.conv_b == params.conv_b
    assert run.params.dense_w == params.dense_w
    assert run.params.dense_b == params.dense_b


@pytest.mark.slow
def test_emulator_matches_the_reference_over_two_full_epochs():
    run = mnist_cnn.run_emulator(epochs=2, lr_shift=6, report=True)
    params = mnist_model.init_params(seed=mnist_cnn.SEED)
    want = mnist_model.train(params, epochs=2, lr_shift=6)
    assert run.stats == want
    # 6,000 samples of drift would have to be invisible in eight integers to get
    # this far; the 210 parameters are what actually has to agree.
    assert run.params.conv_w == params.conv_w
    assert run.params.conv_b == params.conv_b
    assert run.params.dense_w == params.dense_w
    assert run.params.dense_b == params.dense_b


def _reference_epochs(params, *, epochs: int, samples: int, lr_shift: int):
    """``mnist_model.train`` restricted to the first ``samples`` images an epoch.

    Written out rather than reusing ``train`` because ``train`` has no cap; the
    per-epoch arithmetic is copied deliberately so the *capped* run is compared
    against the same online-metrics rule the uncapped one uses.
    """
    train_words, val_words = mnist_data.load_packed()
    per = mnist_data.WORDS_PER_IMAGE
    train_set = [
        mnist_data.unpack_image(train_words[k * per : (k + 1) * per]) for k in range(samples)
    ]
    val_set = [mnist_data.unpack_image(val_words[k * per : (k + 1) * per]) for k in range(samples)]
    stats = []
    for _ in range(epochs):
        loss = 0
        correct = 0
        for pixels, label in train_set:
            f = mnist_model.forward(params, pixels)
            loss += mnist_model.cross_entropy(f.probs, label)
            correct += mnist_model.predict(f) == label
            mnist_model.sgd_step(
                params, mnist_model.backward(params, pixels, f, label), lr_shift
            )
        val_loss, val_acc = mnist_model.evaluate(params, val_set)
        stats.append(
            mnist_model.EpochStat(
                train_loss=loss // samples,
                train_acc=correct * 100 // samples,
                val_loss=val_loss,
                val_acc=val_acc,
            )
        )
    return stats


# ── the panels, and the machine ──────────────────────────────────────────────
# Everything below is about the *two-panel build*, which is a different program from
# the one above: `panels=True` paints four pixels an epoch instead of emitting four
# words, because a machine cannot have both a display and an `O` room's output on a
# display-judged wall (and, here, because the STREAM block already owns the machine's
# one legal `I`/`O` pair — SPEC.md makes a second I/O room a load error).
@needs_engine
def test_the_two_panels_are_the_engines_two_displays_in_reading_order():
    m = mnist_cnn.build_machine()
    assert m.panels == (mnist_cnn.PANEL, mnist_cnn.PANEL)
    # Two boxes, four `+=` corners (each box has a north-west and a south-west one).
    corners = sorted(
        y
        for y, row in enumerate(m.rows)
        for x, ch in enumerate(row)
        if ch == "+" and row[x + 1 : x + 2] == "="
    )
    assert len(corners) == 4, f"two panel boxes, two `+=` corners each; got {corners}"
    tops = corners[0], corners[2]
    assert [b - t - 1 for t, b in zip(tops, (corners[1], corners[3]), strict=True)] == [
        mnist_cnn.PANEL[1]
    ] * 2, "each box's interior is the declared height"
    assert tops[0] < tops[1], "panel 0 must precede panel 1 in reading order (R2)"
    # `display` stays the *first* panel's size, which is what every judged caller means.
    assert m.display == mnist_cnn.PANEL


@needs_engine
def test_each_panel_is_fed_by_its_own_room_and_no_room_feeds_two():
    """R1, which is the whole reason this machine has the shape it has.

    One room may feed at most one display (``tests/test_mnist_display.py`` pins the
    engine's refusal), so the CPU cannot drive both panels however many opcodes it
    spends — each panel needs a relay room of its own, reached by a lane of its own.
    """
    m = mnist_cnn.build_machine()
    assert len(m.panel_ports) == 2
    # Three ports a panel, and no port cell is shared between the two panels.
    assert [sorted(p) for p in m.panel_ports] == [sorted(machine.DSP_BANDS)] * 2
    cells = [cell for ports in m.panel_ports for cell in ports.values()]
    assert len(set(cells)) == 6
    for x, y in cells:
        assert m.rows[y][x] == "s"


@needs_engine
def test_the_engine_finds_exactly_the_pipes_the_generator_drew():
    """The acceptance criterion, and on its own it is *not* sufficient.

    ``machine._check_pipe_count`` already runs inside ``build``, so reaching this
    assertion at all means the counts agreed. It is restated here because the count is
    the one cheap check that catches a leg running alongside a room's corner — and it
    is followed immediately by the mis-bind tests below, because a count cannot
    notice a pipe that is drawn correctly and *bound* to the wrong thing
    (``ARCH.md`` §4.4), and this branch has shipped that mistake five times.
    """
    from randomfun2026solvers.littleman import Littleman

    m = mnist_cnn.build_machine()
    info = Littleman().analyze(m.text())
    assert len(info.displays) == 2
    assert len(info.pipes) == 26


def _panel_side(info, panel: int, end: tuple[int, int]) -> str | None:
    (x0, y0), (x1, y1) = info.displays[panel]["min"], info.displays[panel]["max"]
    x, y = end
    if y == y0 - 1 and x0 < x < x1:
        return "top"
    if x == x0 - 1 and y0 < y < y1:
        return "left"
    if y == y1 + 1 and x0 < x < x1:
        return "bottom"
    return None


#: Which side of the panel each port must land on. Which side a pipe lands on is
#: what *makes* it that port (``SPEC.md``), so this mapping is the whole claim.
WANT_SIDE = {
    machine.Band.DSP_ADDR: "top",
    machine.Band.DSP_DATA: "left",
    machine.Band.DSP_SWAP: "bottom",
}


def _port_sides(text: str, panel: int, ports: dict[str, tuple[int, int]]) -> dict[str, str | None]:
    """Ask the engine's own ``route`` which side of ``panel`` each ``s`` glyph reaches.

    The one check that can catch a mis-bound port, and shared verbatim by the test
    that asserts the machine is right and the meta-test that asserts the check
    notices when it is not — so the two cannot drift apart.
    """
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    info = lm.analyze(text)
    out: dict[str, str | None] = {}
    for band, (x, y) in ports.items():
        cells = lm.route(text, x, y)
        assert cells, f"panel {panel}'s {band} `s` at {(x, y)} binds no pipe at all"
        out[band] = _panel_side(info, panel, cells[-1].as_tuple())
    return out


@needs_engine
def test_every_port_routes_to_its_own_panels_own_side():
    """The nearest-pipe oracle (``ARCH.md`` §7.1) on the real grid, panel by panel.

    Six ``s`` glyphs in two relay rooms, and each has five rivals it must lose to.
    ADDR is the top wall, DATA the left, SWAP the bottom, so a swapped pair paints
    where it meant to address and nothing anywhere reports it.
    """
    m = mnist_cnn.build_machine()
    text = m.text()
    for panel, ports in enumerate(m.panel_ports):
        assert _port_sides(text, panel, ports) == WANT_SIDE, f"panel {panel}"


@needs_engine
def test_a_deliberately_mis_bound_port_is_caught_by_the_route_check():
    """The meta-test: a check that cannot fail is worth nothing.

    Two of the relay's outlet columns are swapped on the grid, which is exactly the
    silent failure ``ARCH.md`` §4.4 describes — the pipes are all still there, the
    counts are all still right, the machine still loads and still runs to completion,
    and it paints where it meant to address. The route check above has to notice, and
    the pipe count has to *not* notice, or it is being credited with work it does not
    do.
    """
    from randomfun2026solvers.littleman import Littleman

    m = mnist_cnn.build_machine()
    rows = list(m.rows)
    # Swap panel 0's ADDR and SWAP `s` glyphs for the two `.` cells at each other's
    # columns: the arms now send to each other's ports. Nothing else moves.
    addr = m.panel_ports[0][machine.Band.DSP_ADDR]
    swap = m.panel_ports[0][machine.Band.DSP_SWAP]

    def move(cell: tuple[int, int], to_col: int) -> None:
        x, y = cell
        row = list(rows[y])
        assert row[x] == "s", f"{cell} is {row[x]!r}, not the arm's `s`"
        assert row[to_col] in " .", f"({to_col}, {y}) is {row[to_col]!r}, not free interior"
        row[x], row[to_col] = " ", "s"
        rows[y] = "".join(row)

    move(addr, swap[0])
    move(swap, addr[0])
    text = "\n".join(rows) + "\n"

    info = Littleman().analyze(text)
    assert len(info.pipes) == 26, "the count is unchanged: this is why it is insufficient"
    assert len(info.displays) == 2, "and so is the display count"

    moved = dict(m.panel_ports[0])
    moved[machine.Band.DSP_ADDR] = (swap[0], addr[1])
    moved[machine.Band.DSP_SWAP] = (addr[0], swap[1])
    sides = _port_sides(text, 0, moved)
    # The *same* check the test above passes with must now complain, and say how.
    assert sides != WANT_SIDE
    assert sides[machine.Band.DSP_ADDR] == "bottom", "ADDR now reaches SWAP's wall"
    assert sides[machine.Band.DSP_SWAP] == "top", "SWAP now reaches ADDR's wall"
    assert sides[machine.Band.DSP_DATA] == "left", "and DATA is untouched"


@needs_engine
def test_the_checked_in_machine_matches_its_generator():
    """AGENTS.md: the artifact-matches-generator assertion is what makes every shape
    change visible as a regenerated ``.man`` diff, which is why nothing here pins a
    dimension or a tick count.
    """
    want = (REPO / "tasks" / "solutions" / "mnist-cnn.man").read_text(encoding="utf-8")
    assert mnist_cnn.build_machine().text() == want, (
        "mnist-cnn.man is stale; regenerate with `python -m randomfun2026solvers.mnist_cnn "
        "--man ../../tasks/solutions/mnist-cnn.man --html /tmp/mnist.html --json /tmp/mnist.json`"
    )


# ── the plotting arithmetic ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "loss",
    [0, 1, 1999, 2000, 2001, 2199, 2200, 4000, 7999, 8000, 8001, 99999],
)
def test_the_emitted_loss_row_is_the_reference_mapping_including_both_clamps(loss):
    """``loss_row`` is the oracle; the asm emits the same arithmetic.

    Both clamps are load-bearing rather than defensive: an ADDR outside the panel is
    a run-time error in the panel model and a silent wrap in hardware, so an epoch
    off the scale must pin to a row rather than address one that is not there.
    """
    row = mnist_cnn.loss_row(loss)
    assert 0 <= row < mnist_cnn.PLOT_ROWS
    q = max(0, min((loss - mnist_cnn.LOSS_LO) // mnist_cnn.LOSS_PER_ROW, mnist_cnn.PLOT_ROWS - 1))
    assert row == mnist_cnn.PLOT_ROWS - 1 - (q if loss >= mnist_cnn.LOSS_LO else 0)
    assert mnist_cnn.loss_row(mnist_cnn.LOSS_LO) == mnist_cnn.PLOT_ROWS - 1
    assert mnist_cnn.loss_row(mnist_cnn.LOSS_HI) == 0


def test_accuracy_maps_the_whole_axis_with_no_clamp():
    assert mnist_cnn.acc_row(0) == mnist_cnn.PLOT_ROWS - 1
    assert mnist_cnn.acc_row(100) == 0
    rows = [mnist_cnn.acc_row(a) for a in range(101)]
    assert rows == sorted(rows, reverse=True), "more accuracy is never a lower pixel"
    assert set(rows) <= set(range(mnist_cnn.PLOT_ROWS))


def test_the_machine_plots_exactly_where_the_reference_mapping_says():
    """The emitted asm against ``loss_row``/``acc_row``, through the emulator.

    This is the test that would fail if the branchy clamp were emitted wrong: it
    compares the *painted pixel* of every epoch against the row the pure function
    gives for the number the ``OUT`` build reports for that same epoch.
    """
    run = mnist_cnn.run_emulator(epochs=2, lr_shift=6, samples=8, frames=True)
    stats = mnist_cnn.run_emulator(epochs=2, lr_shift=6, samples=8)
    assert [len(f) for f in run.frames] == [3, 3], "the axes frame plus one an epoch"
    train, val = f"{mnist_cnn.TRAIN_COLOUR:x}", f"{mnist_cnn.VAL_COLOUR:x}"

    def check(frame, col, train_row, val_row):
        # Validation is painted second, so when the two land on the same row the cell
        # carries the validation colour. Asserting otherwise would pin an accident of
        # the two curves being apart.
        assert frame[val_row][col] == val
        assert frame[train_row][col] == (val if train_row == val_row else train)

    for e, stat in enumerate(stats):
        col = mnist_cnn.COL0 + e
        check(
            run.frames[mnist_cnn.LOSS_PANEL][e + 1],
            col,
            mnist_cnn.loss_row(stat.train_loss),
            mnist_cnn.loss_row(stat.val_loss),
        )
        check(
            run.frames[mnist_cnn.ACC_PANEL][e + 1],
            col,
            mnist_cnn.acc_row(stat.train_acc),
            mnist_cnn.acc_row(stat.val_acc),
        )


def test_the_panels_stay_a_persistent_framebuffer():
    """Every commit is ``SWAP 1``, so epoch *n*'s frame still holds epochs 1..n-1.

    With ``SWAP 0`` the machine would have to repaint 158 axis cells and every earlier
    point on every commit, and the curve is the point of the machine.
    """
    run = mnist_cnn.run_emulator(epochs=3, lr_shift=6, samples=4, frames=True)
    swaps = [v for _panel, port, v in _panel_writes() if port == 2]
    assert swaps and set(swaps) == {1}
    for frames in run.frames:
        drawn = [sum(ch != "0" for row in f for ch in row) for f in frames]
        assert drawn == sorted(drawn), "a frame never loses pixels the last one had"
        assert drawn[-1] > drawn[0], "and the epochs do add some"


def _panel_writes():
    """The port writes of the run that produced ``run.frames``, re-derived.

    ``RunReport`` deliberately keeps frames rather than writes — frames are what the
    engine can be compared against — so a test about the *writes* re-runs them.
    """
    src = mnist_cnn.emit_source(lr_shift=6, epochs=3, train_samples=4, val_samples=4, panels=True)
    program = asm.assemble(src, name="mnist", isa=isa.LM1_EXT)
    store = DictStore(size=mnist_cnn.STORE_WORDS)
    em = Emulator(program, store=store)
    stream_in = mnist_cnn._boot_stream() + mnist_cnn._dataset_stream(
        epochs=3, train_n=4, val_n=4
    )
    return em.run(input=[3, *stream_in], max_instructions=10**12).panel_writes


def test_panel_zeros_writes_still_reach_display_writes_unchanged():
    """The compatibility claim, stated as a test.

    ``plotter``, ``palette``, ``snake`` and ``deadman-3d`` all read
    ``display_writes`` as ``(port, value)``. Panel 0's writes must appear there
    exactly as before *and* in the panel-aware channel; panel 1's must appear only in
    the panel-aware one, or a single-panel consumer would silently see a second
    panel's pixels.
    """
    writes = _panel_writes()
    assert any(p == 1 for p, _port, _v in writes), "the second panel is used at all"
    src = mnist_cnn.emit_source(lr_shift=6, epochs=3, train_samples=4, val_samples=4, panels=True)
    program = asm.assemble(src, name="mnist", isa=isa.LM1_EXT)
    em = Emulator(program, store=DictStore(size=mnist_cnn.STORE_WORDS))
    stream_in = mnist_cnn._boot_stream() + mnist_cnn._dataset_stream(epochs=3, train_n=4, val_n=4)
    result = em.run(input=[3, *stream_in], max_instructions=10**12)
    assert list(result.display_writes) == [
        (port, v) for panel, port, v in result.panel_writes if panel == 0
    ]


def test_the_out_build_and_the_panel_build_report_the_same_numbers():
    """The two variants of one program have to agree, or the panels are a fiction.

    The ``OUT`` build is what every equality test against the reference model reads,
    and the panel build is what the grid runs; nothing else ties them together.
    """
    stats = mnist_cnn.run_emulator(epochs=2, lr_shift=6, samples=8)
    run = mnist_cnn.run_emulator(epochs=2, lr_shift=6, samples=8, frames=True)
    assert run.params == mnist_cnn.run_emulator(
        epochs=2, lr_shift=6, samples=8, report=True
    ).params
    assert len(stats) == 2


def test_frames_and_single_step_are_refused_together():
    with pytest.raises(ValueError, match="single step"):
        mnist_cnn.run_emulator(epochs=0, frames=True)
    with pytest.raises(ValueError, match="single_step"):
        mnist_cnn.emit_source(lr_shift=6, panels=True, single_step=True)


@needs_engine
def test_more_epochs_than_the_panels_have_columns_is_refused():
    with pytest.raises(ValueError, match="epoch columns"):
        mnist_cnn.build_machine(epochs=mnist_cnn.MAX_EPOCH_COLS + 1)
