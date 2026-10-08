# QLever A/B performance suite

End-to-end comparison of two (or more) QLever builds on the same machine:
index build, server load and query latency. Used for the split layout `Id`
plan (`../PLAN_split_layout_id.md`, `../BENCHMARK_PLAN_split_layout_id.md`,
section 8).

Only the ratio new/old measured in the same session is meaningful, absolute
times depend on the machine.

## Layout

- `scripts/qlever_ab.py`: **the runner**. Uses QLever's own tooling (the
  `qlever` command line tool: `index`, `start`, `benchmark-queries`, `stop`),
  natively or in containers (`--container`, built from QLever's `Dockerfile`).
- `scripts/run_ab_benchmark.py`: the first runner, which starts the binaries
  itself. Its summary/report code is shared with `qlever_ab.py`.
- `scripts/extract_queries.py`: extracts the plain (non-text) queries from
  QLever's `e2e/scientists_queries.yaml`.
- `scripts/queries_no_text.json`: **frozen** snapshot of these queries (55,
  some names occur twice). Use it as it is, so that runs stay comparable.
- `scripts/e2e-build-settings.json`: the index build settings of QLever's e2e
  test (same settings for every build).
- `dataset/scientists.nt`: the unzipped e2e "scientists" collection (18 MB).
- `results/`: one directory per run (`measurements.csv`, `summary.csv`,
  `report.txt`, `context.json`).
  `aa_noise_2026-10-06/` is an A/A run (the same binaries twice): the noise
  floor of the laptop, differences below ~10 % are not trustworthy on this
  data set.

## Run with QLever's tooling (recommended)

Needs `qlever` (`brew install qlever-control`). Native: two builds.

```bash
python3 scripts/qlever_ab.py --target old=<baseline-bin-dir> --target new=<build-dir>
```

In containers (same Linux environment and limits for both targets, the images
are built from the `Dockerfile` of each checkout and tagged by the content of
the sources of the binaries, so they are rebuilt only when `src` etc. change;
Docker has to be running; a build takes a while):

```bash
python3 scripts/qlever_ab.py --container --cpus 4 --memory 8g --threads 4 \
  --checkout old=master --target new=<qlever-checkout>
```

`--dry-run` shows the image builds and the `docker run` commands without
running anything. Containers need a **full checkout** (a real `.git`
directory, the Dockerfile copies it), not a git worktree; `--checkout` makes
one for a git ref.

## Run (first runner, native only)

Two Release builds in separate directories (or a baseline archive of the
binaries `qlever-index`, `qlever-server`), laptop on the charger, nothing else
running:

```bash
python3 scripts/run_ab_benchmark.py \
  --target old=<baseline-bin-dir> \
  --target new=<build-dir> \
  --rounds 3
```

Per round and target (order alternates A B / B A): fresh index (wall, CPU,
peak RSS, sizes), fresh server (load time, peak RSS) with the query cache
**disabled** (`--cache-max-size 0B`; with the default cache repeated queries
are cache hits), all queries (client and server time). `--summarize-only
<result-dir>` prints a report again.

## Known limits

- The data set is small (370k triples): most queries take 0.4-10 ms, the
  server-side times are whole milliseconds. A larger data set is planned.
- Text-index queries are not part of the frozen query set.
