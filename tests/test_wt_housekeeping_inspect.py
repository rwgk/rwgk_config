"""Housekeeping reports work instead of relying on cleanup's recovery safety net."""

from __future__ import annotations

import copy
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import test_wt_github as fixtures
from rwgk_wt import github
from rwgk_wt import housekeeping_inspect as inspection
from rwgk_wt.core import WTError

git = fixtures.git


class HousekeepingInspectionTests(unittest.TestCase):
    setUp = fixtures.GitHubTests.setUp
    remove_external_trees = fixtures.GitHubTests.remove_external_trees
    response = fixtures.GitHubTests.response
    raw_pr = fixtures.GitHubTests.raw_pr
    refresh_pr = fixtures.GitHubTests.refresh_pr
    gh = fixtures.GitHubTests.gh
    pushes = fixtures.GitHubTests.pushes

    def pair(self, *, state="closed", merged=True):
        oid = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            self.repo.oid("main"),
            "-m",
            "Contributor work",
        )
        self.prs[42] = self.raw_pr(
            42,
            author="contributor",
            head_repo=None,
            branch="topic",
            oid=oid,
            state=state,
            merged=merged,
        )
        git(self.primary, "push", "upstream", f"{oid}:refs/pull/42/head")
        path = github.open_pr(self.repo, 42)
        return fixtures.FixtureRepo(self, path)

    def assess(self, repo, **options):
        return inspection.assess(repo, "rwgk/review-pr42", **options)

    def codes(self, entry):
        return {row["code"] for row in entry["reasons"]}

    def commit(self, repo, name="valuable"):
        (repo.root / name).write_text(name + "\n")
        git(repo.root, "add", name)
        git(repo.root, "commit", "-m", "Add " + name)
        return repo.oid("HEAD")

    def remote_commit(self, repo):
        oid = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            self.prs[42]["head"]["sha"],
            "-m",
            "Contributor updated",
        )
        self.prs[42]["head"]["sha"] = oid
        git(self.primary, "push", "upstream", f"{oid}:refs/pull/42/head")
        return oid

    def test_terminal_pr_covered_head_is_eligible_even_when_not_in_trunk(self):
        repo = self.pair()
        before = repo.state_path.read_text()
        oid = repo.oid("HEAD")
        entry = self.assess(repo)
        self.assertTrue(entry["eligible"], entry)
        self.assertTrue(entry["pr_verified"])
        self.assertEqual(
            entry["coverage"], {"pr_head_oid": oid, "trunk_oid": None, "basis": "pr"}
        )
        self.assertFalse(entry["error"])
        self.assertEqual(repo.state_path.read_text(), before)
        self.assertEqual(repo.oid("HEAD"), oid)
        self.assertFalse(self.pushes())

    def test_closed_unmerged_pr_head_can_be_obsolete(self):
        repo = self.pair(merged=False)
        self.assertTrue(self.assess(repo)["eligible"])

    def test_additional_local_commit_stays_visible_despite_cleanup_recoverability(self):
        repo = self.pair()
        local = self.commit(repo)
        entry = self.assess(repo)
        self.assertFalse(entry["eligible"])
        self.assertIn("local-history", self.codes(entry))
        self.assertEqual(entry["facts"]["local_only_commits"], 1)
        self.assertEqual(repo.oid("HEAD"), local)
        self.assertFalse(entry["error"])

    def test_history_already_in_verified_trunk_can_be_cleaned(self):
        repo = self.pair()
        local = self.commit(repo)
        git(self.primary, "push", "upstream", f"{local}:refs/heads/main")
        entry = self.assess(repo)
        self.assertTrue(entry["eligible"], entry)
        self.assertEqual(entry["coverage"]["basis"], "trunk")
        self.assertEqual(entry["coverage"]["trunk_oid"], local)

    def test_older_local_review_already_contained_in_final_pr_head_is_eligible(self):
        repo = self.pair()
        self.remote_commit(repo)
        self.assertTrue(self.assess(repo)["eligible"])

    def test_modified_staged_and_untracked_paths_are_counted(self):
        repo = self.pair()
        (repo.root / "base").write_text("changed\n")
        (repo.root / "new-staged").write_text("staged\n")
        git(repo.root, "add", "new-staged")
        (repo.root / "scratch").write_text("authored\n")
        entry = self.assess(repo)
        self.assertFalse(entry["eligible"])
        self.assertEqual(set(entry["facts"]["modified"]), {"base", "new-staged"})
        self.assertEqual(entry["facts"]["untracked"], ["scratch"])
        self.assertIn("working-files", self.codes(entry))

    def test_hidden_index_flags_keep_worktree(self):
        repo = self.pair()
        git(repo.root, "update-index", "--assume-unchanged", "base")
        entry = self.assess(repo)
        self.assertFalse(entry["eligible"])
        self.assertIn("worktree-check", self.codes(entry))
        self.assertIn("assume-unchanged", entry["reasons"][0]["message"])

    def ignore(self, repo, pattern):
        info = repo.common / "info" / "exclude"
        with info.open("a") as stream:
            stream.write("\n" + pattern + "\n")

    def test_generated_builds_require_flag_and_then_use_shared_classifier(self):
        repo = self.pair()
        self.ignore(repo, "/build/")
        (repo.root / "build").mkdir()
        (repo.root / "build" / "output").write_text("generated\n")
        kept = self.assess(repo)
        self.assertIn("build-outputs", self.codes(kept))
        self.assertFalse(kept["eligible"])
        self.assertTrue(self.assess(repo, discard_builds=True)["eligible"])
        self.assertTrue((repo.root / "build" / "output").is_file())

    def test_arbitrary_ignored_files_stay_even_with_discard_builds(self):
        repo = self.pair()
        self.ignore(repo, ".env")
        (repo.root / ".env").write_text("authored material\n")
        entry = self.assess(repo, discard_builds=True)
        self.assertIn("ignored-material", self.codes(entry))
        self.assertFalse(entry["eligible"])
        self.assertEqual((repo.root / ".env").read_text(), "authored material\n")

    def test_locked_worktree_is_retained(self):
        repo = self.pair()
        git(self.primary, "worktree", "lock", str(repo.root))
        entry = self.assess(repo)
        self.assertFalse(entry["eligible"])
        self.assertIn("locked", self.codes(entry))
        git(self.primary, "worktree", "unlock", str(repo.root))

    def test_open_pr_reports_equal_ahead_behind_and_divergence(self):
        repo = self.pair(state="open", merged=False)
        equal = self.assess(repo)
        self.assertIn("pr-open", self.codes(equal))
        self.assertEqual((equal["facts"]["ahead"], equal["facts"]["behind"]), (0, 0))
        self.commit(repo)
        ahead = self.assess(repo)
        self.assertTrue(any("wt push" in hint for hint in ahead["hints"]))
        self.remote_commit(repo)
        diverged = self.assess(repo)
        self.assertTrue(any("wt pull --rebase" in hint for hint in diverged["hints"]))
        git(
            repo.root,
            "reset",
            "--hard",
            repo.branches["rwgk/review-pr42"]["last_checked_out_head"],
        )
        behind = self.assess(repo)
        self.assertTrue(any("'wt pull'" in hint for hint in behind["hints"]))

    def test_open_stack_layer_hints_use_stack_commands(self):
        repo = self.pair(state="open", merged=False)
        self.commit(repo)
        with mock.patch("rwgk_wt.stacks.get_stack", return_value={"branches": []}):
            ahead = self.assess(repo)
            self.remote_commit(repo)
            diverged = self.assess(repo)
        self.assertTrue(any("wt publish --stack" in hint for hint in ahead["hints"]))
        self.assertTrue(any("wt stack pull" in hint for hint in diverged["hints"]))
        self.assertFalse(
            any(
                "'wt push'" in hint or "'wt pull --rebase'" in hint
                for hint in ahead["hints"] + diverged["hints"]
            )
        )

    def test_replaced_directory_cannot_borrow_old_worktree_registration(self):
        repo = self.pair()
        destination = repo.root.with_name(repo.root.name + "-moved")
        repo.root.rename(destination)
        repo.root.mkdir()
        git(repo.root, "init", "--initial-branch=rwgk/review-pr42")
        entry = inspection.assess(self.repo, "rwgk/review-pr42")
        self.assertFalse(entry["eligible"])
        self.assertTrue(entry["error"])
        self.assertIn("ownership", self.codes(entry))

    def test_open_pr_rewind_does_not_suggest_restoring_removed_history(self):
        repo = self.pair(state="open", merged=False)
        self.commit(repo)
        original = repo.oid("main")
        self.prs[42]["head"]["sha"] = original
        git(
            self.primary, "push", "--force", "upstream", f"{original}:refs/pull/42/head"
        )
        entry = self.assess(repo)
        self.assertTrue(any("wt pull --rebase" in hint for hint in entry["hints"]))
        self.assertFalse(any("wt push" in hint for hint in entry["hints"]))

    def test_lookup_failure_is_not_interpreted_as_terminal(self):
        repo = self.pair()
        with mock.patch.object(
            github, "get_pr", side_effect=WTError("API unavailable")
        ):
            entry = self.assess(repo)
        self.assertTrue(entry["error"])
        self.assertFalse(entry["eligible"])
        self.assertIn("pr-check-failed", self.codes(entry))
        self.assertFalse(entry["pr_verified"])

    def test_foreign_pr_association_is_refused_before_api_or_fetch(self):
        repo = self.pair()
        repo.branches["rwgk/review-pr42"]["pr"]["base_repo"] = "Other/project"
        before = len(self.commands)
        with mock.patch.object(github, "get_pr") as lookup:
            entry = self.assess(repo)
        lookup.assert_not_called()
        self.assertFalse(any("fetch" in argv for argv in self.commands[before:]))
        self.assertFalse(entry["eligible"])
        self.assertTrue(entry["error"])
        self.assertFalse(entry["pr_verified"])
        self.assertIn("foreign-pr", self.codes(entry))
        self.assertTrue(any("wt doctor" in hint for hint in entry["hints"]))

    def test_pr_source_change_is_not_silently_adopted(self):
        repo = self.pair()
        before = copy.deepcopy(repo.branches["rwgk/review-pr42"])
        self.prs[42]["head"]["ref"] = "different-source"
        entry = self.assess(repo)
        self.assertTrue(entry["error"])
        self.assertIn(
            "source repository or branch changed", entry["reasons"][-1]["message"]
        )
        self.assertEqual(repo.branches["rwgk/review-pr42"], before)

    def test_racing_pr_head_is_not_assumed_obsolete(self):
        repo = self.pair()
        old = self.prs[42]["head"]["sha"]
        self.remote_commit(repo)
        self.prs[42]["head"]["sha"] = old
        entry = self.assess(repo)
        self.assertFalse(entry["eligible"])
        self.assertTrue(entry["error"])

    def test_no_pr_work_is_retained(self):
        path = self.repo.create("rwgk/unpublished", "main")
        repo = fixtures.FixtureRepo(self, path)
        entry = inspection.assess(repo, "rwgk/unpublished")
        self.assertIn("no-pr", self.codes(entry))
        self.assertFalse(entry["eligible"])
        self.assertFalse(self.gh_calls)

    def test_missing_and_mismatched_owners_report_invariants(self):
        repo = self.pair()
        branch = "rwgk/review-pr42"
        repo.branches[branch]["path"] = str(self.root / "missing")
        missing = self.assess(repo)
        self.assertIn("missing-worktree", self.codes(missing))
        self.assertFalse(missing["pr_verified"])
        repo.branches[branch]["path"] = str(self.primary)
        primary = self.assess(repo)
        self.assertIn("ownership", self.codes(primary))
        self.assertTrue(primary["error"])
        self.assertFalse(primary["eligible"])

    def test_unfinished_git_operation_keeps_workspace(self):
        repo = self.pair()
        marker = Path(
            repo.git("rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD")
        )
        marker.write_text(repo.oid("main") + "\n")
        entry = self.assess(repo)
        self.assertIn("unfinished-operation", self.codes(entry))
        self.assertFalse(entry["eligible"])
        marker.unlink()

    def module(self, repo):
        source = self.root / "module"
        source.mkdir()
        git(source, "init", "--initial-branch=main")
        git(source, "config", "user.name", "Module fixture")
        git(source, "config", "user.email", "fixture@example.invalid")
        (source / "code").write_text("module\n")
        git(source, "add", ".")
        git(source, "commit", "-m", "Module base")
        subprocess.run(
            [
                "git",
                "-C",
                str(repo.root),
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(source),
                "module",
            ],
            check=True,
            capture_output=True,
        )
        git(repo.root / "module", "config", "user.name", "Module fixture")
        git(repo.root / "module", "config", "user.email", "fixture@example.invalid")
        git(repo.root, "commit", "-m", "Add module")
        oid = repo.oid("HEAD")
        self.prs[42]["head"]["sha"] = oid
        git(self.primary, "push", "upstream", f"{oid}:refs/pull/42/head")
        return repo.root / "module"

    def test_clean_submodule_with_normal_remote_covered_refs_is_eligible(self):
        repo = self.pair()
        self.module(repo)
        entry = self.assess(repo)
        self.assertTrue(entry["eligible"], entry)
        self.assertEqual([row["path"] for row in entry["submodules"]], ["module"])

    def test_submodule_private_branch_work_keeps_parent(self):
        repo = self.pair()
        module = self.module(repo)
        original = git(module, "rev-parse", "HEAD")
        extra = git(
            module, "commit-tree", "HEAD^{tree}", "-p", original, "-m", "Private work"
        )
        git(module, "branch", "private-work", extra)
        entry = self.assess(repo)
        self.assertIn("submodule-local-history", self.codes(entry))
        self.assertFalse(entry["eligible"])
        self.assertEqual(git(module, "rev-parse", "HEAD"), original)

    def test_submodule_unknown_private_refs_keep_parent(self):
        repo = self.pair()
        module = self.module(repo)
        git(
            module,
            "update-ref",
            "refs/notes/authored",
            git(module, "rev-parse", "HEAD"),
        )
        entry = self.assess(repo)
        self.assertIn("submodule-local-history", self.codes(entry))
        self.assertFalse(entry["eligible"])

    def test_submodule_working_changes_keep_parent(self):
        repo = self.pair()
        module = self.module(repo)
        (module / "code").write_text("unique edits\n")
        entry = self.assess(repo)
        self.assertIn("submodule-work", self.codes(entry))
        self.assertFalse(entry["eligible"])

    def test_submodule_stash_keeps_parent_even_with_clean_checkout(self):
        repo = self.pair()
        module = self.module(repo)
        git(module, "config", "user.name", "Module fixture")
        git(module, "config", "user.email", "fixture@example.invalid")
        (module / "code").write_text("authored edit\n")
        git(module, "stash", "push", "-m", "Important parked work")
        entry = self.assess(repo)
        self.assertIn("submodule-local-history", self.codes(entry))
        self.assertFalse(entry["eligible"])
        self.assertTrue(git(module, "rev-parse", "refs/stash"))

    def test_submodule_ignored_material_uses_same_disposal_policy(self):
        repo = self.pair()
        module = self.module(repo)
        info = Path(
            git(
                module,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "info/exclude",
            )
        )
        with info.open("a") as stream:
            stream.write("\n.env\n")
        (module / ".env").write_text("private inputs\n")
        entry = self.assess(repo, discard_builds=True)
        self.assertIn("ignored-material", self.codes(entry))
        self.assertFalse(entry["eligible"])


if __name__ == "__main__":
    unittest.main()
