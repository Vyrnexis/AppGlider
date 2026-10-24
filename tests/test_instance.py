"""A second launch wakes the running instance instead of opening another window."""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest

from appglider.instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def test_second_launch_is_refused_and_wakes_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first, second = SingleInstance(Path(temp)), SingleInstance(Path(temp))
            woken: list[bool] = []
            first.activated.connect(lambda: woken.append(True))
            self.assertTrue(first.acquire())
            self.addCleanup(first.release)
            self.assertFalse(second.acquire())
            for _ in range(20):
                if woken:
                    break
                QTest.qWait(50)
            self.assertEqual(woken, [True])

    def test_quiet_second_launch_does_not_wake_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first, second = SingleInstance(Path(temp)), SingleInstance(Path(temp))
            woken: list[bool] = []
            first.activated.connect(lambda: woken.append(True))
            self.assertTrue(first.acquire())
            self.addCleanup(first.release)
            self.assertFalse(second.acquire(wake=False))
            QTest.qWait(300)
            self.assertEqual(woken, [])

    def test_lock_is_released_and_directories_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = SingleInstance(Path(one))
            self.assertTrue(first.acquire())
            other = SingleInstance(Path(two))
            self.assertTrue(other.acquire())
            other.release()
            first.release()
            again = SingleInstance(Path(one))
            self.assertTrue(again.acquire())
            again.release()


if __name__ == "__main__":
    unittest.main()
