# Protocol: `std::tie` vs. `std::array` for row comparisons (2026-10-08)

Branch `create_64bit_id_tie_to_array`, PR [#3577](https://github.com/ad-freiburg/qlever/pull/3577). Question: the branch replaces `std::tie(row[0], ...)` by `std::array<Id, N>{row[0], ...}`, because `std::tie` cannot bind to the proxy objects that a column storage may return instead of `Id&`. The array copies the `Id`s. Does that make the comparisons slower?

Short answer: no. In the measurements it is even faster, but not because an array is better than a tuple. The difference is only whether the compiler inlines `ValueId::compareThreeWay`.

## 1. Setup

- Apple M5, Apple Clang, `-O3`, C++20, libc++, Release build of the project flags. No other load (load average below 2).
- The code under test is the comparison of a located triple with a row of an `IdTable` in `src/index/LocatedTriples.cpp` (`<` and `==`, also used by `processBlockForVacuum`).
- Data: an `IdTable` with 5M rows and a vector of 5M `std::array<Id, 3>` triples. Row `i` is `(i/4, i/2, i)`. The triple is equal to the row for one of three positions and one larger or smaller otherwise, so the comparison is decided in all three columns and the result is `less`/`equal`/`greater` equally often.
- Variants: `std::tie` (references), `std::tuple<Id, Id, Id>` (copies), `std::array<Id, 3>` (copies), and a hand-written lexicographic comparison with `ql::compareThreeWay`.
- Each variant is its own non-inlined function, 15 runs in alternating order, median.

## 2. Results (median over 5M rows)

| Variant | `triple < row` | `triple == row` |
|---|---|---|
| `std::tie` (references) | 10.0 ms | 10.2 ms |
| `std::tuple<Id>` (copies) | 12.1 ms | 12.2 ms |
| `std::array<Id, 3>` (copies) | 6.9 ms | 7.2 ms |
| hand-written, `compareThreeWay` | 7.7 ms | 7.1 ms |

The ratio array/tie was between 0.64 and 0.73 in all runs. Both variants count the same number of hits (1,666,666 for `<`, 1,666,667 for `==`). The absolute difference is about 0.6 ns per row.

The same comparison as a benchmark in the QLever infrastructure (`benchmark/TieVsArrayBenchmark.cpp`, run with `-p`), three runs: `<` 9.7 to 10.7 ms for tie against 6.6 to 7.6 ms for array, `==` 9.6 to 9.9 ms against 7.7 to 8.3 ms.

## 3. Why

It is not the copy and not the algorithm:

- `std::tuple<Id>` with copies is as slow as `std::tie` with references. So references vs. copies makes no difference.
- Both the tuple and the array end up in the same comparison of an `Id`: `ValueId::compareThreeWay`. It checks whether one of the two is a `LocalVocabIndex`; if not (the normal case) it compares the whole 64-bit word, datatype bits included, in one step. `operator==` and `operator<` of `ValueId` both call it. There is no separate "datatype first, then payload" step in the legacy layout. That only exists in the split layout, where the two are stored in separate fields.
- The difference is **inlining**. Assembly of the benchmark loops (`-S`):

| Function | instructions | calls of `ValueId::compareThreeWay` |
|---|---|---|
| `std::tie` | 62 | 3 (one per element) |
| `std::tuple<Id>` | 73 | 3 |
| `std::array` | 264 | 0 on the normal path (the slow `LocalVocabEntry` path still calls) |
| hand-written | 287 | 0 on the normal path |

- The real file shows the same trend. `LocatedTriples.cpp` compiled from `master` (tie) has 384 calls of `ValueId::compareThreeWay` and 40,826 instructions; the branch (array) has 324 calls and 45,226 instructions. So the compiler inlines more, and the object code is about 11 % larger.
- Proof that the inlining is the cause: with `-mllvm -inline-threshold=5000` (everything inlined) all variants are equally fast.

| Variant | default | forced inlining |
|---|---|---|
| `std::tie` `<` | 10.6 ms | 6.6 ms |
| `std::array` `<` | 7.0 ms | 6.6 ms |
| `std::tuple<Id>` `<` | 12.4 ms | 7.0 ms |
| `std::tie` `==` | 10.9 ms | 7.1 ms |
| `std::array` `==` | 7.8 ms | 7.3 ms |
| `std::tuple<Id>` `==` | 12.7 ms | 7.1 ms |

## 4. Conclusion

- Changing `std::tie` to `std::array` does not make the comparisons slower. The proxy-safe version is the one we need anyway, and here it is also faster.
- The speed-up is a side effect of the inliner's decision, not a property of `std::array`. I did not find out why Clang inlines in the array case and not in the tuple case; it is a cost heuristic. A different compiler or version can change it.
- `std::forward_as_tuple` would be a tuple of references to temporaries (dangling after the return), `std::make_tuple` copies like the array. Therefore the array is the simplest correct choice.
- If the speed-up should not depend on the heuristic, `ValueId::compareThreeWay` itself would have to be made cheaper to inline. That is a different change and not part of this PR.

## 5. Limits

- Only Apple Clang with libc++ in C++20 mode. GCC/libstdc++ and C++17 were not built or measured here.
- Synthetic benchmark of the comparison only, no run of the real vacuum or update paths.
- The `std::tie` variant of the benchmark only compiles while the element access of the `IdTable` returns references, so the benchmark only works for the legacy layout.

## 6. Reproduce

```bash
cd build
cmake --build . --target TieVsArrayBenchmark
./benchmark/TieVsArrayBenchmark -p
```

The inlining check: compile the benchmark file with `-mllvm -inline-threshold=5000` added to the compile command and run it again.
