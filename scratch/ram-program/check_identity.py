"""Is a freshly built machine byte-identical to the checked-in artifact?"""
import sys
from pathlib import Path

from randomfun2026solvers.lm1 import machine as M

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"
for slug, tier, name in [
    ("deadman-3d", "men-v3", "deadman-3d.man"),
    ("deadman-3d", "taped", "deadman-3d_taped.man"),
]:
    m = M.build_for(slug, store=tier)
    got = "\n".join(m.rows)
    want = EX.joinpath(name).read_text().rstrip("\n")
    w, h = max(len(r) for r in m.rows), len(m.rows)
    print(f"{name}: built {w}x{h}  identical={got == want}")
    if got != want:
        for i, (a, b) in enumerate(zip(got.split("\n"), want.split("\n"))):
            if a != b:
                print(f"  first diff row {i}")
                break
        sys.exit(1)
