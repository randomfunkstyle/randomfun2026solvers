"""Rank every taped variant that was actually **built and run**, against 159.46.

Everything here cleared the live ``check_bindings`` and painted ``passed=True``
frames on a 6-round native gate, so nothing in this table is a witness. The
metric is the steady walk ``frame_ticks[-1] - frame_ticks[0]`` (five rounds,
boot excluded) -- the same one ``hires_bankrun.py`` uses -- and the t/instr
column applies that percentage to the shipped 21-round 159.46.

    python tapedrank.py /tmp/z3work/price.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_KNOBS: dict = {}
BASE_STEADY = 29_442_688      # shipped: pad 2, squash 13, drop 16, store_dy -1
BASE_TPI = 159.46
BASE_TOUR = 140_379_566


def key(r):
    k = r["knobs"]
    return (k.get("pad", 2), k.get("squash", 13), k.get("drop", 16),
            k.get("store_dy", -1))


def main():
    rows = {}
    for p in sys.argv[1:] or ["/tmp/z3work/price.jsonl"]:
        for ln in Path(p).read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("steady") and r.get("passed") and r["steady"] > 20_000_000:
                rows[key(r)] = r          # 3-round probes filtered out by size
    ranked = sorted(rows.values(), key=lambda r: r["steady"])
    print(f"{len(ranked)} built + frame-gated variants, best first. "
          f"Baseline = shipped, {BASE_STEADY:,} steady ticks / {BASE_TPI} t/instr\n")
    print(f"  {'pad':>4} {'sq':>3} {'drop':>4} {'st_dy':>5} {'br':>4} {'eff':>4} "
          f"{'romcap':>6} {'box':>9} {'steady':>11} {'delta':>9} {'%':>8} {'t/instr':>8}")
    print("  " + "-" * 92)
    for r in ranked:
        k = r["knobs"]
        pad, sq = k.get("pad", 2), k.get("squash", 13)
        drop, dy = k.get("drop", 16), k.get("store_dy", -1)
        br = "HIGH" if dy > 12 - sq else "low"
        d = r["steady"] - BASE_STEADY
        pct = 100.0 * d / BASE_STEADY
        print(f"  {pad:>4} {sq:>3} {drop:>4} {dy:>5} {br:>4} {drop - sq:>4} "
              f"{r['rom_capacity']:>6} {r['w']}x{r['h']:<4} {r['steady']:>11,} "
              f"{d:>+9,} {pct:>+8.4f} {BASE_TPI * (1 + pct / 100):>8.3f}")
    best = ranked[0]
    pct = 100.0 * (best["steady"] - BASE_STEADY) / BASE_STEADY
    print(f"\n  best: {best['knobs']}  {pct:+.4f}%  ->  "
          f"{BASE_TPI * (1 + pct / 100):.3f} t/instr, "
          f"~{BASE_TOUR * (1 + pct / 100):,.0f} tour ticks (extrapolated)")


if __name__ == "__main__":
    main()
