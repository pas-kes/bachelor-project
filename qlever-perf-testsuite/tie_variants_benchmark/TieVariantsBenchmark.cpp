// Copyright 2026 The QLever Authors, in particular:
//
// 2026 Pascal Keßler <kesslerp@informatik.uni-freiburg.de>, UFR
//
// UFR = University of Freiburg, Chair of Algorithms and Data Structures
//
// You may not use this file except in compliance with the Apache 2.0 License,
// which can be found in the `LICENSE` file at the root of the QLever project.

// Compare the candidates for `tieHelper` in `LocatedTriples.cpp`:
//
// - `std::tie`: the current code, only works if `row[I]` is an `Id&`.
// - A: `std::tie` if all `row[I]` are lvalues, else a tuple of `Id` copies.
// - B: a tuple that keeps references to lvalues and stores proxies by value.
// - array: `std::array<Id, N>` (the code of PR #3577).
// - helper: no tuple at all, a hand-written function that compares the three
//   entries one after the other with `<=>` (the proxy is converted to the `Id`
//   once per entry). This would need a change of the callers.
// - helper (bits): like helper, but without the check for `LocalVocabIndex`
//   (`compareWithoutLocalVocab`). Only a reference for how much that check
//   costs, it is not correct for `Id`s with a `LocalVocabIndex`.
//
// Each merge loop exists in two shapes: as in `mergeTriplesImpl` with two
// comparisons per row (`lessThan`, then `equal`), and with a single three-way
// comparison per row ("single compare"), which would need a change of the
// loop.
//
// The comparisons are done in the loop of
// `LocatedTriplesPerBlock::mergeTriplesImpl`: a sorted block of rows is merged
// with a sorted list of located triples. The data is random (fixed seed), the
// result of the comparison is data dependent, and the lambdas `lessThan` and
// `equal` are inside the function and can be inlined like there. Every variant
// has to compute the same result as `std::tie` on the same kind of rows.
//
// The rows come from four different sources, to cover today's code and the
// planned `Id` rework:
//
// 1. "legacy Id&": an `IdTable`, `row[I]` is a `const Id&`. This is the code
//    that runs today.
// 2. "bridge ConstIdRef": three `IdColumnVector`s of the repository (payloads
//    and datatypes in separate arrays), `row[I]` is the proxy `ConstIdRef`.
//    The `Id` is still the packed legacy `Id`, so the proxy has to convert
//    (`idFromBitsCompat`) to compare. This is the split storage as it exists
//    in the repository now.
// 3. "future Id&": the planned `SplitLayoutId` (datatype byte and full 64 bit
//    payload, 16 bytes) stored as plain objects in columns, `row[I]` is a
//    `const FutureId&`.
// 4. "future proxy": the same `IdColumnVector`s, but with the planned
//    `SplitLayoutId`: the proxy `row[I]` reads the two fields through
//    `ConstIdRef::getBits()` and compares them directly, without building a
//    packed `Id`.
//
// `SplitLayoutId` does not exist in the repository yet. `FutureId` is a
// stand-in in this file: it compares `SplitLayoutIdBitRepresentation` of the
// repository (datatype first, then payload) and has the same check for
// `LocalVocabIndex` as the legacy `Id::compareThreeWay` (the slow path is not
// inlined). The data has no `LocalVocabIndex`.
//
// Build it twice (with and without a change of `ValueId::compareThreeWay`) and
// run the binaries alternately to see the influence of the inlining.

#include <algorithm>
#include <array>
#include <compare>
#include <memory>
#include <random>
#include <string>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include "../benchmark/infrastructure/Benchmark.h"
#include "engine/idTable/IdTable.h"
#include "engine/idTable/splitLayout/IdColumnVector.h"
#include "engine/idTable/splitLayout/IdRef.h"
#include "engine/idTable/splitLayout/SplitLayoutIdBitRepresentation.h"
#include "global/Datatype.h"
#include "global/Id.h"
#include "util/AllocatorWithLimit.h"
#include "util/CompilerExtensions.h"
#include "util/Exception.h"
#include "util/Log.h"

