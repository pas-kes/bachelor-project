# Benchmark set for the split layout Id

These are my working notes. They belong to `PLAN_split_layout_id.md`, the
section numbers below refer to it. Results are open until I have measured them.

## 1. Goal

I want to measure the legacy layout (`ValueId`, `std::vector<Id>` columns, 8
bytes per entry) and the new split layout (`SplitLayoutId`, two arrays, 9 bytes
per column entry) with the same code, so that the decisions in the plan are
based on numbers:

- Gate after B2: is the new Id fast enough to continue? (sorting, comparison)
- A4, A5, A6: are the prep PRs really free on the legacy build?
- F1: which configuration becomes the default?

## 2. How the comparison works

- Now (no flag yet) only the legacy side exists. I take baseline numbers on
  master. Where both variants can be written next to each other in one binary, I
  compare them there (for example `std::vector<Id>` against `IdColumnVector`, or
  `&Id::isUndefined` against a lambda).
- After D1 and D2 I build the same benchmark twice, once with the flag off and
  once with it on (two build directories, for example `build/` and
  `build-split/`). I run the same binary with the same config and seed, export
  JSON with `-w` and compare the two files with a small script.
- Rules: Release build, same machine, fixed seeds, laptop on the charger. Every
  benchmark runs at least 5 times and I look at the median, because the
  infrastructure only gives one timing per measurement. The result of every
  measured lambda has to be used (for example log a sum), otherwise the compiler
  removes it, see `benchmark/Usage.md`.

How to run: `./benchmark/<Name> -p` prints, `-w out.json` writes, `-o` shows the
config options and `-s 'key=value'` sets options.

## 3. What exists already

| Benchmark | What it measures | Useful for |
|---|---|---|
| `JoinAlgorithmBenchmark` | hash join and merge/galloping joins on random `IdTable`s, many config options (sizes, overlap, columns) | baseline for comparing and merging (5.2, 5.3) |
| `GroupByHashMapBenchmark` | `GroupBy` with hash maps, Int/Double/string, MIN/MAX/AVG/SUM/COUNT | hashing and column reads (5.7), `getSortedGroupColumns` |
| `SparqlExpressionBenchmark` | numeric binary expressions over `IdTable` columns, datatype patterns | A5 (`getIdsFromVariable` copies), `getDatatype` and `isUndefined` |
| `ParallelMergeBenchmark`, `BlockIndirectSortBenchmark` | merge and sort of `size_t` and wide structs, not of `Id` | only as a pattern |
| `IdColumnBenchmark` (from `base_classes`, merged) | `IdColumnVector`: push_back, range construction, iteration, `ranges::sort` | baseline of the split column types alone |
| `TieVsArrayBenchmark` (A4) | comparison of a triple with a row, `std::tie` against `std::array` | A4, result in `PROTOCOL_tie_vs_array_2026-10-08.md` |

Nothing covers comparing Ids, sorting an `IdTable`, bulk copy, `clone` and views,
UNDEF scans, hashing `Id` as a key, compression of columns and memory per Id.

## 4. Benchmark list

"now" means I can measure it on master (legacy), "B3" means it needs the split
types with the new Id, "flag" means it needs D1 and D2, so I run it twice
later. Effort: S is an afternoon, M is 1 to 2 days, L is more.

