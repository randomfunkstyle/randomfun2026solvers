"""Unified search harness for ``deadman-3d_hires`` men-v3 — the shared core.

One parameter vector, three ways to judge it, in increasing cost:

===============  ========  =====================================================
stage            cost      what it rules on
===============  ========  =====================================================
``predict``      ~10us     §7.1 binding, from a geometry model, no builder at all
``capture``      ~5s       §7.1 binding, from the **real** builder, aborted at
                           the first ``check_bindings``
``build``        ~21s      the whole grid: bindings, room placement, assembly
``gate``         ~150s     21 rounds, ``passed`` and the tick count
===============  ========  =====================================================

The point of the ladder is the ratio.  A model evaluation is 2 million times
cheaper than a gate, so a search can afford to enumerate the whole cross-product
and spend the builder only on what survives.

**Why the geometry is modelled and the rule is not.**  ``check_bindings`` is
twelve lines of exact integer arithmetic (:func:`verdict` restates it), so there
is nothing to approximate there — every error in the binding model is an error in
predicting *where the glyphs and the touches land*, which is what
:mod:`hz_geom` calibrates and :mod:`hz_validate` measures.  Keeping the two apart
means a wrong verdict is always attributable to one or the other.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
if str(WT / "solvers" / "python") not in sys.path:
    sys.path.insert(0, str(WT / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
STORE = "men-v3"
KEY = (SLUG, STORE)

# ── the parameter vector ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class P:
    """One point in the layout parameter space.

    Defaults are **the shipped 52dbadf men-v3 configuration**, read back from the
    registries by :func:`shipped` rather than transcribed, so a lever landed by
    another agent moves this baseline without anyone editing it.  New levers are
    added as fields here and to :func:`apply`; nothing else in the harness needs
    to know they exist.
    """

    # geometry, integer-valued
    squash_band: int = 7
    rom_touch_drop: int = 7
    lane_pitch: int = 1
    rom_rows: int = 119
    store_dx: int = 0
    store_dy: int = 10
    mem_pad: int | None = None        # None = let build_for pick the smallest
    seek_slab_pitch: int = 11
    store_cols: int = 15
    store_rows: int = 61
    # on/off levers
    straight_trie: bool = True
    folded_lanes: bool = True
    tucked_drops: bool = True
    seek_taken_drop_east: bool = True
    seek_tight_struct_drops: bool = True
    # coupled pair; None = whatever the registry falls back to today
    lane_order: tuple[str, ...] | None = None
    opcode_slots: tuple[tuple[str, int], ...] | None = None

    def key(self) -> tuple:
        return tuple(sorted(self.__dict__.items()))

    def label(self, base: "P | None" = None) -> str:
        """Only what differs from ``base`` — a search log of 40-field dumps is noise."""
        base = base if base is not None else P()
        d = [f"{k}={v!r}" for k, v in self.__dict__.items()
             if v != getattr(base, k)]
        return ",".join(d) if d else "shipped"


def bump(p: P, **kw) -> P:
    return replace(p, **kw)


# ── the builder, set up once per process ─────────────────────────────────────

_STATE: dict = {}


def setup():
    """Assemble the program once.  Everything after this is layout, not code."""
    if "M" in _STATE:
        return _STATE["M"], _STATE["prog"], _STATE["hires"]
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    if not WAD.exists():
        raise RuntimeError(f"no IWAD at {WAD}; hires is WAD-derived and cannot build")
    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    _STATE.update(M=M, prog=assemble(hires.hires_source(), name=SLUG), hires=hires)
    return M, _STATE["prog"], hires


def shipped() -> P:
    """Read the registries, so the baseline is the machine and not a memory."""
    M, _, _ = setup()
    off = M.TIER_LAYOUT.get(KEY, {}).get("store_offset", (0, 0))
    shape = M.STORE_SHAPE.get(SLUG, (15, 61))
    return P(
        squash_band=M.SQUASH_BAND.get(KEY, 0),
        rom_touch_drop=M.ROM_TOUCH_DROP.get(KEY, 0),
        lane_pitch=M.LANE_PITCH.get(KEY, 2),
        rom_rows=int(M.SEEK_TIER_LAYOUT.get(KEY, {}).get("rom_rows", 119)),
        store_dx=off[0], store_dy=off[1],
        mem_pad=M.MEM_PAD_FOR.get(KEY),
        seek_slab_pitch=M.SEEK_SLAB_PITCH.get(SLUG, 11),
        store_cols=shape[0], store_rows=shape[1],
        straight_trie=KEY in M.STRAIGHT_TRIE,
        folded_lanes=KEY in M.FOLDED_LANES,
        tucked_drops=KEY in M.TUCKED_DROPS,
        seek_taken_drop_east=KEY in M.SEEK_TAKEN_DROP_EAST,
        seek_tight_struct_drops=KEY in M.SEEK_TIGHT_STRUCT_DROPS,
        lane_order=M.LANE_ORDER.get(SLUG),
        opcode_slots=(tuple(sorted(M.OPCODE_SLOTS[KEY].items()))
                      if KEY in M.OPCODE_SLOTS else None),
    )


def _set(reg, key, value):
    """Set or delete a registry entry, returning an undo thunk."""
    had = key in reg
    old = reg.get(key)

    def undo():
        if had:
            reg[key] = old
        else:
            reg.pop(key, None)
    if value is None:
        reg.pop(key, None)
    else:
        reg[key] = value
    return undo


def _member(reg, key, on):
    had = key in reg

    def undo():
        (reg.add if had else reg.discard)(key)
    (reg.add if on else reg.discard)(key)
    return undo


@contextmanager
def apply(p: P):
    """Install ``p`` into the live registries for the duration of the block.

    Registries are global mutable state shared with every other slug, so this is
    strictly scoped and always restored — a leaked ``STORE_SHAPE`` would move
    ``deadman-3d``'s checked-in grid and the byte-identity tests would blame the
    wrong change.
    """
    M, _, _ = setup()
    undos = [
        _set(M.SEEK_TIER_LAYOUT, KEY, {"rom_rows": p.rom_rows}),
        _set(M.TIER_LAYOUT, KEY, {"store_offset": (p.store_dx, p.store_dy)}),
        _set(M.MEM_PAD_FOR, KEY, p.mem_pad),
        _set(M.SEEK_SLAB_PITCH, SLUG, p.seek_slab_pitch),
        _set(M.STORE_SHAPE, SLUG, (p.store_cols, p.store_rows)),
        _set(M.LANE_ORDER, SLUG, p.lane_order),
        _set(M.OPCODE_SLOTS, KEY, dict(p.opcode_slots) if p.opcode_slots else None),
        _member(M.SEEK_TAKEN_DROP_EAST, KEY, p.seek_taken_drop_east),
        _member(M.SEEK_TIGHT_STRUCT_DROPS, KEY, p.seek_tight_struct_drops),
        _member(M.STRAIGHT_TRIE, KEY, p.straight_trie),
        _member(M.FOLDED_LANES, KEY, p.folded_lanes),
        _member(M.TUCKED_DROPS, KEY, p.tucked_drops),
        _set(M.SQUASH_BAND, KEY, p.squash_band),
        _set(M.ROM_TOUCH_DROP, KEY, p.rom_touch_drop),
        _set(M.LANE_PITCH, KEY, p.lane_pitch),
    ]
    try:
        yield M
    finally:
        for u in reversed(undos):
            u()


# ── §7.1, restated so a prediction and a build rule the same way ─────────────

INCOMING = {"rom", "in", "mem_resp", "stream_resp"}


def want_of(glyph: str, band: str) -> str:
    if band == "mem":
        return "mem_req" if glyph == "s" else "mem_resp"
    return band


def verdict(glyphs, touches) -> tuple[bool, str]:
    """``check_bindings`` as a predicate that explains itself instead of raising.

    Byte-for-byte the same arithmetic as :func:`machine.check_bindings`, including
    the two things that are easy to get wrong: rivals are only the touches on this
    glyph's side of the incoming/outgoing divide, and **a tie fails**.
    """
    for x, y, glyph, band in glyphs:
        want = want_of(glyph, band)
        rivals = {n: abs(px - x) + abs(py - y) for n, (px, py) in touches.items()
                  if (n in INCOMING) == (glyph == "r")}
        if want not in rivals:
            return False, f"{glyph!r} at {(x, y)} wants absent pipe {want!r}"
        best = min(rivals.values())
        if rivals[want] != best:
            near = min(rivals, key=lambda n: rivals[n])
            return False, (f"{glyph!r} at {(x, y)} wants {want} ({rivals[want]}) "
                           f"but {near} is nearer ({rivals[near]})")
        if sum(1 for d in rivals.values() if d == best) > 1:
            tied = sorted(n for n, d in rivals.items() if d == best)
            return False, f"{glyph!r} at {(x, y)} ties {tied} at {best}"
    return True, ""


# ── stage 2: the real builder, aborted the moment it has ruled ───────────────

@dataclass
class Geom:
    """The CPU's binding problem, per ``mem_pad``, as the builder posed it.

    ``build_for`` does not build one machine — it sweeps ``mem_pad`` over 0..39,
    keeps every pad whose pipes all bind, and ships the smallest footprint among
    them.  "Does this parameter vector bind" is therefore an **existential** over
    pads, not a single verdict, and a model that predicts one pad answers the
    wrong question.  That is the shape of the problem, and it is why the four
    ``r``/``s`` failures in the frequency-shaping search were so hard to call by
    hand: each candidate is forty binding problems.
    """

    #: pad -> (glyphs, touches); only the pads that reached ``check_bindings``
    pads: dict = field(default_factory=dict)
    #: pad -> the structural ``MachineError`` raised before §7.1 got a look in
    early: dict = field(default_factory=dict)
    #: pads whose every pipe binds, ascending
    good: tuple = ()
    binds: bool = False
    reason: str = ""
    err: str | None = None
    secs: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.pads) or bool(self.early)

    def at(self, pad):
        return self.pads.get(pad)


class _Abort(Exception):
    pass


_HZ = "hz: captured"


def capture(p: P) -> Geom:
    """Pose every pad's binding problem without drawing a single grid.

    ``check_bindings`` sits about two thirds of the way through ``_assemble``, so
    raising from it skips the ROM emission, the store, the pipes and the row
    rendering that follow — the whole pad sweep costs ~6s against the full
    build's ~21s, and every §7.1 verdict it yields is **exact**, because the
    glyphs and touches are the same tuples the production checker would rule on.

    What it cannot see is a structural failure that happens *after* §7.1 (room
    placement, pipe counting).  :func:`build` is the arbiter for those, and
    :mod:`hz_validate` measures how often the difference matters.
    """
    M, prog, _ = setup()
    g = Geom()
    real_check, real_asm = M.check_bindings, M._assemble
    cur = {"pad": None}

    def patched_asm(program, plan, words, tape_n, rom_rows, mem_pad, *a, **kw):
        cur["pad"] = mem_pad
        try:
            return real_asm(program, plan, words, tape_n, rom_rows, mem_pad, *a, **kw)
        except M.MachineError as exc:
            if str(exc) != _HZ:
                g.early.setdefault(mem_pad, str(exc)[:160])
            raise

    def patched_check(glyphs, touches):
        # A ``MachineError`` and not a private exception, because that is what the
        # pad loop is written to catch: it records this pad and moves to the next,
        # so one ``build_for`` yields all forty binding problems for the price of
        # the ~5s planning prefix they share.
        g.pads[cur["pad"]] = (list(glyphs), dict(touches))
        raise M.MachineError(_HZ)

    t0 = time.time()
    with apply(p):
        M.check_bindings = patched_check
        M._assemble = patched_asm
        try:
            M.build_for(SLUG, program=prog, store=STORE,
                        lane_pitch=p.lane_pitch, rom_touch_drop=p.rom_touch_drop,
                        squash_band=p.squash_band, straight_trie=p.straight_trie,
                        tuck_drops=p.tucked_drops, fold_lanes=p.folded_lanes)
        except M.MachineError:
            pass  # always: every pad was aborted, so nothing was ever "best"
        except Exception as exc:  # noqa: BLE001 - a failed placement is a datum
            g.err = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            M.check_bindings, M._assemble = real_check, real_asm
    g.secs = time.time() - t0
    good, why = [], {}
    for pad, (glyphs, touches) in sorted(g.pads.items()):
        ok, reason = verdict(glyphs, touches)
        (good.append(pad) if ok else why.setdefault(reason, pad))
    g.good, g.binds = tuple(good), bool(good)
    g.reason = "ok" if good else (
        "; ".join(list(why)[:2]) if why
        else ("; ".join(sorted(set(g.early.values()))[:1]) or "no pad reached §7.1"))
    return g


# ── stage 3: the whole grid ──────────────────────────────────────────────────

@dataclass
class Built:
    box: tuple[int, int] | None = None
    rows: list | None = None
    err: str | None = None
    secs: float = 0.0
    routes: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.err is None


def build(p: P) -> Built:
    """The full builder — bindings, rooms, pipes, ROM emission."""
    M, prog, _ = setup()
    b = Built()
    t0 = time.time()
    with apply(p):
        try:
            m = M.build_for(SLUG, program=prog, store=STORE,
                            lane_pitch=p.lane_pitch, rom_touch_drop=p.rom_touch_drop,
                            squash_band=p.squash_band, straight_trie=p.straight_trie,
                            tuck_drops=p.tucked_drops, fold_lanes=p.folded_lanes)
            b.box, b.rows = (m.width, m.height), m.rows
            b.routes = dict(m.route_lengths)
        except Exception as exc:  # noqa: BLE001
            b.err = f"{type(exc).__name__}: {str(exc)[:200]}"
    b.secs = time.time() - t0
    return b


# ── stage 3b: load, which is where "does not fit signed 64 bits" lives ───────

def loads(rows) -> tuple[bool, str]:
    """Does the engine accept this grid at all?

    Four of the nine builds that bound in the frequency-shaping search died here
    rather than at ``check_bindings`` — binding is necessary and not sufficient,
    so the ladder needs a rung between "built" and "gated".  Parsing a grid is
    ~0.1s against the gate's ~150s.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman
    try:
        FastLittleman("\n".join(rows))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


