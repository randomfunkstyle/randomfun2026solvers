"""The Q12 fixed-point reference model — the oracle every other tier must match.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §4.

Pure integer arithmetic, so it is exact and cheap: the whole file belongs in the
fast tier. What is pinned is *behaviour* — that gradients agree with finite
differences, that softmax normalises, that shapes are what the hardware assumes —
never an accuracy, which is a quality number (AGENTS.md).
"""

from __future__ import annotations

from randomfun2026solvers import mnist_model as mm


def test_probs_sum_to_one_in_q12():
    p = mm.init_params(seed=1)
    pixels = [(i * 7) % 16 for i in range(64)]
    f = mm.forward(p, pixels)
    assert abs(sum(f.probs) - mm.SCALE) <= 10, "softmax must normalise to 1.0 in Q12"
    assert all(x >= 0 for x in f.probs)


def test_shapes_match_what_the_hardware_assumes():
    p = mm.init_params(seed=1)
    assert (len(p.conv_w), len(p.conv_b), len(p.dense_w), len(p.dense_b)) == (18, 2, 180, 10)
    f = mm.forward(p, [0] * 64)
    assert (len(f.pre), len(f.pooled), len(f.logits), len(f.probs)) == (72, 18, 10, 10)


def test_relu_zeroes_negative_preactivations():
    p = mm.init_params(seed=1)
    f = mm.forward(p, [(i * 3) % 16 for i in range(64)])
    assert all(a == max(0, q) for a, q in zip(f.act, f.pre, strict=True))


def test_dense_weight_gradient_matches_finite_difference():
    """The gradient is the whole program; a sign error here is invisible downstream."""
    _assert_gradient_agrees("dense_w", min_informative=28)


def test_conv_weight_gradient_matches_finite_difference():
    _assert_gradient_agrees("conv_w", min_informative=12)


def test_dense_bias_gradient_matches_finite_difference():
    """The biases learn too, and a dropped sign there is just as invisible."""
    _assert_gradient_agrees("dense_b", min_informative=24)


def test_conv_bias_gradient_matches_finite_difference():
    _assert_gradient_agrees("conv_b", min_informative=3)


