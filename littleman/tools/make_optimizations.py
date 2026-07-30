#!/usr/bin/env python3
"""Render the optimization catalogue to Markdown and to a self-contained HTML page.

One data source (`optimizations_data.py`), two outputs, so they cannot drift:

    uv run python littleman/tools/make_optimizations.py           # both
    uv run python littleman/tools/make_optimizations.py --md-only
    uv run python littleman/tools/make_optimizations.py --html-only

The HTML additionally embeds `littleman/optimizations.debug.json` — the per-entry
debug run — as a collapsible panel under each entry, plus the judged submission
ladder for every problem an entry touches. Regenerate that first with:

    uv run python littleman/tools/optimization_debug.py

Every entry is rendered at three depths, so the page can be read at any of them:

  1. the **gist**  — one sentence, no context
  2. the **glyphs** — the change in real littleman, where there is one to show
  3. the **detail** — what it was, the measurements, and the alternatives
"""

# ruff: noqa: E501 -- an HTML/CSS template plus rendered data tables.

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import optimizations_data as data  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEBUG_JSON = REPO / "littleman" / "optimizations.debug.json"

STATUS_ORDER = {"shipped": 0, "superseded": 1, "rejected": 2, "parked": 3}
STATUS_GLYPH = {"shipped": "✓", "superseded": "✓→", "rejected": "✗", "parked": "·"}
STATUS_SHORT = {
    "shipped": "used",
    "superseded": "used, later replaced",
    "rejected": "measured, not used",
    "parked": "designed, not built",
}


def load_debug() -> dict:
    if DEBUG_JSON.exists():
        try:
            return json.loads(DEBUG_JSON.read_text())
        except json.JSONDecodeError:
            pass
    return {"entries": {}, "ladders": {}, "generated_at_commit": "?"}


def grouped() -> dict[str, dict[str, list[dict]]]:
    """group key -> block -> entries, each list sorted shipped-first then by date."""
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in data.ENTRIES:
        out[e["group"]][e["block"]].append(e)
    for blocks in out.values():
        for entries in blocks.values():
            entries.sort(key=lambda e: (STATUS_ORDER.get(e["status"], 9), e["date"]))
    return out


def counts() -> dict:
    c = {"total": len(data.ENTRIES)}
    for e in data.ENTRIES:
        c[e["status"]] = c.get(e["status"], 0) + 1
        c[e["era"]] = c.get(e["era"], 0) + 1
    return c


# ═══════════════════════════════════════════════════════════════════════════
# MARKDOWN
# ═══════════════════════════════════════════════════════════════════════════


def md_entry(e: dict) -> list[str]:
    out: list[str] = []
    era = " · **(post-contest)**" if e["era"] == "post-contest" else ""
    star = "★ " if e.get("important") else ""
    out.append(f"#### {star}{STATUS_GLYPH[e['status']]} {e['title']}")
    out.append("")
    out.append(f"`{e['date']}` · **{data.STATUS_LABEL[e['status']]}**{era} · {', '.join(e['problems'])}")
    out.append("")
    out.append(f"> {e['gist']}")
    out.append("")

    if e.get("not_representable"):
        out.append(f"**Not representable in littleman** — {e['not_representable']}.")
        out.append("")

    g = e.get("glyphs")
    if g:
        if g.get("before"):
            out.append("**Before**")
            out.append("")
            out.append("```")
            out.append(g["before"].strip("\n"))
            out.append("```")
            out.append("")
            out.append("**After**")
        else:
            out.append("**After**")
        out.append("")
        out.append("```")
        out.append(g["after"].strip("\n"))
        out.append("```")
        out.append("")
        if g.get("note"):
            out.append(g["note"])
            out.append("")

    out.append("<details><summary>Detail, measurements and alternatives</summary>")
    out.append("")
    out.append(e["what"])
    out.append("")

    for key, label in (("before", "Before"), ("after", "After")):
        if e.get(key):
            out.append(f"*{label}*")
            out.append("")
            out.append("```")
            out.append(e[key].strip("\n"))
            out.append("```")
            out.append("")

    if e["numbers"]:
        out.append("| | before | after | |")
        out.append("|---|---|---|---|")
        for row in e["numbers"]:
            cells = [str(c).replace("|", "\\|") for c in row]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    if e["alternatives"]:
        out.append("**Alternatives considered**")
        out.append("")
        for a in e["alternatives"]:
            why = f" — {a['why']}" if a.get("why") else ""
            out.append(f"- *{a['name']}* → **{a['verdict']}**{why}")
        out.append("")

    tail = []
    if e["commits"]:
        tail.append("commits " + " ".join(f"`{c}`" for c in e["commits"]))
    if e["sources"]:
        tail.append("see " + ", ".join(e["sources"]))
    if e["artifacts"]:
        tail.append("artifacts " + ", ".join(f"`{a}`" for a in e["artifacts"]))
    if tail:
        out.append(" · ".join(tail))
        out.append("")

    out.append("</details>")
    out.append("")
    return out


