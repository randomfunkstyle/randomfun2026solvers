#!/bin/sh
# A 1M-tick run of a built machine on the reference wasm engine, JSON snapshot.
# usage: tick1m.sh <file.man> <input.txt> [ticks]
set -e
cd "$(dirname "$0")/../.."
node littleman/lm.mjs tick "$1" "${3:-1000000}" --input "$(cat "$2")" --json
