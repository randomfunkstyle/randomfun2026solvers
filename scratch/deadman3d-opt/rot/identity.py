"""Byte-identity of every grid this edit is **not** allowed to move.

``memory_tape`` and ``memory_taped`` are shared with ``matmul``, ``sudoku`` and
the byte-pinned ``deadman-3d``; the rotating worker is selected by a registry,
never edited into a shared body, and this is the check that says so. It hashes
built grids under this worktree and under a ``git archive HEAD`` checkout and
compares hash for hash.

    git archive HEAD solvers | tar -x -C /tmp/rothead
    python identity.py /tmp/rothead

The package is installed **editable**, which registers a ``MetaPathFinder`` that
outranks ``sys.path`` entirely — so the HEAD side drops it or it silently
measures the working tree.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def load(repo: str | None):
    if repo:
        sys.meta_path = [
            f for f in sys.meta_path if "editable" not in type(f).__name__.lower()
        ]
        root = Path(repo)
    else:
        root = Path("/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/"
                    ".claude/worktrees/compactor")
    for k in [k for k in sys.modules if k.startswith("randomfun2026solvers")]:
        del sys.modules[k]
    sys.path.insert(0, str(root / "solvers" / "python"))
    import randomfun2026solvers  # noqa: F401

    return root


def h(rows) -> str:
    if hasattr(rows, "rows"):
        rows = rows.rows
    if isinstance(rows, dict):
        rows = [f"{k}={v}" for k, v in sorted(rows.items())]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]


def grids() -> dict[str, str]:
    from randomfun2026solvers import memory_tape as T
    from randomfun2026solvers import memory_taped as MT
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1 import programs

    out: dict[str, str] = {}

    # ── the shared low-level bodies, every form any caller selects ───────────
    for n in (7, 8, 22, 53, 59, 115, 135, 442):
        out[f"worker_v2({n})"] = h(T.worker(n).rows())
        out[f"worker_v2_v3({n})"] = h(T.worker_v2(n).rows())
        out[f"worker_v2_v3park({n})"] = h(T.worker_v2(n, park_const=True).rows())
        out[f"worker_v2_v4({n})"] = h(
            T.worker_v2(n, park_const=True, protocol="v4").rows())
        out[f"worker_jump_v3({n})"] = h(T.worker_v2_jump(n).rows())
        out[f"worker_jump_v3park({n})"] = h(T.worker_v2_jump(n, park_const=True).rows())
        out[f"worker_jump_v4({n})"] = h(
            T.worker_v2_jump(n, park_const=True, protocol="v4").rows())
        out[f"worker_jump4({n})"] = h(T.worker_v2_jump4(n).rows())
        out[f"worker_v3({n})"] = h(T.worker_v3(n).rows())
        for sb in (1, 2, 4):
            t = M.tape_block(n, skip_batch=sb)
            out[f"tape_block({n},{sb})"] = h([f"{k}{v}" for k, v in sorted(t.cells.items())])
        for proto in ("v3", "v4"):
            t = M.tape_block(n, skip_batch=2, park_const=True, protocol=proto,
                             west_grow=4)
            out[f"tape_block_wg4({n},{proto})"] = h(
                [f"{k}{v}" for k, v in sorted(t.cells.items())])
    for hgt in (8, 14, 20, 35):
        out[f"feed_relay({hgt})"] = h(MT.feed_relay(hgt)[0])
        out[f"feed_unpack({hgt})"] = h(MT.feed_unpack(hgt)[0])
    for m in (5, 8, 102, 441):
        for high in (None, 901):
            for proto in ("v3", "v4"):
                g, w = MT.bank_gate(m, compact=True, high=high, park_const=True,
                                    protocol=proto)
                out[f"bank_gate({m},{high},{proto})"] = h(
                    [str(w)] + [f"{k}{v}" for k, v in sorted(g.items())])
    # ... and the taped block itself, at deadman-3d's own plan
    b = MT.taped_store_block(600, M.TAPED_BANKS["deadman-3d"], skip_batch=2,
                             compact_gate=True,
                             order=list(M.TAPED_BANK_ORDER[("deadman-3d", "taped")]),
                             chain_reach=True, feed_teleport=True)
    out["taped_block(deadman-3d)"] = h([f"{k}{v}" for k, v in sorted(b.cells.items())])

    if os.environ.get("BODIES_ONLY"):
        return out
    # ── whole machines for every slug that shares these modules ─────────────
    import time
    only = os.environ.get("SLUGS")
    todo = [("matmul", None), ("sudoku-validity", None), ("snake-ring", None),
            ("brackets", None), ("deadman-3d", None), ("deadman-3d", "men-v3"),
            ("deadman-3d", "taped")]
    for slug, store in todo:
        if only and slug not in only.split(","):
            continue
        if True:
            for seek in (False, True):
                key = f"{slug}/{store or 'default'}{'+seek' if seek else ''}"
                t0 = time.time()
                try:
                    kw = {}
                    if store:
                        kw["store"] = store
                    if seek is not None:
                        kw["seek"] = seek
                    out[key] = h(M.build_for(slug, **kw))
                except Exception as e:  # noqa: BLE001
                    out[key] = f"<{type(e).__name__}: {str(e)[:60]}>"
                print(f"    {key} {out[key]} ({time.time()-t0:.0f}s)",
                      file=sys.stderr, flush=True)
    return out


if __name__ == "__main__":
    head = sys.argv[1] if len(sys.argv) > 1 else None
    load(None)
    mine = grids()
    if head:
        load(head)
        theirs = grids()
    else:
        theirs = mine
    keys = sorted(set(mine) | set(theirs))
    bad = [k for k in keys if mine.get(k) != theirs.get(k)]
    print(f"{len(keys)} grids hashed, {len(bad)} differ")
    for k in bad:
        print(f"  DIFF {k}: {mine.get(k)} vs {theirs.get(k)}")
    err = [k for k in keys if str(mine.get(k)).startswith("<")]
    for k in err:
        print(f"  (skipped {k}: {mine[k]})")
    raise SystemExit(1 if bad else 0)