def render_md() -> str:
    c = counts()
    g = grouped()
    out: list[str] = []
    out.append("# Every optimization we tried, grouped by what it attacks")
    out.append("")
    out.append(
        f"**{c['total']} ideas** across eight resources. "
        f"{c.get('shipped', 0)} shipped, {c.get('rejected', 0)} were measured and not taken, "
        f"{c.get('parked', 0)} were designed and never built, {c.get('superseded', 0)} shipped and was later replaced. "
        f"{c.get('post-contest', 0)} of them are **post-contest** and are marked as such."
    )
    out.append("")
    out.append("Every entry is written at three depths: a one-sentence **gist**, the change in **real littleman** where there is one to show, and a collapsed **detail** block with the measurement that decided it and the alternatives that lost. Nothing here is a projection unless it says so.")
    out.append("")
    out.append("| | means |")
    out.append("|---|---|")
    for k in ("shipped", "superseded", "rejected", "parked"):
        out.append(f"| {STATUS_GLYPH[k]} `{data.STATUS_LABEL[k]}` | {STATUS_SHORT[k]} |")
    out.append("")

    out.append("## Contents")
    out.append("")
    for grp in data.GROUPS:
        blocks = g.get(grp["key"], {})
        n = sum(len(v) for v in blocks.values())
        anchor = grp["title"].lower().replace(" — ", "--").replace(" ", "-")
        for ch in ",()`/":
            anchor = anchor.replace(ch, "")
        out.append(f"- [{grp['title']}](#{anchor}) — {n} ideas")
    out.append("")

    for grp in data.GROUPS:
        blocks = g.get(grp["key"], {})
        if not blocks:
            continue
        out.append(f"## {grp['title']}")
        out.append("")
        out.append(grp["blurb"])
        out.append("")
        out.append(f"> **The law of this group.** {grp['law']}")
        out.append("")
        for block, entries in blocks.items():
            out.append(f"### {block}")
            out.append("")
            for e in entries:
                out.extend(md_entry(e))
        out.append("")

    out.append("---")
    out.append("")
    out.append("Generated from `littleman/tools/optimizations_data.py` by `littleman/tools/make_optimizations.py`. The HTML version (`littleman/optimizations.html`) carries the same content plus a debug panel per entry — artifact geometry, the commits behind it, and the judged submission ladder for every problem it touches.")
    out.append("")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════
# HTML
# ═══════════════════════════════════════════════════════════════════════════

