"""Unix-socket IPC server — emits command_received into the Qt main thread."""
import json
import socket
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class IpcServer(QObject):
    command_received = Signal(dict, object)  # (request: dict, conn: socket)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None

    def start(self, sock_path: str) -> None:
        path = Path(sock_path)
        path.unlink(missing_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            try:
                f = conn.makefile("r", encoding="utf-8")
                line = f.readline()
                if line:
                    request = json.loads(line)
                    self.command_received.emit(request, conn)
            except Exception:
                conn.close()
