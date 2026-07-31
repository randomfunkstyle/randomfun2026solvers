"""Fit ticks = C + S*taken_seeks + W*classic_words to the measured sweep.

S is the flat cost of a taken seek, W the counted discard's cost per ring word.
Their ratio S/W is the *break-even skip*: the distance at which seeking and
discarding cost the same, i.e. what SEEK_THRESHOLD should be.
"""
import json, itertools

# (threshold, taken seeks, classic discard words) from econ.py's emulator trace
COUNTS = {
    64: (8401, 1414031), 128: (8372, 1417434), 192: (8291, 1431536),
    256: (8252, 1440489), 320: (8148, 1471782), 384: (8079, 1496100),
    448: (7965, 1543308), 512: (7864, 1591090), 600: (7699, 1681488),
    700: (7468, 1829190), 800: (7206, 2023874), 1000: (5956, 3202341),
    1200: (5652, 3536063), 1500: (5203, 4135371), 2000: (4544, 5235406),
}

TAPED = {64: 147399952, 128: 147102907, 256: 147213896, 448: 147172710,
         512: 147884620, 600: 147595591, 700: 147994317, 800: 149097221,
         1000: 153560537, 1500: 157284642, 2000: 161507241}

# men-v3: 64, 600 and 700 build but will not load — the packing hazard
# SEEK_TIER_LAYOUT records (a literal whose reverse reading overflows signed 64
# bits). Three of the eight alternatives to 256 are simply unrunnable.
MEN = {128: 88356367, 256: 88217704, 384: 88225590, 448: 88281024,
       512: 88263401, 800: 89385795, 1000: 93985704}


def fit(ticks, name, s_hint=None):
    xs = [(COUNTS[t][0], COUNTS[t][1], v) for t, v in sorted(ticks.items())]
    n = len(xs)
    # ordinary least squares on [1, seeks, words]
    import statistics
    A = [[1.0, float(a), float(b)] for a, b, _ in xs]
    y = [float(c) for _, _, c in xs]
    # normal equations, 3x3 solved by Gaussian elimination
    M = [[sum(A[k][i] * A[k][j] for k in range(n)) for j in range(3)]
         + [sum(A[k][i] * y[k] for k in range(n))] for i in range(3)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(M[r][i]))
        M[i], M[p] = M[p], M[i]
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                for c in range(i, 4):
                    M[r][c] -= f * M[i][c]
    C, S, W = (M[i][3] / M[i][i] for i in range(3))
    resid = [y[k] - (C + S * A[k][1] + W * A[k][2]) for k in range(n)]
    print(f"\n== {name}: ticks = C + S*seeks + W*words, {n} measured builds ==")
    print(f"  S = {S:,.0f} ticks a taken seek")
    print(f"  W = {W:.2f} ticks a classic discard word")
    print(f"  break-even skip = S/W = {S/W:,.0f} ring words   "
          f"(shipped SEEK_THRESHOLD = 256)")
    print(f"  max |residual| = {max(abs(r) for r in resid):,.0f} ticks "
          f"({100*max(abs(r) for r in resid)/statistics.mean(y):.2f}% of the run)")
    if s_hint:
        w2 = None
        # two-point estimate against the profiled S, most-separated pair
        (t1, t2) = min(ticks), max(ticks)
        ds = COUNTS[t1][0] - COUNTS[t2][0]
        dw = COUNTS[t2][1] - COUNTS[t1][1]
        dt = ticks[t2] - ticks[t1]
        w2 = (dt + s_hint * ds) / dw
        print(f"  with the PROFILED S = {s_hint:,.0f}: W = {w2:.2f}, "
              f"break-even = {s_hint/w2:,.0f} words")
    return S, W


fit(TAPED, "taped", s_hint=1394.1)
if MEN:
    fit(MEN, "men-v3", s_hint=1755.6)
