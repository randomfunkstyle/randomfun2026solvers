#!/bin/zsh
# Full test suite from this worktree (never the main checkout's .pth resolution).
WT=/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-a55fdea5340ff344a
PY=/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.venv/bin/python
cd $WT || exit 1
export PYTHONPATH=$WT/solvers/python
exec $PY -m pytest -q "$@"
