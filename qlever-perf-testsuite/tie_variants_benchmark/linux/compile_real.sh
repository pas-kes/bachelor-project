#!/bin/bash
# usage: compile_real.sh <g++|clang++> <variant file tag>
CXX=$1; V=$2
mapfile -t FLAGS < /w/flags.txt
$CXX -std=gnu++20 -O3 -DNDEBUG "${FLAGS[@]}" -I/qv/worktrees/exp_plain/src -I/qv/worktrees/exp_plain/src/index -c /w/real/LT_$V.cpp -o /w/real/LT_${V}_$CXX.o 2> /w/real/LT_${V}_$CXX.log && echo "ok $CXX $V" || { echo "FAILED $CXX $V"; head -8 /w/real/LT_${V}_$CXX.log | cut -c1-240; }
