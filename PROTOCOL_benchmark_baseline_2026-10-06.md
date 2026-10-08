# Protocol: benchmark infrastructure and first baseline (2026-10-06)

Nothing in the QLever code has changed yet that should affect the performance.
In this protocol I write down what I measured, how I measured it and what the
numbers mean. The goal of the day was a measurement setup that I can trust
before the `SplitLayoutId` PRs are merged.

## 1. Goal

I want to be able to say "PR X made QLever N % slower or faster", on two levels:

1. Micro benchmarks with QLever's own `benchmark/` framework for single
   operations.
2. End to end (E2E): build an index, start a server, run the 55 queries and
   compare two QLever versions.

## 1a. What is in the measured branch

"new" is my local branch `create_64bit_benchmarks` at `eb72bbf6`, based on
master `a815b3b7` ("old"). The diff to master has 39 files, +2327 / -133 lines.
The branch is a merge of these PR branches. The local tip is 5 commits ahead of
`origin/create_64bit_benchmarks`, because the merge of `extract_datatype` is not
pushed there.

| Part | PR branch | What it adds or changes | Relevant at runtime? |
|---|---|---|---|
| Split layout base classes | `create_64bit_id_base_classes` ([#3462](https://github.com/ad-freiburg/qlever/pull/3462), merged to master on 2026-10-08) | New types in `src/engine/idTable/splitLayout/` that nothing uses yet: `IdRef`, `IdColumn`, `IdColumnIterator`, `IdColumnVector`, `SplitLayoutIdBitRepresentation`, their tests in `test/engine/idTable/splitLayout/` and `IdColumnBenchmark`. Small changes in `IdTable.h` (type aliases) and the old `IdColumn.h` stub is removed. | no |
| Pointer-to-member replaced by lambdas | `create_64bit_id_replace_lambdas` ([#3460](https://github.com/ad-freiburg/qlever/pull/3460), merged to master on 2026-10-07) | `ValueId` gets `isUndefinedL`, `isDefinedL`, `getBitsL` and `getDatatypeL`. The call sites change from `&Id::isUndefined` and similar to the lambdas: `MultiColumnJoin`, `OptionalJoin`, `Minus`, `ExistsJoin`, `EmptyPath`, `JoinAlgorithms.h`, `GraphComputation.h`, `ExternalSortFunctors.h`, `NamedResultCacheSerializer.h`. New tests (`ValueIdTest`, `MultiColumnJoin.undefInJoinColumns`, small test fixes). | yes, same code path, I expect no difference (section 2) |
| Benchmarks | `create_64bit_benchmarks` | `LambdaReplacementBenchmark`, `UndefHandlingBenchmark`, `DatatypeTrivialBenchmark` and the CMake entries. | no, separate binaries |
| `Datatype` header | `create_64bit_id_extract_datatype` ([#3615](https://github.com/ad-freiburg/qlever/pull/3615), open) | `Datatype`, `isDatatypeTrivial` and `toString` move from `ValueId.h` to `src/global/Datatype.h` with unchanged logic, plus `test/DatatypeTest.cpp`. | no, only a moved header |

Not in the branch: `assign_swap`, `remaining_changes` (the `IdColumn` alias in
`EmptyPath` and `PathSearch`), `trivial_type_changes`, `tie_to_array` and
`bytes_per_column_entry`. The PR for the column storage traits (D1) is not
written yet.

So the E2E comparison below mainly measures the lambda replacement. The other
parts cannot change the behaviour.

## 2. Micro benchmarks (`benchmark/splitLayout/`)

| Benchmark | Question | Result |
|---|---|---|
| `LambdaReplacementBenchmark` | Is a lambda slower than `&Id::isUndefined` or `&Id::getBits` (A6)? | Equal in the realistic patterns (`any_of`, `find`, lookup of the graph). There is no reason to keep the pointer-to-member. |
| `UndefHandlingBenchmark` | What does the `isCheap` check in `MultiColumnJoin` cost, and what does the generic path with UNDEF cost? | Cheap path 0.061 s, with 1 % UNDEF in a join column 0.56 to 0.58 s (1M rows, 2 join columns), so the generic path is about 9 times slower. The `isCheap` scan takes about 2 ms, which is about 3 % of the cheap join. |
| `DatatypeTrivialBenchmark` | `isDatatypeTrivial` with `contains` or with `switch`? | `contains` about 0.0015 s, `switch` about 0.0025 s for 10M datatypes, the same for 3 data sets. I keep `contains`. |

Problems I ran into while writing them, all fixed: the first measurement is
slower because of cold caches (needs a warm-up), an array size known at compile
time changed the generated code (I use a `std::vector`), a lazy `transform`
measured nothing, and groups were copied instead of referenced.

I added the test `MultiColumnJoin.undefInJoinColumns` (UNDEF only left, only
right, and on both sides). It passes.

## 3. E2E tooling

The old harness measured cache hits (`computeResult: 0ms`), so its numbers were
useless. I replaced it:

- `scripts/qlever_ab.py` uses QLever's own `qlever` tool (`index`, `start`,
  `benchmark-queries`, `stop`) and runs each version in a Docker container that
  is built from the `Dockerfile` of the repo (like in the CI). The image tag is a
  hash of the sources, so unchanged code is not built again.
- The server cache is off (`--cache-max-size 0B`, `--cache-max-num-entries 0`
  does not switch it off) and it is cleared for every query.
- Fixed inputs: `dataset/scientists.nt` (369,661 triples) and the frozen
  `queries_no_text.json` (55 queries).
- Method: A/B in one session, the order alternates per round (ABBA), 2 warm-ups
  and 5 repetitions per query, median per round, ratio new/old, min and max per
  round. I use ratios and not absolute times, so that the results can be
  compared between machines.
- `scripts/run_ab_benchmark.py` does the same for native binaries without
  containers. I used it for the noise floor.

Two things I found on the way:

- The Docker build with 4 builder CPUs ran out of memory in the VM (swap full at
  step 758/877). With 2 builder CPUs it works and takes 28.1 min. So the default
  is `--build-cpus 2`.
- The first formula for the image tag (commit and diff) caused rebuilds that
  were not needed. Now the tag is a hash of the contents (`git ls-files` and
  sha256).

## 4. Measurements

Machine: Apple M5, 10 CPUs, 16 GB, on the charger. Docker VM with 10 CPUs and
11.7 GiB. Limits of the containers: `--cpus 4 --memory 8g --threads 4`.

### 4.1 Noise floor (A/A, native, the same binary under both labels, 3 rounds x 5 repetitions)

- `query_client` geometric mean 0.993, 19 of 55 queries "slower".
- Per query up to about +-15 % for queries below a few ms.
- `build_wall` +-10 %, `load_wall` +-14 to 24 %, RSS +-10 %.

### 4.2 Container check (A/A, 8 queries)

`query_client` geometric mean 1.002, ratios per query 0.95 to 1.03 (one outlier
with [0.96..1.14]). The container setup works and shows no systematic offset.

### 4.3 Real run: master against `create_64bit_benchmarks` (`results/qab_20261006_160305/`)

old is master `a815b3b7`, new is the branch `create_64bit_benchmarks` at
`eb72bbf6`. 5 rounds x 5 repetitions, all 55 queries, container mode.

| Metric | Ratio new/old | Range over the rounds |
|---|---|---|
| index size (all parts) | 1.000 (9.026 MiB both) | |
| build_wall | 1.007 | 0.62..1.05 |
| load_wall | 0.976 | 0.78..1.31 |
| server_peak_rss (sampled with docker stats) | 1.070 | 0.85..1.19 |
| query_client, geometric mean | 1.010 | rounds: 1.001 / 1.018 / 1.016 / 1.007 / 0.988 |
| query_server_exec, geometric mean | 1.025 | whole ms of 1 to 3 ms, not usable |
| query_server_planning, geometric mean | 0.979 | whole ms |

Details for `query_client`:

- 36 of 55 queries are slower, but only 3 in all 5 rounds:
  `06_scientists-order-by-aggregate-count` x1.048, `11_giant-int-scientists`
  x1.027 and `51_CONCAT` x1.020. No query is faster in all 5 rounds.
- By time of the baseline: queries of 5 to 15 ms have a median ratio of 1.001
  (n=30), queries of 15 to 40 ms 1.006 (n=25).
- The largest single value is `08_group-by-profession-average-height` with x1.105
  and a range of [0.85..1.20], so that is noise.

## 5. Interpretation

- The overall difference of +1 % is as large as the spread of the geometric mean
  between the rounds (about +-1.5 %) and lies inside the noise per query (+-5 %
  in the containers, +-15 % for very small queries).
- With pure noise, a query goes in the same direction in all 5 rounds with a
  probability of about 6 % (2 x (1/2)^5). For 55 queries I expect about 3.4 such
  queries, and I see 3. So these are compatible with chance.
- The two branches are not identical. The benchmark branch contains merged prep
  PRs (lambdas, `Datatype` header, base classes, new benchmark files). Even with
  identical sources, two binaries can differ by 1 to 2 % because of code layout
  and inlining.
- The index files have exactly the same size, so no change of the format got in.

My conclusion is that the prep PRs show no measurable slowdown. The setup can
detect effects of about 5 % or more for the queries of 15 to 40 ms, but nothing
smaller.

## 6. Limits

- The data set is tiny (queries of 5 to 40 ms, index of 9 MiB). The noise from
  the process and HTTP is large compared to the query time. Effects of a bigger
  Id on memory and cache do not show up here.
- The memory sampling with `docker stats` is coarse. `build_cpu` and
  `build_peak_rss` are not recorded in container mode.
- I measured on a laptop with the Docker VM running in the background. The
  server side times are whole ms.
- Only 5 rounds.

## 7. Next steps

1. An A/A run with the identical image (5 rounds, 55 queries), so that I know
   the noise of the container setup with exactly the settings of 4.3. Then I
   compare it to the +1 %.
2. A larger data set (synthetic or a bigger real one), so that the effects of the
   Id size become visible. The small one stays as a quick check.
3. Run the three flagged queries again with more rounds, and maybe profile
   `06_scientists-order-by-aggregate-count` with Samply (own build directory with
   `-g -fno-omit-frame-pointer`).
4. Archive one run of master as baseline, so that later PRs can be compared
   without building it again.
5. Use `qlever index-stats`.

## 8. Reproduce

```bash
cd qlever-perf-testsuite
python3 scripts/qlever_ab.py --container --cpus 4 --memory 8g --threads 4 \
  --rounds 5 --reps 5 --warmup 2 \
  --checkout old=<master-sha> --target new=<path-to-qlever-checkout> \
  --workdir <scratch-dir>
```

The raw data is in `qlever-perf-testsuite/results/qab_20261006_160305/`
(`report.txt`, `summary.csv`, `measurements.csv`, `context.json`) and in
`results/aa_noise_2026-10-06/`.
