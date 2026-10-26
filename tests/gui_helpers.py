"""Dispose test windows safely, including after assertions leave a task unfinished."""

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtWidgets import QMessageBox


def close_window(window):
    window.quitting = True
    window.close_after_task = True
    window.close_after_release = True
    window.checks.stop()
    for task in (window.task, window.self_task, *window.checks.tasks.values()):
        if task is not None:
            task.cancel.set()
    with patch(
        "appglider.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
    ):
        if not QThreadPool.globalInstance().waitForDone(5000):
            raise AssertionError("Background worker did not stop during GUI test cleanup")
        QCoreApplication.processEvents()
        # Mocked workers never emit completion signals; real workers have stopped above.
        window.task = None
        window.self_task = None
        window.checks.tasks.clear()
        window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