# ── stage 4: the gate ────────────────────────────────────────────────────────

_TOUR: dict[int, tuple] = {}


def tour(n=21):
    if n not in _TOUR:
        _, _, hires = setup()
        cmds = list(hires.WALK[: n - 1])
        rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
        _TOUR[n] = (" / ".join(" ".join(r["in"]) for r in rounds),
                    [r["frames"] for r in rounds])
    return _TOUR[n]


@dataclass
class Gated:
    ticks: int = 0
    passed: bool = False
    fatal: object = None
    secs: float = 0.0
    err: str | None = None


def gate(rows, rounds=21) -> Gated:
    """``res.frame_ticks[-1]`` at ``rounds``, with ``passed`` — AGENTS.md's one metric."""
    from randomfun2026solvers.fast_littleman import FastLittleman
    inp, frames = tour(rounds)
    g = Gated()
    t0 = time.time()
    try:
        res = FastLittleman("\n".join(rows)).run(
            inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000)
        g.ticks, g.passed, g.fatal = res.frame_ticks[-1], res.passed, res.fatal
    except Exception as exc:  # noqa: BLE001
        g.err = f"{type(exc).__name__}: {str(exc)[:160]}"
    g.secs = time.time() - t0
    return g


__all__ = ["Built", "Gated", "Geom", "KEY", "P", "SLUG", "STORE", "apply",
           "build", "bump", "capture", "gate", "loads", "setup", "shipped",
           "tour", "verdict", "want_of"]
