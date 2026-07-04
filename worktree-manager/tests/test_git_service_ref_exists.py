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
