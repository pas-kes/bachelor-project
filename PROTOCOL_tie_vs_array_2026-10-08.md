# Protocol: `std::tie` vs. `std::array` for row comparisons (2026-10-08)

Branch `create_64bit_id_tie_to_array`, PR
[#3577](https://github.com/ad-freiburg/qlever/pull/3577).

In this PR I replace `std::tie(row[0], ...)` by `std::array<Id, N>{row[0], ...}`.
`std::tie` cannot bind to the proxy objects that a column storage may return
instead of `Id&`. The array copies the `Id`s, so I wanted to know if the
comparisons get slower.

On macOS (Apple Clang with libc++) they do not, the array is even faster. The
reason is not that an array is better than a tuple. It only depends on whether
the compiler inlines `ValueId::compareThreeWay`. On Linux (aarch64, libstdc++)
the result is different: with gcc 13 the array is 13 to 38 % slower in my micro
benchmark, with clang 18 it is 20 % slower for `<` and 25 % faster for `==`. In
all cases the difference is below 1 ns per row (section 6). Section 8 repeats
the measurement in the loop of the real code and corrects this for g++.

## 1. Setup

- Apple M5, Apple Clang, `-O3`, C++20, libc++, the Release flags of the project.
  No other load on the machine (load average below 2).
- I measure the comparison of a located triple with a row of an `IdTable` as it
  is done in `src/index/LocatedTriples.cpp` (`<` and `==`, also used by
  `processBlockForVacuum`).
- Data: an `IdTable` with 5M rows and a vector of 5M `std::array<Id, 3>`
  triples. Row `i` is `(i/4, i/2, i)`. For one of three positions the triple is
  equal to the row, otherwise it is one larger or one smaller. So the comparison
  is decided in all three columns, and the result is less, equal and greater
  equally often.
- Variants: `std::tie` (references), `std::tuple<Id, Id, Id>` (copies),
  `std::array<Id, 3>` (copies) and a hand-written lexicographic comparison with
  `ql::compareThreeWay`.
- Every variant is its own function that is not inlined. 15 runs in alternating
  order, I take the median.

## 2. Results (median over 5M rows)

| Variant | `triple < row` | `triple == row` |
|---|---|---|
| `std::tie` (references) | 10.0 ms | 10.2 ms |
| `std::tuple<Id>` (copies) | 12.1 ms | 12.2 ms |
| `std::array<Id, 3>` (copies) | 6.9 ms | 7.2 ms |
| hand-written, `compareThreeWay` | 7.7 ms | 7.1 ms |

The ratio array/tie was between 0.64 and 0.73 in all runs. Both variants count
the same number of hits (1,666,666 for `<`, 1,666,667 for `==`). The difference
is about 0.6 ns per row.

The same comparison is also a benchmark in the QLever infrastructure
(`benchmark/TieVsArrayBenchmark.cpp`, run with `-p`). In three runs `<` took 9.7
to 10.7 ms with tie and 6.6 to 7.6 ms with array, `==` took 9.6 to 9.9 ms and
7.7 to 8.3 ms.

## 3. Reason

It is neither the copy nor the algorithm.

- `std::tuple<Id>` with copies is as slow as `std::tie` with references, so
  references against copies makes no difference.
- Tuple and array both end up in the same comparison of two `Id`s,
  `ValueId::compareThreeWay`. It checks if one of them is a `LocalVocabIndex`.
  If not (the normal case), it compares the whole 64 bit word, including the
  datatype bits, in one step. `operator==` and `operator<` of `ValueId` both call
  it. In the legacy layout there is no separate step "first datatype, then
  payload". That only exists in the split layout, where both are in separate
  fields.
- The difference is the inlining. This is the assembly of the benchmark loops
  (`-S`):

| Function | instructions | calls of `ValueId::compareThreeWay` |
|---|---|---|
| `std::tie` | 62 | 3 (one per element) |
| `std::tuple<Id>` | 73 | 3 |
| `std::array` | 264 | none on the normal path (the slow `LocalVocabEntry` path still calls) |
| hand-written | 287 | none on the normal path |

- The real file shows the same. `LocatedTriples.cpp` compiled from `master`
  (tie) has 384 calls of `ValueId::compareThreeWay` and 40,826 instructions. The
  branch (array) has 324 calls and 45,226 instructions. So the compiler inlines
  more, and the object code is about 11 % larger.
- To check that the inlining is the cause, I compiled with
  `-mllvm -inline-threshold=5000`, so that everything is inlined. Then all
  variants are equally fast:

| Variant | default | everything inlined |
|---|---|---|
| `std::tie` `<` | 10.6 ms | 6.6 ms |
| `std::array` `<` | 7.0 ms | 6.6 ms |
| `std::tuple<Id>` `<` | 12.4 ms | 7.0 ms |
| `std::tie` `==` | 10.9 ms | 7.1 ms |
| `std::array` `==` | 7.8 ms | 7.3 ms |
| `std::tuple<Id>` `==` | 12.7 ms | 7.1 ms |

## 4. Conclusion

- On macOS `std::array` instead of `std::tie` does not make the comparisons
  slower, it is even faster. On Linux it is not faster, and with gcc it is a bit
  slower (section 6). The version that works with proxies is needed anyway, so
  the change stays, and the cost is small.
- The speed-up is a side effect of the decision of the inliner and not a
  property of `std::array`. I did not find out why Clang inlines in the array
  case and not in the tuple case, it is a heuristic about the cost. Another
  compiler or another version can change this.
- `std::forward_as_tuple` would be a tuple of references to temporaries, which
  are dangling after the return. `std::make_tuple` copies like the array. So the
  array is the simplest correct solution.
- If the speed-up should not depend on the heuristic, `ValueId::compareThreeWay`
  itself has to be made easier to inline. That is a different change and not
  part of this PR.

## 5. Limits

- Sections 1 to 5 are Apple Clang with libc++ in C++20 mode. Linux is in
  section 6, only on aarch64 (the Docker VM on my Mac), not on x86-64. C++17 was
  not measured.
- The benchmark only covers the comparison. I did not run the real vacuum or
  update paths.
- The `std::tie` variant in the benchmark only compiles while the element access
  of the `IdTable` returns references, so the benchmark only works for the legacy
  layout.

## 6. Linux (aarch64, Docker)

NOTE: Section 8 supersedes this section for the question which variant is
faster in the real code. Here every variant is a separate small function in
which g++ inlined `compareThreeWay`. In `mergeTriplesImpl` it is not inlined.

I also compiled the micro benchmark (the four variants, same code and same
flags as on the Mac, `-O3 -std=gnu++20`) in a container with `ubuntu:24.04` (the
base image of the QLever `Dockerfile`), with the real QLever headers. It runs
on the Docker VM of my Mac, so it is Linux on aarch64 and not x86-64, and it
shares the CPU with macOS. The benchmark needs four functions from the libraries
that it never calls on its normal path (`LocalVocabEntry::compareThreeWay`,
`positionInVocabExpensiveCase`, `MemorySize::asString`, and one function of
abseil). I defined them as stubs.

Compilers: g++ 13.3.0 and clang 18.1.3, both with libstdc++. Median of 3 runs
with 15 repetitions each (range in brackets), 5M rows:

| Compiler | Variant | `triple < row` | `triple == row` |
|---|---|---|---|
| g++ 13 | `std::tie` | 7.4 ms [6.6..7.4] | 5.5 ms [5.5..6.2] |
| g++ 13 | `std::tuple<Id>` | 6.8 ms [6.6..7.5] | 5.9 ms [5.8..6.1] |
| g++ 13 | `std::array<Id, 3>` | 8.3 ms [8.2..8.7] | 7.6 ms [7.5..7.7] |
| g++ 13 | hand-written | 5.9 ms [5.9..5.9] | 6.1 ms [5.9..6.2] |
| clang 18 | `std::tie` | 10.9 ms [10.8..10.9] | 11.0 ms [10.9..11.2] |
| clang 18 | `std::tuple<Id>` | 12.9 ms [12.9..13.1] | 12.9 ms [12.8..13.4] |
| clang 18 | `std::array<Id, 3>` | 13.1 ms [12.8..13.1] | 8.2 ms [8.2..8.3] |
| clang 18 | hand-written | 6.7 ms [6.3..6.8] | 5.8 ms [5.7..5.9] |

Ratio array/tie: g++ 1.13 for `<` and 1.38 for `==`, clang 1.20 for `<` and 0.75
for `==`. The difference between tie and array is between 0.2 and 0.6 ns per
row.

What I looked at in the object files (`objdump`):

- With g++ there is no call of `ValueId::compareThreeWay` in any of the
  variants, everything is inlined. So the inlining is not the explanation on
  gcc, as it was on the Mac. The array variants are a bit longer (about 290 to
  310 lines of assembly against 260 to 280 for tie and tuple). I did not look
  further for the reason.
- With clang 18, tie, tuple and the array with `<` still call
  `ValueId::compareThreeWay` three times, the array with `==` is inlined. That
  matches that only the array with `==` is faster.
### Forced inlining

To see how much of the difference comes from the inlining, I compiled the micro
benchmark two more times and made the compiler inline:

1. With raised limits for the inliner (clang `-mllvm -inline-threshold=5000`,
   g++ with `--param max-inline-insns-single=5000` and some more).
2. With the attribute `[[gnu::flatten]]` on the benchmark functions. It inlines
   everything that these functions call.

Time for 5M rows in ms (median of 3 runs):

| Compiler | Comparison | Variant | normal build | raised limits | `flatten` |
|---|---|---|---|---|---|
| g++ 13 | `<` | `std::tie` | 7.4 | 8.0 | 7.4 |
| g++ 13 | `<` | `std::array` | 8.3 | 8.2 | 6.8 |
| g++ 13 | `==` | `std::tie` | 5.5 | 6.1 | 6.0 |
| g++ 13 | `==` | `std::array` | 7.6 | 7.8 | 7.6 |
| clang 18 | `<` | `std::tie` | 10.9 | 6.3 | 10.9 |
| clang 18 | `<` | `std::array` | 13.1 | 6.7 | 6.3 |
| clang 18 | `==` | `std::tie` | 11.0 | 5.6 | 10.9 |
| clang 18 | `==` | `std::array` | 8.2 | 6.0 | 6.7 |

What I take from it:

- With g++ forcing the inlining changes almost nothing, because g++ inlines
  everything already in the normal build. For `<` the array is between 0.9 ms
  slower (normal build) and 0.6 ms faster (`flatten`) than tie. For `==` the array
  is always 1.6 to 2.1 ms slower than tie, whatever I do. So this small cost of the array with `==`
  is not a question of inlining. I do not know the reason.
- With clang the times jump between about 6 ms and about 11 to 13 ms, depending
  on whether the call of `ValueId::compareThreeWay` is inlined. With the raised
  limits everything is inlined, and then tie and array are both around 6 ms (the
  array 0.4 ms slower). With `flatten` the array loop is inlined (6.3 and
  6.7 ms), but the tie loop is not (still 10.9 ms). I do not know why `flatten`
  does not work for tie, and this makes the array look much faster than it is.
- So the differences on clang in the normal build (array 2 ms slower for `<`,
  3 ms faster for `==`) come from the decision of the inliner and not from the
  array. If the call is inlined, both variants take the same time.

So on Linux the array is not faster in the normal build. The change costs a
small amount (up to 0.6 ns per comparison) with gcc, and with clang it depends on
the operator and on what the inliner decides. In the normal build the
hand-written comparison is the fastest variant on Linux, and with `flatten` it is
about as fast as the array.

Limits of this part: only aarch64 and only in a VM, libstdc++ only (no libc++ on
Linux), only gcc 13 and clang 18, and it is the micro benchmark with the four
variants and not the benchmark of the QLever infrastructure. I forced the
inlining on Linux only, not again on the Mac.

## 7. Splitting `ValueId::compareThreeWay`

In the review Johannes suggested to keep only the cheap standard case in
`compareThreeWay`, which is that no `LocalVocabIndex` is involved and the bits
are compared, and to always inline it. The rest would go into a separate function
that is not inlined, because it is expensive anyway. I tried this in a separate
worktree (not in the PR). The change in `src/global/ValueId.h`:

```cpp
// the rare case, not inlined
AD_NO_INLINE QL_CONSTEXPR auto compareThreeWayOneIsLocalVocab(
    const ValueId& other) const {
  // both are LocalVocabIndex, or exactly one: unchanged code from before
}

// the common case, always inlined
AD_ALWAYS_INLINE QL_CONSTEXPR auto compareThreeWay(const ValueId& other) const {
  using enum Datatype;
  if (getDatatype() != LocalVocabIndex &&
      other.getDatatype() != LocalVocabIndex) {
    return ql::compareThreeWay(_bits, other._bits);
  }
  return compareThreeWayOneIsLocalVocab(other);
}
```

The only other change is the include of `util/CompilerExtensions.h` for the two
macros. The logic of the rare case is the same as before.

This was measured on the Mac only (Apple M5, macOS, Apple Clang with libc++,
`-O3`, C++20). I built the benchmark once without and once with this change
(same worktree, same flags as in section 1), copied both binaries and ran them
alternately 15 times each. Median over 5M rows, range in brackets:

| Comparison | Variant | before | with the split |
|---|---|---|---|
| `triple < row` | `std::tie` | 10.8 ms [9.6..12.0] | 6.2 ms [6.1..7.4] |
| `triple < row` | `std::array<Id, 3>` | 8.0 ms [7.1..8.3] | 8.1 ms [7.9..9.0] |
| `triple == row` | `std::tie` | 10.1 ms [9.5..11.2] | 5.9 ms [5.7..6.3] |
| `triple == row` | `std::array<Id, 3>` | 8.2 ms [7.2..8.9] | 6.5 ms [6.3..6.9] |

What I take from it:

- `std::tie` gets about 40 % faster with the split (10.8 to 6.2 ms for `<`,
  10.1 to 5.9 ms for `==`).
- The array does not change for `<`, because it was already inlined, and gets a
  bit faster for `==`.
- So after the split `std::tie` is the fastest variant. The array is 1.9 ms
  (30 %, about 0.4 ns per row) slower for `<` and 0.5 ms (9 %, about 0.1 ns per
  row) slower for `==`.
- This fits section 3: the difference between tie and array came from whether
  `compareThreeWay` is inlined. In the binary without the change there is an
  out-of-line `ValueId::compareThreeWay`. With the change only
  `compareThreeWayOneIsLocalVocab` is out of line.

For the PR this speaks for keeping `std::tie` for `Id&` and using something else
only for proxies.

Limits of this part: only macOS with Apple Clang, only the micro benchmark.
I did not measure Linux, the effect on the rest of QLever (code size, joins, sort), the test suite with the change, or
GCC 8 in C++17 mode.

## 8. Realistic benchmark with the real column types, and a check on Linux

Sections 2 to 7 used small loops that compare every row separately, and a
stand-in for the proxy. This section replaces them where they disagree. The
benchmark (`qlever-perf-testsuite/tie_variants_benchmark/TieVariantsBenchmark.cpp`,
not part of the QLever repository) is built to be closer to the real code:

- It is the loop of `LocatedTriplesPerBlock::mergeTriplesImpl`: a sorted block
  of rows is merged with sorted located triples, first `lessThan`, then
  `equal`. The data is random (fixed seed): 64 values in column 0, `rows / 16`
  in column 1, 4096 in column 2, about one located triple per 100 rows (half of
  them existing rows, mostly deletions, half new triples). The result of a
  comparison depends on the data, and rows and located triples share prefixes.
- The comparison functions are inlined into the loop, as the lambdas are in the
  real code. (My first version of this benchmark had them as member functions
  that clang did not inline in one of the builds, which hid the effect I wanted
  to measure. I found that by reading the assembly.)
- Two block sizes: about 100,000 rows (fits into the caches) and about 3 million
  rows. The tables show the small block, the large block gives the same picture
  with 5 to 10 % lower times.
- Every variant has to return the same result (copied, inserted and matched
  rows, and a checksum) as `std::tie` on the same kind of rows, otherwise the
  benchmark aborts.
- The rows come from four sources:
  1. `legacy Id&`: an `IdTable`, as today.
  2. `bridge ConstIdRef`: three `IdColumnVector`s of the repository (payloads
     and datatypes in separate arrays). `row[I]` is the real proxy
     `ConstIdRef`. The `Id` is still the packed legacy `Id`, so the proxy
     builds it again for every comparison (`idFromBitsCompat`).
  3. `future Id&`: the planned `SplitLayoutId` (datatype byte and 64 bit
     payload, 16 bytes) as plain objects in columns.
  4. `future proxy`: the same `IdColumnVector`s, but with the planned
     `SplitLayoutId`: the proxy reads both fields with `ConstIdRef::getBits()`
     and compares them directly.
- `SplitLayoutId` is not in the repository. `FutureId` in the benchmark is a
  stand-in: it compares `SplitLayoutIdBitRepresentation` of the repository
  (datatype first, then payload) and has the same check for `LocalVocabIndex`
  as `Id::compareThreeWay`, with a slow path that is not inlined. The data has
  no `LocalVocabIndex`.
- Variants: `std::tie` (current), A, B and `std::array<Id, 3>` as before. New:
  "helper", a hand-written function that compares the three entries one after
  the other with `<=>` (no tuple), and "helper (bits)", the same without the
  check for `LocalVocabIndex` (`compareWithoutLocalVocab`). The last one is only
  a reference for what that check costs, it is wrong for `Id`s with a
  `LocalVocabIndex`.
- Variant B in the benchmark is the code of `tieHelper` in `LocatedTriples.cpp`
  (with the alias `TieElement`). A `static_assert` in the benchmark checks that
  `std::tuple<decltype(row[I])...>`, without the alias, has the same type for all
  four kinds of rows, so the alias is not needed for these types. I measured the
  rebuilt benchmark again (5 rounds on the Mac), B is unchanged.
- Each loop exists in two shapes: as in `mergeTriplesImpl` with two comparisons
  per row, and with one three-way comparison per row ("single compare").
- "without" and "with" in the tables mean the build without and with the change
  of `ValueId::compareThreeWay` from section 7 (`compareThreeWay_split.patch`).
  For the future sources the change cannot matter, because `FutureId` does not
  use `ValueId`.

### 8.1 What the real code looks like

I compiled the real `LocatedTriples.cpp` with Apple Clang (the build in the
repository), and in the Linux container with g++ 13.3 and clang 18.1.3, each
for master (`std::tie`), for variant A and for the array of PR #3577, and
counted the calls of `ValueId::compareThreeWay` inside the six
`mergeTriplesImpl<...>` functions (1, 2 or 3 index columns, with or without the
graph column):

| Build | `std::tie` and A | array |
|---|---|---|
| Apple Clang | up to 8 calls (checked in the object file of the variant A build) | not checked |
| g++ 13 | 2, 4, 4, 6, 6, 8 | 2, 4, 4, 2, 2, 2 |
| clang 18 | 2, 4, 4, 6, 6, 8 | 2, 2, 2, 3, 3, 4 |

- The lambdas `lessThan` and `equal` are inlined into `mergeTriplesImpl`, there
  is no function for them in the object file.
- `ValueId::compareThreeWay` is not inlined in the current code: two calls per
  compared column, with all three compilers. So the columns "without" are the
  relevant ones for the code that runs today. The columns "with" show what the
  change of `compareThreeWay` could bring.
- Master and variant A give the same code (the same number of instructions and
  calls with g++ and clang 18). So A costs nothing for `Id&`.
- The array version inlines `compareThreeWay` partly (fewer calls).

### 8.2 Results

All times are milliseconds for 20 million processed rows, median over 13 rounds
(Apple Clang) and 9 rounds (Linux), the two builds alternate in every round.
Each cell is "without / with". Apple Clang is the M5 Mac, g++ and clang 18 run
in a Linux container (aarch64, libstdc++) on the same Mac.

Rows from the `IdTable` (`Id&`), two comparisons per row:

| Variant | Apple Clang | g++ 13 | clang 18 |
|---|---|---|---|
| `std::tie` (current) | 67.7 / 47.0 | 109.3 / 44.2 | 70.8 / 71.0 |
| A | 67.6 / 46.4 | 107.8 / 44.3 | 71.0 / 70.4 |
| B | 67.6 / 46.9 | 107.9 / 44.3 | 70.4 / 73.9 |
| `std::array<Id, 3>` | 49.7 / 53.0 | 53.6 / 54.4 | 69.5 / 64.8 |
| helper | 69.8 / 43.4 | 95.9 / 41.8 | 72.4 / 44.4 |
| helper (bits) | 24.6 / 24.5 | 17.1 / 17.1 | 16.8 / 16.9 |

The same with a single three-way comparison per row:

| Variant | Apple Clang | g++ 13 | clang 18 |
|---|---|---|---|
| `std::tie` (current) | 41.1 / 31.5 | 38.9 / 27.8 | 42.9 / 43.0 |
| A | 41.1 / 31.4 | 38.9 / 28.4 | 43.2 / 42.9 |
| B | 41.1 / 32.0 | 38.9 / 28.0 | 43.1 / 41.3 |
| `std::array<Id, 3>` | 34.4 / 32.7 | 33.3 / 37.5 | 45.8 / 40.6 |
| helper | 39.7 / 30.4 | 38.0 / 28.4 | 42.6 / 30.5 |
| helper (bits) | 18.3 / 17.2 | 16.9 / 16.8 | 25.2 / 25.2 |

Full merge from the `IdTable` (with allocation of the result and copying of the
rows, as in `mergeTriplesImpl`), two comparisons:

| Variant | Apple Clang | g++ 13 | clang 18 |
|---|---|---|---|
| `std::tie` (current) | 94.2 / 75.7 | 130.7 / 99.7 | 129.1 / 116.3 |
| A | 94.3 / 74.6 | 130.4 / 99.7 | 131.2 / 116.7 |
| B | 96.1 / 75.5 | 129.8 / 99.6 | 130.4 / 115.9 |
| `std::array<Id, 3>` | 83.7 / 83.1 | 127.7 / 108.3 | 117.8 / 112.0 |
| helper | 95.5 / 69.1 | 140.0 / 97.2 | 117.5 / 86.6 |

Proxy rows of today (`ConstIdRef` over `IdColumnVector`, legacy `Id`), two
comparisons (`std::tie` does not compile here):

| Variant | Apple Clang | g++ 13 | clang 18 |
|---|---|---|---|
| A | 76.4 / 59.4 | 116.1 / 63.0 | 79.4 / 81.3 |
| B | 104.5 / 100.9 | 105.7 / 62.4 | 89.7 / 53.8 |
| `std::array<Id, 3>` | 60.8 / 67.4 | 63.4 / 76.0 | 77.3 / 72.2 |
| helper | 67.4 / 42.3 | 122.7 / 46.6 | 70.8 / 42.7 |

Future layout, two comparisons (one value per cell, the build "without"). Rows
as `FutureId&` and as proxy, written as "`FutureId&` / proxy":

| Variant | Apple Clang | g++ 13 | clang 18 |
|---|---|---|---|
| `std::tie` | 66.9 / not possible | 48.0 / not possible | 73.8 / not possible |
| A | 67.1 / 59.5 | 45.3 / 50.6 | 70.8 / 56.6 |
| B | 67.0 / 70.2 | 47.0 / 59.2 | 70.7 / 60.7 |
| `std::array<Id, 3>` | 55.9 / 57.4 | 76.0 / 72.6 | 64.5 / 72.3 |
| helper | 44.9 / 44.9 | 55.1 / 51.2 | 37.6 / 37.9 |
| helper (bits) | 35.0 / 34.8 | 22.9 / 25.2 | 28.9 / 28.4 |

The raw numbers (all groups, both block sizes, minima) are in
`qlever-perf-testsuite/tie_variants_benchmark/results/`.

### 8.3 What I take from it

- For `Id&` rows variant A is the same code as `std::tie` (section 8.1) and the
  same speed in all measurements. B is the same type for lvalues and the same
  speed, too. So neither costs anything today.
- In the current code `compareThreeWay` is not inlined (8.1). Then the array is
  faster than `std::tie` on Apple Clang (49.7 against 67.7 ms) and on g++ (53.6
  against 109.3 ms) and as fast on clang 18. This corrects section 6: there g++
  had inlined `compareThreeWay` everywhere in the small separate functions,
  which is not what happens in `mergeTriplesImpl`. For the real code, the array
  of PR #3577 would be faster than master on two of three compilers, not slower.
- If `compareThreeWay` is made inlinable, as in section 7, the picture flips:
  `std::tie`, A and B become faster than the array on Apple Clang (47 against
  53 ms) and on g++ (44 against 54 ms), 30 % and 60 % faster than before. On
  clang 18 they do not change (71 ms), the array is a bit better (65 ms).
- The hand-written helper (no tuple) is the best or tied for best on all three
  compilers as soon as `compareThreeWay` is inlined (43.4, 41.8 and 44.4 ms),
  and it is far better than the tuple variants for the future layout on clang
  18 (37.6 against 70.7 ms) and on Apple Clang (44.9 against 67 ms). Without the
  inlining it is not better than `std::tie`. It would need a change of the
  callers, so it is not a candidate for this PR.
- Comparing once per row instead of twice (`lessThan`, then `equal`) saves 30 to
  65 % of the time of the comparisons, for all variants and compilers except the
  reference without the `LocalVocabIndex` check. That is a bigger effect than the choice between `std::tie`, A, B and the array. It
  needs a change of the loop in `mergeTriplesImpl`.
- The check for `LocalVocabIndex` in `compareThreeWay` costs a lot: without it
  the comparisons are about twice as fast as the best other variant (the
  "helper (bits)" rows). It cannot
  simply be left out. A cheap test on block level (for example "this block has no
  local vocab entries") might be worth a look later, but I did not check if that
  is possible.
- Proxies: A is faster than B on Apple Clang and g++ (future proxy 59.5 against
  70.2 ms and 50.6 against 59.2 ms), but not everywhere: with clang 18 and the
  split B is much faster than A for the bridge proxy (53.8 against 81.3 ms). So
  I cannot say that A is better than B in general. For the future proxy the array
  is among the worst on g++ (72.6 ms against 50.6 for A) and on clang 18 (72.3
  against 56.6 for A). The helper is the most robust choice.
- Two builds of the same code can differ by up to 20 % (for example the future
  proxy with B on g++: 59.2 and 48.3 ms in the two builds, where the change of
  `compareThreeWay` cannot matter). I only trust patterns that show up in
  several compilers or in both block sizes.

For the PR #3577 this means: A (or B) keeps the current code unchanged for
`Id&` and works for proxies, which is what the review asks for. The array
would be faster today only because `compareThreeWay` is not inlined, and it
would be slower as soon as that is fixed, and slower for the future layout.
The faster options (inlining `compareThreeWay`, one comparison per row, the
helper) are separate changes.

### 8.4 Limits

- Only aarch64 (the M5 Mac and the Docker VM on it, which shares the CPU with
  macOS), not x86-64. On Linux only libstdc++ with g++ 13.3 and clang 18.1.3,
  not the compilers and versions of the CI in full, and not libc++.
- `SplitLayoutId` is a stand-in, and the proxy rows are built from the real
  `IdColumnVector` but without an `IdTable` around them (that needs the column
  storage traits, which are not merged). The real proxy of the future may be
  built differently.
- The data is synthetic (1 % located triples, my guess for the distribution of
  the values), and there are no `LocalVocabIndex` ids.
- C++17 mode and GCC 8 were not tried.
- The Mac was quiet for most runs (load below 3), but the Linux container
  shares the CPU with macOS. Differences below about 10 % between single cells
  are noise.

## 9. Reproduce

```bash
cd build
cmake --build . --target TieVsArrayBenchmark
./benchmark/TieVsArrayBenchmark -p
```

For the inlining check I added `-mllvm -inline-threshold=5000` to the compile
command of the benchmark file and ran it again.

For Linux I mounted the repository into an `ubuntu:24.04` container (under
`/repo`, because mounting it at its own path did not work), installed `g++`,
`clang`, `libboost1.83-dev` and `libicu-dev`, and compiled the micro benchmark
with the `-D` and `-I` flags from the ninja command of a test, plus the stubs.
For the run with `flatten` I added `-DFLATTEN=[[gnu::flatten]]` to the compile
command, the benchmark functions have this macro in their attributes.

For section 7 I built `TieVsArrayBenchmark` in a worktree at the tip of the
branch, copied the binary, applied the change to `ValueId.h`, built again and
copied the second binary. Both binaries were run alternately 15 times with
`-p`.

For section 8 (all files are in `qlever-perf-testsuite/tie_variants_benchmark/`):

- Mac: I built `TieVariantsBenchmark` in two worktrees of the branch, one
  unchanged and one with `compareThreeWay_split.patch` applied to
  `src/global/ValueId.h`, copied the two binaries and ran
  `python3 run_alternating_mac.py <without> <with> 13`.
- Linux: `linux/Dockerfile` builds the toolchain image (ubuntu 24.04, g++ 13.3,
  clang 18.1.3, Boost, ICU). `linux/build.sh` compiles the benchmark with the
  `-D` and `-I` flags of the ninja command (`linux/flags_container.txt`, the
  repository is mounted at `/qv`) for one compiler and one worktree.
  `linux/Benchmark.h` replaces QLever's benchmark infrastructure (it prints the
  same lines), `linux/stubs.cpp` has stubs for the functions of the QLever and
  abseil libraries that are never called on the normal path, and
  `linux/run_linux.py` runs the four binaries alternately.
- The calls of `compareThreeWay` in the real `mergeTriplesImpl`:
  `linux/compile_real.sh` compiles `LocatedTriples.cpp` of master, of variant A
  and of the array version, `linux/analyze.py` counts the calls in the object
  files.
