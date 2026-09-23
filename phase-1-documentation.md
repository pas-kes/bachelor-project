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

## [Commit 1 — `291b96d3` Introduce `IdColumn`/`ConstIdColumn` as aliases](https://github.com/ad-freiburg/qlever/pull/3442/changes/291b96d3c2d8fb75aac516a3cf3d3b51ab4cd3f8)

Pure rename, no behavior change: introduces `IdColumn`/`ConstIdColumn` as
aliases for `ql::span<Id>`/`ql::span<const Id>` in the new
`src/engine/idTable/IdColumn.h`, and repoints every call site that used to
spell out the span type directly. This gives the rest of the refactor a
single seam to redefine later (see Commit 4/5) without having to touch every
call site again.

## [Commit 2 — `3305c8c9` Store `ValueId` as 1 datatype byte + full 64-bit payload word](https://github.com/ad-freiburg/qlever/pull/3442/changes/a10aa8542e96a95269d93a287753337bb52642c9)

The actual bit-layout change: `Id::BitRepresentation` goes from a packed
4-bit-tag/60-bit-payload word to `{uint8_t datatype_; uint64_t payload_;}`,
and `Id::getBits()`/`Id::fromBits()` are rewritten around it. This is the
commit that actually achieves the "full 64-bit payload" goal — everything
else on the branch exists to convert old on-disk data and old in-memory
representations to/from this new layout.

Also carries the `relationSizesAtTheSmallRelationThreshold`/
`ScanSpecAndBlocks.removePrefix` test-constant fixes that logically belong
with the size change, and a documented, empirically-verified investigation
into a `boost::asio` thread-pool allocator budget (confirmed not an issue
introduced by this change, kept only as an explanatory note in the commit
message).

## [Commit 3 — `0edf451a` Extend `qlever-upgrade-index` for the new `ValueId` byte layout](https://github.com/ad-freiburg/qlever/pull/3442/changes/a1d43a5476e76d6e4c4ee1a616a59b9b97f4689d)

Since old on-disk indexes were written with the old 60-bit-payload layout,
`qlever-upgrade-index` needs to read the *old* format and rewrite it in the
new one. This introduces shadow structs (`LegacyId`, `LegacyPermutedTriple`,
`LegacyCompressedBlockMetadata`, `LegacyPermutationSummary`) in
[`src/index/IndexFormatConverter.cpp`](src/index/IndexFormatConverter.cpp)
that mirror the old format so the existing generic
`ad_utility::serialization` framework can still parse it.

Two things worth calling out here:

- **`EncodedVal` bug you caught in testing:** `LegacyId::convert()` initially
  had `EncodedVal` go through separate extract/reshift logic from `Double`.
  Both types actually occupy the *entire* legacy payload width the same way
  `Double` does, so both now convert through the same
  `payload() << numDatatypeBits` shift. Found because
  `convertIdOfEachDatatype` wasn't updated for `EncodedVal`'s corrected shift
  and started failing.
- **Const-correctness fix you asked me to audit for and integrate here:**
  `legacyScanAndConvert(std::string filename, LegacyPermutationSummary summary, ...)`
  originally took `summary` *by value*, as a "sink" pattern so the returned
  lazy range could own its data. But the per-block transform lambda inside it
  only ever reads `const LegacyCompressedBlockMetadata&` — it never moves out
  of `summary` — and both call sites need `summary` again afterward (for
  `verifyConvertedPermutation`), so the by-value copy was pure waste. Changed
  to `const LegacyPermutationSummary& summary`; safe because both callers
  hand the returned range straight into `writePermutation(...)`, which
  consumes it synchronously before either caller touches `summary` again.

Also includes the downgrade-writer machinery for
`MultiBlockIndexFormatConverterTest` (`legacyBitsFromId()`,
`writeLegacyPermutationFile()`, `downgradeIndexToLegacyFormat()`), so tests
can synthesize legacy-format fixtures at runtime instead of needing more
checked-in binary files — this was necessary because the old "fake the
version tag" trick only worked for transitions that didn't change the
on-disk byte width, and this one does.

## [Commit 4 — `824bc701` Add split-column storage machinery for `IdColumn` (unused so far)](https://github.com/ad-freiburg/qlever/pull/3442/changes/e253a92e734c70e4d966ffc3d5c1db9d3a3fa465)

