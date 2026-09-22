# C++ Patterns Behind the Split-Column `IdColumn` Refactor

Companion to `64bit_id_refactor_summary.md`. That file walks through the 5
commits; this one collects the recurring C++ idioms those commits lean on —
mostly consequences of one root cause: **`column[i]` no longer returns a real
`Id&`/`const Id&`, it returns a proxy object (`IdRef`/`ConstIdRef`) that only
*converts* to `Id`.** Once you know that, almost every "why is this written
so oddly" question in the diff has the same answer. This is also the
material that used to be repeated, almost verbatim, at ~30 call sites in the
code — it now lives here once, and the in-code comments just point back to
it.

## Why a proxy at all?

`Id` used to be a single packed 64-bit word, so a column of `Id`s was a
plain, contiguous `Id[]`/`ql::span<Id>` — same as any other array. After this
refactor `Id` is 16 bytes (a full 64-bit payload word + a datatype byte +
padding), so a contiguous `Id[]` wastes 7 bytes per entry. `IdColumn` instead
stores the payload words and datatype bytes in two separate arrays (a
"structure of arrays", `IdColumnVector.h`), and `column[i]` returns
`IdRef`/`ConstIdRef` — a tiny struct of two pointers (`uint64_t*` +
`uint8_t*`) that mirrors `Id`'s read API and converts implicitly to/from
`Id`, so that `column[i].isUndefined()` keeps compiling. It's the same idea
as `std::vector<bool>::reference`.

```cpp
// BasicIdRef<IsConst>, engine/idTable/IdRef.h — simplified
template <bool IsConst>
class BasicIdRef {
  PayloadPointer payload_;
  DatatypePointer datatype_;
 public:
  /*implicit*/ operator Id() const { return Id::fromBits({*datatype_, *payload_}); }
  bool isUndefined() const { return static_cast<Id>(*this).isUndefined(); }
  // ... mirrors the rest of Id's read API the same way
};
```

Everything below is a consequence of that one design choice.

---

## 1. Pointer-to-member can't target a proxy

`&Id::isUndefined` used as a callable (`ranges::any_of(column, {}, &Id::isUndefined)`)
relies on `std::invoke`/`obj.*pmf` semantics, which require `obj` to *literally be*
an `Id` (or publicly derive from it) — not just be implicitly convertible to one.
`ConstIdRef` only converts to `Id`, so it fails to bind.

```cpp
// Before (column was ql::span<const Id>):
ranges::any_of(column, {}, &Id::isUndefined);

// After (column is ConstIdColumn, elements are ConstIdRef):
ranges::any_of(column, [](const Id& id) { return id.isUndefined(); });
```

The lambda works because an ordinary function call *does* trigger `ConstIdRef`'s
implicit conversion operator; pointer-to-member dispatch doesn't.

Recurs (as a one-line comment, `// Lambda, not &Id::X: proxy elements don't
support pointer-to-member (see IdColumn.h).`) in `MultiColumnJoin.cpp`,
`OptionalJoin.cpp`, `ExistsJoin.cpp`, `Minus.cpp`, `JoinAlgorithms.h` (×2),
`NamedResultCacheSerializer.h`, `ExternalSortFunctors.h`,
`OptionalJoinTest.cpp`.

## 2. `decltype(auto)` instead of a hardcoded `T&`/`const T&`

Generic code that used to declare `auto&` or a fixed `const T&` return type
now has to work for both: a real reference (any non-`Id` column) and a
proxy returned *by value* (an `Id` column). `decltype(auto)` preserves
whichever the underlying expression actually produced.

```cpp
// Before:
T& operator[](size_t i) const { return (*table_)(i, col_); }

// After — correct for both real references and by-value proxies:
decltype(auto) operator[](size_t i) const { return (*table_)(i, col_); }
```

