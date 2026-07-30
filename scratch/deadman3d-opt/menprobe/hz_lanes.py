"""What ``LANE_ORDER`` is actually allowed to be for this program.

The registry takes a permutation of the **unpinned** lanes: ``IN`` is pinned to
the top by :func:`machine.plan` and the display lane to the bottom, and under the
seek drum ``JMPS`` is spliced in by the builder rather than named.  The trie
model permutes the full north-to-south list, so a spec has to be projected onto
this set before the builder will look at it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hz_core as H  # noqa: E402

M, prog, _ = H.setup()
try:
    with H.apply(H.bump(H.shipped(), lane_order=("BOGUS",))):
        M.build_for(H.SLUG, program=prog, store=H.STORE)
except M.MachineError as exc:
    print(exc)
