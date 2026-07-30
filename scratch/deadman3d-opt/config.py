#!/usr/bin/env python3
"""A measurement is meaningless without the feature set it was taken under.

Every tick figure in ``METRICS.md`` and in ``machine.py``'s docstrings is an
unlabelled number: it says what something was worth, never *on what machine*.
That is exactly the gap that let ``TAPED_CHAIN_REACH`` sit declined at -0.020%
while being worth -2.678% — the number was right, the machine underneath it had
changed, and nothing recorded which machine it had been.

So: a measurement is a triple.

    measurement = (task, feature-set, result)

:func:`feature_set` reads the *applicable* configuration straight out of
``lm1.machine`` — every registry entry keyed by the slug or by ``(slug, tier)``,
plus the program knobs, which live in a function call rather than a registry and
so have to be passed in. :func:`digest` reduces it to eight hex characters that
can be quoted beside a number, and :func:`diff` says what changed between two of
them, which is the question actually worth asking:

    $ python scratch/deadman3d-opt/config.py
    deadman-3d_hires/taped  config 4d9c1a77   (23 features)
      ...

Not a bitmask: bit positions are only stable while nobody adds a feature, and
these values are not booleans — ``TAPED_BANKS`` is an eleven-tuple, ``LANE_PITCH``
an int, ``STORE_SHAPE`` a pair. A sorted key->value mapping hashed to a digest
keeps the compactness of a mask, survives new features, and stays diffable.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "solvers" / "python") not in sys.path:
    sys.path.insert(0, str(REPO / "solvers" / "python"))

#: Program-level knobs: real features that are *not* registries, because this
#: family builds its source at call time (`deadman3d_hires.hires_source`). They
#: have to be stated rather than read, so state them here in one place.
PROGRAM_KNOBS = ("dda_acc_reload", "dda_diff", "lap_via_jump", "dda_stepy_split")


def feature_set(slug: str, store: str = "taped", **program) -> dict[str, object]:
    """Every ``lm1.machine`` setting that applies to ``(slug, store)``.

    Reads the registries generically rather than from a hand-kept list, so a
    feature added tomorrow is captured without anyone remembering to add it —
    the failure mode of a hand-kept list is silently omitting the one knob that
    mattered.
    """
    from randomfun2026solvers.lm1 import machine as M

    out: dict[str, object] = {}
    for name in dir(M):
        if not name.isupper() or name.startswith("_"):
            continue
        v = getattr(M, name)
        if isinstance(v, set):
            if slug in v:
                out[name] = True
            elif (slug, store) in v:
                out[name] = True
        elif isinstance(v, dict):
            if slug in v:
                out[name] = v[slug]
            elif (slug, store) in v:
                out[name] = v[(slug, store)]
    for k in PROGRAM_KNOBS:
        if k in program:
            out[f"prog:{k}"] = program[k]
    return out


def canonical(features: dict) -> str:
    """Stable JSON: sorted keys, tuples as lists, no whitespace drift."""
    return json.dumps(features, sort_keys=True, default=list, separators=(",", ":"))


def digest(features: dict) -> str:
    return hashlib.sha256(canonical(features).encode()).hexdigest()[:8]


def diff(a: dict, b: dict) -> list[tuple[str, object, object]]:
    """``(key, was, now)`` for every feature that differs. The useful output."""
    return [
        (k, a.get(k, "—"), b.get(k, "—"))
        for k in sorted(set(a) | set(b))
        if a.get(k, "—") != b.get(k, "—")
    ]


def record(path: Path, task: str, features: dict, **result) -> dict:
    """Append one measurement to a JSONL log and return it.

    The log is the point: a number in a commit message is prose, a number beside
    its config digest is checkable. Nothing here is IWAD-derived — registry values
    and tick counts only — so it is safe to commit.
    """
    row = {
        "task": task,
        "config": digest(features),
        "features": features,
        **result,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=list) + "\n")
    return row


def main(argv: list[str]) -> int:
    slug = argv[0] if argv else "deadman-3d_hires"
    store = argv[1] if len(argv) > 1 else "taped"
    # what `deadman3d_hires.hires_source()` actually passes today
    knobs = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True,
                 lap_via_jump=False) if slug == "deadman-3d_hires" else {}
    f = feature_set(slug, store, **knobs)
    print(f"{slug}/{store}  config {digest(f)}   ({len(f)} features)")
    for k, v in sorted(f.items()):
        s = str(v)
        print(f"  {k:24} {s if len(s) <= 60 else s[:57] + '...'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
