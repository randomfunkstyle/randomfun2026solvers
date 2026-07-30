"""Known-answer validation: re-derive ``ROM_TOUCH_DROP``'s feasible interval.

``scratch/deadman3d-opt/rom_touch_probe.py`` brute-forces the drop against the real
``check_bindings`` over the twelve ``r`` glyphs that want ``rom``, one *whole build*
per value, and reports:

    drop 0..3   fail — rom 58..55 against mem_resp 54
    drop 4      fails — rom **ties** mem_resp at 54, and ties fail too
    drop 5..14  every one of the twelve binds

A solver that cannot re-derive that interval is not modelling §7.1 yet. This does
it analytically from a **single** capture: no build per candidate, and the answer
is an interval with both endpoints attributed, so it also reports the upper bound
the sweep never reached (it stopped at 14 because that was the range chosen, not
because 15 failed).

Two independent checks, because agreeing with a docstring is not validation:

1. **against the real checker, exhaustively** — for every ``t`` in a wide domain,
   the analytic verdict must equal ``check_bindings``' verdict on the shifted
   touches. Any disagreement is a bug in the model, and is reported as one.
2. **against the shipped registry** — ``ROM_TOUCH_DROP`` is 22 and the tick table
   records 26 flat and 32 unbuildable, so the interval must contain 22 and 26 and
   exclude 32.

    python -m scratch.layout2.romtouch
"""

from __future__ import annotations

from .bindsolve import Violation, feasible, violations
from .capture import Capture, capture, rom_wanting


def shifted(touches: dict, moving: str, axis: str, t: int) -> dict:
    """``touches`` with one touch moved ``t`` along one axis."""
    x, y = touches[moving]
    return {**touches, moving: (x, y + t) if axis == "y" else (x + t, y)}


def truth(cap: Capture, moving: str, axis: str, t: int) -> bool:
    """The **production** checker's verdict on the shifted placement."""
    from randomfun2026solvers.lm1.machine import MachineError, check_bindings

    try:
        check_bindings(cap.glyphs, shifted(cap.touches, moving, axis, t))
        return True
    except MachineError:
        return False


def cross_check(cap: Capture, moving: str = "rom", axis: str = "y",
                domain: tuple[int, int] = (-40, 80)) -> list[str]:
    """Exhaustively compare the analytic set against the real checker."""
    fs, _ = feasible(cap.glyphs, cap.touches, moving=moving, axis=axis)
    bad: list[str] = []
    for t in range(domain[0], domain[1] + 1):
        if (t in fs) != truth(cap, moving, axis, t):
            bad.append(f"t={t}: solver says {t in fs}, check_bindings says "
                       f"{truth(cap, moving, axis, t)}")
    return bad


def main() -> int:
    # Capture at pitch 1 / drop 0: the placement the withdrawal was recorded on, so
    # ``t`` is the drop itself rather than an offset from some other baseline.
    cap = capture(lane_pitch=1, rom_touch_drop=0)
    print(f"capture: config {cap.config}  ({len(cap.features)} features)")
    print(f"  build: {cap.error or f'ok {cap.box}'}")
    print(f"  glyphs={len(cap.glyphs)}  wanting rom={len(rom_wanting(cap))}"
          f"  touches={sorted(cap.touches)}")
    if not cap.glyphs:
        print("  NO CAPTURE — the build failed before check_bindings")
        return 1

    v: list[Violation] = violations(cap.glyphs, cap.touches)
    print(f"\nat drop 0, {len(v)} glyph(s) misbind:")
    for one in v[:6]:
        print(f"  {one}")

    fs, bounds = feasible(cap.glyphs, cap.touches, moving="rom", axis="y")
    print(f"\nfeasible drop = {fs}")
    lo = [b for b in bounds if b.side == "lo"]
    hi = [b for b in bounds if b.side == "hi"]
    if lo:
        print(f"  lower bound set by {lo[-1]}")
    if hi:
        print(f"  upper bound set by {hi[-1]}")

    print("\nknown answer (rom_touch_probe.py brute force over whole builds):")
    print("  0..3 fail / 4 ties / 5..14 bind")
    ok_lo = 5 in fs and 4 not in fs and 3 not in fs
    print(f"  interval starts at 5, 4 excluded (a tie): {'YES' if ok_lo else 'NO'}")
    ok_ship = 22 in fs and 26 in fs and 32 not in fs
    print(f"  contains shipped 22 and 26, excludes 32:  {'YES' if ok_ship else 'NO'}")

    bad = cross_check(cap)
    print(f"\nexhaustive cross-check against check_bindings over t=-40..80: "
          f"{'AGREES' if not bad else f'{len(bad)} DISAGREEMENTS'}")
    for line in bad[:8]:
        print(f"  {line}")
    return 0 if (ok_lo and ok_ship and not bad) else 1


if __name__ == "__main__":
    raise SystemExit(main())
