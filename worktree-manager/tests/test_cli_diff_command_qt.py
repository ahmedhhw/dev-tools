"""Tests for parse_args diff subcommand and handle_diff resolution/dispatch."""
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from worktree_manager.cli import parse_args, App
from worktree_manager.models import WorktreeModel


# ── parse_args ───────────────────────────────────────────────────────────────

def test_parse_args_diff_subcommand_no_flags():
    args = parse_args(["diff"])
    assert args.command == "diff"
    assert args.from_ref is None
    assert args.to_ref is None


def test_parse_args_bare_repo_path_no_regression():
    args = parse_args(["/some/path"])
    assert args.repo_path == "/some/path"
    assert args.command is None


# ── App fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_store():
    store = MagicMock()
    store.all_repos.return_value = {}
    store.get_ui_pref.side_effect = lambda key, default=None: default
    store.get_experimental_features.return_value = False
    return store


@pytest.fixture
def mock_git():
    git = MagicMock()
    git.list_worktrees.return_value = []
    git.toplevel_for.return_value = None
    return git


@pytest.fixture
def app(qtbot, mock_store, mock_git, monkeypatch):
    monkeypatch.setattr("worktree_manager.cli.ConfigStore", lambda *a, **kw: mock_store)
    monkeypatch.setattr("worktree_manager.cli.GitService", lambda *a, **kw: mock_git)
    monkeypatch.setattr("worktree_manager.cli.WorktreeMgmtViewModel", MagicMock())
    monkeypatch.setattr("worktree_manager.cli.BranchMgmtViewModel", MagicMock())
    # Patch IpcServer so it doesn't try to bind a real socket in tests
    mock_ipc = MagicMock()
    monkeypatch.setattr("worktree_manager.cli.IpcServer", lambda *a, **kw: mock_ipc)
    a = App()
    qtbot.addWidget(a)
    yield a


# ── focus command: App.raise_and_activate ────────────────────────────────────

def test_app_raise_and_activate_on_focus(app):
    """App raises and activates the window in response to focus command."""
    conn = MagicMock()
    conn.makefile.return_value = MagicMock()
    with patch.object(app, "raise_and_activate") as mock_raise:
        app._handle_ipc_command({"action": "focus"}, conn)
    mock_raise.assert_called_once()


# ── handle_diff: error cases ─────────────────────────────────────────────────

def test_handle_diff_error_when_not_in_git_repo(app, mock_git):
    mock_git.toplevel_for.return_value = None
    conn = MagicMock()
    conn.makefile.return_value = MagicMock()
    request = {"action": "diff", "cwd": "/not/a/git/dir", "from_ref": None, "to_ref": None}
    reply = app._handle_diff_command(request)
    assert reply["ok"] is False
    assert "git repository" in reply["error"]


def test_handle_diff_error_when_cwd_not_in_any_tracked_repo(app, mock_git, mock_store):
    mock_git.toplevel_for.return_value = "/some/repo"
    mock_store.all_repos.return_value = {"/other/repo": MagicMock()}
    mock_git.list_worktrees.return_value = []  # no worktrees match /some/repo
    request = {"action": "diff", "cwd": "/some/repo", "from_ref": None, "to_ref": None}
    reply = app._handle_diff_command(request)
    assert reply["ok"] is False
    assert "tracked" in reply["error"]


# ── handle_diff: happy path ───────────────────────────────────────────────────

def test_handle_diff_resolves_and_calls_diff_from_working_tree(app, mock_git, mock_store):
    """handle_diff resolves cwd → toplevel → tracked repo → WorktreeModel,
    raises window, shows Diff panel, calls _diff_from_working_tree (no explicit refs)."""
    worktree_path = "/myrepo"
    mock_git.toplevel_for.return_value = worktree_path
    wt = MagicMock(spec=WorktreeModel)
    wt.path = worktree_path
    mock_git.list_worktrees.return_value = [wt]
    mock_store.all_repos.return_value = {"/myrepo": MagicMock()}

    with patch.object(app, "raise_and_activate") as mock_raise, \
         patch.object(app, "_diff_from_working_tree") as mock_diff_wt, \
         patch.object(app, "_show_diff"):
        request = {"action": "diff", "cwd": "/myrepo/subdir", "from_ref": None, "to_ref": None}
        reply = app._handle_diff_command(request)

    assert reply["ok"] is True
    mock_raise.assert_called_once()
    mock_diff_wt.assert_called_once_with(worktree_path)


# ── handle_diff: explicit refs (Iteration 1) ──────────────────────────────────

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


# ── diff CLI: no running instance ─────────────────────────────────────────────

def test_diff_cli_no_running_instance_prints_error_and_exits(capsys):
    """worktree-manager diff with no running instance → stderr + exit 1."""
    from worktree_manager.cli import main
    with patch("worktree_manager.cli.try_send_command", return_value=None), \
         patch("sys.argv", ["worktree-manager", "diff"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "No running instance" in err
