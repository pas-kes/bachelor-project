# Open points of the Id refactor

Things I noticed during the work on the split layout and did not fix yet. They
are either not used with the relevant type yet, or they belong into a later PR.
I look at them again when the code path is used.

## `IdColumnVector::insertImpl` converts through `Id` for split column sources

`insertImpl` (`src/engine/idTable/splitLayout/IdColumnVector.h`) fills its two
temporary vectors with

```cpp
auto [datatype_, payload_] = getBitsCompat(static_cast<Id>(*first));
```

If `*first` is a `BasicIdRef` (dereferenced iterator of another split column),
`static_cast<Id>(*first)` first packs the already separate `payload_` and
`datatype_` into one word (`operator Id()` and `idFromBitsCompat`), and
`getBitsCompat(...)` splits that word again right away. That are two bit
operations per element which are not needed, because the source already has the
two values apart.

`BasicIdRef::getBits()` (`IdRef.h`) returns the bit representation directly
without any bit operations.

Idea for the fix: in `insertImpl`, check whether `decltype(*first)` has a
`.getBits()` that returns `Id::BitRepresentation`. If yes (split column as
source), use it directly. If not (plain `Id` as source), keep the current
`getBitsCompat(static_cast<Id>(*first))`.

Nobody calls `IdColumnVector::insert` or the range constructor with an
iterator of another split column yet (for example when merging one split column
into another), so this costs nothing at the moment. I look at it again when
such a call exists.

## `LocalVocabEntry::IdProxy` and the position cache for the split layout

`IdProxy` is `TypedIndex<IdBitRepresentation, tag>` (alias in
`engine/idTable/IdBitRepresentation.h`). For `ValueId` that is a `uint64_t`, for
`SplitLayoutId` a 16 byte struct `{datatype_, payload_}`. The cache in
`LocalVocabEntry` is unchanged: a `CopyableAtomic<IdProxy>` each for
`lowerBound` and `upperBound`, plus a `CopyableAtomic<bool>` flag. I tried a
once-pattern with a state flag and plain fields (`CachedPosition`) and reverted
it for now.

The problem in the split build is that `std::atomic` over a 16 byte struct (with
7 bytes of padding) is not lock-free everywhere:

- On Linux (gcc, and clang with libstdc++) the loads and stores are calls into
  `libatomic`, so `-latomic` has to be linked. `CMakeLists.txt` does not do that
  yet. On macOS arm64 it is not needed.
- `is_always_lock_free` is false there. On x86-64 a 16 byte load can be a
  `cmpxchg16b`, which writes to the cache line. The hot path `positionInVocab()`
  (two such loads for every comparison of a `LocalVocabIndex` Id with a vocab
  Id) could get slower with several threads.
- `LocalVocabEntry` and `PositionInVocab` grow from 16 to 32 bytes for the two
  bounds. I have to check the memory estimates and the `sizeof` tests.

What I want to check before this goes into a PR:

- Build the split variant with `-DUSE_SPLIT_LAYOUT=ON` in its own build
  directory on Linux (gcc and clang). Then decide whether to link `libatomic`
  conditionally in CMake (`if (NOT APPLE)`, better with a
  `check_cxx_source_compiles`) or to avoid wide atomics.
- Alternative 1, once-pattern: an atomic state `unknown`, `beingWritten`,
  `known` and plain fields that the first thread writes once (the others compute
  the same value and do not store it). That works for any size of `IdProxy` and
  needs no `libatomic`.
- Alternative 2, two fields: separate atomics for the datatype (`uint8_t`) and
  the payload (`uint64_t`) of each bound. Lock-free everywhere. It is safe
  because all writers store the same values and the flag is released after the
  writes. It only fits the split layout and needs a wrapper that depends on the
  layout.
- Benchmark the hot path `positionInVocab()` with several threads for the
  variant I choose. The legacy `uint64_t` build must not get slower.

More open points for the split build:

- `ValueId.h` (`compareThreeWay`) calls `IdProxy::make(getBits())` with a
  `uint64_t`, and `SplitLayoutId.h` does the same with the struct. So
  `SplitLayoutId.h` may only be included under `QLEVER_USE_ID_SPLIT_LAYOUT`, or
  both need a conversion.
- `FindUndefRanges.h` calls `row[...].incremented()`. In the split layout the
  row element is a `BasicIdRef` proxy, which has no `incremented()`.
- More call sites of `fromBits(...)` and `numDataBits` do not compile with the
  split layout yet: `SpecialIds.h`, `IndexFormatConverter.cpp`,
  `DeltaTriples.cpp`, `LocalVocab.cpp`, `IndexRebuilder.cpp`,
  `CompressedRelationReader.h`, `EncodedIriManager.h`.
- `SplitLayoutIdBitRepresentation::incremented()` compared `payload_` with the
  maximum of `DatatypeType` instead of `PayloadType`. That is fixed, but there is
  no test yet for the overflow into the datatype.

## `alignas(16)` on `LocalVocabEntry` and `BasicLiteralOrIri`

