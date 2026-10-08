# Accepted performance trade-offs (Id refactor)

Known, currently-accepted inefficiencies noted during the 64-bit-Id/split-column
refactor. Not fixed yet because the code path isn't exercised with the
relevant input type yet; revisit once it is.

## `IdColumnVector::insertImpl` round-trips through `Id` unnecessarily for split-column sources

`insertImpl` (`src/engine/idTable/splitLayout/IdColumnVector.h`) builds its two
temporary vectors via:

```cpp
auto [datatype_, payload_] = getBitsCompat(static_cast<Id>(*first));
```

If `*first` is a `BasicIdRef` (dereferencing a `BasicIdColumnIterator` over
another split-storage column), `static_cast<Id>(*first)` first **combines**
its already-separate `payload_`/`datatype_` into one packed word (via
`operator Id()` -> `idFromBitsCompat`, bit-shifting), and `getBitsCompat(...)`
immediately **splits** that packed word back apart (bit-shifting again) --
two unnecessary bit operations per element, since the source already had the
two values sitting apart in memory with nothing to combine or split.

`BasicIdRef::getBits()` (`IdRef.h`) already returns `Id::BitRepresentation`
directly, with no bit math, for exactly this case.

Fix (not yet applied): dispatch in `insertImpl` on whether `decltype(*first)`
has a `.getBits()` returning `Id::BitRepresentation` (split-column source --
use it directly) vs. not (plain `Id` source -- keep the current
`getBitsCompat(static_cast<Id>(*first))` path).

Not yet exercised: nothing currently calls `IdColumnVector::insert`/the
range constructor with a `BasicIdColumnIterator`-based source (e.g. merging
one split column into another), so the round-trip has no measurable cost
today. Revisit once such a call site exists.

## `LocalVocabEntry::IdProxy` / position cache for the split layout (to check)

Status: `IdProxy` is `TypedIndex<IdBitRepresentation, tag>` (alias in
`engine/idTable/IdBitRepresentation.h`), so it is `uint64_t` for `ValueId` and a
16-byte `{datatype_, payload_}` struct for `SplitLayoutId`. The cache in
`LocalVocabEntry` is unchanged: `CopyableAtomic<IdProxy>` for `lowerBound` and
`upperBound` plus a `CopyableAtomic<bool>` flag. (A once-pattern with a state
flag and plain fields, `CachedPosition`, was tried and reverted for now.)

Problem in the split build: `std::atomic` over a 16-byte struct (7 bytes
padding) is not lock-free everywhere.

- Linux (gcc, clang with libstdc++): load/store are calls into `libatomic`, so
  `-latomic` has to be linked; `CMakeLists.txt` does not do that yet. macOS
  arm64 does not need it.
- `is_always_lock_free` is false there. On x86-64 a 16-byte load may be a
  `cmpxchg16b` (a write to the cache line), so the hot path `positionInVocab()`
  (two such loads per comparison of a `LocalVocabIndex` Id with a vocab Id)
  could get slower with several threads.
- `LocalVocabEntry` and `PositionInVocab` grow from 16 to 32 bytes for the two
  bounds; check memory estimates and `sizeof` tests.

To check before this goes into a PR:

- Build the split variant with `-DUSE_SPLIT_LAYOUT=ON` in its own build dir
  on Linux (gcc and clang) and decide: link `libatomic` conditionally in CMake
  (`if (NOT APPLE)`, ideally with a `check_cxx_source_compiles`), or avoid wide
  atomics.
- Alternative 1, once-pattern: an atomic state `unknown/beingWritten/known`
  plus plain fields that are written once by the first thread (the others
  compute the same value and do not store). Works for any size of `IdProxy`,
  no `libatomic`.
- Alternative 2, two-field variant: separate atomics for the datatype
  (`uint8_t`) and the payload (`uint64_t`) of each bound. Lock-free everywhere.
  Safe because all writers store the same values and the "known" flag is
  released after the writes, but it is specific to the split layout and needs a
  layout-dependent wrapper.
- Benchmark the hot path `positionInVocab()` with several threads for the
  chosen variant; the legacy `uint64_t` build must not get slower.

Related open points for the split build:

- `ValueId.h` (`compareThreeWay`) still calls `IdProxy::make(getBits())` with a
  `uint64_t`; and `SplitLayoutId.h` does the same with the struct, so
  `SplitLayoutId.h` must only be included under `QLEVER_USE_ID_SPLIT_LAYOUT`
  (or both need a conversion).
- `FindUndefRanges.h` calls `row[...].incremented()`; in the split layout the
  row element is a `BasicIdRef` proxy, which has no `incremented()`.
