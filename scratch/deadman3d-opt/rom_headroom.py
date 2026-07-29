"""How many more ROM words can the taped machine take before it breaks 300?

The stepY-split DDA costs ~576 words, and `tests/test_deadman3d.py` pins the
taped machine under a 300-column ceiling. Rather than build the split and find
out, fake the growth: raise `DDA_UNROLL` (which adds whole copies of exactly the
block that would grow) and read the box off `build_for`.

    uv run python scratch/deadman3d-opt/rom_headroom.py [unroll ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402

base = d3.DDA_UNROLL
for unroll in (int(a) for a in (sys.argv[1:] or ("16", "18", "20", "22", "24"))):
    d3.DDA_UNROLL = unroll
    try:
        prog = d3.taped_program()
        m = machine.build_for("deadman-3d", store="taped")
        print(f"unroll {unroll:>3}  P={prog.P:>6}  {m.width}x{m.height}"
              f"  max={max(m.width, m.height)}"
              f"  {'OK' if m.width <= 300 else 'OVER THE 300 CEILING'}")
    except Exception as exc:  # noqa: BLE001
        print(f"unroll {unroll:>3}  build failed: {type(exc).__name__}: {exc}")
d3.DDA_UNROLL = base