| # | Benchmark | What / where | Plan | Measurable | Effort |
|---|---|---|---|---|---|
| 01 | Id comparison | `compareThreeWay`, `operator<` and `compareWithoutLocalVocab` on random arrays: only Int, mixed Int/Double/Vocab, with and without `LocalVocabIndex` (`ValueId.h`) | 5.2 | now; flag | S |
| 02 | Sort a column | `ql::ranges::sort` on one `IdTable` column and with a `getBits` projection, sizes 1e5 to 1e7 (`IdTable::getColumn`) | 5.3, gate B2 | now; A/B with `IdColumnVector`; flag | S-M |
| 03 | Sort rows | sort an `IdTable` by 1, 2 and 4 columns, and the `Sort` operation (`engine/Sort.cpp`, `IdTableRow` swap = A3) | 5.3, A3 | now; flag | M |
| 04 | UNDEF scan | `any_of(column, Id::isUndefinedL)` against `&Id::isUndefined` against `getDatatype() == Undefined`, with 0 %, 1 % and 50 % UNDEF; also `findSmallerUndefRanges` (`FindUndefRanges.h`) and the `isCheap` check in `MultiColumnJoin` | 5.6, A6 | now; flag (scan of the datatype array only in split) | S |
| 05 | Hashing Ids | `HashSet<Id>` and `HashMap<Id, size_t>`: insert and lookup of 1e6 random Ids, mix of 2 datatypes (`AbslHashValue`) | 5.7 | now; flag | S |
| 06 | Bulk copy and views | `IdTable::insertAtEnd`, `clone`, `subView`, `asColumnSubsetView`, `push_back` of rows (`IdTable.h`) | 5.4 | now; flag | M |
| 07 | Row materialization | `std::tie` against `std::array<Id, N>` as projection in the located triples comparators (`LocatedTriples.cpp`) | A4 | done (`TieVsArrayBenchmark`) | M |
| 08 | Expression on variable operands | extend `SparqlExpressionBenchmark`: variable against constant operands, run on `master` and on the A5 branch | A5 | now (two branches); flag | M |
| 09 | GroupBy baseline | run `GroupByHashMapBenchmark` as it is and add a case with several group columns | 5.1, 5.7 | now; flag | S |
| 10 | Join baseline | run `JoinAlgorithmBenchmark` as it is with a fixed config file and keep the JSON | 5.2, 5.3 | now; flag | S |
| 11 | Column compression | write columns with `CompressedIdTableBlocks` or the external writer: bytes and time for data with mostly vocabulary Ids, mostly Ints, and mixed | 5.8, E | now (8 B per entry); flag (2 arrays) | M-L |
| 12 | Memory per Id | `sizeof(Id)`, bytes per column entry, `IdTriple`, `IdTableRow`; peak memory of a sort (counters of the allocator with memory limit) | 5.1, A7, C6 | now (facts); flag | S |
| 13 | Split column A/B | extend `IdColumnBenchmark` with the same measurements for `std::vector<Id>` (push_back, iterate, sort, bulk copy, UNDEF scan) | gate B2, 5.3, 5.4, 5.5 | possible now; distorted by the compat bridge until B3 | M |
| 14 | End to end | a few representative queries on a small index (flag off and on), wall time and peak RSS | F1 | flag + E | L |

## 5. What can be measured when

| Stage | Possible | Not possible |
|---|---|---|
| now (master) | 01 to 06, 09, 10, 12 (legacy numbers); 08 on two branches | comparison with the split layout |
| `base_classes` merged (done) | 13 (A/B inside one binary) | the numbers are biased, `BasicIdRef` still goes through `toId()` and `getBitsCompat` |
| after B2 and B3 | 01, 02, 04 for the new Id; the gate | effects on the whole system |
| after D1 and D2 (flag) | 03, 05, 06, 09, 10, 11 twice, side by side | index size and load time (needs E) |
| after E | 11 on real data, 14 | |

## 6. What I write next

These are small and self-contained, and in this order I learn the
infrastructure:

1. 04 UNDEF scan. One file, no dependency on the engine. It shows if A6 (lambda
   instead of pointer-to-member) is really free, so it is a good first
   benchmark. (Done in the meantime: `LambdaReplacementBenchmark` and
   `UndefHandlingBenchmark`.)
2. 01 Id comparison. Random Id arrays with a fixed seed, three datatype mixes,
   nothing else to set up.
3. 05 Hashing Ids. Same data generator as 01, with `HashSet` and `HashMap`.
4. 12 Memory per Id (static facts as metadata and a few allocator counters).
   Small, but I need it later for the claim "9 bytes per entry".

If I have time, after the PRs are merged: 13 (needs `base_classes`, which is
merged now) and 02 (same generator plus the A/B).

Planned layout: one file `benchmark/IdOperationsBenchmark.cpp` with one
`BenchmarkInterface` class per topic (compare, UNDEF, hash), registered with
`addAndLinkBenchmark(IdOperationsBenchmark testUtil)` in
`benchmark/CMakeLists.txt`. The data generator goes into an anonymous namespace.
I use `ad_utility::FastRandomIntGenerator` (`util/Random.h`) with a fixed seed
and `results.addMeasurement(name, lambda)`, and tables (`results.addTable(...)`)
for "size x variant".

## 7. Pitfalls

- On an Apple laptop thermal throttling and noise are a problem, so I repeat
  and take the median.
- The comparison of legacy and split is only fair after B3. Before that, the
  proxy builds the packed Id again at every access (plan 5.5).
- No comparison of numbers between different machines or build types.
- A measured lambda whose result is not used is optimized away.
- Memory numbers from the allocator counters only count what goes through
  `AllocatorWithLimit`.
