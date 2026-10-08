# Optimizations on `refactor/create_64bit_id_type`

Widening `Id`/`ValueId` to full 64-bit payloads plus split-column storage
(datatype bytes and payload words kept in separate arrays) makes individual
`Id` operations (comparison, copy) more expensive, since they go through the
`BasicIdRef` proxy instead of a raw 64-bit word. Every optimization below
uses the same idea: read/write the separate byte arrays directly instead of
going through the proxy, and move as much work as possible from "per row" to
"per run of one datatype".

## 1. GROUP BY: fast path for a single grouping column

`GroupByImpl::searchBlockBoundaries` (`src/engine/GroupByImpl.cpp`) finds
group boundaries directly on the datatype bytes and payload words via
`memchr`/bitwise comparison instead of comparing materialized `Id`s, when
there is exactly one grouping column and it contains no `LocalVocabIndex`
values.

**Measured** (`benchmark/GroupByFastPathBenchmark.cpp`: `GROUP BY ?a
(AVG(?b))` over 3M rows / 100k groups, sorted, single `IntId` group column,
Release build, no concurrent load, 5 runs each):

| Checkpoint | Mean time |
|---|---|
| Before the fast path (commit `6043f519`) | 33.1 ms |
| After the fast path (commit `3ef9c696`) | 31.4 ms |
| Final (all three optimizations, commit `429312e8`) | 32.7 ms |

No significant difference — the three ranges overlap substantially. At this
scale, the per-row cost of evaluating `AVG(?b)` and writing an output row
dominates the total time far more than the block-boundary search the fast
path speeds up (unlike sort/join below, where the optimized code path *is*
essentially the whole operation). A configuration with many more, smaller
groups (i.e. more boundary checks per row of aggregation work) might show a
larger effect; not tested further given the time budget for this benchmark.

## 2. Sort: `SingleKeySorter` (`src/index/IdColumnSort.{h,cpp}`)

Sorts an `IdTable` by one column by sorting a `(payload, rowIndex)`
permutation and then applying it to all columns, instead of comparing/
swapping the row proxies directly.

1. **`partitionByDatatype`** — counting-sorts the pairs by datatype byte. If
   every row shares one datatype (the common case in practice), it instead
   fills the permutation directly in original order, skipping the
   bucket-offset bookkeeping — which isn't just extra work to skip: its
   per-element `positions[type]++` indirection creates a data dependency
   chain that defeats auto-vectorization.
2. **`sortPartitions`** — sorts each datatype partition by payload (in
   parallel via `ad_utility::parallel_sort`), or the single partition
   directly if there's only one datatype.
3. **`applyPermutation`** — applies the permutation to every column
   (gather into a scratch buffer, copy back), spread across up to
   `numThreads` worker threads (`ad_utility::JThread`), one disjoint
   subset of columns per thread.

`SingleKeySorter::isSortable(table, keyColumn)` checks up front whether the
column contains `LocalVocabIndex` IDs (not bitwise-comparable); if so,
`IdTableUtils::sort` falls back to the generic comparison sort.

**Measured** (`benchmark/IdTableColumnBenchmark.cpp`: sort a 3M-row/
4-column table by its first column, single `VocabIndex` datatype, Release
build, no concurrent load, 5 runs each):

| Checkpoint | Mean time | Δ vs. master |
|---|---|---|
| master (8-byte `Id`, before this branch) | 266.3 ms | – |
| Branch without fast path (`Id` widening only) | 368.7 ms | +38.4 % |
| Branch, bucket-based sort (no Step-3 threading, no single-datatype fast path) | 175.2–178.4 ms | −33 to −34 % |
| Branch, with Step-3 threading + single-datatype fast path | ~168.1 ms | −37 % |

Sub-results from the Step 3 (permutation-apply) threading work: sequential
35.6–38.6 ms vs. threaded 32.2–34.6 ms for that step alone (non-overlapping
ranges). The single-datatype fast path (steps 1–2) then took total sort
time from ~178 ms to ~168 ms (5 runs, ranges 165.6–172.9 ms vs.
176.1–183.0 ms, non-overlapping).

## 3. Join: `ZipperJoiner` (`src/engine/idTable/ZipperJoiner.h`)

Merge join of two sorted `ConstIdColumn`s without UNDEF values, handling
`LocalVocabIndex` IDs (which compare by vocabulary position, not bits)
inline instead of falling back to the generic row-wise join whenever one is
involved:

- **`mergeBitwiseChunk`** — merges the stretches without `LocalVocabIndex`
  IDs run by run of matching datatype: `skipSmallerTypeRun` skips a run
  present on only one side; `mergeEqualTypeRuns` merges two runs of the
  same datatype on their payload words alone (one 64-bit comparison per
  step, as fast as the old packed 64-bit `Id` layout).
- **`mergeSemanticChunk`** — compares elements at and around
  `LocalVocabIndex` positions via `Id::compareThreeWay`.

Used as the join implementation for `JoinImpl::join`'s single-column path
without UNDEF, and for `BlockZipperJoinImpl::joinSubranges`'s fast path in
the generic `JoinAlgorithms.h` (prefiltered index-scan joins).

**Measured** (`benchmark/JoinFastPathBenchmark.cpp`: join two sorted
1M-row tables on a single `IntId` column, ~50% match rate, no UNDEF,
Release build, no concurrent load, 5 runs each):

| Checkpoint | Mean time |
|---|---|
| Before `ZipperJoiner` | 22.2 ms |
| After `ZipperJoiner` (final, commit `429312e8`) | 16.1 ms |

**−27.2 %**, ranges 15.3–18.9 ms vs. 21.2–25.5 ms (non-overlapping). Unlike
GROUP BY, the join operation here is dominated by the merge-join loop
itself rather than by per-row aggregation, so the raw kernel speedup shows
through clearly.

## End-to-end query latency (`scientists` dataset, 51 queries × 5 repetitions)

| Checkpoint | Sum of mean latencies | Δ vs. master |
|---|---|---|
| master | 85.0 ms | – |
| Branch without fast paths | 95.2 ms | +12.0 % |
| Branch + sort/GROUP BY fast path | 97.2 ms | +14.4 % |
| Branch + join kernel | 95.0 ms | +11.8 % |

Neither the sort/GROUP BY fast path nor the join kernel measurably improves
end-to-end latency on this dataset (0 individually significant query
changes between the two checkpoints). `scientists` (18 MB) is too small:
query latency is dominated by HTTP/parsing overhead, not by the sort/join/
GROUP BY share that the fast paths target. The real sort/join gains seen
above (−37 % / −27 %) need datasets where sort/join actually make up a
meaningful share of query time — millions of rows flowing through those
operators, not the few thousand here.

## Raw data

- `idtable_benchmark_summary.csv` / `idtable_benchmark_raw.csv` — sort/scan
  micro-benchmark, all 4 checkpoints.
- `e2e_query_latency_summary.csv` / `e2e_query_latency_raw.csv` — end-to-end,
  all 4 checkpoints.
- The GROUP BY/join fast-path numbers, and the Step-3-threading/
  single-datatype-fast-path sort sub-results, are single-machine
  measurements from this development machine (not lab conditions) taken
  during the optimization/review process; they aren't in the CSVs above
  since the final git history bundles each optimization into one commit.
- Unit test coverage for the new classes: `test/IdColumnSortTest.cpp`
  (`SingleKeySorter`), `test/ZipperJoinerTest.cpp` (`ZipperJoiner` via
  `zipperJoinIdColumns`).
