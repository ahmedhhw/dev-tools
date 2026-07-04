# Context: Iteration 1 — `worktree-manager diff --from REF --to REF`

## Goal
Let a caller override the diffed refs explicitly: `worktree-manager diff --from REF` and/or `--to REF`. Reuses all of Iteration 0's resolution/dispatch — this iteration only wires the already-parsed `from_ref`/`to_ref` flags through to `DiffPanel.show_diff`, which Iteration 0's `_handle_diff_command` already branches on (`if from_ref or to_ref: ... else: _diff_from_working_tree(...)`).

## Tests to write
- `worktree-manager diff --from main` shows the diff panel comparing the worktree's working tree against `main`, leaving "to" at the default (working tree)
- `worktree-manager diff --to HEAD~3` shows the diff panel comparing the default "from" (inferred parent branch) against `HEAD~3`
- `worktree-manager diff --from main --to HEAD~3` shows the diff panel comparing exactly those two refs, no defaults applied
- an invalid `--from`/`--to` ref (one git can't resolve) surfaces as an error reply rather than a silent no-op or crash — mirrors however `DiffPanel`/`GitService` already surfaces bad refs elsewhere (check for existing error-reply behavior before adding new handling)
- regression: `worktree-manager diff` with no flags still preselects the inferred parent branch (Iteration 0 behavior unchanged)

## Files to touch
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — no new methods; `App._handle_diff_command` (added in Iteration 0) already accepts and forwards `from_ref`/`to_ref` to `DiffPanel.show_diff`. Confirm/extend `parse_args`'s existing `diff_parser.add_argument("--from"/"--to", ...)` from Iteration 0 if any adjustment is needed.
- `tests/test_cli_diff_command_qt.py` — extend with the explicit-ref cases (same file Iteration 0 created).

## Design / pseudocode

#### `worktree_manager/cli.py` — `App._handle_diff_command`
```
# Already implemented in Iteration 0:
if from_ref or to_ref:
    self._panel_cache["diff"].show_diff(repo_path, worktree_path=worktree.path,
                                         from_ref=from_ref, to_ref=to_ref)
else:
    self._diff_from_working_tree(worktree.path)
```
This iteration verifies/tests this branch specifically — no production code changes expected unless testing surfaces a gap (e.g. invalid-ref error handling not yet covered).

## Relevant existing code

[`DiffPanel.show_diff`](worktree-manager/worktree_manager/ui/diff_panel.py#L213):
```python
def show_diff(self, repo_path: str, to_ref: str | None,
              from_ref: str | None = None, worktree_path: str | None = None) -> None:
    self._set_repo_combo(repo_path)
    self._load_repo(repo_path)
    if worktree_path is not None:
        self._set_worktree_combo(worktree_path)
        self._load_worktree(worktree_path)
    # Only override what the caller explicitly provided; leave None slots
    # as-is so _load_worktree's auto-selections (defaults) are preserved.
    if to_ref is not None:
        self._point_selector._select_by_ref(self._point_selector._newer_list, to_ref)
    if from_ref is not None:
        self._point_selector._select_by_ref(self._point_selector._older_list, from_ref)
```

`App._handle_diff_command` and `_find_tracked_repo_for_toplevel` — built in Iteration 0's context file, unchanged here.

## Constraints / invariants
- `show_diff` only overwrites the combo selection for the ref it was explicitly given — passing only `--to` still gets the inferred parent branch as `from_ref` (Iteration 0's default-resolution behavior), and vice versa. Do not special-case "one flag given" — the existing `if to_ref is not None` / `if from_ref is not None` branches in `DiffPanel.show_diff` already handle this correctly.
- No new resolution logic should be needed for this iteration — if production code changes turn out to be non-trivial, that's a signal the design assumption (Iteration 0 already threads `from_ref`/`to_ref` through) was wrong and worth flagging before proceeding.

## Done when (gate items)
- [ ] `worktree-manager diff --from main` opens the diff panel with `main` as the "from" ref and the default "to" (working tree).
- [ ] `worktree-manager diff --to HEAD~3` opens the diff panel with the default inferred parent branch as "from" and `HEAD~3` as "to".
- [ ] `worktree-manager diff --from main --to HEAD~3` opens the diff panel with exactly those two refs, no inferred defaults used.
- [ ] An unresolvable `--from`/`--to` ref produces a clear error rather than a crash or silent blank diff.
- [ ] Regression: `worktree-manager diff` (no flags) still preselects the inferred parent branch as before.
- [ ] Regression: single-instance focus behavior from Iteration 0 still works.

## TDD mode: <set at Stage 3>
