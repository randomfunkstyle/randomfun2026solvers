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

import pytest
from randomfun2026solvers import mnist_cnn, mnist_data, mnist_model
from randomfun2026solvers.lm1 import asm, isa, stream
from randomfun2026solvers.lm1.emulator import Emulator
from randomfun2026solvers.lm1.store import DictStore, StoreError
from randomfun2026solvers.lm1.stream import StreamError

S = mnist_cnn.S


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
    completion and train nothing. Refusing to build the wrong one is a correctness
    property; what the refusal *says* changed with Task 6, which drew the depth-4
    unit (twelve arms, eleven pipes, every glyph checked against the engine's own
    ``route``) but did not place the block around it — so the refusal is now about
    the placement rather than about the trie. Either way it is a raise, and this
    test pins the raise, not the wording of the part that is still to do.
    """
    with pytest.raises(StreamError, match="the drawing does not"):
        stream.build_stream(
            a_slots=16, b_slots=64, c_slots=16, trie_bits=asm.UNIT_TRIE_BITS["stream4"]
        )
    # matmul's own width still builds.
    assert stream.build_stream(a_slots=16, b_slots=64, c_slots=16, trie_bits=3)


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
