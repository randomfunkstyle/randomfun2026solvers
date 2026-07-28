"""Regenerate every deadman-3d artifact family from the current registry."""
import shutil
from pathlib import Path

from randomfun2026solvers.lm1 import machine

EX = Path(__file__).resolve().parents[2] / "littleman" / "examples"


def emit(stem: str, m) -> None:
    text = "\n".join(m.rows) + "\n"
    (EX / f"{stem}.man").write_text(text, encoding="utf-8")
    m.debug_map().write_html(m.rows, EX / f"{stem}.debug.html")
    m.debug_map().write_json(EX / f"{stem}.debug.json")
    print(f"{stem}: {m.width}x{m.height} max={max(m.width, m.height)}")


emit("deadman-3d", machine.build_for("deadman-3d"))
emit("deadman-3d_taped", machine.build_for("deadman-3d", store="taped"))
emit("deadman-3d_trim", machine.build_for("deadman-3d", trim_dead=True))

for stem in ("man", "debug.html", "input.txt"):
    shutil.copyfile(EX / f"deadman-3d.{stem}", EX / f"deadman-3d_v2.{stem}")
for stem in ("man", "debug.html", "debug.json"):
    shutil.copyfile(EX / f"deadman-3d_taped.{stem}", EX / f"deadman-3d_m6_taped.{stem}")
print("copies refreshed")
