"""A Q12 fixed-point CNN on 8x8 MNIST — the oracle every later tier must match.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §4.

Why fixed point and not floats: the end product is a littleman grid whose only
number is a signed 64-bit integer. There is no float to be had, so the reference
model cannot use one either — if it did, "the emulator agrees with the reference"
would be a statement about rounding luck instead of about the program. Everything
here is ``int``, and every operation is one the engine can actually perform:
add, multiply, arithmetic shift, floored divide, compare.

Why Q12 (``SCALE = 4096``): activations live in 0..1, weights in roughly
+-0.25, and a Q12 product needs 24 bits of headroom before the shift. The conv
accumulator sums nine such products, the dense one eighteen — five extra bits,
so ~29 bits of a 63-bit budget. Q12 is the largest power of two that leaves the
gradient path (which multiplies two Q12 numbers *and* sums 36 of them) far away
from overflow while still resolving 1/4096 = 0.00024, finer than the 1/15 steps
the 4-bit input arrives in.

Why ``>>`` and never ``//`` or ``round``: ``>>`` floors toward negative
infinity, which is exactly what the engine's arithmetic shift does and what its
division does. Python's ``//`` agrees on negatives but ``int()``/C truncation
does not, and ``round`` is not expressible on the target at all. Using one rule
everywhere means the bias introduced by truncation is *the same* bias the
hardware has, so it cancels out of every cross-tier comparison.

Why the accumulator is shifted once, not per product: ``sum(a*b) >> SHIFT``
rather than ``sum((a*b) >> SHIFT)``. It is both cheaper on the target (one shift
per output instead of one per tap) and strictly more accurate, since the
truncation error is paid once rather than nine or eighteen times. Later tiers
must reproduce *this* grouping to stay bit-exact — it is not an implementation
detail.

Why this shape and no other: conv 3x3 valid over 8x8 gives 6x6, two filters,
per-channel bias, ReLU, MaxPool2 to 3x3x2 = 18 features, Dense(10), softmax,
cross-entropy. There is no BatchNorm because at batch size 1 the batch mean *is*
the sample: the layer would subtract the value it is normalising and emit a
constant. Its only surviving parameter is the per-channel shift, which is
``conv_b``. Dropout is likewise absent: with 18 features there is nothing to
spare.

Why there is no conv *input* gradient: conv is the first layer, so the gradient
with respect to its input is never read. Computing it would be 648 multiplies of
dead work per sample on a machine where every multiply is an instruction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from randomfun2026solvers import mnist_data as md

__all__ = [
    "SCALE",
    "SHIFT",
    "N_FILTERS",
    "KERNEL",
    "CONV_OUT",
    "POOL",
    "POOL_OUT",
    "N_FEATURES",
    "N_CLASSES",
    "Params",
    "Grads",
    "Forward",
    "EpochStat",
    "exp_lut",
    "exp_q12",
    "init_params",
    "forward",
    "cross_entropy",
    "backward",
    "sgd_step",
    "predict",
    "evaluate",
    "train",
]

SCALE = 4096
SHIFT = 12

N_FILTERS = 2
KERNEL = 3
CONV_OUT = md.IMG - KERNEL + 1  # 6, "valid" convolution
POOL = 2
POOL_OUT = CONV_OUT // POOL  # 3
N_CLASSES = 10

CONV_TAPS = KERNEL * KERNEL  # 9 weights per filter
CONV_CELLS = CONV_OUT * CONV_OUT  # 36 pre-activations per filter
N_FEATURES = N_FILTERS * POOL_OUT * POOL_OUT  # 18 pooled features

PIXEL_MAX = 15  # the input is a nibble; 15 maps to exactly 1.0 in Q12

_CONV_INIT = SCALE // 8  # +-0.125
_DENSE_INIT = SCALE // 16  # +-0.0625

_LCG_MUL = 1103515245
_LCG_ADD = 12345
_LCG_MASK = 0x7FFFFFFF

# --- the exp table -----------------------------------------------------------
#
# This is the *only* approximation in the forward pass, so its construction is
# pinned here and must be reproduced bit-for-bit by the emulator and the grid.
#
# Softmax is evaluated at ``z = logit - max(logits) <= 0``, so the table only
# ever needs ``exp`` on the non-positive half line, and only down to where Q12
# underflows: ``exp(-8) * 4096 = 1.4``, below which every value rounds into the
# last tick or two. The *whole* useful domain is therefore ``z`` in ``[-8, 0]``.
#
# Range: 32 entries at a stride of ``2**10 = 1024`` Q12 units = 1/4 of a unit
# cover exactly that domain, ``z`` in ``(-8.0, 0]``, from 4096 down to 2. Entry
# ``k`` stands for ``z = -k/4`` and holds ``round(4096 * exp(-k/4))``.
#
# Resolution: a quarter of a unit is coarse, so the low ``_EXP_INDEX_SHIFT`` bits
# of ``-z`` interpolate linearly between neighbouring entries. Interpolation
# costs one subtract, one multiply and two shifts per class — 40 integer ops a
# sample against a forward pass of ~1900 multiplies — and buys two things worth
# far more than that. It makes the table accurate enough that this model trains
# to the same validation accuracy as the same architecture in floating point
# (80% either way), and it makes the loss respond to a perturbation smaller than
# one table stride, without which the finite-difference gradient tests measure
# nothing: 95% of their probes come back as an exact zero difference.
#
# Why the stride is 1024 and not 128: a 128-unit stride puts all 32 entries
# inside ``z`` in ``(-1, 0]`` and clamps everything beyond, which caps how far
# apart two classes can be told apart. Measured, that costs 30 points of
# validation accuracy — 49% against 80% — because the clamp erases the ordering
# of every class more than one unit behind the leader and leaves a permanent
# floor under the gradient, so the model never settles. This is the one place
# where the design as briefed had to change; see the task report.
#
# Beyond entry 31 the value is clamped, with no interpolation: that region is
# ``exp(z) < 0.0005``, one Q12 tick, where there is nothing left to resolve.
# Clamping is monotone, so it never reorders classes.
_EXP_ENTRIES = 32
_EXP_INDEX_SHIFT = 10
_EXP_INDEX_MASK = (1 << _EXP_INDEX_SHIFT) - 1

# --- the log table -----------------------------------------------------------
#
# Used only by ``cross_entropy``, which reports loss; the gradient path never
# calls it (``dz = probs - onehot`` needs no log). It still has to be exact and
# integer, because ``EpochStat`` is compared across tiers.
#
# ``ln(x/SCALE)`` is split as ``e*ln2 + ln(m)`` with ``x = m * 2**e`` and
# ``m`` in [1, 2). The mantissa term reads a 33-entry table at 1/32 spacing and
# *interpolates linearly* between neighbours. The interpolation is not
# decoration: without it the loss would be quantised to 128 Q12 units, coarser
# than the difference a finite-difference probe produces, and the gradient tests
# would be measuring the table instead of the model.
_LN_MANT_BITS = 5
_LN_MANT_ENTRIES = 1 << _LN_MANT_BITS  # 32 intervals, 33 endpoints
_LN_MANT_SHIFT = SHIFT - _LN_MANT_BITS  # 7


@dataclass
class Params:
    """The learnable state. Flat lists, because the target has no 2D anything.

    ``conv_w[f * 9 + t]``, ``conv_b[f]``, ``dense_w[j * 18 + i]``, ``dense_b[j]``.
    """

    conv_w: list[int]
    conv_b: list[int]
    dense_w: list[int]
    dense_b: list[int]


@dataclass
class Grads:
    """Same shapes as :class:`Params`, so ``sgd_step`` is a straight zip."""

    conv_w: list[int]
    conv_b: list[int]
    dense_w: list[int]
    dense_b: list[int]


@dataclass
class Forward:
    """Everything backward needs, and nothing it does not.

    ``argmax`` holds indices *into* ``pre`` rather than positions within a pool
    window, so routing a pooled gradient back is one indexed store with no
    arithmetic — the cheapest possible thing on the target.
    """

    pre: list[int]
    act: list[int]
    pooled: list[int]
    argmax: list[int]
    logits: list[int]
    probs: list[int]


@dataclass(frozen=True)
class EpochStat:
    """One epoch's report: losses in Q12, accuracies in whole percent.

    Frozen so it is hashable and compares by value — a later tier asserts
    ``emulator_stats == reference_stats``.
    """

    train_loss: int
    train_acc: int
    val_loss: int
    val_acc: int


def exp_lut() -> list[int]:
    """32 Q12 entries: ``round(SCALE * exp(-k / 4))`` for ``k`` in 0..31.

    Entry ``k`` is ``exp(z)`` at ``z = -k/4``, i.e. at ``-z = k * 2**10`` in Q12,
    which is why the index is ``(-z) >> 10``. See the module's "exp table" note
    for the range and resolution this pins. Built from :func:`math.exp` once, at
    import, and then never touched — it is a constant table, the same one the
    generated grid will hold in ROM.
    """
    stride = (1 << _EXP_INDEX_SHIFT) / SCALE  # 1/4 of a unit
    return [round(SCALE * math.exp(-k * stride)) for k in range(_EXP_ENTRIES)]


_EXP = exp_lut()


def exp_q12(d: int) -> int:
    """``exp(-d / SCALE)`` in Q12, for ``d >= 0``. The softmax's whole approximation.

    Split out of :func:`forward` so it can be pinned entry by entry: this is the
    one function two later tiers must reproduce bit-for-bit, and it is four
    instructions where an off-by-one in the index, a dropped mask, or a swapped
    ``lo``/``hi`` would all still produce a plausible-looking softmax.

    ``d`` is ``max(logits) - logit``, so it is never negative and the shift is
    never applied to a negative value. Above entry 31 the value is clamped with no
    interpolation — that is ``exp(z) < 0.0005``, under one Q12 tick, where there is
    nothing left to resolve.
    """
    k = d >> _EXP_INDEX_SHIFT
    if k >= _EXP_ENTRIES - 1:
        return _EXP[_EXP_ENTRIES - 1]
    lo, hi = _EXP[k], _EXP[k + 1]
    return lo + (((hi - lo) * (d & _EXP_INDEX_MASK)) >> _EXP_INDEX_SHIFT)


_LN2 = round(SCALE * math.log(2))
_LN_MANT = [round(SCALE * math.log(1 + i / _LN_MANT_ENTRIES)) for i in range(_LN_MANT_ENTRIES + 1)]


def _ln_q12(x: int) -> int:
    """``ln(x / SCALE)`` in Q12, for ``x >= 1``. Negative when ``x < SCALE``."""
    if x < 1:
        raise ValueError("ln of a non-positive fixed-point value")
    exponent = x.bit_length() - 1 - SHIFT
    mant = x >> exponent if exponent >= 0 else x << -exponent  # in [SCALE, 2*SCALE)
    frac = mant - SCALE
    i = frac >> _LN_MANT_SHIFT
    rest = frac & ((1 << _LN_MANT_SHIFT) - 1)
    lo, hi = _LN_MANT[i], _LN_MANT[i + 1]
    return exponent * _LN2 + lo + (hi - lo) * rest // (1 << _LN_MANT_SHIFT)


def init_params(seed: int) -> Params:
    """Deterministic Q12 init from the classic glibc LCG.

    ``x = (x * 1103515245 + 12345) & 0x7FFFFFFF``, seeded with ``seed``, drawn in
    the order conv_w, dense_w. A uniform ``[-A, A]`` is taken as
    ``r % (2A + 1) - A``: symmetric, inclusive at both ends, and one modulo — the
    target has no rejection sampling to spare.

    Amplitudes are ``SCALE//8`` for conv and ``SCALE//16`` for dense — roughly a
    fan-in scaling, 9 taps against 18 features. Swept over four-fold ranges in
    both, accuracy moves by two or three points and never by more, so this is not
    a delicate choice; the exp table's range was the thing that mattered.

    Biases start at zero. Weight asymmetry is the only symmetry breaking needed,
    and a zero ``conv_b`` is the right start for what is morally BatchNorm's beta.
    """
    state = seed & _LCG_MASK

    def draw(amp: int) -> int:
        nonlocal state
        state = (state * _LCG_MUL + _LCG_ADD) & _LCG_MASK
        return state % (2 * amp + 1) - amp

    conv_w = [draw(_CONV_INIT) for _ in range(N_FILTERS * CONV_TAPS)]
    dense_w = [draw(_DENSE_INIT) for _ in range(N_CLASSES * N_FEATURES)]
    return Params(
        conv_w=conv_w,
        conv_b=[0] * N_FILTERS,
        dense_w=dense_w,
        dense_b=[0] * N_CLASSES,
    )


def to_q12(pixels: list[int]) -> list[int]:
    """Nibbles 0..15 -> Q12 in 0..SCALE. ``15`` is exactly 1.0."""
    return [p * SCALE // PIXEL_MAX for p in pixels]


def forward(p: Params, pixels: list[int]) -> Forward:
    """One sample, nibbles in, everything backward needs out."""
    x = to_q12(pixels)

    pre = [0] * (N_FILTERS * CONV_CELLS)
    for f in range(N_FILTERS):
        wbase = f * CONV_TAPS
        obase = f * CONV_CELLS
        bias = p.conv_b[f]
        for oy in range(CONV_OUT):
            for ox in range(CONV_OUT):
                acc = 0
                for ky in range(KERNEL):
                    xrow = (oy + ky) * md.IMG + ox
                    wrow = wbase + ky * KERNEL
                    for kx in range(KERNEL):
                        acc += x[xrow + kx] * p.conv_w[wrow + kx]
                pre[obase + oy * CONV_OUT + ox] = bias + (acc >> SHIFT)

    act = [v if v > 0 else 0 for v in pre]

    pooled: list[int] = []
    argmax: list[int] = []
    for f in range(N_FILTERS):
        obase = f * CONV_CELLS
        for py in range(POOL_OUT):
            for px in range(POOL_OUT):
                best = -1  # act is never negative, so the first cell always wins first
                best_at = -1
                for dy in range(POOL):
                    row = obase + (py * POOL + dy) * CONV_OUT + px * POOL
                    for dx in range(POOL):
                        cell = row + dx
                        if act[cell] > best:
                            best = act[cell]
                            best_at = cell
                pooled.append(best)
                argmax.append(best_at)

    logits: list[int] = []
    for j in range(N_CLASSES):
        wbase = j * N_FEATURES
        acc = 0
        for i in range(N_FEATURES):
            acc += pooled[i] * p.dense_w[wbase + i]
        logits.append(p.dense_b[j] + (acc >> SHIFT))

    top = max(logits)
    exps = [exp_q12(top - z) for z in logits]  # top - z >= 0 always
    total = sum(exps)
    probs = [e * SCALE // total for e in exps]

    return Forward(pre=pre, act=act, pooled=pooled, argmax=argmax, logits=logits, probs=probs)


def cross_entropy(probs: list[int], label: int) -> int:
    """``-ln(probs[label])`` in Q12, non-negative.

    Reporting only — the gradient is ``probs - onehot`` and never needs a log.
    A zero probability is clamped to one Q12 tick, capping the loss at
    ``-ln(1/4096) = 8.32`` rather than diverging.
    """
    p = probs[label]
    return -_ln_q12(p if p >= 1 else 1)


def backward(p: Params, pixels: list[int], f: Forward, label: int) -> Grads:
    """Gradients of :func:`cross_entropy` in Q12, same layout as :class:`Params`.

    Softmax and cross-entropy collapse into ``dz = probs - onehot(label)``, which
    is why no log and no division appear anywhere on this path. From there it is
    dense grads, the pooled gradient, a routed store through ``argmax``, the ReLU
    mask, and the conv grads. No conv input gradient — see the module docstring.
    """
    x = to_q12(pixels)

    dz = [f.probs[j] - (SCALE if j == label else 0) for j in range(N_CLASSES)]

    dense_b = list(dz)
    dense_w = [0] * (N_CLASSES * N_FEATURES)
    for j in range(N_CLASSES):
        wbase = j * N_FEATURES
        d = dz[j]
        for i in range(N_FEATURES):
            dense_w[wbase + i] = (d * f.pooled[i]) >> SHIFT

    dpooled = [0] * N_FEATURES
    for i in range(N_FEATURES):
        acc = 0
        for j in range(N_CLASSES):
            acc += dz[j] * p.dense_w[j * N_FEATURES + i]
        dpooled[i] = acc >> SHIFT

    # MaxPool is a router: only the winning cell saw the value, only it gets the
    # gradient. ReLU masks on the *pre*-activation sign; at exactly zero the
    # derivative is taken as zero, matching ``act = max(0, pre)`` being flat there.
    dpre = [0] * (N_FILTERS * CONV_CELLS)
    for i in range(N_FEATURES):
        cell = f.argmax[i]
        if f.pre[cell] > 0:
            dpre[cell] += dpooled[i]

    conv_b = [sum(dpre[fl * CONV_CELLS : (fl + 1) * CONV_CELLS]) for fl in range(N_FILTERS)]

    conv_w = [0] * (N_FILTERS * CONV_TAPS)
    for fl in range(N_FILTERS):
        obase = fl * CONV_CELLS
        for t in range(CONV_TAPS):
            ky, kx = divmod(t, KERNEL)
            acc = 0
            for oy in range(CONV_OUT):
                drow = obase + oy * CONV_OUT
                xrow = (oy + ky) * md.IMG + kx
                for ox in range(CONV_OUT):
                    d = dpre[drow + ox]
                    if d:
                        acc += d * x[xrow + ox]
            conv_w[fl * CONV_TAPS + t] = acc >> SHIFT

    return Grads(conv_w=conv_w, conv_b=conv_b, dense_w=dense_w, dense_b=dense_b)


def sgd_step(p: Params, g: Grads, lr_shift: int) -> None:
    """``w -= grad >> lr_shift``, in place. The learning rate is a shift count.

    A shift is the only division the target does cheaply, so the learning rate is
    ``2**-lr_shift`` and nothing else. ``>>`` floors, so a gradient smaller in
    magnitude than ``2**lr_shift`` nudges a weight up by one tick if it is
    negative and leaves it alone if it is positive. That asymmetry is the
    hardware's, and it is reproduced rather than corrected.
    """
    for arr, grad in (
        (p.conv_w, g.conv_w),
        (p.conv_b, g.conv_b),
        (p.dense_w, g.dense_w),
        (p.dense_b, g.dense_b),
    ):
        for k, d in enumerate(grad):
            arr[k] -= d >> lr_shift


def predict(f: Forward) -> int:
    """Argmax over ``logits``, first index winning ties.

    Over logits and not probs on purpose: the exp table clamps, so two very
    different logits can share a probability, and the tie-break would then decide
    the answer. Logits carry the full ordering and are what the grid compares.
    """
    best = f.logits[0]
    at = 0
    for j in range(1, N_CLASSES):
        if f.logits[j] > best:
            best = f.logits[j]
            at = j
    return at


def _unpack_all(words: list[int]) -> list[tuple[list[int], int]]:
    per = md.WORDS_PER_IMAGE
    return [md.unpack_image(words[i : i + per]) for i in range(0, len(words), per)]


def evaluate(p: Params, samples: list[tuple[list[int], int]]) -> tuple[int, int]:
    """``(mean loss in Q12, accuracy in whole percent)`` over ``samples``."""
    loss = 0
    correct = 0
    for pixels, label in samples:
        f = forward(p, pixels)
        loss += cross_entropy(f.probs, label)
        correct += predict(f) == label
    n = len(samples)
    return loss // n, correct * 100 // n


def train(params: Params, epochs: int, lr_shift: int) -> list[EpochStat]:
    """SGD at batch size 1 over the vendored split, in file order. Mutates ``params``.

    No shuffling: the target has no cheap permutation and a fixed order is one
    less thing to reproduce across tiers. Train loss and accuracy are measured
    *online*, on the forward pass the update is about to consume, because that
    pass is already paid for; validation is a separate read-only sweep.
    """
    train_words, val_words = md.load_packed()
    train_set = _unpack_all(train_words)
    val_set = _unpack_all(val_words)

    stats: list[EpochStat] = []
    for _ in range(epochs):
        loss = 0
        correct = 0
        for pixels, label in train_set:
            f = forward(params, pixels)
            loss += cross_entropy(f.probs, label)
            correct += predict(f) == label
            sgd_step(params, backward(params, pixels, f, label), lr_shift)
        n = len(train_set)
        val_loss, val_acc = evaluate(params, val_set)
        stats.append(
            EpochStat(
                train_loss=loss // n,
                train_acc=correct * 100 // n,
                val_loss=val_loss,
                val_acc=val_acc,
            )
        )
    return stats
