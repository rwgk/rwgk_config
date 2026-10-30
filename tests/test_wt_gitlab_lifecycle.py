"""GitLab cleanup uses the shared recovery engine without deleting review forks."""

from __future__ import annotations

import sys
from pathlib import Path

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
        self.assertFalse((path.parent / "pr42").exists())
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
        self.assertEqual((path.parent / "pr42").resolve(), path)
        self.assertTrue(restored)
        self.assert_checkouts()

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
