# Plan: new `SplitLayoutId` next to `ValueId`

## 1. Goal

- Build a new Id (64 bit payload + 8 bit datatype, `sizeof` 16) next to
  `ValueId`, in `engine/idTable/splitLayout/`.
- Use the full 64 bit payload range right away.
- Do it in small PRs, so that switching is only the flag.
- **The legacy Id stays, permanently.** It has fewer bits and is expected to be
  faster, so it remains a selectable option. It is called `MixedValueId` now
  (datatype and value mixed in one word), `ValueId` is an alias of whichever Id
  is active. Both Ids are kept and chosen at build time with one flag. Which
  one is the default is decided later, with benchmarks.
- The index format changes later, in its own PR series.

## 2. Where we are (2026-10-08)

All pull requests are listed in `readme.md`.

- **Merged:** [#3459](https://github.com/ad-freiburg/qlever/pull/3459) (A1, `IdColumnRef` / `ConstIdColumnRef` aliases),
  [#3460](https://github.com/ad-freiburg/qlever/pull/3460) (A6, forwarding lambdas), [#3462](https://github.com/ad-freiburg/qlever/pull/3462) (B0, the split-layout column
  types: `IdRef`, column views, iterator, `IdColumnVector`, bit
  representation). Nothing uses the new types yet, and they still convert
  through the old `Id`.
- **Open, based on master:** [#3578](https://github.com/ad-freiburg/qlever/pull/3578) (A2), [#3575](https://github.com/ad-freiburg/qlever/pull/3575) (A3), [#3577](https://github.com/ad-freiburg/qlever/pull/3577) (A4),
  [#3576](https://github.com/ad-freiburg/qlever/pull/3576) (A5), [#3553](https://github.com/ad-freiburg/qlever/pull/3553) (A7), [#3615](https://github.com/ad-freiburg/qlever/pull/3615) (B1), [#3646](https://github.com/ad-freiburg/qlever/pull/3646) (C7 preparation).
- **Closed:** [#3457](https://github.com/ad-freiburg/qlever/pull/3457) and [#3458](https://github.com/ad-freiburg/qlever/pull/3458), the first versions of A1 and A6, replaced
  by [#3459](https://github.com/ad-freiburg/qlever/pull/3459) and [#3460](https://github.com/ad-freiburg/qlever/pull/3460).
- **Draft:** [#3442](https://github.com/ad-freiburg/qlever/pull/3442), the big branch of the first approach (see `archive/`).
- `ColumnStorageTraits` is no longer part of A2, it comes back as its own PR
  (D1).
- **Local only, no PR yet:** the new Id itself (`create_64bit_id_new_splitLayoutId`:
  `SplitLayoutId`, `IdBitRepresentation.h`, CMake option `USE_SPLIT_LAYOUT`).

## 3. Problems with my first idea

1. **The index format has to come before the switch.** Ids are written raw at
   several places and depend on `sizeof(Id) == 8`
   (`CompressedRelationReader.cpp:1015`, `Pattern.h:37`,
   `CompressedIdTableBlocks.h:63`, `IdTriple.h:96`, `TripleSerializer.h`).
   "Later" only holds relative to the Id class PRs.
2. **Changing `using Id = ...` in `Id.h` is not enough.** `ValueId` is used
   ~829 times outside of `ValueId.h` / `Id.h`. If `ValueId` kept meaning the
   legacy class, those places would silently stay on the legacy Id in a
   flag-on build. Solution ([#3646](https://github.com/ad-freiburg/qlever/pull/3646)): the legacy class is renamed to
   `MixedValueId` (file `MixedValueId.h`), and `ValueId` becomes an alias of
   `Id` (`using Id = MixedValueId; using ValueId = Id;`). So the old name
   follows the flag and the ~829 places do not have to be touched at once.
3. **Typed tests over both Ids only work for the shared range, and they stay.**
   The full range is different on purpose (`maxInt`, `maxIndex`, double
   rounding), so edge cases need separate tests per type, and tests with limits
   depend on the flag permanently.
4. **Both configurations have to stay green forever.** The CI job with the
   flag is expensive and red for a long time at first. Start it as
   non-blocking, make it required once green, then keep it as a permanent
   second configuration.

## 4. PRs

`[solo]` = can be merged on its own, no change in behaviour.

### A: prep on master (running)

All `[solo]`, all based on master, independent of each other. They make the
generic code work with proxy elements (`IdRef` is a prvalue, not an `Id&`).
That is needed whenever the split *storage* is used, no matter which Id class
sits inside. Status on 2026-10-08:

| # | PR | Branch | What it does | Status / notes |
|---|---|---|---|---|
| A1 | [#3459](https://github.com/ad-freiburg/qlever/pull/3459) | `datatype_alias` | `IdColumnRef` / `ConstIdColumnRef` aliases | merged |
| A2 | [#3578](https://github.com/ad-freiburg/qlever/pull/3578) | `trivial_type_changes` | `decltype(auto)` instead of `T&` in `IdTable` / `IdTableRow` element access; call sites take `Id` / `auto&&` instead of `Id&` (with `std::forward` in lambdas); new test `IdTable.rowReferenceOperatorBracket` with an `Id&` and a proxy-returning table | open. **`ColumnStorageTraits` is no longer part of it** (own PR, D1), `IdTable.h` is unchanged. Zero cost |
| A3 | [#3575](https://github.com/ad-freiburg/qlever/pull/3575) | `assign_swap` | `IdTableRow::swapImpl` swaps via `using std::swap; swap(a[i], b[i]);` (ADL), the proxy provides a hidden-friend `swap` taking `const&` | open. Zero cost, diff is only `IdTableRow.h` |
| A4 | [#3577](https://github.com/ad-freiburg/qlever/pull/3577) | `tie_to_array` | `std::tie(row[I]...)` -> `std::array<Id, N>` in row comparisons and `LocatedTriples` projections (a tie of prvalue proxies would dangle); helpers renamed `tie...` -> `...AsArray`; benchmark `TieVsArrayBenchmark` | open. Measured, not slower (about 30 % faster): the compiler inlines `ValueId::compareThreeWay` for arrays but not for tuples, see `PROTOCOL_tie_vs_array_2026-10-08.md` |
| A5 | [#3576](https://github.com/ad-freiburg/qlever/pull/3576) | `remaining_changes` | Sites that need a real `Id`. Reworked as planned: `EmptyPath::graphsOf` and `getIdsFromVariable` return the column view `ConstIdColumnRef` (no copy), `PathSearch` / `PathQuery` use the new alias `IdColumn = std::vector<Id>` in `IdTable.h`, `ExportQueryExecutionTrees` copies the single `Id` | open. PR title still says "Return owned Id vectors", update before merging |
| A6 | [#3460](https://github.com/ad-freiburg/qlever/pull/3460) | `replace_lambdas` | `&Id::isUndefined` and similar -> `static constexpr` lambdas in `ValueId` (`Id::isUndefinedL`, `isDefinedL`, `getBitsL`, `getDatatypeL`) | merged. `SplitLayoutId` has to provide the same set (B7) |
| A7 | [#3553](https://github.com/ad-freiburg/qlever/pull/3553) | `bytes_per_column_entry` | `BYTES_PER_ID_COLUMN_ENTRY` in `global/Id.h` (moved there because #3462 removed `IdColumn.h`); used at the on-disk / spill-file sites and, since 2026-10-08, also at the memory estimates of `IdTable` columns (`QueryExecutionContext.h`, `IndexScan.h`, `Sort.cpp`, `CompressedExternalIdTable.h`, `ExternalIdTableSorterMergeConfig.h`, `GroupByImpl.cpp`, `IndexImpl.cpp`); `TextMetaData::sizeOnDisk` uses the real member sizes | open. Constant is `sizeof(Id)` for now, has to be 9 for the split layout (D1). Open points in `TODO_id_refactor_performance.md` (`GroupByImpl` limit, tests with hard-coded 8 bytes, wire format and hash sites) |

What changes for the order: nothing blocks anything, A2–A7 can be merged in any
order. Overlap: `JoinAlgorithmBenchmark.cpp` is touched by A2 and A5 in
different lines (checked with `git merge-tree`, merges without conflict).

### B: build the new Id (new code and tests only)

| # | What | Depends on |
|---|---|---|
| B0 | `base_classes` ([#3462](https://github.com/ad-freiburg/qlever/pull/3462)), merged (without `ColumnStorageTraits`, see D1) | – |
| B1 | Move `Datatype` enum to its own small header `[solo]`, open as [#3615](https://github.com/ad-freiburg/qlever/pull/3615) | – |
| B2 | `SplitLayoutId` core: bits, undefined, Int (`int64_t`), Double, Bool, `compareThreeWay`, hash, `operator<<`. **With benchmark** vs `ValueId` | B0, B1 |
| | **Gate:** look at the benchmark results (section 5) | |
| B3 | `BasicIdRef` builds the new Id directly, remove `getBitsCompat` / `toId()` | B2 |
| B4 | Index types (`VocabIndex`, `TextRecordIndex`, `BlankNodeIndex`, ...) | B2 |
| B5 | `LocalVocabIndex` + comparison with vocab position | B4, C2 |
| B6 | `Date`, `GeoPoint` (encoding stays for now) | B2 |
| B7 | Rest of the API (`visit`, `compareWithoutLocalVocab`, ...), including the `*L` lambdas from A6 | B2–B6, A6 |
| B8 | Typed tests over both Ids (shared range) + edge-case tests. Permanent, they keep both Ids in sync | B7 |

### C: make existing code independent of the Id type (on master)

Does not depend on B, can start now. All `[solo]`.

| # | What | Size |
|---|---|---|
| C0 | Audit, no code: sort the uses of `getBits()` / `fromBits()` / `Id::T` / `numDataBits` (hash key, sort key, disk, unrelated) | ~86 / ~33 / ~24 / ~43 |
| C1 | Replace the `Id::T` dependency by a small abstraction, one PR per directory | from C0 |
| C2 | `LocalVocabEntry` / `IdProxy` no longer takes raw `T` bits | small |
| C3 | Check everything that assumes 60 bit (`maxInt`, `IntegerType`, `maxIndex`, `FoldedId`) | ~26 / ~14 / ~8 |
| C4 | Decouple disk from memory: explicit Id <-> on-disk conversion, still the old 8 byte format | ~6 places |
| C5 | Replace raw `.data()` access on Id columns (compression, external sort) | few, but central |
| C6 | Memory estimates: `sizeof(Id)` -> bytes per column entry (8 legacy / 9 split). **Started in A7** ([#3553](https://github.com/ad-freiburg/qlever/pull/3553)) for the `IdTable` column sites; what is left: the constant per flag (D1), the wire format of the binary export and the byte hash in `Pattern.h` (they must not depend on the layout), tests with hard-coded numbers | few places |
| C7 | Rename of the legacy class, `ValueId` as alias of `Id` ([#3646](https://github.com/ad-freiburg/qlever/pull/3646), open). Replaces the mechanical rename of ~829 places (section 3, point 2). The ~829 uses can be changed to `Id` later, file by file, without a deadline | done in the PR |

### D: integration behind a flag

| # | What | Depends on |
|---|---|---|
| D1 | **`ColumnStorageTraits` + the flag** (own PR). The traits are no longer part of A2, so this PR adds the legacy variant, the use in `IdTable` (`ViewSpans`, both `operator()`, `subView`, both `getColumn`) and the split variant (`IdRef`, `IdColumnRef`, `IdColumnVector`) and the build flag (CMake option + compile definition, default off) that selects the variant. Also makes the bytes-per-entry constant of A7 / C6 depend on the flag. Flag off: same types as before, no change in behaviour. Flag on: split columns, but still with the legacy `Id` (works through the compat bridge), so `IdTable` can be tested with split columns before the new Id exists. This is the one and only flag, D2 extends it | B0 |
| D2 | The same flag now also switches the Id: `Id` points to the new class (`MixedValueId` stays the legacy class, `ValueId` follows `Id`), switch the `IdColumnRef` aliases. `MixedValueIdTest` is only built without the flag. Flag on = new Id and split columns together, flag off = legacy. Default stays off | D1, B8, C7 |
| D3 | CI job with the flag (non-blocking) | D2, C1–C5 |
| D4 | Fix everything that breaks in that job (PRs on master) | D3 |
| D5 | Make the job required once it is green. It stays as a permanent second configuration | D4 |

Note on D1: with the flag on, all call sites that use `.data()` on an Id
column stop compiling (C5). That does not block merging D1 (flag is off by
default), but it blocks the CI job (D3).

### E: index format (own series)

| # | What |
|---|---|
| E1 | New format behind the C4 abstraction, bump version. **The format depends on the flag**: the index records which Id layout it was built with, and a build with the other layout refuses to load it with a clear error |
| E2 | Column serialization (payload and datatype arrays separate) |
| E3 | Converter between the two formats, only if we want to move indexes between the two configurations |

### F: default and cleanup

The flag does not go away.

| # | What |
|---|---|
| F1 | Decide the default configuration with benchmarks (new Id vs. `ValueId`), switch the default if the new one wins, document the choice |
| F2 | Cleanup: leftovers in `TODO_id_refactor_performance.md`, remove the `getBitsCompat` / `toId()` bridge if still there |

## 5. Performance (to measure after B2)

There is `benchmark/IdColumnBenchmark.cpp` already. `ValueId` stays, so the
legacy build must not get slower either: measure the prep PRs (A2–A7) on the
legacy configuration, not only the new one.

1. **Memory.** A single Id is 16 byte instead of 8. A split column costs
   9 byte per element (+12.5 %). Everything outside columns doubles:
   `IdTriple`, `IdTableRow`, `std::vector<Id>`, `HashMap<Id, ...>`. The size
   estimates (`IndexScan.h:200`, `QueryExecutionContext.h:58`, `Sort.cpp:89`,
   `GroupByImpl.cpp:223`, external sorter configs) must use 9 byte per column
   entry (A7), not 8 and not 16.
2. **Comparison.** Was one 64 bit compare, is now (datatype, payload). Compare
   branching vs. branch-free (`cmp` + `sbb`, or one `unsigned __int128`
   compare).
3. **Sorting.** `ranges::sort` over proxy iterators moves two arrays per
   element (two loads, two stores). Measure 1 column and several columns. If
   it is slower: sort 128 bit keys, or sort a permutation and apply it.
4. **Bulk operations.** `insertAtEnd` / `clone` use `memmove` because `Id` is
   trivially copyable. For split columns that has to be two `memmove`s per
   column. Right now the `IdColumnVector` range constructor does one
   `push_back` per element, and `insertImpl` packs and unpacks (see the TODO
   file).
5. **`BasicIdRef` getters.** They rebuild the packed `Id` every time. Gone
   after B3.
6. **UNDEF.** `isUndefined()` becomes "datatype == Undefined", so a scan over
   the datatype array (1 byte per element) should be enough to find columns
   without UNDEF (`FindUndefRanges.h`, joins). Possible gain.
7. **Hashing.** Two fields to combine. For GroupBy hash maps a 16 byte key
   costs memory and cache. One 64 bit mix is cheaper (like in
   `SplitLayoutIdBitRepresentation` already).
8. **Compression.** The datatype array is almost constant, so I expect it to
   compress well. Disk size 9 vs. 8 byte per Id before compression. Needs a
   measurement.

## 6. Behaviour differences between the two configurations (full range)

These are permanent, not temporary. Results at the edges differ depending on
the flag, so tests with limits have to know the configuration.

- **Int:** full `int64_t` (now 60 bit). Overflow checks and tests with
  `maxInt` / `minInt` change (C3). Results for values in `[2^59, 2^63)`
  change.
- **Double:** bit exact (now rounded by `FoldedId`). Values that used to
  share an Id are now different (`DISTINCT`, `GROUP BY`, joins). Ordering of
  the bits stays the same.
- **Indexes:** `maxIndex` becomes `UINT64_MAX`.
- **`LocalVocabIndex`:** pointer uses the full payload. The comparison logic
  and the `static_assert`s about the enum order stay (B1).
- **`Date` / `GeoPoint`:** encoding stays for now. A change would also change
  the index format, so it goes to E.

## 7. Open questions

1. ~~Redirect the names or rename?~~ Decided by `ValueId` staying: rename
   `ValueId` -> `Id` (C7).
2. Disk format: 9 byte (two arrays per block) or 16 byte per Id?
3. Outside columns (`IdTableRow` etc.): keep the 16 byte struct, or a packed
   form?
4. Do old indexes still have to be readable (E3)?
5. What do `getBits()` callers get that only want an opaque 64 bit value
   (hash keys)? Decide in C0 / C1.
6. How much extra CI time are we willing to spend on D3?
7. ~~One flag or two?~~ Decided: one flag. Both layouts at the same time make
   no sense. Between D1 and D2 the flag-on build is only an intermediate state
   (split columns, legacy Id).
8. ~~Keep `ColumnStorageTraits` in A2?~~ Decided: no. It was removed from A2
   (#3578) and comes as its own PR (D1) with both variants.
9. Which configuration is the default (F1)? Benchmark-driven. Until then the
   default stays legacy.
10. Do we need a way to move an index between the two configurations (E3), or
    is "rebuild the index" fine?
