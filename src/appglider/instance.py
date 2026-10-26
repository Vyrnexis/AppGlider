"""Keep one AppGlider running per data directory and let a second launch wake it."""

import fcntl
import hashlib
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import IO

from PySide6.QtCore import QObject, QSocketNotifier, Signal

CONNECT_TIMEOUT = 1.0
AFTER_UPDATE_WAIT = 15.0
AFTER_UPDATE_INTERVAL = 0.15


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        digest = hashlib.sha256(str(directory).encode()).hexdigest()[:16]
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        self.socket_path = runtime / f"appglider-{digest}.sock"
        self._lock: IO[bytes] | None = None
        self._server: socket.socket | None = None
        self._notifier: QSocketNotifier | None = None

    def acquire(self, wake: bool = True) -> bool:
        """Return True when this process is the only instance and now owns the lock.

        When another instance holds it, that one is asked to show its window unless
        ``wake`` is False (a quiet autostart launch must not raise the window).
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = (self.directory / "appglider.lock").open("ab")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            if wake:
                self._wake_existing()
            return False
        self._lock = lock
        self._listen()
        return True

    def acquire_after_update(self, wake: bool = True) -> bool:
        """Like ``acquire``, but first wait for the previous AppGlider to finish quitting.

        A restart after an update starts the new instance while the old one is still closing.
        The lock is tried quietly for a few seconds; if it never frees, behave as usual.
        """
        deadline = time.monotonic() + AFTER_UPDATE_WAIT
        while time.monotonic() < deadline:
            if self.acquire(wake=False):
                return True
            time.sleep(AFTER_UPDATE_INTERVAL)
        return self.acquire(wake=wake)

    def release(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier = None
        if self._server is not None:
            self._server.close()
            self._server = None
            self.socket_path.unlink(missing_ok=True)
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _listen(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket_path.unlink(missing_ok=True)
            server.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            server.listen(4)
            server.setblocking(False)
        except OSError:
            server.close()
            return  # running without wake-up support is better than not running
        self._server = server
        self._notifier = QSocketNotifier(server.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._accept)

    def _wake_existing(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(CONNECT_TIMEOUT)
            try:
                client.connect(str(self.socket_path))
                client.sendall(b"show")
            except OSError:
                return

    def _accept(self) -> None:
        while self._server is not None:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            connection.close()
            self.activated.emit()
