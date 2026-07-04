# Plan: Iteration 1 — `worktree-manager diff --from REF --to REF`

**TDD mode:** Reviewed (author complete Red tests and Green production code for review; do not run).

## Summary

Iteration 0 already wired `from_ref`/`to_ref` end-to-end: [`parse_args`](worktree-manager/worktree_manager/cli.py#L85) parses `--from`/`--to`, [`main`](worktree-manager/worktree_manager/cli.py#L1171) forwards them over IPC, [`App._handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921) branches on `if from_ref or to_ref` and calls [`DiffPanel.show_diff`](worktree-manager/worktree_manager/ui/diff_panel.py#L213), which selectively overrides only the ref it was given via [`DiffPointSelector._select_by_ref`](worktree-manager/worktree_manager/ui/diff_point_selector.py#L227).

So this iteration is **mostly test-hardening** of an already-built path, plus **one genuine production gap**:

> **Gap flagged (per context Constraints):** the context says invalid-ref handling should "mirror however `DiffPanel`/`GitService` already surfaces bad refs elsewhere (check for existing error-reply behavior before adding new handling)." I checked: there is **no** existing surface. [`_select_by_ref`](worktree-manager/worktree_manager/ui/diff_point_selector.py#L227) silently returns when no list row matches the ref, and [`_handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921) unconditionally returns `{"ok": True}`. An unresolvable `--from`/`--to` today is therefore a **silent no-op** (the diff panel just keeps its defaults) — which also violates the user's "no silent exceptions" rule. Phase 1.5 adds the smallest error surface: a `GitService.ref_exists` check reusing the existing `{"ok": False, "error": ...}` reply pattern already in `_handle_diff_command`. This is the only new production code in the iteration; everything else is Red tests over Iteration 0's code.

Phases are ordered so each Red test fails against the current tree before its Green code, and each phase is independently testable.

---

### Phase 1.1 — `--from` alone forwards to show_diff with default "to"
**What it covers:** `worktree-manager diff --from main` dispatches into `DiffPanel.show_diff` with `from_ref="main"` and `to_ref=None`, so the "to" side keeps its worktree default.
**Files touched:**
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — no change expected; the `if from_ref or to_ref` branch in [`_handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921) already forwards.
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py) — extend with the `--from`-only case.

**Tests (Red):**
```python
def test_diff_with_from_ref_only_forwards_from_and_leaves_to_default(app, mock_git, mock_store):
    """diff --from main forwards from_ref=main and to_ref=None to the diff panel,
    so the 'to' side keeps its worktree default."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}
    mock_git.ref_exists.return_value = True

    diff_panel = MagicMock()
    app._panel_cache["diff"] = diff_panel

    with patch.object(app, "raise_and_activate"), \
         patch.object(app, "_show_diff"), \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt:
        request = {"action": "diff", "cwd": "/myrepo/subdir",
                   "from_ref": "main", "to_ref": None}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is True
    mock_diff_wt.assert_not_called()
    diff_panel.show_diff.assert_called_once_with(
        "/myrepo", worktree_path=worktree_path, from_ref="main", to_ref=None,
    )
```

**Production code (Green):** None — Iteration 0's [`_handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921) already satisfies this once Phase 1.5's `ref_exists` guard is in place. During this phase, stub `mock_git.ref_exists.return_value = True` in the test's git fixture usage (the attribute is a `MagicMock` auto-attr, so it already returns truthy; the explicit line documents intent). If the assertion fails, that is a real Iteration-0 regression to flag, not new code to write here.

**Done when:** `test_diff_with_from_ref_only_forwards_from_and_leaves_to_default` passes: `show_diff` is called once with `from_ref="main"`, `to_ref=None`, and `_diff_from_working_tree` is never called.

---

### Phase 1.2 — `--to` alone forwards to show_diff with default "from"
**What it covers:** `worktree-manager diff --to HEAD~3` dispatches into `DiffPanel.show_diff` with `to_ref="HEAD~3"` and `from_ref=None`, so the "from" side keeps the inferred parent-branch default.
**Files touched:**
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — no change expected.
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py) — extend with the `--to`-only case.

**Tests (Red):**
```python
def test_diff_with_to_ref_only_forwards_to_and_leaves_from_default(app, mock_git, mock_store):
    """diff --to HEAD~3 forwards to_ref=HEAD~3 and from_ref=None to the diff panel,
    so the 'from' side keeps its inferred parent-branch default."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}
    mock_git.ref_exists.return_value = True

    diff_panel = MagicMock()
    app._panel_cache["diff"] = diff_panel

    with patch.object(app, "raise_and_activate"), \
         patch.object(app, "_show_diff"), \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt:
        request = {"action": "diff", "cwd": "/myrepo/subdir",
                   "from_ref": None, "to_ref": "HEAD~3"}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is True
    mock_diff_wt.assert_not_called()
    diff_panel.show_diff.assert_called_once_with(
        "/myrepo", worktree_path=worktree_path, from_ref=None, to_ref="HEAD~3",
    )
```

**Production code (Green):** None — same as Phase 1.1; Iteration 0's branch already forwards. A failure here signals an Iteration-0 regression to flag.

**Done when:** `test_diff_with_to_ref_only_forwards_to_and_leaves_from_default` passes: `show_diff` is called once with `from_ref=None`, `to_ref="HEAD~3"`, and `_diff_from_working_tree` is never called.

---

### Phase 1.3 — both `--from` and `--to` forward exactly, no defaults
**What it covers:** `worktree-manager diff --from main --to HEAD~3` forwards both refs verbatim with no inferred defaults applied.
**Files touched:**
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — no change expected.
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py) — extend with the both-flags case.

**Tests (Red):**
```python
def test_diff_with_both_refs_forwards_both_exactly(app, mock_git, mock_store):
    """diff --from main --to HEAD~3 forwards exactly those two refs to the diff
    panel with no inferred defaults substituted."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}
    mock_git.ref_exists.return_value = True

    diff_panel = MagicMock()
    app._panel_cache["diff"] = diff_panel

    with patch.object(app, "raise_and_activate"), \
         patch.object(app, "_show_diff"), \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt:
        request = {"action": "diff", "cwd": "/myrepo/subdir",
                   "from_ref": "main", "to_ref": "HEAD~3"}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is True
    mock_diff_wt.assert_not_called()
    diff_panel.show_diff.assert_called_once_with(
        "/myrepo", worktree_path=worktree_path, from_ref="main", to_ref="HEAD~3",
    )
```

**Production code (Green):** None — Iteration 0's branch already forwards both refs.

**Done when:** `test_diff_with_both_refs_forwards_both_exactly` passes: `show_diff` is called once with `from_ref="main"`, `to_ref="HEAD~3"`.

---

### Phase 1.4 — `show_diff` overrides only the explicitly-provided ref
**What it covers:** At the `DiffPanel` level, `show_diff` calls `_select_by_ref` on the newer list only when `to_ref` is given and on the older list only when `from_ref` is given — proving the "one flag given, other stays default" contract lives in `show_diff` itself.
**Files touched:**
- [worktree_manager/ui/diff_panel.py](worktree-manager/worktree_manager/ui/diff_panel.py) — no change expected; asserts existing [`show_diff`](worktree-manager/worktree_manager/ui/diff_panel.py#L213) behavior.
- `tests/test_diff_panel_show_diff_refs_qt.py` — new file (no existing `DiffPanel.show_diff` unit test covers selective override).

**Tests (Red):**
```python
"""Tests that DiffPanel.show_diff overrides only the explicitly-provided ref."""
from unittest.mock import MagicMock, patch

import pytest

from worktree_manager.ui.diff_panel import DiffPanel


@pytest.fixture
def panel(qtbot):
    git = MagicMock()
    git.list_worktrees.return_value = []
    store = MagicMock()
    store.all_repos.return_value = {}
    store.get_diff_selection.return_value = {}
    p = DiffPanel(git_service=git, config_store=store)
    qtbot.addWidget(p)
    return p


def test_show_diff_selects_only_newer_when_only_to_given(panel):
    """When only to_ref is given, show_diff selects the newer list and leaves
    the older list's default untouched."""
    with patch.object(panel, "_set_repo_combo"), \
         patch.object(panel, "_load_repo"), \
         patch.object(panel._point_selector, "_select_by_ref") as mock_select:
        panel.show_diff("/repo", to_ref="HEAD~3", from_ref=None)

    mock_select.assert_called_once_with(panel._point_selector._newer_list, "HEAD~3")


def test_show_diff_selects_only_older_when_only_from_given(panel):
    """When only from_ref is given, show_diff selects the older list and leaves
    the newer list's default untouched."""
    with patch.object(panel, "_set_repo_combo"), \
         patch.object(panel, "_load_repo"), \
         patch.object(panel._point_selector, "_select_by_ref") as mock_select:
        panel.show_diff("/repo", to_ref=None, from_ref="main")

    mock_select.assert_called_once_with(panel._point_selector._older_list, "main")


def test_show_diff_selects_both_lists_when_both_given(panel):
    """When both refs are given, show_diff selects both lists."""
    with patch.object(panel, "_set_repo_combo"), \
         patch.object(panel, "_load_repo"), \
         patch.object(panel._point_selector, "_select_by_ref") as mock_select:
        panel.show_diff("/repo", to_ref="HEAD~3", from_ref="main")

    assert mock_select.call_count == 2
    called_lists = [c.args[0] for c in mock_select.call_args_list]
    called_refs = [c.args[1] for c in mock_select.call_args_list]
    assert panel._point_selector._newer_list in called_lists
    assert panel._point_selector._older_list in called_lists
    assert "HEAD~3" in called_refs
    assert "main" in called_refs
```

**Production code (Green):** None — [`show_diff`](worktree-manager/worktree_manager/ui/diff_panel.py#L213)'s existing `if to_ref is not None` / `if from_ref is not None` guards already satisfy these. A failure signals an Iteration-0 regression to flag.

**Done when:** All three tests in `tests/test_diff_panel_show_diff_refs_qt.py` pass, confirming selective override lives in `show_diff`.

---

### Phase 1.5 — an unresolvable `--from`/`--to` ref surfaces as an error reply
**What it covers:** When git can't resolve an explicitly-provided `from_ref` or `to_ref`, `_handle_diff_command` returns `{"ok": False, "error": ...}` (which `main` prints to stderr + exit 1) instead of the current silent no-op where the panel keeps its defaults.
**Files touched:**
- [worktree_manager/git_service.py](worktree-manager/worktree_manager/git_service.py) — add a small `ref_exists(repo_path, ref)` method (reuses the existing `subprocess.run`/`CalledProcessError` pattern already used by [`toplevel_for`](worktree-manager/worktree_manager/git_service.py#L45)).
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — in [`_handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921), validate each provided ref before dispatch, reusing the existing `{"ok": False, "error": ...}` reply shape.
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py) — add invalid-ref case.
- `tests/test_git_service_ref_exists.py` — new file for the `ref_exists` unit test.

**Tests (Red):**
```python
# tests/test_git_service_ref_exists.py
"""Tests for GitService.ref_exists."""
import subprocess
from unittest.mock import patch

from worktree_manager.git_service import GitService


def test_ref_exists_returns_true_for_resolvable_ref():
    """A ref git can resolve returns True."""
    git = GitService()
    with patch.object(git, "_run", return_value="deadbeef\n") as mock_run:
        assert git.ref_exists("/repo", "main") is True
    cmd = mock_run.call_args.args[0]
    assert cmd[:3] == ["git", "rev-parse", "--verify"]
    assert cmd[-1] == "main^{commit}"


def test_ref_exists_returns_false_for_unresolvable_ref():
    """A ref git cannot resolve returns False rather than raising."""
    git = GitService()
    err = subprocess.CalledProcessError(128, ["git", "rev-parse"])
    with patch.object(git, "_run", side_effect=err):
        assert git.ref_exists("/repo", "no-such-ref") is False
```

```python
# appended to tests/test_cli_diff_command_qt.py
def test_diff_with_unresolvable_ref_returns_error_reply(app, mock_git, mock_store):
    """An explicit --from/--to ref git cannot resolve surfaces as an error reply
    instead of a silent no-op, and the diff panel is not driven."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}
    mock_git.ref_exists.return_value = False  # git can't resolve the ref

    diff_panel = MagicMock()
    app._panel_cache["diff"] = diff_panel

    with patch.object(app, "raise_and_activate"), \
         patch.object(app, "_show_diff"), \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt:
        request = {"action": "diff", "cwd": "/myrepo/subdir",
                   "from_ref": "no-such-ref", "to_ref": None}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is False
    assert "no-such-ref" in reply["error"]
    diff_panel.show_diff.assert_not_called()
    mock_diff_wt.assert_not_called()
```

**Production code (Green):**
```python
# worktree_manager/git_service.py — new method on GitService
def ref_exists(self, repo_path: str, ref: str) -> bool:
    """True if git can resolve ``ref`` to a commit in ``repo_path``."""
    try:
        self._run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=repo_path,
        )
        return True
    except subprocess.CalledProcessError:
        return False
```

```python
# worktree_manager/cli.py — inside App._handle_diff_command, after the worktree
# lookup and before self.raise_and_activate(); replaces the current unconditional
# dispatch so an unresolvable explicit ref becomes an error reply.

        worktree = next((w for w in worktrees if w.path == toplevel), None)
        if worktree is None:
            return {"ok": False, "error": "cwd's worktree is not tracked"}
        for ref in (from_ref, to_ref):
            if ref and not self._git.ref_exists(repo_path, ref):
                return {"ok": False, "error": f"cannot resolve ref {ref!r}"}
        self.raise_and_activate()
        self._show_diff()
        if from_ref or to_ref:
            self._panel_cache["diff"].show_diff(
                repo_path, worktree_path=worktree.path,
                from_ref=from_ref, to_ref=to_ref,
            )
        else:
            self._diff_from_working_tree(worktree.path)
        return {"ok": True}
```

**Done when:** `test_ref_exists_returns_true_for_resolvable_ref`, `test_ref_exists_returns_false_for_unresolvable_ref`, and `test_diff_with_unresolvable_ref_returns_error_reply` all pass; a bad ref yields `{"ok": False}` with the offending ref in the message, and neither `show_diff` nor `_diff_from_working_tree` runs.

---

### Phase 1.6 — regression: no flags still preselects the inferred parent branch
**What it covers:** `worktree-manager diff` with no flags still routes to `_diff_from_working_tree` (Iteration 0's default path) and never calls `show_diff` with explicit refs — the ref-validation added in Phase 1.5 must not disturb the no-flag path.
**Files touched:**
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py) — no change expected; guards this against Phase 1.5's edits.
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py) — add explicit no-flag regression (complements the existing `test_handle_diff_resolves_and_calls_diff_from_working_tree`).

