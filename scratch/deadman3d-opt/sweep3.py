"""Why does west input fail? Isolate the teleport / corridor-depth interaction."""
import sys

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs

SLUG = "deadman-3d"
PROG = programs.load(SLUG)
print("ROM_CPU_GAP =", M.ROM_CPU_GAP, " ROM_CPU_GAP_WITHOUT_INPUT =", M.ROM_CPU_GAP_WITHOUT_INPUT)


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
        print(f"{tag:52s} FAIL {type(exc).__name__}: {str(exc)[:100]}")
        return None
    r = m.regions
    print(
        f"{tag:52s} {m.width:4d}x{m.height:<4d} max={max(m.width, m.height):4d}"
        f"  rom={r['rom'][2]}x{r['rom'][3]} cpuY={r['cpu:trie'][1]}"
        f" I={r.get('io:I')} tpL={r.get('teleport:L')}"
    )
    return m


orig = M.ROM_CPU_GAP
for gap in (orig, 5, 6, 7):
    M.ROM_CPU_GAP = gap
    for tele in (True, False):
        for pad in (15, 17, 20, 24, 28, 32, 39):
            row(f"west gap={gap} tele={tele} pad={pad}", in_north=False, store_teleport=tele, mem_pad=pad)
M.ROM_CPU_GAP = orig
print("--- north reference")
row("north pad=17", in_north=True, mem_pad=17)
print("done")
