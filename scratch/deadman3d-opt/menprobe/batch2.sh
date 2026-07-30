#!/bin/sh
# The two 21-round rows that decide it: the rebuilt baseline and the candidate,
# both profiled, both gated on passed/fatal at the full tour.
D=$(dirname "$0")
TAG=G21BASE ROUNDS=21 sh "$D/fp.sh"
TAG=G21D2JB ROUNDS=21 DRAIN_SEEK=2 DRAIN_OPS=JMPF,BRZ sh "$D/fp.sh"
echo done
