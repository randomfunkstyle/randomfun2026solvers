"""Does the recorded ``squashed, no SEEK_TELEPORT`` machine exist?

``SQUASH_BAND``'s docstring prices the squash by differencing two rows:

    | no ``SEEK_TELEPORT``               | 649x495 | 192,066,009 | +1.534% |
    | **squashed**, no ``SEEK_TELEPORT`` | 649x485 | 191,600,156 | +1.288% |

A full squash at the shipped drop 22 does **not** build here — §7.1 refuses the
fetch ``r`` against the ``in`` touch. Rather than sweep 33 builds to find out
whether *some* drop rescues it, solve it: capture once and read off the exact
interval of drops that bind.

    python -m scratch.layout2.noseek
"""

from __future__ import annotations

from .bindsolve import feasible, violations
from .capture import capture


def report(label: str, **kw) -> None:
    cap = capture(rom_touch_drop=0, **kw)
    print(f"\n{label}")
    print(f"  build at drop 0: {cap.error or f'ok {cap.box}'}")
    if not cap.glyphs:
        print("  no capture — refused before check_bindings ran")
        return
    fs, bounds = feasible(cap.glyphs, cap.touches, moving="rom", axis="y")
    print(f"  misbinding glyphs at drop 0: {len(violations(cap.glyphs, cap.touches))}")
    print(f"  feasible drop = {fs}")
    up = [b for b in bounds if b.side == "hi"]
    lo = [b for b in bounds if b.side == "lo"]
    if lo:
        print(f"    lower: {lo[-1]}")
    if up:
        print(f"    upper: {up[-1]}")
    print(f"  shipped drop 22 feasible? {22 in fs}")


def main() -> int:
    report("no SEEK_TELEPORT, no squash (the '+1.534%' row)",
           seek_teleport=False, squash_band=False)
    report("no SEEK_TELEPORT, FULL squash (the '+1.288%' row)",
           seek_teleport=False, squash_band=True)
    report("SEEK_TELEPORT on, full squash", seek_teleport=True, squash_band=True)
    print("\nIf the full-squash row has an empty or 22-excluding interval, the "
          "recorded 649x485 machine\nwas built at some other drop — and the "
          "-0.243% differenced from it is not a squash measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
