import sys, re
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/scratch/ring")
from common import setup, SLUG
d3, hires, M, prog = setup()
from randomfun2026solvers.memory_taped import taped_plan, gate_chain
n = M.TAPE_SIZE[SLUG]
banks = M.TAPED_BANKS[SLUG]
plan = taped_plan(n, banks)
order = M.TAPED_BANK_ORDER.get((SLUG, "taped"))
chain = gate_chain(plan, order)
print("TAPE_SIZE", n, "banks", banks)
print("plan", plan)
print("chain", chain)
print("rotate", M.TAPED_ROTATE_BANKS.get((SLUG, "taped")))
print("skip_batch", M.TAPED_SKIP_BATCH.get(SLUG), "thresh", M.TAPED_JUMP_THRESHOLD.get(SLUG))
from randomfun2026solvers.lm1.machine import _resolve_tape_skip_batch
for i, (k, _t) in enumerate(chain):
    sz = plan[k]
    print(f"  chainpos={i} bank={k} size={sz} slots={sz+1} "
          f"batch={_resolve_tape_skip_batch(sz+1, None, 16)} "
          f"rot={k in M.TAPED_ROTATE_BANKS[(SLUG,'taped')]}")

m = M.build_for(SLUG, program=prog, store="taped")
rows = m.rows
print("machine", m.width, m.height)
for y in range(len(rows) - 1):
    for mo in re.finditer(r"drsmv", rows[y]):
        x = mo.start()
        if rows[y+1][x:x+5] == "^msrd":
            print("  ring at", (x, y))
