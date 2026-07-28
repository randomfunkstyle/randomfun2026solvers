from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.programs import load

prog = load("deadman-3d")
kwargs = dict(
    tape_n=M.TAPE_SIZE["deadman-3d"],
    store=M.STORE_TIER["deadman-3d"],
    mem_pad=M.MEM_PAD["deadman-3d"],
    rom_rows=M.ROM_ROWS.get("deadman-3d"),
    middle_order=M.LANE_ORDER.get("deadman-3d"),
)
if hasattr(M, "display_for"):
    kwargs["display"] = M.display_for("deadman-3d")
else:
    kwargs["display"] = (64, 48)
if hasattr(M, "STREAM") and isinstance(getattr(M, "STREAM"), dict):
    kwargs["stream"] = M.STREAM.get("deadman-3d")
m = M.build(prog, **kwargs)
print(m.report())
