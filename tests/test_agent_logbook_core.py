"""Storage concurrency, resource identity and host/context preservation."""

from __future__ import annotations

import concurrent.futures
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import context, resources, store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-logbook-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "index"

    def test_concurrent_writers_publish_complete_distinct_events(self):
        def write(number):
            return store.append_event(
                {"session_id": str(number), "note": "complete " + str(number)},
                self.directory,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            paths = list(executor.map(write, range(100)))
        self.assertEqual(len(set(paths)), 100)
        self.assertEqual(len(store.load_sessions(self.directory)), 100)
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertFalse(list(self.directory.rglob(".event-*")))

    def test_later_partial_events_preserve_prior_metadata_and_locations(self):
        store.append_event(
            {
                "session_id": "one",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "host": "host-a",
                "cwd": "/work/old",
                "transcript_path": "/logs/one.jsonl",
                "title": "original task",
                "note": "created",
                "links": [
                    {
                        "key": "github:owner/repo#1",
                        "relation": "created",
                        "display": "PR",
                    }
                ],
            },
            self.directory,
        )
        store.append_event(
            {
                "session_id": "one",
                "timestamp": "2026-01-02T00:00:00+00:00",
                "host": "host-b",
                "cwd": "/work/new",
                "note": "reviewed",
                "title": "",
                "links": [
                    {"key": "github:owner/repo#1", "relation": "reviewed"},
                    {"key": "github:owner/repo#1", "relation": "created"},
                ],
            },
            self.directory,
        )
        session = store.load_sessions(self.directory)[0]
        self.assertEqual(session["title"], "original task")
        self.assertEqual(session["transcript_path"], "/logs/one.jsonl")
        self.assertEqual(session["hosts"], ["host-a", "host-b"])
        self.assertEqual(len(session["locations"]), 2)
        self.assertEqual(session["notes"], ["created", "reviewed"])
        self.assertEqual(len(session["links"]), 2)
        self.assertEqual(session["links"][0]["last_seen"], session["updated_at"])

    def test_agents_do_not_overwrite_each_other_and_reads_do_not_create_store(self):
        self.assertEqual(store.load_sessions(self.directory), [])
        self.assertFalse(self.directory.exists())
        for agent in ("codex", "claude"):
            store.append_event({"session_id": "same", "agent": agent}, self.directory)
        self.assertEqual(len(store.load_sessions(self.directory)), 2)

    def test_malformed_events_fail_and_temporary_files_are_ignored(self):
        path = store.append_event({"session_id": "good"}, self.directory)
        (path.parent / "broken.json").write_text('{"partial":')
        (path.parent / "wrong-shape.json").write_text("[]")
        (path.parent / ".event-interrupted").write_text('{"partial":')
        with self.assertRaisesRegex(ValueError, "broken.json"):
            store.load_sessions(self.directory)
        self.assertTrue((path.parent / "broken.json").exists())

    def test_default_directory_selection_and_explicit_unusable_store(self):
        base = Path(self.temporary.name)
        h, w = base / "h", base / "w"
        h.mkdir()
        w.mkdir()
        with patch.dict(os.environ, {"H": str(h), "W": str(w)}, clear=True):
            self.assertEqual(store.default_directory(), h / "agent-logbook")
            h.rmdir()
            with self.assertRaisesRegex(ValueError, "Configured .H"):
                store.default_directory()
            del os.environ["H"]
            self.assertEqual(store.default_directory(), w / "agent-logbook")
            blocked = base / "file"
            blocked.write_text("not a directory")
            os.environ["AGENT_LOGBOOK_DIR"] = str(blocked)
            with self.assertRaises(OSError):
                store.append_event({"session_id": "one"})
            self.assertFalse((w / "agent-logbook").exists())

    def test_unreadable_shard_fails_instead_of_returning_partial_index(self):
        path = store.append_event({"session_id": "hidden"}, self.directory)
        shard = path.parent
        shard.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                store.load_sessions(self.directory)
        finally:
            shard.chmod(0o700)

    def test_non_directory_event_tree_fails_instead_of_looking_empty(self):
        self.directory.mkdir()
        (self.directory / "events").write_text("broken")
        with self.assertRaises(NotADirectoryError):
            store.load_sessions(self.directory)

    def test_invalid_links_type_names_bad_event_as_value_error(self):
        path = store.append_event({"session_id": "bad"}, self.directory)
        value = json.loads(path.read_text())
        value["links"] = None
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, path.name):
            store.load_sessions(self.directory)

    def test_invalid_records_fail_before_creating_store(self):
        with self.assertRaises(ValueError):
            store.append_event({"session_id": "../bad\n", "agent": []}, self.directory)
        self.assertFalse(self.directory.exists())


