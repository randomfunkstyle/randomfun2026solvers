"""§7.1 pipe binding as an SMT problem, plus a *decider* for captured geometry.

Encoding. Every glyph g has a fixed position (gx,gy) and an intended pipe w.
Every pipe p has an attachment (tx_p,ty_p). For each rival q in the same
direction pool:

    lex_lt( (d_w, ty_w, tx_w), (d_q, ty_q, tx_q) )      d_p = |tx_p-gx| + |ty_p-gy|

strict, because w must *win* the tie, not merely reach it. That is exactly the
engines' key: ``min(candidates, key=(distance, attach_y, attach_x))``
(``machine.check_bindings``, ``fast_littleman._bind_pipe_ops``).

Making the touches free variables turns UNSAT into a *proof*: if no placement of
any pipe anywhere in the box binds at a given pad, no repositioning we have not
thought of can rescue it.

``decide()`` is the cheap path -- pure arithmetic on a captured geometry, no
solver -- and is what the pad sweeps use.
"""

from __future__ import annotations

import json
from pathlib import Path

INCOMING = {"rom", "in", "mem_resp", "Band.STREAM_RESP", "stream_resp"}


def pools(touches):
    inc = [n for n in touches if n in INCOMING]
    out = [n for n in touches if n not in INCOMING]
    return inc, out


def want_of(glyph, band):
    if band in ("Band.MEM", "mem"):
        return "mem_req" if glyph == "s" else "mem_resp"
    return {"Band.IN": "in", "Band.OUT": "out"}.get(band, band)


# ── the exact engine rule, on fixed geometry ─────────────────────────────────
def decide(glyphs, touches):
    """Return [] if every glyph binds its band, else the list of violations.

    Verbatim ``check_bindings``: nearest attach, Manhattan, ties by (y, x).
    """
    inc, out = pools(touches)
    bad = []
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        if w not in pool:
            bad.append((gx, gy, gl, w, "not in pool"))
            continue
        winner = min(
            pool,
            key=lambda n: (
                abs(touches[n][0] - gx) + abs(touches[n][1] - gy),
                touches[n][1],
                touches[n][0],
            ),
        )
        if winner != w:
            d = {n: abs(touches[n][0] - gx) + abs(touches[n][1] - gy) for n in pool}
            bad.append((gx, gy, gl, w, sorted(d.items(), key=lambda kv: kv[1])[:3]))
    return bad


def margins(glyphs, touches):
    """Per-glyph slack: how much closer a rival would have to get to steal it.

    0 means the binding is decided by the reading-order tie -- a one-cell margin,
    and the failure mode is a wrong frame rather than an exception.
    """
    inc, out = pools(touches)
    res = []
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        d = {n: abs(touches[n][0] - gx) + abs(touches[n][1] - gy) for n in pool}
        rivals = sorted((v, n) for n, v in d.items() if n != w)
        if not rivals:
            continue
        rd, rn = rivals[0]
        slack = rd - d[w]
        if slack == 0:
            # decided by reading order; a tie the intended pipe wins is legal
            slack = 0 if (touches[w][1], touches[w][0]) < (touches[rn][1], touches[rn][0]) else -1
        res.append((slack, gx, gy, gl, w, rn, d[w], rd))
    res.sort()
    return res


def ties(glyphs, touches):
    """Bindings decided by an exact distance tie (slack 0 either way)."""
    out = []
    for slack, gx, gy, gl, w, rn, dw, rd in margins(glyphs, touches):
        if dw == rd:
            out.append((gx, gy, gl, w, rn, dw, "won" if slack == 0 else "LOST"))
    return out


# ── the relaxation ───────────────────────────────────────────────────────────
def model_for(glyphs, touches, free, box=None, pin=()):
    """`free`: pipe names whose attach may move. `box`: (x0,x1,y0,y1)."""
    import z3

    inc, out = pools(touches)
    T = {}
    s = z3.Solver()

    def absv(e):
        return z3.If(e >= 0, e, -e)

    def lex_lt(a, b):
        return z3.Or(
            a[0] < b[0],
            z3.And(a[0] == b[0], z3.Or(a[1] < b[1], z3.And(a[1] == b[1], a[2] < b[2]))),
        )

    for n, (px, py) in touches.items():
        if n in free:
            tx, ty = z3.Int(f"tx_{n}"), z3.Int(f"ty_{n}")
            if box:
                s.add(tx >= box[0], tx <= box[1], ty >= box[2], ty <= box[3])
            T[n] = (tx, ty)
        else:
            T[n] = (z3.IntVal(px), z3.IntVal(py))
    for n, v in pin:
        s.add(T[n][0] == v[0], T[n][1] == v[1])
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        if w not in pool:
            raise SystemExit(f"{gl} at {gx},{gy} wants {w}, pool {pool}")

        def key(n):
            d = absv(T[n][0] - gx) + absv(T[n][1] - gy)
            return (d, T[n][1], T[n][0])

        for q in pool:
            if q != w:
                s.add(lex_lt(key(w), key(q)))
    return s, T


# ── geometry i/o ─────────────────────────────────────────────────────────────
def load(path):
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


def geom(rec):
    return [tuple(g) for g in rec["glyphs"]], {k: tuple(v) for k, v in rec["touches"].items()}