CSS = """
:root{
  --bg:#fbfaf8; --panel:#fff; --ink:#1b1a18; --muted:#6b6660; --rule:#e3ded6;
  --code-bg:#f4f1ec; --accent:#7a4bd0;
  --ok:#2f7a4f; --no:#a63d3d; --park:#8a6a1f; --sup:#3a6ea5;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 8px 24px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#131316; --panel:#1b1b1f; --ink:#e8e6e3; --muted:#9a948c; --rule:#2e2e34;
    --code-bg:#0f0f12; --accent:#b79bf0;
    --ok:#6ec495; --no:#e88d8d; --park:#d9b45f; --sup:#8fc0ee;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  }
}
:root[data-theme="light"]{
  --bg:#fbfaf8; --panel:#fff; --ink:#1b1a18; --muted:#6b6660; --rule:#e3ded6;
  --code-bg:#f4f1ec; --accent:#7a4bd0;
  --ok:#2f7a4f; --no:#a63d3d; --park:#8a6a1f; --sup:#3a6ea5;
}
:root[data-theme="dark"]{
  --bg:#131316; --panel:#1b1b1f; --ink:#e8e6e3; --muted:#9a948c; --rule:#2e2e34;
  --code-bg:#0f0f12; --accent:#b79bf0;
  --ok:#6ec495; --no:#e88d8d; --park:#d9b45f; --sup:#8fc0ee;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  overflow-x:hidden}
.wrap{max-width:62rem;margin:0 auto;padding:2.5rem 1.25rem 6rem}
h1{font-size:clamp(1.7rem,4vw,2.5rem);line-height:1.15;margin:0 0 .5rem;letter-spacing:-.02em}
h2{font-size:clamp(1.25rem,2.6vw,1.6rem);margin:3.5rem 0 .25rem;letter-spacing:-.01em;
   padding-top:1.25rem;border-top:2px solid var(--rule)}
h3{font-size:1rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
   margin:2.25rem 0 .75rem;font-weight:650}
.lede{color:var(--muted);max-width:46rem}
.counts{display:flex;flex-wrap:wrap;gap:.5rem;margin:1.25rem 0 2rem}
.chip{border:1px solid var(--rule);border-radius:999px;padding:.2rem .7rem;
  font-size:.82rem;color:var(--muted);background:var(--panel)}
.chip b{color:var(--ink)}
.groupnote{color:var(--muted);max-width:46rem;margin:.5rem 0 0}
.law{border-left:3px solid var(--accent);padding:.6rem 0 .6rem .9rem;margin:1rem 0 0;
  color:var(--ink);background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 7%,transparent),transparent);
  border-radius:0 6px 6px 0;max-width:48rem}
.law b{color:var(--accent)}

.card{background:var(--panel);border:1px solid var(--rule);border-radius:10px;
  padding:1.1rem 1.2rem;margin:.9rem 0;box-shadow:var(--shadow)}
.card > h4{margin:0;font-size:1.06rem;line-height:1.35;letter-spacing:-.005em}
.meta{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin:.55rem 0 .7rem;
  font-size:.78rem;color:var(--muted)}
.tag{border-radius:5px;padding:.1rem .45rem;border:1px solid currentColor;
  font-weight:650;letter-spacing:.03em;font-size:.72rem;white-space:nowrap}
.t-shipped{color:var(--ok)} .t-rejected{color:var(--no)}
.t-parked{color:var(--park)} .t-superseded{color:var(--sup)}
.t-post{color:var(--accent)}
.date{font-variant-numeric:tabular-nums}
.probs{color:var(--muted)}
.gist{font-size:1.02rem;line-height:1.55;margin:0 0 .35rem}

pre{background:var(--code-bg);border:1px solid var(--rule);border-radius:8px;
  padding:.8rem .9rem;overflow-x:auto;margin:.7rem 0;
  font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre.lm{border-left:3px solid var(--accent)}
.ba{display:grid;gap:.6rem;grid-template-columns:1fr}
@media (min-width:52rem){.ba.two{grid-template-columns:1fr 1fr}}
.balabel{font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;
  color:var(--muted);font-weight:700;margin:.4rem 0 -.3rem}
.note{color:var(--muted);font-size:.9rem;margin:.2rem 0 0}

details{margin:.7rem 0 0;border-top:1px dashed var(--rule);padding-top:.6rem}
details summary{cursor:pointer;color:var(--accent);font-size:.86rem;font-weight:600;
  list-style:none;user-select:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";display:inline-block;transition:transform .15s}
details[open] summary::before{content:"▾ "}
details .body{padding:.6rem 0 0;font-size:.94rem}

.tblwrap{overflow-x:auto;margin:.7rem 0}
table{border-collapse:collapse;font-size:.86rem;min-width:100%}
th,td{border-bottom:1px solid var(--rule);padding:.35rem .6rem;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:650;font-size:.76rem;text-transform:uppercase;letter-spacing:.05em}
td:nth-child(1){color:var(--muted)}
code{background:var(--code-bg);border-radius:4px;padding:.05rem .3rem;font-size:.88em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
ul.alts{margin:.4rem 0;padding-left:1.1rem}
ul.alts li{margin:.3rem 0}
ul.alts .v{font-weight:650}
.refs{color:var(--muted);font-size:.8rem;margin-top:.6rem}
.dbg{font-size:.82rem}
.dbg .kv{display:flex;flex-wrap:wrap;gap:.35rem .9rem;margin:.3rem 0 .6rem}
.dbg .kv span{color:var(--muted)}
.dbg .kv b{color:var(--ink);font-variant-numeric:tabular-nums}
.filters{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 92%,transparent);
  backdrop-filter:blur(8px);border:1px solid var(--rule);border-radius:10px;
  padding:.7rem .9rem;margin:1.5rem 0 0;box-shadow:var(--shadow)}
.filters .row{display:flex;flex-wrap:wrap;gap:.5rem .9rem;align-items:center}
.filters label{display:inline-flex;align-items:center;gap:.4rem;cursor:pointer;
  font-size:.88rem;border:1px solid var(--rule);border-radius:999px;
  padding:.25rem .75rem;background:var(--panel);user-select:none;white-space:nowrap}
.filters label:hover{border-color:var(--accent)}
.filters label:has(input:checked){border-color:var(--accent);
  background:color-mix(in srgb,var(--accent) 10%,var(--panel));color:var(--accent);font-weight:600}
.filters input{accent-color:var(--accent);margin:0}
.filters .shown{margin-left:auto;font-size:.82rem;color:var(--muted);font-variant-numeric:tabular-nums}
.filters .hint{font-size:.78rem;color:var(--muted);margin:.45rem 0 0}
.star{color:var(--accent);font-weight:700}
.norep{border-left:3px solid var(--rule);padding:.45rem .8rem;margin:.55rem 0 0;
  color:var(--muted);font-size:.88rem;background:var(--code-bg);border-radius:0 6px 6px 0}
.norep b{color:var(--ink)}
.empty{display:none;color:var(--muted);padding:2rem 0}
.toc{columns:2;column-gap:2rem;margin:1rem 0 0;padding:0;list-style:none}
@media (max-width:44rem){.toc{columns:1}}
.toc li{margin:.25rem 0;break-inside:avoid}
.toc a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.toc .n{color:var(--muted);font-size:.8rem}
.foot{margin-top:4rem;padding-top:1.25rem;border-top:1px solid var(--rule);
  color:var(--muted);font-size:.85rem}
"""


