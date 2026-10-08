# Protocol: `std::tie` vs. `std::array` for row comparisons (2026-10-08)

Branch `create_64bit_id_tie_to_array`, PR
[#3577](https://github.com/ad-freiburg/qlever/pull/3577).

In this PR I replace `std::tie(row[0], ...)` by `std::array<Id, N>{row[0], ...}`.
`std::tie` cannot bind to the proxy objects that a column storage may return
instead of `Id&`. The array copies the `Id`s, so I wanted to know if the
comparisons get slower.

They do not. In my measurements the array is even faster. The reason is not that
an array is better than a tuple. It only depends on whether the compiler inlines
`ValueId::compareThreeWay`.

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

- `std::array` instead of `std::tie` does not make the comparisons slower. The
  version that works with proxies is needed anyway, and here it is also faster.
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

- I only measured Apple Clang with libc++ in C++20 mode. GCC with libstdc++ and
  C++17 were not built or measured.
- The benchmark only covers the comparison. I did not run the real vacuum or
  update paths.
- The `std::tie` variant in the benchmark only compiles while the element access
  of the `IdTable` returns references, so the benchmark only works for the legacy
  layout.

## 6. Reproduce

```bash
cd build
cmake --build . --target TieVsArrayBenchmark
./benchmark/TieVsArrayBenchmark -p
```

For the inlining check I added `-mllvm -inline-threshold=5000` to the compile
command of the benchmark file and ran it again.