namespace ad_benchmark {
namespace {
using columnBasedIdTable::splitLayout::ConstIdRef;
using columnBasedIdTable::splitLayout::getBitsCompat;
using columnBasedIdTable::splitLayout::IdColumnVector;
using columnBasedIdTable::splitLayout::SplitLayoutIdBitRepresentation;

// Number of rows that all measurements process in total, such that a
// measurement takes some tens of milliseconds, independent of the block size.
constexpr size_t totalRowsPerMeasurement = 20'000'000;

// ____________________________________________________________________________
// Stand-in for the planned `SplitLayoutId`, see the comment at the top.
struct FutureId {
  SplitLayoutIdBitRepresentation bits_;

  // The (rare) comparison with a `LocalVocabIndex`. Not inlined, like the
  // expensive part of `Id::compareThreeWay`.
  AD_NO_INLINE static std::strong_ordering compareWithLocalVocab(
      const FutureId& a, const FutureId& b) {
    return a.bits_ <=> b.bits_;
  }

  friend std::strong_ordering operator<=>(const FutureId& a,
                                          const FutureId& b) {
    constexpr auto localVocab = static_cast<uint8_t>(Datatype::LocalVocabIndex);
    if (a.bits_.datatype_ != localVocab && b.bits_.datatype_ != localVocab)
        [[likely]] {
      return a.bits_ <=> b.bits_;
    }
    return compareWithLocalVocab(a, b);
  }
  [[nodiscard]] std::strong_ordering compareWithoutLocalVocab(
      const FutureId& other) const {
    return bits_ <=> other.bits_;
  }
  // Like the legacy `Id`, `==` goes through the three-way comparison.
  friend bool operator==(const FutureId& a, const FutureId& b) {
    return (a <=> b) == 0;
  }
  [[nodiscard]] uint64_t sum() const {
    return bits_.payload_ ^ bits_.datatype_;
  }
};

// The proxy for the planned layout: a `ConstIdRef` of the repository, which
// reads the datatype byte and the payload word from the two arrays, but
// converts to the cheap `FutureId` instead of the packed legacy `Id`.
struct FutureRef {
  ConstIdRef ref_;
  /*implicit*/ operator FutureId() const { return FutureId{ref_.getBits()}; }
  friend std::strong_ordering operator<=>(const FutureRef& a,
                                          const FutureId& b) {
    return FutureId{a.ref_.getBits()} <=> b;
  }
  friend std::strong_ordering operator<=>(const FutureRef& a,
                                          const FutureRef& b) {
    return FutureId{a.ref_.getBits()} <=> FutureId{b.ref_.getBits()};
  }
  friend bool operator==(const FutureRef& a, const FutureId& b) {
    return FutureId{a.ref_.getBits()} == b;
  }
  friend bool operator==(const FutureRef& a, const FutureRef& b) {
    return FutureId{a.ref_.getBits()} == FutureId{b.ref_.getBits()};
  }
};

uint64_t sumOf(const Id& id) { return id.getBits(); }
uint64_t sumOf(const FutureId& id) { return id.sum(); }

// ____________________________________________________________________________
template <typename IdT>
struct LocatedTripleStandIn {
  std::array<IdT, 3> ids_;
  bool insertOrDelete_;
};

using Allocator = decltype(ad_utility::makeUnlimitedAllocator<Id>());
using SplitColumn = IdColumnVector<Allocator>;

struct Block {
  // Source 1: the legacy `IdTable`.
  IdTable table{3, ad_utility::makeUnlimitedAllocator<Id>()};
  // Sources 2 and 4: the split columns of the repository.
  std::array<SplitColumn, 3> splitColumns{
      SplitColumn{ad_utility::makeUnlimitedAllocator<Id>()},
      SplitColumn{ad_utility::makeUnlimitedAllocator<Id>()},
      SplitColumn{ad_utility::makeUnlimitedAllocator<Id>()}};
  // Source 3: plain `FutureId` objects in columns.
  std::array<std::vector<FutureId>, 3> futureColumns;
  std::vector<LocatedTripleStandIn<Id>> located;
  std::vector<LocatedTripleStandIn<FutureId>> locatedFuture;
};

// A sorted block with about `targetRows` distinct rows `(c0, c1, c2)`: `c0` has
// 64 values (like predicates), `c1` has `targetRows / 16` values and `c2` has
// 4096. About one percent as many located triples: half of them are copies of
// existing rows (mostly deletions), half are new triples (insertions).
Block makeBlock(size_t targetRows) {
  using Raw = std::array<int64_t, 3>;
  std::mt19937_64 gen{42};
  const auto maxC1 = static_cast<int64_t>(targetRows / 16);
  std::uniform_int_distribution<int64_t> d0{0, 63}, d1{0, maxC1}, d2{0, 4095};
  auto randomRaw = [&] { return Raw{d0(gen), d1(gen), d2(gen)}; };
  auto makeId = [](int64_t value) { return Id::makeFromInt(value); };
  auto makeFuture = [&](int64_t value) {
    return FutureId{getBitsCompat(makeId(value))};
  };

  std::vector<Raw> rows;
  rows.reserve(targetRows);
  for (size_t i = 0; i < targetRows; ++i) {
    rows.push_back(randomRaw());
  }
  std::sort(rows.begin(), rows.end());
  rows.erase(std::unique(rows.begin(), rows.end()), rows.end());

  Block block;
  block.table.resize(rows.size());
  for (size_t column = 0; column < 3; ++column) {
    block.splitColumns[column].reserve(rows.size());
    block.futureColumns[column].reserve(rows.size());
    for (size_t i = 0; i < rows.size(); ++i) {
      block.table(i, column) = makeId(rows[i][column]);
      block.splitColumns[column].push_back(makeId(rows[i][column]));
      block.futureColumns[column].push_back(makeFuture(rows[i][column]));
    }
  }

  std::vector<std::pair<Raw, bool>> located;
  for (size_t j = 0; j < rows.size() / 100; ++j) {
    const bool existing = gen() % 2 == 0;
    Raw raw = existing ? rows[gen() % rows.size()] : randomRaw();
    const bool insert = existing ? gen() % 5 == 0 : true;
    located.emplace_back(raw, insert);
  }
  std::sort(located.begin(), located.end());
  for (const auto& [raw, insert] : located) {
    block.located.push_back(
        {{makeId(raw[0]), makeId(raw[1]), makeId(raw[2])}, insert});
    block.locatedFuture.push_back(
        {{makeFuture(raw[0]), makeFuture(raw[1]), makeFuture(raw[2])}, insert});
  }
  return block;
}

// ____________________________________________________________________________
// The row sources. `row(block, i)` returns an object whose `operator[]` returns
// the entry of column `c`, like `RowReference` of the `IdTable` does.
struct LegacyRows {
  using IdT = Id;
  static constexpr bool hasPlainTie = true;
  static auto row(const Block& block, size_t i) { return block.table[i]; }
  static const auto& located(const Block& block) { return block.located; }
};

struct BridgeRow {
  const Block* block_;
  size_t row_;
  ConstIdRef operator[](size_t column) const {
    return block_->splitColumns[column][row_];
  }
};
struct BridgeRows {
  using IdT = Id;
  static constexpr bool hasPlainTie = false;
  static auto row(const Block& block, size_t i) { return BridgeRow{&block, i}; }
  static const auto& located(const Block& block) { return block.located; }
};

struct FutureLvalueRow {
  const Block* block_;
  size_t row_;
  const FutureId& operator[](size_t column) const {
    return block_->futureColumns[column][row_];
  }
};
struct FutureLvalueRows {
  using IdT = FutureId;
  static constexpr bool hasPlainTie = true;
  static auto row(const Block& block, size_t i) {
    return FutureLvalueRow{&block, i};
  }
  static const auto& located(const Block& block) { return block.locatedFuture; }
};

struct FutureProxyRow {
  const Block* block_;
  size_t row_;
  FutureRef operator[](size_t column) const {
    return FutureRef{block_->splitColumns[column][row_]};
  }
};
struct FutureProxyRows {
  using IdT = FutureId;
  static constexpr bool hasPlainTie = false;
  static auto row(const Block& block, size_t i) {
    return FutureProxyRow{&block, i};
  }
  static const auto& located(const Block& block) { return block.locatedFuture; }
};

// ____________________________________________________________________________
// The candidates, in the form in which they would be used in `tieHelper`.
template <typename Row, size_t... I>
auto tieCurrent(Row& row, std::index_sequence<I...>) {
  return std::tie(row[I]...);
}

template <typename IdT, typename Row, size_t... I>
auto tieA(Row& row, std::index_sequence<I...>) {
  if constexpr ((std::is_lvalue_reference_v<decltype(row[I])> && ...)) {
    return std::tie(row[I]...);
  } else {
    return std::make_tuple(static_cast<IdT>(row[I])...);
  }
}

// Exactly the code of `tieHelper` in `LocatedTriples.cpp` (variant B): lvalues
// stay references (as in `std::tie`), a proxy that is returned by value is
// stored by value.
template <typename T>
using TieElement =
    std::conditional_t<std::is_lvalue_reference_v<T>, T, std::decay_t<T>>;

template <typename Row, size_t... I>
auto tieB(Row& row, std::index_sequence<I...>) {
  return std::tuple<TieElement<decltype(row[I])>...>(row[I]...);
}

// The same without the alias. If this has the same type as `tieB` for all
// kinds of rows in this benchmark, `TieElement` is not needed (a call that
// returns a reference has the type of a reference in `decltype`, and a call
// that returns a value has the type of the value).
template <typename Row, size_t... I>
auto tieBWithoutAlias(Row& row, std::index_sequence<I...>) {
  return std::tuple<decltype(row[I])...>(row[I]...);
}

template <typename IdT, typename Row, size_t... I>
auto arrayOf(Row& row, std::index_sequence<I...>) {
  return std::array<IdT, sizeof...(I)>{row[I]...};
}

// Compile-time check that the alias in `tieB` changes nothing for the rows of
// this benchmark.
template <typename Source>
constexpr bool aliasIsRedundant() {
  using Row = decltype(Source::row(std::declval<const Block&>(), 0));
  constexpr auto seq = std::make_index_sequence<3>{};
  return std::is_same_v<decltype(tieB(std::declval<Row&>(), seq)),
                        decltype(tieBWithoutAlias(std::declval<Row&>(), seq))>;
}
static_assert(aliasIsRedundant<LegacyRows>());
static_assert(aliasIsRedundant<BridgeRows>());
static_assert(aliasIsRedundant<FutureLvalueRows>());
static_assert(aliasIsRedundant<FutureProxyRows>());

enum class Variant { Current, A, B, Array, Helper, HelperBits };

template <Variant variant, typename IdT, typename Row>
auto tieRow(Row& row) {
  constexpr auto seq = std::make_index_sequence<3>{};
  if constexpr (variant == Variant::Current) {
    return tieCurrent(row, seq);
  } else if constexpr (variant == Variant::A) {
    return tieA<IdT>(row, seq);
  } else if constexpr (variant == Variant::B) {
    return tieB(row, seq);
  } else {
    return arrayOf<IdT>(row, seq);
  }
}

// The side of the located triple: the array variant compares array with array,
// all others compare a tuple of references with the row.
template <Variant variant, typename IdT>
auto tieTriple(const std::array<IdT, 3>& triple) {
  if constexpr (variant == Variant::Array) {
    return triple;
  } else {
    return std::tie(triple[0], triple[1], triple[2]);
  }
}

// The hand-written comparison of the entries one after the other.
template <bool ignoreLocalVocab, typename IdT, typename Row>
AD_ALWAYS_INLINE std::strong_ordering threeWay(const std::array<IdT, 3>& t,
                                               const Row& row) {
  auto compareOne = [](const IdT& a, const IdT& b) {
    if constexpr (ignoreLocalVocab) {
      return a.compareWithoutLocalVocab(b);
    } else {
      return a <=> b;
    }
  };
  if (auto c = compareOne(t[0], static_cast<IdT>(row[0])); c != 0) {
    return c;
  }
  if (auto c = compareOne(t[1], static_cast<IdT>(row[1])); c != 0) {
    return c;
  }
  return compareOne(t[2], static_cast<IdT>(row[2]));
}

// What a merge computes. All variants have to agree on it.
struct MergeResult {
  size_t copied_ = 0;
  size_t inserted_ = 0;
  size_t matched_ = 0;
  uint64_t checksum_ = 0;
  bool operator==(const MergeResult&) const = default;
  MergeResult& operator+=(const MergeResult& other) {
    copied_ += other.copied_;
    inserted_ += other.inserted_;
    matched_ += other.matched_;
    checksum_ += other.checksum_;
    return *this;
  }
};

// NOTE: In `mergeTriplesImpl` these are lambdas that are inlined into the loop
// (checked in the object file). The member functions are forced to be inlined
// as well, otherwise the compilers may keep them as separate functions.
template <Variant variant, typename IdT>
struct Comparators {
  static constexpr bool isHelper =
      variant == Variant::Helper || variant == Variant::HelperBits;
  static constexpr bool ignoreLocalVocab = variant == Variant::HelperBits;

