# Benchmark set for the split layout Id

Working notes, linked to `PLAN_split_layout_id.md` (section numbers below
refer to it). Everything about results is open until measured.

## 1. Goal

Measure legacy (`ValueId`, `std::vector<Id>` columns, 8 byte per entry) and the
new split layout (`SplitLayoutId`, two arrays, 9 byte per column entry) with
the same code, so that the decisions in the plan are based on numbers:

- Gate after B2: is the new Id fast enough to continue? (sorting, comparison)
- A4 / A5 / A6: are the prep PRs really free on the legacy build?
- F1: which configuration becomes the default?

## 2. How the comparison works

- **Now (no flag yet):** only the legacy side exists. Baseline numbers on
  master, plus A/B inside one binary where both variants can be written down
  side by side (e.g. `std::vector<Id>` vs. `IdColumnVector`, `&Id::isUndefined`
  vs. a lambda).
- **After D1/D2:** build the same benchmark twice, once with the flag off and
  once on (two build dirs, e.g. `build/` and `build-split/`), run the same
  binary with the same config and seed, export JSON with `-w`, compare the two
  files with a small script.
- **Rules:** Release build, same machine, fixed seeds, laptop plugged in, run
  every benchmark >= 5 times and look at the median (the infrastructure only
  gives one timing per measurement). Consume the result of every measured
  lambda (e.g. log a sum), otherwise the compiler removes it, see
  `benchmark/Usage.md`.

How to run: `./benchmark/<Name> -p` (print), `-w out.json` (write), `-o`
(config options), `-s 'key=value'` (set options).

## 3. What exists already

| Benchmark | What it measures | Useful for |
|---|---|---|
| `JoinAlgorithmBenchmark` | hash join, merge/galloping joins on random `IdTable`s, many config options (sizes, overlap, columns) | baseline for compare + merge (5.2, 5.3) |
| `GroupByHashMapBenchmark` | `GroupBy` with hash maps, Int/Double/string, MIN/MAX/AVG/SUM/COUNT | hashing and column reads (5.7), `getSortedGroupColumns` |
| `SparqlExpressionBenchmark` | numeric binary expressions over `IdTable` columns, datatype patterns | A5 (`getIdsFromVariable` copies), `getDatatype` / `isUndefined` |
| `ParallelMergeBenchmark`, `BlockIndirectSortBenchmark` | merge / sort of `size_t` and wide structs, **not** `Id` | pattern only |
| `IdColumnBenchmark` (only on `base_classes`) | `IdColumnVector`: push_back, range-construct, iterate, `ranges::sort` | standalone baseline of the split column types |

Not covered anywhere: comparing Ids, sorting an `IdTable`, bulk copy / `clone`
/ views, UNDEF scans, hashing `Id` as key, compression of columns, memory per
Id.

## 4. Benchmark list

Legend: **now** = measurable on master (legacy), **B3** = needs the split
types with the new Id, **flag** = needs D1/D2, so run twice later.
Effort: S (an afternoon), M (1-2 days), L (more).

