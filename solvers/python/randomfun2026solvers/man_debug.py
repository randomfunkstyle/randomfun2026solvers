#!/usr/bin/env python3
"""Debug metadata and HTML overlays for generated Littleman programs.

The generated ``.man`` grid cannot carry comments.  This module keeps a sidecar
map of named regions and lanes so generators can document their own geometry in
coordinates that are useful while tracing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
import json
from pathlib import Path
from typing import Iterable, Literal


Point = tuple[int, int]


def _cells_of(points: list[Point]) -> list[Point]:
    cells: list[Point] = []
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:])):
        if x0 != x1 and y0 != y1:
            raise ValueError(f"non-rectilinear segment {(x0, y0)} -> {(x1, y1)}")
        sx = (x1 > x0) - (x1 < x0)
        sy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        if i == 0:
            cells.append((x, y))
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
    return cells


@dataclass(frozen=True)
class Region:
    name: str
    x: int
    y: int
    w: int
    h: int
    note: str = ""
    color: str = "#3b82f6"
    tags: list[str] = field(default_factory=list)

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass(frozen=True)
class CircleRegion:
    name: str
    cx: int
    cy: int
    r: int
    note: str = ""
    color: str = "#22c55e"
    tags: list[str] = field(default_factory=list)

    def contains(self, x: int, y: int) -> bool:
        return (x - self.cx) ** 2 + (y - self.cy) ** 2 <= self.r**2


@dataclass(frozen=True)
class Lane:
    name: str
    points: list[Point]
    note: str = ""
    color: str = "#f97316"
    kind: Literal["control", "pipe", "data", "expected"] = "control"
    expect: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def cells(self) -> list[Point]:
        return _cells_of(self.points)

    def contains(self, x: int, y: int) -> bool:
        return (x, y) in set(self.cells)


@dataclass(frozen=True)
class TraceScenario:
    """A reproducible emulator setup for a marked machine."""

    name: str
    input: str
    from_tick: int
    to_tick: int
    watch: list[str] = field(default_factory=list)
    note: str = ""


class DebugMap:
    def __init__(self, title: str, *, offset: Point = (0, 0)) -> None:
        self.title = title
        self.offset = offset
        self.regions: list[Region] = []
        self.circles: list[CircleRegion] = []
        self.lanes: list[Lane] = []
        self.scenarios: list[TraceScenario] = []

    def xy(self, x: int, y: int) -> Point:
        ox, oy = self.offset
        return ox + x, oy + y

    def points(self, pts: Iterable[Point]) -> list[Point]:
        return [self.xy(x, y) for x, y in pts]

    def region(
        self,
        name: str,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        note: str = "",
        color: str = "#3b82f6",
        tags: list[str] | None = None,
        local: bool = False,
    ) -> None:
        if local:
            x, y = self.xy(x, y)
        self.regions.append(Region(name, x, y, w, h, note, color, tags or []))

    def circle(
        self,
        name: str,
        cx: int,
        cy: int,
        r: int,
        *,
        note: str = "",
        color: str = "#22c55e",
        tags: list[str] | None = None,
        local: bool = False,
    ) -> None:
        if local:
            cx, cy = self.xy(cx, cy)
        self.circles.append(CircleRegion(name, cx, cy, r, note, color, tags or []))

    def lane(
        self,
        name: str,
        points: list[Point],
        *,
        note: str = "",
        color: str = "#f97316",
        kind: Literal["control", "pipe", "data", "expected"] = "control",
        expect: str = "",
        tags: list[str] | None = None,
        local: bool = False,
    ) -> None:
        pts = self.points(points) if local else points
        self.lanes.append(Lane(name, pts, note, color, kind, expect, tags or []))

    def scenario(
        self,
        name: str,
        input: str,
        from_tick: int,
        to_tick: int,
        *,
        watch: list[str] | None = None,
        note: str = "",
    ) -> None:
        self.scenarios.append(TraceScenario(name, input, from_tick, to_tick, watch or [], note))

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "offset": list(self.offset),
            "regions": [asdict(r) for r in self.regions],
            "circles": [asdict(c) for c in self.circles],
            "scenarios": [asdict(s) for s in self.scenarios],
            "lanes": [
                {
                    **asdict(l),
                    "points": [list(p) for p in l.points],
                    "cells": [list(p) for p in l.cells],
                }
                for l in self.lanes
            ],
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    def write_html(self, rows: list[str], path: str | Path) -> None:
        Path(path).write_text(render_html(rows, self), encoding="utf-8")

    def translated(self, dx: int, dy: int) -> "DebugMap":
        out = DebugMap(self.title, offset=(self.offset[0] + dx, self.offset[1] + dy))
        out.regions = [
            Region(r.name, r.x + dx, r.y + dy, r.w, r.h, r.note, r.color, r.tags)
            for r in self.regions
        ]
        out.circles = [
            CircleRegion(c.name, c.cx + dx, c.cy + dy, c.r, c.note, c.color, c.tags)
            for c in self.circles
        ]
        out.lanes = [
            Lane(
                l.name,
                [(x + dx, y + dy) for x, y in l.points],
                l.note,
                l.color,
                l.kind,
                l.expect,
                l.tags,
            )
            for l in self.lanes
        ]
        out.scenarios = list(self.scenarios)
        return out


def render_html(rows: list[str], dbg: DebugMap) -> str:
    w = max(len(r) for r in rows)
    h = len(rows)
    cell = 14
    pad = 24
    grid_lines = []
    for y, row in enumerate(rows):
        for x in range(w):
            ch = row[x] if x < len(row) else " "
            if ch != " ":
                grid_lines.append(
                    f'<text class="cell" x="{pad + x * cell}" y="{pad + y * cell}">{escape(ch)}</text>'
                )
    regions = []
    for r in dbg.regions:
        regions.append(
            f'<rect class="region debug-target" data-name="{escape(r.name)}" data-kind="region" '
            f'data-detail="{escape(r.note)}" x="{pad + r.x * cell - cell * .45}" '
            f'y="{pad + r.y * cell - cell * .9}" width="{r.w * cell}" height="{r.h * cell}" '
            f'style="--c:{escape(r.color)}"><title>{escape(r.name)}: {escape(r.note)}</title></rect>'
        )
        regions.append(
            f'<text class="label debug-target" data-name="{escape(r.name)}" data-kind="region" '
            f'data-detail="{escape(r.note)}" x="{pad + r.x * cell}" y="{pad + r.y * cell - cell}">{escape(r.name)}</text>'
        )
    for c in dbg.circles:
        regions.append(
            f'<circle class="region circle-region debug-target" data-name="{escape(c.name)}" data-kind="boundary" '
            f'data-detail="{escape(c.note)}" '
            f'cx="{pad + c.cx * cell}" cy="{pad + c.cy * cell - cell * .35}" r="{c.r * cell}" '
            f'style="--c:{escape(c.color)}"><title>{escape(c.name)}: {escape(c.note)}</title></circle>'
        )
        regions.append(
            f'<text class="label debug-target" data-name="{escape(c.name)}" data-kind="boundary" '
            f'data-detail="{escape(c.note)}" x="{pad + (c.cx - c.r) * cell}" y="{pad + (c.cy - c.r) * cell - cell}">'
            f'{escape(c.name)}</text>'
        )
    lanes = []
    for l in dbg.lanes:
        pts = " ".join(f"{pad + x * cell},{pad + y * cell - cell * .35}" for x, y in l.points)
        detail = l.expect or l.note
        lanes.append(
            f'<polyline class="lane {escape(l.kind)} debug-target" data-name="{escape(l.name)}" data-kind="{escape(l.kind)} lane" '
            f'data-detail="{escape(detail)}" '
            f'points="{pts}" style="--c:{escape(l.color)}"><title>{escape(l.name)}: '
            f'{escape(detail)}</title></polyline>'
        )
        if l.points:
            x, y = l.points[0]
            lanes.append(
                f'<text class="lane-label debug-target" data-name="{escape(l.name)}" data-kind="{escape(l.kind)} lane" '
                f'data-detail="{escape(detail)}" x="{pad + x * cell}" y="{pad + y * cell - cell * 1.3}">'
                f'{escape(l.name)}</text>'
            )
    legend_items = [
        (x.name, "region", x.note, x.color) for x in dbg.regions
    ] + [
        (x.name, "boundary", x.note, x.color) for x in dbg.circles
    ] + [
        (x.name, f"{x.kind} lane", x.expect or x.note, x.color) for x in dbg.lanes
    ]
    legend = "\n".join(
        f'<li class="debug-target" data-name="{escape(name)}" data-kind="{escape(kind)}" '
        f'data-detail="{escape(detail)}"><span style="background:{escape(color)}"></span><b>{escape(name)}</b> '
        f'{escape(detail)}</li>'
        for name, kind, detail, color in legend_items
    )
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>{escape(dbg.title)}</title>
<style>
body {{ margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; background: #111827; color: #e5e7eb; }}
.wrap {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; height: 100vh; }}
.canvas {{ overflow: auto; padding: 16px; position: relative; }}
.zoom-toolbar {{ position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 6px; width: max-content; margin-bottom: 10px; padding: 6px; border: 1px solid #374151; background: #0b1120; }}
.zoom-toolbar button {{ width: 28px; height: 28px; padding: 0; border: 1px solid #64748b; border-radius: 3px; background: #111827; color: #e5e7eb; font: 16px ui-monospace, monospace; cursor: pointer; }}
.zoom-toolbar button:hover {{ background: #1e293b; }}
.zoom-toolbar output {{ min-width: 42px; color: #cbd5e1; font: 12px ui-monospace, monospace; text-align: center; }}
.zoom-stage {{ position: relative; }}
svg {{ display: block; background: #030712; border: 1px solid #374151; transform-origin: top left; }}
.cell {{ fill: #e5e7eb; font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; text-anchor: middle; }}
.region {{ fill: color-mix(in srgb, var(--c), transparent 88%); stroke: var(--c); stroke-width: .75; }}
.circle-region {{ stroke-dasharray: 3.5 2; }}
.lane {{ fill: none; stroke: var(--c); stroke-width: 1.5; opacity: .88; marker-end: url(#arrow); }}
.lane.pipe {{ stroke-dasharray: 2 1.5; }}
.lane.expected {{ stroke-width: 2.5; opacity: .5; }}
.label, .lane-label {{ fill: #f9fafb; font: 6.5px ui-monospace, SFMono-Regular, Menlo, monospace; paint-order: stroke; stroke: #030712; stroke-width: 2px; }}
.debug-target {{ cursor: pointer; }}
svg .debug-target.is-active {{ filter: brightness(1.8); opacity: 1; stroke-width: 2; }}
svg text.debug-target.is-active {{ fill: #fff; font-weight: 700; }}
li.debug-target {{ cursor: pointer; border-left: 3px solid transparent; padding-left: 8px; }}
li.debug-target.is-active {{ background: #172554; border-left-color: #e5e7eb; }}
aside {{ overflow: auto; border-left: 1px solid #374151; padding: 16px; background: #0b1120; }}
li {{ margin: 0 0 10px; line-height: 1.35; }}
span {{ display: inline-block; width: 10px; height: 10px; margin-right: 8px; }}
#tooltip {{ position: fixed; z-index: 10; max-width: 340px; pointer-events: none; opacity: 0; transform: translate(14px, 14px); padding: 10px 12px; border: 1px solid #64748b; border-radius: 4px; background: #020617; box-shadow: 0 12px 30px #0008; transition: opacity .1s; }}
#tooltip.visible {{ opacity: 1; }}
#tooltip strong, #tooltip small, #tooltip p {{ display: block; margin: 0; }}
#tooltip small {{ margin-top: 2px; color: #94a3b8; text-transform: uppercase; }}
#tooltip p {{ margin-top: 6px; color: #e2e8f0; }}
</style>
<div class="wrap">
<div class="canvas">
<div class="zoom-toolbar" aria-label="Diagram zoom">
<button type="button" id="zoom-out" title="Zoom out" aria-label="Zoom out">-</button>
<output id="zoom-value">100%</output>
<button type="button" id="zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
<button type="button" id="zoom-reset" title="Reset zoom" aria-label="Reset zoom">1:1</button>
</div>
<div class="zoom-stage" id="zoom-stage">
<svg id="diagram" width="{pad * 2 + w * cell}" height="{pad * 2 + h * cell}" viewBox="0 0 {pad * 2 + w * cell} {pad * 2 + h * cell}">
<defs><marker id="arrow" markerWidth="2" markerHeight="2" refX="1.5" refY=".75" orient="auto"><path d="M0,0 L0,1.5 L1.75,.75 z" fill="#f9fafb"/></marker></defs>
{''.join(regions)}
{''.join(lanes)}
{''.join(grid_lines)}
</svg>
</div>
</div>
<aside>
<h1>{escape(dbg.title)}</h1>
<p>{w} x {h}</p>
<ul>{legend}</ul>
</aside>
</div>
<div id="tooltip" role="tooltip"><strong></strong><small></small><p></p></div>
<script>
const targets = [...document.querySelectorAll('.debug-target')];
const tooltip = document.querySelector('#tooltip');
const title = tooltip.querySelector('strong');
const kind = tooltip.querySelector('small');
const detail = tooltip.querySelector('p');
let pinned = null;
const canvas = document.querySelector('.canvas');
const stage = document.querySelector('#zoom-stage');
const diagram = document.querySelector('#diagram');
const zoomValue = document.querySelector('#zoom-value');
const baseWidth = {pad * 2 + w * cell};
const baseHeight = {pad * 2 + h * cell};
let zoom = 1;
let pan = null;

function setZoom(next) {{
  zoom = Math.min(3, Math.max(.35, next));
  diagram.style.transform = `scale(${{zoom}})`;
  stage.style.width = `${{baseWidth * zoom}}px`;
  stage.style.height = `${{baseHeight * zoom}}px`;
  zoomValue.value = `${{Math.round(zoom * 100)}}%`;
  zoomValue.textContent = zoomValue.value;
}}
document.querySelector('#zoom-in').addEventListener('click', () => setZoom(zoom * 1.25));
document.querySelector('#zoom-out').addEventListener('click', () => setZoom(zoom / 1.25));
document.querySelector('#zoom-reset').addEventListener('click', () => setZoom(1));
canvas.addEventListener('wheel', (event) => {{
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  setZoom(zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
}}, {{ passive: false }});
stage.addEventListener('pointerdown', (event) => {{
  if (event.button !== 0 || event.target.closest('.debug-target')) return;
  pan = {{ x: event.clientX, y: event.clientY, left: canvas.scrollLeft, top: canvas.scrollTop }};
  stage.setPointerCapture(event.pointerId);
  event.preventDefault();
}});
stage.addEventListener('pointermove', (event) => {{
  if (!pan) return;
  canvas.scrollLeft = pan.left - (event.clientX - pan.x);
  canvas.scrollTop = pan.top - (event.clientY - pan.y);
}});
stage.addEventListener('pointerup', () => {{ pan = null; }});
stage.addEventListener('pointercancel', () => {{ pan = null; }});
setZoom(1);

function setActive(name) {{
  for (const target of targets) target.classList.toggle('is-active', target.dataset.name === name);
}}
function clearActive() {{
  if (!pinned) {{
    for (const target of targets) target.classList.remove('is-active');
    tooltip.classList.remove('visible');
  }}
}}
function show(target, event) {{
  title.textContent = target.dataset.name;
  kind.textContent = target.dataset.kind;
  detail.textContent = target.dataset.detail || 'No additional note.';
  tooltip.style.left = event.clientX + 'px';
  tooltip.style.top = event.clientY + 'px';
  tooltip.classList.add('visible');
  setActive(target.dataset.name);
}}
for (const target of targets) {{
  target.addEventListener('pointerenter', (event) => show(target, event));
  target.addEventListener('pointermove', (event) => show(target, event));
  target.addEventListener('pointerleave', clearActive);
  target.addEventListener('click', (event) => {{
    event.preventDefault();
    pinned = pinned === target.dataset.name ? null : target.dataset.name;
    if (pinned) show(target, event); else clearActive();
  }});
}}
</script>
</html>
"""
