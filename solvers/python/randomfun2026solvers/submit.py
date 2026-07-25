#!/usr/bin/env python3
"""Submit a ``.man`` to the contest, read the verdict back, and archive it.

Three jobs, and the third is the one that matters after a long night: **nothing
that ever scored should be recoverable only from shell history.** Every accepted
submission is written to a per-task directory named by its *server-verified*
score, next to a free-form note saying what it was:

    solutions/<slug>/<score>_<slug>.man
    solutions/<slug>/<score>_<slug>.descr

Scores are zero-padded so the directory sorts best-first, and because the name is
the score, a better submission can never overwrite a worse one — the history of
what we tried is the directory listing.

Two guards, both learned from how the API is shaped (``littleman/reference/api.txt``):

* **Verify locally before spending a submission.** Only 5 submissions may be
  pending at once, and grading is asynchronous, so a submission that fails on the
  public cases is a wasted slot and a wasted wait. ``--force`` overrides for the
  case where you deliberately want the server's opinion.
* **Never lower a score by accident.** The server keeps your best per problem, so
  submitting is safe — but the *archive* is ours, and it records what actually
  happened rather than what we hoped.

The API key is a secret and does not belong in this file. It is read from
``$ICFP_TOKEN`` or, for convenience, from an untracked ``.icfp-token`` at the repo
root (see ``.gitignore``). Splitting a key across string literals in a tracked
file would not hide it — the file and its history are both readable — so it is
kept out of the tree entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "API",
    "SubmitError",
    "Verdict",
    "already_sent",
    "archive",
    "fingerprint",
    "poll",
    "problem_id",
    "submit",
    "token",
]

API = "https://icfpcontest2026.com/api/v1"
REPO = Path(__file__).resolve().parents[3]
PROBLEM_DIR = REPO / "tasks" / "problems"
SOLUTION_DIR = REPO / "tasks" / "solutions"
ARCHIVE = REPO / "solutions"
TOKEN_FILE = REPO / ".icfp-token"

#: Terminal states, per the API reference: pending -> running -> done | failed.
TERMINAL = {"done", "failed"}


class SubmitError(RuntimeError):
    """A refusal, an API error, or a verdict we could not make sense of."""


def token() -> str:
    """The API key, from ``$ICFP_TOKEN`` or the untracked ``.icfp-token``."""
    env = os.environ.get("ICFP_TOKEN")
    if env:
        return env.strip()
    if TOKEN_FILE.exists():
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise SubmitError(
        "no API key. Either export ICFP_TOKEN=... or write it to "
        f"{TOKEN_FILE.relative_to(REPO)} (already in .gitignore)."
    )


def problem_id(slug: str) -> str:
    """The UUID the submit endpoint wants. Everything else in the API takes the slug."""
    path = PROBLEM_DIR / f"{slug}.json"
    if not path.exists():
        raise SubmitError(
            f"no such problem {slug!r}; have {sorted(p.stem for p in PROBLEM_DIR.glob('*.json'))}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    pid = data.get("id")
    if not pid:
        raise SubmitError(f"{path.name} has no `id` field")
    if data.get("status") == "practice":
        raise SubmitError(f"{slug} is a practice problem and rejects submissions")
    return pid


#: Cloudflare fronts the contest API and **rejects the default urllib
#: User-Agent outright** — HTTP 403 with body ``error code: 1010``, which is a
#: browser-signature ban and not an auth problem, so it is easy to misread as a bad
#: key. Any ordinary UA gets through; the curl examples in the API reference work
#: for exactly this reason.
_USER_AGENT = "randomfun2026solvers/1.0 (+https://icfpcontest2026.com)"


def _call(method: str, url: str, body: dict | None = None, *, auth: bool = True) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", _USER_AGENT)
    if auth:
        req.add_header("Authorization", f"Bearer {token()}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            err = json.loads(raw)["error"]
            detail = f"{err.get('code')}: {err.get('message')}"
        except Exception:
            detail = raw[:300]
        # Two codes are worth naming, because both read as something they are not.
        hint = ""
        if exc.code == 429:
            hint = "  (up to 5 submissions may be pending — wait for one to finish)"
        elif exc.code == 403 and "1010" in raw:
            hint = "  (Cloudflare browser-signature ban, not auth — check _USER_AGENT)"
        raise SubmitError(f"HTTP {exc.code} {detail}{hint}") from exc
    except urllib.error.URLError as exc:
        raise SubmitError(f"network: {exc.reason}") from exc


def submit(slug: str, program: str) -> str:
    """POST the grid; returns the submission id."""
    out = _call("POST", f"{API}/submissions", {"problemId": problem_id(slug), "program": program})
    sid = out.get("id")
    if not sid:
        raise SubmitError(f"submit returned no id: {out}")
    return sid


@dataclass
class Verdict:
    """A terminal submission result."""

    id: str
    status: str
    cases_passed: int | None = None
    cases_total: int | None = None
    score: float | None = None
    area2: int | None = None
    avg_ticks: float | None = None
    load_error: str | None = None
    output: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """A full pass. Score is null until every case passes, so that is the test."""
        return self.status == "done" and self.score is not None

    def summary(self) -> str:
        if self.load_error:
            return f"{self.status}: load error — {self.load_error}"
        cases = (
            f"{self.cases_passed}/{self.cases_total} cases"
            if self.cases_total is not None
            else "no case counts"
        )
        if self.accepted:
            ticks = (
                "footprint-only" if self.avg_ticks is None else f"avgTicks {self.avg_ticks:,.0f}"
            )
            return f"{self.status}: {cases}, area2 {self.area2}, {ticks}, score {self.score:,.0f}"
        return f"{self.status}: {cases}, no score (a full pass is required)"


def _verdict(sid: str, data: dict[str, Any]) -> Verdict:
    return Verdict(
        id=sid,
        status=data.get("status", "?"),
        cases_passed=data.get("casesPassed"),
        cases_total=data.get("casesTotal"),
        score=data.get("score"),
        area2=data.get("area2"),
        avg_ticks=data.get("avgTicks"),
        load_error=data.get("loadError"),
        output=data.get("output") or "",
        raw=data,
    )


def poll(sid: str, *, timeout: float = 600.0, on_state=None) -> Verdict:
    """Poll until the submission reaches a terminal state.

    Backs off gently: grading is asynchronous and there is no callback, so the
    only thing to do is wait — but hammering a rate-limited API is how you get a
    429 on the *next* submission.
    """
    deadline = time.monotonic() + timeout
    delay, last = 1.0, None
    while True:
        data = _call("GET", f"{API}/submissions/{sid}")
        v = _verdict(sid, data)
        if v.status != last and on_state:
            on_state(v)
        last = v.status
        if v.status in TERMINAL:
            return v
        if time.monotonic() > deadline:
            raise SubmitError(f"submission {sid} still {v.status} after {timeout:.0f}s")
        time.sleep(delay)
        delay = min(delay * 1.5, 15.0)


# ── the archive ──────────────────────────────────────────────────────────────
def _score_tag(score: float | None) -> str:
    """Zero-padded so a directory listing sorts best-first."""
    return "unscored" if score is None else f"{int(round(score)):015d}"


def archive(
    slug: str,
    program: str,
    verdict: Verdict,
    *,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write the grid and a free-form ``.descr`` under ``solutions/<slug>/``.

    Named by the *server-verified* score, so a better run can never overwrite a
    worse one and the directory listing is the history of what we tried.
    """
    out = ARCHIVE / slug
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{_score_tag(verdict.score)}_{slug}"
    man = out / f"{stem}.man"
    man.write_text(program if program.endswith("\n") else program + "\n", encoding="utf-8")

    rows = program.rstrip("\n").split("\n")
    lines = [
        f"# {slug} — submission {verdict.id}",
        "",
        f"verdict      {verdict.summary()}",
        f"submitted    {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"grid         {max(len(r) for r in rows)}x{len(rows)}",
        f"problem id   {problem_id(slug)}",
        f"fingerprint  {fingerprint(program)}",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"{k:12s} {v}")
    if verdict.output:
        lines += ["", "runner output:", verdict.output.rstrip()]
    lines += ["", "notes:", (note.strip() or "(none given)")]
    (out / f"{stem}.descr").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return man


