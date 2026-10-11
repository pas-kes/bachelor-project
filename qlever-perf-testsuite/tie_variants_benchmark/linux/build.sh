#!/bin/bash
# usage: build.sh <g++|clang++> <worktree name> <tag>
CXX=$1; TREE=$2; TAG=$3
mapfile -t FLAGS < /w/flags.txt
mkdir -p /w/out
C="$CXX -std=gnu++20 -O3 -DNDEBUG ${FLAGS[*]} -I/qv/worktrees/$TREE/src -I/w"
$CXX -std=gnu++20 -O3 -DNDEBUG "${FLAGS[@]}" -I/qv/worktrees/$TREE/src -I/w -c /w/benchmark/TieVariantsBenchmark.cpp -o /w/out/tv_$TAG.o 2> /w/out/compile_$TAG.log || { echo COMPILE_FAILED $TAG; head -30 /w/out/compile_$TAG.log; exit 1; }
$CXX -std=gnu++20 -O2 -DNDEBUG "${FLAGS[@]}" -I/qv/worktrees/$TREE/src -I/w -c /w/stubs.cpp -o /w/out/stubs_$TAG.o 2> /w/out/stubs_$TAG.log || { echo STUBS_FAILED $TAG; head -20 /w/out/stubs_$TAG.log; exit 1; }
$CXX -O2 -std=gnu++20 -I/w -c /w/main.cpp -o /w/out/main_$TAG.o || exit 1
$CXX /w/out/tv_$TAG.o /w/out/stubs_$TAG.o /w/out/main_$TAG.o -o /w/out/bench_$TAG 2> /w/out/link_$TAG.log || { echo LINK_FAILED $TAG; grep -o "undefined reference to.*" /w/out/link_$TAG.log | sort -u | head; exit 1; }
echo built $TAG