JS = """
(function(){
  var imp = document.getElementById('f-imp'),
      lm  = document.getElementById('f-lm'),
      no  = document.getElementById('f-nolm'),
      out = document.getElementById('shown'),
      none= document.getElementById('empty'),
      cards = Array.prototype.slice.call(document.querySelectorAll('.card'));

  function apply(){
    var shown = 0;
    cards.forEach(function(c){
      var isImp = c.dataset.imp === '1',
          hasLm = c.dataset.lm === '1',
          ok = (!imp.checked || isImp) && ((hasLm && lm.checked) || (!hasLm && no.checked));
      c.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    // a block heading, and then a whole group, disappear when everything under
    // it is filtered out -- otherwise the page is mostly empty headings
    document.querySelectorAll('.block').forEach(function(b){
      var any = Array.prototype.some.call(b.querySelectorAll('.card'),
                function(c){ return c.style.display !== 'none'; });
      b.style.display = any ? '' : 'none';
    });
    document.querySelectorAll('.group').forEach(function(gr){
      var any = Array.prototype.some.call(gr.querySelectorAll('.block'),
                function(b){ return b.style.display !== 'none'; });
      gr.style.display = any ? '' : 'none';
    });
    out.textContent = shown + ' of ' + cards.length + ' shown';
    none.style.display = shown ? 'none' : 'block';
  }

  [imp, lm, no].forEach(function(el){ el.addEventListener('change', apply); });
  apply();
})();
"""


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def _emph(text: str) -> str:
    """**bold** then *italic*, on a segment already known to be outside code."""
    parts = text.split("**")
    text = "".join(p if i % 2 == 0 else f"<b>{p}</b>" for i, p in enumerate(parts))
    parts = text.split("*")
    if len(parts) % 2 == 1:  # balanced
        text = "".join(p if i % 2 == 0 else f"<i>{p}</i>" for i, p in enumerate(parts))
    return text


