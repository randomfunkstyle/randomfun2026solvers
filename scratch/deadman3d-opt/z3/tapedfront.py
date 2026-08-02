"""taped's joint (mem_pad x rom_touch_drop x squash_band x in_west) frontier.

Four synthesis rules, each checked against a real taped capture before use
(``--validate``). Three are men-v3's; one is new, and one differs in what it is
*for*:

  R1  ``mem_pad`` d: the 22 MEM glyphs move d columns east, nothing else. Exact
      at pad 0 and above. **It breaks at pad -1 exactly as it broke on men-v3**,
      and worse: ``_flat_lane`` advances with ``while x < target``, so a lane
      already east of the target ignores it, and the captured pad -1, -2, -3 and
      -4 bands are **byte-identical** -- columns ``18..24, 26`` in all four. Only
      some lanes gain the column; the band's binding-critical ``r`` stays at
      x=20, the same column pad 0 puts it in. So pad 0 is the last uniform shift
      and everything below it is one ragged geometry wearing four names. The
      sweep therefore uses the *captured* pad -1 record, never a synthesised one.
  R2  ``rom_touch_drop`` d: ``touches["rom"]`` moves d rows south, nothing else.
  R3  ``squash_band`` d: every glyph AND every touch **except ``in``** moves d
      rows north, and the machine loses d rows of height. ``in`` is pinned to
      ``(18 - in_west, CY - 1)``.
  R4  ``in_west`` d: ``touches["in"]`` alone moves to x = 18 - d. 9 is the
      ceiling (10 raises "puts the input pipe off the CPU north wall"), so the
      shipped 9 already has ``in`` as far west -- as far from the memory band --
      as the wall allows. There is nothing left on this knob.

**Why taped differs from men-v3 in kind, not degree.** On men-v3 the westmost MEM
``r`` is squeezed between ``rom`` from below and ``in`` from above, so the pad
floor moves with ``ROM_TOUCH_DROP`` and ticks follow ``eff = drop - squash``. On
taped ``rom`` is at ``(7, 176)``, twenty rows south and fifteen columns west of
the band, and it is nowhere near the binding: **every** tight binding on taped is
against ``in``, at strict slack 2. Sliding ``rom`` therefore does nothing to the
pad floor, which is what the previous run measured and could not explain.

What *does* move it is R3. ``in`` does not move with the squash and every MEM
glyph does, and every MEM glyph is **south** of ``in``, so lowering the squash
walks the whole band away from the one rival that threatens it while leaving its
distance to ``mem_resp`` exactly as it was. One squash row = one row of strict
slack = half a pad column, and it costs one row of height.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, margins  # noqa: E402
from gate import decide_strict, slack_strict  # noqa: E402

#: The shipped taped knobs -- the capture every delta below is taken from.
BASE = {"pad": 2, "drop": 16, "squash": 13, "in_west": 9, "store_dy": -1}

#: ``touches["in"]`` x as a function of ``in_west``: 9 -> 9, 8 -> 10 (measured).
IN_X = lambda w: 18 - w  # noqa: E731

#: The store block's own level, and the gate that fires *before* ``check_bindings``.
#: The recorded form is one equation, ``store_dy = 12 - squash_band``. The
#: measured form is **two levels**, and the second one is worth -0.940%::
#:
#:     store_dy <= 12 - squash_band          # a run, top value shipped
#:     store_dy == 16 - squash_band          # a second level, exactly one row wide
#:
#: Mapped by capture at three squashes, and it is the same shape at all three:
#:
#: | squash | legal run | forbidden | 2nd level | forbidden again |
#: |---|---|---|---|---|
#: | 13 | -5, -2, **-1** | 0, 1, 2 | **3** | 4, 6, 10 |
#: | 10 | -1, 0, 1, **2** | 3, 4, 5 | **6** | 7, 10 |
#: |  6 | **6** | 7, 8, 9 | **10** | -- |
#:
#: The failures are collisions, not bindings -- ``collision at (47, 148): 'v' vs
#: '<'``, ``collision at (45, 151): '+' vs '-'`` -- in the adapter's request leg's
#: own columns. That is the "the store's request wall is on row N and the
#: adapter's request leaves on row M: a straight leg needs them level" gate, and
#: the reason it reads as an equation is that a straight leg has exactly two rows
#: it can be level on. Nobody had tried the second.
#:
#: ``store_dy`` moves **no glyph and no touch** (squash 12 at dy 0 and at dy -1
#: capture byte-identically), so §7.1 cannot see it and it was filed as a
#: feasibility detail. It is not: on the shipped machine, ``-1 -> 3`` alone is
#: 29,442,688 -> 29,165,928 steady ticks, **-0.940%**, with nothing else moved,
#: and -5 is +1.046% the other way. Deeper is worse, monotonically, and 16-squash
#: is the highest row the leg can reach.
STORE_DY = lambda squash: 12 - squash  # noqa: E731
STORE_DY_HIGH = lambda squash: 16 - squash  # noqa: E731
STORE_FORBIDDEN = lambda squash: range(13 - squash, 16 - squash)  # noqa: E731


def store_dy_ok(squash, dy):
    return dy <= 12 - squash or dy == 16 - squash


def synth(g0, t0, dpad=0, ddrop=0, dsquash=0, din_west=0):
    """Project the base capture to another knob setting. Validated, not modelled."""
    g = [(x + (dpad if b == "mem" else 0), y - dsquash, gl, b) for x, y, gl, b in g0]
    t = {n: ((x, y - dsquash) if n != "in" else (x, y)) for n, (x, y) in t0.items()}
    t["rom"] = (t["rom"][0], t["rom"][1] + ddrop)
    if din_west:
        t["in"] = (IN_X(BASE["in_west"] + din_west), t["in"][1])
    return g, t


def base_of(recs):
    for r in recs:
        kn = r["knobs"]
        if "glyphs" in r and all(kn.get(k, v) == v for k, v in BASE.items()):
            return r
    raise SystemExit("no baseline capture (shipped knobs) in the given files")


def validate(recs):
    b = base_of(recs)
    g0, t0 = geom(b)
    neg = next((r for r in recs if r["knobs"] == {"pad": -1}), None)
    gneg, tneg = geom(neg) if neg else (None, None)
    ok = True
    print("=== synthesis rules vs real taped captures ===", flush=True)
    for r in recs:
        if "glyphs" not in r:
            continue
        kn = r["knobs"]
        if "ranks" in kn:
            continue
        pad = kn.get("pad", BASE["pad"])
        # Below pad 0 R1 does not hold and no synthesis is claimed: the ragged
        # band is projected off its own capture, which tests R2/R3/R4 on it.
        gb, tb, dpad = (g0, t0, pad - BASE["pad"]) if pad >= 0 else (gneg, tneg, 0)
        if gb is None:
            continue
        g, t = synth(
            gb, tb,
            dpad=dpad,
            ddrop=kn.get("drop", BASE["drop"]) - BASE["drop"],
            dsquash=kn.get("squash", BASE["squash"]) - BASE["squash"],
            din_west=kn.get("in_west", BASE["in_west"]) - BASE["in_west"],
        )
        gr, tr = geom(r)
        tag = "" if pad >= 0 else " [ragged base]"
        gm = "glyphs OK" if g == list(gr) else "GLYPH MISMATCH"
        bad = [(n, t[n], tr.get(n)) for n in t if t[n] != tr.get(n)]
        tm = "touches OK" if not bad else f"TOUCH MISMATCH {bad}"
        if "MISMATCH" in gm + tm:
            ok = False
        print(f"  {str(kn):46}{tag} {gm}, {tm}", flush=True)
    print(f"  ==> taped synthesis is {'EXACT' if ok else 'WRONG -- do not trust the sweep'}",
          flush=True)
    return g0, t0, ok


def project(g0, t0, gneg, tneg, pad, squash, drop):
    """The geometry at ``(pad, squash, drop)``, from whichever capture is valid.

    Pad 0 and above: synthesised off the shipped capture (R1 exact there).
    Pad -1 and below: the *captured* ragged band, which no pad below -1 changes.
    """
    if pad >= 0:
        return synth(g0, t0, dpad=pad - BASE["pad"], ddrop=drop - BASE["drop"],
                     dsquash=squash - BASE["squash"])
    return synth(gneg, tneg, ddrop=drop - BASE["drop"],
                 dsquash=squash - BASE["squash"])


def frontier(g0, t0, gneg, tneg, pads=(-1, 0, 1, 2, 3), squashes=range(0, 21),
             drops=range(0, 45)):
    """Lowest legal pad per squash, under the live gate, with the drop window.

    ``binds`` is ``bind.decide`` -- ``machine.check_bindings`` verbatim since
    `c86ef95`. ``clear`` is the same with ties refused (``gate.decide_strict``):
    a row that binds but is not clear is legal on a reading-order tie and one
    geometry nudge from painting a wrong frame.
    """
    print("\n=== taped joint frontier: lowest mem_pad per squash ===", flush=True)
    print("   mem_x is the memory band's westmost column (19 + pad at pad >= 0);"
          " every column is walked twice per MEM instruction.", flush=True)
    print(f"   {'squash':>6} {'h':>4} {'st_dy':>6} {'pad':>4} {'mem_x':>6} "
          f"{'drops':>9} {'tie?':>5}  tightest binding", flush=True)
    rows = []
    for sq in squashes:
        got = None
        for pad in pads:
            win = [d for d in drops
                   if not decide(*project(g0, t0, gneg, tneg, pad, sq, d))]
            if win:
                clear = [d for d in win
                         if not decide_strict(*project(g0, t0, gneg, tneg, pad, sq, d))]
                got = (pad, win, clear)
                break
        rows.append((sq, got))
    for sq, got in rows:
        h = 398 + (BASE["squash"] - sq)
        if got is None:
            print(f"   {sq:>6} {h:>4} {STORE_DY(sq):>6} {'--':>4} {'--':>6} "
                  f"{'--':>9} {'--':>5}  nothing in pads {pads} binds at any drop",
                  flush=True)
            continue
        pad, win, clear = got
        d = clear[len(clear) // 2] if clear else win[len(win) // 2]
        sl = slack_strict(*project(g0, t0, gneg, tneg, pad, sq, d))[0]
        mx = 19 + pad if pad >= 0 else 18
        print(f"   {sq:>6} {h:>4} {STORE_DY(sq):>6} {pad:>4} {mx:>6} "
              f"{f'{win[0]}..{win[-1]}':>9} {('no' if clear else 'TIE'):>5}  "
              f"slack {sl[0]} {sl[3]!r}@({sl[1]},{sl[2]}) {sl[4]} vs {sl[5]}",
              flush=True)
    return rows


def drop_role(g0, t0):
    """What the drop actually does on taped: it holds `rom` off the sinking band."""
    print("\n=== the drop's role on taped ===", flush=True)
    for sq, pad in ((13, 2), (11, 1), (9, 0), (7, 0)):
        win = [d for d in range(0, 45)
               if not decide(*synth(g0, t0, dpad=pad - BASE["pad"],
                                    ddrop=d - BASE["drop"],
                                    dsquash=sq - BASE["squash"]))]
        print(f"   squash {sq:>2} pad {pad:>2}: drop window "
              f"{win[0] if win else '--'}..{win[-1] if win else '--'} "
              f"(eff = drop - squash {win[0] - sq if win else '--'}"
              f"..{win[-1] - sq if win else '--'})", flush=True)
    print("   Lowering the squash walks the band SOUTH, toward `rom` at (7,176) as"
          " well as away from `in`; the drop is what pushes `rom` back out of the"
          " way. That is why the window travels with the squash.", flush=True)


if __name__ == "__main__":
    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    g0, t0, ok = validate(recs)
    neg = next(r for r in recs if r["knobs"] == {"pad": -1})
    gneg, tneg = geom(neg)
    drop_role(g0, t0)
    frontier(g0, t0, gneg, tneg)
