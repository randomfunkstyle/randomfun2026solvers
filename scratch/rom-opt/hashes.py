"""Hash every checked-in example against a fresh build_for, plus the .asm."""
import hashlib
import sys
from pathlib import Path

from randomfun2026solvers.lm1 import machine as M

EX = Path("littleman/examples")


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


rows = []
for man in sorted(EX.glob("*.man")):
    rows.append((man.name, h(man.read_text())))
for name, digest in rows:
    print(f"{digest}  {name}")
print(f"{h((EX / 'deadman-3d.input.txt').read_text())}  deadman-3d.input.txt")

print("\n-- fresh builds vs checked-in --")
for stem, kw in (
    ("deadman-3d", {}),
    ("deadman-3d_taped", {"store": "taped"}),
    ("deadman-3d_trim", {"trim_dead": True}),
):
    m = M.build_for("deadman-3d", **kw)
    live = "\n".join(m.rows) + "\n"
    disk = (EX / f"{stem}.man").read_text()
    print(f"{stem:22s} {m.width}x{m.height}  live={h(live)} disk={h(disk)} "
          f"{'MATCH' if live == disk else 'DIFFERS'}")
