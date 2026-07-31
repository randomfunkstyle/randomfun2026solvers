#!/bin/zsh
# Ask the reference WASM engine which pipe each r/s binds, on a built grid.
# usage: route.sh <grid.man> <out.txt>
WT=/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-a55fdea5340ff344a
cd $WT/littleman || exit 1
node tools/route-check.mjs "$1" > "$2" 2>&1
echo "exit=$?  lines=$(wc -l < "$2")"