def inline(s: str) -> str:
    """Very small markdown subset: `code`, **bold**, *italic*.

    Code spans are extracted first and never touched by the emphasis pass — the
    language uses `*` and `-` as glyphs, so a naive replace corrupts them.
    """
    parts = esc(s).split("`")
    return "".join(
        _emph(p) if i % 2 == 0 else f"<code>{p}</code>" for i, p in enumerate(parts)
    )


def html_debug(e: dict, dbg: dict, ladders: dict) -> str:
    d = dbg.get(e["id"])
    if not d:
        return ""
    bits: list[str] = ['<details><summary>Debug run</summary><div class="body dbg">']

    gl = d.get("glyphs")
    if gl:
        bad = len(gl.get("illegal", []))
        srcs = ", ".join(f"<code>{esc(s)}</code>" for s in gl.get("sources", [])[:4]) or "—"
        bits.append("<div class='balabel'>Figure check</div><div class='kv'>"
                    f"<span>grid rows <b>{gl['rows']}</b></span>"
                    f"<span>found verbatim on disk <b>{gl['sourced']}</b></span>"
                    f"<span>illegal glyphs <b>{bad}</b></span></div>")
        bits.append(f"<p class='note'>Traced to {srcs}. Rows not found verbatim are "
                    "micro-program fragments or excerpts trimmed to fit — the alphabet check "
                    "covers those.</p>")
        for item in gl.get("illegal", []):
            bits.append(f"<p class='note'>⚠ <code>{esc(''.join(item['chars']))}</code> in "
                        f"<code>{esc(item['line'][:80])}</code></p>")
    elif d.get("not_representable"):
        bits.append("<div class='balabel'>Figure check</div>"
                    f"<p class='note'>No littleman figure: {inline(d['not_representable'])}.</p>")

    if d.get("artifacts"):
        bits.append("<div class='balabel'>Artifacts, measured on disk</div>")
        bits.append("<div class='tblwrap'><table><tr><th>grid</th><th>box</th><th>max(w,h)²</th>"
                    "<th>fill</th><th>men at boot</th><th>pipe ops</th><th>judged</th></tr>")
        for a in d["artifacts"]:
            if not a.get("exists"):
                bits.append(f"<tr><td colspan=7><code>{esc(a['path'])}</code> — not present at this commit</td></tr>")
                continue
            score = f"{a['archived_score']:,}" if "archived_score" in a else "—"
            bits.append(
                f"<tr><td><code>{esc(Path(a['path']).name)}</code></td>"
                f"<td>{a['width']}×{a['height']}</td><td>{a['area2']:,}</td>"
                f"<td>{a['fill_pct']}%</td><td>{a['men_at_boot']}</td>"
                f"<td>{a['pipe_ops']}</td><td>{score}</td></tr>"
            )
        bits.append("</table></div>")
        for a in d["artifacts"]:
            if a.get("descr", {}).get("verdict"):
                bits.append(f"<p class='note'><b>{esc(Path(a['path']).name)}</b> — {esc(a['descr']['verdict'])}</p>")

    if d.get("commits"):
        bits.append("<div class='balabel'>Commits</div>")
        bits.append("<div class='tblwrap'><table><tr><th>sha</th><th>when</th><th>subject</th><th>files</th></tr>")
        for c in d["commits"]:
            if not c.get("found"):
                bits.append(f"<tr><td><code>{esc(c['sha'])}</code></td><td colspan=3>not in this history</td></tr>")
                continue
            bits.append(
                f"<tr><td><code>{esc(c['sha'])}</code></td><td class='date'>{esc(c['date'])}</td>"
                f"<td>{esc(c['subject'])}</td><td>{c['files_changed']}</td></tr>"
            )
        bits.append("</table></div>")

    for slug in d.get("problems", []):
        rungs = ladders.get(slug) or []
        if not rungs:
            continue
        bits.append(f"<div class='balabel'>Judged ladder — {esc(slug)} ({len(rungs)} graded submissions)</div>")
        bits.append("<div class='tblwrap'><table><tr><th>score</th><th>grid</th><th>cases</th>"
                    "<th>avg ticks</th><th>submitted</th><th>what changed</th></tr>")
        for r in rungs:
            note = r["note"][:170] + ("…" if len(r["note"]) > 170 else "")
            bits.append(
                f"<tr><td><b>{r['score']:,}</b></td><td>{esc(r['grid'])}</td><td>{esc(r['cases'])}</td>"
                f"<td>{esc(r['avg_ticks'])}</td><td class='date'>{esc(r['submitted'][:10])}</td>"
                f"<td>{esc(note)}</td></tr>"
            )
        bits.append("</table></div>")

    bits.append("</div></details>")
    return "".join(bits)


