"""Decode the ring at each lap boundary and compare planes with a reference."""
import json
import sys

sys.path.insert(0, 'scratch/pf')
import prog  # noqa: E402
import vm2  # noqa: E402

P = prog.build()
d = json.load(open('tasks/problems/pathfinder.json'))
case = d['publicTestData'][0]
r0 = [int(v) for v in case['rounds'][0]['in']]
board = r0[:256]
rx, ry = r0[256], r0[257]
freeset = {p for p in range(256) if board[p] == 0}


def decode(ring):
    """ring = [Q, P, (S1,NB,S2,S3) x4] -> dict of plane name -> set of p."""
    words = list(ring)
    out = {"Q": words[0], "P": words[1]}
    for name, idx in (("S1", 0), ("NB", 1), ("S2", 2), ("S3", 3)):
        s = set()
        for pos in range(4):
            w = words[2 + 4 * pos + idx]
            j = 3 - pos
            for bit in range(64):
                if (w >> bit) & 1:
                    s.add(255 - (64 * j + bit))
        out[name] = s
    return out


ins = [int(v) for rnd in case['rounds'] for v in rnd['in']]
m = vm2.Machine(P, ins)
block = "INIT"
n = 0
stop_at = int(sys.argv[1]) if len(sys.argv) > 1 else 6
while block != "HALT" and m.ops < 300000:
    if block == "ITERPRE":
        dec = decode(m.ring)
        print(f"[{n}] Q={dec['Q']} P={dec['P']} "
              f"|NB|={len(dec['NB'])} |S1|={len(dec['S1'])} "
              f"|S2|={len(dec['S2'])} |S3|={len(dec['S3'])}")
        if n == 0:
            print("   free size", len(freeset), " NB should be", len(freeset) - 1)
            print("   S1 =", sorted(dec['S1']), "expect", [dec['Q']])
        if n == 1:
            nb1 = {p for p in freeset
                   if any(q in dec['S3'] for q in (p - 1, p + 1, p - 16, p + 16))}
            print("   S1 =", sorted(dec['S1']))
            print("   want", sorted(nb1 - {dec['Q']}))
        n += 1
        if n > stop_at:
            break
    toks, succ = P[block]
    lane = m.step_tokens(toks)
    if lane == "DRY":
        break
    block = succ if isinstance(succ, str) else succ[lane]
print("robot start", 16 * ry + rx)
