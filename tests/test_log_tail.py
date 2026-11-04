import tempfile
import unittest
from pathlib import Path

from appglider.install.appimage import (
    FUSE_HELP,
    LOG_TAIL_BYTES,
    LOG_TAIL_LINES,
    fuse_explanation,
    log_tail,
)


class LogTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "app.log"

    def test_missing_file_gives_nothing(self) -> None:
        self.assertEqual(log_tail(self.path), "")

    def test_keeps_last_non_empty_lines(self) -> None:
        self.path.write_text("\n".join(f"line {n}\n" for n in range(30)))
        lines = log_tail(self.path).splitlines()
        self.assertEqual(len(lines), LOG_TAIL_LINES)
        self.assertEqual(lines[-1], "line 29")

    def test_reads_only_the_end_of_a_large_file(self) -> None:
        self.path.write_bytes(b"first\n" + b"x\n" * LOG_TAIL_BYTES + b"last\n")
        text = log_tail(self.path)
        self.assertNotIn("first", text)
        self.assertTrue(text.endswith("last"))

    def test_undecodable_bytes_are_replaced(self) -> None:
        self.path.write_bytes(b"bad \xff\xfe bytes\n")
        self.assertIn("bad", log_tail(self.path))

    def test_directory_is_tolerated(self) -> None:
        self.assertEqual(log_tail(Path(self.directory.name)), "")


class FuseTests(unittest.TestCase):
    def test_recognizes_fuse_failures(self) -> None:
        for text in (
            "dlopen(): error loading libfuse.so.2",
            "error while loading shared libraries: libfuse.so.2",
            "fuse: device not found, try 'modprobe fuse' first",
            "Cannot mount AppImage, please check your FUSE setup.",
            "sh: fusermount: command not found",
        ):
            with self.subTest(text=text):
                self.assertEqual(fuse_explanation(text), FUSE_HELP)

    def test_ignores_other_failures(self) -> None:
        self.assertIsNone(fuse_explanation("Segmentation fault"))
        self.assertIsNone(fuse_explanation(""))


if __name__ == "__main__":
    unittest.main()
