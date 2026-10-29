"""Offline PR rebases use durable boundaries, frozen targets and real Git."""

from __future__ import annotations

import contextlib
import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli, github, pr_rebase, submodules
from rwgk_wt.core import Repo, WTError

WORKTREE_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


class LocalRepo(Repo):
    def run(self, argv, **kwargs):
        argv = list(map(str, argv))
        if argv[0] == "git":
            argv[1:1] = ["-c", "protocol.file.allow=always"]
        return super().run(argv, **kwargs)


@unittest.skipUnless(
    WORKTREE_ROOT, "Set WT_TEST_WORKTREE_ROOT under /wrk/trees/.wt-tests"
)
class PullRebaseTests(unittest.TestCase):
    def setUp(self):
        area = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(
            area.is_relative_to(
                (Path(os.environ.get("W", "/wrk")) / "trees/.wt-tests").resolve()
            )
        )
        area.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="pr-rebase-", dir=area)
        self.addCleanup(self.temporary.cleanup)
        self.area = Path(self.temporary.name)
        self.primary = self.area / "primary"
        self.source = self.area / "source"
        self.remote = self.area / "upstream.git"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.area / "records"),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git(self.area, "init", "--quiet", "-b", "main", str(self.primary))
        self.configure(self.primary)
        (self.primary / "base").write_text("base\n")
        self.commit(self.primary, "Base")
        self.base = self.oid(self.primary)
        self.git(
            self.area, "clone", "--quiet", "--bare", str(self.primary), str(self.remote)
        )
        self.git(self.primary, "remote", "add", "upstream", str(self.remote))
        self.git(self.area, "clone", "--quiet", str(self.primary), str(self.source))
        self.configure(self.source)
        self.git(self.source, "checkout", "--quiet", "-b", "contributor-topic")
        self.git(self.source, "remote", "add", "upstream", str(self.remote))
        (self.source / "contributor").write_text("old contribution\n")
        self.commit(self.source, "Contributor original")
        self.original = self.oid(self.source)
        self.live = {
            "number": 42,
            "url": "https://github.com/Acme/project/pull/42",
            "state": "OPEN",
            "author": "contributor",
            "head_repo": "contributor/project",
            "head_ref": "contributor-topic",
            "head_oid": self.original,
            "base_repo": "Acme/project",
            "base_ref": "main",
            "is_draft": False,
            "merge_oid": None,
        }
        self.set_remote()
        self.repo = LocalRepo(self.primary, initialized=False)
        self.repo.config.update(
            key="fixture",
            primary=str(self.primary),
            trunk="main",
            upstream_remote="upstream",
            upstream_repo="Acme/project",
            publish_remote="upstream",
            archive_remote="upstream",
            trees_root=str(self.area / "trees"),
            backtracking=str(self.area / "records"),
        )
        self.repo.branches["main"] = {"path": str(self.primary), "role": "trunk"}
        self.repo.save()
        for patch in (
            mock.patch.object(
                github, "get_pr", side_effect=lambda *a, **kw: copy.deepcopy(self.live)
            ),
            mock.patch.object(github, "_checked_remote", return_value="Acme/project"),
            mock.patch("rwgk_wt.stacks.get_stack", return_value=None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, path, *args, check=True):
        result = subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "-C",
                str(path),
                *map(str, args),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if check and result.returncode:
            self.fail("Git failed: " + " ".join(map(str, args)) + "\n" + result.stderr)
        return result.stdout.strip()

    def configure(self, path):
        self.git(path, "config", "user.name", "PR pull fixture")
        self.git(path, "config", "user.email", "fixture@example.invalid")
        self.git(path, "config", "commit.gpgsign", "false")

    def commit(self, path, message):
        self.git(path, "add", "--all")
        self.git(path, "commit", "--quiet", "-m", message)
        return self.oid(path)

    def oid(self, path, revision="HEAD"):
        return self.git(path, "rev-parse", revision)

    def set_remote(self):
        self.git(
            self.source,
            "push",
            "--quiet",
            "--force",
            "upstream",
            "HEAD:refs/pull/42/head",
        )
        if hasattr(self, "live"):
            self.live["head_oid"] = self.oid(self.source)
        return self.oid(self.source)

    def review(self):
        self.repo.git("fetch", "--no-tags", "upstream", "refs/pull/42/head")
        self.path = self.repo.create("rwgk/review-pr42", "FETCH_HEAD", role="review")
        metadata = self.repo.branches["rwgk/review-pr42"]
        metadata.update(
            pr=copy.deepcopy(self.live), last_checked_out_head=self.live["head_oid"]
        )
        self.repo.save()
        self.repo = LocalRepo(self.path)
        return self.path

    def local_commit(self, filename="local", text="local contribution\n"):
        (self.path / filename).write_text(text)
        return self.commit(self.path, "Local contribution")

    def remote_commit(self, filename="remote", text="remote contribution\n"):
        (self.source / filename).write_text(text)
        self.commit(self.source, "Remote contribution")
        return self.set_remote()

    def assert_retained(self, oid):
        refs = self.repo.git(
            "for-each-ref", "--format=%(objectname)", "refs/rwgk/backtracking"
        )
        self.assertIn(oid, refs.splitlines())
        records = list((self.area / "records").glob("*.json"))
        self.assertTrue(any(oid in p.read_text() for p in records))

    def test_linear_divergence_rebases_and_preserves_history_without_tracking(self):
        self.review()
        old_local = self.local_commit()
        target = self.remote_commit()
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertEqual((self.path / "local").read_text(), "local contribution\n")
        self.assertEqual((self.path / "remote").read_text(), "remote contribution\n")
        self.assert_retained(old_local)
        self.assertNotIn("pr_rebase", self.repo.state)
        self.assertEqual(
            self.git(
                self.path,
                "for-each-ref",
                "--format=%(upstream)",
                "refs/heads/rwgk/review-pr42",
            ),
            "",
        )
        self.assertEqual(self.oid(self.primary), self.base)

    def test_force_rewritten_remote_replays_only_unpublished_suffix(self):
        self.review()
        self.local_commit()
        self.git(self.source, "reset", "--hard", self.base)
        (self.source / "replacement").write_text("rewritten contribution\n")
        self.commit(self.source, "Rewritten contributor history")
        target = self.set_remote()
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertTrue((self.path / "local").exists())
        self.assertTrue((self.path / "replacement").exists())
        self.assertFalse((self.path / "contributor").exists())
        self.assertEqual(
            self.git(self.path, "rev-list", "--count", f"{target}..HEAD"), "1"
        )

    def test_deleted_fork_rewrite_replays_local_commits_through_retained_pr_ref(self):
        self.review()
        self.local_commit()
        self.git(self.source, "reset", "--hard", self.base)
        self.remote_commit("replacement", "preserved in retained PR ref\n")
        self.live["head_repo"] = None
        target = self.live["head_oid"]
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertTrue((self.path / "local").exists())
        self.assertTrue((self.path / "replacement").exists())
        self.assertFalse((self.path / "contributor").exists())
        metadata = self.repo.branches["rwgk/review-pr42"]
        self.assertEqual(
            metadata["associated_source"]["head_repo"], "contributor/project"
        )
        self.assertIsNone(metadata["pr"]["head_repo"])

    def test_remote_rewrite_without_local_commits_adopts_new_head(self):
        self.review()
        self.git(self.source, "reset", "--hard", self.base)
        self.remote_commit("replacement")
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "refreshed")
        self.assertEqual(self.oid(self.path), self.live["head_oid"])
        self.assert_retained(self.original)

    def test_force_rewind_replays_local_suffix_without_resurrecting_removed_pr_commits(
        self,
    ):
        self.review()
        local = self.local_commit()
        self.git(self.source, "reset", "--hard", self.base)
        target = self.set_remote()
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertTrue((self.path / "local").exists())
        self.assertFalse((self.path / "contributor").exists())
        self.assertEqual(
            self.git(self.path, "rev-list", "--count", f"{target}..HEAD"), "1"
        )
        self.assert_retained(local)

    def test_force_rewind_without_local_commits_moves_to_remote_tip(self):
        self.review()
        self.git(self.source, "reset", "--hard", self.base)
        target = self.set_remote()
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "refreshed")
        self.assertEqual(self.oid(self.path), target)
        self.assertFalse((self.path / "contributor").exists())
        self.assert_retained(self.original)

    def test_missing_replay_boundary_refuses_without_changing_local_head(self):
        self.review()
        old_local = self.local_commit()
        self.remote_commit()
        self.repo.branches["rwgk/review-pr42"].pop("last_checked_out_head")
        self.repo.save()
        with self.assertRaisesRegex(WTError, "replay boundary"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), old_local)
        self.assertNotIn("pr_rebase", self.repo.state)

    def test_checkpoint_not_ancestor_of_local_history_refuses(self):
        self.review()
        self.git(self.path, "reset", "--hard", self.base)
        local = self.local_commit()
        self.remote_commit()
        with self.assertRaisesRegex(WTError, "replay boundary"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), local)

    def test_matching_main_merges_are_dropped_as_in_pr3279(self):
        self.review()
        (self.primary / "main-development").write_text("already on main\n")
        main = self.commit(self.primary, "Main advancement")
        self.git(self.path, "merge", "--no-ff", "-m", "Local merge of main", main)
        local_merge = self.oid(self.path)
        self.local_commit()
        self.git(self.source, "fetch", "--quiet", "origin", "main")
        self.git(
            self.source, "merge", "--no-ff", "-m", "Contributor merge of main", main
        )
        remote_merge = self.set_remote()
        self.assertNotEqual(local_merge, remote_merge)
        self.assertEqual(
            self.oid(self.path, local_merge + "^{tree}"),
            self.oid(self.source, remote_merge + "^{tree}"),
        )
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), remote_merge)
        self.assertEqual(
            self.git(self.path, "rev-list", "--count", f"{remote_merge}..HEAD"), "1"
        )

    def test_unrepresented_local_merge_refuses(self):
        self.review()
        (self.primary / "main-development").write_text("main\n")
        main = self.commit(self.primary, "Main advancement")
        self.git(self.path, "merge", "--no-ff", "-m", "Unique merge", main)
        old_local = self.local_commit()
        self.remote_commit()
        with self.assertRaisesRegex(WTError, "Local merge commits"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), old_local)
        self.assertNotIn("pr_rebase", self.repo.state)

    def test_same_parent_merge_with_different_resolution_is_not_dropped(self):
        self.review()
        (self.primary / "main-development").write_text("main version\n")
        main = self.commit(self.primary, "Main advancement")
        self.git(self.path, "merge", "--no-ff", "-m", "Local merge", main)
        (self.path / "main-development").write_text("unique local merge resolution\n")
        self.git(self.path, "add", "main-development")
        self.git(self.path, "commit", "--amend", "--no-edit")
        local = self.local_commit()
        self.git(self.source, "fetch", "--quiet", "origin", "main")
        self.git(self.source, "merge", "--no-ff", "-m", "Contributor merge", main)
        self.set_remote()
        with self.assertRaisesRegex(WTError, "Local merge commits"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), local)
        self.assertEqual(
            (self.path / "main-development").read_text(),
            "unique local merge resolution\n",
        )

    def test_user_rebase_configuration_does_not_move_unrelated_branch_or_set_tracking(
        self,
    ):
        self.review()
        local = self.local_commit()
        self.git(self.path, "branch", "fixture-extra", local)
        self.git(self.path, "config", "rebase.updateRefs", "true")
        self.git(self.path, "config", "rebase.autoStash", "true")
        self.remote_commit()
        pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path, "fixture-extra"), local)
        self.assertEqual(
            self.git(self.path, "config", "--get", "rebase.updateRefs"), "true"
        )
        self.assertEqual(
            self.git(self.path, "config", "--get", "rebase.autoStash"), "true"
        )
        self.assertEqual(
            self.git(
                self.path,
                "for-each-ref",
                "--format=%(upstream)",
                "refs/heads/rwgk/review-pr42",
            ),
            "",
        )

    def test_already_applied_local_patch_is_not_duplicated(self):
        self.review()
        local = self.local_commit("shared-patch", "same change\n")
        target = self.remote_commit("shared-patch", "same change\n")
        self.assertNotEqual(local, target)
        pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), target)
        self.assert_retained(local)

    def conflict(self):
        self.review()
        local = self.local_commit("base", "local version\n")
        target = self.remote_commit("base", "remote version\n")
        with self.assertRaisesRegex(WTError, "wt pull --continue"):
            pr_rebase.start(self.repo)
        self.assertIsNone(self.repo.current_branch())
        self.assertEqual(self.repo.state["pr_rebase"]["target_oid"], target)
        return local, target

    def test_conflict_continue_survives_restart_and_does_not_refetch(self):
        local, target = self.conflict()
        self.remote_commit("later-remote", "new remote work during conflict\n")
        (self.path / "base").write_text("both changes resolved\n")
        self.git(self.path, "add", "base")
        self.repo = LocalRepo(self.path)
        with mock.patch.object(
            github, "get_pr", side_effect=AssertionError("resume fetched")
        ):
            result = pr_rebase.resume(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertFalse((self.path / "later-remote").exists())
        self.assertEqual(
            self.repo.branches["rwgk/review-pr42"]["last_checked_out_head"], target
        )
        self.assert_retained(local)

    def test_conflict_abort_restores_original_branch_and_checkpoint(self):
        local, _ = self.conflict()
        self.repo = LocalRepo(self.path)
        result = pr_rebase.resume(self.repo, abort=True)
        self.assertEqual(result["result"], "aborted")
        self.assertEqual(self.oid(self.path), local)
        self.assertEqual((self.path / "base").read_text(), "local version\n")
        self.assertEqual(
            self.repo.branches["rwgk/review-pr42"]["last_checked_out_head"],
            self.original,
        )
        self.assertNotIn("pr_rebase", self.repo.state)

    def conflict_after_remote_removal(self):
        self.review()
        local = self.local_commit("base", "local version\n")
        self.git(self.source, "rm", "contributor")
        self.remote_commit("base", "remote version\n")
        with self.assertRaisesRegex(WTError, "wt pull --continue"):
            pr_rebase.start(self.repo)
        self.assertFalse((self.path / "contributor").exists())
        return local

    def ignore(self, name):
        exclude = Path(
            self.git(
                self.path,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "info/exclude",
            )
        )
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as stream:
            stream.write(name + "\n")
        self.assertEqual(self.git(self.path, "check-ignore", name), name)

    def test_abort_preserves_ignored_file_recreated_during_conflict(self):
        local = self.conflict_after_remote_removal()
        valuable = self.path / "contributor"
        valuable.write_text("valuable new ignored material\n")
        self.ignore("contributor")
        with self.assertRaisesRegex(WTError, "overwrite ignored files"):
            pr_rebase.resume(self.repo, abort=True)
        self.assertEqual(valuable.read_text(), "valuable new ignored material\n")
        self.assertEqual(self.oid(self.path, "refs/heads/rwgk/review-pr42"), local)
        self.assertIsNone(self.repo.current_branch())
        self.assertIn("pr_rebase", self.repo.state)

    def test_abort_preserves_untracked_file_recreated_during_conflict(self):
        local = self.conflict_after_remote_removal()
        valuable = self.path / "contributor"
        valuable.write_text("valuable new untracked material\n")
        with self.assertRaisesRegex(
            WTError, "Preserve untracked parent files before aborting"
        ):
            pr_rebase.resume(self.repo, abort=True)
        self.assertEqual(valuable.read_text(), "valuable new untracked material\n")
        self.assertEqual(self.oid(self.path, "refs/heads/rwgk/review-pr42"), local)
        self.assertIsNone(self.repo.current_branch())
        self.assertIn("pr_rebase", self.repo.state)

    def test_continue_preserves_new_ignored_file_at_future_replay_path(self):
        self.review()
        self.local_commit("base", "local version\n")
        self.local_commit("future-local", "authored local change\n")
        self.remote_commit("base", "remote version\n")
        with self.assertRaisesRegex(WTError, "wt pull --continue"):
            pr_rebase.start(self.repo)
        valuable = self.path / "future-local"
        valuable.write_text("valuable newly created ignored material\n")
        self.ignore("future-local")
        (self.path / "base").write_text("resolved first conflict\n")
        self.git(self.path, "add", "base")
        with self.assertRaisesRegex(WTError, "overwrite ignored files"):
            pr_rebase.resume(self.repo)
        self.assertEqual(
            valuable.read_text(), "valuable newly created ignored material\n"
        )
        self.assertIsNone(self.repo.current_branch())
        self.assertIn("pr_rebase", self.repo.state)

    def test_navigation_from_primary_finds_paused_rebase_owner(self):
        self.conflict()
        primary = LocalRepo(self.primary)
        self.assertIsNone(primary.owner("rwgk/review-pr42"))
        self.assertEqual(
            pr_rebase.navigation_path(primary, "rwgk/review-pr42"), self.path
        )
        self.assertIsNone(pr_rebase.navigation_path(primary, "rwgk/other-branch"))
        self.assertIsNone(self.repo.current_branch())

    def test_cli_go_from_primary_returns_paused_pr_worktree_without_moving_head(self):
        self.conflict()
        primary = LocalRepo(self.primary)
        paused_head = self.oid(self.path)
        reserved_head = self.oid(self.path, "refs/heads/rwgk/review-pr42")
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "Repo", return_value=primary),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(["go", "pr42", "--print-path"])
        self.assertEqual((status, stderr.getvalue()), (0, ""))
        self.assertEqual(stdout.getvalue().strip(), str(self.path.resolve()))
        self.assertEqual(self.oid(self.path), paused_head)
        self.assertEqual(
            self.oid(self.path, "refs/heads/rwgk/review-pr42"), reserved_head
        )
        self.assertIsNone(self.repo.current_branch())
        self.assertEqual(self.oid(self.primary), self.base)

    def test_navigation_refuses_a_replaced_rebase_branch_marker(self):
        self.conflict()
        marker = Path(
            self.git(
                self.path,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "rebase-merge/head-name",
            )
        )
        marker.write_text("refs/heads/rwgk/unrelated\n")
        with self.assertRaisesRegex(WTError, "Another Git rebase replaced"):
            pr_rebase.navigation_path(LocalRepo(self.primary), "rwgk/review-pr42")
        self.assertIsNone(self.repo.current_branch())

    def test_changed_reserved_branch_refuses_resume(self):
        self.conflict()
        self.git(self.path, "update-ref", "refs/heads/rwgk/review-pr42", self.base)
        with self.assertRaisesRegex(WTError, "reserved PR branch changed"):
            pr_rebase.resume(self.repo, abort=True)
        self.assertIn("pr_rebase", self.repo.state)

    def test_resume_from_another_worktree_refuses(self):
        self.conflict()
        with self.assertRaisesRegex(WTError, "original registered worktree"):
            pr_rebase.resume(LocalRepo(self.primary), abort=True)

    def test_fast_forward_and_local_ahead_need_no_rewrite(self):
        self.review()
        target = self.remote_commit()
        result = pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), target)
        self.assertEqual(result["result"], "refreshed")
        self.assertEqual(result["oid"], target)
        local = self.local_commit()
        result = pr_rebase.start(self.repo)
        self.assertEqual(result["result"], "unchanged")
        self.assertEqual(self.oid(self.path), local)

    def test_ignored_parent_material_cannot_be_overwritten_by_rebase_reset(self):
        (self.source / ".gitignore").write_text("secret\n")
        self.commit(self.source, "Ignore local secret")
        self.set_remote()
        self.review()
        old_local = self.local_commit()
        (self.path / "secret").write_text("valuable local material\n")
        (self.source / "secret").write_text("incoming tracked data\n")
        self.git(self.source, "add", "--force", "secret")
        self.git(self.source, "commit", "--quiet", "-m", "Track former ignored path")
        self.set_remote()
        with self.assertRaisesRegex(WTError, "overwrite ignored files"):
            pr_rebase.start(self.repo)
        self.assertEqual(
            (self.path / "secret").read_text(), "valuable local material\n"
        )
        self.assertEqual(self.oid(self.path), old_local)

    def add_module(self):
        module = self.area / "module-source"
        self.git(self.area, "init", "--quiet", "-b", "main", str(module))
        self.configure(module)
        (module / "module-file").write_text("old module\n")
        (module / ".gitignore").write_text("secret/\n")
        old = self.commit(module, "Old module")
        (module / "module-file").write_text("new module\n")
        new = self.commit(module, "New module")
        self.git(
            self.primary, "submodule", "add", "--quiet", str(module), "deps/module"
        )
        self.git(self.primary / "deps/module", "checkout", "--quiet", "--detach", old)
        parent = self.commit(self.primary, "Initialize module")
        self.git(self.source, "fetch", "--quiet", "origin", "main")
        self.git(self.source, "reset", "--hard", parent)
        (self.source / "contributor").write_text("old contribution\n")
        self.commit(self.source, "Contributor with module")
        self.original = self.set_remote()
        self.module_old, self.module_new = old, new

    def remote_module_update(self):
        self.git(
            self.source,
            "update-index",
            "--cacheinfo",
            "160000," + self.module_new + ",deps/module",
        )
        self.git(self.source, "commit", "--quiet", "-m", "Update module pin")
        return self.set_remote()

    def test_submodule_pin_alignment_and_local_module_refs_are_preserved(self):
        self.add_module()
        self.review()
        module = self.path / "deps/module"
        self.git(module, "branch", "valuable-module-branch", self.module_old)
        self.local_commit()
        target = self.remote_module_update()
        pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertEqual(self.oid(module), self.module_new)
        self.assertEqual(self.oid(module, "valuable-module-branch"), self.module_old)
        records = list((self.area / "records").glob("*.json"))
        self.assertTrue(any("valuable-module-branch" in p.read_text() for p in records))

    def test_submodule_unknown_ignored_material_refuses_before_parent_rewrite(self):
        self.add_module()
        self.review()
        old_local = self.local_commit()
        self.remote_module_update()
        secret = self.path / "deps/module/secret"
        secret.mkdir()
        (secret / "auth").write_text("precious fixture input\n")
        with self.assertRaisesRegex(WTError, "Preserve ignored local material"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), old_local)
        self.assertTrue((secret / "auth").exists())

    def test_submodule_alignment_failure_can_be_resumed_after_restart(self):
        self.add_module()
        self.review()
        self.local_commit()
        target = self.remote_module_update()
        initialize = submodules.initialize
        with (
            mock.patch.object(
                submodules, "initialize", side_effect=WTError("fixture clone failure")
            ),
            self.assertRaisesRegex(WTError, "fixture clone failure"),
        ):
            pr_rebase.start(self.repo)
        self.repo = LocalRepo(self.path)
        self.assertEqual(self.repo.state["pr_rebase"]["phase"], "aligning")
        with self.assertRaisesRegex(WTError, "rebase finished"):
            pr_rebase.resume(self.repo, abort=True)
        with mock.patch.object(submodules, "initialize", wraps=initialize):
            result = pr_rebase.resume(self.repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(self.oid(self.path, "HEAD^"), target)
        self.assertEqual(self.oid(self.path / "deps/module"), self.module_new)
        self.assertNotIn("pr_rebase", self.repo.state)

    def pending_alignment(self):
        self.add_module()
        self.review()
        self.local_commit()
        self.remote_module_update()
        with (
            mock.patch.object(
                submodules,
                "initialize",
                side_effect=WTError("fixture alignment failure"),
            ),
            self.assertRaisesRegex(WTError, "fixture alignment failure"),
        ):
            pr_rebase.start(self.repo)
        self.repo = LocalRepo(self.path)

    def test_changed_parent_head_during_pending_alignment_is_preserved(self):
        self.pending_alignment()
        self.git(self.path, "commit", "--allow-empty", "-m", "Concurrent local work")
        current = self.oid(self.path)
        with self.assertRaisesRegex(WTError, "HEAD changed after PR rebase"):
            pr_rebase.resume(self.repo)
        self.assertEqual(self.oid(self.path), current)
        self.assertEqual(self.oid(self.path / "deps/module"), self.module_old)
        self.assertIn("pr_rebase", self.repo.state)

    def test_dirty_parent_during_pending_alignment_is_preserved(self):
        self.pending_alignment()
        (self.path / "base").write_text("new authored work while alignment paused\n")
        with self.assertRaisesRegex(WTError, "Resolve parent worktree changes"):
            pr_rebase.resume(self.repo)
        self.assertEqual(self.oid(self.path / "deps/module"), self.module_old)
        self.assertIn("pr_rebase", self.repo.state)

    def test_initialized_submodule_removal_refuses_before_reset(self):
        self.add_module()
        self.review()
        local = self.local_commit()
        self.git(self.source, "rm", "--cached", "deps/module")
        self.git(self.source, "commit", "--quiet", "-m", "Remove module")
        self.set_remote()
        with self.assertRaisesRegex(WTError, "removes or replaces"):
            pr_rebase.start(self.repo)
        self.assertEqual(self.oid(self.path), local)
        self.assertTrue((self.path / "deps/module/.git").is_file())


if __name__ == "__main__":
    unittest.main()
