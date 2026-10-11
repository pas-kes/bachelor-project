# QLever A/B performance suite

With this suite I compare two (or more) QLever builds on the same machine:
index build, server start and query latency. It belongs to the plan for the
split layout `Id` (`../PLAN_split_layout_id.md` and
`../BENCHMARK_PLAN_split_layout_id.md`, section 8).

Only the ratio new/old from the same session says something. The absolute
times depend on the machine.

## Files

- `scripts/qlever_ab.py` is the runner I use. It calls QLever's own tooling
  (the `qlever` command line tool with `index`, `start`, `benchmark-queries`
  and `stop`), either natively or in containers (`--container`, the images are
  built from QLever's `Dockerfile`).
- `scripts/run_ab_benchmark.py` is my first runner, which starts the binaries
  itself. `qlever_ab.py` reuses its code for the summary and the report.
- `scripts/extract_queries.py` takes the plain (non-text) queries from QLever's
  `e2e/scientists_queries.yaml`.
- `scripts/queries_no_text.json` is a frozen copy of these queries (55, some
  names occur twice). I do not change it, so that runs stay comparable.
- `scripts/e2e-build-settings.json` has the index build settings of QLever's
  e2e test. Every build uses the same settings.
- `dataset/scientists.nt` is the unzipped e2e "scientists" data set (18 MB).
- `tie_variants_benchmark/TieVariantsBenchmark.cpp` is a micro benchmark for the
  candidates of `tieHelper` in `LocatedTriples.cpp` (`std::tie`, a tie that
  falls back to a tuple of `Id`s, a tuple of references and proxies, and
  `std::array`), for rows that return `Id&` and for rows that return a proxy.
  It is only for me and not part of the QLever repository. To run it I copy the
  file to `benchmark/` of a checkout, add
  `addAndLinkBenchmark(TieVariantsBenchmark engine testUtil)` to
  `benchmark/CMakeLists.txt`, build the target and run
  `./benchmark/TieVariantsBenchmark -p`. The results are in section 8 of
  `../PROTOCOL_tie_vs_array_2026-10-08.md`. The folder also has the scripts for
  the Linux container (`linux/`), the change of `compareThreeWay` that was
  tested (`compareThreeWay_split.patch`), `run_alternating_mac.py` and the raw
  results (`results/`).
- `results/` has one directory per run (`measurements.csv`, `summary.csv`,
  `report.txt`, `context.json`). `aa_noise_2026-10-06/` is an A/A run (the same
  binaries twice). It shows the noise of my laptop: on this data set,
  differences below about 10 % are not reliable.

## Running with QLever's tooling

This needs `qlever` (`brew install qlever-control`). Natively I need two
builds:

```bash
python3 scripts/qlever_ab.py --target old=<baseline-bin-dir> --target new=<build-dir>
```

In containers both targets get the same Linux environment and the same limits.
The images are built from the `Dockerfile` of each checkout and tagged with a
hash of the sources, so they are only rebuilt when `src` and the other build
inputs change. Docker has to be running, and a build takes a while:

```bash
python3 scripts/qlever_ab.py --container --cpus 4 --memory 8g --threads 4 \
  --checkout old=master --target new=<qlever-checkout>
```

With `--dry-run` it only prints the image builds and the `docker run` commands.
The container mode needs a full checkout with a real `.git` directory (the
`Dockerfile` copies it), a git worktree does not work. `--checkout` creates
such a checkout for a git ref.

## First runner (native only)

I need two Release builds in separate directories (or an archive of the
binaries `qlever-index` and `qlever-server` as baseline), the laptop on the
charger and nothing else running:

```bash
python3 scripts/run_ab_benchmark.py \
  --target old=<baseline-bin-dir> \
  --target new=<build-dir> \
  --rounds 3
```

In every round and for every target (the order alternates, A B and then B A)
it builds a fresh index (wall time, CPU time, peak RSS, sizes), starts a fresh
server (load time, peak RSS) and runs all queries (client and server time). The
query cache is switched off with `--cache-max-size 0B`, because with the
default cache repeated queries would be cache hits. `--summarize-only
<result-dir>` prints the report of an old run again.

## Limits

- The data set is small (370k triples). Most queries take 0.4 to 10 ms, and the
  server side times are whole milliseconds. I want to try a larger data set.
- Queries with the text index are not in the frozen query set.
