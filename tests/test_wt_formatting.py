"""Bound filename reports while preserving their beginning and end."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt.formatting import format_file_list, summarize_lines


class FilenameFormattingTests(unittest.TestCase):
    def test_lists_of_at_most_ten_lines_are_unchanged(self):
        for count in (0, 6, 10):
            with self.subTest(count=count):
                lines = [f"line {number}" for number in range(count)]
                self.assertEqual(summarize_lines(lines), lines)

    def test_long_lists_keep_three_at_each_end_and_report_exact_omitted_count(self):
        for count in (11, 100):
            with self.subTest(count=count):
                lines = [f"line {number}" for number in range(1, count + 1)]
                self.assertEqual(
                    summarize_lines(lines),
                    [*lines[:3], f"... {count - 6} lines omitted ...", *lines[-3:]],
                )

    def test_file_list_retains_heading_and_one_filename_per_line(self):
        paths = [
            f"venv/site-packages/package/file{number:03d}.py" for number in range(100)
        ]
        self.assertEqual(
            format_file_list("Unknown files:", paths),
            "\n".join(
                ["Unknown files:", *paths[:3], "... 94 lines omitted ...", *paths[-3:]]
            ),
        )


if __name__ == "__main__":
    unittest.main()
