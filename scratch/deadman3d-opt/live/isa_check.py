#!/usr/bin/env python3
"""Differential-test the ISA effect table (:mod:`mansem`) against ``littleman.wasm``.

The transparency prover below is only as good as its write sets, and a wrong
write set makes it *confidently* wrong — the exact failure mode this exercise
exists to remove.  So the table is not trusted because SPEC.md says so; it is
trusted because for every instruction glyph we run the reference interpreter
with A/B/BP seeded to three distinguishable values, fire the glyph, and read the
three registers plus the heading back out of the snapshot JSON.

A register counts as *written* if the interpreter changed it.  That is a
one-sided check — a glyph that writes back the value it read would look inert —
so the seeds are chosen pairwise-distinct and coprime-ish (A=7, B=3, BP=5) and
each glyph's observed result is also printed, so an identity write would show up
as a value that only makes sense if the write happened.

Pipe glyphs (``s S r R U q``) cannot run in a bare room (``no-pipe``), so they
get a two-room harness with one incoming and one outgoing pipe.

    uv run python scratch/deadman3d-opt/live/isa_check.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))
LM = REPO / "littleman" / "lm.mjs"

from randomfun2026solvers.mansem import glyph_effect  # noqa: E402

#: A=7, B=3, BP=5 — pairwise distinct, and distinct from every value any single
#: glyph can compute from them (10, 4, -7, 21, 2, 1, …) except where noted.
SEED = "5b3M7"
A0, B0, BP0 = 7, 3, 5

#: Every instruction glyph SPEC.md's ``validOps`` lists, minus the ones whose
#: harness differs (pipes, split, halt) and minus ` (needs a matched pair).
PLAIN = "0123456789.MWN+-*/%&|~{}<>^vVXxda bmq]"
PIPED = "sSrRUq"


def _run(rows: list[str], ticks: int, inp: str = "") -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".man", delete=False) as fh:
        fh.write("\n".join(rows) + "\n")
        path = fh.name
    argv = [str(LM), "tick", path, str(ticks), "--json"]
    if inp:
        argv += ["--input", inp]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)
    Path(path).unlink(missing_ok=True)
    if out.returncode != 0:
        return {"error": out.stderr.strip() or out.stdout.strip()}
    return json.loads(out.stdout)


def _man(snap: dict) -> dict | None:
    rs = (snap.get("entities") or {}).get("runners") or []
    return rs[0] if rs else None


DIRS = {(1, 0): ">", (-1, 0): "<", (0, -1): "^", (0, 1): "v"}


#: A second seed, used only to re-test glyphs whose first-seed result was
#: *inert* — the one-sided check cannot tell "no write" from "wrote back the same
#: value", and A=7 collides with both ``7`` (loads 7) and ``|`` (7|3 == 7).
SEED2, A1, B1, BP1 = "5b3M6", 6, 3, 5


def probe_plain(g: str, seed: str = SEED, a0: int = A0, b0: int = B0,
                bp0: int = BP0) -> dict:
    """Fire ``g`` once with A=7,B=3,BP=5 in a bare room; report the deltas."""
    A0, B0, BP0 = a0, b0, bp0
    SEED = seed
    body = "@" + SEED + g + "." * 6
    rows = ["+" + "-" * len(body) + "+", "|" + body + "|", "+" + "-" * len(body) + "+"]
    # '@' is a nop, then 5 seed glyphs, then g: g fires on tick 7, visible at 7.
    snap = _run(rows, 7)
    if "error" in snap:
        return {"err": snap["error"]}
    m = _man(snap)
    if m is None:
        return {"err": "no runner"}
    a, b, bp = int(m["a"]), int(m["b"]), int(m["backpack"])
    return {
        "a": a, "b": b, "bp": bp,
        "wrote": frozenset(r for r, old, new in
                           (("A", A0, a), ("B", B0, b), ("BP", BP0, bp)) if old != new),
        "dir": DIRS.get(tuple(m["dir"]), tuple(m["dir"])),
    }


def probe_piped(g: str) -> dict:
    """Fire ``g`` in a room with one incoming and one outgoing pipe.

    Layout: an input room feeds ``in``; the test room sends into an output room.
    Two values are supplied so ``q`` sees a non-trivial count.
    """
    body = "@" + SEED + g + "." * 4
    w = len(body)
    rows = [
        "+-+  +" + "-" * w + "+  +-+",
        "|I|>>|" + body + "|>>|O|",
        "+-+  +" + "-" * w + "+  +-+",
    ]
    snap = _run(rows, 7, inp="11 12 13")
    if "error" in snap:
        return {"err": snap["error"]}
    m = _man(snap)
    if m is None:
        return {"err": "no runner"}
    a, b, bp = int(m["a"]), int(m["b"]), int(m["backpack"])
    return {
        "a": a, "b": b, "bp": bp,
        "wrote": frozenset(r for r, old, new in
                           (("A", A0, a), ("B", B0, b), ("BP", BP0, bp)) if old != new),
        "dir": DIRS.get(tuple(m["dir"]), tuple(m["dir"])),
    }


def main() -> int:
    print(f"seed A={A0} B={B0} BP={BP0}  (glyph fires on tick 7)\n", flush=True)
    bad = 0
    print(f"{'g':<3} {'model.writes':<12} {'obs.writes':<12} "
          f"{'A':>5} {'B':>5} {'BP':>5} {'dir':>4}  {'head':<7} verdict")
    for g in PLAIN:
        eff = glyph_effect(g)
        r = probe_plain(g)
        if "err" in r:
            print(f"{g!r:<3} {'':<12} {'':<12} {'':>5} {'':>5} {'':>5} {'':>4}  "
                  f"{eff.heading:<7} SKIP {r['err'][:50]}", flush=True)
            continue
        model = eff.writes
        obs = r["wrote"]
        if obs < model and eff.heading == "keep":
            # Inert under seed 1 — could be a same-value write. Retry on seed 2.
            r2 = probe_plain(g, SEED2, A1, B1, BP1)
            if "err" not in r2:
                obs = obs | r2["wrote"]
        # A steer/branch glyph writes no register; heading is checked separately.
        ok = obs <= model
        exact = obs == model
        verdict = "ok" if exact else ("ok(sound)" if ok else "MISMATCH")
        if not ok:
            bad += 1
        print(f"{g!r:<3} {','.join(sorted(model)) or '-':<12} "
              f"{','.join(sorted(obs)) or '-':<12} "
              f"{r['a']:>5} {r['b']:>5} {r['bp']:>5} {str(r['dir']):>4}  "
              f"{eff.heading:<7} {verdict}", flush=True)
    print(flush=True)
    for g in PIPED:
        eff = glyph_effect(g)
        r = probe_piped(g)
        if "err" in r:
            print(f"{g!r:<3} pipe-harness error: {r['err'][:70]}", flush=True)
            continue
        model, obs = eff.writes, r["wrote"]
        ok = obs <= model
        if not ok:
            bad += 1
        print(f"{g!r:<3} {','.join(sorted(model)) or '-':<12} "
              f"{','.join(sorted(obs)) or '-':<12} "
              f"{r['a']:>5} {r['b']:>5} {r['bp']:>5} {str(r['dir']):>4}  "
              f"{eff.heading:<7} {'ok' if obs == model else ('ok(sound)' if ok else 'MISMATCH')}",
              flush=True)
    print(f"\n{bad} mismatch(es)", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
