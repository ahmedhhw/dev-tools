"""Tests for IpcServer bind/accept/emit."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from worktree_manager.ipc_server import IpcServer


def test_ipc_server_start_binds_and_emits_command(qtbot):
    sock_path = Path("/tmp/wm_test_ipc_server.sock")
    sock_path.unlink(missing_ok=True)

    received = []
    event = threading.Event()

    server = IpcServer()

    def on_command(request, conn):
        received.append((request, conn))
        # send a reply so the client doesn't hang
        try:
            f = conn.makefile("w", encoding="utf-8")
            f.write(json.dumps({"ok": True}) + "\n")
            f.flush()
        except Exception:
            pass
        event.set()

    server.command_received.connect(on_command)
    server.start(str(sock_path))

    # give the accept thread a moment
    time.sleep(0.05)

    # client sends a command
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(sock_path))
    payload = {"action": "focus"}
    client.sendall((json.dumps(payload) + "\n").encode())
    client.close()

    # process Qt events until signal fires or timeout
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    deadline = time.time() + 2.0
    while not event.is_set() and time.time() < deadline:
        if app:
            app.processEvents()
        time.sleep(0.01)

    assert event.is_set(), "command_received was never emitted"
    assert len(received) == 1
    assert received[0][0] == {"action": "focus"}

    sock_path.unlink(missing_ok=True)
