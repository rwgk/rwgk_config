"""wt codex delegates before opening or locking repository metadata."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import cli as logbook
from rwgk_wt import cli


class WtCodexTests(unittest.TestCase):
    def test_codex_delegates_without_repo_construction_or_lock(self):
        with (
            patch.object(cli, "Repo") as repo,
            patch.object(logbook, "main", return_value=0) as lookup,
        ):
            self.assertEqual(cli.main(["codex"]), 0)
        repo.assert_not_called()
        lookup.assert_called_once_with(["here"])

    def test_list_and_json_are_passed_through(self):
        with (
            patch.object(cli, "Repo") as repo,
            patch.object(logbook, "main", return_value=1) as lookup,
        ):
            self.assertEqual(cli.main(["codex", "--list", "--json"]), 1)
        repo.assert_not_called()
        lookup.assert_called_once_with(["here", "--json", "--list"])

    def test_print_path_rejected_before_launch(self):
        with (
            patch.object(cli, "Repo") as repo,
            patch.object(logbook, "main") as lookup,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(cli.main(["codex", "--print-path"]), 1)
        repo.assert_not_called()
        lookup.assert_not_called()
        self.assertIn("navigation path", stderr.getvalue())

    def test_named_resume_and_cwd_delegate_without_repository(self):
        with (
            patch.object(cli, "Repo") as repo,
            patch.object(logbook, "main", return_value=0) as lookup,
        ):
            self.assertEqual(
                cli.main(["codex", "wtdesign", "--cwd", "/chosen", "--list"]), 0
            )
        repo.assert_not_called()
        lookup.assert_called_once_with(
            ["resume", "wtdesign", "--cwd", "/chosen", "--list"]
        )

    def test_nickname_operations_delegate_without_repository(self):
        for arguments, options in (
            (["nickname"], ["nickname"]),
            (["nickname", "wtdesign"], ["nickname", "wtdesign"]),
            (
                ["nickname", "wtdesign", "session-id", "--json"],
                ["nickname", "wtdesign", "session-id", "--json"],
            ),
            (
                ["nickname", "--delete", "wtdesign"],
                ["nickname", "wtdesign", "--delete"],
            ),
        ):
            with (
                self.subTest(arguments=arguments),
                patch.object(cli, "Repo") as repo,
                patch.object(logbook, "main", return_value=0) as lookup,
            ):
                self.assertEqual(cli.main(arguments), 0)
                repo.assert_not_called()
                lookup.assert_called_once_with(options)

    def test_nickname_print_path_rejected_before_writing(self):
        with (
            patch.object(cli, "Repo") as repo,
            patch.object(logbook, "main") as lookup,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(cli.main(["nickname", "wtdesign", "--print-path"]), 1)
        repo.assert_not_called()
        lookup.assert_not_called()
        self.assertIn("navigation path", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
