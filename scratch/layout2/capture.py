"""Point the solver at the **real** ``deadman-3d_hires``, not a transcription.

Phase 2's requirement is that the solver read live configuration rather than a
hand-kept list, because a hand-kept list is how ``TAPED_BANKS`` stayed empty for
weeks while costing 63%. So every number here comes from one of two places:

* the geometry — ``glyphs`` and ``touches`` — is **captured out of the running
  builder** by wrapping ``check_bindings``, so it is the same tuple the production
  checker rules on, including whatever the current registries happen to say;
* the configuration is ``scratch/deadman3d-opt/config.py``'s ``feature_set``,
  which walks ``dir(lm1.machine)`` generically.

Nothing is transcribed, so a registry edited tomorrow shows up here without anyone
remembering to update it. A capture that silently drifted from the machine it
claims to model would invalidate every interval this package computes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "solvers" / "python", REPO, REPO / "scratch" / "deadman3d-opt"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
STORE = "taped"
KEY = (SLUG, STORE)

#: what ``deadman3d_hires.hires_source()`` actually passes today, mirrored from
#: ``config.py``'s ``main`` so the digest is comparable with its output
PROGRAM_KNOBS = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True,
                     lap_via_jump=False)

_PROG: object | None = None


def program():
    """Assemble the hires program once — it is the expensive part of a build."""
    global _PROG
    if _PROG is None:
        from randomfun2026solvers import deadman3d as d3
        from randomfun2026solvers import deadman3d_hires as hires
        from randomfun2026solvers.lm1 import machine as M
        from randomfun2026solvers.lm1.asm import assemble

        if not WAD.exists():
            raise RuntimeError(f"no IWAD at {WAD}; hires is WAD-derived and cannot build")
        hires.install_wad(WAD)
        M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
        _PROG = assemble(hires.hires_source(), name=SLUG)
    return _PROG


@dataclass
class Capture:
    """One build's binding problem, plus what it was built from."""

    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    touches: dict[str, tuple[int, int]] = field(default_factory=dict)
    calls: int = 0
    box: tuple[int, int] | None = None
    error: str | None = None
    config: str = ""
    features: dict = field(default_factory=dict)
    #: room-H geometry, when ``_seek_teleport`` ran
    seek: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def capture(*, seek_teleport: bool | None = None, **overrides) -> Capture:
    """Build hires under ``overrides`` and return its CPU binding problem.

    ``overrides`` go straight to ``build_for`` (``lane_pitch``, ``rom_touch_drop``,
    ``squash_band``, ...). The build is allowed to fail: the capture is taken
    *before* ``check_bindings`` rules, so a failing placement is still readable —
    which is the case a repair is needed for.

    ``seek_teleport`` toggles the registry membership for the duration, because
    :data:`SEEK_TELEPORT` is a set rather than a ``build_for`` argument, and the
    recorded ``-0.243%`` was measured with it removed.
    """
    from config import digest, feature_set  # scratch/deadman3d-opt/config.py
    from randomfun2026solvers.lm1 import machine as M

    prog = program()
    had = KEY in M.SEEK_TELEPORT
    if seek_teleport is not None:
        (M.SEEK_TELEPORT.add if seek_teleport else M.SEEK_TELEPORT.discard)(KEY)
    cap = Capture()
    real_check = M.check_bindings
    real_seek = M._seek_teleport

    def patched_check(glyphs, touches):
        cap.calls += 1
        if "rom" in touches and not cap.glyphs:
            cap.glyphs = list(glyphs)
            cap.touches = dict(touches)
        return real_check(glyphs, touches)

    def patched_seek(g, *, cmd_y, src_x, x_e, rom_east, ry, y_b):
        hx0, hx1 = src_x + 1, x_e
        hy1 = y_b + 1
        hy0 = hy1 - (M._TELE_H + 1)
        blocked = [(x, y) for y in range(hy0, hy1 + 1)
                   for x in range(hx0, hx1 + 1) if (x, y) in g.c]
        rows: dict[int, int] = {}
        for _, y in blocked:
            rows[y] = rows.get(y, 0) + 1
        # ``floor_y``: the first occupied row at or above H's bottom, across H's
        # full width — the store block's underside as room H actually sees it.
        # This is the whole placement constraint: H needs ``hy1 - floor_y >= 4``
        # rows, and every candidate squash changes ``hy1`` while leaving
        # ``floor_y`` where it is, because the store is anchored to ``CY``.
        floor_y = None
        for y in range(hy1, max(0, hy1 - 400), -1):
            if any((x, y) in g.c for x in range(hx0, hx1 + 1)):
                floor_y = y
                break
        cap.seek = dict(cmd_y=cmd_y, y_b=y_b, band=(hx0, hy0, hx1, hy1),
                        blocked=len(blocked), floor_y=floor_y,
                        available=None if floor_y is None else hy1 - floor_y,
                        rows={k: rows[k] for k in sorted(rows)})
        return real_seek(g, cmd_y=cmd_y, src_x=src_x, x_e=x_e,
                         rom_east=rom_east, ry=ry, y_b=y_b)

    M.check_bindings = patched_check
    M._seek_teleport = patched_seek
    try:
        f = feature_set(SLUG, STORE, **PROGRAM_KNOBS)
        cap.features, cap.config = f, digest(f)
        m = M.build_for(SLUG, program=prog, store=STORE, **overrides)
        cap.box = (m.width, m.height)
    except Exception as exc:  # noqa: BLE001
        cap.error = f"{type(exc).__name__}: {exc}"
    finally:
        M.check_bindings = real_check
        M._seek_teleport = real_seek
        if seek_teleport is not None:
            (M.SEEK_TELEPORT.add if had else M.SEEK_TELEPORT.discard)(KEY)
    return cap


def rom_wanting(cap: Capture) -> list[tuple[int, int, str, str]]:
    """The glyphs that must bind ``rom`` — the twelve of ``ROM_TOUCH_DROP``."""
    return [g for g in cap.glyphs if g[2] == "r" and g[3] == "rom"]


__all__ = ["Capture", "KEY", "SLUG", "STORE", "capture", "program", "rom_wanting"]