- The compiler can inline differently for variants that look the same. In the
  `std::tie` against `std::array` benchmark this was the whole difference, see
  `PROTOCOL_tie_vs_array_2026-10-08.md`. So I check the assembly if two variants
  differ more than I expect.

## 8. End-to-end benchmarks (index and queries)

Two questions: how fast can an index be built, upgraded and loaded, and how fast
are queries end to end? It has to work today and at every step of the refactor,
and the numbers must be comparable independent of the hardware.

### 8.1 What exists already

- `qlever-perf-testsuite/` (this folder, created on 2026-09-23) builds an index
  with a given `qlever-index`, starts `qlever-server`, runs 51 frozen queries (5
  repetitions and 2 warm-ups) over HTTP and writes CSV files. The data set is
  `scientists.nt` (18 MB).
- QLever's own `e2e/` (`e2e.sh`, `queryit.py`, `scientists_queries.yaml`) is a
  correctness test and not a tool for timing.
- Binaries: `qlever-index`, `qlever-server`, `qlever-upgrade-index`
  (`IndexUpgraderMain.cpp`) and `PrintIndexVersionMain`.

The baseline of 2026-09-23 compared the old in-place branch with master. It is
not a baseline for the parallel plan, so I deleted it together with the old
scripts on 2026-10-06. `run_ab_benchmark.py` (8.7) replaced them.

### 8.2 Problems of the first harness

1. Query cache. `qlever-server` caches results by default (`--cache-max-size 30
   GB`, `--cache-max-num-entries 1000`). The script ran every query 2 + 5 times,
   so most repetitions were cache hits and measured HTTP and the result export,
   not the execution. The fix is to start the server with the cache switched off.
2. The data set is too small. The index is built in about 1.3 s and most queries
   take 3 to 7 ms, so timer noise and HTTP overhead dominate. Effects of the
   layout grow with the data.
3. One index build per label, no repetition, no peak memory, no load time (the
   script waited for the server but did not record how long), no upgrade.
4. Only the time on the client side. The response also contains the runtime
   information of the server, which does not include HTTP and the JSON export.
5. The order of the runs (all master, then all branch) lets drift of the machine
   look like a difference.

### 8.3 What to measure

| Area | Metric | How |
|---|---|---|
| Index build | wall time, peak RSS, size per file type (permutations, patterns, vocabulary) | `/usr/bin/time -l qlever-index ...` (macOS), 5 repetitions |
| Index load | time from the start of the server until it answers, then the first query | start `qlever-server`, poll, record |
| Index upgrade | time of `qlever-upgrade-index` on an index built by the baseline binary | only useful from phase E on |
| Queries | server and client time, median of at least 5, per query | cache off, frozen query list |

The query set I extend where needed, by what it stresses in the plan: `ORDER BY`
and sort (5.3), `GROUP BY` and `DISTINCT` (hashing, 5.7), joins, `OPTIONAL` and
`MINUS` with UNDEF (the generic join path, 5.6), expressions over numeric
columns (A5) and string functions (local vocab).

### 8.4 Comparable independent of the hardware

Absolute times are only a local diary. What counts is the ratio new/old,
measured in the same session on the same machine:

- Baseline archive: I build the baseline once (master, later also every
  important step) and copy `qlever-index`, `qlever-server` and
  `qlever-upgrade-index` to `baselines/<git-sha>/`. Then I can compare any step
  with it without building again.
- Same session, interleaved: old, new, old, new (A B A B) and compare the
  medians per pair, so that drift (temperature, background load) hits both
  sides.
- Fixed inputs: data set checked with sha256, frozen query file, same settings
  file, same flags, same number of threads.
- Context in every result file: CPU, RAM, OS, both git SHAs, build type and
  flags, setting of the cache.
- Report ratios with the spread (median ratio, min and max per query, the flag
  for more than 2 standard deviations) and not only means.
- No other builds or heavy jobs while measuring. This spoiled my first attempt,
  see `qlever-perf-testsuite/README.md`.

Later (flag, D2) "old" and "new" are the same source tree, built twice with the
flag off and on.

### 8.5 When it works

| Stage | Possible |
|---|---|
| today | baseline archive of master, fixed harness (8.2), check of the prep PRs (A2 to A7) against it: they must not make queries slower on the legacy build |
| after D2 (flag) | A/B of queries, build and load for old and new |
| after E (format) | index size, upgrade and load time of the new format |

### 8.6 Steps

1. Fix the harness: cache off, 5 repetitions of the index build, peak RSS, load
   time, server side time, order A B A B, context in the output.
2. Decide on the data: keep `scientists.nt` for a quick check and add a larger
   data set (a public one, or a generated one with a controlled mix of
   datatypes: vocabulary Ids, Ints, Doubles, UNDEF).
