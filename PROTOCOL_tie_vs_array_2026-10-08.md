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
all cases the difference is below 1 ns per row (section 6).

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

I built the benchmark once without and once with this change (same worktree,
same flags as in section 1), copied both binaries and ran them alternately 15
times each. Median over 5M rows, range in brackets:

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

## 8. Reproduce

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
