#!/bin/zsh
# run a menprobe script with the worktree's package forced onto PYTHONPATH.
# usage: rp.sh <script.py> [args...]   (stdout/stderr go to the caller)
WT=/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-a55fdea5340ff344a
PY=/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.venv/bin/python
cd $WT/scratch/deadman3d-opt/menprobe || exit 1
export PYTHONPATH=$WT/solvers/python
exec $PY "$@"
