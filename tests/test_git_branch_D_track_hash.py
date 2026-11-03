"""Local-only deletion preserves unpaired branches without registering a wt repo."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from rwgk_wt import lifecycle, local_branch
from rwgk_wt.core import Repo, WTError

WORKTREE_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


@unittest.skipUnless(
    WORKTREE_ROOT, "Set WT_TEST_WORKTREE_ROOT under /wrk/trees/.wt-tests"
)
class LocalBranchDeletionTests(unittest.TestCase):
    def setUp(self):
        area = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(
            area.is_relative_to(
                (Path(os.environ.get("W", "/wrk")) / "trees/.wt-tests").resolve()
            )
        )
        area.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="local-delete-", dir=area)
        self.addCleanup(self.temporary.cleanup)
        self.area = Path(self.temporary.name)
        self.primary = self.area / "primary"
        self.primary.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Local delete fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.area / "no-hooks"))
        (self.primary / "file").write_text("base\n")
        self.git("add", "file")
        self.git("commit", "--quiet", "-m", "Base")
        self.base = self.git("rev-parse", "HEAD")
        self.branch = "legacy/topic"
        self.tip = self.git(
            "commit-tree", "HEAD^{tree}", "-p", self.base, "-m", "Unmerged local work"
        )
        self.git("update-ref", "refs/heads/" + self.branch, self.tip)
        self.records = self.area / "records"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.records),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(
            ["git", *map(str, args)],
            cwd=cwd or self.primary,
            check=False,
            text=True,
            capture_output=True,
        )
        if check and result.returncode:
            self.fail("Git failed: " + " ".join(map(str, args)) + "\n" + result.stderr)
        return result.stdout.strip()

    def invoke(self, *args):
        return subprocess.run(
            [str(ROOT / "bin/git_branch_D_track_hash.py"), *args],
            cwd=self.primary,
            text=True,
            capture_output=True,
            check=False,
        )

    def saved_records(self):
        return list(self.records.glob("*.json"))

    def assert_branch(self, oid=None, branch=None):
        self.assertEqual(
            self.git("rev-parse", "--verify", "refs/heads/" + (branch or self.branch)),
            oid or self.tip,
        )

    def delete(self):
        repo = Repo(self.primary, initialized=False)
        return local_branch.delete(repo, self.branch)

    def test_unregistered_delete_preserves_recovery_and_target_config_without_remote_changes(
        self,
    ):
        remote = self.area / "origin.git"
        self.git(
            "clone", "--quiet", "--bare", str(self.primary), str(remote), cwd=self.area
        )
        self.git("remote", "add", "origin", str(remote))
        self.git("update-ref", "refs/remotes/origin/topic", self.base)
        self.git("config", "branch." + self.branch + ".remote", "origin")
        self.git("config", "branch." + self.branch + ".merge", "refs/heads/topic")
        self.git(
            "config", "branch." + self.branch + ".description", "Useful branch context"
        )
        self.git("config", "branch.main.description", "Keep this configuration")
        self.git("tag", self.branch, self.base)
        remote_before = self.git("show-ref", cwd=remote)
        (self.primary / "file").write_text("uncommitted main work\n")
        result = self.invoke(self.branch)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertFalse(
            self.git("show-ref", "--verify", "refs/heads/" + self.branch, check=False)
        )
        self.assertEqual(self.git("rev-parse", "refs/tags/" + self.branch), self.base)
        self.assertEqual((self.primary / "file").read_text(), "uncommitted main work\n")
        self.assertEqual(self.git("show-ref", cwd=remote), remote_before)
        self.assertFalse((self.primary / ".git/rwgk-wt.json").exists())
        self.assertEqual(len(self.saved_records()), 1)
        record = json.loads(self.saved_records()[0].read_text())
        self.assertEqual(record["branch"], self.branch)
        self.assertEqual(record["snapshots"][0]["oid"], self.tip)
        recovery = record["snapshots"][0]["recovery_ref"]
        self.assertEqual(self.git("rev-parse", recovery), self.tip)
        self.assertIn(
            ["branch." + self.branch + ".description", "Useful branch context"],
            record["branch_config"],
        )
        self.assertIn(
            ["branch." + self.branch + ".remote", "origin"], record["branch_config"]
        )
        self.assertFalse(
            self.git("config", "--get-regexp", "^branch\\.legacy/topic\\.", check=False)
        )
        self.assertEqual(
            self.git("config", "branch.main.description"), "Keep this configuration"
        )
        self.assertIn(self.tip, result.stdout)
        self.assertIn(str(self.saved_records()[0]), result.stdout)
        self.assertIn(recovery, result.stdout)
        self.git("branch", self.branch, recovery)
        self.assert_branch()

    def test_registered_ad_hoc_branch_does_not_change_catalog_and_managed_branch_is_refused(
        self,
    ):
        repo = Repo(self.primary, initialized=False)
        repo.config.update(key="fixture", primary=str(self.primary), trunk="main")
        repo.branches["main"] = {"path": str(self.primary), "role": "trunk"}
        repo.branches["rwgk/missing"] = {
            "path": str(self.area / "missing"),
            "role": "owned",
        }
        self.git("branch", "rwgk/missing", self.tip)
        repo.save()
        before = repo.state_path.read_bytes()
        result = self.invoke("rwgk/missing")
        self.assertNotEqual(result.returncode, 0)
        self.assert_branch(branch="rwgk/missing")
        self.assertFalse(self.saved_records())
        result = self.invoke(self.branch)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(repo.state_path.read_bytes(), before)

    def test_current_linked_and_missing_worktree_owners_are_refused(self):
        result = self.invoke("main")
        self.assertNotEqual(result.returncode, 0)
        self.assert_branch(self.base, "main")
        linked = self.area / "linked"
        self.git("worktree", "add", "--quiet", str(linked), self.branch)
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    shutil.rmtree(linked)
                result = self.invoke(self.branch)
                self.assertNotEqual(result.returncode, 0)
                self.assert_branch()
                self.assertFalse(self.saved_records())

    def test_detached_conflicted_rebase_still_reserves_original_branch(self):
        linked = self.area / "linked"
        self.git("worktree", "add", "--quiet", str(linked), self.branch)
        (linked / "file").write_text("topic edit\n")
        self.git("commit", "--quiet", "-am", "Topic conflict", cwd=linked)
        tip = self.git("rev-parse", "HEAD", cwd=linked)
        (self.primary / "file").write_text("main edit\n")
        self.git("commit", "--quiet", "-am", "Main conflict")
        self.git("rebase", "main", cwd=linked, check=False)
        self.assertEqual(
            self.git("symbolic-ref", "--quiet", "HEAD", cwd=linked, check=False), ""
        )
        result = self.invoke(self.branch)
        self.assertNotEqual(result.returncode, 0)
        self.assert_branch(tip)
        self.assertFalse(self.saved_records())

    def test_failed_backtracking_write_leaves_branch_and_configuration_intact(self):
        self.git("config", "branch." + self.branch + ".description", "Keep this")
        with (
            mock.patch.object(
                lifecycle, "_atomic_json", side_effect=OSError("disk full")
            ),
            self.assertRaisesRegex((OSError, WTError), "disk full"),
        ):
            self.delete()
        self.assert_branch()
        self.assertEqual(
            self.git("config", "branch." + self.branch + ".description"), "Keep this"
        )

    def test_tip_advanced_after_preservation_is_not_deleted(self):
        advanced = self.git(
            "commit-tree", "HEAD^{tree}", "-p", self.tip, "-m", "Concurrent update"
        )
        original = lifecycle._save_record
        changed = False

        def advance_after_preserving(repo, record):
            nonlocal changed
            original(repo, record)
            if not changed and any(
                snapshot.get("recovery_ref") for snapshot in record["snapshots"]
            ):
                self.git("update-ref", "refs/heads/" + self.branch, advanced, self.tip)
                changed = True

        with (
            mock.patch.object(
                lifecycle, "_save_record", side_effect=advance_after_preserving
            ),
            self.assertRaises(WTError),
        ):
            self.delete()
        self.assertTrue(changed)
        self.assert_branch(advanced)
        self.assertIn(
            self.tip,
            self.git(
                "for-each-ref", "--format=%(objectname)", "refs/rwgk/backtracking"
            ),
        )

    def test_new_worktree_after_preservation_prevents_deletion(self):
        original = lifecycle._save_record
        linked = self.area / "concurrent-owner"
        created = False

        def attach_after_preserving(repo, record):
            nonlocal created
            original(repo, record)
            if not created and any(
                snapshot.get("recovery_ref") for snapshot in record["snapshots"]
            ):
                self.git("worktree", "add", "--quiet", str(linked), self.branch)
                created = True

        with (
            mock.patch.object(
                lifecycle, "_save_record", side_effect=attach_after_preserving
            ),
            self.assertRaises(WTError),
        ):
            self.delete()
        self.assertTrue(created)
        self.assert_branch()
        self.assertEqual(
            self.git("symbolic-ref", "--short", "HEAD", cwd=linked), self.branch
        )

    def test_revision_expressions_tags_and_symbolic_branch_refs_cannot_be_deleted(self):
        self.git("tag", "only-tag", self.base)
        self.git("symbolic-ref", "refs/heads/alias", "refs/heads/" + self.branch)
        for value in (
            self.branch + "~1",
            "only-tag",
            "refs/heads/" + self.branch,
            "alias",
        ):
            with self.subTest(value=value):
                result = self.invoke(value)
                self.assertNotEqual(result.returncode, 0)
                self.assert_branch()
                self.assertEqual(self.git("rev-parse", "refs/tags/only-tag"), self.base)
                self.assertEqual(
                    self.git("symbolic-ref", "refs/heads/alias"),
                    "refs/heads/" + self.branch,
                )
        self.assertFalse(self.saved_records())


if __name__ == "__main__":
    unittest.main()
