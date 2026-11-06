"""Independent PR synchronization respects another worktree's recovery scope."""

import contextlib
import copy
import io
import json
import os
import unittest
from unittest import mock

import test_wt_github as fixtures
import test_wt_push as push_fixtures
from rwgk_wt import cli, operation_scope, stacks
from rwgk_wt.core import WTError

git = fixtures.git


@unittest.skipUnless(
    os.environ.get("WT_TEST_WORKTREE_ROOT"),
    "Set WT_TEST_WORKTREE_ROOT under $W/trees/.wt-tests for real Git fixtures",
)
class OperationScopeTests(unittest.TestCase):
    setUp = push_fixtures.PushTests.setUp
    remove_external_trees = push_fixtures.PushTests.remove_external_trees
    response = push_fixtures.PushTests.response
    raw_pr = push_fixtures.PushTests.raw_pr
    gh = push_fixtures.PushTests.gh
    pushes = push_fixtures.PushTests.pushes
    refresh_pr = push_fixtures.PushTests.refresh_pr

    def prepare(self):
        self.review = push_fixtures.PushTests.pair(self)
        self.lower = "rwgk/stack-one"
        self.upper = "rwgk/stack-two"
        self.lower_path = self.repo.create(self.lower, "main")
        self.upper_path = self.repo.create(self.upper, self.lower)
        self.stack = {
            "id": "stack-one",
            "number": 10,
            "trunk": {"branch": "main", "head": self.repo.oid("main")},
            "branches": [
                {
                    "branch": branch,
                    "head": self.repo.oid(branch),
                    "base": self.repo.oid("main"),
                }
                for branch in (self.lower, self.upper)
            ],
        }
        self.catalog = self.repo.common / "gh-stack"
        self.catalog.write_text(
            json.dumps({"schemaVersion": 1, "stacks": [self.stack]})
        )
        self.pending = {
            "phase": "aligning",
            "primary_before": self.repo.oid("main"),
            # Only the lower layer has a module snapshot. The upper layer still
            # belongs to the operation and must be reserved by the full scope.
            "captured": [
                {
                    "branch": self.lower,
                    "path": str(self.lower_path),
                    "modules": [],
                    "heads": {},
                }
            ],
            "scope": {
                "stack_id": "stack-one",
                "stack_number": 10,
                "branches": [self.lower, self.upper],
                "worktrees": [
                    {"branch": branch, "path": str(path)}
                    for branch, path in (
                        ("main", self.primary),
                        (self.lower, self.lower_path),
                        (self.upper, self.upper_path),
                    )
                ],
            },
        }
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()

    def invoke(self, *arguments, path=None):
        repo = fixtures.FixtureRepo(self, path or self.review.root)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "Repo", return_value=repo),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def saved_operation(self, name="submodule_rebase"):
        return json.loads(self.repo.state_path.read_text()).get(name)

    def stack_snapshot(self):
        return {
            "heads": {
                branch: self.repo.oid(branch)
                for branch in ("main", self.lower, self.upper)
            },
            "catalog": self.catalog.read_bytes(),
        }

    def advance_review(self):
        target = push_fixtures.PushTests.advance_remote(self, self.review)
        git(self.primary, "push", "upstream", f"{target}:refs/pull/42/head")
        return target

    def test_unrelated_pull_updates_only_review_and_preserves_pending_restack(self):
        self.prepare()
        before = self.stack_snapshot()
        target = self.advance_review()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.review.oid("HEAD"), target)
        self.assertEqual(self.saved_operation(), self.pending)
        self.assertEqual(self.stack_snapshot(), before)

    def test_unrelated_push_keeps_other_stack_and_its_recovery_record(self):
        self.prepare()
        target = push_fixtures.PushTests.commit(self, self.review)
        before = self.stack_snapshot()
        status, _, errors = self.invoke("push")
        self.assertEqual(status, 0, errors)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), target)
        self.assertEqual(self.saved_operation(), self.pending)
        self.assertEqual(self.stack_snapshot(), before)

    def test_reservation_includes_primary_and_layer_without_initialized_modules(self):
        self.prepare()
        for path in (self.primary, self.lower_path, self.upper_path):
            with self.subTest(path=path):
                repo = fixtures.FixtureRepo(self, path)
                repo._wt_independent_sync = True
                with self.assertRaisesRegex(
                    WTError, "pending submodule work"
                ) as stopped:
                    repo.ensure_idle()
                self.assertIn(str(self.lower_path), str(stopped.exception))
        self.assertEqual(self.saved_operation(), self.pending)

    def test_retargeted_stack_reserves_old_base_without_making_it_a_recovery_owner(
        self,
    ):
        self.prepare()
        branch = self.review.current_branch()
        self.stack["trunk"]["branch"] = branch
        self.catalog.write_text(
            json.dumps({"schemaVersion": 1, "stacks": [self.stack]})
        )
        self.pending["scope"] = operation_scope.stack_scope(self.repo, self.stack)
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()
        repo = fixtures.FixtureRepo(self, self.review.root)
        repo._wt_independent_sync = True
        with self.assertRaisesRegex(WTError, "pending submodule work"):
            repo.ensure_idle()
        with self.assertRaisesRegex(WTError, "cannot resume or replace"):
            operation_scope.require_restack_owner(repo)
        self.assertEqual(self.saved_operation(), self.pending)

    def test_legacy_scope_uses_complete_stack_not_only_captured_module_owners(self):
        self.prepare()
        self.pending.pop("scope")
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()
        target = self.advance_review()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.review.oid("HEAD"), target)
        upper = fixtures.FixtureRepo(self, self.upper_path)
        upper._wt_independent_sync = True
        with self.assertRaisesRegex(WTError, "pending submodule work"):
            upper.ensure_idle()
        self.assertEqual(self.saved_operation(), self.pending)

    def test_unknown_operation_scope_stays_blocked(self):
        self.prepare()
        self.pending.pop("scope")
        self.pending["captured"] = [
            {"branch": "main", "path": str(self.primary), "modules": [], "heads": {}}
        ]
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 1)
        self.assertIn("pending submodule work", errors)
        self.assertEqual(self.saved_operation(), self.pending)

    def test_other_stack_cannot_consume_or_replace_pending_alignment(self):
        self.prepare()
        self.repo.state.pop("submodule_rebase")
        self.repo.save()
        other = self.repo.create("rwgk/other-stack", "main")
        other_stack = {
            "id": "stack-other",
            "trunk": self.stack["trunk"],
            "branches": [{"branch": "rwgk/other-stack"}],
        }
        self.catalog.write_text(
            json.dumps({"schemaVersion": 1, "stacks": [self.stack, other_stack]})
        )
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()
        repo = fixtures.FixtureRepo(self, other)
        before = self.stack_snapshot()
        with (
            mock.patch.object(stacks, "require_extension"),
            mock.patch.object(stacks, "_rebase") as execute,
            self.assertRaises(WTError),
        ):
            stacks.rebase(repo)
        execute.assert_not_called()
        self.assertFalse(getattr(repo, "_wt_submodule_rebase_active", False))
        self.assertEqual(self.saved_operation(), self.pending)
        self.assertEqual(self.stack_snapshot(), before)

    def test_detached_layer_without_module_snapshot_cannot_finish_alignment(self):
        self.prepare()
        git(self.upper_path, "checkout", "--detach", "HEAD")
        repo = fixtures.FixtureRepo(self, self.upper_path)
        with (
            mock.patch.object(stacks, "require_extension"),
            mock.patch.object(stacks, "_rebase") as execute,
            self.assertRaises(WTError),
        ):
            stacks.rebase(repo)
        execute.assert_not_called()
        self.assertEqual(self.saved_operation(), self.pending)

    def test_another_pr_rebase_allows_plain_pull_but_keeps_single_recovery_slot(self):
        self.prepare()
        self.repo.state.pop("submodule_rebase")
        pending = {
            "branch": self.lower,
            "path": str(self.lower_path),
            "phase": "rebasing",
        }
        self.repo.state["pr_rebase"] = pending
        self.repo.save()
        target = self.advance_review()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.review.oid("HEAD"), target)
        status, _, errors = self.invoke("pull", "--rebase")
        self.assertEqual(status, 1)
        self.assertIn("pending PR rebase", errors)
        self.assertEqual(self.saved_operation("pr_rebase"), pending)

    def test_local_git_operation_is_still_blocked_in_unrelated_review(self):
        self.prepare()
        marker = self.review.root / git(
            self.review.root, "rev-parse", "--git-path", "MERGE_HEAD"
        )
        marker.write_text(self.review.oid("HEAD") + "\n")
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 1)
        self.assertIn("Unfinished Git operation", errors)
        self.assertEqual(self.saved_operation(), self.pending)

    def test_unknown_native_recovery_journal_remains_repository_wide(self):
        self.prepare()
        for name in ("gh-stack-adopt-state", "gh-stack-rebase-state"):
            marker = self.repo.common / name
            marker.write_text("{}\n")
            with self.subTest(name=name):
                status, _, errors = self.invoke("pull")
                self.assertEqual(status, 1)
                self.assertIn(name, errors)
                self.assertEqual(marker.read_text(), "{}\n")
            marker.unlink()
        self.assertEqual(self.saved_operation(), self.pending)

    def test_correlated_native_rebase_ignores_unrelated_owners_but_not_touched_refs(
        self,
    ):
        self.prepare()
        self.stack["branches"][0]["pullRequest"] = {"merged": True}
        self.catalog.write_text(
            json.dumps({"schemaVersion": 1, "stacks": [self.stack]})
        )
        self.pending["phase"] = "rebasing"
        self.pending["captured"][0]["branch"] = self.upper
        self.pending["captured"][0]["path"] = str(self.upper_path)
        self.repo.state["submodule_rebase"] = copy.deepcopy(self.pending)
        self.repo.save()
        review_branch = self.review.current_branch()
        native = {
            "stackId": "stack-one",
            "stackTrunk": "main",
            "stackBranches": [self.lower, self.upper],
            "originalBranch": self.upper,
            "originalStack": self.stack,
            "originalRefs": {
                branch: self.repo.oid(branch)
                for branch in ("main", self.lower, self.upper)
            },
            "phase": "rebasing",
            "worktrees": {
                "origin": {"path": str(self.upper_path)},
                "owners": {
                    branch: {"path": str(path)}
                    for branch, path in (
                        ("main", self.primary),
                        (self.lower, self.lower_path),
                        (self.upper, self.upper_path),
                        (review_branch, self.review.root),
                    )
                },
                "touched": {self.upper: self.repo.oid(self.upper)},
                "pending": self.upper,
                "pendingBefore": self.repo.oid(self.upper),
            },
        }
        marker = self.repo.common / "gh-stack-rebase-state"
        marker.write_text(json.dumps(native))
        before = marker.read_bytes()
        target = self.advance_review()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.review.oid("HEAD"), target)
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(self.saved_operation(), self.pending)
        # Native-only restacks have no submodule journal when no module owners
        # were captured. Their same complete native scope remains usable.
        saved = fixtures.FixtureRepo(self)
        saved.state.pop("submodule_rebase")
        saved.save()
        target = self.advance_review()
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.review.oid("HEAD"), target)
        self.assertEqual(marker.read_bytes(), before)
        self.assertIsNone(self.saved_operation())
        saved = fixtures.FixtureRepo(self)
        saved.state["submodule_rebase"] = copy.deepcopy(self.pending)
        saved.save()
        native["worktrees"]["touched"][review_branch] = target
        marker.write_text(json.dumps(native))
        status, _, errors = self.invoke("pull")
        self.assertEqual(status, 1)
        self.assertIn("gh-stack-rebase-state", errors)
        self.assertEqual(self.saved_operation(), self.pending)


if __name__ == "__main__":
    unittest.main()
