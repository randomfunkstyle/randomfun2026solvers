# Grading, scoring and submission (ICFP Contest 2026)

From the official `/grading`, `/rules` and `/api-help` pages (raw extracts in
[`reference/`](reference/)). Language spec: [`SPEC.md`](SPEC.md). Problem specs:
[`../tasks/problems/`](../tasks/problems/).

## Program score — lower is always better

| Scoring mode | Formula |
|---|---|
| `footprint-tick` (nearly all problems) | **max(width, height)² × average ticks across all test cases** |
| `footprint` (a few problems) | **max(width, height)²** — speed irrelevant |

- Width and height are the bounding box of your **entire** program (all rooms, all pipes, all whitespace inside the box).
- A test case's tick count runs until your **final correct output value** is emitted (for display problems, until your final frame matches). Ticks after that don't count, and **the program need not halt**.
- Each problem page states its mode; the JSONs in `../tasks/problems/` carry it in `scoring`.

Because footprint is squared, a program is usually better off *narrow and
looping* than wide and unrolled — and `Y` (split, see SPEC.md) lets you add
parallel little men without adding rooms.

## Points and ranking

Up to **2 points per graded problem**:

```
test-case points = passing test cases / total test cases          (max 1)
ranking points   = (eligible teams you rank above or tie) / (other eligible teams)   (max 1)
```

- **Eligibility:** you must pass at least one **private** test case to score points. On a problem with **no** private cases, passing any case makes you eligible.
- Teams are ranked first by number of test cases passed; teams passing *all* cases are then ranked by program score (lower better). Ties allowed. Sole eligible team gets the full ranking point.
- Team total = sum of the best score on every problem. Only your **best submission per problem** counts — submitting can never lower your score.
- Only the **graded** problem sets count; "Practice Problems (Ungraded)" do not.

## Test cases

Public cases are shown in full on the problem page and in the editor's *test
cases* tab (and are served by the API as `publicTestData`). Private cases are
never shown, and are documented as exercising the *same* behaviour as the public
ones — no hidden tricks; they exist to stop hardcoding. Grading runs both.

You pass a case as soon as you emit the correct output; you do not have to halt.

## Rounds

A test case contains one or more **rounds** — each an input/expected-output pair.
All rounds run against a **single run** of the program; there is no reset between
rounds.

- The input for round N+1 is not available until all output for round N has been received.
- A round that expects no output unlocks the next round's input immediately.
- In the editor, `/` separates rounds in the input and expected-output boxes: `1 42 / 2 41 42` is two rounds.
- Display problems can be round-based too; committed frames gate the next round exactly like output does.

## ASCII problems

Some problems read or write ASCII: an integer 0–127 per character. Everything is
still plain decimal integers on the wire — `"hi"` is `104 105`. The editor
auto-enables ASCII mode for those problems (toggleable in the program menu). Full
table at `icfpcontest2026.com/ascii.txt`.

## Limits

- Program size: **10 MB**.
- Step cap: usually **5,000,000 ticks**; a few problems differ, stated on the problem page (`tickCap` in the problem JSON — `null` means the default). Hitting the cap ends the program immediately.
- Additional internal grading limits exist (e.g. wall-clock); well-behaved programs shouldn't see them.

## Submission API

Base URL: `https://icfpcontest2026.com/api/v1`. All responses JSON. The API key
is a **team** bearer token, needed only for submission endpoints.

```sh
# List every released problem: id, slug, name, problemSetName, status. No key needed.
curl https://icfpcontest2026.com/api/v1/public/problems

# One problem: adds description, io, scoring, publicTestData (same cases the editor runs),
# tickCap, privateTestCount. Private cases are never served. No key needed.
curl https://icfpcontest2026.com/api/v1/public/problems/<slug>

# Submit. `program` is the grid itself, newlines and all. Submitting takes the problem *id*
# (everything else takes the slug). Returns 202 {"id":"…","status":"pending"}.
curl -X POST https://icfpcontest2026.com/api/v1/submissions \
  -H "Authorization: Bearer $ICFP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"problemId":"<problem-id>","program":"<source>"}'

# Poll a result. You may only read your own team's submissions.
curl https://icfpcontest2026.com/api/v1/submissions/<submission-id> \
  -H "Authorization: Bearer $ICFP_API_KEY"
```

Submission result fields:

- `status`: `pending` → `running` → `done` | `failed`
- on `done`: `casesPassed` / `casesTotal`, and `output` (the runner's summary)
- on a **full** pass: `score` = `area2` (max(width, height)²) × `avgTicks`, or `area2` alone on `footprint` problems where `avgTicks` is null. All three are null until every case passes.
- `loadError`: set instead when the program failed to load — no test case was run.

Errors are `{"error":{"code":"…","message":"…"}}` with a matching HTTP status:

| Status | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | missing or invalid key |
| 403 | `forbidden` | the problem is practice-only |
| 404 | `not_found` | no such problem, or not released |
| 413 | `payload_too_large` | programs cap at 10 MB |
| 429 | `too_many_requests` | at most 5 of your submissions may be queued; wait for one to finish |

Grading is asynchronous — track submissions on `/submissions`.

## Contest rules (summary)

- Open contest; anyone may enter except the organisers. No registration fee.
- Teams of any size; a contestant may belong to only **one** team. Teams may not divide, merge, or collaborate after the start.
- Any languages, platforms and tools, **including AI agents**.
- One set of credentials per team, shared by its members — using more than one set may mean disqualification.
- Prize eligibility requires submitting source code at the end of the contest; multiple submissions during the contest are fine, best scores show on the live scoreboard.
- Scoreboard freezes: **hours 22–26** (around the lightning round deadline) and **from hour 70** to the end.
- Don't attack the contest server; organisers may monitor and investigate; their decisions are final.
- Contestants keep IP ownership; organisers get a non-exclusive perpetual licence to use/publish submissions for contest purposes.
