#!/bin/sh
# Exit as soon as either layout attempt produces a grid.
cd /Users/oleg/projects/randomfun2026solvers/.claude/worktrees/agent-a420a5720678441b2 || exit 1
while [ ! -f tasks/solutions/pathfinder_ring.man ] \
   && [ ! -f tasks/solutions/pathfinder_grid.man ]; do
  sleep 30
done
ls -l tasks/solutions/pathfinder_ring.man tasks/solutions/pathfinder_grid.man 2>/dev/null
