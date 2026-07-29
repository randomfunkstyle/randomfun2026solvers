#!/usr/bin/env python3
"""Is the **seek drum** worth anything on ``deadman-3d_hires``?

``SEEK_DRUM`` has never contained the hires slug and nobody had measured it: the
-18.7% / -11.0% it is worth on ``deadman-3d`` was read off a ~4,300-word program,
and hires is P=9,225 against a 494-column router wall.  ``build_for`` takes an
explicit ``seek=`` so the whole sweep runs without touching the registry.

Every coupled registry is re-derived rather than copied, because each is keyed to
``deadman-3d``'s geometry: :data:`machine.SEEK_MEM_PAD` /
:data:`machine.MEM_PAD_FOR` (hires has no ``MEM_PAD`` entry at all, so both the
classic and the seek build fall through to ``build``'s own pad search),
:data:`machine.SEEK_SLAB_PITCH`, :data:`machine.SEEK_TIER_LAYOUT` (the fold), and
the two seek-only routing knobs :data:`machine.SEEK_TELEPORT` /
:data:`machine.SEEK_TAKEN_DROP_EAST`.

    python scratch/deadman3d-opt/hires_seek.py build  [variant ...]
    python scratch/deadman3d-opt/hires_seek.py fold  LO HI STEP
    python scratch/deadman3d-opt/hires_seek.py run  ROUNDS [variant ...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")

#: name -> registry overlay.  ``seek`` is passed to ``build_for`` directly; the
#: rest are written into the module registries around the one call and undone
#: after, so nothing here can leak into another variant.
VARIANTS: dict[str, dict[str, object]] = {
    #: no overlay at all — whatever the registries currently say.  After shipping
    #: this must reproduce ``S119`` to the tick.
    "ship": {},
    "base": {"seek": False},
    "seek": {"seek": True, "jmps_slot": 25},
    "seek26": {"seek": True, "jmps_slot": 26},
    "seek27": {"seek": True, "jmps_slot": 27},
    # the fold has to be re-picked from scratch: ROM_ROWS' 88 does not even build
    # under the drum ("row 0 holds 152 words >= K=128"), because a seek row's
    # words are addressed as ``row*K + offset`` and K is 128.
    # 110 is the shallowest that builds and 111 the shallowest that RUNS (110,
    # 121, 122 and 123 pack a literal whose reverse reading leaves signed 64
    # bits).  Every fold in 111..135 comes out 531 wide and pad 35.
    "seek111": {"seek": True, "jmps_slot": 25, "rom_rows": 111},
    "seek115": {"seek": True, "jmps_slot": 25, "rom_rows": 115},
    "seek120": {"seek": True, "jmps_slot": 25, "rom_rows": 120},
    "seek130": {"seek": True, "jmps_slot": 25, "rom_rows": 130},
    # the seek-only routing knobs, both keyed to ``deadman-3d`` only today
    "seek111+tp": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "teleport": True},
    "seek111+de": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "drop_east": True},
    "seek111+both": {"seek": True, "jmps_slot": 25, "rom_rows": 111,
                     "teleport": True, "drop_east": True},
    # and the pad, which is the seek build's headline cost: 35 against base's 15
    "seek111+pitch11": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "pitch": 11},
    "seek111+inw": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "in_west": 13},
    "seek111+p12": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "pitch": 12},
    "seek111+p11w": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "pitch": 11,
                     "in_west": 13},
    # the stack: fold 111, pitch 11, both routing knobs
    "S": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "pitch": 11,
          "teleport": True, "drop_east": True},
    "S+inw": {"seek": True, "jmps_slot": 25, "rom_rows": 111, "pitch": 11,
              "teleport": True, "drop_east": True, "in_west": 13},
}

#: the full stack, re-folded — 121..123 are literal holes, so the candidates are
#: 111..120 and 124..130.
_STACK = {"seek": True, "jmps_slot": 25, "pitch": 11, "teleport": True,
          "drop_east": True, "in_west": 13}
for _r in (111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 124, 126, 128, 130):
    VARIANTS[f"S{_r}"] = dict(_STACK, rom_rows=_r)
for _s in (26, 27):
    VARIANTS[f"S119.j{_s}"] = dict(_STACK, rom_rows=119, jmps_slot=_s)
for _ops in (("JMPF", "BRZ"), ("JMPF", "BRZ", "BRN")):
    VARIANTS["S119.ops" + str(len(_ops))] = dict(_STACK, rom_rows=119, seek_ops=_ops)
# the knobs, taken back off the stack one at a time at the chosen fold
VARIANTS["S119-tp"] = dict(_STACK, rom_rows=119, teleport=False)
VARIANTS["S119-de"] = dict(_STACK, rom_rows=119, drop_east=False)
VARIANTS["S119-inw"] = dict(_STACK, rom_rows=119, in_west=None)
VARIANTS["S119-pitch"] = dict(_STACK, rom_rows=119, pitch=None)


def _apply(M, ov: dict[str, object]):
    """Set the overlay, returning a thunk that puts every registry back."""
    undo: list = []

    def pin_dict(reg, key, val):
        d = getattr(M, reg)
        had = key in d
        old = d.get(key)
        if val is None:
            d.pop(key, None)
        else:
            d[key] = val
        undo.append(lambda: (d.__setitem__(key, old) if had else d.pop(key, None)))

    def pin_set(reg, key, on):
        s = getattr(M, reg)
        had = key in s
        if on:
            s.add(key)
        else:
            s.discard(key)
        undo.append(lambda: (s.add(key) if had else s.discard(key)))

    if "rom_rows" in ov:
        pin_dict("SEEK_TIER_LAYOUT", KEY, {"rom_rows": ov["rom_rows"]})
    if "mem_pad" in ov:
        pin_dict("MEM_PAD_FOR", KEY, ov["mem_pad"])
    if "pitch" in ov:
        pin_dict("SEEK_SLAB_PITCH", SLUG, ov["pitch"])
    if "classic_pitch" in ov:
        pin_dict("SLAB_PITCH", SLUG, ov["classic_pitch"])
    if "classic_rom_rows" in ov:
        tl = dict(M.TIER_LAYOUT[KEY])
        tl["rom_rows"] = ov["classic_rom_rows"]
        pin_dict("TIER_LAYOUT", KEY, tl)
    if "teleport" in ov:
        pin_set("SEEK_TELEPORT", KEY, bool(ov["teleport"]))
    if "drop_east" in ov:
        pin_set("SEEK_TAKEN_DROP_EAST", KEY, bool(ov["drop_east"]))
    if "in_west" in ov:
        pin_dict("INPUT_NORTH_WEST", KEY, ov["in_west"])
    if "seek_ops" in ov:
        pin_dict("SEEK_OPS_FOR", SLUG, ov["seek_ops"])
    if "jmps_slot" in ov:
        # A seek build grows a 22nd lane and the shipped map names 21, so it has
        # to be named or nothing builds.  Rank-preserving and unused: 25, 26, 27
        # (between JMPF's 24 and SND's 28); all three are two-digit opcodes, so
        # the drum pays the same 345 cells whichever is picked and only the trie
        # can tell them apart.  Inert for a classic build — ``_relabel_slots``
        # filters names the build does not use.
        slots = dict(M.OPCODE_SLOTS[KEY])
        slots["JMPS"] = int(ov["jmps_slot"])
        pin_dict("OPCODE_SLOTS", KEY, slots)

    def restore():
        for fn in reversed(undo):
            fn()

    return restore


def setup():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    return hires, M, prog


def tour(hires, n: int):
    cmds = list(hires.WALK[: n - 1])
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    return inp, frames, len(rounds)


def build_one(M, prog, ov: dict[str, object]):
    restore = _apply(M, ov)
    try:
        # ``seek=None`` is "ask the registry", which is what the shipped machine
        # does; every sweep variant states it explicitly instead.
        seek = ov.get("seek")
        return M.build_for(SLUG, program=prog, store="taped",
                           seek=None if seek is None else bool(seek))
    finally:
        restore()


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "build"
    hires, M, prog = setup()
    print(f"P={prog.P} tape={M.TAPE_SIZE[SLUG]}", flush=True)

    if mode == "fold":
        lo, hi, step = (int(x) for x in argv[1:4])
        seek = argv[4] != "classic" if len(argv) > 4 else True
        extra = eval(argv[5]) if len(argv) > 5 else {}  # noqa: S307 - local probe
        for rows in range(lo, hi + 1, step):
            ov = dict(extra)
            ov["seek"] = seek
            ov["rom_rows" if seek else "classic_rom_rows"] = rows
            t0 = time.time()
            try:
                m = build_one(M, prog, ov)
            except Exception as exc:  # noqa: BLE001
                print(f"  rows={rows:>4}: FAIL {type(exc).__name__}: "
                      f"{str(exc)[:150]}  ({time.time() - t0:.0f}s)", flush=True)
                continue
            print(f"  rows={rows:>4}: {m.width}x{m.height} area2={max(m.width, m.height)**2:,}"
                  f" pad={m.mem_pad}  ({time.time() - t0:.0f}s)", flush=True)
        return 0

    if mode == "lit":
        # A fold that builds is not a fold that runs: the packed words' *reverse*
        # reading has to be a signed 64-bit value too, and some folds pack a
        # literal that is not.  Constructing FastLittleman is where that is
        # caught, and it is ~1s against the tour's ~30, so gate the fold sweep on
        # it before spending a run on anything.
        from randomfun2026solvers.fast_littleman import FastLittleman

        lo, hi, step = (int(x) for x in argv[1:4])
        extra = eval(argv[4]) if len(argv) > 4 else {}  # noqa: S307 - local probe
        for rows in range(lo, hi + 1, step):
            ov = dict(extra, seek=True, rom_rows=rows)
            t0 = time.time()
            try:
                m = build_one(M, prog, ov)
            except Exception as exc:  # noqa: BLE001
                print(f"  rows={rows:>4}: BUILD {type(exc).__name__}: {str(exc)[:90]}",
                      flush=True)
                continue
            try:
                FastLittleman("\n".join(m.rows))
            except Exception as exc:  # noqa: BLE001
                print(f"  rows={rows:>4}: {m.width}x{m.height} pad={m.mem_pad} "
                      f"NO-RUN {type(exc).__name__}: {str(exc)[:70]}"
                      f"  ({time.time() - t0:.0f}s)", flush=True)
                continue
            print(f"  rows={rows:>4}: {m.width}x{m.height} pad={m.mem_pad} ok"
                  f"  ({time.time() - t0:.0f}s)", flush=True)
        return 0

    if mode == "build":
        for name in argv[1:] or list(VARIANTS):
            ov = VARIANTS[name]
            t0 = time.time()
            try:
                m = build_one(M, prog, ov)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:>16}: FAIL {type(exc).__name__}: {str(exc)[:200]}"
                      f"  ({time.time() - t0:.0f}s)", flush=True)
                continue
            print(f"  {name:>16}: {m.width}x{m.height} pad={m.mem_pad}"
                  f"  ({time.time() - t0:.0f}s)", flush=True)
        return 0

    if mode == "run":
        from randomfun2026solvers.fast_littleman import FastLittleman

        n = int(argv[1])
        inp, frames, nr = tour(hires, n)
        print(f"tour {nr} rounds", flush=True)
        results: dict[str, tuple[int, str]] = {}
        for name in argv[2:] or list(VARIANTS):
            ov = VARIANTS[name]
            t0 = time.time()
            try:
                m = build_one(M, prog, ov)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:>16}: BUILD FAILED — {type(exc).__name__}: "
                      f"{str(exc)[:200]}", flush=True)
                continue
            box = f"{m.width}x{m.height}"
            try:
                eng = FastLittleman("\n".join(m.rows))
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:>16}: {box} NO-RUN — {type(exc).__name__}: "
                      f"{str(exc)[:120]}", flush=True)
                continue
            res = eng.run(
                inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
            if res.fatal or res.passed is not True:
                print(f"  {name:>16}: {box} RUN FAILED — fatal={res.fatal} "
                      f"passed={res.passed} at {res.step:,}  ({time.time() - t0:.0f}s)",
                      flush=True)
                continue
            walk = res.frame_ticks[-1] - res.frame_ticks[0]
            results[name] = (walk, box)
            vs = ""
            if "base" in results and name != "base":
                b = results["base"][0]
                vs = f"  {walk - b:+,} = {100.0 * (walk - b) / b:+.3f}%"
            print(f"  {name:>16}: {box} pad={m.mem_pad} walk={walk:,}{vs}"
                  f"  ({time.time() - t0:.0f}s)", flush=True)
        return 0

    raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