def fingerprint(program: str) -> str:
    """A stable hash of the grid itself.

    Normalised on trailing whitespace and the final newline, because those are not
    part of the program: two grids that differ only there would submit identically
    and must dedup against each other.
    """
    body = "\n".join(line.rstrip() for line in program.rstrip("\n").split("\n"))
    return hashlib.sha256(body.encode()).hexdigest()


def already_sent(slug: str, program: str) -> tuple[Path, str] | None:
    """A previous submission of this exact grid, if there is one.

    The archive *is* the index — every submission is in there — so this hashes what
    is on disk rather than keeping a side table that could fall out of step with it.
    Returns ``(archived .man, its score tag)``.
    """
    want = fingerprint(program)
    for man in sorted((ARCHIVE / slug).glob("*.man")):
        if fingerprint(man.read_text(encoding="utf-8")) == want:
            return man, man.stem.split("_", 1)[0]
    return None


def best_archived(slug: str) -> tuple[int, Path] | None:
    """The best archived submission for a task, or ``None``."""
    out = ARCHIVE / slug
    scored = [
        (int(p.stem.split("_", 1)[0]), p)
        for p in out.glob("*.man")
        if p.stem.split("_", 1)[0].isdigit()
    ]
    return min(scored) if scored else None


# ── CLI ──────────────────────────────────────────────────────────────────────
def _local_check(slug: str, path: Path, tick_cap: int) -> tuple[bool, str]:
    """Run the public cases locally. Display problems are judged on frames."""
    from . import optimize, scoring

    prob = scoring.load_problem(slug)
    display = any(scoring._is_display_case(c) for c in (prob.get("publicTestData") or []))
    if display:
        # Frames, not output: reuse the emulator-side panel model via the program,
        # since the engine exposes only the *current* buffer at a tick.
        from .lm1 import machine, programs
        from .lm1.display import frames_from_writes
        from .lm1.emulator import Emulator

        width, height = machine.display_for(slug) or (0, 0)
        for (case, rounds), (_c, expected) in zip(
            programs.rounds_for_problem(slug), programs.frames_for_problem(slug), strict=True
        ):
            res = Emulator(programs.load(slug)).run(rounds, max_instructions=3_000_000)
            got = frames_from_writes(res.display_writes, width=width, height=height)
            want = [f for rf in expected for f in rf]
            if got != want:
                return False, f"{slug}: frames differ on {case!r} (emulator)"
        return True, f"{slug}: all public frame cases match (emulator)"

    res = optimize.verify(path, slug, tick_cap=tick_cap)
    failed = [c.name for c in res.cases if not c.passed]
    if not res.passed:
        return False, f"{slug}: {res.n_passed}/{len(res.cases)} public cases — failed {failed}"
    return (
        True,
        f"{slug}: {res.n_passed}/{len(res.cases)} public cases, avg {res.avg_ticks:,.0f} ticks",
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send", help="verify locally, submit, poll, archive")
    s.add_argument("slug")
    s.add_argument("--file", help="grid to send (default tasks/solutions/<slug>_cpu.man)")
    s.add_argument("--note", default="", help="free-form text for the .descr")
    s.add_argument("--force", action="store_true", help="submit even if the local check fails")
    s.add_argument(
        "--resend",
        action="store_true",
        help="submit even if this exact grid was already submitted",
    )
    s.add_argument("--dry-run", action="store_true", help="check and print, do not submit")
    s.add_argument("--timeout", type=float, default=600.0)
    s.add_argument("--tick-cap", type=int, default=3_000_000)

    g = sub.add_parser("get", help="poll one submission id")
    g.add_argument("id")

    ls = sub.add_parser("list", help="what is archived, best first per task")
    ls.add_argument("slug", nargs="?")

    args = ap.parse_args(argv)

    if args.cmd == "get":
        print(poll(args.id, timeout=5.0).summary())
        return 0

    if args.cmd == "list":
        slugs = (
            [args.slug] if args.slug else sorted(p.name for p in ARCHIVE.glob("*") if p.is_dir())
        )
        if not slugs:
            print(f"nothing archived under {ARCHIVE.relative_to(REPO)}/")
            return 0
        for slug in slugs:
            entries = sorted((ARCHIVE / slug).glob("*.man"))
            best = best_archived(slug)
            print(
                f"{slug}: {len(entries)} submission(s)"
                + (f", best score {best[0]:,}" if best else "")
            )
            for p in entries:
                print(f"    {p.name}")
        return 0

    # ── send ─────────────────────────────────────────────────────────────────
    slug = args.slug
    path = Path(args.file) if args.file else SOLUTION_DIR / f"{slug}_cpu.man"
    if not path.exists():
        print(f"no grid at {path}")
        return 1
    program = path.read_text(encoding="utf-8")

    ok, detail = _local_check(slug, path, args.tick_cap)
    print(f"local check: {detail}")
    if not ok and not args.force:
        print("refusing to spend a submission on a failing grid; --force to override")
        return 1

    rows = program.rstrip("\n").split("\n")
    print(f"grid {max(len(r) for r in rows)}x{len(rows)}, problem {problem_id(slug)}")

    # Identical grid already submitted? Don't spend a slot re-learning the answer.
    # Only 5 submissions may be pending, grading is asynchronous, and the server
    # keeps our best — so a byte-identical resend can only cost time.
    dup = already_sent(slug, program)
    if dup and not args.resend:
        man, tag = dup
        verdict = (
            "unscored (it did not pass every case)" if tag == "unscored" else f"score {int(tag):,}"
        )
        print(f"already submitted: this exact grid is {man.name} — {verdict}")
        descr = man.with_suffix(".descr")
        if descr.exists():
            for line in descr.read_text(encoding="utf-8").splitlines():
                if line.startswith(("verdict", "submitted")):
                    print(f"  {line}")
        print("not resending; pass --resend if you really want the server to re-grade it")
        return 0
    if dup and args.resend:
        print(f"--resend: submitting a grid identical to {dup[0].name} anyway")

    if args.dry_run:
        if dup:
            print("dry run: would be a duplicate of " + dup[0].name)
        print("dry run: not submitting")
        return 0

    sid = submit(slug, program)
    print(f"submitted {sid}")
    verdict = poll(sid, timeout=args.timeout, on_state=lambda v: print(f"  ... {v.status}"))
    print(verdict.summary())

    commit = os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip() or "unknown"
    man = archive(
        slug,
        program,
        verdict,
        note=args.note,
        extra={"commit": commit, "local": detail, "source": str(path.resolve().relative_to(REPO))},
    )
    print(f"archived {man.relative_to(REPO)}")
    return 0 if verdict.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
