"""Plain wording for failures: OSError text, GitHub errors and the Update all summary."""

import errno
import unittest
from unittest.mock import patch

from appglider.github import http
from appglider.ui.errors import describe_error
from appglider.ui.window import update_all_message


class DescribeErrorTests(unittest.TestCase):
    def test_common_errno_values_get_a_sentence_and_a_next_step(self) -> None:
        cases = {
            errno.ENOSPC: "not enough free disk space",
            errno.EDQUOT: "not enough free disk space",
            errno.EACCES: "not allowed",
            errno.EPERM: "not allowed",
            errno.EROFS: "read-only",
            errno.ENAMETOOLONG: "too long",
        }
        for code, words in {
            errno.ENOENT: "could not be found",
            errno.ENOTDIR: "could not be found",
            errno.EBUSY: "in use",
        }.items():
            with self.subTest(code=code):
                text = describe_error(OSError(code, "raw", "/nowhere/apps/file.part"))
                self.assertIn(words, text)
                self.assertNotIn("raw", text)
                self.assertNotIn("Errno", text)
        for code, words in cases.items():
            with self.subTest(code=code):
                text = describe_error(OSError(code, "raw", "/nowhere/apps/file.part"))
                self.assertIn(words, text)
                self.assertIn("/nowhere/apps", text)
                self.assertIn("Settings → Storage", text)
                self.assertNotIn("raw", text)

    def test_a_missing_file_name_leaves_the_folder_out(self) -> None:
        text = describe_error(OSError(errno.ENOSPC, "No space left on device"))
        self.assertTrue(text.startswith("There is not enough free disk space."))

    def test_other_errors_keep_their_text(self) -> None:
        self.assertEqual(describe_error(ValueError("plain")), "plain")

    def test_an_unknown_os_error_gets_a_short_reason_not_the_raw_errno_text(self) -> None:
        text = describe_error(OSError(errno.EIO, "Input/output error", "/x/y"))
        self.assertEqual(
            text,
            "Something went wrong: input/output error. "
            "Try again; if it keeps happening, check the log.",
        )
        self.assertNotIn("Errno", describe_error(OSError("odd")))


class GitHubTextTests(unittest.TestCase):
    def test_rate_limit_rounds_minutes_up_and_reassures(self) -> None:
        with patch.object(http.time, "time", return_value=1000.0):
            text = str(http.RateLimited("api.github.com", 1000.0 + 61))
            self.assertIn("about 2 minutes", text)
            self.assertIn("installed keep working", text)
            self.assertIn("about 1 minute.", str(http.RateLimited("api.github.com", 1030.0)))
            self.assertIn("about 1 minute.", str(http.RateLimited("api.github.com", 1000.0)))
            self.assertIn("about 10 minutes", str(http.RateLimited("h", 1000.0 + 600)))

    def test_server_errors_blame_github_and_suggest_waiting(self) -> None:
        for code in (500, 502, 503):
            text = http.status_text(code)
            self.assertIn("problem on its side", text)
            self.assertIn("try again later", text)
        self.assertEqual(http.status_text(418), "GitHub answered HTTP 418.")

    def test_unreachable_keeps_its_old_text(self) -> None:
        self.assertEqual(str(http.Unreachable()), http.UNREACHABLE)
        self.assertIsInstance(http.Unreachable(), ValueError)


class UpdateAllMessageTests(unittest.TestCase):
    def test_plural_and_empty_results(self) -> None:
        self.assertEqual(update_all_message(1, 0), "Updated 1 app.")
        self.assertEqual(update_all_message(2, 0), "Updated 2 apps.")
        self.assertEqual(update_all_message(0, 0), "No apps were updated.")
        self.assertEqual(update_all_message(1, 1), "Updated 1 app. 1 could not be updated.")

    def test_a_stop_reason_comes_first(self) -> None:
        self.assertEqual(
            update_all_message(0, 0, "Could not reach GitHub."),
            "Could not reach GitHub. No apps were updated.",
        )


if __name__ == "__main__":
    unittest.main()


class NetworkErrorTests(unittest.TestCase):
    def test_connection_failures_say_github_could_not_be_reached(self) -> None:
        from urllib.error import URLError

        from appglider.ui.errors import OFFLINE, describe_error

        for error in (
            URLError("Name or service not known"),
            TimeoutError(),
            OSError(errno.ENETUNREACH, "Network is unreachable"),
        ):
            self.assertEqual(describe_error(error), OFFLINE)

    def test_own_messages_without_an_errno_are_kept(self) -> None:
        from appglider.ui.errors import describe_error

        self.assertEqual(
            describe_error(OSError("The file is not an AppImage.")), "The file is not an AppImage."
        )
