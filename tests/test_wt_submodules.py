"""Offline submodule sharing and lifecycle checks using isolated real Git repos."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import github, lifecycle, stacks, submodules
from rwgk_wt.core import Repo, WTError

WORKTREE_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


class LocalRepo(Repo):
    """Permit local fixture remotes without weakening the production protocol policy."""

    def run(self, argv, **kwargs):
        argv = list(map(str, argv))
        if argv[0] == "git":
            argv[1:1] = ["-c", "protocol.file.allow=always"]
        return super().run(argv, **kwargs)


@unittest.skipUnless(
    WORKTREE_ROOT, "Set WT_TEST_WORKTREE_ROOT under $W/trees for submodule fixtures"
)
class SubmoduleTests(unittest.TestCase):
    def setUp(self):
        root = Path(WORKTREE_ROOT).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="submodules-", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.area = Path(self.temporary.name)
        environment = mock.patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.area / "records"),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.area / "module-source"
        self.initialize_repository(self.source)
        (self.source / "file.txt").write_text("first\n")
        (self.source / ".gitignore").write_text("build/\nsecret/\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-m", "First module revision")
        self.a = self.git(self.source, "rev-parse", "HEAD")
        (self.source / "file.txt").write_text("second\n")
        self.git(self.source, "commit", "-am", "Second module revision")
        self.b = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "checkout", "-b", "alternate", self.a)
        (self.source / "alternate.txt").write_text("unrelated branch\n")
        self.git(self.source, "add", "alternate.txt")
        self.git(self.source, "commit", "-m", "Separate module history")
        self.c = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "tag", "-a", "retained-tag", "-m", "Annotated tag")
        self.git(self.source, "checkout", "main")
        self.remote = self.area / "module.git"
        self.git(self.area, "clone", "--bare", str(self.source), str(self.remote))
        self.primary = self.area / "primary"
        self.initialize_repository(self.primary)
        (self.primary / "base").write_text("superproject\n")
        self.git(self.primary, "add", "base")
        self.git(self.primary, "commit", "-m", "Before submodules")
        self.before_module = self.git(self.primary, "rev-parse", "HEAD")
        self.relative = Path("vendor/library")
        self.primary_module = self.primary / self.relative
        self.git(
            self.primary,
            "submodule",
            "add",
            self.remote.as_uri(),
            self.relative.as_posix(),
        )
        self.git(self.primary_module, "checkout", "--detach", self.a)
        self.git(self.primary, "add", ".gitmodules", self.relative.as_posix())
        self.git(self.primary, "commit", "-m", "Pin first module revision")
        self.repo = LocalRepo(self.primary, initialized=False)
        self.repo.config.update(
            key="submodule-fixture",
            primary=str(self.primary),
            trunk="main",
            upstream_remote="origin",
            upstream_repo="",
            publish_remote="origin",
            archive_remote="origin",
            trees_root=str(self.area / "trees"),
            backtracking=str(self.area / "records"),
        )
        self.repo.save()

    def git(self, path, *args, check=True):
        result = subprocess.run(
            ["git", "-c", "protocol.file.allow=always", *map(str, args)],
            cwd=path,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode:
            self.fail(f"Git failed: {' '.join(map(str, args))}\n{result.stderr}")
        return result.stdout.strip()

    def initialize_repository(self, path):
        path.mkdir()
        self.git(path, "init", "-b", "main")
        self.git(path, "config", "user.name", "Submodule fixture")
        self.git(path, "config", "user.email", "fixture@example.invalid")
        self.git(path, "config", "commit.gpgsign", "false")

    def module(self, topic):
        return topic / self.relative

    def gitdir(self, path):
        return Path(
            self.git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )

    def assert_borrows(self, module, lender):
        objects = self.gitdir(module) / "objects"
        alternates = objects / "info" / "alternates"
        self.assertTrue(alternates.is_file())
        paths = {
            (objects / line).resolve() for line in alternates.read_text().splitlines()
        }
        self.assertIn((self.gitdir(lender) / "objects").resolve(), paths)

    def test_creation_reuses_primary_objects_with_independent_checkout(self):
        topic = self.repo.create("rwgk/first", "main")
        module = self.module(topic)
        self.assert_borrows(module, self.primary_module)
        self.assertNotEqual(self.gitdir(module), self.gitdir(self.primary_module))
        self.assertEqual(self.git(module, "rev-parse", "HEAD"), self.a)
        self.assertEqual(self.git(self.primary_module, "rev-parse", "HEAD"), self.a)
        counts = dict(
            line.split(": ", 1)
            for line in self.git(module, "count-objects", "-v").splitlines()
        )
        self.assertEqual(counts["count"], "0")
        self.assertEqual(counts["in-pack"], "0")
        self.assertEqual(self.git(self.primary, "branch", "--show-current"), "main")

    def test_different_pins_update_without_changing_other_worktrees(self):
        first = self.repo.create("rwgk/first", "main")
        second = self.repo.create("rwgk/second", "main")
        self.git(
            second,
            "update-index",
            "--cacheinfo",
            f"160000,{self.b},{self.relative.as_posix()}",
        )
        self.git(second, "commit", "-m", "Advance module pin")
        submodules.initialize(self.repo, second)
        self.assertEqual(self.git(self.module(second), "rev-parse", "HEAD"), self.b)
        self.assertEqual(self.git(self.module(first), "rev-parse", "HEAD"), self.a)
        self.assertEqual(self.git(self.primary_module, "rev-parse", "HEAD"), self.a)
        self.assert_borrows(self.module(second), self.primary_module)

    def test_lender_gc_retains_history_beyond_selected_gitlink(self):
        topic = self.repo.create("rwgk/borrower", "main")
        module = self.module(topic)
        borrowed = self.git(
            module, "for-each-ref", "--format=%(objectname)"
        ).splitlines()
        self.assertIn(self.c, borrowed)
        lender_refs = self.git(
            self.primary_module, "for-each-ref", "--format=%(refname)"
        ).splitlines()
        self.assertTrue(any(ref.startswith("refs/rwgk/") for ref in lender_refs))
        for ref in lender_refs:
            if not ref.startswith("refs/rwgk/"):
                self.git(self.primary_module, "update-ref", "-d", ref)
        self.git(
            self.primary_module,
            "reflog",
            "expire",
            "--expire=now",
            "--expire-unreachable=now",
            "--all",
        )
        self.git(self.primary_module, "gc", "--prune=now")
        for oid in {self.a, self.b, self.c, *borrowed}:
            self.git(module, "cat-file", "-e", oid)
        self.git(module, "fsck", "--full", "--no-reflogs")

    def prepare_pr_update(self):
        remote = self.area / "superproject.git"
        self.git(self.area, "init", "--bare", str(remote))
        self.git(self.primary, "remote", "add", "upstream", remote.as_uri())
        self.git(self.primary, "config", "submodule.recurse", "true")
        self.repo.config.update(
            upstream_remote="upstream", upstream_repo="fixture/project"
        )
        self.repo.save()
        topic = self.repo.create("rwgk/review-pr42", "main", role="review")
        old = self.git(topic, "rev-parse", "HEAD")
        self.git(self.primary_module, "checkout", "--detach", self.b)
        self.git(self.primary, "add", self.relative.as_posix())
        self.git(self.primary, "commit", "-m", "Advance published module pin")
        new = self.git(self.primary, "rev-parse", "HEAD")
        self.git(self.primary, "push", "upstream", new + ":refs/pull/42/head")
        pr = {
            "number": 42,
            "url": "https://github.com/fixture/project/pull/42",
            "base_repo": "fixture/project",
            "base_ref": "main",
            "head_repo": "contributor/project",
            "head_ref": "topic",
            "head_oid": new,
            "author": "contributor",
            "state": "OPEN",
            "is_draft": False,
        }
        self.repo.branches["rwgk/review-pr42"].update(
            pr={**pr, "head_oid": old}, last_checked_out_head=old
        )
        self.repo.save()
        return topic, new, pr

    def pull_pr(self, topic, pr, repo=None):
        with (
            mock.patch("rwgk_wt.github._checked_remote"),
            mock.patch("rwgk_wt.github.get_pr", return_value=pr),
        ):
            return github.refresh_current(repo or LocalRepo(topic))

    def interrupt_module_update(self, repo):
        original = repo.run

        def interrupt(argv, **kwargs):
            args = list(map(str, argv))
            if "submodule" in args:
                position = args.index("submodule")
                if args[position : position + 4] == [
                    "submodule",
                    "update",
                    "--init",
                    "--checkout",
                ]:
                    raise WTError("Injected submodule initialization interruption")
            return original(argv, **kwargs)

        return mock.patch.object(repo, "run", side_effect=interrupt)

    def interrupted_pr_update(self):
        topic, new, pr = self.prepare_pr_update()
        repo = LocalRepo(topic)
        with (
            self.interrupt_module_update(repo),
            self.assertRaisesRegex(WTError, "Injected submodule"),
        ):
            self.pull_pr(topic, pr, repo)
        pending = Path(
            self.git(
                topic,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "rwgk-wt-submodules-pending.json",
            )
        )
        self.assertTrue(pending.is_file())
        self.assertEqual(self.git(topic, "rev-parse", "HEAD"), new)
        self.assertEqual(self.git(self.module(topic), "rev-parse", "HEAD"), self.a)
        return topic, new, pr, pending

    def test_pr_pull_updates_the_gitlink_checkout_with_recursion_enabled(self):
        topic, new, pr = self.prepare_pr_update()
        result = self.pull_pr(topic, pr)
        self.assertEqual(result["oid"], new)
        self.assertEqual(self.git(self.module(topic), "rev-parse", "HEAD"), self.b)
        self.assertEqual(self.git(self.primary, "rev-parse", "HEAD"), new)
        self.assert_borrows(self.module(topic), self.primary_module)
        LocalRepo(topic).ensure_clean(topic)

    def test_pr_pull_keeps_local_ahead_commit_that_adds_initialized_module(self):
        topic, _, pr = self.prepare_pr_update()
        old = self.git(topic, "rev-parse", "HEAD")
        pr["head_oid"] = old
        self.git(
            self.primary, "push", "--force", "upstream", old + ":refs/pull/42/head"
        )
        extra = Path("vendor/local-added")
        module = topic / extra
        self.git(topic, "submodule", "add", self.remote.as_uri(), extra.as_posix())
        module_head = self.git(module, "rev-parse", "HEAD")
        module_gitdir = self.gitdir(module)
        self.git(topic, "add", ".gitmodules", extra.as_posix())
        self.git(topic, "commit", "-m", "Add dependency in local review work")
        local = self.git(topic, "rev-parse", "HEAD")
        result = self.pull_pr(topic, pr)
        self.assertEqual(result["previous_oid"], local)
        self.assertEqual(result["oid"], local)
        self.assertEqual(result["pr"]["head_oid"], old)
        self.assertEqual(self.git(topic, "rev-parse", "HEAD"), local)
        self.assertEqual(self.git(module, "rev-parse", "HEAD"), module_head)
        self.assertEqual(self.gitdir(module), module_gitdir)
        self.assertEqual((module / "file.txt").read_text(), "second\n")
        LocalRepo(topic).ensure_clean(topic)

    def test_trunk_update_moves_primary_pin_without_changing_borrower(self):
        remote = self.area / "trunk.git"
        self.git(self.area, "clone", "--bare", str(self.primary), str(remote))
        self.git(self.primary, "remote", "add", "origin", remote.as_uri())
        self.git(self.primary, "config", "submodule.recurse", "true")
        topic = self.repo.create("rwgk/stay-old", "main")
        original_parent = self.git(topic, "rev-parse", "HEAD")
        publisher = self.area / "trunk-publisher"
        self.git(self.area, "clone", remote.as_uri(), str(publisher))
        self.git(publisher, "config", "user.name", "Submodule fixture")
        self.git(publisher, "config", "user.email", "fixture@example.invalid")
        self.git(publisher, "config", "commit.gpgsign", "false")
        self.git(
            publisher,
            "update-index",
            "--cacheinfo",
            f"160000,{self.b},{self.relative.as_posix()}",
        )
        self.git(publisher, "commit", "-m", "Advance trunk module pin")
        updated_parent = self.git(publisher, "rev-parse", "HEAD")
        self.git(publisher, "push", "origin", "main")
        result = self.repo.trunk_update()
        self.assertEqual(result["oid"], updated_parent)
        self.assertEqual(self.git(self.primary_module, "rev-parse", "HEAD"), self.b)
        self.assertEqual(self.git(topic, "rev-parse", "HEAD"), original_parent)
        self.assertEqual(self.git(self.module(topic), "rev-parse", "HEAD"), self.a)
        self.assert_borrows(self.module(topic), self.primary_module)

    @unittest.skipUnless(shutil.which("gh"), "gh stack required")
    def test_real_stack_rebase_aligns_module_checkouts_in_their_own_worktrees(self):
        available = subprocess.run(
            ["gh", "stack", "--help"], text=True, capture_output=True, check=False
        )
        if available.returncode:
            self.skipTest("gh stack extension unavailable")
        remote = self.area / "stack.git"
        self.git(self.area, "clone", "--bare", str(self.primary), str(remote))
        self.git(self.primary, "remote", "add", "origin", remote.as_uri())
        self.git(self.primary, "config", "rerere.enabled", "true")
        self.git(self.primary, "config", "submodule.recurse", "true")
        lower = self.repo.create("rwgk/lower", "main")
        (lower / "lower").write_text("lower layer\n")
        self.git(lower, "add", "lower")
        self.git(lower, "commit", "-m", "Lower layer")
        lower_repo = LocalRepo(lower)
        stacks.initialize(lower_repo)
        upper = stacks.add(lower_repo, "upper")
        (upper / "upper").write_text("upper layer\n")
        self.git(upper, "add", "upper")
        self.git(upper, "commit", "-m", "Upper layer")
        self.git(self.primary_module, "checkout", "--detach", self.b)
        self.git(self.primary, "add", self.relative.as_posix())
        self.git(self.primary, "commit", "-m", "Advance trunk module revision")
        self.git(self.primary, "push", "origin", "main")
        result = stacks.rebase(lower_repo)
        self.assertEqual(result["result"], "rebased")
        for path, branch in (
            (self.primary, "main"),
            (lower, "rwgk/lower"),
            (upper, "rwgk/upper"),
        ):
            self.assertEqual(self.git(path, "branch", "--show-current"), branch)
            self.assertEqual(self.git(self.module(path), "rev-parse", "HEAD"), self.b)
            self.assertEqual(lower_repo.owner(branch), path)
            pending = Path(
                self.git(
                    path,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "rwgk-wt-submodules-pending.json",
                )
            )
            self.assertFalse(pending.exists())
        self.assert_borrows(self.module(lower), self.primary_module)
        self.assert_borrows(self.module(upper), self.primary_module)
        self.assertEqual((upper / "lower").read_text(), "lower layer\n")
        self.assertEqual((upper / "upper").read_text(), "upper layer\n")
        self.assertNotIn("submodule_rebase", LocalRepo(self.primary).state)
        self.assertEqual(stacks.navigate(lower_repo, "up"), upper)

    def test_pending_stack_submodule_alignment_blocks_retirement(self):
        branch = "rwgk/pending-stack"
        topic = self.repo.create(branch, "main")
        self.repo.state["submodule_rebase"] = {"phase": "aligning", "captured": []}
        self.repo.save()
        with self.assertRaisesRegex(WTError, "pending submodule work"):
            lifecycle.retire(self.repo, branch, apply=True, local_only=True)
        self.assertTrue(topic.is_dir())
        self.assertEqual(self.git(topic, "branch", "--show-current"), branch)
        self.assertEqual(list((self.area / "records").glob("*.json")), [])

    def test_interrupted_pr_update_retries_its_pending_module_initialization(self):
        topic, new, pr, pending = self.interrupted_pr_update()
        result = self.pull_pr(topic, pr)
        self.assertEqual(result["oid"], new)
        self.assertEqual(self.git(self.module(topic), "rev-parse", "HEAD"), self.b)
        self.assertFalse(pending.exists())
        LocalRepo(topic).ensure_clean(topic)

    def test_interrupted_update_preserves_an_unexpected_user_module_head(self):
        topic, new, pr, pending = self.interrupted_pr_update()
        module = self.module(topic)
        self.git(module, "checkout", "--detach", self.c)
        with self.assertRaisesRegex(WTError, "Submodule HEAD changed"):
            self.pull_pr(topic, pr)
        self.assertEqual(self.git(module, "rev-parse", "HEAD"), self.c)
        self.assertEqual((module / "alternate.txt").read_text(), "unrelated branch\n")
        self.assertEqual(self.git(topic, "rev-parse", "HEAD"), new)
        self.assertTrue(pending.is_file())

    def test_interrupted_creation_reuses_its_incomplete_worktree_on_retry(self):
        branch = "rwgk/interrupted"
        path = self.repo.canonical_path(branch)
        with (
            self.interrupt_module_update(self.repo),
            self.assertRaisesRegex(WTError, "Injected submodule"),
        ):
            self.repo.create(branch, "main")
        self.assertEqual(self.repo.owner(branch), path)
        self.assertTrue(self.repo.branches[branch]["creating"])
        retried = LocalRepo(self.primary)
        self.assertEqual(retried.create(branch, "main"), path)
        self.assertFalse(retried.branches[branch].get("creating", False))
        self.assertEqual(self.git(self.module(path), "rev-parse", "HEAD"), self.a)
        self.assertEqual(len(retried.worktrees()), 2)

    def test_nested_submodule_uses_corresponding_primary_reference(self):
        leaf_source = self.area / "leaf-source"
        self.initialize_repository(leaf_source)
        (leaf_source / "leaf").write_text("nested source\n")
        self.git(leaf_source, "add", "leaf")
        self.git(leaf_source, "commit", "-m", "Nested module")
        leaf_oid = self.git(leaf_source, "rev-parse", "HEAD")
        leaf_remote = self.area / "leaf.git"
        self.git(self.area, "clone", "--bare", str(leaf_source), str(leaf_remote))
        self.git(self.source, "submodule", "add", leaf_remote.as_uri(), "deps/leaf")
        self.git(self.source, "commit", "-am", "Add nested module")
        newer = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "push", self.remote.as_uri(), "main")
        self.git(self.primary_module, "fetch", "origin")
        self.git(self.primary_module, "checkout", "--detach", newer)
        self.git(self.primary, "add", self.relative.as_posix())
        self.git(self.primary, "commit", "-m", "Pin module with nested dependency")
        submodules.initialize(self.repo, self.primary)
        topic = self.repo.create("rwgk/nested", "main")
        nested = self.module(topic) / "deps/leaf"
        self.assertEqual(self.git(nested, "rev-parse", "HEAD"), leaf_oid)
        self.assert_borrows(nested, self.primary_module / "deps/leaf")
        paths = {row["path"] for row in submodules.inspect(self.repo, topic)}
        self.assertIn("vendor/library/deps/leaf", paths)

    def test_existing_independent_submodule_is_not_recloned(self):
        branch = "rwgk/existing"
        path = self.repo.canonical_path(branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.primary, "worktree", "add", "-b", branch, str(path), "main")
        self.repo.branches[branch] = {"path": str(path), "role": "owned"}
        self.repo.save()
        self.git(path, "submodule", "update", "--init")
        module = self.module(path)
        alternates = self.gitdir(module) / "objects/info/alternates"
        self.assertFalse(alternates.exists())
        original = self.gitdir(module)
        submodules.initialize(self.repo, path)
        self.assertEqual(self.gitdir(module), original)
        self.assertFalse(alternates.exists())

    def test_repositories_without_submodules_need_no_initialization(self):
        topic = self.repo.create("rwgk/no-modules", self.before_module)
        self.assertFalse((topic / ".gitmodules").exists())
        self.assertFalse(self.module(topic).exists())
        self.assertEqual(submodules.inspect(self.repo, topic), [])

    def test_local_submodule_branches_survive_retirement_and_restore(self):
        topic = self.repo.create("rwgk/recover", "main")
        module = self.module(topic)
        local = self.git(
            module,
            "-c",
            "user.name=Submodule fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit-tree",
            "HEAD^{tree}",
            "-p",
            "HEAD",
            "-m",
            "Unpublished local history",
        )
        self.git(module, "update-ref", "refs/heads/local-review", local)
        record = lifecycle.retire(
            self.repo, "rwgk/recover", apply=True, local_only=True
        )
        self.assertFalse(topic.exists())
        self.assertTrue(record["submodules"])
        self.assertTrue(record["submodules"][0]["recovery_gitdir"])
        restored = lifecycle.restore(LocalRepo(self.primary), record["id"])
        recovered = self.module(restored)
        self.assertEqual(
            self.git(recovered, "rev-parse", "refs/heads/local-review"), local
        )
        self.assertEqual(self.git(recovered, "rev-parse", "HEAD"), self.a)
        self.assert_borrows(recovered, self.primary_module)

    def test_nested_work_is_protected_even_when_superproject_ignores_modules(self):
        self.git(self.primary, "config", "submodule.vendor/library.ignore", "all")
        self.git(self.primary, "config", "diff.ignoreSubmodules", "all")
        for problem in ("dirty", "untracked", "hidden", "pending", "local-head"):
            with self.subTest(problem=problem):
                branch = "rwgk/" + problem
                topic = self.repo.create(branch, "main")
                module = self.module(topic)
                if problem == "dirty":
                    (module / "file.txt").write_text("keep edit\n")
                elif problem == "untracked":
                    (module / "valuable").write_text("keep untracked\n")
                elif problem == "hidden":
                    self.git(module, "update-index", "--assume-unchanged", "file.txt")
                    (module / "file.txt").write_text("keep hidden edit\n")
                elif problem == "pending":
                    (self.gitdir(module) / "MERGE_HEAD").write_text(self.a + "\n")
                else:
                    self.git(module, "checkout", "--detach", self.b)
                with self.assertRaises(WTError):
                    lifecycle.retire(
                        self.repo,
                        branch,
                        apply=True,
                        local_only=True,
                        discard_builds=True,
                    )
                self.assertTrue(topic.exists())

    def test_restore_recovers_an_unpublished_submodule_gitlink(self):
        topic = self.repo.create("rwgk/unpublished-pin", "main")
        module = self.module(topic)
        self.git(module, "config", "user.name", "Submodule fixture")
        self.git(module, "config", "user.email", "fixture@example.invalid")
        self.git(module, "config", "commit.gpgsign", "false")
        (module / "file.txt").write_text("unpublished module work\n")
        self.git(module, "commit", "-am", "Unpublished module revision")
        unpublished = self.git(module, "rev-parse", "HEAD")
        self.git(topic, "add", self.relative.as_posix())
        self.git(topic, "commit", "-m", "Pin unpublished module revision")
        record = lifecycle.retire(
            self.repo, "rwgk/unpublished-pin", apply=True, local_only=True
        )
        self.assertFalse(topic.exists())
        restored = lifecycle.restore(LocalRepo(self.primary), record["id"])
        self.assertEqual(
            self.git(self.module(restored), "rev-parse", "HEAD"), unpublished
        )
        self.assertEqual(
            (self.module(restored) / "file.txt").read_text(),
            "unpublished module work\n",
        )
        self.assertEqual(self.git(self.primary_module, "rev-parse", "HEAD"), self.a)

    def test_restore_preserves_a_submodule_ref_changed_after_its_baseline(self):
        topic = self.repo.create("rwgk/ref-race", "main")
        self.git(self.module(topic), "update-ref", "refs/heads/local-review", self.a)
        record = lifecycle.retire(
            self.repo, "rwgk/ref-race", apply=True, local_only=True
        )
        original = submodules.preserve
        raced = []

        def concurrent_ref_update(repo, path, record_id, **kwargs):
            snapshots = original(repo, path, record_id, **kwargs)
            if str(record_id).startswith("restore-before-"):
                module = self.module(Path(path))
                self.git(module, "update-ref", "refs/heads/local-review", self.c)
                raced.append(module)
            return snapshots

        with (
            mock.patch.object(
                submodules, "preserve", side_effect=concurrent_ref_update
            ),
            self.assertRaisesRegex(WTError, "Submodule ref changed"),
        ):
            lifecycle.restore(LocalRepo(self.primary), record["id"])
        self.assertTrue(raced)
        self.assertTrue(topic.is_dir())
        self.assertEqual(
            self.git(self.module(topic), "rev-parse", "refs/heads/local-review"), self.c
        )
        self.assertEqual(self.git(self.module(topic), "rev-parse", "HEAD"), self.a)

    def test_nested_ignored_builds_require_consent_and_unknowns_stay_protected(self):
        topic = self.repo.create("rwgk/builds", "main")
        module = self.module(topic)
        (module / "build").mkdir()
        (module / "build/generated").write_text("build output\n")
        with self.assertRaisesRegex(WTError, "discard-builds"):
            lifecycle.retire(self.repo, "rwgk/builds", local_only=True)
        lifecycle.retire(self.repo, "rwgk/builds", local_only=True, discard_builds=True)
        (module / "secret").mkdir()
        precious = module / "secret/valuable"
        precious.write_text("keep ignored work\n")
        with self.assertRaisesRegex(WTError, "not recognized"):
            lifecycle.retire(
                self.repo,
                "rwgk/builds",
                apply=True,
                local_only=True,
                discard_builds=True,
            )
        self.assertEqual(precious.read_text(), "keep ignored work\n")

    def test_new_module_without_primary_checkout_restores_from_retained_cache(self):
        topic = self.repo.create("rwgk/new-module", "main")
        relative = Path("vendor/extra")
        module = topic / relative
        self.git(topic, "submodule", "add", self.remote.as_uri(), relative.as_posix())
        self.git(module, "config", "user.name", "Submodule fixture")
        self.git(module, "config", "user.email", "fixture@example.invalid")
        self.git(module, "config", "commit.gpgsign", "false")
        (module / "file.txt").write_text("unpublished new module work\n")
        self.git(module, "commit", "-am", "Unpublished extra module revision")
        unpublished = self.git(module, "rev-parse", "HEAD")
        self.git(topic, "add", ".gitmodules", relative.as_posix())
        self.git(topic, "commit", "-m", "Add module with unpublished pin")
        record = lifecycle.retire(
            self.repo, "rwgk/new-module", apply=True, local_only=True
        )
        snapshot = next(
            row for row in record["submodules"] if row["path"] == relative.as_posix()
        )
        cache = Path(snapshot["recovery_gitdir"])
        self.assertTrue(cache.is_relative_to(self.repo.common / "rwgk-wt/submodules"))
        restored = lifecycle.restore(LocalRepo(self.primary), record["id"])
        self.assertEqual(
            self.git(restored / relative, "rev-parse", "HEAD"), unpublished
        )
        self.assertEqual(
            (restored / relative / "file.txt").read_text(),
            "unpublished new module work\n",
        )
        self.assertFalse((self.primary / relative).exists())


if __name__ == "__main__":
    unittest.main()
