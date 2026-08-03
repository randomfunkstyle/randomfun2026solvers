import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from lib import sweep

MT = "import randomfun2026solvers.memory_tape as mt; "
A4 = MT + "mt.JUMP_V4_P2_BATCH = 4; "
V = [
    ("control", "pass"),
    ("A4 (repeat)", A4),
    # ── the relay's own slope, both directions ──────────────────────────────
    ("relay 3x3  (8.00 t/word)", "M.TAPE_RELAY_SIZE = (3, 3)"),
    ("relay 6x3  (3.33 t/word)", "M.TAPE_RELAY_SIZE = (6, 3)"),
    ("A4 + relay 3x3 (slow)",    A4 + "M.TAPE_RELAY_SIZE = (3, 3)"),
    ("A4 + relay flat 12",       A4 + "M.TAPE_RELAY_FLAT = 12"),
    # ── the price of the one extra worker row A4 costs ──────────────────────
    ("post_pad=1 (row price)",   MT + "mt.WORKER_JUMP_V4_POST_PAD = 1"),
    # ── does a 4x cheaper P2 change which banks want to rotate? ─────────────
    ("A4 + rotate all batch2",   A4 + "M.TAPED_ROTATE_BANKS[KEY] = (0,1,2,3,5,6,7)"),
    ("A4 + rotate +bank6",       A4 + "M.TAPED_ROTATE_BANKS[KEY] = (0,1,2,5,6)"),
]
sweep(V, jobs=5)
