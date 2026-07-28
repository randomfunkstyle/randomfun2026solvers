"""Build-only sweep of the deadman-3d layout registry knobs."""
import itertools
import sys
import traceback

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


def dims(store="men-v3", **over):
    try:
        m = build(store=store, **over)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:110]}"
    return (m.width, m.height), m


def main():
    what = sys.argv[1]
    if what == "rom":
        for rr in range(48, 96, 2):
            d, m = dims(rom_rows=rr)
            print(f"rom_rows={rr:3d}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    elif what == "romfine":
        for rr in range(56, 76):
            d, m = dims(rom_rows=rr)
            print(f"rom_rows={rr:3d}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    elif what == "shape":
        for c in range(6, 15):
            for r in range(max(1, -(-M.TAPE_SIZE[SLUG] // c)), -(-M.TAPE_SIZE[SLUG] // c) + 3):
                for rr in (52, 56, 60, 64, 68, 72):
                    d, m = dims(store_shape=(c, r), rom_rows=rr)
                    print(f"shape={c}x{r} rom={rr:3d}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    elif what == "pad":
        for north in (True, False):
            for pad in range(8, 48):
                d, m = dims(in_north=north, mem_pad=pad)
                print(f"north={north} pad={pad:3d}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    elif what == "dy":
        for dy in range(0, 12):
            d, m = dims(store_offset=(0, dy))
            print(f"store_dy={dy}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    elif what == "taped":
        for rr in range(48, 90, 2):
            d, m = dims(store="taped", rom_rows=rr)
            print(f"taped rom_rows={rr:3d}  {d}  max={max(d) if d else '-'}  {'' if d else m}")
    print("done")


main()