  AD_ALWAYS_INLINE auto compare(const LocatedTripleStandIn<IdT>& lt,
                                const auto& row) const {
    if constexpr (isHelper) {
      return threeWay<ignoreLocalVocab, IdT>(lt.ids_, row);
    } else {
      auto rhs = tieRow<variant, IdT>(row);
      return tieTriple<variant, IdT>(lt.ids_) <=> rhs;
    }
  }
  AD_ALWAYS_INLINE bool lessThan(const LocatedTripleStandIn<IdT>& lt,
                                 const auto& row) const {
    if constexpr (isHelper) {
      return threeWay<ignoreLocalVocab, IdT>(lt.ids_, row) < 0;
    } else {
      auto rhs = tieRow<variant, IdT>(row);
      return tieTriple<variant, IdT>(lt.ids_) < rhs;
    }
  }
  AD_ALWAYS_INLINE bool equal(const LocatedTripleStandIn<IdT>& lt,
                              const auto& row) const {
    if constexpr (isHelper) {
      return threeWay<ignoreLocalVocab, IdT>(lt.ids_, row) == 0;
    } else {
      auto rhs = tieRow<variant, IdT>(row);
      return tieTriple<variant, IdT>(lt.ids_) == rhs;
    }
  }
};

// The loop of `mergeTriplesImpl`, but it only counts and sums up instead of
// writing the result.
template <Variant variant, typename Source>
AD_NO_INLINE MergeResult mergeComparisonOnly(const Block& block,
                                             size_t numRows) {
  using IdT = typename Source::IdT;
  constexpr Comparators<variant, IdT> cmp;
  const auto& located = Source::located(block);
  MergeResult result;
  size_t rowIt = 0;
  size_t ltIt = 0;
  const size_t numLocated = located.size();
  while (rowIt < numRows && ltIt < numLocated) {
    const auto row = Source::row(block, rowIt);
    const auto& lt = located[ltIt];
    if (cmp.lessThan(lt, row)) {
      if (lt.insertOrDelete_) {
        ++result.inserted_;
        result.checksum_ += sumOf(lt.ids_[2]);
      }
      ++ltIt;
    } else if (cmp.equal(lt, row)) {
      if (!lt.insertOrDelete_) {
        ++rowIt;
      }
      ++result.matched_;
      ++ltIt;
    } else {
      ++result.copied_;
      result.checksum_ += sumOf(static_cast<IdT>(row[2]));
      ++rowIt;
    }
  }
  result.copied_ += numRows - rowIt;
  return result;
}

// The same loop with a single three-way comparison per row.
template <Variant variant, typename Source>
AD_NO_INLINE MergeResult mergeSingleCompare(const Block& block,
                                            size_t numRows) {
  using IdT = typename Source::IdT;
  constexpr Comparators<variant, IdT> cmp;
  const auto& located = Source::located(block);
  MergeResult result;
  size_t rowIt = 0;
  size_t ltIt = 0;
  const size_t numLocated = located.size();
  while (rowIt < numRows && ltIt < numLocated) {
    const auto row = Source::row(block, rowIt);
    const auto& lt = located[ltIt];
    const auto order = cmp.compare(lt, row);
    if (order < 0) {
      if (lt.insertOrDelete_) {
        ++result.inserted_;
        result.checksum_ += sumOf(lt.ids_[2]);
      }
      ++ltIt;
    } else if (order == 0) {
      if (!lt.insertOrDelete_) {
        ++rowIt;
      }
      ++result.matched_;
      ++ltIt;
    } else {
      ++result.copied_;
      result.checksum_ += sumOf(static_cast<IdT>(row[2]));
      ++rowIt;
    }
  }
  result.copied_ += numRows - rowIt;
  return result;
}

// Like `mergeComparisonOnly`, but with the allocation of the result and the
// copying of the rows, as in `mergeTriplesImpl`. Only for the `IdTable`.
template <Variant variant>
AD_NO_INLINE MergeResult mergeFull(const Block& block) {
  constexpr Comparators<variant, Id> cmp;
  const auto& table = block.table;
  IdTable result{table.numColumns(), table.getAllocator()};
  result.resize(table.numRows() + block.located.size());

  MergeResult summary;
  auto rowIt = table.begin();
  auto resultIt = result.begin();
  size_t ltIt = 0;
  const size_t numLocated = block.located.size();
  while (rowIt != table.end() && ltIt < numLocated) {
    const auto& lt = block.located[ltIt];
    if (cmp.lessThan(lt, *rowIt)) {
      if (lt.insertOrDelete_) {
        for (size_t i = 0; i < 3; ++i) {
          (*resultIt)[i] = lt.ids_[i];
        }
        ++resultIt;
        ++summary.inserted_;
      }
      ++ltIt;
    } else if (cmp.equal(lt, *rowIt)) {
      if (!lt.insertOrDelete_) {
        ++rowIt;
      }
      ++summary.matched_;
      ++ltIt;
    } else {
      *resultIt++ = *rowIt++;
      ++summary.copied_;
    }
  }
  while (rowIt != table.end()) {
    *resultIt++ = *rowIt++;
    ++summary.copied_;
  }
  // Make sure that the result is used.
  const size_t used = summary.copied_ + summary.inserted_;
  if (used > 0) {
    summary.checksum_ = result(0, 2).getBits() + result(used - 1, 2).getBits();
  }
  return summary;
}

template <Variant v>
using VariantTag = std::integral_constant<Variant, v>;

class TieVariantsBenchmark : public BenchmarkInterface {
 public:
  [[nodiscard]] std::string name() const final;

