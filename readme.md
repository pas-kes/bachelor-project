# Bachelor Project

## Contents

| Path | What |
|---|---|
| `PLAN_split_layout_id.md` | plan: new `SplitLayoutId` next to the legacy Id, order of the PRs, risks |
| `BENCHMARK_PLAN_split_layout_id.md` | which benchmarks I want, what they measure, how I compare old and new |
| `TODO_id_refactor_performance.md` | open points that I still have to check |
| `PROTOCOL_benchmark_baseline_2026-10-06.md` | first end to end baseline (master against benchmark branch), noise, contents of the branch |
| `PROTOCOL_tie_vs_array_2026-10-08.md` | `std::tie` against `std::array` for row comparisons: measurements and the reason (inlining) |
| `qlever-perf-testsuite/` | end to end A/B benchmark (index build, load, queries), see its README |
| `archive/` | old documentation of the first approach |

## Pull requests

These are my pull requests in `ad-freiburg/qlever` ([list on GitHub](https://github.com/ad-freiburg/qlever/pulls/pas-kes)), status of 2026-10-08. The phases (A1, B0, ...) are the ones from `PLAN_split_layout_id.md`.

| PR | Title | Branch | Phase | Status |
|---|---|---|---|---|
| [#3442](https://github.com/ad-freiburg/qlever/pull/3442) | Replace 64bit datatype/payload mix with a full 64 bit payload + 8 bit datatype (big branch of the first approach) | `refactor/create_64bit_id_type` | first approach | draft |
| [#3457](https://github.com/ad-freiburg/qlever/pull/3457) | Introduce IdColumn/ConstIdColumn as type aliases | `create_64bit_id_type_alias` | A1 (first version) | closed, replaced by #3459 |
| [#3458](https://github.com/ad-freiburg/qlever/pull/3458) | Replace &Id::method pointer-to-member usages with lambdas | `create_64bit_id_type_lambdas` | A6 (first version) | closed, replaced by #3460 |
| [#3459](https://github.com/ad-freiburg/qlever/pull/3459) | Introduce `IdColumnRef` and `ConstIdColumnRef` as aliases | `create_64bit_datatype_alias` | A1 | merged |
| [#3460](https://github.com/ad-freiburg/qlever/pull/3460) | Replace pointers-to-member by forwarding lambdas like `Id::isUndefinedL` | `create_64bit_id_replace_lambdas` | A6 | merged |
| [#3462](https://github.com/ad-freiburg/qlever/pull/3462) | Add `IdColumnVector`, `IdColumnRef` and `IdRef` for `Id` columns in the split layout | `create_64bit_id_base_classes` | B0 | merged |
| [#3553](https://github.com/ad-freiburg/qlever/pull/3553) | Replace sizeof(Id) with a const variable | `create_64bit_id_bytes_per_column_entry` | A7 / C6 | open |
| [#3575](https://github.com/ad-freiburg/qlever/pull/3575) | Extract assignSwap utility, use it for IdTableRow swapImpl | `create_64bit_id_assign_swap` | A3 | open |
| [#3576](https://github.com/ad-freiburg/qlever/pull/3576) | Return owned Id vectors instead of column views at a few sites | `create_64bit_id_remaining_changes` | A5 | open (title outdated after rework) |
| [#3577](https://github.com/ad-freiburg/qlever/pull/3577) | Replace `std::tie` by `std::array` for row comparisons and projections | `create_64bit_id_tie_to_array` | A4 | open |
| [#3578](https://github.com/ad-freiburg/qlever/pull/3578) | Route IdTable/IdTableRow element access through decltype(auto) | `create_64bit_id_trivial_type_changes` | A2 | open |
| [#3615](https://github.com/ad-freiburg/qlever/pull/3615) | Move the `Datatype` enum from `ValueId.h` into its own header `Datatype.h` | `create_64bit_id_extract_datatype` | B1 | open |
| [#3646](https://github.com/ad-freiburg/qlever/pull/3646) | Rename `ValueId.h` to `MixedValueId.h`, add an alias `ValueId = MixedValueId` | `create_64bit_id_rename_valueId` | C7 (preparation) | open |

## Initial setup
```shell
# first clone repository
git clone ad-freiburg/qlever

# install conan for dependencies 
brew install conan

# install dependencies
conan install . --build=missing

# ensure directory
mkdir -p build

# prebuild project 
cmake -B build \
-DCMAKE_BUILD_TYPE=Release \
-DLOGLEVEL=INFO \
-DUSE_PARALLEL=true \
-D_NO_TIMING_TESTS=ON \
-DCMAKE_GTEST_DISCOVER_TESTS_DISCOVERY_MODE=PRE_TEST \
-DCMAKE_CXX_FLAGS="-Wno-psabi" \
-DCMAKE_PREFIX_PATH="/opt/homebrew/opt/icu4c;/opt/homebrew/opt/boost" \
-GNinja

# build cpp project
cmake --build build \
--target qlever-index qlever-server qlever-upgrade-index
```



## build index
```shell

mkdir -p ./data/ && cd data
qlever index --index-binary ../build/qlever-index
```

## start server
```shell
mkdir -p ./data/ && cd data
../build/qlever-server -i olympics --port 8080
```
