#!/bin/sh
# The three 6-round rows that price the drain against the corridor it needs.
D=$(dirname "$0")
TAG=DROP6 ROUNDS=6 MEM_PAD=9 ROM_DROP=6 sh "$D/fp.sh"
TAG=DROP10 ROUNDS=6 MEM_PAD=9 ROM_DROP=10 sh "$D/fp.sh"
TAG=D2ALL10 ROUNDS=6 MEM_PAD=9 ROM_DROP=10 DRAIN_SEEK=2 sh "$D/fp.sh"
echo done
