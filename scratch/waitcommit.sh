#!/bin/sh
# Exit as soon as a new commit lands on this worktree's branch.
cd /Users/oleg/projects/randomfun2026solvers/.claude/worktrees/agent-a420a5720678441b2 || exit 1
n=$(git rev-list --count HEAD)
while [ "$(git rev-list --count HEAD)" -le "$n" ]; do
  sleep 20
done
git log --oneline -3
