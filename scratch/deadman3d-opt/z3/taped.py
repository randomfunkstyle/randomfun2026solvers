"""Capture real glyph geometry + touch tables for `deadman-3d_hires` **taped**.

Same instrument as :mod:`capture` (stub ``check_bindings``, read the FIRST
surviving call's geometry) pointed at the taped tier, whose knobs differ:
``rom_touch_drop`` and ``squash_band`` are *keyword arguments* of
:func:`build_for`, not registry entries we have to patch and restore, so a trial
touches the module only for ``mem_pad``, ``in_west`` and the store offset.

Two deciders are reported, and they are not two *rules*: since `c86ef95`
``machine.check_bindings`` is the engines' key verbatim, which is
:func:`bind.decide`, and that is the gate a build has to pass.
:func:`gate.decide_strict` is the superseded tie-refusing rule, kept as a
**margin** -- a binding it rejects is legal but decided by reading order, one
geometry nudge from a wrong frame.

Output goes to /tmp only: the geometry is derived from DOOM1.WAD.

Usage:  python taped.py <out.jsonl> '<json list of knob dicts>'
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

#: ``LM1_REPO`` points the import at another checkout. The working tree is shared
#: with other agents and can be mid-edit (a half-saved ``machine.py`` raised
#: ``NameError: adapter_compact`` in the middle of a sweep), so a measurement run
#: that has to be reproducible pins itself to committed HEAD instead::
#:
#:     git archive HEAD solvers | tar -x -C /tmp/z3head
#:     LM1_REPO=/tmp/z3head python taped.py ...
REPO = Path(os.environ.get("LM1_REPO") or Path(__file__).resolve().parents[3])
if os.environ.get("LM1_REPO"):
    # The package is installed editable, which registers a ``MetaPathFinder``.
    # A finder outranks ``sys.path`` entirely, so pointing the path elsewhere is
    # silently ignored -- the symptom is a build that reports the *working
    # tree's* registry while you believe you pinned a checkout. Drop the finder.
    sys.meta_path = [f for f in sys.meta_path
                     if "editable" not in type(f).__name__.lower()]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SLUG = "deadman-3d_hires"
TIER = "taped"
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


def shipped(M):
    """The registry values this tier ships with -- the baseline every delta is off."""
    return {
        "pad": M.MEM_PAD_FOR.get(KEY),
        "in_west": M.INPUT_NORTH_WEST.get(KEY),
        "drop": M.ROM_TOUCH_DROP.get(KEY, 0),
        "squash": M.SQUASH_BAND.get(KEY, 0),
        "store_dy": M.TIER_LAYOUT[KEY]["store_offset"][1],
        "ranks": M.TRIE_SLACK_ROWS.get(KEY, "<unset>"),
    }


def apply_knobs(M, kn):
    saved = {}
    lay = M.TIER_LAYOUT[KEY]
    saved["_store"] = lay["store_offset"]
    if "store_dy" in kn:
        lay["store_offset"] = (saved["_store"][0], kn["store_dy"])
    for name, table in (("pad", M.MEM_PAD_FOR), ("in_west", M.INPUT_NORTH_WEST),
                        ("ranks", M.TRIE_SLACK_ROWS)):
        saved[name] = table.get(KEY, "<unset>")
        if name in kn:
            v = kn[name]
            if v is None:
                table.pop(KEY, None)
            else:
                table[KEY] = v
    return saved


def restore(M, saved):
    M.TIER_LAYOUT[KEY]["store_offset"] = saved["_store"]
    for name, table in (("pad", M.MEM_PAD_FOR), ("in_west", M.INPUT_NORTH_WEST),
                        ("ranks", M.TRIE_SLACK_ROWS)):
        v = saved[name]
        if v == "<unset>":
            table.pop(KEY, None)
        else:
            table[KEY] = v


def grab(M, prog, kn, *, live=False):
    """Build with ``check_bindings`` stubbed (``live=False``); FIRST call's geometry.

    ``live=True`` leaves the real gate in place *and still records* -- that is how
    a candidate is promoted from witness to built.
    """
    saved = apply_knobs(M, kn)
    kw = {}
    if "drop" in kn:
        kw["rom_touch_drop"] = kn["drop"]
    if "squash" in kn:
        kw["squash_band"] = kn["squash"]
    seen: list = []
    real = M.check_bindings

    def spy(g, t):
        seen.append((list(g), dict(t)))
        if live:
            real(g, t)

    M.check_bindings = spy
    t0 = time.time()
    err = None
    m = None
    try:
        m = M.build_for(SLUG, program=prog, store=TIER, **kw)
    except Exception as e:  # noqa: BLE001 - the message is the measurement
        err = f"{type(e).__name__}: {e}"
    finally:
        M.check_bindings = real
        restore(M, saved)
    rec = {"knobs": kn, "secs": round(time.time() - t0, 1), "live": live,
           "n_calls": len(seen), "error": err}
    if seen:
        g, t = seen[0]
        rec["glyphs"] = [[x, y, gl, str(b)] for x, y, gl, b in g]
        rec["touches"] = {str(k): list(v) for k, v in t.items()}
    if m is not None:
        rec.update(w=m.width, h=m.height, mem_pad=m.mem_pad)
    return rec


def main():
    out = Path(sys.argv[1])
    trials = json.loads(sys.argv[2])
    live = "--live" in sys.argv[3:]
    _, _, M, prog = setup()
    print(f"setup done; shipped = {shipped(M)}; {len(trials)} trials, live={live}",
          flush=True)
    with out.open("a") as fh:
        for i, kn in enumerate(trials):
            rec = grab(M, prog, kn, live=live)
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
