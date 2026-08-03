import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from lib import sweep

MT = "import randomfun2026solvers.memory_tape as mt; "
sweep([
    ("control (flag 0)", "pass"),
    ("A4 (rot room now follows)", MT + "mt.JUMP_V4_P2_BATCH = 4"),
], jobs=2)
