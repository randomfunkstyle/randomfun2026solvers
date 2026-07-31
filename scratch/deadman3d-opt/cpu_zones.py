#!/usr/bin/env python3
"""CPU debug view with **read/write binding zones** — where an `r`/`s` may move to.

§7.1 binds an ``r`` to the Manhattan-nearest *incoming* pipe touch and an ``s`` to
the nearest *outgoing* one, **ties broken by reading order** (``SPEC.md:183``).
So "can I move this glyph?" is exactly "does it stay inside its pipe's zone?",
and the zones are Manhattan Voronoi cells over the CPU box.

That question has driven most of this machine's geometry work: `mem_pad` is a
§7.1 floor, `ROM_TOUCH_DROP` moves the ROM touch to buy binding room, and three
separate "free" compactions turned out to cost pad columns. This renders the
answer instead of rediscovering it per experiment.

Two overlays, each toggled by a checkbox:

* **READ zones** — one colour per incoming pipe (`rom`, `in`, `mem_resp`,
  stream-resp). An ``r`` anywhere in a zone binds that pipe.
* **WRITE zones** — one per outgoing pipe (`mem_req`, `out`, `cmd`, display and
  stream bands). An ``s`` anywhere in a zone binds that pipe.

**Tie cells are hatched** — a tie is *decidable*, not fatal, and an amber cell is
one the intended pipe wins on reading order. ``check_bindings`` used to refuse
these outright, which made the builder strictly stronger than the machine and
cost men-v3 a whole ``mem_pad`` column; it now applies the engines' own key.
Hatching still marks a **one-cell margin**: any geometry move can flip which
attach reads first, and the failure mode is a wrong frame, not an exception.

    uv run python scratch/deadman3d-opt/cpu_zones.py [out_dir]
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"

#: Distinct hues; `rom` and `mem_resp` deliberately far apart — they are the pair
#: whose tie is the `mem_pad` floor, so their boundary is the one worth seeing.
PALETTE = [
    "#8b5cf6", "#0ea5e9", "#22c55e", "#f59e0b", "#ec4899",
    "#14b8a6", "#a855f7", "#ef4444", "#84cc16", "#06b6d4",
]


def capture(store: str):
    """Build, and grab the touches from the `check_bindings` call that survived.

    `build_for` sweeps `mem_pad` over `range(0, 40)` and calls this hook once per
    trial, keeping the smallest footprint — so the **first call that did not
    raise** is the grid that shipped. `seen[-1]` is pad 39, a machine that was
    never built; reading it reports margins that do not exist (it is where a
    phantom "margin-1 `s` at (39,198)" came from).
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    seen: list[tuple] = []
    real = M.check_bindings

    def spy(glyphs, touches):
        real(glyphs, touches)  # raises on a doomed pad; only survivors get recorded
        seen.append((list(glyphs), dict(touches)))

    M.check_bindings = spy
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    finally:
        M.check_bindings = real
    if not seen:
        raise SystemExit("check_bindings never bound")
    glyphs, touches = seen[0]
    return m, glyphs, touches, M


def zones(touches: dict, names: list[str], x0, x1, y0, y1):
    """Manhattan-nearest touch per cell; ``None`` where two are equidistant."""
    out = {}
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            # reading order: top to bottom, left to right (SPEC.md:183)
            ranked = sorted(
                names, key=lambda n: (abs(touches[n][0] - x) + abs(touches[n][1] - y),
                                      touches[n][1], touches[n][0])
            )
            best = ranked[0]
            bd = abs(touches[best][0] - x) + abs(touches[best][1] - y)
            tie = sum(
                1 for n in names
                if abs(touches[n][0] - x) + abs(touches[n][1] - y) == bd
            ) > 1
            out[(x, y)] = (best, tie)
    return out


