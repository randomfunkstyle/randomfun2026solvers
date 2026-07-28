import json, subprocess, sys

MAN = sys.argv[1] if len(sys.argv) > 1 else "toy_fetch.man"
INP = sys.argv[2] if len(sys.argv) > 2 else "1 0 0 15 0 3 23"
LO = int(sys.argv[3]) if len(sys.argv) > 3 else 628
HI = int(sys.argv[4]) if len(sys.argv) > 4 else 730
STEP = int(sys.argv[5]) if len(sys.argv) > 5 else 6


def snap(t):
    r = subprocess.run(
        ["node", "../../littleman/lm.mjs", "tick", MAN, str(t), "--input", INP, "--json"],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)


d = snap(LO)
for room in d["entities"]["rooms"]:
    print("room", room["id"], room["min"], room["max"], "runners:", room.get("runners"))
for t in range(LO, HI, STEP):
    d = snap(t)
    for run in d["entities"]["runners"]:
        x, y = run["pos"]
        if 17 <= x <= 49 and 27 <= y:
            print(t, "router man", (x, y), "A", run["a"], "B", run["b"], "BP", run["backpack"])
