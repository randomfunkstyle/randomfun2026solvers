#!/usr/bin/env python3
"""The debug pass over the optimization catalogue.

For every entry in ``optimizations_data.ENTRIES`` this runs whatever can actually
be *run* about it and writes the result to ``littleman/optimizations.debug.json``,
which ``make_optimizations.py`` embeds into the HTML page as a per-entry panel.

Three probes, in increasing cost:

1. **artifact** — for every ``.man`` an entry names: does it exist, what is its
   box, its `max(w,h)²` factor, its glyph census, how many rooms/pipes it draws
   and how many little men are born at boot (`@` cells). Pure Python, no engine.
2. **archive** — the judged verdicts for that entry's problems, read out of
   ``solutions/<slug>/*.descr``: score, box, cases, submit time, commit and the
   free-form note. This is the *measured ladder* the entry sits on.
3. **commits** — for each commit an entry cites: its real author date, subject,
   and the files it touched. Cheap `git show`.

Nothing here re-runs the engine: a full re-verification of every family is hours,
and every tick number in the catalogue is already the judge's or a recorded
engine run. Pass ``--verify`` to additionally run the native validator over the
artifacts of entries that name a problem slug it recognises — that *is* an engine
run, and it is slow.

Usage:
    uv run python littleman/tools/optimization_debug.py
    uv run python littleman/tools/optimization_debug.py --verify --only df-tcp-ring
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import optimizations_data as data  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# glyphs that mean something structural, for the census
WALL = set("+-|")
ARROWS = set("<>^v")
PIPE_OPS = set("rsRSUq")
BRANCH = set("Xdax]")
SPLIT = set("Y")


def _run(*args: str) -> str:
    try:
        out = subprocess.run(
            args, cwd=REPO, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


# ── probe 1: the artifact itself ─────────────────────────────────────────────


def probe_artifact(rel: str) -> dict:
    """Box, factor and census of one `.man`, without booting any engine."""
    path = REPO / rel
    info: dict = {"path": rel, "exists": path.exists()}
    if not path.exists():
        return info

    text = path.read_text(errors="replace")
    lines = text.rstrip("\n").split("\n")
    height = len(lines)
    width = max((len(line) for line in lines), default=0)
    census = Counter(ch for line in lines for ch in line)

    filled = sum(1 for line in lines for ch in line if ch not in " ")
    info.update(
        {
            "width": width,
            "height": height,
            "max_side": max(width, height),
            "area2": max(width, height) ** 2,
            "bbox_cells": width * height,
            "filled_cells": filled,
            "fill_pct": round(100.0 * filled / (width * height), 1) if width and height else 0.0,
            "men_at_boot": census.get("@", 0),
            "wall_cells": sum(census.get(c, 0) for c in WALL),
            "arrow_cells": sum(census.get(c, 0) for c in ARROWS),
            "pipe_ops": sum(census.get(c, 0) for c in PIPE_OPS),
            "branch_ops": sum(census.get(c, 0) for c in BRANCH),
            "splits": sum(census.get(c, 0) for c in SPLIT),
            "digit_cells": sum(census.get(c, 0) for c in "0123456789"),
            "backticks": census.get("`", 0),
            "bytes": path.stat().st_size,
        }
    )
    # the archived score, when the filename carries one
    m = re.match(r"^(\d{6,})_", path.name)
    if m:
        info["archived_score"] = int(m.group(1))
        descr = path.with_suffix(".descr")
        if descr.exists():
            info["descr"] = _parse_descr(descr)
    return info


DESCR_FIELDS = ("verdict", "submitted", "grid", "commit", "local")


def _parse_descr(path: Path) -> dict:
    out: dict = {}
    text = path.read_text(errors="replace")
    for field in DESCR_FIELDS:
        m = re.search(rf"^{field}\s+(.+)$", text, re.M)
        if m:
            out[field] = m.group(1).strip()
    m = re.search(r"^notes:\n(.*)$", text, re.S | re.M)
    if m:
        out["notes"] = " ".join(m.group(1).split())[:600]
    return out


# ── probe 2: the judged ladder for a problem ─────────────────────────────────

_SCORE_RE = re.compile(r"score\s+([\d,]+)")
_TICKS_RE = re.compile(r"avgTicks\s+([\d,]+)")
_CASES_RE = re.compile(r"done:\s*(\d+/\d+)\s*cases")


def _first(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    return m.group(1) if m else ""


def archive_ladder(slug: str) -> list[dict]:
    """Every graded submission for `slug`, best score first."""
    folder = REPO / "solutions" / slug
    if not folder.is_dir():
        return []
    rungs = []
    for man in sorted(folder.glob("*.man")):
        m = re.match(r"^(\d+)_", man.name)
        if not m:
            continue
        side = man.with_suffix(".descr")
        descr = _parse_descr(side) if side.exists() else {}
        verdict = descr.get("verdict", "")
        rungs.append(
            {
                "score": int(m.group(1)),
                "file": str(man.relative_to(REPO)),
                "grid": descr.get("grid", ""),
                "cases": _first(_CASES_RE, verdict),
                "avg_ticks": _first(_TICKS_RE, verdict),
                "submitted": descr.get("submitted", "")[:19],
                "commit": descr.get("commit", ""),
                "note": descr.get("notes", ""),
            }
        )
    rungs.sort(key=lambda r: r["score"])
    return rungs


# ── probe 3: the commits an entry cites ──────────────────────────────────────


def probe_commit(sha: str) -> dict:
    fmt = "--format=%H%x00%ad%x00%s"
    line = _run("git", "show", "-s", "--date=format:%Y-%m-%d %H:%M", fmt, sha)
    if not line or "\x00" not in line:
        return {"sha": sha, "found": False}
    full, date, subject = line.split("\x00", 2)
    stat = _run("git", "show", "--stat=200", "--format=", sha)
    files = [ln.strip() for ln in stat.split("\n") if "|" in ln][:12]
    return {
        "sha": sha,
        "full": full[:12],
        "found": True,
        "date": date,
        "subject": subject,
        "files": files,
        "files_changed": len([ln for ln in stat.split("\n") if "|" in ln]),
    }


# ── optional probe 4: an actual engine run ───────────────────────────────────


# ── probe 4: are the catalogue's littleman figures real? ─────────────────────

# every glyph the language defines, plus room walls, pipe art and the two
# display-panel border characters. Anything else in a grid row is a typo.
ALPHABET = set(
    "0123456789`MW+-*%/N&|~{}><^vVX.H bmdaq]xrsRSUYIO@"
    "+-|:=,"  # walls, panel border
)

_MAN_CACHE: list[tuple[str, set[str]]] = []


def _man_corpus() -> list[tuple[str, set[str]]]:
    """Every checked-in grid, as a set of its rows, for verbatim lookup."""
    if _MAN_CACHE:
        return _MAN_CACHE
    for folder in ("solutions", "tasks/solutions", "littleman/programs",
                   "littleman/examples", "scratch"):
        base = REPO / folder
        if not base.is_dir():
            continue
        for man in base.rglob("*.man"):
            try:
                text = man.read_text(errors="replace")
                rows = {ln.rstrip() for ln in text.split("\n") if ln.strip()}
            except OSError:
                continue
            _MAN_CACHE.append((str(man.relative_to(REPO)), rows))
    return _MAN_CACHE


def _grid_part(line: str) -> str:
    """Strip the pointing commentary a figure carries beside its grid rows.

    A room row runs to its closing `|`; a wall row is the leading run of wall
    and panel-border characters. Everything after that is prose about the
    figure and must not be checked as if it were glyphs.
    """
    s = line.strip()
    if s.startswith("|"):
        end = s.rfind("|")
        # a room row always closes with a second `|`. A single leading `|` is a
        # pointer line drawn beside the figure, not a row of the grid.
        return s[: end + 1] if end > 0 else ""
    if s.startswith("+"):
        i = 0
        while i < len(s) and s[i] in "+-=:":
            i += 1
        return s[:i]
    return s


def probe_glyphs(entry: dict) -> dict:
    """Check each figure: legal alphabet, and whether its rows exist on disk.

    A figure is a mix of grid rows and pointing commentary, so only lines that
    look like grid rows (they start with `|` or `+`) are checked. `sourced`
    counts the ones found verbatim in a checked-in `.man`; a figure assembled
    from micro-program fragments legitimately finds none, which is why this
    reports rather than asserts.
    """
    g = entry.get("glyphs")
    if not g:
        return {}
    corpus = _man_corpus()
    result: dict = {"illegal": [], "rows": 0, "sourced": 0, "sources": []}
    for side in ("before", "after"):
        for raw in (g.get(side) or "").split("\n"):
            line = raw.rstrip()
            if not line.strip().startswith(("|", "+")):
                continue
            probe = _grid_part(line)
            if len(probe) < 3:
                continue
            result["rows"] += 1
            stray = sorted({ch for ch in probe if ch not in ALPHABET})
            if stray:
                result["illegal"].append({"side": side, "line": probe, "chars": stray})
            for name, rows in corpus:
                if probe in rows:
                    result["sourced"] += 1
                    if name not in result["sources"]:
                        result["sources"].append(name)
                    break
    return result


def probe_verify(rel: str, slug: str) -> dict:
    """Run the native validator. Slow; only under --verify."""
    out = _run(
        "uv", "run", "python", "-m", "randomfun2026solvers.fast_littleman", rel, slug
    )
    return {"path": rel, "slug": slug, "output": out[-2000:] if out else "(no output)"}


# ── driver ───────────────────────────────────────────────────────────────────


def build(only: str | None = None, verify: bool = False) -> dict:
    entries: dict = {}
    ladders: dict = {}
    known_slugs = {p.name for p in (REPO / "solutions").iterdir() if p.is_dir()}

    for entry in data.ENTRIES:
        if only and entry["id"] != only:
            continue
        dbg: dict = {"artifacts": [], "commits": [], "problems": []}

        glyphs = probe_glyphs(entry)
        if glyphs:
            dbg["glyphs"] = glyphs
        if entry.get("not_representable"):
            dbg["not_representable"] = entry["not_representable"]

        for rel in entry.get("artifacts", []):
            dbg["artifacts"].append(probe_artifact(rel))

        for sha in entry.get("commits", []):
            dbg["commits"].append(probe_commit(sha))

        for slug in entry.get("problems", []):
            if slug in known_slugs:
                dbg["problems"].append(slug)
                if slug not in ladders:
                    ladders[slug] = archive_ladder(slug)

        if verify:
            dbg["verify"] = [
                probe_verify(a["path"], entry["problems"][0])
                for a in dbg["artifacts"]
                if a.get("exists") and entry.get("problems") and entry["problems"][0] in known_slugs
            ]

        entries[entry["id"]] = dbg

    head = _run("git", "rev-parse", "--short", "HEAD")
    return {
        "generated_at_commit": head,
        "entry_count": len(entries),
        "entries": entries,
        "ladders": ladders,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "littleman" / "optimizations.debug.json"))
    ap.add_argument("--only", help="one entry id, for iterating")
    ap.add_argument("--verify", action="store_true", help="also run the native validator (slow)")
    args = ap.parse_args()

    payload = build(only=args.only, verify=args.verify)
    Path(args.out).write_text(json.dumps(payload, indent=1) + "\n")

    n_art = sum(len(e["artifacts"]) for e in payload["entries"].values())
    n_ok = sum(1 for e in payload["entries"].values() for a in e["artifacts"] if a.get("exists"))
    n_com = sum(len(e["commits"]) for e in payload["entries"].values())
    n_found = sum(1 for e in payload["entries"].values() for c in e["commits"] if c.get("found"))
    print(f"{payload['entry_count']} entries debugged at {payload['generated_at_commit']}")
    print(f"  artifacts : {n_ok}/{n_art} resolved")
    print(f"  commits   : {n_found}/{n_com} resolved")
    print(f"  ladders   : {len(payload['ladders'])} problems, "
          f"{sum(len(v) for v in payload['ladders'].values())} graded submissions")

    figs = [e["glyphs"] for e in payload["entries"].values() if e.get("glyphs")]
    rows = sum(f["rows"] for f in figs)
    src = sum(f["sourced"] for f in figs)
    bad = [(eid, i) for eid, e in payload["entries"].items()
           for i in e.get("glyphs", {}).get("illegal", [])]
    print(f"  figures   : {len(figs)} with littleman, {rows} grid rows, "
          f"{src} found verbatim in a checked-in grid")
    n_rep = sum(1 for e in payload["entries"].values() if e.get("not_representable"))
    print(f"              {n_rep} marked not representable")
    if bad:
        print(f"  !! {len(bad)} row(s) contain characters the language does not define:")
        for eid, item in bad[:10]:
            print(f"       {eid}: {''.join(item['chars'])!r} in {item['line'][:60]}")
    else:
        print("              every grid row uses only legal glyphs")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
