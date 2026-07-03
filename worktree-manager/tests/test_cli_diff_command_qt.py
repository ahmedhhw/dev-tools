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