def html_entry(e: dict, dbg: dict, ladders: dict) -> str:
    st = e["status"]
    tags = [f'<span class="tag t-{st}">{esc(data.STATUS_LABEL[st])}</span>']
    if e["era"] == "post-contest":
        tags.append('<span class="tag t-post">POST-CONTEST</span>')

    has_lm = "1" if e.get("glyphs") else "0"
    imp = "1" if e.get("important") else "0"
    out = [f'<div class="card" id="{esc(e["id"])}" data-imp="{imp}" data-lm="{has_lm}">']
    star = '<span class="star" title="important">★</span> ' if e.get("important") else ""
    out.append(f"<h4>{star}{inline(e['title'])}</h4>")
    out.append('<div class="meta">' + "".join(tags)
               + f'<span class="date">{esc(e["date"])}</span>'
               + f'<span class="probs">{esc(", ".join(e["problems"]))}</span></div>')
    out.append(f'<p class="gist">{inline(e["gist"])}</p>')

    if e.get("not_representable"):
        out.append('<p class="norep"><b>Not representable in littleman</b> — '
                   f'{inline(e["not_representable"])}.</p>')

    g = e.get("glyphs")
    if g:
        two = "two" if g.get("before") else ""
        out.append(f'<div class="ba {two}">')
        if g.get("before"):
            out.append('<div><div class="balabel">Before</div>'
                       f'<pre class="lm">{esc(g["before"].strip("\n"))}</pre></div>')
        out.append('<div><div class="balabel">After</div>'
                   f'<pre class="lm">{esc(g["after"].strip("\n"))}</pre></div>')
        out.append("</div>")
        if g.get("note"):
            out.append(f'<p class="note">{inline(g["note"])}</p>')

    # detail
    det = ['<details><summary>Detail, measurements and alternatives</summary><div class="body">']
    det.append(f"<p>{inline(e['what'])}</p>")
    for key, label in (("before", "Before"), ("after", "After")):
        if e.get(key):
            det.append(f'<div class="balabel">{label}</div><pre>{esc(e[key].strip("\n"))}</pre>')
    if e["numbers"]:
        det.append('<div class="tblwrap"><table><tr><th></th><th>before</th><th>after</th><th></th></tr>')
        for row in e["numbers"]:
            det.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
        det.append("</table></div>")
    if e["alternatives"]:
        det.append('<div class="balabel">Alternatives considered</div><ul class="alts">')
        for a in e["alternatives"]:
            why = f" — {inline(a['why'])}" if a.get("why") else ""
            det.append(f"<li>{inline(a['name'])} → <span class='v'>{inline(a['verdict'])}</span>{why}</li>")
        det.append("</ul>")
    refs = []
    if e["commits"]:
        refs.append("commits " + " ".join(f"<code>{esc(c)}</code>" for c in e["commits"]))
    if e["sources"]:
        refs.append("see " + ", ".join(esc(s) for s in e["sources"]))
    if refs:
        det.append(f'<p class="refs">{" · ".join(refs)}</p>')
    det.append("</div></details>")
    out.append("".join(det))

    out.append(html_debug(e, dbg, ladders))
    out.append("</div>")
    return "".join(out)


