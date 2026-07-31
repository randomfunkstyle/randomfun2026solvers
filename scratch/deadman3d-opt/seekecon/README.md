# Seek economics — the completed curve, and a caveat on the fit

men-v3 `SEEK_THRESHOLD` sweep, baseline 88,217,704, every runnable row
`fatal=None passed=True`:

| thr | ticks | Δ |
|---|---|---|
| 64 | — | **will not load** (signed-64 literal) |
| 128 | 88,356,367 | +0.157% |
| **256 (shipped)** | **88,217,704** | — |
| 384 | 88,225,590 | +0.009% |
| 448 | 88,281,024 | +0.072% |
| 512 | 88,263,401 | +0.052% |
| 600 / 700 | — | **will not load** |
| 800 | 89,385,795 | +1.325% |
| 1000 | 93,985,704 | +6.539% |
| 1500 | 96,497,752 | +9.386% |
| 2000 | — | **will not load** |

**4 of the 9 alternatives build but will not load**, and every one that loads is
worse. That strengthens the packing hazard rather than changing the verdict.

## Do not inherit the loose number

The free 3-parameter regression (`ticks = C + S·seeks + W·words`) is trustworthy
only near the shipped value. Adding the far points swings it wildly — S goes
823 → 2,434 → 1,026 t/seek as points are added, residual growing to 0.56% of the
run — because past ~800 the packing and box changes dominate and the linear term
stops describing the machine.

The **profile-anchored** estimate is stable: pin S to the directly measured
1,755.6 t/seek and solve only for W, and break-even comes out 317 / 346 / 348
words across every subset. So the number to quote is:

* men-v3 break-even **≈ 320–350** ring words
* taped **≈ 180–275**

and the free regression must not be used at the far end. "The fit gives 178 on
taped" is a weaker claim than the anchored 273.

This caveat exists because this task *inherited* a wrong number — "550 ticks per
taken seek", which came from dividing the seek pool by all 36,145 taken `JMPF`s
when only **8,252** are seeks. The real figure is 1,755.6. A loose number handed
forward costs a whole investigation.