Used in `IdTableRow.h` (`operator[]`, and the row iterator's `ValueType`),
`GroupByImpl.cpp`, `JoinColumnMapping.h`'s `col()[idx]` accessor.

## 3. Explicit `std::array<Id, N>{...}` instead of relying on CTAD

`std::array{a, b, c}` deduces its element type from its arguments. If `a`,
`b`, `c` are `ConstIdRef` (e.g. `row[0]`, `row[1]`, `row[2]`), CTAD deduces
`std::array<ConstIdRef, 3>`, not the intended `std::array<Id, 3>`.

```cpp
// Before (row[i] used to be Id/const Id&):
auto key = std::array{row[0], row[1], row[2]};

// After — CTAD would otherwise deduce std::array<ConstIdRef, 3>:
std::array<Id, 3> key{row[0], row[1], row[2]};
```

Used in `IndexImpl.cpp` (×2), `JoinColumnMapping.h::GetColsFromTable`,
`CompressedRelationHelpersImpl.h`, `CompressedRelationPermutationWriterImpl.h`.

## 4. `std::tie` can't bind a proxy's prvalue either

Same root cause as #3, different idiom: `std::tie(a, b)` builds a tuple of
`T&`s, which requires actual lvalues to bind to. A `ConstIdRef` returned by
value from `operator[]` is a prvalue, so it can't bind to `std::tie`'s
reference parameters. Materializing into a small `std::array<Id, N>` (then
comparing/hashing that) sidesteps the issue and is compared by value anyway.

```cpp
// Before:
return std::tie(a[c1Idx], a[c2Idx], a[c3Idx]) < std::tie(b[c1Idx], ...);

// After:
std::array<Id, 3> lhs{a[c1Idx], a[c2Idx], a[c3Idx]};
std::array<Id, 3> rhs{b[c1Idx], b[c2Idx], b[c3Idx]};
return lhs < rhs;
```

Used in `CompressedRelationHelpersImpl.h`, `CompressedRelationPermutationWriterImpl.h`,
`LocatedTriples.cpp` (×2).

## 5. Materializing `std::vector<Id>` instead of returning a view

Some functions used to return `ql::span<const Id>` — a "slice into an
existing, contiguous column". That assumes the column *is* contiguous, which
is no longer true for `IdColumn`. Where the caller only needs to read the
values once (not alias them long-term), the fix is to return an owned
`std::vector<Id>` instead — a deliberate, small, bounded copy, not a design
regression.

```cpp
// Before:
ql::span<const Id> graphsOf(...) const { return column.subspan(...); }

// After — column is no longer one contiguous range of Id:
std::vector<Id> graphsOf(...) const {
  return {column.begin() + from, column.begin() + to};
}
```

Used in `EmptyPath.cpp::graphsOf`, `PathSearch.h` (sources/targets),
`SparqlExpressionGenerators.h::getIdsFromVariable` — the single entry point
through which the (heavily templated, `contiguous_range`-assuming) SPARQL
expression evaluation machinery reads a column; making that generic code
proxy-aware was out of scope for this refactor.

## 6. `-> Id`, not `-> const Id&`

Returning `const Id&` from a function whose actual result comes from a
`ConstIdRef` conversion binds the reference to a *temporary* `Id` that dies
when the function returns — a dangling reference, only sometimes caught by
`-Wdangling-reference` in practice. Once a column can't hand out a real
`const Id&` to begin with, the honest return type is `Id` (a value).

```cpp
// Before (rowOrId[0] used to be a real Id):
const Id& firstId(const RowOrId& rowOrId) { return rowOrId[0]; }

// After — rowOrId[0] is ConstIdRef, and there is no persistent Id to
// reference anymore:
Id firstId(const RowOrId& rowOrId) { return rowOrId[0]; }
```

Used in `IndexImpl.cpp`, `JoinColumnMapping.h`'s `col()[idx]`/`.front()`/`.back()`.

## 7. Why `BasicIdRef` needs its own `operator=(const BasicIdRef&)`

The trickiest one, and worth a slightly longer example. `IdRef` defines
`operator=(Id)` with write-through semantics (writes to the referenced slot,
not to the proxy's own two pointers). That alone is *not* enough:

```cpp
IdRef a = column[0];
IdRef b = column[1];
a = b;  // which operator= is picked here?
```

Without an explicit `operator=(const BasicIdRef&)`, the compiler generates
its own copy-assignment operator for `IdRef`. Overload resolution always
prefers an identity match over a user-defined conversion, so `a = b` would
resolve to the *compiler-generated* one — which just copies `b`'s two
pointers into `a`, rebinding `a` to point at `b`'s slot instead of writing
`b`'s value into `a`'s slot. That silently corrupts data instead of copying
it, and is exactly what algorithms like `ranges::sort` rely on internally
when swapping two elements.

```cpp
// Required, not redundant with operator=(Id):
BasicIdRef& operator=(const BasicIdRef& other) {
  *this = static_cast<Id>(other);  // routes through the write-through operator=(Id)
  return *this;
}
```

This is also why `operator=` is declared `const` on the proxy itself (it
mutates the *referenced* slot, not the proxy's own state) — the same
requirement `std::vector<bool>::reference` and `std::indirectly_writable`
impose on any proxy reference type.

## 8. `enable_borrowed_range` / `enable_view` for a span-like custom type

`ql::span` gets two `std::ranges` opt-ins "for free" that a hand-written
span-like type has to declare explicitly:

- **`enable_borrowed_range`**: without it, a *temporary* view (e.g. the
  result of `column.subspan(...)`) passed straight into an algorithm like
  `ranges::equal_range` comes back as `ranges::dangling` instead of a real
  iterator, because the algorithm assumes a temporary range's iterators die
  with it. `BasicIdColumnView` doesn't own anything — it's a pair of
  pointers into someone else's arrays — so its iterators outlive it fine.
- **`enable_view`**: without it, `ranges::views::all`/`::zip` etc. wrap a
  passed-in lvalue in a `ranges::ref_view` (a reference back to that
  specific lvalue) instead of copying the lightweight view directly — which
  dangles the moment that particular lvalue goes out of scope (e.g. a
  locally-constructed `subspan()` result).

```cpp
template <bool IsConst>
inline constexpr bool std::ranges::enable_borrowed_range<
    columnBasedIdTable::BasicIdColumnView<IsConst>> = true;
template <bool IsConst>
inline constexpr bool std::ranges::enable_view<
    columnBasedIdTable::BasicIdColumnView<IsConst>> = true;
```

## 9. Legacy on-disk format via "shadow structs"

`qlever-upgrade-index` has to read an old on-disk format whose `Id` was a
single packed word (4-bit datatype + 60-bit payload), while the rest of the
codebase now assumes the new layout. Rather than special-casing the
converter's parsing logic, `IndexFormatConverter.cpp` defines shadow structs
(`LegacyId`, `LegacyPermutedTriple`, `LegacyCompressedBlockMetadata`) that
mirror the real types field-for-field, so the existing generic
`ad_utility::serialization` framework parses old bytes correctly just by
instantiating it for the shadow type instead:

```cpp
struct LegacyId {
  uint64_t bits_;  // datatype in the top 4 bits, payload in the low 60
  Id convert() const { ... }  // the one place that knows the old encoding
};
// LegacyPermutedTriple mirrors CompressedBlockMetadata::PermutedTriple,
// field for field, with LegacyId in place of Id — same (de)serialization
// code, just instantiated for the shadow type.
```

`LegacyId::convert()` is the single place that knows the three
datatype-specific re-encodings: most datatypes just zero-extend into the
wider payload, but `Double`/`Int`/`EncodedVal` (see
`IndexFormatConverter.cpp`) each need actual re-encoding because the *old*
format packed their value differently depending on the payload width.

## 10. Sink-by-value vs. const-reference parameter

Covered in detail in `64bit_id_refactor_summary.md` (Commit 3), noted here
for completeness: `legacyScanAndConvert` took `LegacyPermutationSummary`
by value under the assumption that the returned lazy range needed to own
it. Since the per-block transform only ever reads a block (never moves from
it), and both callers need `summary` again afterward anyway, a
`const LegacyPermutationSummary&` avoids a needless copy of a potentially
large `blocks_` vector — safe because both callers consume the returned
range synchronously before touching `summary` again.

## 11. The `sizeof(Id)` → `BYTES_PER_ID_COLUMN_ENTRY` divisor trap

Anywhere a block size in bytes gets converted to a row count by dividing by
"the size of one `Id`", that divisor has to change from `sizeof(Id) == 16`
(a materialized, padded `Id`) to `BYTES_PER_ID_COLUMN_ENTRY == 9` (1
datatype byte + 8 payload bytes, the packed on-disk/on-wire size with no
padding) — otherwise block boundaries silently shift and every test that
hardcodes "N rows per block" as a byte count breaks.

```cpp
// Before:
size_t rowsPerBlock = blockSizeInBytes / sizeof(Id);        // assumes 16
// After:
size_t rowsPerBlock = blockSizeInBytes / BYTES_PER_ID_COLUMN_ENTRY;  // 9
```

Fixed in `CompressedExternalIdTable.h` (4 sites), `CompressedRelation.h`,
and correspondingly re-tuned in every test that hardcodes a block size in
bytes (`IndexTestHelpers.h`'s central default, `CompressedRelationsTest.cpp`,
`IndexFormatConverterTest.cpp`, `IndexScanTest.cpp`).

## 12. Open question: `SecondaryVocabIndex`'s 60-bit cap is test-driven, not requirement-driven

Flagged for discussion with the advisor, not yet changed.

Before this refactor, *every* index type (`VocabIndex`, `LocalVocabIndex`,
`SecondaryVocabIndex`, `TextRecordIndex`, ...) was capped at 60 usable bits,
simply because the payload only had 60 bits to begin with (4 bits went to
the datatype tag in the same 64-bit word). Since the datatype now lives in
its own separate byte, the payload is a full 64-bit word for everyone, and
`maxIndex = std::numeric_limits<T>::max()` — none of these types can
overflow anymore, so their `IndexTooLargeException` paths became dead code
and were dropped.

`SecondaryVocabIndex` is the one exception (`global/ValueId.h`):

```cpp
// `SecondaryVocabIndex` is deliberately kept at the pre-refactor limit of
// 60 usable bits (rather than the full 64-bit `maxIndex` above): unlike the
// other index types, its `IndexTooLargeException` is still exercised by
// `SecondaryVocabularyTest`, so a real, enforced bound is kept for it.
static constexpr T maxSecondaryVocabIndex = (1ull << 60) - 1;
```

The commit that introduced this (`3305c8c9`) says the same thing directly:

> The one exception is SecondaryVocabIndex: unlike the other index types, it
> keeps a real, enforced bound (...) because a dedicated test
> (SecondaryVocabularyTest) exercises that boundary.

In other words: the cap isn't there because anything downstream actually
needs `SecondaryVocabIndex` to fit in 60 bits. I checked for a structural
reason (e.g. whether it's packed together with other data somewhere, the
way `GeoCellGrid.h`'s `SplitVocabulary` marker bits are) and found none —
`SplitVocabulary`/`GeoCellGrid` is a completely unrelated mechanism. The
*only* place `maxSecondaryVocabIndex` is referenced outside `ValueId.h` is
one assertion in `test/SecondaryVocabularyTest.cpp`:

```cpp
EXPECT_THROW(Id::makeFromSecondaryVocabIndex(
                 SecondaryVocabIndex::make(Id::maxSecondaryVocabIndex + 1)),
             Id::IndexTooLargeException);
```

So the dependency runs backwards from the usual direction: normally a test
exists to verify a real constraint of the code; here, a constraint
(`maxSecondaryVocabIndex`, `IndexTooLargeException`, the bounds check in
`makeFromSecondaryVocabIndex`) was *kept in production code* purely so that
one pre-existing test assertion would keep being meaningful, rather than
updating/removing that assertion the way the analogous checks for every
other index type apparently were.

**Options to raise with the advisor:**
- Keep it as is (maybe there's a forward-looking reason not visible yet,
  e.g. reserving headroom for a future use of those top bits).
- Drop `maxSecondaryVocabIndex`/`IndexTooLargeException` for
  `SecondaryVocabIndex` too, for consistency with every other index type,
  and adapt/remove the one test assertion that currently exercises it.

## 13. Open question: could the `GeoCellGrid` marker bit move into the datatype byte instead of the payload?

Flagged for discussion with the advisor, not yet changed.

`SplitVocabulary` (the mechanism that lets a `VocabIndex` point into either
the regular vocabulary or a geo sub-vocabulary of WKT literals) currently
distinguishes the two by reserving the *top bit of the payload* as a marker
(`geoVocabMarkerBit`, `GeoCellGrid.h:188`). Since that marker bit sits right
next to the cell/position bits inside the same 64-bit payload word, one more
bit of headroom has to be reserved below it too, to keep the exclusive-upper-
bound arithmetic in `vocabIndexRangeForCells` from carrying into it (see
section 12's counterpart discussion, and the code walkthrough given earlier
in this session) — 2 bits of the payload spent on bookkeeping rather than
actual vocabulary addressing.

`Datatype` (`global/ValueId.h:33-54`) only has 13 values (`MaxValue =
EncodedVal`, i.e. values 0–12), so 4 of the datatype byte's 8 bits are
currently unused. The question raised: instead of a marker *bit inside the
payload*, why not a dedicated `Datatype::GeoVocabIndex` value (placed
directly adjacent to `VocabIndex` in the enum, the same way
`SecondaryVocabIndex`/`LocalVocabIndex` already have to be, per the ordering
comment right above `SecondaryVocabIndex`'s declaration) — freeing the
marker bit from the payload entirely, and getting the full 64 bits for pure
cell+position addressing?

This looks sound in principle:

- The marker bit disappears from the payload outright (encoded in the
  datatype byte instead), and `numPositionBits()` could drop the `- 2` back
  to just `- numCellBits()` (or `- 1` if the headroom concern below still
  needs one bit).
- Sort order is preservable the same way `SecondaryVocabIndex`'s position
  already is: by placing the new datatype value adjacently in the enum, not
  by the marker bit's position.

It doesn't fully eliminate the *headroom* concern, only the *marker* bit:
if `numCellBits() + numPositionBits()` used all 64 bits exactly, the
sentinel cell's exclusive-bound computation (`(last + 1) << numPositionBits()`)
would still overflow a `uint64_t` to 0 instead of a real one-past-the-end
value. That would need a different fix (special-case the sentinel, or widen
the bound computation) rather than the marker bit's old side effect of
leaving a safety margin — so the saving is likely 1 bit for certain, and a
2nd bit if that overflow case is handled another way.

**Why this wasn't done as part of this refactor:** `SplitVocabulary`/
`GeoCellGrid`'s marker-bit scheme predates this Id-layout change entirely;
this refactor only re-tuned its existing headroom math for the wider
payload, it didn't redesign the scheme. Introducing a new `Datatype` value
is a bigger change than it looks: every `switch`/`visit` over `Datatype`
(serialization, `isTypeBitwiseComparable_`, `datatypesOfPositionInVocab_`,
`stringTypes_`, comparison logic, ...) would need to account for it. That's
a deliberate design change to the `SplitVocabulary` layer, not something to
fold into this refactor incidentally.

**Options to raise with the advisor:**
- Keep the current marker-bit-in-payload scheme (simpler, already tested,
  entirely orthogonal to this refactor's actual goal).
- Move the marker into a new adjacent `Datatype` value as a follow-up
  cleanup, reclaiming 1-2 payload bits for the geo sub-vocabulary's
  addressable range.
