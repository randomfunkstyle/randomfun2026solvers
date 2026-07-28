"""tape_block geometry vs bank size, and taped block dims vs a plan."""
import sys

from randomfun2026solvers.lm1.machine import tape_block
from randomfun2026solvers import memory_taped as T

if len(sys.argv) > 1 and "," in sys.argv[1]:
    plan = tuple(int(x) for x in sys.argv[1].split(","))
    order = tuple(int(x) for x in sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    b = T.taped_store_block(601, plan, skip_batch=2, compact_gate=True, order=order)
    print(f"plan={plan} order={order} -> block {b.width}x{b.height}")
    sys.exit()

for size in (16, 24, 32, 40, 48, 56, 64, 72, 85, 96, 128, 160, 195, 200, 224, 256, 288, 300, 320, 384, 448, 512, 600):
    t = tape_block(size + 1, skip_batch=2)
    print(f"  size={size:4d}  tape {t.width}x{t.height}")
