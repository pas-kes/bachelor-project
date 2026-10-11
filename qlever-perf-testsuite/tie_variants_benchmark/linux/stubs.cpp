// Stubs for functions of QLever's libraries that the benchmark never calls on
// its normal path (the `LocalVocabIndex` slow path and string conversion).
#include "index/LocalVocabEntry.h"
#include "util/MemorySize/MemorySize.h"

ql::strong_ordering LocalVocabEntry::compareThreeWay(
    const LocalVocabEntry&) const {
  return ql::strong_ordering::equal;
}
LocalVocabEntry::PositionInVocab LocalVocabEntry::positionInVocabExpensiveCase()
    const {
  return LocalVocabEntry::PositionInVocab{};
}
std::string ad_utility::MemorySize::asString() const { return {}; }

// The logging code of QLever (`util/Log.h`) is pulled in by some headers, but
// the benchmark does not log through it. Stubs for the abseil functions that it
// references, so that no abseil library has to be linked.
#include "absl/strings/numbers.h"
#include "absl/strings/str_cat.h"
#include "absl/time/internal/cctz/include/cctz/time_zone.h"
#include "absl/time/time.h"

namespace absl {
inline namespace lts_20260107 {
std::string FormatTime(absl::string_view, Time, TimeZone) { return {}; }
Time Now() { return Time{}; }
namespace numbers_internal {
char* FastIntToBuffer(uint32_t, char* out) {
  *out = '0';
  return out + 1;
}
}  // namespace numbers_internal
namespace strings_internal {
std::string CatPieces(std::initializer_list<absl::string_view> pieces) {
  std::string result;
  for (auto piece : pieces) {
    result.append(piece.data(), piece.size());
  }
  return result;
}
}  // namespace strings_internal
namespace time_internal {
namespace cctz {
time_zone local_time_zone() { return time_zone{}; }
}  // namespace cctz
}  // namespace time_internal
}  // namespace lts_20260107
}  // namespace absl
