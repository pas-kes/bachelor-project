# Plan: new `SplitLayoutId` next to `ValueId`

## 1. Goal

- I build a new Id (64 bit payload and 8 bit datatype, `sizeof` 16) next to the
  legacy Id, in `engine/idTable/splitLayout/`.
- It uses the full 64 bit payload range from the start.
- I do it in small PRs, so that switching is only a flag.
- The legacy Id stays, permanently. It has fewer bits and is probably faster, so
  it remains a selectable option. It is called `MixedValueId` now (datatype and
  value are mixed in one word), and `ValueId` is an alias of whichever Id is
  active. Both Ids are kept and chosen at build time with one flag. Which one is
  the default I decide later with benchmarks.
- The index format changes later, in its own PR series.

## 2. Where I am (2026-10-08)

All pull requests are listed in `readme.md`.

- Merged: [#3459](https://github.com/ad-freiburg/qlever/pull/3459) (A1,
  `IdColumnRef` and `ConstIdColumnRef` aliases),
  [#3460](https://github.com/ad-freiburg/qlever/pull/3460) (A6, forwarding
  lambdas) and [#3462](https://github.com/ad-freiburg/qlever/pull/3462) (B0, the
  column types of the split layout: `IdRef`, column views, iterator,
  `IdColumnVector`, bit representation). Nothing uses the new types yet, and
  they still convert through the old `Id`.
