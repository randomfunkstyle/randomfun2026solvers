"""Reference model of the bitplane BFS algorithm, validated on the public cases."""
import json, sys

def solve(rounds):
    r0 = [int(v) for v in rounds[0]['in']]
    board = r0[:256]
    rx, ry = r0[256], r0[257]
    free = [0]*16
    for y in range(16):
        w = 0
        for x in range(16):
            if board[y*16+x] == 0:
                w |= 1 << x
        free[y] = w
    buf = [[7 if board[y*16+x] else 0 for x in range(16)] for y in range(16)]
    frames = []
    buf[ry][rx] = 10
    frames.append(["".join("%x" % c for c in row) for row in buf])

    for rnd in rounds[1:]:
        fx, fy = (int(v) for v in rnd['in'])
        buf[fy][fx] = 9
        l0 = [0]*16
        l1 = [0]*16
        l0[fy] |= 1 << fx
        w = 0
        def labelled(y):
            return l0[y] | l1[y]
        def match(y, v):
            a = l0[y] if (v & 1) else (~l0[y] & 0xFFFF)
            b = l1[y] if (v & 2) else (~l1[y] & 0xFFFF)
            return a & b
        d = None
        if (labelled(ry) >> rx) & 1:
            d = 0
        while d is None:
            tf = (w % 3) + 1
            tn = ((w + 1) % 3) + 1
            f = [match(y, tf) for y in range(16)]
            new = [0]*16
            for y in range(16):
                c = (f[y] << 1) | (f[y] >> 1)
                if y > 0:
                    c |= f[y-1]
                if y < 15:
                    c |= f[y+1]
                c &= 0xFFFF & free[y] & ~labelled(y)
                new[y] = c
            for y in range(16):
                if tn & 1:
                    l0[y] |= new[y]
                if tn & 2:
                    l1[y] |= new[y]
            w += 1
            if (labelled(ry) >> rx) & 1:
                d = w
            if w > 300:
                raise RuntimeError("bfs did not reach the robot")
        for step in range(d):
            tgt = (((d - step - 1) % 3) + 1)
            for dx, dy in ((0,-1),(1,0),(0,1),(-1,0)):
                nx, ny = rx+dx, ry+dy
                if (match(ny, tgt) >> nx) & 1:
                    break
            else:
                raise RuntimeError("stuck")
            buf[ry][rx] = 0
            rx, ry = nx, ny
            buf[ry][rx] = 10
            frames.append(["".join("%x" % c for c in row) for row in buf])
    return frames

def main():
    d = json.load(open(sys.argv[1]))
    ok = True
    for case in d['publicTestData']:
        exp = []
        for rnd in case['rounds']:
            exp.extend(rnd['frames'])
        got = solve(case['rounds'])
        if got == exp:
            print("PASS", case['name'], len(exp), "frames")
        else:
            ok = False
            print("FAIL", case['name'], "got", len(got), "expected", len(exp))
            for i, (a, b) in enumerate(zip(got, exp)):
                if a != b:
                    print(" first mismatch frame", i)
                    for ra, rb in zip(a, b):
                        print("  got", ra, " exp", rb, "" if ra == rb else "  <<<")
                    break
    print("ALL PASS" if ok else "FAILURES")

main()