def render_html() -> str:
    payload = load_debug()
    dbg, ladders = payload.get("entries", {}), payload.get("ladders", {})
    c, g = counts(), grouped()

    body: list[str] = ['<div class="wrap">']
    body.append("<h1>Every optimization we tried, grouped by what it attacks</h1>")
    body.append(
        '<p class="lede">Four days inside a 2D grid, and one post-contest week after it. '
        "Each idea is written at three depths — a one-sentence gist, the change in real littleman "
        "where there is one to show, and a collapsed detail block with the measurement that decided "
        "it and the alternatives that lost. Ideas that were measured and <i>not</i> taken are kept "
        "here deliberately: a priced dead end is a result.</p>"
    )
    body.append('<div class="counts">')
    body.append(f'<span class="chip"><b>{c["total"]}</b> ideas</span>')
    for k in ("shipped", "superseded", "rejected", "parked"):
        if c.get(k):
            body.append(f'<span class="chip"><b>{c[k]}</b> {STATUS_SHORT[k]}</span>')
    body.append(f'<span class="chip"><b>{c.get("post-contest", 0)}</b> post-contest</span>')
    body.append(f'<span class="chip">debug run at <b>{esc(payload.get("generated_at_commit", "?"))}</b></span>')
    body.append("</div>")

    n_imp = sum(1 for e in data.ENTRIES if e.get("important"))
    n_lm = sum(1 for e in data.ENTRIES if e.get("glyphs"))
    body.append(
        '<div class="filters"><div class="row">'
        f'<label><input type="checkbox" id="f-imp" checked> Important only <span class="n">({n_imp})</span></label>'
        f'<label><input type="checkbox" id="f-lm" checked> With littleman <span class="n">({n_lm})</span></label>'
        f'<label><input type="checkbox" id="f-nolm"> Without littleman <span class="n">({len(data.ENTRIES) - n_lm})</span></label>'
        '<span class="shown" id="shown"></span></div>'
        '<p class="hint">“With littleman” means the change has a real grid figure. The rest are marked '
        '<i>not representable</i> with the reason — a finding about geometry, ordering or measurement is not a grid edit.</p>'
        "</div>"
    )

    body.append('<ul class="toc">')
    for grp in data.GROUPS:
        n = sum(len(v) for v in g.get(grp["key"], {}).values())
        body.append(f'<li><a href="#{grp["key"]}">{esc(grp["title"])}</a> <span class="n">{n}</span></li>')
    body.append("</ul>")

    for grp in data.GROUPS:
        blocks = g.get(grp["key"], {})
        if not blocks:
            continue
        body.append(f'<section class="group" id="{grp["key"]}">')
        body.append(f'<h2>{esc(grp["title"])}</h2>')
        body.append(f'<p class="groupnote">{inline(grp["blurb"])}</p>')
        body.append(f'<p class="law"><b>The law of this group.</b> {inline(grp["law"])}</p>')
        for block, entries in blocks.items():
            body.append('<section class="block">')
            body.append(f"<h3>{inline(block)}</h3>")
            for e in entries:
                body.append(html_entry(e, dbg, ladders))
            body.append("</section>")
        body.append("</section>")

    body.append('<p class="empty" id="empty">Nothing matches those filters — tick '
                '<b>Without littleman</b> or untick <b>Important only</b>.</p>')

    body.append(
        '<p class="foot">Generated from <code>littleman/tools/optimizations_data.py</code> by '
        "<code>littleman/tools/make_optimizations.py</code>. Debug panels come from "
        "<code>littleman/tools/optimization_debug.py</code>, which measures each named grid on disk, "
        "resolves each cited commit, and reads the judged verdict of every archived submission. "
        "The same catalogue in Markdown is <code>OPTIMIZATIONS.md</code>.</p>"
    )
    body.append("</div>")

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>littleman — the optimization catalogue</title>"
        f"<style>{CSS}</style></head><body>{''.join(body)}"
        f"<script>{JS}</script></body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", default=str(REPO / "OPTIMIZATIONS.md"))
    ap.add_argument("--html", default=str(REPO / "littleman" / "optimizations.html"))
    ap.add_argument("--md-only", action="store_true")
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    if not args.html_only:
        Path(args.md).write_text(render_md())
        print(f"markdown -> {args.md}  ({len(data.ENTRIES)} entries)")
    if not args.md_only:
        Path(args.html).write_text(render_html())
        print(f"html     -> {args.html}")


if __name__ == "__main__":
    main()
