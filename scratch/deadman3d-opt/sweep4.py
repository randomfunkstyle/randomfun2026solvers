"""Taped tier: bank count x rom_rows frontier (build-only)."""
from randomfun2026solvers.lm1 import machine as M

plans = {
    "6 shaped (current)": (128, 128, 96, 99, 64, 84),
    "5 cold-merged": (128, 128, 195, 64, 85),
    "4 cold-merged": (256, 195, 64, 85),
    "3": (256, 195, 149),
}
for tag, plan in plans.items():
    M.TAPED_BANKS["deadman-3d"] = plan
    best = None
    for rr in range(58, 110, 2):
        M.ROM_ROWS["deadman-3d"] = rr
        try:
            m = M.build_for("deadman-3d", store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"{tag:22s} rom={rr:3d} FAIL {str(exc)[:60]}")
            continue
        w, h = m.width, m.height
        if best is None or max(w, h) < best[0]:
            best = (max(w, h), rr, w, h)
        print(f"{tag:22s} rom={rr:3d} {w:4d}x{h:<4d} max={max(w,h):4d} store_w={m.regions['tape'][2]}")
    print(f"  -> BEST {tag}: max={best[0]} at rom_rows={best[1]} ({best[2]}x{best[3]})\n")
M.ROM_ROWS["deadman-3d"] = 61
