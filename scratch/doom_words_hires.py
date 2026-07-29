"""The same command-word census as `doom_words.py`, on the 128x96 program.

Only the counts a run/plotter primitive would move: words per frame by unit
opcode, and how many pixels the frame has to show for them.  IWAD-only, like
everything else in the hires family.

    uv run python scratch/doom_words_hires.py [--rounds 20]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

IWAD = Path(os.environ.get("DEADMAN3D_IWAD", "")) if os.environ.get("DEADMAN3D_IWAD") \
    else Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    args = ap.parse_args()

    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import emulator as emu_mod
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round
    from randomfun2026solvers.lm1.store import DoomUnit

    hires.install_wad(IWAD)
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")
    code_name = {v: k for k, v in DoomUnit.CODES.items()}

    words: list[int] = []
    real_send = emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND]

    def spy(e, operand):
        words.append(e.b)
        return real_send(e, operand)

    emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND] = spy
    try:
        cmds = list(hires.WALK[: args.rounds])
        em = Emulator(prog)
        res = em.run([Round(input=tuple(hires.input_words(cmds)))],
                     max_instructions=60_000_000)
    finally:
        emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND] = real_send

    print(f"reason={res.reason}  instructions={res.instructions:,}  "
          f"rounds={len(cmds)}")

    # The router prefixes every word with a selector, so the *unit* opcode is not
    # `w % 8` — the four-panel word is `(arg*8 + code)*8 + sel`.  Strip the
    # selector first; COMMIT goes to SEL["ALL"] and is the frame boundary.
    from randomfun2026solvers.lm1 import d3_router

    commit_sel = d3_router.SEL["ALL"]
    codes = [(w // 8) % 8 for w in words]
    bounds = [i for i, w in enumerate(words)
              if w % 8 == commit_sel and (w // 8) % 8 == DoomUnit.CODES["COMMIT"]]
    print(f"total words {len(words):,}, COMMITs (frames) {len(bounds)}")
    if len(bounds) < 2:
        print("not enough frames to split off the title")
        return 1
    play = codes[bounds[0] + 1:]
    n_frames = len(bounds) - 1
    by = Counter(code_name.get(c, f"?{c}") for c in play)
    total = sum(by.values())
    print(f"\n── 128x96 raycast frames: {total:,} words over {n_frames} frames "
          f"= {total / n_frames:.1f}/frame  (12,288 px/frame, 4 panels)")
    for cn, c in by.most_common():
        print(f"  {cn:<8}{c:>8,}{c / n_frames:>9.1f}/frame{100 * c / total:>8.1f}%")
    print(f"\n  title frame: {bounds[0] + 1:,} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
