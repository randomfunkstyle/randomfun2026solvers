"""The **tie-free** rule: legality with a margin, not legality.

``bind.decide`` is the live gate. Since `c86ef95` ("the ties were ours, not the
machine's") ``machine.check_bindings`` is the engines' key verbatim --
``min(candidates, key=(distance, attach_y, attach_x))`` -- so an exact distance
tie is *decidable* and the intended pipe may win one. Both the shipped men-v3
machine (``'r'(21,163)``, ``rom`` and ``mem_resp`` both 31 away) and its
``mem_pad`` 2 depend on that.

The rule here is the **superseded** one, which refused a tie outright::

    if rivals[want] != best or sum(1 for d in rivals.values() if d == best) > 1:

It is kept because it is exactly the "one-cell margin" test the current
``check_bindings`` docstring warns about: a tie-decided binding flips to the
wrong pipe under any geometry move that reorders the two attaches, and the
failure mode is a wrong frame rather than an exception -- something only a long
frame gate catches. So a sweep reports both, and a candidate that binds only
under :func:`bind.decide` is legal *and* sitting on a knife edge, which is a
different recommendation from one that clears by two.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import pools, want_of  # noqa: E402


def dists(glyph, touches, pool):
    gx, gy = glyph[0], glyph[1]
    return {n: abs(touches[n][0] - gx) + abs(touches[n][1] - gy) for n in pool}


def decide_strict(glyphs, touches):
    """[] if ``machine.check_bindings`` would pass, else the violations it raises on.

    Each violation is ``(gx, gy, glyph, want, sorted_distances, reason)`` with
    reason in ``{"absent", "beaten", "tied"}``.
    """
    inc, out = pools(touches)
    bad = []
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        if w not in pool:
            bad.append((gx, gy, gl, w, [], "absent"))
            continue
        d = dists((gx, gy), touches, pool)
        best = min(d.values())
        order = sorted(d.items(), key=lambda kv: kv[1])
        if d[w] != best:
            bad.append((gx, gy, gl, w, order[:3], "beaten"))
        elif sum(1 for v in d.values() if v == best) > 1:
            bad.append((gx, gy, gl, w, order[:3], "tied"))
    return bad


def slack_strict(glyphs, touches):
    """Per-glyph strict slack: ``d(rival) - d(want)``; >=1 is safe, <=0 refuses.

    Sorted ascending, so ``[0]`` is the binding that is about to break.
    """
    inc, out = pools(touches)
    res = []
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        if w not in pool:
            continue
        d = dists((gx, gy), touches, pool)
        rivals = sorted((v, n) for n, v in d.items() if n != w)
        if not rivals:
            continue
        rd, rn = rivals[0]
        res.append((rd - d[w], gx, gy, gl, w, rn, d[w], rd))
    res.sort()
    return res
