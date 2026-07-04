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
