"""The known facts the model has to reproduce, or it is not modelling §7.1.

Run this after any change to :mod:`bind` or :mod:`frontier`. It projects the
*captured* men-v3 geometry back to the pre-``ROM_TOUCH_DROP``-11 machine and
asserts the two things that were established by build:

* the pad floor at ``rom_touch_drop`` 9 is 2 -- 0 and 1 refuse, 2 and 3 bind;
* pad 2 there has exactly one tie-decided binding, ``'r'(22,163)`` at 30 against
  both ``rom`` and ``mem_resp``, won by ``mem_resp`` because its attach reads
  first. That tie is the whole reason pad 2 was ever legal.

The **taped** block below pins the same model against the other tier, which fails
for a different reason and so tests a different part of the encoding: taped's
floor is set by ``in``, not ``rom``, and its knobs are ``build_for`` keyword
arguments rather than registry entries.

Usage:  python regress.py /tmp/z3work/base.jsonl [taped-captures.jsonl ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import decide, geom, load, ties  # noqa: E402
from frontier import synth  # noqa: E402
from gate import decide_strict, slack_strict  # noqa: E402
from tapedfront import BASE as TBASE  # noqa: E402
from tapedfront import STORE_FORBIDDEN, base_of, project  # noqa: E402
from tapedfront import synth as tsynth  # noqa: E402

#: What a taped pad does at the shipped drop/squash, and why -- measured, from
#: real captures with the binding gate stubbed and then confirmed by live builds
#: that ran the 6-round frame gate green.
#:
#: pad 1 is the interesting one: it is an exact 22-22 tie between ``mem_resp``
#: and ``in``, and ``in``'s attach ``(9,138)`` reads before ``mem_resp``'s
#: ``(43,148)``, so the tie goes the *wrong* way and the pad is refused. The same
#: relaxation that made men-v3's pad 2 legal does nothing here.
TAPED_PADS = {
    -1: (False, "beaten"),
    0: (False, "beaten"),
    1: (False, "beaten"),
    2: (True, None),
    3: (True, None),
}
#: pad 2 at squash 13 binds for exactly these drops and no others.
TAPED_DROP_WINDOW = (12, 17)
#: Two squash rows buy one pad column, and the drop window travels with them.
TAPED_LADDER = {13: (2, (12, 17)), 12: (1, (14, 18)), 11: (1, (14, 19)),
                10: (0, (16, 20)), 9: (0, (16, 21))}

EXPECT = {0: False, 1: False, 2: True, 3: True}
EXPECT_TIE = (22, 163, "r", "mem_resp", "rom", 30, "won")


def main():
    recs = load(sys.argv[1] if len(sys.argv) > 1 else "/tmp/z3work/base.jsonl")
    base = next(r for r in recs if r["knobs"] == {"pad": 1})
    g0, t0 = geom(base)
    ok = True
    for pad, want in EXPECT.items():
        g, t = synth(g0, t0, dpad=pad - 1, ddrop=9 - 11)
        got = not decide(g, t)
        flag = "ok" if got == want else "MISMATCH"
        ok &= got == want
        print(f"  drop 9, pad {pad}: {'binds' if got else 'refuses':8} {flag}", flush=True)
    g, t = synth(g0, t0, dpad=1, ddrop=-2)
    got = ties(g, t)
    flag = "ok" if got == [EXPECT_TIE] else f"MISMATCH (wanted [{EXPECT_TIE}])"
    ok &= got == [EXPECT_TIE]
    print(f"  drop 9, pad 2 ties: {got}  {flag}", flush=True)
    if len(sys.argv) > 2:
        ok &= taped(sys.argv[2:])
    print("MODEL OK" if ok else "MODEL WRONG -- fix before trusting any new number",
          flush=True)
    return 0 if ok else 1


def taped(paths):
    """The taped tier's own facts. Same encoding, a different pipe doing the work."""
    recs = []
    for p in paths:
        recs += load(p)
    g0, t0 = geom(base_of(recs))
    neg = next((r for r in recs if r["knobs"] == {"pad": -1}), None)
    gneg, tneg = geom(neg) if neg else (None, None)
    ok = True
    print("\ntaped, shipped squash 13 / drop 16:", flush=True)
    for pad, (want, why) in TAPED_PADS.items():
        if pad < 0 and gneg is None:
            continue
        g, t = project(g0, t0, gneg, tneg, pad, TBASE["squash"], TBASE["drop"])
        bad = decide(g, t)
        got = not bad
        hit = "ok" if got == want else "MISMATCH"
        ok &= got == want
        note = ""
        if bad:
            v = bad[0]
            note = f"  '{v[2]}'@({v[0]},{v[1]}) wants {v[3]}, sees {v[4]}"
        print(f"  pad {pad:>2}: {'binds' if got else 'refuses':8} {hit}{note}", flush=True)
    # the shipped machine is not merely legal, it is tie-free with two to spare
    sl = slack_strict(*project(g0, t0, gneg, tneg, 2, 13, 16))
    hit = "ok" if not decide_strict(*project(g0, t0, gneg, tneg, 2, 13, 16)) \
        and sl[0][0] == 2 else "MISMATCH"
    ok &= hit == "ok"
    print(f"  shipped margin: strict slack {sl[0][0]}, no ties, tightest "
          f"{sl[0][3]!r}@({sl[0][1]},{sl[0][2]}) {sl[0][4]} vs {sl[0][5]}  {hit}",
          flush=True)
    # two squash rows buy one pad column, and the drop window travels with them
    for sq, (wpad, wwin) in TAPED_LADDER.items():
        got_pad = next((p for p in (-1, 0, 1, 2, 3)
                        if any(not decide(*project(g0, t0, gneg, tneg, p, sq, d))
                               for d in range(0, 45))), None)
        win = [d for d in range(0, 45)
               if not decide(*project(g0, t0, gneg, tneg, wpad, sq, d))]
        got_win = (win[0], win[-1]) if win else None
        hit = "ok" if (got_pad, got_win) == (wpad, wwin) else \
            f"MISMATCH (wanted pad {wpad} drops {wwin})"
        ok &= hit == "ok"
        print(f"  squash {sq:>2}: lowest pad {got_pad}, drops {got_win}  {hit}", flush=True)
    # the store gate is a three-row forbidden band, not an equation
    hit = "ok" if all(list(STORE_FORBIDDEN(s)) == [13 - s, 14 - s, 15 - s]
                      for s in (6, 10, 13)) else "MISMATCH"
    ok &= hit == "ok"
    print(f"  store_dy forbidden band = [13-squash, 15-squash]  {hit}", flush=True)
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