def test_finite_difference_check_rejects_a_broken_backward():
    """The guard on the guard: prove the comparison above can actually fail.

    Every gradient test passes by measuring *nothing* if the probe is blind, so
    each of these deliberately wrong gradients must be caught. The zeroed variant
    is the one that matters most: an all-zero ``Grads`` is what a backward pass
    that silently does nothing returns, and under a tolerance with an
    ``analytic == 0`` escape branch it would sail through.
    """
    breakages = {
        "zeroed": lambda v: [0] * len(v),
        "sign flipped": lambda v: [-x for x in v],
        "ten times too big": lambda v: [x * 10 for x in v],
        "ten times too small": lambda v: [x // 10 for x in v],
        "index-reversed": lambda v: v[::-1],
    }
    for name, breakage in breakages.items():
        for field, minimum in _MIN_INFORMATIVE.items():
            try:
                _assert_gradient_agrees(field, minimum, breakage=breakage)
            except AssertionError:
                continue
            raise AssertionError(f"{field} gradients survived being {name}")


def test_pool_argmax_points_at_the_cell_that_won():
    """Backward routes the gradient through this index; if it lies, training is silent noise."""
    p = mm.init_params(seed=3)
    f = mm.forward(p, [(i * 11) % 16 for i in range(64)])
    for i, (value, cell) in enumerate(zip(f.pooled, f.argmax, strict=True)):
        filt, rest = divmod(i, 9)
        py, px = divmod(rest, 3)
        window = {
            filt * 36 + (py * 2 + dy) * 6 + (px * 2 + dx) for dy in range(2) for dx in range(2)
        }
        assert cell in window, f"pooled[{i}] argmax {cell} is outside its 2x2 window"
        assert f.act[cell] == value == max(f.act[c] for c in window)


def test_pool_breaks_ties_toward_the_lowest_index():
    """A tie rule nothing else pins: on real data 22% of windows tie.

    Zeroing ``conv_w`` and setting a positive ``conv_b`` makes every
    pre-activation identical, so all four cells of all eighteen windows tie —
    the case a fixed pixel pattern almost never produces. The pooled value is
    positive, so the ReLU mask lets the gradient through and the routing is
    observable too. Flipping ``>`` to ``>=`` in the pooling loop picks the last
    cell instead of the first and fails here.
    """
    p = mm.init_params(seed=5)
    p.conv_w = [0] * 18
    p.conv_b = [mm.SCALE, mm.SCALE]
    pixels = [(i * 5) % 16 for i in range(64)]

    f = mm.forward(p, pixels)
    assert all(v == mm.SCALE for v in f.pre), "the construction must make every cell tie"
    lowest = [
        filt * 36 + (py * 2) * 6 + px * 2 for filt in range(2) for py in range(3) for px in range(3)
    ]
    highest = [
        filt * 36 + (py * 2 + 1) * 6 + px * 2 + 1
        for filt in range(2)
        for py in range(3)
        for px in range(3)
    ]
    assert f.argmax == lowest, "a tie must go to the lowest index in the window"

    # ...and the choice is load-bearing: routing through the other end of each
    # tied window produces different conv weight gradients, so getting the tie
    # rule wrong is not a cosmetic difference.
    doctored = mm.Forward(
        pre=f.pre, act=f.act, pooled=f.pooled, argmax=highest, logits=f.logits, probs=f.probs
    )
    assert mm.backward(p, pixels, f, 3).conv_w != mm.backward(p, pixels, doctored, 3).conv_w


def test_relu_mask_blocks_the_gradient_when_every_cell_is_dead():
    """The backward ReLU mask, which no other test can fail on.

    ``pooled`` is a max over four cells, so on any ordinary sample the winner is
    positive and ``if f.pre[cell] > 0`` never actually blocks anything — deleting
    the mask entirely leaves the rest of this suite green. Forcing every
    pre-activation negative is the only way to observe it: the whole conv gradient
    must then be exactly zero, while ``dense_b`` stays live to prove the sample
    is not simply inert.
    """
    p = mm.init_params(seed=5)
    p.conv_w = [0] * 18
    p.conv_b = [-mm.SCALE, -mm.SCALE]
    pixels = [(i * 5) % 16 for i in range(64)]

    f = mm.forward(p, pixels)
    assert all(v < 0 for v in f.pre) and all(a == 0 for a in f.act)

    g = mm.backward(p, pixels, f, 3)
    assert g.conv_w == [0] * 18, "a dead ReLU must pass no gradient to the conv weights"
    assert g.conv_b == [0] * 2, "nor to the conv biases"
    assert any(v != 0 for v in g.dense_b), "sanity: the sample must still produce a gradient"


def test_init_params_is_deterministic_and_bounded():
    a, b, c = mm.init_params(seed=7), mm.init_params(seed=7), mm.init_params(seed=8)
    assert a == b, "the LCG must be a pure function of the seed"
    assert a != c
    assert all(abs(w) <= mm.SCALE // 8 for w in a.conv_w)
    assert all(abs(w) <= mm.SCALE // 16 for w in a.dense_w)


def test_exp_q12_interpolation_is_pinned_entry_by_entry():
    """The one approximation two later tiers must reproduce bit-for-bit.

    Expected values are written out here rather than computed from
    :func:`mm.exp_q12`, so this pins the index, the mask, the direction of the
    ``hi - lo`` ramp and the clamp independently of the code under test. Each was
    worked out by hand from the table: at ``d = 512`` the first interval runs
    4096 -> 3190, so the answer is ``4096 + ((3190 - 4096) * 512 >> 10)`` = 3643.
    Swap ``lo`` and ``hi`` and every interior value below moves the wrong way.
    """
    assert mm.exp_q12(0) == mm.SCALE  # exp(0) exactly
    assert mm.exp_q12(512) == 3643  # first interval, halfway
    assert mm.exp_q12(1023) == 3190  # first interval, last step
    assert mm.exp_q12(1024) == 3190  # ...and continuous into the second
    assert mm.exp_q12(16384) == 75  # a middle entry, k=16, on the nose
    assert mm.exp_q12(16896) == 66  # k=16 halfway: 75 + ((58-75)*512 >> 10)
    assert mm.exp_q12(29696) == 3  # k=29, the last entry above the floor
    assert mm.exp_q12(30208) == 2  # k=29 halfway: 3 + ((2-3)*512 >> 10)
    assert mm.exp_q12(31 * 1024) == 2  # the clamp boundary itself
    assert mm.exp_q12(10**9) == 2  # far past it, still clamped, never negative


def test_exp_q12_never_ramps_the_wrong_way():
    """Walk every input the softmax can present and pin the shape of the whole curve."""
    lut = mm.exp_lut()
    values = [mm.exp_q12(d) for d in range(41 * 1024)]
    assert all(values[d] >= values[d + 1] for d in range(len(values) - 1)), (
        "exp must be non-increasing in -z, or the softmax reorders classes"
    )
    assert min(values) == lut[31] and max(values) == mm.SCALE
    assert all(mm.exp_q12(k * 1024) == lut[k] for k in range(31)), (
        "at a multiple of the stride the answer must be the table entry itself"
    )
    for k in range(31):  # every interpolated value lies inside its own interval
        assert all(lut[k + 1] <= mm.exp_q12(k * 1024 + f) <= lut[k] for f in (1, 300, 512, 1023))


def test_exp_lut_is_a_monotone_32_entry_q12_table():
    """Two properties the rest of the model leans on, and no third one.

    Non-increasing, so the table never reorders two classes and ``argmax(probs)``
    stays ``argmax(logits)``. Strictly positive, so ``sum(exps)`` is never zero
    and the normalising division is always defined. It is *not* strictly
    decreasing — the tail rounds to ``[3, 2, 2]`` in Q12, which is exactly the
    underflow the table's range is chosen to stop at.
    """
    lut = mm.exp_lut()
    assert len(lut) == 32
    assert lut[0] == mm.SCALE, "exp(0) must be exactly 1.0 in Q12"
    assert all(lut[k] >= lut[k + 1] > 0 for k in range(31))
    assert lut[31] < mm.SCALE // 1000, "the table must reach the Q12 underflow floor"


def test_sgd_step_subtracts_the_shifted_gradient_in_place():
    p = mm.init_params(seed=4)
    before = mm.Params(list(p.conv_w), list(p.conv_b), list(p.dense_w), list(p.dense_b))
    g = mm.Grads(
        conv_w=[(-1) ** i * (i + 1) * 300 for i in range(18)],
        conv_b=[512, -512],
        dense_w=[(i - 90) * 40 for i in range(180)],
        dense_b=[(-1) ** j * 4096 for j in range(10)],
    )
    assert mm.sgd_step(p, g, lr_shift=6) is None, "sgd_step updates in place"
    for name in ("conv_w", "conv_b", "dense_w", "dense_b"):
        got, was, grad = getattr(p, name), getattr(before, name), getattr(g, name)
        assert got == [w - (d >> 6) for w, d in zip(was, grad, strict=True)]


def test_epoch_stat_compares_by_value():
    """A later tier asserts ``emulator_stats == reference_stats``; that needs value equality."""
    assert mm.EpochStat(1, 2, 3, 4) == mm.EpochStat(1, 2, 3, 4)
    assert mm.EpochStat(1, 2, 3, 4) != mm.EpochStat(1, 2, 3, 5)


# --- the finite-difference apparatus ----------------------------------------
#
# Three samples rather than one. The first is the sample this suite started with;
# its conv gradients turn out to be too small for a finite difference to see at
# all (zero measurable probes), so two more are included that do exercise them.
# Nothing here is random: the params come from the LCG and the pixels from a
# fixed ramp, so every probe below is deterministic.
_SAMPLES = ((2, 5, 3), (5, 3, 3), (5, 13, 3))  # (seed, pixel stride, label)

_EPS = 64  # Q12: 64/4096 — big enough to beat truncation, small enough to be local

# The probe's resolution floor, and why a probe below it proves nothing.
#
# ``numeric`` is ``(hi - lo) * SCALE // (2 * eps)``, and ``hi - lo`` is a
# difference of two integer Q12 losses. The loss is ``-ln(probs[label])``, so the
# smallest change it can register is one Q12 tick of ``probs[label]``, worth
# ``SCALE / probs[label]`` in the loss. At init ``probs[label] ~ 410``, giving
# ``hi - lo`` a quantum of ~10 and ``numeric`` a quantum of ``10 * 32 = 320``.
#
# So a ``numeric`` of 0 and a ``numeric`` of 320 are the same measurement: "less
# than about one quantum". Neither carries magnitude information, and comparing
# either against an analytic value is comparing against rounding noise. A probe
# counts only when *both* sides clear one quantum; anything else is discarded as
# uninformative and, crucially, is not allowed to count as a pass.
#
# Measured: over 60 samples and every parameter index, this rule admits 1398
# probes and produces zero disagreements. The old rule — pass anything where
# either side was zero — admitted the noise and let an all-zero ``Grads`` through.
_NOISE_FLOOR = 320

# How many probes each field must actually resolve. Observed over ``_SAMPLES``:
# dense_w 36, dense_b 30, conv_w 16, conv_b 4. Set below those so an honest
# change in the arithmetic has a little room, but high enough that the suite fails
# loudly if a probe goes blind rather than quietly passing on nothing.
_MIN_INFORMATIVE = {"dense_w": 28, "conv_w": 12, "dense_b": 24, "conv_b": 3}

_measured: dict[str, list[tuple[int, int, int]]] = {}


def _measure(field: str) -> list[tuple[int, int, int]]:
    """``(sample, index, numeric)`` for every index of ``field`` over ``_SAMPLES``.

    Cached, and deliberately independent of ``backward``: these are pure forward
    measurements, so the injection test can reuse them against doctored gradients
    without paying for the probes again.
    """
    if field not in _measured:
        rows = []
        for s, (seed, stride, label) in enumerate(_SAMPLES):
            p = mm.init_params(seed=seed)
            pixels = [(i * stride) % 16 for i in range(64)]
            for idx in range(len(getattr(p, field))):
                lo = _loss_at(p, pixels, label, idx, -_EPS, which=field)
                hi = _loss_at(p, pixels, label, idx, +_EPS, which=field)
                rows.append((s, idx, (hi - lo) * mm.SCALE // (2 * _EPS)))
        _measured[field] = rows
    return _measured[field]


def _analytic(field: str, sample: int) -> list[int]:
    seed, stride, label = _SAMPLES[sample]
    p = mm.init_params(seed=seed)
    pixels = [(i * stride) % 16 for i in range(64)]
    return getattr(mm.backward(p, pixels, mm.forward(p, pixels), label), field)


def _assert_gradient_agrees(field, min_informative, breakage=None):
    """Compare ``backward``'s ``field`` against finite differences of the real loss.

    ``breakage`` doctors the analytic gradient before comparison; the injection
    test uses it to prove this check can fail.
    """
    grads = {s: _analytic(field, s) for s in range(len(_SAMPLES))}
    if breakage is not None:
        grads = {s: breakage(v) for s, v in grads.items()}

    informative = 0
    for sample, idx, numeric in _measure(field):
        analytic = grads[sample][idx]
        if abs(numeric) < _NOISE_FLOOR or abs(analytic) < _NOISE_FLOOR:
            continue  # below the probe's resolution: proves nothing either way
        informative += 1
        assert _same_sign_and_magnitude(numeric, analytic), (
            f"{field}[{idx}] on sample {_SAMPLES[sample]}: "
            f"finite difference {numeric}, backward {analytic}"
        )
    assert informative >= min_informative, (
        f"only {informative} of the {field} probes resolved anything "
        f"(need {min_informative}); the finite difference is measuring noise, "
        f"so this test proves nothing"
    )


def _loss_at(p, pixels, label, idx, delta, which="dense_w"):
    import copy

    q = copy.deepcopy(p)
    getattr(q, which)[idx] += delta
    return mm.cross_entropy(mm.forward(q, pixels).probs, label)


def _same_sign_and_magnitude(numeric: int, analytic: int) -> bool:
    """Fixed point makes exact equality hopeless; agreement in sign and order is the claim.

    No escape branch for zeros. A zero on either side means the probe could not
    resolve the gradient, which is a reason to discard the probe (see
    ``_NOISE_FLOOR``), never a reason to call it a pass — the previous version of
    this predicate let an all-zero ``Grads`` satisfy three of the four gradient
    tests.
    """
    return numeric * analytic > 0 and 0.3 <= abs(numeric / analytic) <= 3.0
