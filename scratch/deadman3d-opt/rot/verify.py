"""Whole-machine diff: box, route_lengths, cells, and every pipe binding.

The binding map is the thing a geometry change breaks silently, so it is compared
by **pipe name** rather than by index: a room whose glyph now reaches a different
pipe is a wrong answer nobody's tests would see. Cells that exist in only one of
the two grids are reported separately — under a body swap those are exactly the
worker rooms and nothing else may appear there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["CLAUDE_JOB_DIR"] + "/tmp/h")
from common import SLUG, setup  # noqa: E402


def build(M, prog, rot):
    from randomfun2026solvers import memory_tape as T

    if rot is None:  # HEAD: neither lever
        M.TAPED_ROTATE_BANKS.pop((SLUG, "taped"), None)
        T._JUMP_V4_TIGHT_ARMS = False
    else:
        M.TAPED_ROTATE_BANKS[(SLUG, "taped")] = rot
        T._JUMP_V4_TIGHT_ARMS = True
    return M.build_for(SLUG, program=prog, store="taped")


def main() -> None:
    _d3, _hires, M, prog = setup()
    assert "worktrees/compactor" in M.__file__, M.__file__
    from randomfun2026solvers.fast_littleman import FastLittleman

    sys.path.insert(0, str(Path(M.__file__).resolve().parents[4] / "scratch"))
    from doom_case import pipe_names, room_labels  # noqa: E402

    shipped_rot = (0, 1, 2, 5)
    out = {}
    for tag, rot in (("control", None), ("rot", shipped_rot)):
        m = build(M, prog, rot)
        g = FastLittleman("\n".join(m.rows))
        pn = pipe_names(g, m)
        rl = room_labels(g, m)
        named = {}
        for cell, b in g._bindings.items():
            if isinstance(b, tuple):
                named[cell] = tuple(sorted(pn[i] for i in b))
            else:
                named[cell] = pn[b]
        cells = {(x, y): m.rows[y][x] for y in range(m.height)
                 for x in range(len(m.rows[y])) if m.rows[y][x] != " "}
        out[tag] = dict(
            m=m, box=(m.width, m.height), routes=dict(m.route_lengths),
            bindings=named, cells=cells, rooms=len(rl), pipes=len(pn), labels=rl,
        )
        print(f"{tag:8s} box={m.width}x{m.height} rooms={len(rl)} pipes={len(pn)} "
              f"bindings={len(named):,} cells={len(cells):,}", flush=True)
        print(f"         route_lengths={m.route_lengths}", flush=True)

    a, b = out["control"], out["rot"]
    print(f"\nbox identical: {a['box'] == b['box']}")
    print(f"route_lengths identical: {a['routes'] == b['routes']}")
    print(f"room labels identical: {a['labels'] == b['labels']}")

    ka, kb = set(a["bindings"]), set(b["bindings"])
    common = ka & kb
    changed = {c for c in common if a["bindings"][c] != b["bindings"][c]}
    print(f"\nbindings: {len(ka):,} vs {len(kb):,}; common {len(common):,}, "
          f"changed-in-common {len(changed)}")
    for c in sorted(changed)[:40]:
        print(f"  {c}: {a['bindings'][c]} -> {b['bindings'][c]}")
    for tag, only in (("control-only", ka - kb), ("rot-only", kb - ka)):
        rooms = sorted({_room_of(out['rot' if tag == 'rot-only' else 'control'], c)
                        for c in only})
        print(f"  {tag}: {len(only)} glyphs in rooms {rooms}")
        for c in sorted(only):
            src = b if tag == "rot-only" else a
            print(f"     {c} {src['m'].rows[c[1]][c[0]]!r} -> {src['bindings'][c]}")

    ca, cb = a["cells"], b["cells"]
    diff = {k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k)}
    print(f"\ncells differing: {len(diff)}")
    rooms = sorted({_room_of(b, c) or _room_of(a, c) for c in diff})
    print(f"  confined to rooms: {rooms}")


def _room_of(side, cell):
    from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: F401
    g = getattr(side, "_g", None)
    if g is None:
        g = FastLittleman("\n".join(side["m"].rows))
        side["_g"] = g
    labels = side["labels"]
    for i, r in enumerate(g.rooms):
        if r.min[0] <= cell[0] <= r.max[0] and r.min[1] <= cell[1] <= r.max[1]:
            return labels[i]
    return None


if __name__ == "__main__":
    main()