| # | Benchmark | What / where | Plan link | Measurable | Effort |
|---|---|---|---|---|---|
| 01 | **Id comparison** | `compareThreeWay` / `operator<` / `compareWithoutLocalVocab` on random arrays: all Int, mixed Int/Double/Vocab, with and without `LocalVocabIndex` (`ValueId.h`) | 5.2 | now; flag | S |
| 02 | **Sort a column** | `ql::ranges::sort` on one `IdTable` column and `getBits` projection; sizes 1e5..1e7 (`IdTable::getColumn`) | 5.3, gate B2 | now; A/B with `IdColumnVector` on `base_classes`; flag | S-M |
| 03 | **Sort rows** | sort an `IdTable` by 1, 2, 4 columns, and the `Sort` operation (`engine/Sort.cpp`, `IdTableRow` swap = A3) | 5.3, A3 | now; flag | M |
| 04 | **UNDEF scan** | `any_of(column, Id::isUndefinedL)` vs. `&Id::isUndefined` vs. `getDatatype() == Undefined`, with 0 %, 1 %, 50 % UNDEF; also `findSmallerUndefRanges` (`FindUndefRanges.h`) and the `isCheap` check in `MultiColumnJoin` | 5.6, A6 | now; flag (datatype-array scan only in split) | S |
| 05 | **Hashing Ids** | `HashSet<Id>` / `HashMap<Id, size_t>`: insert + lookup of 1e6 random Ids, 2 datatypes mix (`AbslHashValue`) | 5.7 | now; flag | S |
| 06 | **Bulk copy and views** | `IdTable::insertAtEnd`, `clone`, `subView`, `asColumnSubsetView`, `push_back` of rows (`IdTable.h`) | 5.4 | now; flag | M |
| 07 | **Row materialization** | `std::tie` vs. `std::array<Id, N>` projection in the located-triples comparators (`LocatedTriples.cpp`) | A4 | now (both variants in one file) | M |
| 08 | **Expression on variable operands** | extend `SparqlExpressionBenchmark`: variable vs. constant operands; run on `master` and on the A5 branch | A5 | now (two branches); flag | M |
| 09 | **GroupBy baseline** | run `GroupByHashMapBenchmark` as is, add a case with several group columns | 5.1, 5.7 | now; flag | S |
| 10 | **Join baseline** | run `JoinAlgorithmBenchmark` as is with a fixed config file, store the JSON | 5.2, 5.3 | now; flag | S |
| 11 | **Column compression** | write columns with `CompressedIdTableBlocks` / the external writer: bytes and time, for Vocab-heavy, Int-heavy, mixed data | 5.8, E | now (8 B/entry); flag (2 arrays) | M-L |
| 12 | **Memory per Id** | `sizeof(Id)`, bytes per column entry, `IdTriple`, `IdTableRow`; peak memory of a sort (memory-limit allocator counters) | 5.1, A7, C6 | now (facts); flag | S |
| 13 | **Split column A/B** | extend `IdColumnBenchmark` with the same measurements for `std::vector<Id>` (push_back, iterate, sort, bulk copy, UNDEF scan) | gate B2, 5.3, 5.4, 5.5 | needs `base_classes`; distorted by the compat bridge until B3 | M |
| 14 | **End to end** | a few representative queries on a small index (flag off/on), wall clock + peak RSS | F1 | flag + E | L |

## 5. What can be measured when

| Stage | Possible | Not possible |
|---|---|---|
| now (master) | 01-07, 09, 10, 12 (legacy numbers); 08 on two branches | split layout comparison |
| `base_classes` merged | 13 (A/B inside one binary) | numbers are biased: `BasicIdRef` still goes through `toId()` / `getBitsCompat` |
| after B2/B3 | 01, 02, 04 for the new Id; the **gate** | whole-system effects |
| after D1/D2 (flag) | 03, 05, 06, 09, 10, 11 twice, side by side | index size / load time (needs E) |
| after E | 11 on real data, 14 | - |

## 6. My picks to write now

Self-contained, small, and they teach the infrastructure in this order:

1. **04 UNDEF scan.** One file, no engine dependencies, tells whether A6 (lambda
   instead of pointer-to-member) is really free. Good first benchmark.
2. **01 Id comparison.** Random Id arrays with fixed seed, three datatype mixes,
   nothing else to set up.
3. **05 Hashing Ids.** Same data generator as 01, `HashSet` / `HashMap`.
4. **12 Memory per Id** (static facts as metadata, a few allocator counters).
   Tiny, but needed later for the "9 byte per entry" claim.

Stretch, once the PRs are merged: **13** (needs `base_classes`) and **02**
(same generator, plus the A/B).

Suggested layout: one file `benchmark/IdOperationsBenchmark.cpp` with one
`BenchmarkInterface` class per theme (compare, UNDEF, hash), registered with
`addAndLinkBenchmark(IdOperationsBenchmark testUtil)` in
`benchmark/CMakeLists.txt`; shared data generator in an anonymous namespace.
Use `ad_utility::FastRandomIntGenerator` (`util/Random.h`) with a fixed seed
and `results.addMeasurement(name, lambda)`, tables
(`results.addTable(...)`) for "size x variant".

## 7. Pitfalls

- Apple laptop: thermal throttling and noise, so repeat and use the median.
- The legacy-vs-split comparison is only fair after B3 (before that the proxy
  rebuilds the packed Id on every access, see plan 5.5).
- Do not compare numbers between different machines or build types.
- A measured lambda without a consumed result is optimized away.
- Memory numbers from the allocator counters only count what goes through
  `AllocatorWithLimit`.

## 8. End-to-end benchmarks (index and queries)

Questions: (1) how fast can an index be built, upgraded and loaded, (2) how
fast are queries end to end? It has to work **today and at every step** of the
refactor, and the numbers must be comparable independent of the hardware.

### 8.1 What exists already

