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


def test_dense_gradient_matches_finite_difference():
    """The gradient is the whole program; a sign error here is invisible downstream."""
    p = mm.init_params(seed=2)
    pixels = [(i * 5) % 16 for i in range(64)]
    label = 3
    f = mm.forward(p, pixels)
    g = mm.backward(p, pixels, f, label)

    eps = 64  # Q12: 64/4096 — big enough to beat truncation, small enough to be local
    for idx in (0, 37, 179):
        lo, hi = _loss_at(p, pixels, label, idx, -eps), _loss_at(p, pixels, label, idx, +eps)
        numeric = (hi - lo) * mm.SCALE // (2 * eps)
        assert _same_sign_and_magnitude(numeric, g.dense_w[idx])


def test_conv_gradient_matches_finite_difference():
    p = mm.init_params(seed=2)
    pixels = [(i * 5) % 16 for i in range(64)]
    label = 3
    f = mm.forward(p, pixels)
    g = mm.backward(p, pixels, f, label)
    eps = 64
    for idx in (0, 8, 17):
        lo = _loss_at(p, pixels, label, idx, -eps, which="conv_w")
        hi = _loss_at(p, pixels, label, idx, +eps, which="conv_w")
        numeric = (hi - lo) * mm.SCALE // (2 * eps)
        assert _same_sign_and_magnitude(numeric, g.conv_w[idx])


def test_bias_gradients_match_finite_difference():
    """The biases learn too, and a dropped sign there is just as invisible."""
    p = mm.init_params(seed=2)
    pixels = [(i * 5) % 16 for i in range(64)]
    label = 3
    f = mm.forward(p, pixels)
    g = mm.backward(p, pixels, f, label)
    eps = 64
    for which, analytic in (("dense_b", g.dense_b), ("conv_b", g.conv_b)):
        for idx in range(len(analytic)):
            lo = _loss_at(p, pixels, label, idx, -eps, which=which)
            hi = _loss_at(p, pixels, label, idx, +eps, which=which)
            numeric = (hi - lo) * mm.SCALE // (2 * eps)
            assert _same_sign_and_magnitude(numeric, analytic[idx]), f"{which}[{idx}]"


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


def test_init_params_is_deterministic_and_bounded():
    a, b, c = mm.init_params(seed=7), mm.init_params(seed=7), mm.init_params(seed=8)
    assert a == b, "the LCG must be a pure function of the seed"
    assert a != c
    assert all(abs(w) <= mm.SCALE // 8 for w in a.conv_w)
    assert all(abs(w) <= mm.SCALE // 16 for w in a.dense_w)


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


def _loss_at(p, pixels, label, idx, delta, which="dense_w"):
    import copy

    q = copy.deepcopy(p)
    getattr(q, which)[idx] += delta
    return mm.cross_entropy(mm.forward(q, pixels).probs, label)


def _same_sign_and_magnitude(numeric: int, analytic: int) -> bool:
    """Fixed point makes exact equality hopeless; agreement in sign and order is the claim."""
    if numeric == 0 or analytic == 0:
        return abs(numeric - analytic) <= mm.SCALE
    return numeric * analytic > 0 and 0.3 <= abs(numeric / analytic) <= 3.0
