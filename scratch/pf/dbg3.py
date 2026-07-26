import json
import sys

sys.path.insert(0, 'scratch/pf')
import prog  # noqa: E402
import vm2  # noqa: E402

P = prog.build()
d = json.load(open('tasks/problems/pathfinder.json'))
case = d['publicTestData'][0]
ins = [int(v) for rnd in case['rounds'] for v in rnd['in']]
m = vm2.Machine(P, ins)
block = "INIT"
watch = set(sys.argv[1:]) or {"AFTERLOAD", "SETROBOT", "ALIGNEND", "MAIN"}
seen = 0
while block != "HALT" and m.ops < 200000:
    if block in watch:
        print(block, "len", len(m.ring), "head", list(m.ring)[:3],
              "tail", list(m.ring)[-2:])
        seen += 1
        if seen > 8:
            break
    toks, succ = P[block]
    lane = m.step_tokens(toks)
    if lane == "DRY":
        break
    block = succ if isinstance(succ, str) else succ[lane]