- `qlever-perf-testsuite/` (this folder, created 2026-09-23): builds an index
  with a given `qlever-index`, starts `qlever-server`, runs 51 frozen queries
  (5 repetitions + 2 warm-ups) over HTTP, writes CSVs, and
  `aggregate_results.py` compares two labels (mean/median/stddev, delta,
  `> 2 stddev` flag). Dataset: `scientists.nt` (18 MB).
- QLever's own `e2e/` (`e2e.sh`, `queryit.py`, `scientists_queries.yaml`) is a
  **correctness** test, not a timing tool.
- Binaries: `qlever-index`, `qlever-server`, `qlever-upgrade-index`
  (`IndexUpgraderMain.cpp`), `PrintIndexVersionMain`.

The baseline of 2026-09-23 compared the old in-place branch with master. It was
**not** the baseline for the parallel plan and has been deleted together with
the old scripts (2026-10-06); `run_ab_benchmark.py` (8.7) replaced them.

### 8.2 Problems of the current harness

1. **Query cache.** `qlever-server` caches results by default
   (`--cache-max-size 30 GB`, `--cache-max-num-entries 1000`). The script
   runs every query 2 + 5 times, so most repetitions are probably cache hits
   and measure HTTP + result export, not execution. To check and fix: start
   the server with the cache disabled (try `-k 0` / `-c 0B`) or clear it
   between runs.
2. **Too small.** The index builds in ~1.3 s and most queries take 3-7 ms, so
   timer noise and HTTP overhead dominate. Layout effects grow with the data.
3. **One index build per label**, no repetition, no peak memory, no load
   time (the script waits for the server but does not record how long), no
   upgrade.
4. Client-side time only. The response contains the server-side runtime
   information, which does not include HTTP and JSON export.
5. Result order of the runs (all master, then all branch) lets machine drift
   look like a difference.

### 8.3 What to measure

| Area | Metric | How |
|---|---|---|
| Index build | wall time, peak RSS, size per file type (permutations, patterns, vocabulary) | `/usr/bin/time -l qlever-index ...` (macOS), 5 repetitions |
| Index load | time from server start until it answers, then first query | start `qlever-server`, poll, record |
| Index upgrade | time of `qlever-upgrade-index` on an index built by the *baseline* binary | only meaningful from phase E on |
| Queries | server-side time and client-side time, median of >= 5, per query | cache off, frozen query list |

Query set (extend the frozen 51 where needed), by what it stresses in our
plan: `ORDER BY` / sort (5.3), `GROUP BY` and `DISTINCT` (hashing, 5.7),
joins, `OPTIONAL` / `MINUS` with UNDEF (the generic join path, 5.6),
expressions over numeric columns (A5), string functions (local vocab).

### 8.4 Comparable independent of the hardware

Absolute times are only a local diary. What counts is the **ratio
new/old measured in the same session on the same machine**:

- **Baseline archive:** build the baseline once (master, later also each
  important step), copy `qlever-index`, `qlever-server`,
  `qlever-upgrade-index` into `baselines/<git-sha>/`. Any step can then be
  compared against it without rebuilding.
- **Same session, interleaved:** run old, new, old, new (A B A B) and
  compare medians per pair, so that drift (thermal state, background load)
  affects both sides.
- **Fixed inputs:** dataset checked by sha256, frozen query file, same
  settings file, same flags, same thread count.
- **Record the context** in every result file: CPU, RAM, OS, both git SHAs,
  build type and flags, the cache setting.
- **Report ratios with spread** (median ratio, per-query min/max, the
  `> 2 stddev` flag), not only means.
- No other builds or heavy jobs while measuring (this skewed the first
  attempt, see `qlever-perf-testsuite/README.md`).

Later (flag, D2): "old" and "new" are the same source tree built twice with
the flag off and on.

### 8.5 When it works

| Stage | Possible |
|---|---|
| today | baseline archive of master, harness fixed (8.2), prep PRs (A2-A7) checked against it: they must not make queries slower on the legacy build |
| after D2 (flag) | old/new A/B of queries, build and load |
| after E (format) | index size, upgrade, load time of the new format |

### 8.6 Steps to build it

1. Fix the harness: cache off, 5 repetitions of the index build, peak RSS,
   load time, server-side time, A B A B order, context in the output.
2. Decide on the data: keep `scientists.nt` for a quick check, add a larger
   dataset (public one, or a generated one with a controlled mix of
   datatypes: vocabulary ids, ints, doubles, UNDEF).
