"""Nickname pointers remain exact and publish each replacement atomically."""

from __future__ import annotations

import concurrent.futures
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import nicknames


class NicknameStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nickname-store-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "logbook"
        self.original = str(uuid.uuid4())
        self.fork = str(uuid.uuid4())

    def test_reads_and_unknown_deletion_do_not_create_store(self):
        self.assertEqual(nicknames.load(self.root), {})
        self.assertIsNone(nicknames.get("wtdesign", self.root))
        with self.assertRaisesRegex(ValueError, "Unknown session nickname"):
            nicknames.delete("wtdesign", self.root)
        self.assertFalse(self.root.exists())

    def test_assignment_reassignment_and_deletion(self):
        self.assertIsNone(
            nicknames.assign("wtdesign", self.original.upper(), self.root)
        )
        pointer = self.root / "nicknames" / "wtdesign.json"
        self.assertEqual(nicknames.get("wtdesign", self.root), self.original)
        identity = pointer.stat().st_ino
        self.assertEqual(
            nicknames.assign("wtdesign", self.original, self.root), self.original
        )
        self.assertEqual(pointer.stat().st_ino, identity)
        self.assertEqual(
            nicknames.assign("wtdesign", self.fork, self.root), self.original
        )
        self.assertEqual(nicknames.get("wtdesign", self.root), self.fork)
        self.assertEqual(nicknames.delete("wtdesign", self.root), self.fork)
        self.assertEqual(nicknames.load(self.root), {})

    def test_fork_and_other_nickname_leave_original_mapping_unchanged(self):
        nicknames.assign("wtdesign", self.original, self.root)
        nicknames.assign("experiment", self.fork, self.root)
        self.assertEqual(
            nicknames.load(self.root),
            {"experiment": self.fork, "wtdesign": self.original},
        )
        nicknames.assign("experiment", self.original, self.root)
        self.assertEqual(nicknames.get("wtdesign", self.root), self.original)

    def test_names_and_session_ids_validate_before_creating_store(self):
        for name in ("../escape", "", "two words", "--delete"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                nicknames.assign(name, self.original, self.root)
        with self.assertRaisesRegex(ValueError, "session UUID"):
            nicknames.assign("wtdesign", "not-a-session", self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(nicknames.validate_name("Wt.design-2_3"), "Wt.design-2_3")

    def test_concurrent_different_names_are_complete_and_private(self):
        def write(number):
            name = f"task-{number:02d}"
            target = str(uuid.uuid4())
            nicknames.assign(name, target, self.root)
            return name, target

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            expected = dict(executor.map(write, range(24)))
        self.assertEqual(nicknames.load(self.root), expected)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "nicknames").stat().st_mode), 0o700)
        for path in (self.root / "nicknames").iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text())["name"], path.stem)

    def test_failed_replacement_preserves_old_pointer_and_cleans_temporary(self):
        nicknames.assign("wtdesign", self.original, self.root)
        with (
            patch.object(nicknames.os, "replace", side_effect=OSError("write failed")),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            nicknames.assign("wtdesign", self.fork, self.root)
        self.assertEqual(nicknames.get("wtdesign", self.root), self.original)
        self.assertEqual(
            [path.name for path in (self.root / "nicknames").iterdir()],
            ["wtdesign.json"],
        )

    def test_corrupt_pointers_stop_lookup_and_listing(self):
        nicknames.assign("wtdesign", self.original, self.root)
        pointer = self.root / "nicknames" / "wtdesign.json"
        pointer.write_text('{"version": 1, "name": "other"}')
        for operation in (
            lambda: nicknames.get("wtdesign", self.root),
            lambda: nicknames.load(self.root),
        ):
            with self.assertRaisesRegex(ValueError, "wtdesign.json"):
                operation()
        pointer.write_text("{")
        with self.assertRaisesRegex(ValueError, "wtdesign.json"):
            nicknames.get("wtdesign", self.root)

    def test_selected_logbook_directory_is_shared_with_existing_store(self):
        with patch.dict(os.environ, {"AGENT_LOGBOOK_DIR": str(self.root)}):
            nicknames.assign("wtdesign", self.original)
            self.assertEqual(nicknames.get("wtdesign"), self.original)
            self.assertEqual(nicknames.load(), {"wtdesign": self.original})


if __name__ == "__main__":
    unittest.main()