3. Extend the frozen query list by the categories from 8.3.
4. Build the baseline archive of master.
5. One script `run_all` that runs old and new, writes the JSON/CSV and prints
   the table of ratios.

### 8.7 Status of 2026-10-06: step 1 done

The new script is `qlever-perf-testsuite/scripts/run_ab_benchmark.py`. Example:

```
python3 scripts/run_ab_benchmark.py --target old=<build-dir> --target new=<build-dir> --rounds 3
```

In every round and for every target (the order alternates, A B and B A) it
builds a fresh index (wall time, CPU, peak RSS, sizes per file type), starts a
fresh server with the cache switched off (load time, peak RSS) and runs all
queries (client and server side time). The output is `measurements.csv`,
`summary.csv`, `report.txt` and `context.json` (machine, sha256 of binaries and
inputs, state of the battery or charger). `--summarize-only <dir>` prints a
report again.

What I checked on the way:

- With the default cache the second repetition of a query is a cache hit
  (`computeResult: 0ms`, 7 ms in total instead of 17 ms for a sort query). So
  the old harness mostly measured HTTP and the export.
- `--cache-max-size 0B` switches the cache off, `--cache-max-num-entries 0` does
  not. Short options with a value need separate arguments, `-k 1` as one string
  fails.
- The server side times are whole milliseconds, which is too coarse for this
  data set.

The A/A run (same binaries twice, 3 rounds, `results/aa_noise_2026-10-06/`) is
the noise floor of this machine. The geometric mean of the query ratios is
0.993. Single queries (most take 0.4 to 1.2 ms) lie between x0.9 and x1.15. The
wall time of the index build is x0.985 with a range of 0.89 to 1.07 over the
rounds, the load time varies by up to +-14 %. So differences below about 10 % are
not reliable on this data set. Next come a larger data set (step 2) and the
baseline archive (step 4). The laptop was on battery during this run, so for real
measurements I plug it in.

### 8.8 Status of 2026-10-06: QLever tooling and containers

`scripts/qlever_ab.py` makes the same comparison with QLever's own tooling:
`qlever index`, `qlever start`, `qlever benchmark-queries` (the cache is cleared
before every query, `--accept application/qlever-results+json`, so that the YAML
contains the runtime information of the server) and `qlever stop`. `--threads`
fixes the threads of the server, `CACHE_MAX_SIZE = 0B` switches the cache off.

- Native (`--target LABEL=<bin-dir>`): tested, an A/A run gives ratios around 1.
- Container (`--container --target LABEL=<checkout>` or `--checkout
  LABEL=<ref>`): the image is built from the `Dockerfile` of the repository (the
  one the CI builds, with `--build-arg RUN_TESTS=false`). `qlever` does not set
  limits for CPU or memory, so the script takes the `docker run` command that
  `qlever ... --show` would run and adds `--cpus`, `--memory` and
  `--memory-swap`. On 2026-10-06 I built the image of the current checkout (28
  min) and ran an A/A run in containers (`--cpus 4 --memory 8g --threads 4`, 2
  rounds, 8 queries). It works end to end: index in a container, server
  container, queries, stop, no containers left over, ratios of about 1.00. The
  second label reuses the Docker cache, so an A/A run needs only one build.
- Builder CPUs: the `Dockerfile` calls `cmake --build .` without `-j`. With 4
  builder CPUs (6 ninja jobs) the 11.7 GiB of the Docker Desktop VM ran out at
  step 758 of 877 (swap full, no progress for 7 minutes). With `--build-cpus 2`
  (4 jobs, the default) the peak was about 5 GiB.
- In container mode `build_cpu` and `build_peak_rss` of the index are not
  recorded (they would be the values of the `docker` CLI), only the wall time and
  the file sizes of the index.
- The CLI does not cover the peak memory of a container (the script samples
  `docker stats`) and the phase times of the index build (`qlever index-stats`,
  not used yet).
- The image tag is a hash of the build inputs (`src`, `CMakeLists.txt`,
  `CompilationInfo.cmake`, `GitVersion.cmake`, `Dockerfile`, tracked and
  untracked files), not of the commit and not of `.git`. The first version used
  commit and diff. It rebuilt the image (28 min) after every change that did not
  touch these files, and the Docker cache is invalidated by every git command,
  because the `Dockerfile` copies `.git` before the compile step. Now the image
  is reused as long as the sources of the three binaries do not change
  (`qlever-server --version` shows the commit the image was built from).
