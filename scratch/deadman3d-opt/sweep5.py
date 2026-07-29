"""Taped tier joint frontier: bank plan x store dx x rom_rows."""
from randomfun2026solvers.lm1 import machine as M

best = {}
for plan in [(256, 195, 64, 85), (128, 128, 195, 64, 85), (256, 195, 84, 65)]:
    M.TAPED_BANKS["deadman-3d"] = plan
    for dx in (0, -8, -16, -24, -32, -40):
        M.MEM_PLACE["deadman-3d"] = ((0, 0), (dx, 0))
        ok = None
        for rr in range(60, 130, 2):
            M.ROM_ROWS["deadman-3d"] = rr
            try:
                m = M.build_for("deadman-3d", store="taped")
            except Exception as exc:  # noqa: BLE001
                if ok is None:
                    ok = f"FAIL {str(exc)[:70]}"
                continue
            k = max(m.width, m.height)
            key = (plan, dx)
            if key not in best or k < best[key][0]:
                best[key] = (k, rr, m.width, m.height, m.regions["tape"][0], m.regions["tape"][2])
        if key not in best:
            print(f"plan={plan} dx={dx}: {ok}")
for (plan, dx), v in sorted(best.items(), key=lambda kv: kv[1][0]):
    print(f"plan={plan} dx={dx:4d} -> max={v[0]} at rom_rows={v[1]}  {v[2]}x{v[3]}  store x={v[4]} w={v[5]}")
