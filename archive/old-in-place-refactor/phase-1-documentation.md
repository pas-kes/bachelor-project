# Refactor: Moving `Id` to a Full 64-Bit Payload

**Branch:** `refactor/create_64bit_id_type` (5 commits on top of master `47259112`)

**Big goal:** QLever's `Id`/`ValueId` currently packs a 4-bit datatype tag and a
60-bit payload into a single 64-bit word. This caps things like integers,
timestamps, and vocabulary indices at 60 usable bits. The goal of this branch
is to widen the payload to a *full* 64 bits by storing the datatype tag in its
own byte instead of stealing bits from the payload — at the cost of `Id`
growing from 8 to 16 bytes in memory (1 payload word + 1 datatype byte +
padding). Every commit below is a step toward that, ordered so the tree
builds and all tests pass at every commit boundary.

---

## [Commit 1](https://github.com/ad-freiburg/qlever/pull/3442/changes/291b96d3c2d8fb75aac516a3cf3d3b51ab4cd3f8) — `291b96d3` Introduce `IdColumn`/`ConstIdColumn` as aliases

Pure rename, no behavior change: introduces `IdColumn`/`ConstIdColumn` as
aliases for `ql::span<Id>`/`ql::span<const Id>` in the new
`src/engine/idTable/IdColumn.h`, and repoints every call site that used to
spell out the span type directly. This gives the rest of the refactor a
single seam to redefine later (see Commit 4/5) without having to touch every
call site again.

## [Commit 2](https://github.com/ad-freiburg/qlever/pull/3442/changes/a10aa8542e96a95269d93a287753337bb52642c9) — `3305c8c9` Store `ValueId` as 1 datatype byte + full 64-bit payload word

The actual bit-layout change: `Id::BitRepresentation` goes from a packed
4-bit-tag/60-bit-payload word to `{uint8_t datatype_; uint64_t payload_;}`,
and `Id::getBits()`/`Id::fromBits()` are rewritten around it. This is the
commit that actually achieves the "full 64-bit payload" goal — everything
else on the branch exists to convert old on-disk data and old in-memory
representations to/from this new layout.

## [Commit 3](https://github.com/ad-freiburg/qlever/pull/3442/changes/a1d43a5476e76d6e4c4ee1a616a59b9b97f4689d) — `0edf451a` Extend `qlever-upgrade-index` for the new `ValueId` byte layout

Since old on-disk indexes were written with the old 60-bit-payload layout,
`qlever-upgrade-index` needs to read the *old* format and rewrite it in the
new one. This introduces shadow structs (`LegacyId`, `LegacyPermutedTriple`,
`LegacyCompressedBlockMetadata`, `LegacyPermutationSummary`) in
[`src/index/IndexFormatConverter.cpp`](src/index/IndexFormatConverter.cpp)
that mirror the old format so the existing generic
`ad_utility::serialization` framework can still parse it.

Also includes the downgrade-writer machinery for
`MultiBlockIndexFormatConverterTest` (`legacyBitsFromId()`,
`writeLegacyPermutationFile()`, `downgradeIndexToLegacyFormat()`), so tests
can synthesize legacy-format fixtures at runtime instead of needing more
checked-in binary files.
This was necessary because the old "fake the version tag" trick only worked for transitions that didn't change the
on-disk byte width, and this one does.

## [Commit 4](https://github.com/ad-freiburg/qlever/pull/3442/changes/e253a92e734c70e4d966ffc3d5c1db9d3a3fa465) — `824bc701` Add split-column storage machinery for `IdColumn` (unused so far)

With `sizeof(Id)` now 16 bytes (vs. 8 before), storing `Id` columns as a
plain contiguous `Id[]` array wastes 7 bytes/entry to padding. This commit
adds, but does not yet wire in, a structure-of-arrays alternative: a
`uint64_t[]` payload array + `uint8_t[]` datatype array, addressed through
proxy types so existing call sites (`column[i].isUndefined()`, etc.) keep
compiling once this is wired in. Nothing existing includes these new files
yet, so this commit is risk-free in isolation.

## [Commit 5](https://github.com/ad-freiburg/qlever/pull/3442/changes/aee4128c6d4ba0742e51af3b707b7d1540dca3bc) — `7f3c849c` Switch `IdColumn`/`ConstIdColumn` to real split-column storage

The "flip the switch" commit: `IdColumn.h`'s aliases now point at
`columnBasedIdTable::IdColumn`/`ConstIdColumn` instead of `ql::span`, and
every caller that assumed contiguous `Id[]` storage, raw pointer access, or
`sizeof(Id)`-based byte math is fixed in the same commit (this has to be
atomic — there's no way to change the type without breaking every caller
simultaneously).

The main mechanical fallout: block sizes for compressed permutations/patterns
are computed by dividing a memory budget by bytes-per-row. That divisor used
to be `sizeof(Id) == 16`; with split storage it's
`BYTES_PER_ID_COLUMN_ENTRY == 9` (1 datatype byte + 8 payload bytes, no
padding). This ripples into hardcoded test byte-size constants that encode
"N rows per block" — fixed in
[`test/util/IndexTestHelpers.h`](test/util/IndexTestHelpers.h) (the central
default, `32_B` → `18_B`, which resolved most of the fallout at once),
[`test/CompressedRelationsTest.cpp`](test/CompressedRelationsTest.cpp),
[`test/index/IndexFormatConverterTest.cpp`](test/index/IndexFormatConverterTest.cpp),
and [`test/engine/IndexScanTest.cpp`](test/engine/IndexScanTest.cpp).
