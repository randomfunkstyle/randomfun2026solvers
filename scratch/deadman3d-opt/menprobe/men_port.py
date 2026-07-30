"""Port the hires CPU-side (slug,'taped') registries onto the men-v3 build.

Every hires tick lever landed so far is keyed ("deadman-3d_hires", "taped"), so a
men-v3 build silently loses all of them.  This adds them back, one group at a
time, and measures.

usage: men_port.py <rounds> <colsxrows> <group> [group ...]
groups: none | pitch | rom | squash | tucked | slots | loop | inw | pad | seek
        | doom | store | all
"""
import sys, time
sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
T = (SLUG, "taped")
V = (SLUG, "men-v3")
rounds = int(sys.argv[1])
cols, rws = (int(v) for v in sys.argv[2].split("x"))
groups = sys.argv[3:]
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds, shape {cols}x{rws}", flush=True)
M.STORE_SHAPE[SLUG] = (cols, rws)


def reset():
    M.SEEK_TIER_LAYOUT[V] = {"rom_rows": 119}
    for reg in ("LANE_PITCH", "ROM_TOUCH_DROP", "SQUASH_BAND", "OPCODE_SLOTS",
                "DOOM_LOOP_ROW", "INPUT_NORTH_WEST", "MEM_PAD_FOR",
                "DOOM_CLUSTER_LIFT", "DOOM_PACK_NORTH_UP", "DOOM_PACK_NORTH_WEST",
                "DOOM_LEAF_COLS"):
        getattr(M, reg).pop(V, None)
    for reg in ("TUCKED_DROPS", "SEEK_TAKEN_DROP_EAST", "SEEK_TELEPORT",
                "STORE_ANSWER_WEST", "STORE_REQUEST_REACH"):
        getattr(M, reg).discard(V)
    M.TIER_LAYOUT.pop(V, None)


def copy_dict(name):
    reg = getattr(M, name)
    if T in reg:
        reg[V] = reg[T]


def copy_set(name):
    reg = getattr(M, name)
    if T in reg:
        reg.add(V)


GROUPS = {
    "pitch": lambda: copy_dict("LANE_PITCH"),
    "rom": lambda: copy_dict("ROM_TOUCH_DROP"),
    "squash": lambda: copy_dict("SQUASH_BAND"),
    "tucked": lambda: copy_set("TUCKED_DROPS"),
    "slots": lambda: copy_dict("OPCODE_SLOTS"),
    "loop": lambda: copy_dict("DOOM_LOOP_ROW"),
    "inw": lambda: copy_dict("INPUT_NORTH_WEST"),
    "pad": lambda: copy_dict("MEM_PAD_FOR"),
    "seek": lambda: (copy_set("SEEK_TAKEN_DROP_EAST"), copy_set("SEEK_TELEPORT")),
    "doom": lambda: [copy_dict(n) for n in ("DOOM_CLUSTER_LIFT", "DOOM_PACK_NORTH_UP",
                                            "DOOM_LEAF_COLS")]
             + [copy_dict("DOOM_PACK_NORTH_WEST")],
    "store": lambda: (copy_set("STORE_ANSWER_WEST"), copy_set("STORE_REQUEST_REACH")),
}
GROUPS["all"] = lambda: [g() for k, g in GROUPS.items() if k != "all"]
GROUPS["none"] = lambda: None

for spec in groups:
    reset()
    for part in spec.split("+"):
        if part.startswith("dy"):
            M.TIER_LAYOUT[V] = {"store_offset": (0, int(part[2:]))}
        else:
            GROUPS[part]()
    t = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except Exception as exc:
        print(f"  {spec:>28}: BUILD FAILED {type(exc).__name__}: {str(exc)[:130]}",
              flush=True)
        continue
    print(f"  {spec:>28}: built {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    run(m, inp, frames, spec)