3. Extend the frozen query list by the categories in 8.3.
4. Build the baseline archive of master.
5. One script `run_all` that runs old/new, writes the JSON/CSV and prints the
   ratio table.

### 8.7 Status (2026-10-06): step 1 done

New script `qlever-perf-testsuite/scripts/run_ab_benchmark.py` (the old
`run_e2e_benchmark.py` is unchanged). Example:

```
python3 scripts/run_ab_benchmark.py --target old=<build-dir> --target new=<build-dir> --rounds 3
```

What it does: per round and target (order alternates A B / B A) it builds a
fresh index (wall, CPU, peak RSS, sizes per file type), starts a fresh server
with the cache disabled (load time, peak RSS) and runs all queries (client and
server-side time). Output: `measurements.csv`, `summary.csv`, `report.txt`,
`context.json` (machine, sha256 of binaries and inputs, battery/power state).
`--summarize-only <dir>` re-prints a report.

Checked on the way:
- With the default cache the 2nd repetition of a query is a cache hit
  (`computeResult: 0ms`, total 7 ms instead of 17 ms for a sort query), so the
  old harness measured mostly HTTP and export.
- `--cache-max-size 0B` disables the cache. `--cache-max-num-entries 0` does
  **not**. (Short options with a value need separate arguments, `-k 1` as one
  string fails.)
- The server-side times are whole milliseconds, too coarse for this data set.

A/A run (same binaries twice, 3 rounds, `results/aa_noise_2026-10-06/`) = the
noise floor of this machine: geometric mean of the query ratios 0.993; single
queries (most take 0.4-1.2 ms) between x0.9 and x1.15; index build wall time
x0.985 with per-round range 0.89-1.07; load time up to +-14 %. So differences
below ~10 % on this data set are not trustworthy. Next: a larger data set
(step 2) and the baseline archive (step 4). The laptop was on battery power
during the run, plug it in for real measurements.

### 8.8 Status (2026-10-06): QLever tooling and containers

`scripts/qlever_ab.py` runs the same comparison with QLever's own tooling:
`qlever index`, `qlever start`, `qlever benchmark-queries` (cache cleared before
every query, `--accept application/qlever-results+json`, so the YML contains
the server-side runtime information) and `qlever stop`. `--threads` fixes the
server threads, `CACHE_MAX_SIZE = 0B` disables the cache.

- **Native** (`--target LABEL=<bin-dir>`): tested, A/A run gives ratios around 1.
- **Container** (`--container --target LABEL=<checkout>` / `--checkout
  LABEL=<ref>`): the image is built from the repository's `Dockerfile` (the one
  the CI builds, with `--build-arg RUN_TESTS=false`) and tagged with commit +
  diff, so it is rebuilt only when the source changes. `qlever` does not set
  CPU or memory limits, so the script takes the `docker run` command that
  `qlever ... --show` would run and adds `--cpus/--memory/--memory-swap`.
  **Checked (2026-10-06):** the image of the current checkout was built in
  28 min, an A/A run in containers (`--cpus 4 --memory 8g --threads 4`, 2
  rounds, 8 queries) works end to end: index in a container, server
  container, queries, stop, no leftover containers; query ratios ~1.00. The
  second label reuses the Docker cache, so an A/A run needs one build only.
- **Builder CPUs:** the `Dockerfile` calls `cmake --build .` without `-j`.
  With 4 builder CPUs (6 ninja jobs) the 11.7 GiB of the Docker Desktop VM
  ran out of memory at step 758 of 877 (swap full, no progress for 7
  minutes). With `--build-cpus 2` (4 jobs, the default) the peak was ~5 GiB.
- In container mode `build_cpu` / `build_peak_rss` of the index are not
  recorded (they would be those of the `docker` CLI), only wall time and the
  index file sizes.
- Not covered by the CLI: peak memory of a container (the script samples
  `docker stats`), phase times of the index build (`qlever index-stats`, not
  used yet).
- **Image tag = content of the build inputs** (`src`, `CMakeLists.txt`,
  `CompilationInfo.cmake`, `GitVersion.cmake`, `Dockerfile`; tracked and
  untracked), not the commit and not `.git`. First version used commit + diff:
  it rebuilt the image (28 min) after any change that did not touch these
  files, and Docker's own cache is invalidated by every git command, because
  the `Dockerfile` copies `.git` before the compile step. Now the image is
  reused unless the sources of the three binaries change (`qlever-server
  --version` then shows the commit the image was built from).

