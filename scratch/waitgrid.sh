#!/bin/sh
# Exit when either grid file changes size (a smaller/rebuilt box) or the other
# attempt lands a grid at all.
cd /Users/oleg/projects/randomfun2026solvers/.claude/worktrees/agent-a420a5720678441b2 || exit 1
sig() {
  wc -c < tasks/solutions/pathfinder_grid.man 2>/dev/null
  wc -c < tasks/solutions/pathfinder_ring.man 2>/dev/null
}
before=$(sig)
while [ "$(sig)" = "$before" ]; do
  sleep 30
done
for f in tasks/solutions/pathfinder_grid.man tasks/solutions/pathfinder_ring.man; do
  [ -f "$f" ] && awk -v n="$f" \
    '{ if (length($0)>m) m=length($0); r++ } END { print n, "rows", r, "width", m }' "$f"
done