def render(m, glyphs, touches, M, store: str, path: Path) -> None:
    from randomfun2026solvers.lm1.machine import Band

    cpu = [b for n, b in m.regions.items() if n.startswith("cpu")]
    x0 = min(b[0] for b in cpu) - 1
    x1 = max(b[0] + b[2] - 1 for b in cpu) + 1
    y0 = min(b[1] for b in cpu) - 1
    y1 = max(b[1] + b[3] - 1 for b in cpu) + 1

    incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
    reads = sorted([n for n in touches if n in incoming], key=str)
    writes = sorted([n for n in touches if n not in incoming], key=str)
    colour = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(reads + writes)}

    rz = zones(touches, reads, x0, x1, y0, y1)
    wz = zones(touches, writes, x0, x1, y0, y1)

    # the r/s glyphs themselves, so you can see which zone each one sits in
    rs = {(x, y): g for x, y, g, _b in glyphs if g in "rs"}

    cells = []
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            ch = m.rows[y][x] if y < len(m.rows) and x < len(m.rows[y]) else " "
            (r, rt), (w, wt) = rz[(x, y)], wz[(x, y)]
            cls = ["c", "r-" + str(r).replace(".", "_"), "w-" + str(w).replace(".", "_")]
            if rt:
                cls.append("rtie")
            if wt:
                cls.append("wtie")
            if (x, y) in rs:
                cls.append("glyph")
            title = (f"({x},{y}) {ch!r}  read->{r}{' [TIE, won by reading order]' if rt else ''}"
                     f"  write->{w}{' [TIE, won by reading order]' if wt else ''}")
            row.append(
                f'<span class="{" ".join(cls)}" title="{html.escape(title)}">'
                f"{html.escape(ch) if ch.strip() else '&nbsp;'}</span>"
            )
        cells.append("".join(row))

    css_r = "\n".join(
        f".zr .r-{str(n).replace('.', '_')} {{ background: {colour[n]}33; }}" for n in reads
    )
    css_w = "\n".join(
        f".zw .w-{str(n).replace('.', '_')} {{ background: {colour[n]}33; }}" for n in writes
    )
    legend = "".join(
        f'<span class="key"><i style="background:{colour[n]}"></i>{html.escape(str(n))}'
        f" <tt>{touches[n]}</tt></span>"
        for n in reads + writes
    )

    grid = "\n".join(f'<div class="row">{r}</div>' for r in cells)
    doc = f"""<!doctype html><meta charset="utf-8">
<title>{SLUG} CPU — {store} — binding zones</title>
<style>
 body {{ font: 13px/1.4 system-ui, sans-serif; margin: 18px; background:#fafafa; color:#1c1917 }}
 h1 {{ font-size: 15px; margin: 0 0 2px }}
 .sub {{ color:#57534e; margin-bottom:10px }}
 .grid {{ font: 12px/1.05 ui-monospace, Menlo, monospace; white-space: pre;
          display:inline-block; background:#fff; padding:8px; border:1px solid #e7e5e4 }}
 .row {{ height: 1.05em }}
 .c {{ display:inline-block; width: 1ch }}
 .glyph {{ outline: 1px solid #1c1917; font-weight: 700 }}
 .zr .rtie, .zw .wtie {{
   background-image: repeating-linear-gradient(45deg, #f59e0b88 0 3px, transparent 3px 6px) }}
 {css_r}
 {css_w}
 .keys {{ margin: 8px 0 }}
 .key {{ display:inline-block; margin-right:12px; white-space:nowrap }}
 .key i {{ display:inline-block; width:10px; height:10px; margin-right:4px; border-radius:2px }}
 label {{ margin-right:14px; user-select:none }}
 tt {{ color:#78716c }}
</style>
<h1>{SLUG} — {store} — {m.width}×{m.height}, CPU x={x0}..{x1} y={y0}..{y1}</h1>
<div class="sub">§7.1 binds an <tt>r</tt> to the Manhattan-nearest <b>incoming</b>
pipe and an <tt>s</tt> to the nearest <b>outgoing</b> one, <b>ties by reading
order</b> (top to bottom, left to right). A glyph may move freely inside its own
zone; crossing a boundary rebinds it. Hatched cells are ties — legal, but a
one-cell margin, so a glyph parked there rebinds if any geometry shifts. Boxed glyphs are the actual
<tt>r</tt>/<tt>s</tt> cells. Hover any cell for its bindings.</div>
<div>
 <label><input type="checkbox" id="cr" checked> Show READ zones</label>
 <label><input type="checkbox" id="cw"> Show WRITE zones</label>
</div>
<div class="keys">{legend}</div>
<div class="grid zr" id="g">{grid}</div>
<script>
 const g = document.getElementById('g');
 const sync = () => {{
   g.classList.toggle('zr', document.getElementById('cr').checked);
   g.classList.toggle('zw', document.getElementById('cw').checked);
 }};
 document.getElementById('cr').addEventListener('change', sync);
 document.getElementById('cw').addEventListener('change', sync);
 sync();
</script>
"""
    path.write_text(doc, encoding="utf-8")

    nr = sum(1 for _n, t in rz.values() if t)
    nw = sum(1 for _n, t in wz.values() if t)
    tot = (x1 - x0 + 1) * (y1 - y0 + 1)
    print(f"{store:>7}: {path}  ({path.stat().st_size:,} bytes)")
    print(f"          {len(reads)} read zones, {len(writes)} write zones; "
          f"tie cells {nr}/{tot} read, {nw}/{tot} write "
          f"(decided by reading order; legal, but a one-cell margin)")


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else REPO / "littleman" / "examples" / "local"
    out.mkdir(parents=True, exist_ok=True)
    for store, stem in (("men-v3", "deadman-3d_hires_men_v3"), ("taped", "deadman-3d_hires")):
        m, glyphs, touches, M = capture(store)
        render(m, glyphs, touches, M, store, out / f"{stem}.zones.debug.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
