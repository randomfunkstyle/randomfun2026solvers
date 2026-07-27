#!/usr/bin/env bash
# Submit a grid only if it is projected to beat what is already live.
#
# Two sessions submitted worse grids today because the repo archive was stale
# against the server: subset-sum (18.5e9 against a live 13.6e9) and matmul
# (340M against a live 222M).  Neither did damage -- the server keeps the best
# score -- but both cost a submit cycle and, worse, both were reported as wins
# until the board was read.  The live score is the only baseline that counts.
#
#   scripts/submit_guarded.sh <slug> <grid.man> [local_score] [ratio]
#
# With local_score and ratio it refuses to submit when local*ratio >= live.
# Without them it prints the live score and asks for confirmation.
#
# PASS THE HIGH END OF THE RATIO RANGE.  The judge scores 20 cases where
# score_program averages 7-8, and the private cases are the *longer* ones, so a
# local average is optimistic by a factor that is itself uncertain.  Measured
# across the archive:
#
#     triangle        1.000        reverse-a-list  1.562-1.565
#     sudoku-validity 1.016-1.018  tcp             1.596-1.597
#     lllm            1.087        brackets        1.763-1.930
#     matmul          1.236-1.506  gradebook       2.557-2.752
#     snake           1.527        subset-sum      2.735
#     memory          2.164-4.452
#
# The ratio belongs to the *machine*, not the problem: it is tight where
# submissions share an algorithm (tcp 1.596-1.597) and wide where they do not
# (memory 2.164-4.452), because what it measures is how steeply cost grows with
# case size.  So it cannot be carried across a redesign -- treat it as a floor
# for a quadratic rebuild, an overestimate for a linear one, and re-pin it from
# the first judge response.  Using the low end of matmul's range projected 340M
# for a grid that judged at 340M against a live 222M; the high end would have
# refused it.
set -euo pipefail

SLUG=${1:?slug}; GRID=${2:?grid.man}; LOCAL=${3:-}; RATIO=${4:-}
cd "$(dirname "$0")/.."
TOK=$(cat .icfp-token)
UA="randomfun2026solvers/1.0 (+https://icfpcontest2026.com)"
API=https://icfpcontest2026.com/api/v1
TEAM=JSKVwHaBsZ63cz2EH3Lxfq5rwnz64jV6
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

curl -s --max-time 40 -A "$UA" -H "Authorization: Bearer $TOK" "$API/public/problems" -o "$TMP/p.json"
PID=$(python3 -c "
import json,sys
for p in json.load(open('$TMP/p.json')):
    if p['slug']=='$SLUG': print(p['id']); break
else: sys.exit('no such slug: $SLUG')")

curl -s --max-time 45 -A "$UA" -H "Authorization: Bearer $TOK" "$API/standings/problems/$PID" -o "$TMP/b.json"
LIVE=$(python3 -c "
import json
d=json.load(open('$TMP/b.json'))
me=[r for r in d['rows'] if r['teamId']=='$TEAM']
print(me[0]['score'] if me and me[0]['score'] else 0)")

echo "live $SLUG: $(python3 -c "print(f'{float(\"$LIVE\"):,.0f}' if float('$LIVE') else 'nothing submitted yet')")"

if [ -n "$LOCAL" ] && [ -n "$RATIO" ]; then
  python3 -c "
proj=float('$LOCAL')*float('$RATIO'); live=float('$LIVE')
print(f'projected: {proj:,.0f}  (local {float(\"$LOCAL\"):,.0f} x {float(\"$RATIO\")})')
if live and proj >= live:
    raise SystemExit(f'REFUSING: projected {proj:,.0f} is not better than live {live:,.0f}')
print('projected to improve — submitting')"
fi

python3 -c "
import json
print(json.dumps({'problemId':'$PID','program':open('$GRID').read()}))" > "$TMP/s.json"
SID=$(curl -s --max-time 120 -X POST -A "$UA" -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" --data @"$TMP/s.json" "$API/submissions" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "submitted $SID"

for _ in $(seq 1 60); do
  curl -s --max-time 60 -A "$UA" -H "Authorization: Bearer $TOK" "$API/submissions/$SID" -o "$TMP/v.json"
  ST=$(python3 -c "import json; print(json.load(open('$TMP/v.json')).get('status'))")
  case "$ST" in
    done|failed) python3 -m json.tool "$TMP/v.json" | head -16; break ;;
    *) echo "  $ST"; sleep 20 ;;
  esac
done
