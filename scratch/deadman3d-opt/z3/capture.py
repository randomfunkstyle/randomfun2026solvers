"""Capture real glyph geometry + touch tables for `deadman-3d_hires` men-v3.

A pad that §7.1 refuses still *builds* -- it is ``check_bindings`` that raises --
so we stub the hook out and read back the geometry the builder would have made.
One build then answers the binding question at every pad at once, in Z3.

Output goes to /tmp (the geometry is derived from DOOM1.WAD, which is not
redistributable, so nothing derived from level data is written into the repo).

Usage:  python capture.py <out.jsonl> '<json list of knob dicts>'

Each knob dict may set: pad, in_west (None to unset), drop, squash, lane_pad,
plus any `EXTRA` registry key handled below.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))

SLUG = "deadman-3d_hires"
TIER = "men-v3"
KEY = (SLUG, TIER)


def setup():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    return d3, hires, M, prog


def _registries(M):
    return {
        "pad": M.MEM_PAD_FOR,
        "in_west": M.INPUT_NORTH_WEST,
        "drop": M.ROM_TOUCH_DROP,
        "squash": M.SQUASH_BAND,
        "ranks": M.TRIE_SLACK_ROWS,
    }


def apply_knobs(M, kn):
    """Set the registries this trial asks for; return the baseline to restore."""
    saved = {}
    if "store_dy" in kn:
        lay = M.TIER_LAYOUT[KEY]
        saved["_store"] = lay["store_offset"]
        dx = saved["_store"][0]
        lay["store_offset"] = (dx, kn["store_dy"])
    reg = _registries(M)
    for name, table in reg.items():
        saved[name] = table.get(KEY, "<unset>")
        if name in kn:
            v = kn[name]
            if v is None:
                table.pop(KEY, None)
            else:
                table[KEY] = v
    return saved


def restore(M, saved):
    if "_store" in saved:
        M.TIER_LAYOUT[KEY]["store_offset"] = saved["_store"]
    reg = _registries(M)
    for name, table in reg.items():
        v = saved[name]
        if v == "<unset>":
            table.pop(KEY, None)
        else:
            table[KEY] = v


def grab(M, prog, kn):
    """Build with ``check_bindings`` stubbed; return the FIRST call's geometry.

    ``build_for`` calls the hook once per pad trial across ``range(0,40)`` and
    keeps the smallest footprint, so ``seen[-1]`` describes pad 39 -- a machine
    nobody ever built. The first surviving call is the one that matters.
    """
    saved = apply_knobs(M, kn)
    seen: list = []
    real = M.check_bindings
    M.check_bindings = lambda g, t: seen.append((list(g), dict(t)))
    t0 = time.time()
    err = None
    m = None
    try:
        m = M.build_for(SLUG, program=prog, store=TIER)
    except Exception as e:  # noqa: BLE001 - we want the message, whatever it is
        err = f"{type(e).__name__}: {e}"
    finally:
        M.check_bindings = real
        restore(M, saved)
    if not seen:
        return {"knobs": kn, "error": err or "no check_bindings call", "secs": time.time() - t0}
    g, t = seen[0]
    rec = {
        "knobs": kn,
        "secs": round(time.time() - t0, 1),
        "n_calls": len(seen),
        "error": err,
        "glyphs": [[x, y, gl, str(b)] for x, y, gl, b in g],
        "touches": {str(k): list(v) for k, v in t.items()},
    }
    if m is not None:
        rec.update(w=m.width, h=m.height, mem_pad=m.mem_pad)
    return rec


def main():
    out = Path(sys.argv[1])
    trials = json.loads(sys.argv[2])
    _, _, M, prog = setup()
    print(f"setup done, {len(trials)} trials", flush=True)
    with out.open("a") as fh:
        for i, kn in enumerate(trials):
            rec = grab(M, prog, kn)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(
                f"[{i + 1}/{len(trials)}] {kn} -> "
                f"{rec.get('w')}x{rec.get('h')} pad={rec.get('mem_pad')} "
                f"calls={rec.get('n_calls')} err={rec.get('error')} ({rec.get('secs')}s)",
                flush=True,
            )


if __name__ == "__main__":
    main()