With `sizeof(Id)` now 16 bytes (vs. 8 before), storing `Id` columns as a
plain contiguous `Id[]` array wastes 7 bytes/entry to padding. This commit
adds — but does not yet wire in — a structure-of-arrays alternative: a
`uint64_t[]` payload array + `uint8_t[]` datatype array, addressed through
proxy types so existing call sites (`column[i].isUndefined()`, etc.) keep
compiling once this *is* wired in. Nothing existing includes these new files
yet, so this commit is risk-free in isolation.

**Design decisions from your review of this commit (three questions you
raised):**

1. **Byte-pack order in `packIdColumnToBytes`/`unpackBytesToIdColumn`**
   ([`src/engine/idTable/IdColumnByteIO.h`](src/engine/idTable/IdColumnByteIO.h)):
   you asked whether it wouldn't make more sense to pack the datatype byte
   *before* the payload, matching the legacy format's own datatype-major
   convention (and `ValueIdBitRepresentation`'s own field order,
   `uint8_t datatype_; uint64_t payload_;`) rather than following `Id`'s own
   incidental in-memory layout (which happens to be payload-first). Applied
   as asked — both functions now write/read datatype-then-payload.

2. **Should `IdColumnView.h` even exist as a separate file, and should the
   types be renamed?** You pointed out that once `IdColumnView`/
   `ConstIdColumnView` are the *real* type (not just an alias for `ql::span`),
   callers can just use the direct `columnBasedIdTable::` types, and asked
   whether the view types should instead be named `IdColumn`/`ConstIdColumn`
   and live directly in `IdColumn.h`, merging `IdColumnView.h` into it. Done:
   `IdColumnView.h` was deleted, its `BasicIdColumnView<IsConst>` template and
   the renamed `IdColumn`/`ConstIdColumn` aliases now live in
   [`src/engine/idTable/IdColumn.h`](src/engine/idTable/IdColumn.h). One
   subtlety this surfaced: in
   [`test/engine/idTable/IdColumnTest.cpp`](test/engine/idTable/IdColumnTest.cpp),
   at this commit's stage `columnBasedIdTable::IdColumn` and the
   still-`ql::span`-based *global* `IdColumn` (from Commit 1) are genuinely
   different types, so under `using namespace columnBasedIdTable;` a bare
   `IdColumn` would be ambiguous — the test fully qualifies
   `columnBasedIdTable::IdColumn`/`ConstIdColumn` there, with a comment
   explaining why.

3. **Why lambdas instead of `&Id::isUndefined`** (e.g. in
   `MultiColumnJoin.cpp`, `OptionalJoin.cpp`, `ExistsJoin.cpp`, `Minus.cpp`,
   `JoinAlgorithms.h`): pointer-to-member syntax (`std::invoke(&Id::isUndefined, x)`
   / `x.*pmf`) requires `x` to literally *be* an `Id` (or publicly derive from
   it) — not merely be implicitly convertible to one. The proxy reference
   type (`ConstIdRef`) that split-column storage hands out is a distinct
   class that only *converts* to `Id`, so it can't bind to a pointer-to-member
   at all. A lambda (`[](const Id& id) { return id.isUndefined(); }`) works
   because ordinary function calls do trigger implicit conversion. Confirmed
   there's no way around this while keeping the proxy design — kept as-is.

## [Commit 5 — `7f3c849c` Switch `IdColumn`/`ConstIdColumn` to real split-column storage](https://github.com/ad-freiburg/qlever/pull/3442/changes/aee4128c6d4ba0742e51af3b707b7d1540dca3bc)

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

**Pre-existing issue documented, not fixed here:** `IndexRebuilder.serverIntegration*`
crashes with a Bus error in Debug builds on macOS. Investigated thoroughly
(you pushed back on my first "unrelated" claim, correctly) — root-caused to
a genuine stack overflow in `boost::asio::thread_pool` worker threads
(macOS gives secondary pthreads a small default stack, and Debug builds have
much larger uninlined stack frames than Release). Verified via a full 2×2
matrix — crashes identically on Debug for both this branch and plain
`master`, passes on Release for both — confirmed pre-existing and
environment-specific, not a regression from this refactor. Documented in the
commit message rather than "fixed" with an unrelated workaround.

---

## Test status

Full build + full `ctest` suite green, except:

- 3× `IndexRebuilder.serverIntegration*` — pre-existing Debug/macOS stack
  overflow (see above), reproduces identically on `master`.
- `Timer.BasicWorkflow` / `ProgressBar.typicalUsage` — timing-threshold
  assertions that are flaky under CPU load (confirmed via retry: both pass in
  isolation); already called out as expected flakiness in Commit 5's own
  message.

No test failure in the final state is attributable to any change made on
this branch.