class ResourceTests(unittest.TestCase):
    def test_github_pr_issue_and_comment_urls_share_identity(self):
        expected = "github:owner/repo#123"
        for value in (
            "https://github.com/Owner/Repo/pull/123",
            "https://github.com/owner/repo/issues/123#issuecomment-10",
            "https://github.com/owner/repo/pull/123/files",
            "Owner/Repo#123",
        ):
            self.assertEqual(resources.normalize(value)["key"], expected)
        self.assertNotEqual(
            resources.normalize("https://github.com/other/repo/pull/123")["key"],
            expected,
        )

    def test_slack_reply_query_points_to_root_thread(self):
        root = "https://work.slack.com/archives/C123/p1700000000123456"
        reply = (
            "https://work.slack.com/archives/C123/p1700000001654321"
            "?thread_ts=1700000000.123456&cid=C123"
        )
        self.assertEqual(
            resources.normalize(root)["key"], resources.normalize(reply)["key"]
        )

    def test_nvbugs_and_arbitrary_urls(self):
        for value in (
            "nvbug 123",
            "nvbug:123",
            "https://nvbugs.nvidia.com/123",
            "https://nvbugs.nvidia.com/bug.aspx?bugid=123",
        ):
            self.assertEqual(resources.normalize(value)["key"], "nvbug:123")
        link = resources.normalize("https://user:password@example.com/doc?id=1#section")
        self.assertEqual(link["key"], "url:https://example.com/doc?id=1#section")

    def test_outside_repository_preserves_file_identity_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.txt"
            result = resources.normalize("artifact.txt", directory)
            self.assertEqual(result["key"], "file:" + str(path.resolve()))
            self.assertEqual(
                context.workspace(directory)["keys"][0]["kind"], "workspace"
            )

    def test_transport_differences_normalize_without_credentials(self):
        urls = (
            "git@github.com:Owner/Repo.git",
            "https://token@github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
        )
        self.assertEqual(
            {context.repository_identity(url) for url in urls},
            {"github.com/owner/repo"},
        )


class RepositoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-logbook-repo-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/rwgk/example")
        self.git("remote", "add", "origin", "git@github.com:personal/project.git")
        self.subdirectory = self.root / "src"
        self.subdirectory.mkdir()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def catalog(self, path=None):
        value = {
            "version": 1,
            "config": {"upstream_repo": "Canonical/Project"},
            "branches": {
                "rwgk/example": {
                    "path": str(path or self.root),
                    "pr": {
                        "base_repo": "Canonical/Project",
                        "number": 10,
                        "url": "https://github.com/Canonical/Project/pull/10",
                    },
                }
            },
        }
        (self.root / ".git" / "rwgk-wt.json").write_text(json.dumps(value))

    def test_owned_wt_pr_uses_base_repository_and_subdirectory_context(self):
        self.catalog()
        current = context.workspace(self.subdirectory)
        self.assertEqual(current["repo"], "github.com/canonical/project")
        self.assertEqual(current["branch"], "rwgk/example")
        self.assertEqual(current["keys"][1]["key"], "github:canonical/project#10")
        self.assertEqual(
            resources.normalize("future.py", self.subdirectory)["key"],
            "file:github.com/canonical/project:src/future.py",
        )

    def test_absolute_filename_uses_its_repository_from_another_directory(self):
        self.catalog()
        filename = self.subdirectory / "future.py"
        with tempfile.TemporaryDirectory() as unrelated:
            self.assertEqual(
                resources.normalize(str(filename), unrelated)["key"],
                "file:github.com/canonical/project:src/future.py",
            )

    def test_malformed_event_fields_fail_without_partial_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = store.append_event({"session_id": "good"}, directory)
            for value in (
                {"version": 1, "session_id": "bad", "agent": []},
                {"version": 1, "session_id": "bad", "timestamp": []},
                {
                    "version": 1,
                    "session_id": "bad",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "links": [{"key": "file:foo", "relation": []}],
                },
            ):
                broken = path.parent / "broken.json"
                broken.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "broken.json"):
                    store.load_sessions(directory)

    def test_stale_owner_does_not_associate_wrong_pr(self):
        self.catalog(self.root / "somewhere-else")
        self.assertEqual(context.workspace(self.root)["pr_url"], "")

    def test_fork_transport_without_catalog_and_malformed_catalog(self):
        path = self.root / ".git" / "rwgk-wt.json"
        path.write_text("invalid")
        self.assertEqual(
            context.workspace(self.root)["repo"], "github.com/personal/project"
        )
        path.write_text('{"version": 1, "config": [], "branches": []}')
        self.assertEqual(
            context.workspace(self.root)["repo"], "github.com/personal/project"
        )


if __name__ == "__main__":
    unittest.main()