- Open, based on master: [#3578](https://github.com/ad-freiburg/qlever/pull/3578)
  (A2), [#3575](https://github.com/ad-freiburg/qlever/pull/3575) (A3),
  [#3577](https://github.com/ad-freiburg/qlever/pull/3577) (A4),
  [#3576](https://github.com/ad-freiburg/qlever/pull/3576) (A5),
  [#3553](https://github.com/ad-freiburg/qlever/pull/3553) (A7),
  [#3615](https://github.com/ad-freiburg/qlever/pull/3615) (B1) and
  [#3646](https://github.com/ad-freiburg/qlever/pull/3646) (preparation for C7).
- Closed: [#3457](https://github.com/ad-freiburg/qlever/pull/3457) and
  [#3458](https://github.com/ad-freiburg/qlever/pull/3458), the first versions of
  A1 and A6, replaced by #3459 and #3460.
- Draft: [#3442](https://github.com/ad-freiburg/qlever/pull/3442), the big branch
  of the first approach (see `archive/`).
- `ColumnStorageTraits` is not part of A2 anymore. It comes back as its own PR
  (D1).
- Only local, no PR yet: the new Id itself (branch
  `create_64bit_id_new_splitLayoutId`; `SplitLayoutId`, `IdBitRepresentation.h`
  and the CMake option `USE_SPLIT_LAYOUT` are not committed yet).

## 3. Problems with my first idea

1. The index format has to come before the switch. Ids are written raw at
   several places and depend on `sizeof(Id) == 8`
   (`CompressedRelationReader.cpp:1015`, `Pattern.h:37`,
   `CompressedIdTableBlocks.h:63`, `IdTriple.h:96`, `TripleSerializer.h`). So
   "later" only holds relative to the PRs of the Id class.
2. Changing `using Id = ...` in `Id.h` is not enough. `ValueId` is used about 829
   times outside of `ValueId.h` and `Id.h`. If `ValueId` kept meaning the legacy
   class, those places would silently stay on the legacy Id in a build with the
   flag. The solution is
   [#3646](https://github.com/ad-freiburg/qlever/pull/3646): the legacy class is
   renamed to `MixedValueId` (file `MixedValueId.h`), and `ValueId` becomes an
   alias of `Id` (`using Id = MixedValueId; using ValueId = Id;`). So the old name
   follows the flag, and I do not have to touch the 829 places at once.
3. Typed tests over both Ids only work for the shared range, and they stay. The
   full range is different on purpose (`maxInt`, `maxIndex`, rounding of
   doubles), so edge cases need separate tests per type, and tests with limits
   depend on the flag permanently.
4. Both configurations have to stay green forever. The CI job with the flag is
   expensive and will be red for a long time at the beginning. I start it as
   non-blocking, make it required when it is green, and keep it as a permanent
   second configuration.

## 4. PRs

`[solo]` means it can be merged on its own and does not change the behaviour.

### A: prep on master (running)

All of them are `[solo]`, based on master and independent of each other. They
make the generic code work with proxy elements (`IdRef` is a prvalue and not an
`Id&`). That is needed as soon as the split storage is used, no matter which Id
class is inside. Status on 2026-10-08:

| # | PR | Branch | What it does | Status / notes |
|---|---|---|---|---|
| A1 | [#3459](https://github.com/ad-freiburg/qlever/pull/3459) | `datatype_alias` | `IdColumnRef` and `ConstIdColumnRef` aliases | merged |
| A2 | [#3578](https://github.com/ad-freiburg/qlever/pull/3578) | `trivial_type_changes` | `decltype(auto)` instead of `T&` in the element access of `IdTable` and `IdTableRow`; call sites take `Id` or `auto&&` instead of `Id&` (with `std::forward` in lambdas); new test `IdTable.rowReferenceOperatorBracket` with an `Id&` and with a table that returns proxies | open. `ColumnStorageTraits` is not part of it anymore (own PR, D1), `IdTable.h` is unchanged. No cost |
| A3 | [#3575](https://github.com/ad-freiburg/qlever/pull/3575) | `assign_swap` | `IdTableRow::swapImpl` swaps with `using std::swap; swap(a[i], b[i]);` (ADL), the proxy provides a hidden friend `swap` that takes `const&` | open. No cost, the diff is only `IdTableRow.h` |
| A4 | [#3577](https://github.com/ad-freiburg/qlever/pull/3577) | `tie_to_array` | `std::tie(row[I]...)` becomes `std::array<Id, N>` in the row comparisons and the projections in `LocatedTriples` (a tie of prvalue proxies would dangle); helpers renamed from `tie...` to `...AsArray`; benchmark `TieVsArrayBenchmark` | open. Measured with a micro benchmark: on macOS (Apple Clang) about 30 % faster, because the compiler inlines `ValueId::compareThreeWay` for arrays but not for tuples. On Linux aarch64 it is not faster: g++ 13 is 13 to 38 % slower, clang 18 is 20 % slower for `<` and 25 % faster for `==`. Always below 1 ns per comparison. See `PROTOCOL_tie_vs_array_2026-10-08.md` |
| A5 | [#3576](https://github.com/ad-freiburg/qlever/pull/3576) | `remaining_changes` | Places that need a real `Id`. Reworked as planned: `EmptyPath::graphsOf` and `getIdsFromVariable` return the column view `ConstIdColumnRef` (no copy), `PathSearch` and `PathQuery` use the new alias `IdColumn = std::vector<Id>` in `IdTable.h`, `ExportQueryExecutionTrees` copies the single `Id` | open. The PR title still says "Return owned Id vectors", I have to change it before merging |
| A6 | [#3460](https://github.com/ad-freiburg/qlever/pull/3460) | `replace_lambdas` | `&Id::isUndefined` and similar become `static constexpr` lambdas in `ValueId` (`Id::isUndefinedL`, `isDefinedL`, `getBitsL`, `getDatatypeL`) | merged. `SplitLayoutId` has to provide the same set (B7) |
| A7 | [#3553](https://github.com/ad-freiburg/qlever/pull/3553) | `bytes_per_column_entry` | `BYTES_PER_ID_COLUMN_ENTRY` in `global/Id.h` (moved there because #3462 removed `IdColumn.h`); used at the places for on-disk and spill files and, since 2026-10-08, also in the memory estimates of `IdTable` columns (`QueryExecutionContext.h`, `IndexScan.h`, `Sort.cpp`, `CompressedExternalIdTable.h`, `ExternalIdTableSorterMergeConfig.h`, `GroupByImpl.cpp`, `IndexImpl.cpp`); `TextMetaData::sizeOnDisk` uses the real sizes of the members | open. For now the constant is `sizeof(Id)`, for the split layout it has to be 9 (D1). Open points are in `TODO_id_refactor_performance.md` (limit in `GroupByImpl`, tests with fixed 8 bytes, wire format and hash) |

For the order this means that nothing blocks anything, A2 to A7 can be merged in
any order. `JoinAlgorithmBenchmark.cpp` is changed by A2 and A5, but in different
lines. I checked with `git merge-tree` that it merges without a conflict.

### B: build the new Id (new code and tests only)

Status on 2026-10-08. "Not started" means there is no branch and no PR yet.

| # | PR | Branch | What it does | Depends on | Status / notes |
|---|---|---|---|---|---|
| B0 | [#3462](https://github.com/ad-freiburg/qlever/pull/3462) | `base_classes` | Column types of the split layout (`IdRef`, column views, iterator, `IdColumnVector`, bit representation), without `ColumnStorageTraits` (see D1) | | merged on 2026-10-08. Nothing uses the types yet |
| B1 | [#3615](https://github.com/ad-freiburg/qlever/pull/3615) | `extract_datatype` | Move the `Datatype` enum to its own small header `[solo]` | | open. `test/DatatypeTest.cpp` is part of it |
| B2 | | `new_splitLayoutId` (local) | Core of `SplitLayoutId`: bits, undefined, Int (`int64_t`), Double, Bool, `compareThreeWay`, hash, `operator<<`. With a benchmark against the legacy Id | B0, B1 | started in my local working copy (`SplitLayoutId.h`, `IdBitRepresentation.h`), not committed yet, no PR |
| | | | Gate: I look at the results of the benchmark (section 5) | | |
| B3 | | | `BasicIdRef` builds the new Id directly, remove `getBitsCompat` and `toId()` | B2 | not started |
| B4 | | | Index types (`VocabIndex`, `TextRecordIndex`, `BlankNodeIndex`, ...) | B2 | not started as a PR. The factory functions are in the local working copy |
| B5 | | | `LocalVocabIndex` and the comparison with the position in the vocab | B4, C2 | not started as a PR. In the local working copy the pointer is already stored unchanged |
| B6 | | | `Date` and `GeoPoint` (the encoding stays for now) | B2 | not started as a PR. A first version of `makeFromDate` and `makeFromGeoPoint` is in the local working copy |
| B7 | | | The rest of the API (`visit`, `compareWithoutLocalVocab`, ...), including the `*L` lambdas from A6 | B2 to B6, A6 | not started |
| B8 | | | Typed tests over both Ids (shared range) and tests for the edge cases. They stay and keep both Ids in sync | B7 | not started. `MixedValueIdTest` ([#3646](https://github.com/ad-freiburg/qlever/pull/3646)) is the first test that names the legacy class directly |

### C: make existing code independent of the Id type (on master)

This does not depend on B and can start now. All of them are `[solo]`.

| # | PR | Branch | What it does | Depends on | Status / notes |
|---|---|---|---|---|---|
| C0 | | | Audit, no code: sort the uses of `getBits()`, `fromBits()`, `Id::T` and `numDataBits` (hash key, sort key, disk, unrelated). Size: about 86 / 33 / 24 / 43 | | not started. The counts are from a first search |
| C1 | | | Replace the dependency on `Id::T` by a small abstraction, one PR per directory | C0 | not started |
| C2 | | `new_splitLayoutId` (local) | `LocalVocabEntry` and `IdProxy` do not take raw `T` bits anymore. Small | | started in the local working copy: `IdProxy` is `TypedIndex<IdBitRepresentation, ...>`, `incremented()` on the Ids. The cache with 16 byte atomics is open, see `TODO_id_refactor_performance.md` |
| C3 | | | Check everything that assumes 60 bit (`maxInt`, `IntegerType`, `maxIndex`, `FoldedId`). Size: about 26 / 14 / 8 | | not started |
| C4 | [#3553](https://github.com/ad-freiburg/qlever/pull/3553) | `bytes_per_column_entry` | Separate disk and memory: an explicit conversion between Id and on-disk format, still the old 8 byte format. About 6 places | | started with the constant `BYTES_PER_ID_COLUMN_ENTRY` (A7). The conversion itself is not started |
| C5 | | | Replace raw `.data()` access on Id columns (compression, external sort). Few places, but central | | not started |
| C6 | [#3553](https://github.com/ad-freiburg/qlever/pull/3553) | `bytes_per_column_entry` | Memory estimates: `sizeof(Id)` becomes the bytes per column entry (8 legacy, 9 split). Few places | | started in A7 for the places that use `IdTable` columns. Still open: the constant per flag (D1), the wire format of the binary export and the byte hash in `Pattern.h` (they must not depend on the layout), tests with fixed numbers |
| C7 | [#3646](https://github.com/ad-freiburg/qlever/pull/3646) | `rename_valueId` | Rename of the legacy class, `ValueId` as alias of `Id`. This replaces the mechanical rename of the 829 places (section 3, point 2). The uses can be changed to `Id` later, file by file, without a deadline | | open. Done in the PR |

### D: integration behind a flag

| # | PR | Branch | What it does | Depends on | Status / notes |
|---|---|---|---|---|---|
| D1 | | `new_splitLayoutId` (local) | `ColumnStorageTraits` and the flag (own PR). The traits are not part of A2 anymore, so this PR adds the legacy variant, the use in `IdTable` (`ViewSpans`, both `operator()`, `subView`, both `getColumn`) and the split variant (`IdRef`, `IdColumnRef`, `IdColumnVector`). It also adds the build flag (CMake option and compile definition, off by default) that chooses the variant, and makes the constant for the bytes per entry from A7 and C6 depend on the flag. Flag off: the same types as before, no change of the behaviour. Flag on: split columns, but still with the legacy `Id` (works through the compat bridge), so that `IdTable` can be tested with split columns before the new Id exists. This is the only flag, D2 extends it | B0 | not started as a PR. The CMake option `USE_SPLIT_LAYOUT` (compile definition `QLEVER_USE_ID_SPLIT_LAYOUT`) exists in the local working copy. The traits file is still in the history of [#3578](https://github.com/ad-freiburg/qlever/pull/3578) |
| D2 | | | The same flag now also switches the Id: `Id` points to the new class (`MixedValueId` stays the legacy class, `ValueId` follows `Id`), and the `IdColumnRef` aliases are switched. `MixedValueIdTest` is only built without the flag. Flag on means new Id and split columns together, flag off means legacy. The default stays off | D1, B8, C7 | not started |
| D3 | | | CI job with the flag (non-blocking) | D2, C1 to C5 | not started |
| D4 | | | Fix everything that breaks in this job (PRs on master) | D3 | not started |
| D5 | | | Make the job required when it is green. It stays as a permanent second configuration | D4 | not started |

About D1: with the flag on, all call sites that use `.data()` on an Id column do
not compile anymore (C5). That does not block the merge of D1 (the flag is off by
default), but it blocks the CI job (D3).

### E: index format (own series)

| # | PR | Branch | What it does | Depends on | Status / notes |
|---|---|---|---|---|---|
| E1 | | | New format behind the abstraction from C4, bump the version. The format depends on the flag: the index records with which Id layout it was built, and a build with the other layout refuses to load it with a clear error | C4, D2 | not started |
| E2 | | | Serialization of columns (payload and datatype arrays separate) | E1 | not started |
| E3 | | | Converter between the two formats, only if indexes should be moved between the two configurations | E1 | not started, maybe not needed (open question 10) |

### F: default and cleanup

The flag does not go away.

| # | PR | Branch | What it does | Depends on | Status / notes |
|---|---|---|---|---|---|
| F1 | | | Decide the default configuration with benchmarks (new Id against legacy), switch the default if the new one wins, and write down the decision | D2, benchmarks | not started. Benchmarks are planned in `BENCHMARK_PLAN_split_layout_id.md` |
| F2 | | | Cleanup: leftovers in `TODO_id_refactor_performance.md`, remove the bridge with `getBitsCompat` and `toId()` if it is still there | F1 | not started |

## 5. Performance (to measure after B2)

There is `benchmark/IdColumnBenchmark.cpp` already. The legacy Id stays, so the
legacy build must not get slower either. That is why I measure the prep PRs (A2
to A7) on the legacy configuration and not only on the new one.

1. Memory. A single Id has 16 bytes instead of 8. A split column needs 9 bytes
   per element (+12.5 %). Everything outside of columns doubles: `IdTriple`,
   `IdTableRow`, `std::vector<Id>`, `HashMap<Id, ...>`. The size estimates
   (`IndexScan.h:200`, `QueryExecutionContext.h:58`, `Sort.cpp:89`,
   `GroupByImpl.cpp:223`, configs of the external sorter) have to use 9 bytes per
   column entry (A7) and neither 8 nor 16.
2. Comparison. It was one 64 bit comparison and is now a comparison of
   (datatype, payload). I compare branching with branch free (`cmp` and `sbb`, or
   one comparison of `unsigned __int128`).
3. Sorting. `ranges::sort` over proxy iterators moves two arrays per element (two
   loads, two stores). I measure 1 column and several columns. If it is slower, I
   sort 128 bit keys, or I sort a permutation and apply it.
4. Bulk operations. `insertAtEnd` and `clone` use `memmove`, because `Id` is
   trivially copyable. For split columns that has to be two `memmove`s per
   column. At the moment the range constructor of `IdColumnVector` does one
   `push_back` per element, and `insertImpl` packs and unpacks (see the TODO
   file).
5. Getters of `BasicIdRef`. They build the packed `Id` again every time. This is
   gone after B3.
6. UNDEF. `isUndefined()` becomes "datatype == Undefined". So a scan over the
   datatype array (1 byte per element) should be enough to find columns without
   UNDEF (`FindUndefRanges.h`, joins). That could be a gain.
7. Hashing. Two fields have to be combined. For the hash maps in GroupBy a key
   of 16 bytes costs memory and cache. One 64 bit mix is cheaper (as it is
   already in `SplitLayoutIdBitRepresentation`).
8. Compression. The datatype array is almost constant, so I expect it to
   compress well. The size on disk is 9 against 8 bytes per Id before the
   compression. I have to measure that.

## 6. Differences in behaviour between the two configurations (full range)

These differences are permanent and not temporary. Results at the edges depend on
the flag, so tests with limits have to know the configuration.

- Int: full `int64_t` (now 60 bit). Overflow checks and tests with `maxInt` and
  `minInt` change (C3). Results for values in `[2^59, 2^63)` change.
- Double: bit exact (now rounded by `FoldedId`). Values that had the same Id
  before are different now (`DISTINCT`, `GROUP BY`, joins). The order of the bits
  stays the same.
- Indexes: `maxIndex` becomes `UINT64_MAX`.
- `LocalVocabIndex`: the pointer uses the full payload. The comparison logic and
  the `static_assert`s about the order of the enum stay (B1).
- `Date` and `GeoPoint`: the encoding stays for now. A change would also change
  the index format, so it goes to E.

## 7. Open questions

1. ~~Redirect the names or rename?~~ Decided: the legacy class is renamed to
   `MixedValueId` and `ValueId` becomes an alias (C7).
2. Format on disk: 9 bytes (two arrays per block) or 16 bytes per Id?
3. Outside of columns (`IdTableRow` etc.): keep the struct of 16 bytes, or use a
   packed form?
4. Do old indexes still have to be readable (E3)?
5. What do callers of `getBits()` get that only want an opaque 64 bit value (hash
   keys)? I decide this in C0 and C1.
6. How much additional CI time are we willing to use for D3?
7. ~~One flag or two?~~ Decided: one flag. Both layouts at the same time make no
   sense. Between D1 and D2 the build with the flag is only an intermediate state
   (split columns, legacy Id).
8. ~~Keep `ColumnStorageTraits` in A2?~~ Decided: no. It was removed from A2
   ([#3578](https://github.com/ad-freiburg/qlever/pull/3578)) and comes as its own
   PR (D1) with both variants.
9. Which configuration is the default (F1)? That depends on the benchmarks. Until
   then the default stays legacy.
10. Do we need a way to move an index between the two configurations (E3), or is
    "build the index again" enough?