- Other `fromBits(...)`/`numDataBits` call sites (`SpecialIds.h`,
  `IndexFormatConverter.cpp`, `DeltaTriples.cpp`, `LocalVocab.cpp`,
  `IndexRebuilder.cpp`, `CompressedRelationReader.h`, `EncodedIriManager.h`)
  do not compile with the split layout yet.
- `SplitLayoutIdBitRepresentation::incremented()` compared `payload_` with the
  maximum of `DatatypeType` instead of `PayloadType`; fixed, but there is no
  test for the overflow into the datatype yet.

## `alignas(16)` on `LocalVocabEntry` / `BasicLiteralOrIri` (to check)

`LocalVocabEntry` (`src/index/LocalVocabEntry.h`) and its base class
`BasicLiteralOrIri` (`src/parser/LiteralOrIri.h`) are `alignas(16)`. The only
place that needs it is the legacy `makeFromLocalVocabIndex` (`LegacyValueId.h`):
the pointer is stored as `ptr >> numDatatypeBits` (4 bits) into the 60-bit
payload, and the `static_assert(alignof(...) >= (1u << numDatatypeBits))` checks
that the low 4 bits are always zero.

`SplitLayoutId::makeFromLocalVocabIndex` stores the pointer verbatim in the
64-bit payload, so the alignment is not needed for it. It only pads every entry
to a multiple of 16 bytes.

To check (not part of the first split-layout PR):

- Make `alignas(16)` conditional on the legacy layout (`#ifndef
  QLEVER_USE_ID_SPLIT_LAYOUT`) in both classes; the legacy layout stays a
  selectable option and still needs it.
- Measure `sizeof(LocalVocabEntry)` and `sizeof(BasicLiteralOrIri)` with and
  without it in the split build. Only worth it if the size actually shrinks.
- Check that nothing else relies on the alignment (I only searched for
  `alignas`/`alignof` in `src/index/LocalVocab*` and `src/parser`).
- Related: `StringMapping::remapId` and its test shift the counter by
  `Id::numDatatypeBits` to counter the `>> 4` of the legacy
  `makeFromLocalVocabIndex`. In the split layout there is no such shift
  (and no `numDatatypeBits`); needs a layout-independent helper.

## `BYTES_PER_ID_COLUMN_ENTRY` and the memory estimates (to check)

`BYTES_PER_ID_COLUMN_ENTRY` (`global/Id.h`) is the size of an entry of an
`IdTable` column, in memory and in the packed on-disk representation. It is
`sizeof(Id)` for the legacy layout; for the split layout it is 9 bytes
(payload array plus datatype array), whereas `sizeof(Id)` is 16 with padding.
The estimates that used `sizeof(Id)` for column entries use the constant now:
`CacheValue::getSize`, `IndexScan::unlikelyToFitInCache`, `Sort::computeResult`,
`CompressedExternalIdTable::memorySizeOfElement`, the block sizes in
`ExternalIdTableSorterMergeConfig.h` and `triplesBytesPerWorker` in
`IndexImpl.cpp`.

To check before the split layout is selected:

- `src/engine/GroupByImpl.cpp`, `LazyGroupByRange::yieldFinalValue()`, the
  dummy table for the last group ("Process remaining items in the last
  group"):

  ```cpp
  IdTable idTable{inWidth_, ad_utility::makeAllocatorWithLimit<Id>(
                                1_B * BYTES_PER_ID_COLUMN_ENTRY * inWidth_)};
  idTable.emplace_back();
  ```

  The limit is a hard allocator limit and fits exactly one row: 8 bytes payload
  plus 1 byte datatype per column in the split layout, so there is no margin. It
  works only if `emplace_back()` allocates exactly one entry per column. That is
  true for the `std::vector` columns of the legacy layout (capacity 1), but not
  checked for `IdColumnVector`. We keep the constant here (it was `sizeof(Id)`
  before, which has 16 bytes per column and therefore a margin), but have to make
  sure of this before the split layout is selected: either check the growth of
  `IdColumnVector::emplace_back` for the first element, or give the limit a
  margin again.
- Tests with hard-coded numbers for 8 bytes (five `ExternalIdTableSorterMergeConfig`
  tests and `CompressedExternalIdTable.runsInputForwardsTheBlockMetadata`) break
  in the split build; they have to be derived from the constant.
- Sites that stay with `sizeof(Id)` because they allocate or store `Id` objects:
  `allocator.allocate(n)` in `CartesianProductJoinTest` and `QleverTest`, the
  one-row limit in `ResultTest`, `ConstructDeduplicator`, `IdTriple`.
- Not covered by a test (found by doubling the constant at all sites):
  `CacheValue::getSize`, `Sort.cpp`, `GroupByImpl.cpp`, `IndexImpl.cpp`.
- A test with a fake column storage of 9 bytes per entry (and an `Id` of 16
  bytes) needs the entry size from the table type (traits PR) instead of the
  global constant.
