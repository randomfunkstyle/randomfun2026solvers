import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vm2 import Machine, cells
import prog

P = prog.build()
d = json.load(open('tasks/problems/pathfinder.json'))
tot = 0
for case in d['publicTestData']:
    exp = []
    for rnd in case['rounds']:
        exp.extend(rnd['frames'])
    ins = [int(v) for rnd in case['rounds'] for v in rnd['in']]
    m = Machine(P, ins)
    try:
        got = m.run()
    except Exception as e:
        print("ERR", case['name'], type(e).__name__, e); break
    ok = got == exp
    print(("PASS" if ok else "FAIL"), case['name'], len(got), "/", len(exp),
          "ops", m.ops, "ring", m.maxring, "F", m.maxfifo, "G", m.maxscr)
    tot += m.ops
    if not ok:
        for i,(a,b) in enumerate(zip(got, exp)):
            if a != b:
                print("  first mismatch frame", i)
                for ra, rb in zip(a, b):
                    print("   got", ra, "exp", rb, "" if ra==rb else "<<<")
                break
        break
print("total glyph cells:", sum(cells(t) for t,_ in P.values()), "blocks", len(P))
