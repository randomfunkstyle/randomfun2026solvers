"""Find a store/mem offset that places the whole machine once the CPU shrinks.

The pitch-1 CPU is ~21 rows shorter, so every block placed relative to it moves.
`TIER_LAYOUT`'s `store_offset` was tuned against the tall room; sweep it.
"""

import sys

from randomfun2026solvers.lm1 import machine as M


def attempt(pitch, store_dy, mem_dy=0, store_dx=-20, rom_rows=81):
    tier = dict(M.TIER_LAYOUT.get(("deadman-3d", "taped"), {}))
    tier.update(M.SEEK_TIER_LAYOUT.get(("deadman-3d", "taped"), {}))
    slug, store = "deadman-3d", "taped"
    return M.build(
        M._tier_program(slug, store),
        tape_n=M.TAPE_SIZE[slug],
        rom_rows=rom_rows,
        mem_pad=M.MEM_PAD_FOR.get((slug, store), M.SEEK_MEM_PAD.get(slug)),
        display=M.display_for(slug),
        stream=M.STREAM_SIZE.get(slug),
        store=store,
        tape_skip_batch=M.TASK_TAPE_CONFIG.get(slug, (1, None))[0],
        tape_relay_size=M.TASK_TAPE_CONFIG.get(slug, (1, None))[1],
        middle_order=M.LANE_ORDER.get(slug),
        opcode_slots=M.OPCODE_SLOTS.get((slug, store)),
        rom_buffer=M.ROM_BUFFER.get(slug),
        mem_offset=(0, mem_dy),
        store_offset=(store_dx, store_dy),
        in_north=slug in M.INPUT_NORTH,
        store_teleport=slug in M.STORE_TELEPORT and (slug, store) not in M.STORE_ANSWER_WEST,
        store_answer_west=(slug, store) in M.STORE_ANSWER_WEST,
        store_request_teleport=(slug, store) in M.STORE_REQUEST_TELEPORT,
        store_chain_reach=(slug, store) in M.TAPED_CHAIN_REACH,
        store_feed_teleport=(slug, store) in M.TAPED_FEED_TELEPORT,
        store_request_reach=(slug, store) in M.STORE_REQUEST_REACH,
        store_compact_gate=(slug, store) in M.TAPED_COMPACT_GATE,
        store_bank_order=M.TAPED_BANK_ORDER.get((slug, store)),
        trim_dead=slug in M.TRIM_DEAD_LANES,
        seek=slug in M.SEEK_DRUM,
        seek_teleport=(slug, store) in M.SEEK_TELEPORT,
        in_west=M.INPUT_NORTH_WEST.get((slug, store), 0),
        seek_taken_drop_east=(slug, store) in M.SEEK_TAKEN_DROP_EAST,
        seek_ops=M.SEEK_OPS_FOR.get(slug, M.SEEK_OPS),
        top_bus=slug in M.TOP_RETURN_BUS,
        store_shape=M.STORE_SHAPE.get(slug),
        doom_loop_row=M.DOOM_LOOP_ROW.get((slug, store)),
        doom_leaf_cols=M.DOOM_LEAF_COLS.get((slug, store)),
        lane_pitch=pitch,
    )


if __name__ == "__main__":
    pitch = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    hits, kinds = [], {}
    for sdy in range(-14, 15):
        try:
            m = attempt(pitch, sdy)
        except Exception as exc:  # noqa: BLE001 - the failure mode is the point
            key = str(exc).split("last:")[-1].strip()[:70]
            kinds[key] = kinds.get(key, 0) + 1
            print(f"store_dy={sdy:+3d}  {key}", flush=True)
            continue
        hits.append((m.width * m.height, sdy, m.width, m.height))
        print(f"store_dy={sdy:+3d}  OK  {m.width}x{m.height}", flush=True)
    print("best:", sorted(hits)[:3] if hits else "NONE PLACED")
