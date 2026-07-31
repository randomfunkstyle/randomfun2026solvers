"""§7.1 binding as an SMT problem, and the pad floor as a relaxation.

Encoding. Every glyph g has a fixed position (gx,gy) and an intended pipe w.
Every pipe p has an attachment (tx_p,ty_p) -- INTEGER VARIABLES, free to move.
For each rival q in the same direction pool:

    lex_lt( (d_w, ty_w, tx_w), (d_q, ty_q, tx_q) )      d_p = |tx_p-gx| + |ty_p-gy|

strict, because w must *win* the tie, not merely reach it. That is exactly the
engines' key: min(candidates, key=(distance, attach_y, attach_x)).

The point of making the touches free is that UNSAT then *proves* a floor: if no
placement of any pipe anywhere in the box binds at a given pad, no repositioning
we have not thought of can rescue it.
"""
import json, os, sys
from pathlib import Path
import z3

recs = json.loads((Path(os.environ["CLAUDE_JOB_DIR"]) / "tmp" / "pads.json").read_text())
INCOMING = {"rom", "in", "mem_resp", "Band.STREAM_RESP", "stream_resp"}

def pools(touches):
    inc = [n for n in touches if n in INCOMING]
    out = [n for n in touches if n not in INCOMING]
    return inc, out

def want_of(glyph, band):
    if band in ("Band.MEM", "mem"):
        return "mem_req" if glyph == "s" else "mem_resp"
    return {"Band.IN": "in", "Band.OUT": "out"}.get(band, band)

def lex_lt(a, b):
    """(a0,a1,a2) < (b0,b1,b2) lexicographically."""
    return z3.Or(a[0] < b[0],
                 z3.And(a[0] == b[0], z3.Or(a[1] < b[1],
                        z3.And(a[1] == b[1], a[2] < b[2]))))

def absv(e):
    return z3.If(e >= 0, e, -e)

def model_for(rec, free, box=None, pin=()):
    """`free`: pipe names whose attach may move. `box`: (x0,x1,y0,y1) domain."""
    touches, glyphs = rec["touches"], rec["glyphs"]
    inc, out = pools(touches)
    T = {}
    s = z3.Solver()
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
    ties = []
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
        ties.append((gx, gy, gl, w, pool))
    return s, T, ties

def shipped_ties(rec):
    """Bindings decided by an exact distance tie, on the fixed geometry."""
    touches, glyphs = rec["touches"], rec["glyphs"]
    inc, out = pools(touches)
    hits = []
    for gx, gy, gl, band in glyphs:
        w = want_of(gl, band)
        pool = inc if gl == "r" else out
        d = {n: abs(touches[n][0]-gx) + abs(touches[n][1]-gy) for n in pool}
        best = min(d.values())
        if sum(1 for v in d.values() if v == best) > 1:
            hits.append((gx, gy, gl, w, sorted(d.items(), key=lambda kv: kv[1])[:2]))
    return hits

by = {(r["pad"], r["in_west"]): r for r in recs}

print("=== VALIDATION: touches pinned at their shipped values ===")
for iw in (9, None):
    for pad in (0, 1, 2, 3):
        s, _, _ = model_for(by[(pad, iw)], free=())
        print(f"  in_west={str(iw):4}  pad {pad}: {s.check()}")

print("\n=== VALIDATION: ties on the shipped grid (pad 2, in_west 9) ===")
for gx, gy, gl, w, near in shipped_ties(by[(2, 9)]):
    print(f"  '{gl}' ({gx},{gy}) wants {w}: {near}")
print("  pad 2, in_west=None:")
for gx, gy, gl, w, near in shipped_ties(by[(2, None)]):
    print(f"  '{gl}' ({gx},{gy}) wants {w}: {near}")

# ── the relaxation: let every attach go anywhere on the grid ──────────────────
# UNSAT here is a proof: no repositioning of any pipe, legal or not, binds.
W, H = by[(2, 9)]["w"], by[(2, 9)]["h"]
BOX = (0, W - 1, 0, H - 1)
ALL = set(by[(2, 9)]["touches"])

print(f"\n=== RELAXATION: every attach free over the whole {W}x{H} grid ===")
for pad in (0, 1, 2):
    s, T, _ = model_for(by[(pad, 9)], free=ALL, box=BOX)
    r = s.check()
    print(f"  pad {pad}: {r}")
    if r == z3.sat:
        m = s.model()
        got = {n: (m[T[n][0]].as_long(), m[T[n][1]].as_long()) for n in ALL}
        for n in sorted(got):
            was = tuple(by[(pad, 9)]["touches"][n])
            mark = "  <-- moved" if got[n] != was else ""
            print(f"      {n:12} {was} -> {got[n]}{mark}")

print("\n=== which single pipe, freed alone, unlocks pad 1? ===")
for n in sorted(ALL):
    s, T, _ = model_for(by[(1, 9)], free={n}, box=BOX)
    r = s.check()
    if r == z3.sat:
        m = s.model()
        print(f"  {n:12} -> SAT at {(m[T[n][0]].as_long(), m[T[n][1]].as_long())} "
              f"(was {tuple(by[(1,9)]['touches'][n])})")
    else:
        print(f"  {n:12} -> {r}")
