"""Tests for single-instance launch and focus dispatch."""
import json
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from worktree_manager.cli import instance_is_running, main, socket_path, try_send_command


# ── instance_is_running ──────────────────────────────────────────────────────

def test_instance_is_running_returns_false_when_no_socket(tmp_path):
    sock_path = tmp_path / "no_such.sock"
    assert instance_is_running(sock_path) is False


def test_second_main_sends_focus_and_skips_qapplication():
    """Second bare launch: sends focus command, never builds QApplication."""
    sent = []

    def fake_try_send(sock_path, payload):
        sent.append(payload)
        return {"ok": True}

    with patch("worktree_manager.cli.instance_is_running", return_value=True), \
         patch("worktree_manager.cli.try_send_command", side_effect=fake_try_send), \
         patch("worktree_manager.cli.QApplication") as mock_qapp, \
         patch("sys.argv", ["worktree-manager"]):
        main()

    assert sent == [{"action": "focus"}]
    mock_qapp.assert_not_called()


def test_instance_is_running_removes_stale_socket_and_returns_false():
    sock_path = Path("/tmp/wm_test_stale.sock")
    sock_path.unlink(missing_ok=True)
    # Create a socket file but don't listen on it (stale)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.close()  # bound but not listening → ConnectionRefusedError on connect
    assert sock_path.exists()
    assert instance_is_running(sock_path) is False
    # The stale file must have been removed
    assert not sock_path.exists()


def test_instance_is_running_returns_true_with_listening_socket():
    sock_path = Path("/tmp/wm_test_listen.sock")
    sock_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    try:
        assert instance_is_running(sock_path) is True
        # server still accepting (connect-and-close doesn't break it)
        assert sock_path.exists()
    finally:
        server.close()
        sock_path.unlink(missing_ok=True)