`LocalVocabEntry` (`src/index/LocalVocabEntry.h`) and its base class
`BasicLiteralOrIri` (`src/parser/LiteralOrIri.h`) are `alignas(16)`. The only
place that needs this is the legacy `makeFromLocalVocabIndex` in `ValueId.h`
(`MixedValueId.h` after #3646). It stores the pointer as `ptr >>
numDatatypeBits` (4 bits) in the 60 bit payload, and the
`static_assert(alignof(...) >= (1u << numDatatypeBits))` checks that the lower 4
bits are always zero.

`SplitLayoutId::makeFromLocalVocabIndex` stores the pointer unchanged in the 64
bit payload, so it does not need the alignment. It only pads every entry to a
multiple of 16 bytes.

Not for the first split layout PR, but to check later:

- Make `alignas(16)` depend on the layout (`#ifndef QLEVER_USE_ID_SPLIT_LAYOUT`)
  in both classes. The legacy layout stays selectable and still needs it.
- Measure `sizeof(LocalVocabEntry)` and `sizeof(BasicLiteralOrIri)` with and
  without it in the split build. It is only worth it if the size really shrinks.
- Check that nothing else relies on the alignment. So far I only searched for
  `alignas` and `alignof` in `src/index/LocalVocab*` and `src/parser`.
- `StringMapping::remapId` and its test shift the counter by
  `Id::numDatatypeBits`, to undo the `>> 4` of the legacy
  `makeFromLocalVocabIndex`. The split layout has no such shift and no
  `numDatatypeBits`, so this needs a helper that does not depend on the layout.

## `BYTES_PER_ID_COLUMN_ENTRY` and the memory estimates

`BYTES_PER_ID_COLUMN_ENTRY` (`global/Id.h`) is the size of one entry of an
`IdTable` column, in memory and in the packed on-disk representation. For the
legacy layout it is `sizeof(Id)`. For the split layout it is 9 bytes (payload
array plus datatype array), while `sizeof(Id)` is 16 with padding. These
estimates used `sizeof(Id)` for column entries and use the constant now:
`CacheValue::getSize`, `IndexScan::unlikelyToFitInCache`, `Sort::computeResult`,
`CompressedExternalIdTable::memorySizeOfElement`, the block sizes in
`ExternalIdTableSorterMergeConfig.h` and `triplesBytesPerWorker` in
`IndexImpl.cpp`.

What I have to check before the split layout is selected:

- `src/engine/GroupByImpl.cpp`, `LazyGroupByRange::yieldFinalValue()`, the dummy
  table for the last group (comment "Process remaining items in the last
  group"):

  ```cpp
  IdTable idTable{inWidth_, ad_utility::makeAllocatorWithLimit<Id>(
                                1_B * BYTES_PER_ID_COLUMN_ENTRY * inWidth_)};
  idTable.emplace_back();
  ```

  This is a hard allocator limit that fits exactly one row: in the split layout
  8 bytes payload and 1 byte datatype per column, so there is no margin. It only
  works if `emplace_back()` allocates exactly one entry per column. For the
  `std::vector` columns of the legacy layout that is true (capacity 1), but I
  have not checked it for `IdColumnVector`. The constant stays here (it was
  `sizeof(Id)` before, which has 16 bytes per column and therefore some margin).
  Before the split layout is selected I either check how `IdColumnVector`
  grows on the first `emplace_back`, or I give the limit a margin again.
- Tests with fixed numbers for 8 bytes (five `ExternalIdTableSorterMergeConfig`
  tests and `CompressedExternalIdTable.runsInputForwardsTheBlockMetadata`) fail
  in the split build. The numbers have to be computed from the constant.
- These places stay with `sizeof(Id)`, because they allocate or store `Id`
  objects: `allocator.allocate(n)` in `CartesianProductJoinTest` and
  `QleverTest`, the one-row limit in `ResultTest`, `ConstructDeduplicator` and
  `IdTriple`.
- No test covers these places (I found that by doubling the constant at all of
  them): `CacheValue::getSize`, `Sort.cpp`, `GroupByImpl.cpp`, `IndexImpl.cpp`.
- A test with a fake column storage of 9 bytes per entry (and an `Id` of 16
  bytes) needs the entry size from the table type, not from the global
  constant. That belongs to the traits PR.

## `std::tie` against `std::array` on Linux (PR #3577)

On macOS the arrays are faster than `std::tie` in the comparison of a located
triple with a row. On Linux (aarch64 in Docker) they are not: with g++ 13 they
are 13 % (`<`) and 38 % (`==`) slower, with clang 18 20 % slower for `<` and 25 %
faster for `==`. The difference is below 1 ns per comparison. The numbers and
the setup are in `PROTOCOL_tie_vs_array_2026-10-08.md`, section 6.

What I still want to do:

- Find out why the arrays are slower with g++. There the call of
  `ValueId::compareThreeWay` is inlined in all variants, so it is not the same
  reason as on the Mac. The array variants have more instructions. I would look
  at the assembly of the loops and at where the two copies of the arrays go.
- Measure on x86-64 (a real machine or the CI runner), with the compilers and
  versions that the CI uses (g++ 11 to 13, clang 16 to 21), with libc++ on Linux
  and in C++17 mode. So far I only have aarch64 in a VM, gcc 13 and clang 18 with
  libstdc++.
- Measure with the real code paths (updates, vacuum in `LocatedTriples`) and not
  only with the micro benchmark.
- Try a hand-written comparison with `ql::compareThreeWay` per element. In all
  my runs on Linux it is the fastest variant, and on the Mac it is about as fast
  as the array. It would be one small helper that works with `Id&` as well as
  with proxies, and then neither tie nor array would be needed.
- If x86-64 shows a loss at the hot comparator in `LocatedTriples`, decide
  between the helper, `std::tie` for `Id&` and `std::array` only for proxies
  (`if constexpr`), or leaving it as it is because the loss is small.