**Tests (Red):**
```python
def test_diff_with_no_flags_still_uses_working_tree_default(app, mock_git, mock_store):
    """diff with no --from/--to still routes to _diff_from_working_tree and never
    calls show_diff with explicit refs, and ref_exists is not consulted."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}

    diff_panel = MagicMock()
    app._panel_cache["diff"] = diff_panel

    with patch.object(app, "raise_and_activate"), \
         patch.object(app, "_show_diff"), \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt:
        request = {"action": "diff", "cwd": "/myrepo/subdir",
                   "from_ref": None, "to_ref": None}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is True
    mock_diff_wt.assert_called_once_with(worktree_path)
    diff_panel.show_diff.assert_not_called()
    mock_git.ref_exists.assert_not_called()
```

**Production code (Green):** None — Phase 1.5's guard is gated behind `if ref and ...`, so with both refs `None` it never calls `ref_exists`, and the existing `else: self._diff_from_working_tree(...)` branch runs unchanged.

**Done when:** `test_diff_with_no_flags_still_uses_working_tree_default` passes: `_diff_from_working_tree` is called once, `show_diff` never, and `ref_exists` is never consulted.

---

## Reference-link check
Every existing file/function/class referenced above is linked relative to this plan file at `/Users/ahmedhhw/repos/dev-tools/`:
- [worktree_manager/cli.py](worktree-manager/worktree_manager/cli.py), and its members [`parse_args`](worktree-manager/worktree_manager/cli.py#L85), [`main`](worktree-manager/worktree_manager/cli.py#L1171), [`App._handle_diff_command`](worktree-manager/worktree_manager/cli.py#L921)
- [worktree_manager/ui/diff_panel.py](worktree-manager/worktree_manager/ui/diff_panel.py), member [`DiffPanel.show_diff`](worktree-manager/worktree_manager/ui/diff_panel.py#L213)
- [worktree_manager/ui/diff_point_selector.py](worktree-manager/worktree_manager/ui/diff_point_selector.py), member [`DiffPointSelector._select_by_ref`](worktree-manager/worktree_manager/ui/diff_point_selector.py#L227)
- [worktree_manager/git_service.py](worktree-manager/worktree_manager/git_service.py), member [`toplevel_for`](worktree-manager/worktree_manager/git_service.py#L45)
- [tests/test_cli_diff_command_qt.py](worktree-manager/tests/test_cli_diff_command_qt.py)

New (unlinked) files introduced by this plan: `tests/test_diff_panel_show_diff_refs_qt.py`, `tests/test_git_service_ref_exists.py`; new method `GitService.ref_exists`.
