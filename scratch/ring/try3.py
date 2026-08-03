import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from lib import sweep

MT = "import randomfun2026solvers.memory_tape as mt; "
A4 = MT + "mt.JUMP_V4_P2_BATCH = 4; "
V = [
    ("control (route generalised)", "pass"),
    ("A4", A4),
    ("post_pad=1 (the row A4 costs)", MT + "mt.WORKER_JUMP_V4_POST_PAD = 1"),
    ("post_pad=2", MT + "mt.WORKER_JUMP_V4_POST_PAD = 2"),
    ("A4 + relay flat 8", A4 + "M.TAPE_RELAY_FLAT = 8"),
]
sweep(V, jobs=5)
