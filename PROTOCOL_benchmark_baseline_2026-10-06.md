# Protocol: benchmark infrastructure and first baseline (2026-10-06)

Status: nothing in the QLever code changed yet that should affect performance. This protocol records what was measured, how, and what the numbers mean. The point of the day was to build a measurement setup we can trust *before* the `SplitLayoutId` PRs land.

## 1. Goal

Have a reproducible way to say "PR X made QLever N % slower/faster", for two levels:

1. Micro benchmarks (QLever's own `benchmark/` framework) for single operations.
2. End-to-end (E2E): build an index, start a server, run the 55 queries, compare two QLever versions.

## 1a. What is in the measured branch

"new" = local `create_64bit_benchmarks` at `eb72bbf6`, based on master `a815b3b7` ("old"). Diff against master: 39 files, +2327 / -133 lines. The branch is a merge of these PR branches (the local tip is 5 commits ahead of `origin/create_64bit_benchmarks`, the `extract_datatype` merge is not pushed there):

| Part | PR branch | What it adds / changes | Runtime relevant? |
|---|---|---|---|
| Split-layout base classes | `create_64bit_id_base_classes` ([#3462](https://github.com/ad-freiburg/qlever/pull/3462), merged to master on 2026-10-08) | New, unused types in `src/engine/idTable/splitLayout/`: `IdRef`, `IdColumn`, `IdColumnIterator`, `IdColumnVector`, `SplitLayoutIdBitRepresentation`; their tests in `test/engine/idTable/splitLayout/`; `IdColumnBenchmark`. Small touches in `IdTable.h` (type aliases) and the removal of the old `IdColumn.h` stub. | No, nothing uses them yet |
| Replace pointer-to-member by lambdas | `create_64bit_id_replace_lambdas` ([#3460](https://github.com/ad-freiburg/qlever/pull/3460), merged to master on 2026-10-07) | `ValueId` gets `isUndefinedL`, `isDefinedL`, `getBitsL`, `getDatatypeL`. Call sites changed from `&Id::isUndefined` etc. to the lambdas: `MultiColumnJoin`, `OptionalJoin`, `Minus`, `ExistsJoin`, `EmptyPath`, `JoinAlgorithms.h`, `GraphComputation.h`, `ExternalSortFunctors.h`, `NamedResultCacheSerializer.h`. New tests (`ValueIdTest`, `MultiColumnJoin.undefInJoinColumns`, small test fixes). | Yes, same code path, expected equal (see section 2) |
| Benchmarks | `create_64bit_benchmarks` | `LambdaReplacementBenchmark`, `UndefHandlingBenchmark`, `DatatypeTrivialBenchmark` (+ CMake entries). | No (separate binaries) |
| `Datatype` header | `create_64bit_id_extract_datatype` ([#3615](https://github.com/ad-freiburg/qlever/pull/3615), open) | `Datatype`, `isDatatypeTrivial`, `toString` moved from `ValueId.h` into `src/global/Datatype.h` (unchanged logic), `test/DatatypeTest.cpp`. | No, header move only |

Not in the branch (separate PRs, still open or pending): `assign_swap`, `remaining_changes` (`IdColumn` alias in `EmptyPath`/`PathSearch`), `trivial_type_changes`, `tie_to_array`, `bytes_per_column_entry`. The column-storage-traits PR (D1) is not written yet.

So the E2E comparison below mostly measures the lambda replacement; the other parts cannot change behaviour.

## 2. Micro benchmarks (`benchmark/splitLayout/`)

| Benchmark | Question | Result |
|---|---|---|
| `LambdaReplacementBenchmark` | Is a lambda slower than `&Id::isUndefined` / `&Id::getBits` (A6)? | Equal in real patterns (`any_of`, `find`, graph lookup). No reason to keep pointer-to-member. |
| `UndefHandlingBenchmark` | What does the `isCheap` check in `MultiColumnJoin` cost, and what does the generic (UNDEF) path cost? | Cheap path 0.061 s, 1 % UNDEF in a join column 0.56–0.58 s (1M rows, 2 join columns), i.e. generic path about 9x slower. The `isCheap` scan is about 2 ms, about 3 % of the cheap join. |
| `DatatypeTrivialBenchmark` | `isDatatypeTrivial` with `contains` vs `switch`? | `contains` about 0.0015 s, `switch` about 0.0025 s on 10M datatypes, consistent over 3 data sets. Keep `contains`. |

Pitfalls found while writing them (all fixed): cold-cache first measurement (needs warm-up), compile-time array size changing codegen (use `std::vector`), lazy `transform` measuring nothing, groups copied instead of referenced.

Test added: `MultiColumnJoin.undefInJoinColumns` (UNDEF left only / right only / both), passes.

## 3. E2E tooling

Old harness problem: it measured **cache hits** (`computeResult: 0ms`), so numbers were meaningless. Replaced by:

- `scripts/qlever_ab.py`: uses QLever's own `qlever` CLI (`index`, `start`, `benchmark-queries`, `stop`), runs each version in a Docker container built from the repo `Dockerfile` (same as CI). Image tag is a content hash of the sources, so unchanged code is never rebuilt.
- Server cache is off (`--cache-max-size 0B`; `--cache-max-num-entries 0` does *not* disable it), cache cleared per query.
- Fixed inputs: `dataset/scientists.nt` (369,661 triples), frozen `queries_no_text.json` (55 queries).
- Method: A/B in one session, alternating order per round (ABBA), 2 warm-ups + 5 reps per query, median per round, ratio new/old, per-round min/max shown. Ratios instead of absolute times so results transfer between machines.
- `scripts/run_ab_benchmark.py`: same idea for native (non-container) binaries, used for the noise floor.

Findings on the way:
- Docker build with 4 builder CPUs ran out of VM memory (swap full, step 758/877). With 2 builder CPUs it works (28.1 min). Default `--build-cpus 2`.
- Old image tag formula (commit + diff) triggered needless rebuilds. Now content-based (`git ls-files` + sha256).

## 4. Measurements

Machine: Apple M5, 10 CPUs, 16 GB, AC power. Docker VM 10 CPUs / 11.7 GiB. Container limits `--cpus 4 --memory 8g --threads 4`.

### 4.1 Noise floor (A/A, native binaries, same binary as both labels, 3 rounds x 5 reps)

- `query_client` geometric mean 0.993, 19 of 55 queries "slower".
- Per query up to about +-15 % for sub-millisecond / few-ms queries.
- `build_wall` +-10 %, `load_wall` +-14..24 %, RSS +-10 %.

### 4.2 Container sanity run (A/A, 8 queries)

`query_client` geometric mean 1.002, per-query ratios 0.95–1.03 (one outlier [0.96..1.14]). Container pipeline works and is not biased.

### 4.3 Real run: master vs. `create_64bit_benchmarks` (`results/qab_20261006_160305/`)

- old = master `a815b3b7`, new = branch `create_64bit_benchmarks` `eb72bbf6`, 5 rounds x 5 reps, all 55 queries, container mode.

| Metric | Ratio new/old | Per-round range |
|---|---|---|
| index size (all parts) | 1.000 (9.026 MiB both) | – |
| build_wall | 1.007 | 0.62..1.05 |
| load_wall | 0.976 | 0.78..1.31 |
| server_peak_rss (docker stats sampling) | 1.070 | 0.85..1.19 |
| query_client, geometric mean | **1.010** | rounds: 1.001 / 1.018 / 1.016 / 1.007 / 0.988 |
| query_server_exec, geometric mean | 1.025 | integer ms, 1–3 ms, meaningless |
| query_server_planning, geometric mean | 0.979 | integer ms |

Details for `query_client`:
- 36 of 55 queries slower, but only 3 in all 5 rounds: `06_scientists-order-by-aggregate-count` x1.048, `11_giant-int-scientists` x1.027, `51_CONCAT` x1.020. No query faster in all 5 rounds.
- By baseline time: 5–15 ms median ratio 1.001 (n=30), 15–40 ms median 1.006 (n=25).
- Largest single value: `08_group-by-profession-average-height` x1.105 with range [0.85..1.20], i.e. noise.

## 5. Interpretation

- The overall difference (+1 %) is the same size as the round-to-round spread of the geometric mean (about +-1.5 %) and inside the per-query noise (+-5 % in containers, +-15 % for tiny queries).
- "All 5 rounds same direction" has probability about 6 % per query under pure noise (2 x (1/2)^5). With 55 queries about 3.4 such queries are expected; we see 3. So the consistent ones are compatible with chance.
- The branches are not identical: the benchmark branch contains merged prep PRs (lambdas, `Datatype` header, base classes, new benchmark files). Even identical sources can differ 1–2 % through code layout and inlining.
- Index files are bit-size identical, so no format change leaked in.

Conclusion: no measurable regression from the prep PRs. The setup is good enough to detect effects of about 5 % or more on the 15–40 ms queries, not smaller ones.

## 6. Limitations

- Dataset is tiny (queries 5–40 ms, index 9 MiB). Noise from process/HTTP overhead is large relative to query time. Memory/cache effects of a bigger Id will not show up here.
- Memory sampling via `docker stats` is coarse. `build_cpu`/`build_peak_rss` are not recorded in container mode.
- Laptop, Docker VM in the background. Server-side times are integer ms.
- Only 5 rounds.

## 7. Next steps

1. A/A run with identical image (5 rounds, 55 queries) to get the container noise floor under exactly the same settings as 4.3, then compare to the +1 %.
2. Larger dataset (synthetic or bigger real one) so that effects of the Id size become visible; keep the small one as quick check.
3. Re-run the three flagged queries with more rounds; optionally profile `06_scientists-order-by-aggregate-count` with Samply (separate `-g -fno-omit-frame-pointer` build dir).
4. Archive a baseline run (master) so later PRs can be compared against it without rebuilding.
5. Integrate `qlever index-stats`.

## 8. How to reproduce

```bash
cd qlever-perf-testsuite
python3 scripts/qlever_ab.py --container --cpus 4 --memory 8g --threads 4 \
  --rounds 5 --reps 5 --warmup 2 \
  --checkout old=<master-sha> --target new=<path-to-qlever-checkout> \
  --workdir <scratch-dir>
```

Raw data: `qlever-perf-testsuite/results/qab_20261006_160305/` (`report.txt`, `summary.csv`, `measurements.csv`, `context.json`) and `results/aa_noise_2026-10-06/`.
