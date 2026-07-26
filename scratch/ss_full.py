"""Throwaway: run the full subset-sum grid on the seven public cases."""
from pathlib import Path

from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.subset_sum_mitm import expected_output, public_cases

lm = Littleman()
for name, vals, t, want in public_cases():
    inp = " ".join(map(str, [len(vals), *vals, t]))
    d = lm.tick(Path("/tmp/ss_full.man"), 8_000_000, input=inp).model_dump()
    got = d["output"]
    exp = expected_output(vals, t)
    print(f"{name:32} {'OK ' if got == exp else 'FAIL'} got={got} want={exp}")
