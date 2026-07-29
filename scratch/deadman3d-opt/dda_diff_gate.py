"""Emulator pixel gate for the `dda_diff` DDA rewrite.

`tests/test_deadman3d.py`'s golden gate runs the *canonical* program, so the
taped tier's program needs its own run against the same golden frames. Usage::

    uv run python scratch/deadman3d-opt/dda_diff_gate.py base   # dda_acc_reload=False
    uv run python scratch/deadman3d-opt/dda_diff_gate.py diff   # ... + dda_diff=True
"""
import sys

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.lm1.asm import assemble
from randomfun2026solvers.lm1.display import frames_from_writes
from randomfun2026solvers.lm1.emulator import Emulator, Round

kw = {"dda_acc_reload": False}
if sys.argv[1:2] == ["diff"]:
    kw["dda_diff"] = True
prog = assemble(d3.deadman3d_source(**kw), name="deadman-3d")
cmds = list(d3.WALK)
res = Emulator(prog).run(
    [Round(input=tuple(d3.input_words(cmds)))], max_instructions=20_000_000
)
assert res.reason == "input-exhausted", res.reason
got = frames_from_writes(res.display_writes, width=d3.WIDTH, height=d3.HEIGHT)
want = [d3.title_frame()] + d3.frames_for_commands(cmds)
print(f"P={prog.P} instructions={res.instructions}")
print(f"frames {len(got)}/{len(want)} pixel-equal: {got == want}")
for i, (a, b) in enumerate(zip(got, want)):
    if a != b:
        print("first differing frame:", i)
        break