  BenchmarkResults runAllBenchmarks() final {
    BenchmarkResults results{};
    for (size_t targetRows : {100'000UL, 3'000'000UL}) {
      addBlockMeasurements(results, targetRows);
    }
    return results;
  }

 private:
  // All measurements for a block with about `targetRows` rows.
  static void addBlockMeasurements(BenchmarkResults& results,
                                   size_t targetRows) {
    // The block is kept alive for the measurements.
    auto block = std::make_shared<Block>(makeBlock(targetRows));
    const size_t numRows = block->table.numRows();
    const size_t repeats =
        std::max<size_t>(1, totalRowsPerMeasurement / numRows);
    const std::string size = "(" + std::to_string(numRows) + " rows) ";

    // Every variant must give the same result as `std::tie` on the same kind
    // of rows.
    const MergeResult reference =
        mergeComparisonOnly<Variant::Current, LegacyRows>(*block, numRows);
    const MergeResult referenceFuture =
        mergeComparisonOnly<Variant::Current, FutureLvalueRows>(*block,
                                                                numRows);
    const MergeResult referenceFull = mergeFull<Variant::Current>(*block);
    AD_LOG_INFO << "Block with " << numRows << " rows and "
                << block->located.size() << " located triples: copied "
                << reference.copied_ << ", inserted " << reference.inserted_
                << ", matched " << reference.matched_ << std::endl;
    AD_CORRECTNESS_CHECK(reference.matched_ > 0 && reference.inserted_ > 0);
    AD_CORRECTNESS_CHECK(reference.copied_ == referenceFuture.copied_ &&
                         reference.inserted_ == referenceFuture.inserted_ &&
                         reference.matched_ == referenceFuture.matched_);

    // Two groups (two-comparison loop, single-compare loop) for each source.
    struct Groups {
      decltype(results.addGroup("")) two;
      decltype(results.addGroup("")) single;
    };
    auto makeGroups = [&](const std::string& source) {
      return Groups{results.addGroup(size + source + ", two comparisons"),
                    results.addGroup(size + source + ", single compare")};
    };
    auto legacyGroups = makeGroups("1 legacy Id&");
    auto& fullGroup = results.addGroup(size + "1 legacy Id&, full merge");
    auto bridgeGroups = makeGroups("2 bridge ConstIdRef");
    auto futureGroups = makeGroups("3 future Id&");
    auto futureProxyGroups = makeGroups("4 future proxy");

    auto add = [&](auto& group, const std::string& name,
                   const MergeResult& expected, auto function) {
      AD_CORRECTNESS_CHECK(function() == expected);
      group.addMeasurement(name, [function, repeats] {
        MergeResult sum;
        for (size_t i = 0; i < repeats; ++i) {
          sum += function();
        }
        AD_LOG_INFO << "Checksum " << sum.checksum_ << std::endl;
      });
    };
    // Add the measurements of one variant for one source (both loops).
    auto addSource = [&](Groups& groups, const std::string& name,
                         const MergeResult& expected, auto sourceTag,
                         auto variantTag) {
      using Source = typename decltype(sourceTag)::type;
      constexpr Variant variant = decltype(variantTag)::value;
      add(groups.two, name, expected, [block, numRows] {
        return mergeComparisonOnly<variant, Source>(*block, numRows);
      });
      add(groups.single, name, expected, [block, numRows] {
        return mergeSingleCompare<variant, Source>(*block, numRows);
      });
    };
    auto addVariant = [&](auto variantTag, const std::string& name) {
      constexpr Variant variant = decltype(variantTag)::value;
      // `std::tie` does not compile for proxy rows.
      constexpr bool onlyLvalues = variant == Variant::Current;
      addSource(legacyGroups, name, reference, std::type_identity<LegacyRows>{},
                variantTag);
      addSource(futureGroups, name, referenceFuture,
                std::type_identity<FutureLvalueRows>{}, variantTag);
      if constexpr (variant != Variant::HelperBits) {
        // The reference variant without the check is not part of the full
        // merge.
        add(fullGroup, name, referenceFull,
            [block] { return mergeFull<variant>(*block); });
      }
      if constexpr (!onlyLvalues) {
        addSource(bridgeGroups, name, reference,
                  std::type_identity<BridgeRows>{}, variantTag);
        addSource(futureProxyGroups, name, referenceFuture,
                  std::type_identity<FutureProxyRows>{}, variantTag);
      }
    };
    addVariant(VariantTag<Variant::Current>{}, "std::tie (current)");
    addVariant(VariantTag<Variant::A>{}, "A (tie, else tuple of Ids)");
    addVariant(VariantTag<Variant::B>{}, "B (tuple of refs / proxies)");
    addVariant(VariantTag<Variant::Array>{}, "std::array<Id, 3>");
    addVariant(VariantTag<Variant::Helper>{}, "helper (<= per entry)");
    addVariant(VariantTag<Variant::HelperBits>{},
               "helper (bits, no LocalVocab check)");
  }
};
}  // namespace

std::string TieVariantsBenchmark::name() const {
  return "Benchmarks for the candidates of tieHelper (tie, A, B, array)";
}

AD_REGISTER_BENCHMARK(TieVariantsBenchmark);
}  // namespace ad_benchmark
