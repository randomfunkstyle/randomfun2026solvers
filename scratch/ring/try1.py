import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from lib import sweep

MT = "import randomfun2026solvers.memory_tape as mt; "
V = [
    ("control", "pass"),
    ("B: flat relay 8",          "M.TAPE_RELAY_FLAT = 8"),
    ("B: flat relay 12",         "M.TAPE_RELAY_FLAT = 12"),
    ("B: flat relay 20",         "M.TAPE_RELAY_FLAT = 20"),
    ("B: flat relay 12 +b1",     "M.TAPE_RELAY_FLAT = 12; M.TAPE_RELAY_FLAT_BATCH1 = True"),
    ("A: P2 batch 4",            MT + "mt.JUMP_V4_P2_BATCH = 4"),
    ("A: P2 batch 2",            MT + "mt.JUMP_V4_P2_BATCH = 2"),
    ("A+B: batch4 + flat 12",    MT + "mt.JUMP_V4_P2_BATCH = 4; M.TAPE_RELAY_FLAT = 12"),
    ("A+B: batch4 + flat 20",    MT + "mt.JUMP_V4_P2_BATCH = 4; M.TAPE_RELAY_FLAT = 20"),
]
sweep(V, jobs=5)
