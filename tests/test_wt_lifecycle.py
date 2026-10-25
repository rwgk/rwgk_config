#!/usr/bin/env python3
"""Exercise lifecycle durability and leases against isolated real Git repositories.

Set WT_TEST_WORKTREE_ROOT to an authorized disposable directory outside /tmp.
All fixture repositories, worktrees and remotes are removed after each test.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import core, lifecycle

WORKTREE_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


@unittest.skipUnless(
    WORKTREE_ROOT,
    "Set WT_TEST_WORKTREE_ROOT outside /tmp for real worktree lifecycle tests",
)
class LifecycleTests(unittest.TestCase):
    def setUp(self):
        root = Path(WORKTREE_ROOT).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="lifecycle-", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.primary = self.root / "primary"
        self.upstream = self.root / "upstream.git"
        self.origin = self.root / "origin.git"
        for directory in (self.upstream, self.origin):
            self.git("init", "--bare", "--quiet", str(directory), cwd=self.root)
        self.git(
            "init", "--initial-branch=main", "--quiet", str(self.primary), cwd=self.root
        )
        self.git("config", "user.name", "Lifecycle fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.primary / ".gitignore").write_text("build/\ncustom-secret/\n")
        (self.primary / "file.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "upstream", str(self.upstream))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "--quiet", "upstream", "main")
        self.repo = core.Repo(self.primary, initialized=False)
        self.repo.config.update(
            key="project",
            primary=str(self.primary),
            trunk="main",
            upstream_remote="upstream",
            upstream_repo="upstream/project",
            publish_remote="upstream",
            archive_remote="origin",
            trees_root=str(self.root / "trees"),
            backtracking=str(self.root / "backtracking"),
        )
        self.repo.save()
        self.environment = mock.patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("MY_GIT_BACKTRACKING_INFO_LOCAL", None)
        os.environ.pop("MY_GIT_BACKTRACKING_INFO_REMOTE", None)
        self.branch = "rwgk/feature"
        self.path = self.repo.create(self.branch, "main")
        (self.path / "file.txt").write_text("feature\n")
        self.git("commit", "--quiet", "-am", "Feature", cwd=self.path)
        self.tip = self.git("rev-parse", "HEAD", cwd=self.path)
        self.git(
            "push", "--quiet", "upstream", f"{self.branch}:refs/heads/{self.branch}"
        )
        self.pr = {
            "number": 42,
            "url": "https://github.com/upstream/project/pull/42",
            "state": "MERGED",
            "author": "tester",
            "head_repo": "upstream/project",
            "head_ref": self.branch,
            "head_oid": self.tip,
            "base_repo": "upstream/project",
            "base_ref": "main",
            "is_draft": False,
        }
        self.repo.branches[self.branch].update(
            pr=self.pr,
            publication={
                "remote": "upstream",
                "repo": "upstream/project",
                "ref": self.branch,
                "oid": self.tip,
            },
        )
        self.repo.alias(self.branch, self.pr)
        self.patches = [
            mock.patch(
                "rwgk_wt.github.get_pr", side_effect=lambda *a, **k: dict(self.pr)
            ),
            mock.patch(
                "rwgk_wt.github.remote_identity",
                side_effect=lambda repo, remote, **k: (
                    "upstream/project" if remote == "upstream" else "tester/project"
                ),
            ),
            mock.patch(
                "rwgk_wt.github.validate_archive_remote", return_value="tester/project"
            ),
            mock.patch("rwgk_wt.github.ensure_source_unused"),
            mock.patch("rwgk_wt.stacks.check_retirement"),
            mock.patch("rwgk_wt.stacks.retired"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, *arguments, cwd=None, check=True):
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.primary,
            text=True,
            capture_output=True,
            check=check,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return result.stdout.strip()

    def has_ref(self, repository, ref):
        return bool(
            self.git(
                "--git-dir=" + str(repository), "show-ref", "--verify", ref, check=False
            )
        )

    def records(self):
        return [
            json.loads(path.read_text())
            for path in (self.root / "backtracking").glob("*.json")
        ]

    def test_shared_stack_lock_refuses_cleanup_before_mutation(self):
        with (self.repo.common / "gh-stack-operation.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(core.WTError, "holds gh-stack-operation.lock"):
                lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertTrue(self.path.is_dir())
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )

    def test_local_commit_during_archival_keeps_worktree_and_remote(self):
        original = lifecycle._pin

        def commit_during_archive(*args, **kwargs):
            archives = original(*args, **kwargs)
            (self.path / "file.txt").write_text("Concurrent local work\n")
            self.git("commit", "--quiet", "-am", "Concurrent local work", cwd=self.path)
            return archives

        with (
            mock.patch.object(lifecycle, "_pin", side_effect=commit_during_archive),
            self.assertRaisesRegex(core.WTError, "Local branch changed"),
        ):
            lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertTrue(self.path.is_dir())
        self.assertNotEqual(self.git("rev-parse", "HEAD", cwd=self.path), self.tip)
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_preview_does_not_publish_or_remove_anything(self):
        plan = lifecycle.retire(self.repo, self.branch)
        self.assertEqual(plan["archive_snapshots"][0]["oid"], self.tip)
        self.assertTrue(self.path.is_dir())
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )
        self.assertFalse(self.records())

    def test_retirement_tags_before_deletion_and_restores_pair(self):
        # Start from the worktree being retired, exercising removal of repo.root.
        self.repo = core.Repo(self.path)
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertEqual(record["status"], "complete")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(
            self.git("show-ref", "--verify", "refs/heads/" + self.branch, check=False)
        )
        self.assertEqual(len(record["archives"]), 1)
        archive = record["archives"][0]
        self.assertEqual(
            self.git("--git-dir=" + str(self.origin), "rev-parse", archive["ref"]),
            self.tip,
        )
        self.assertEqual(
            self.git("rev-parse", record["snapshots"][0]["recovery_ref"]), self.tip
        )
        self.assertEqual(self.records()[0]["status"], "complete")
        restored = lifecycle.restore(core.Repo(self.primary), record["id"])
        self.assertEqual(restored, self.path)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=restored), self.tip)
        self.assertTrue((self.path.parent / "pr42").is_symlink())
        self.assertFalse(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_restore_fetches_archive_when_original_objects_are_absent(self):
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        fresh = self.root / "fresh"
        self.git(
            "clone",
            "--quiet",
            "--no-local",
            "--origin",
            "upstream",
            "--branch",
            "main",
            str(self.upstream),
            str(fresh),
        )
        self.git("remote", "add", "origin", str(self.origin), cwd=fresh)
        repo = core.Repo(fresh, initialized=False)
        repo.config.update(self.repo.config)
        repo.config.update(
            primary=str(fresh), trees_root=str(self.root / "fresh-trees")
        )
        repo.save()
        self.assertFalse(lifecycle._exists(repo, self.tip))
        restored = lifecycle.restore(repo, record["id"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=restored), self.tip)
        self.assertTrue((restored.parent / "pr42").is_symlink())

    def test_pending_published_history_is_recorded_and_archived_at_cleanup(self):
        old_tip = self.git(
            "commit-tree",
            self.git("rev-parse", self.base + "^{tree}"),
            "-p",
            self.base,
            "-m",
            "Previous published iteration",
        )
        self.repo.branches[self.branch]["pending_archive"] = [
            {"oid": old_tip, "label": "published-before-stack-pull"},
            {"oid": old_tip, "label": "duplicate"},
            {"oid": self.tip, "label": "current-tip"},
        ]
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertEqual({s["oid"] for s in record["snapshots"]}, {self.tip, old_tip})
        self.assertEqual(len(record["snapshots"]), 2)
        self.assertEqual({s["oid"] for s in record["archives"]}, {self.tip, old_tip})
        self.assertEqual(self.records()[0]["snapshots"], record["snapshots"])
        for snapshot in record["snapshots"]:
            self.assertEqual(
                self.git("rev-parse", snapshot["recovery_ref"]), snapshot["oid"]
            )

    def test_local_only_retirement_retains_pending_history_without_publishing(self):
        old_tip = self.git(
            "commit-tree",
            self.git("rev-parse", self.base + "^{tree}"),
            "-p",
            self.base,
            "-m",
            "Previous published iteration",
        )
        self.repo.branches[self.branch]["pending_archive"] = [
            {"oid": old_tip, "label": "published-before-stack-pull"}
        ]
        record = lifecycle.retire(self.repo, self.branch, apply=True, local_only=True)
        self.assertEqual(record["archives"], [])
        self.assertEqual({s["oid"] for s in record["snapshots"]}, {self.tip, old_tip})
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )
        for snapshot in record["snapshots"]:
            self.assertEqual(
                self.git("rev-parse", snapshot["recovery_ref"]), snapshot["oid"]
            )

    def test_tip_reachable_from_trunk_needs_no_remote_archive(self):
        self.git("update-ref", "refs/heads/main", self.tip, cwd=self.upstream)
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertEqual(record["archives"], [])
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )
        self.assertEqual(self.records()[0]["local_oid"], self.tip)

    def test_review_retirement_never_deletes_contributors_branch(self):
        self.repo.branches[self.branch]["role"] = "review"
        self.repo.save()
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertEqual(record["archives"], [])
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(self.path.exists())

    def test_explicit_review_archive_pins_tip_without_deleting_source(self):
        self.repo.branches[self.branch]["role"] = "review"
        record = lifecycle.retire(
            self.repo, self.branch, apply=True, archive_local=True
        )
        self.assertEqual(len(record["archives"]), 1)
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_local_only_retirement_does_not_query_github(self):
        with (
            mock.patch(
                "rwgk_wt.github.get_pr", side_effect=AssertionError("No network")
            ),
            mock.patch(
                "rwgk_wt.github.remote_identity",
                side_effect=AssertionError("No network"),
            ),
        ):
            record = lifecycle.retire(
                self.repo, self.branch, apply=True, local_only=True
            )
        self.assertEqual(record["archives"], [])
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_open_pr_requires_explicit_local_only(self):
        self.pr["state"] = "OPEN"
        with self.assertRaisesRegex(core.WTError, "still open"):
            lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertTrue(self.path.is_dir())
        self.assertFalse(self.records())

    def test_dirty_or_untracked_content_is_never_discarded(self):
        (self.path / "valuable.txt").write_text("untracked\n")
        with self.assertRaises(core.WTError):
            lifecycle.retire(self.repo, self.branch, apply=True, discard_builds=True)
        self.assertTrue((self.path / "valuable.txt").exists())
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_hidden_index_flags_are_rejected(self):
        self.git("update-index", "--assume-unchanged", "file.txt", cwd=self.path)
        (self.path / "file.txt").write_text("hidden edits\n")
        with self.assertRaisesRegex(core.WTError, "assume-unchanged"):
            lifecycle.retire(self.repo, self.branch, apply=True)

    def test_ignored_builds_need_explicit_discard(self):
        (self.path / "build").mkdir()
        (self.path / "build" / "generated").write_text("disposable\n")
        with self.assertRaisesRegex(core.WTError, "discard-builds"):
            lifecycle.retire(self.repo, self.branch, apply=True)
        lifecycle.retire(self.repo, self.branch, apply=True, discard_builds=True)
        self.assertFalse(self.path.exists())

    def test_unknown_ignored_content_is_never_discarded(self):
        (self.path / "custom-secret").mkdir()
        (self.path / "custom-secret" / "important").write_text("valuable\n")
        with self.assertRaisesRegex(core.WTError, "not recognized"):
            lifecycle.retire(self.repo, self.branch, apply=True, discard_builds=True)
        self.assertTrue(self.path.exists())

    def test_failed_record_persistence_prevents_mutation(self):
        with (
            mock.patch.object(
                lifecycle, "_save_record", side_effect=OSError("Backup unavailable")
            ),
            self.assertRaisesRegex(OSError, "Backup unavailable"),
        ):
            lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertTrue(self.path.is_dir())
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )

    def test_concurrent_source_change_fails_lease_and_is_preserved(self):
        tree = self.git("rev-parse", self.tip + "^{tree}")
        replacement = subprocess.run(
            ["git", "commit-tree", tree, "-p", self.tip],
            cwd=self.primary,
            input="Concurrent source change\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.git(
            "push",
            "--quiet",
            "upstream",
            replacement + ":refs/heads/concurrent-fixture",
        )
        original = self.repo.git

        def race(*args, **kwargs):
            if (
                args
                and args[0] == "push"
                and any(arg == ":refs/heads/" + self.branch for arg in args)
            ):
                self.git(
                    "update-ref",
                    "refs/heads/" + self.branch,
                    replacement,
                    self.tip,
                    cwd=self.upstream,
                )
            return original(*args, **kwargs)

        with (
            mock.patch.object(self.repo, "git", side_effect=race),
            self.assertRaises(core.WTError),
        ):
            lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertEqual(
            self.git("rev-parse", "refs/heads/" + self.branch, cwd=self.upstream),
            replacement,
        )
        self.assertTrue(self.path.is_dir())
        record = self.records()[0]
        self.assertEqual(record["status"], "planned")
        self.assertTrue(record["archives"])
        with self.assertRaisesRegex(core.WTError, "changed since"):
            lifecycle.resume(self.repo, record["id"])

    def test_resume_stops_if_terminal_pr_was_reopened(self):
        with (
            mock.patch.object(
                lifecycle, "_pin", side_effect=core.WTError("Archive offline")
            ),
            self.assertRaisesRegex(core.WTError, "Archive offline"),
        ):
            lifecycle.retire(self.repo, self.branch, apply=True)
        record = self.records()[0]
        self.pr["state"] = "OPEN"
        with self.assertRaisesRegex(core.WTError, "reopened"):
            lifecycle.resume(self.repo, record["id"])
        self.assertTrue(self.path.is_dir())
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_cleanup_resumes_after_worktree_removal_before_journal_save(self):
        original = self.repo.git

        def interrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:2] == ("worktree", "remove"):
                raise core.WTError("Interrupted after removal")
            return result

        with (
            mock.patch.object(self.repo, "git", side_effect=interrupt),
            self.assertRaisesRegex(core.WTError, "Interrupted"),
        ):
            lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertFalse(self.path.exists())
        record = self.records()[0]
        self.assertNotIn("worktree", record["completed"])
        result = lifecycle.resume(core.Repo(self.primary), record["id"])
        self.assertEqual(result["status"], "complete")
        self.assertNotIn(self.branch, core.Repo(self.primary).branches)

    def test_preserve_local_before_rebase_uses_no_remote(self):
        with mock.patch.object(
            lifecycle, "_remote_tip", side_effect=AssertionError("No remote calls")
        ):
            record = lifecycle.protect_local(
                self.repo, [{"oid": self.tip, "label": "before-rebase"}]
            )
        self.assertEqual(record["status"], "complete")
        self.assertEqual(
            self.git("rev-parse", record["snapshots"][0]["recovery_ref"]), self.tip
        )
        self.assertEqual(self.records()[0]["kind"], "rebase")

    def test_fork_pr_archives_and_deletes_on_same_fork(self):
        self.git("push", "--quiet", "origin", self.branch)
        self.pr["head_repo"] = "tester/project"
        self.repo.branches[self.branch]["publication"].update(
            remote="origin", repo="tester/project"
        )
        record = lifecycle.retire(self.repo, self.branch, apply=True)
        self.assertTrue(record["archives"])
        self.assertFalse(self.has_ref(self.origin, "refs/heads/" + self.branch))
        self.assertTrue(self.has_ref(self.upstream, "refs/heads/" + self.branch))

    def test_rewrite_preservation_skips_trunk_and_covered_history(self):
        lifecycle.pin_snapshots(
            self.repo, [{"oid": self.base, "label": "trunk"}], pr=self.pr
        )
        self.assertFalse(
            self.git("--git-dir=" + str(self.origin), "show-ref", check=False)
        )
        descendant = self.git(
            "commit-tree",
            self.git("rev-parse", self.tip + "^{tree}"),
            "-p",
            self.tip,
            "-m",
            "Descendant archive",
        )
        first = lifecycle.pin_snapshots(
            self.repo, [{"oid": descendant, "label": "new"}], pr=self.pr
        )
        second = lifecycle.pin_snapshots(
            self.repo, [{"oid": self.tip, "label": "old"}], pr=self.pr
        )
        self.assertEqual(second, first)
        self.assertEqual(
            len(self.git("--git-dir=" + str(self.origin), "show-ref").splitlines()), 1
        )

    def test_rewrite_preservation_is_durable_and_immutable(self):
        first = lifecycle.pin_snapshots(
            self.repo, [{"oid": self.tip, "label": "published"}], pr=self.pr
        )
        second = lifecycle.pin_snapshots(
            self.repo, [{"oid": self.tip, "label": "published"}], pr=self.pr
        )
        self.assertEqual(first, second)
        self.assertEqual(
            self.git("--git-dir=" + str(self.origin), "rev-parse", first[0]["ref"]),
            self.tip,
        )
        self.git("update-ref", first[0]["ref"], self.base, cwd=self.origin)
        with self.assertRaisesRegex(core.WTError, "Immutable archive"):
            lifecycle.pin_snapshots(
                self.repo, [{"oid": self.tip, "label": "published"}], pr=self.pr
            )
        self.assertEqual(
            self.git("--git-dir=" + str(self.origin), "rev-parse", first[0]["ref"]),
            self.base,
        )


if __name__ == "__main__":
    unittest.main()
