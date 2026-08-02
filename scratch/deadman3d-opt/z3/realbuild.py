"""Build with ``check_bindings`` LIVE -- the builder's own verdict, not the model's.

``capture.py`` stubs the gate so a doomed pad still yields geometry. This does
the opposite: it lets §7.1 refuse, so a pass here is evidence rather than a
witness. Prints ``mem_x`` (``lane_x0 + max(prefixes) + mem_pad``), which is the
quantity every MEM instruction actually walks -- the pad on its own is not.

Usage:  python realbuild.py '<json list of knob dicts>'
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import KEY, SLUG, TIER, apply_knobs, restore, setup  # noqa: E402


def main():
    trials = json.loads(sys.argv[1])
    _, _, M, prog = setup()
    print(f"setup done, {len(trials)} trials", flush=True)
    for i, kn in enumerate(trials):
        saved = apply_knobs(M, kn)
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store=TIER)
            # the CPU's west wall is CX, and touches["rom"] sits at CX-1
            cx = m.regions.get("cpu", (None,))[0]
            mem_glyph_x = None
            print(
                f"[{i + 1}/{len(trials)}] {kn} -> BUILDS {m.width}x{m.height} "
                f"pad={m.mem_pad} cx={cx} ({time.time() - t0:.0f}s)",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"[{i + 1}/{len(trials)}] {kn} -> REFUSED {type(e).__name__}: {e} "
                f"({time.time() - t0:.0f}s)",
                flush=True,
            )
        finally:
            restore(M, saved)


if __name__ == "__main__":
    main()
