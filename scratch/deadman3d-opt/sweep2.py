"""Second sweep: rom width curve, taped store geometry, store offsets."""
import sys

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs

SLUG = "deadman-3d"
PROG = programs.load(SLUG)


def build(store="men-v3", **over):
    kw = dict(
        tape_n=M.TAPE_SIZE[SLUG],
        rom_rows=M.ROM_ROWS.get(SLUG),
        mem_pad=M.MEM_PAD.get(SLUG),
        display=M.display_for(SLUG),
        stream=M.STREAM_SIZE.get(SLUG),
        store=store,
        tape_skip_batch=M.TASK_TAPE_CONFIG.get(SLUG, (1, None))[0],
        tape_relay_size=M.TASK_TAPE_CONFIG.get(SLUG, (1, None))[1],
        middle_order=M.LANE_ORDER.get(SLUG),
        rom_buffer=M.ROM_BUFFER.get(SLUG),
        mem_offset=M.MEM_PLACE.get(SLUG, ((0, 0), (0, 0)))[0],
        store_offset=M.MEM_PLACE.get(SLUG, ((0, 0), (0, 0)))[1],
        in_north=SLUG in M.INPUT_NORTH,
        store_teleport=SLUG in M.STORE_TELEPORT,
        trim_dead=SLUG in M.TRIM_DEAD_LANES,
        top_bus=SLUG in M.TOP_RETURN_BUS,
        store_shape=M.STORE_SHAPE.get(SLUG),
    )
    kw.update(over)
    return M.build(PROG, **kw)


def row(tag, store="men-v3", **over):
    try:
        m = build(store=store, **over)
    except Exception as exc:  # noqa: BLE001
        print(f"{tag:44s} FAIL {type(exc).__name__}: {str(exc)[:90]}")
        return None
    r = m.regions
    rom = r["rom"]
    tp = r["tape"]
    print(
        f"{tag:44s} {m.width:4d}x{m.height:<4d} max={max(m.width, m.height):4d}"
        f"  rom_w={rom[2]:4d} rom_h={rom[3]:3d}  store x={tp[0]} y={tp[1]} {tp[2]}x{tp[3]}"
    )
    return m


what = sys.argv[1]
if what == "romw":
    for rr in list(range(56, 130, 4)):
        row(f"men-v3 rom={rr} shape 4x150", store_shape=(4, 150), rom_rows=rr)
elif what == "tapedplan":
    plans = {
        "current 6": (128, 128, 96, 99, 64, 84),
        "5 banks": (128, 128, 128, 128, 88),
        "4 banks": (150, 150, 150, 150),
        "3 banks": (200, 200, 200),
        "2 banks": (300, 300),
        "1 bank": (600,),
        "8 banks": (75,) * 8,
    }
    for tag, plan in plans.items():
        M.TAPED_BANKS[SLUG] = plan
        for sb in (1, 2):
            M.TAPED_SKIP_BATCH[SLUG] = sb
            row(f"taped {tag} sb={sb}", store="taped")
    M.TAPED_BANKS[SLUG] = (128, 128, 96, 99, 64, 84)
    M.TAPED_SKIP_BATCH[SLUG] = 2
elif what == "tapedgeom":
    for rr in (58, 59, 60, 61, 62):
        for pad in (15, 16, 17):
            row(f"taped rom={rr} pad={pad}", store="taped", rom_rows=rr, mem_pad=pad)
elif what == "offset":
    for dx in (0, -4, -8, -12, -16):
        for dy in (0, 4, 8):
            row(f"men-v3 store_off=({dx},{dy})", store_offset=(dx, dy))
    for dx in (0, -4, -8, -12, -16):
        row(f"taped store_off=({dx},0)", store="taped", store_offset=(dx, 0))
elif what == "best":
    for rr in (60, 61, 62, 63):
        for pad in (15, 16, 17):
            row(f"men-v3 rom={rr} pad={pad}", rom_rows=rr, mem_pad=pad)
print("done")
