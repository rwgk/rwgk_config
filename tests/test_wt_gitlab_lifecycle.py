"""GitLab cleanup uses the shared recovery engine without deleting review forks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import github, housekeeping, lifecycle
from test_wt_gitlab import GitLabFixture, GitLabRepo, git


class GitLabLifecycleTests(GitLabFixture):
    def closed_review(self):
        self.contributor_mr()
        review = self.open_review()
        self.mrs[42]["state"] = "closed"
        return review, GitLabRepo(self)

    def test_review_cleanup_preserves_contributor_source_and_can_restore_pair(self):
        review, repo = self.closed_review()
        branch, tip, path = review.current_branch(), review.oid("HEAD"), review.root
        before = git(self.remotes["contributor"], "rev-parse", "topic")
        self.commands.clear()
        preview = lifecycle.retire(repo, branch)
        self.assertNotIn("source", preview)
        self.assertEqual(preview["archive_snapshots"], [])
        self.assertTrue(path.exists())
        self.assertFalse(self.pushes())
        record = lifecycle.retire(repo, branch, apply=True)
        self.assertEqual(record["status"], "complete")
        self.assertFalse(path.exists())
        self.assertNotIn(branch, repo.branches)
        self.assertFalse((path.parent / "mr42").exists())
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), before)
        self.assertFalse(self.pushes())
        refs = repo.git(
            "for-each-ref", "--format=%(objectname)", "refs/rwgk/backtracking"
        )
        self.assertIn(tip, refs.splitlines())
        self.assertTrue(list((self.root / "backtracking").glob("*.json")))
        restored = lifecycle.restore(repo, record["id"])
        self.assertTrue(path.exists())
        self.assertEqual(git(path, "rev-parse", "HEAD"), tip)
        self.assertEqual(repo.branches[branch]["role"], "review")
        self.assertEqual((path.parent / "mr42").resolve(), path)
        self.assertTrue(restored)
        self.assert_checkouts()

    def test_cleanup_removes_and_restores_only_matching_mr_and_legacy_aliases(self):
        review, repo = self.closed_review()
        path, branch = review.root, review.current_branch()
        (path.parent / "pr42").symlink_to(path.name, target_is_directory=True)
        unrelated = path.parent / "pr99"
        unrelated.symlink_to(path.name, target_is_directory=True)
        other = path.parent / "mr99"
        other.symlink_to(repo.primary, target_is_directory=True)
        record = lifecycle.retire(repo, branch, apply=True)
        self.assertFalse((path.parent / "mr42").is_symlink())
        self.assertFalse((path.parent / "pr42").is_symlink())
        self.assertTrue(unrelated.is_symlink())
        self.assertEqual(other.resolve(), repo.primary)
        self.assertEqual(set(record["removed_aliases"]), {"mr42", "pr42"})
        restored = lifecycle.restore(repo, record["id"])
        self.assertEqual(restored, path)
        self.assertEqual((path.parent / "mr42").resolve(), path)
        self.assertEqual((path.parent / "pr42").resolve(), path)
        self.assertTrue(unrelated.is_symlink())
        self.assertEqual(other.resolve(), repo.primary)

    def test_interrupted_alias_cleanup_retains_both_names_for_retry_and_restore(self):
        review, repo = self.closed_review()
        path, branch = review.root, review.current_branch()
        preferred, legacy = path.parent / "mr42", path.parent / "pr42"
        legacy.symlink_to(path.name, target_is_directory=True)
        unrelated = path.parent / "pr99"
        unrelated.symlink_to(path.name, target_is_directory=True)
        unlink = Path.unlink

        def interrupted(alias, *args, **kwargs):
            if alias in (preferred, legacy):
                records = [
                    json.loads(item.read_text())
                    for item in lifecycle._operation_dir(repo).glob("*.json")
                ]
                record = next(
                    item
                    for item in records
                    if item["kind"] == "retire" and item["branch"] == branch
                )
                self.assertEqual(set(record["removed_aliases"]), {"mr42", "pr42"})
            if alias == legacy:
                raise OSError("simulated interruption between alias removals")
            return unlink(alias, *args, **kwargs)

        with (
            patch.object(Path, "unlink", new=interrupted),
            self.assertRaisesRegex(OSError, "simulated interruption"),
        ):
            lifecycle.retire(repo, branch, apply=True)
        records = [
            json.loads(item.read_text())
            for item in lifecycle._operation_dir(repo).glob("*.json")
        ]
        record = next(
            item
            for item in records
            if item["kind"] == "retire" and item["branch"] == branch
        )
        self.assertEqual(set(record["removed_aliases"]), {"mr42", "pr42"})
        self.assertFalse(preferred.is_symlink())
        self.assertTrue(legacy.is_symlink())
        self.assertTrue(unrelated.is_symlink())
        resumed = lifecycle.resume(repo, record["id"])
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(set(resumed["removed_aliases"]), {"mr42", "pr42"})
        self.assertFalse(legacy.is_symlink())
        self.assertTrue(unrelated.is_symlink())
        self.assertEqual(lifecycle.restore(repo, record["id"]), path)
        self.assertEqual(preferred.resolve(), path)
        self.assertEqual(legacy.resolve(), path)
        self.assertTrue(unrelated.is_symlink())

    def test_housekeeping_uses_mr_labels_and_navigation_hints(self):
        self.contributor_mr()
        review = self.open_review()
        repo = GitLabRepo(self)
        status, output, error = self.invoke(repo, ["housekeeping"])
        self.assertEqual(status, 0, error)
        self.assertIn("mr42 OPEN", output)
        self.assertIn("MR !42 is open", output)
        self.assertIn("wt go mr42", output)
        self.assertNotIn("wt go pr42", output)
        self.assertTrue(review.root.exists())

    def test_housekeeping_retires_terminal_review_without_any_source_push(self):
        review, repo = self.closed_review()
        path = review.root
        source = git(self.remotes["contributor"], "rev-parse", "topic")
        self.commands.clear()
        preview = housekeeping.run(repo)
        self.assertEqual(len(preview["operations"]), 1)
        self.assertTrue(path.exists())
        self.assertFalse(self.pushes())
        result = housekeeping.run(repo, apply=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["operations"]), 1)
        self.assertFalse(path.exists())
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), source)
        self.assertFalse(self.pushes())
        self.assert_checkouts()

    def test_housekeeping_keeps_unpublished_review_contribution_visible(self):
        review, _ = self.closed_review()
        (review.root / "valuable").write_text("unpublished contribution\n")
        local = self.commit(review.root, "Unpublished contribution")
        repo = GitLabRepo(self)
        self.commands.clear()
        result = housekeeping.run(repo, apply=True)
        self.assertEqual(result["operations"], [])
        remaining = next(
            item
            for item in result["remaining"]
            if item["branch"] == review.current_branch()
        )
        self.assertFalse(remaining["eligible"])
        self.assertTrue(remaining["reasons"])
        self.assertTrue(review.root.exists())
        self.assertEqual(git(review.root, "rev-parse", "HEAD"), local)
        self.assertEqual(
            (review.root / "valuable").read_text(), "unpublished contribution\n"
        )
        self.assertFalse(self.pushes())
        self.assert_checkouts()

    def test_owned_terminal_mr_cleanup_archives_then_deletes_only_own_source(self):
        owned = self.owned_branch()
        published = github.publish(owned)
        number = published["prs"][0]["number"]
        tip, branch, path = owned.oid("HEAD"), owned.current_branch(), owned.root
        self.mrs[number]["state"] = "merged"
        self.mrs[number]["merged_at"] = "2026-10-29T12:00:00Z"
        repo = GitLabRepo(self)
        self.commands.clear()
        self.api_calls.clear()
        record = lifecycle.retire(repo, branch, apply=True)
        self.assertEqual(record["status"], "complete")
        self.assertFalse(path.exists())
        self.assertFalse(
            git(
                self.remotes["origin"],
                "show-ref",
                "--verify",
                "refs/heads/" + branch,
                check=False,
            )
        )
        self.assertEqual(git(self.remotes["upstream"], "rev-parse", "main"), self.base)
        archives = git(
            self.remotes["origin"],
            "for-each-ref",
            "--format=%(objectname)",
            "refs/tags/",
        )
        self.assertIn(tip, archives.splitlines())
        self.assertEqual(record["source"]["remote"], "origin")
        self.assertTrue(
            any(
                action["action"] == "branch-deleted"
                for action in record["remote_actions"]
            )
        )
        self.assertTrue(all(method == "GET" for method, _, _ in self.api_calls))
        self.assert_checkouts()
