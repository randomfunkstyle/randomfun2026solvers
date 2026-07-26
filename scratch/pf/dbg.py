import json
import sys

sys.path.insert(0, 'scratch/pf')
import prog  # noqa: E402
import vm2  # noqa: E402

P = prog.build()
d = json.load(open('tasks/problems/pathfinder.json'))
case = d['publicTestData'][int(sys.argv[1]) if len(sys.argv) > 1 else 0]
ins = [int(v) for rnd in case['rounds'] for v in rnd['in']]
m = vm2.Machine(P, ins)
orig = m.paint


def paint(v):
    if m.pstate == 2 and not (0 <= m.cursor < 256 and 0 <= v <= 15):
        raise RuntimeError(f"cursor {m.cursor} colour {v}")
    orig(v)


m.paint = paint
block = "INIT"
seen = []
try:
    while block != "HALT":
        toks, succ = P[block]
        if block in ("MAIN", "ITERPRE") and (m.fifo or m.scr):
            raise RuntimeError(f"{block} F={list(m.fifo)} G={list(m.scr)}")
        if block == "ITEREND" and (len(m.fifo) != 4 or m.scr):
            raise RuntimeError(f"ITEREND F={len(m.fifo)} G={list(m.scr)}")
        if block == "ROTPRE" and (len(m.fifo) != 2 or m.scr):
            raise RuntimeError(f"ROTPRE F={list(m.fifo)} G={list(m.scr)}")
        seen.append(block)
        if m.ops > 200000:
            raise RuntimeError("op cap")
        lane = m.step_tokens(toks)
        if lane == "DRY":
            break
        block = succ if isinstance(succ, str) else succ[lane]
except Exception as e:
    print(type(e).__name__, e)
    print("tail:", seen[-14:])
    print("ops", m.ops, "frames", len(m.frames), "ring", len(m.ring))
else:
    print("finished, frames", len(m.frames))
