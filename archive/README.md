# Archive

Documentation of the first approach (an in-place rewrite of `Id`/`ValueId` on
the branch `refactor/create_64bit_id_type`, split-column `IdColumn`, and the
optimizations on top of it). It is superseded by the parallel approach in
`../PLAN_split_layout_id.md` (a new `SplitLayoutId` next to `ValueId`, in small
PRs). Kept for reference, not maintained.

- `old-in-place-refactor/phase-1-documentation.md`: the five commits of the
  old branch.
- `old-in-place-refactor/phase-1-documentation-code-specific-places.md`: the
  C++ patterns (proxy `IdRef`, ...), still a useful explanation of the proxy
  idea.
- `old-in-place-refactor/phase-2-optimazations.md`: optimizations measured on
  the old branch.
- `old-in-place-refactor/notes.md`: early ideas for splitting the PRs
  (implemented in the plan).
