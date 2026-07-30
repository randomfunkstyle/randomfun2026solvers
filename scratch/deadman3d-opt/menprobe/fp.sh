#!/bin/sh
# Run flushprobe.py from this worktree with the right PYTHONPATH, logging to
# $LOG (default /tmp/dm3flush/$TAG.log). Knobs come from the environment.
WT=$(cd "$(dirname "$0")/../../.." && pwd)
mkdir -p /tmp/dm3flush
LOG=${LOG:-/tmp/dm3flush/${TAG:-run}.log}
cd "$WT" || exit 1
PYTHONPATH="$WT/solvers/python" exec uv run python \
  "$WT/scratch/deadman3d-opt/menprobe/flushprobe.py" >"$LOG" 2>&1
