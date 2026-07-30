#!/usr/bin/env python3
"""Re-run ``hires_bankcut.py``'s split+order DP at the ring tax a *batched*
worker actually pays.

``hires_bankcut.RING`` is 8.0 — the ``skip_batch=1`` cost (``machine.tape_block``
docstring: "8.00 ticks per slot per access").  The two-word counted worker
(``skip_batch=2``) drops the dominant loop to ~5 ticks per skipped word, and the
four-word one (``skip_batch=4``) lower still.  The cut the DP picks is a balance
between that ring term and the ~21-tick gate hop, so lowering RING should pull
the optimum toward **fewer, longer** banks.  How far is the question.

    python scratch/deadman3d-opt/hires_batchdp.py [ring ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(HERE))

import hires_bankcut as bc  # noqa: E402

SHIPPED = ((102, 21, 229, 7, 306, 135, 6, 9, 7, 58, 21),
           (10, 9, 8, 7, 6, 0, 1, 2, 3, 5, 4))


def main(argv: list[str]) -> int:
    rings = [float(x) for x in argv] or [8.0, 5.0, 3.5]
    data = json.loads(bc.TRAFFIC.read_text())
    top = data["tape_n"] - 1
    acc = [0.0] * (top + 2)
    total = 0.0
    for name in ("reads", "writes"):
        for k, v in data[name].items():
            if int(k) <= top:
                acc[int(k)] += v
                total += v
    print(f"tape_n={data['tape_n']} top={top} traffic={total:,.0f}/frame\n", flush=True)

    from randomfun2026solvers import memory_taped as mt

    for ring in rings:
        bc.RING = ring
        print(f"=== RING={ring} HOP={bc.HOP} ===", flush=True)
        c0, _ = bc.cost(SHIPPED[0], bc.bank_accs(acc, SHIPPED[0]))
        print(f"  shipped 11-bank cut at this RING: cost {c0:12,.0f}")
        best = None
        for nb in range(2, 25):
            _, sizes = bc.dp(acc, top, nb)
            accs = bc.bank_accs(acc, sizes)
            c, order = bc.cost(sizes, accs)
            try:
                mt.gate_chain(list(sizes), order=list(order))
                ok = "ok"
            except Exception as exc:  # noqa: BLE001
                ok = f"REJECTED {exc}"
            flag = ""
            if best is None or c < best[0]:
                best, flag = (c, nb, sizes, order), "  <-- best"
            print(f"  nb={nb:2}  cost {c:12,.0f}  {100 * (c - c0) / c0:+6.2f}% vs shipped"
                  f"  {ok}{flag}", flush=True)
        c, nb, sizes, order = best
        print(f"  BEST nb={nb}: sizes={sizes}\n        order={order}\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
