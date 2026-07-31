"""SEEK_OPS with BRN, apples-to-apples.

`OPCODE_SLOTS[("deadman-3d_hires", "taped")]` is a tuned DP result that names
only the opcodes today's build uses, so adding `BRN` to SEEK_OPS produces a
`BRNS` the map does not name and the build refuses. Re-running that DP is not
the measurement — the measurement is what the *family* is worth. So both arms
here drop the tier's slot map and fall through to the contiguous packing: the
pair is then comparable, and the shipped map's absence is a constant.

usage: ops2.py <store> <rounds> <opset> [opset ...]
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
store = sys.argv[1]
rounds = int(sys.argv[2])
opsets = [tuple(a.split("+")) for a in sys.argv[3:]]
inp, frames = tour(hires, rounds)
M.OPCODE_SLOTS.pop((SLUG, store), None)
print(f"tour {len(frames)} rounds, store={store}, OPCODE_SLOTS dropped", flush=True)

for ops in opsets:
    M.SEEK_OPS_FOR[SLUG] = ops
    n = sum(1 for i in M.seek_split(prog, ops=ops).instrs if i.sem in M._SEEK_SEMS)
    tag = "+".join(ops)
    t0 = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    except Exception as exc:
        print(f"  {tag}: BUILD FAILED {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        continue
    print(f"  {tag}: {n} seek instrs, built {m.width}x{m.height} "
          f"({time.time()-t0:.0f}s)", flush=True)
    try:
        run(m, inp, frames, tag)
    except Exception as exc:
        print(f"  {tag}: RUN FAILED {type(exc).__name__}: {str(exc)[:160]}", flush=True)
