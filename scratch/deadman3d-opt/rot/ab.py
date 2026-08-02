"""Same-moment A/B over the 21-round hires tour, control first and last.

usage: ab.py "label=python;python" ...   (``M``/``MT``/``T``/``K``/``SLUG`` in scope)

The empty control is prepended and appended; both must reproduce the same tick or
the comparison is void, and at 21 rounds it must be :data:`BASE`.
"""
from __future__ import annotations

import copy
import os
import sys
import time

sys.path.insert(0, os.environ["CLAUDE_JOB_DIR"] + "/tmp/h")
from common import SLUG, setup, tour  # noqa: E402

INSTR = 880_332
BASE = 87_431_352


def main() -> None:
    rounds = int(os.environ.get("ROUNDS", "21"))
    _d3, hires, M, prog = setup()
    assert "worktrees/compactor" in M.__file__, M.__file__
    from randomfun2026solvers import memory_tape as T
    from randomfun2026solvers import memory_taped as MT
    from randomfun2026solvers.fast_littleman import FastLittleman

    inp, frames = tour(hires, rounds)
    K = (SLUG, "taped")

    revert = os.environ.get("REVERT", "M.TAPED_ROTATE_BANKS.pop(K, None)")
    specs = [("control(HEAD)", revert), ("shipped", "")]
    for a in sys.argv[1:]:
        label, _, code = a.partition("=")
        specs.append((label, code))
    specs.append(("control2(HEAD)", revert))

    ns = {"M": M, "MT": MT, "T": T, "K": K, "SLUG": SLUG}
    mods = {"M": M, "MT": MT, "T": T}
    saved = {
        (mn, a): getattr(mod, a)
        for mn, mod in mods.items()
        for a in dir(mod)
        if a.isupper()
        and isinstance(getattr(mod, a), (int, bool, str, set, dict, tuple, list))
    }

    def restore() -> None:
        for (mn, a), v in saved.items():
            setattr(mods[mn], a, copy.deepcopy(v))

    out: list[tuple[str, int]] = []
    for label, code in specs:
        restore()
        for stmt in [x for x in code.split(";") if x.strip()]:
            exec(stmt, ns, ns)  # noqa: S102
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="taped")
        except Exception as e:  # noqa: BLE001
            print(f"RESULT {label:22s} BUILD-FAIL {type(e).__name__}: {e}", flush=True)
            continue
        bt = time.time() - t0
        t0 = time.time()
        res = FastLittleman("\n".join(m.rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000
        )
        tk = res.frame_ticks[-1]
        out.append((label, tk))
        d = 100.0 * (tk - out[0][1]) / out[0][1]
        print(
            f"RESULT {label:22s} {m.width}x{m.height} ticks={tk:,} "
            f"({d:+.3f}%) t/instr={tk / INSTR:.4f} passed={res.passed} "
            f"fatal={res.fatal} build={bt:.0f}s run={time.time() - t0:.0f}s",
            flush=True,
        )
    restore()
    if out and out[0][1] != out[-1][1]:
        print(f"!! CONTROL DRIFT {out[0][1]:,} vs {out[-1][1]:,}", flush=True)
    elif out and rounds == 21 and out[0][1] != BASE:
        print(f"!! CONTROL != {BASE:,}: {out[0][1]:,}", flush=True)


if __name__ == "__main__":
    main()
