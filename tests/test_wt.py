"""Real Git coverage of pair ownership and worktree-aware stack adapters."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from rwgk_wt import stacks
from rwgk_wt.core import Repo, WTError, branch_name, directory_name, run_command

TEST_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


@unittest.skipUnless(
    TEST_ROOT, "Set WT_TEST_WORKTREE_ROOT to a scratch directory under $W/trees"
)
class WorktreeTests(unittest.TestCase):
    def setUp(self):
        Path(TEST_ROOT).mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="core-", dir=TEST_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.area = Path(self.temporary.name)
        environment = patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.area / "backtracking"),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.primary = self.area / "primary"
        self.primary.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "WT Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "rerere.enabled", "true")
        (self.primary / "base").write_text("base\n")
        self.git("add", "base")
        self.git("commit", "-m", "Base")
        self.repo = Repo(self.primary, initialized=False)
        self.repo.initialize(
            key="fixture",
            repo="unit/fixture",
            trees=str(self.area / "trees"),
            backtracking=str(self.area / "backtracking"),
        )

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.primary,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode:
            self.fail(result.stderr)
        return result.stdout.strip()

    def cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin/git-wt"), *args],
            cwd=cwd or self.primary,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_create_pair_does_not_switch_primary_or_track_trunk(self):
        path = self.repo.create("rwgk/one", "main")
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("branch", "--show-current", cwd=path), "rwgk/one")
        self.assertEqual(
            self.git("for-each-ref", "--format=%(upstream)", "refs/heads/rwgk/one"), ""
        )
        self.assertEqual(path, self.area / "trees/fixture/rwgk-one")
        self.assertTrue(self.repo.doctor()["ok"])

    def test_create_from_topic_uses_registered_trunk(self):
        first = self.repo.create("rwgk/first", "main")
        (first / "topic").write_text("topic\n")
        self.git("add", "topic", cwd=first)
        self.git("commit", "-m", "Topic", cwd=first)
        result = self.cli("new", "second", "--print-path", cwd=first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.git("rev-parse", "HEAD", cwd=Path(result.stdout.strip())),
            self.git("rev-parse", "main"),
        )
        self.assertFalse((Path(result.stdout.strip()) / "topic").exists())

    def test_existing_unoccupied_branch_retains_tip(self):
        self.git("branch", "rwgk/legacy")
        before = self.git("rev-parse", "rwgk/legacy")
        path = self.repo.create("rwgk/legacy", "main")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=path), before)

    def test_occupied_legacy_branch_is_never_stolen(self):
        existing = self.area / "legacy"
        self.git("worktree", "add", "-b", "rwgk/legacy", str(existing))
        with self.assertRaises(WTError):
            self.repo.create("rwgk/legacy", "main")
        self.assertEqual(
            self.git("branch", "--show-current", cwd=existing), "rwgk/legacy"
        )

    def test_existing_path_is_not_overwritten(self):
        path = self.repo.canonical_path("rwgk/one")
        path.mkdir(parents=True)
        (path / "precious").write_text("keep")
        with self.assertRaises(WTError):
            self.repo.create("rwgk/one", "main")
        self.assertEqual((path / "precious").read_text(), "keep")

    def test_primary_branch_violation_stops_creation(self):
        self.git("switch", "-c", "wrong")
        with self.assertRaises(WTError):
            self.repo.create("rwgk/one", "main")
        self.assertEqual(self.git("branch", "--show-current"), "wrong")

    def test_pr_alias_points_to_same_physical_worktree(self):
        path = self.repo.create("rwgk/one", "main")
        alias = self.repo.alias(
            "rwgk/one", {"number": 42, "base_repo": "unit/fixture", "state": "OPEN"}
        )
        self.assertEqual(alias.resolve(), path.resolve())
        result = self.cli("go", "pr42", "--print-path")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(path))
        self.assertEqual(len(self.repo.worktrees()), 2)

    def test_alias_collision_preserves_existing_target(self):
        one = self.repo.create("rwgk/one", "main")
        self.repo.alias("rwgk/one", {"number": 42, "base_repo": "unit/fixture"})
        self.repo.create("rwgk/two", "main")
        with self.assertRaises(WTError):
            self.repo.alias("rwgk/two", {"number": 42, "base_repo": "unit/fixture"})
        self.assertEqual((one.parent / "pr42").resolve(), one)

    def test_doctor_reports_legacy_without_migrating_it(self):
        self.git("branch", "old-task")
        report = self.repo.doctor()
        self.assertIn("old-task", report["legacy_branches"])
        self.assertEqual(self.repo.branches, {})

    def test_hidden_index_flag_blocks_clean(self):
        self.git("update-index", "--assume-unchanged", "base")
        (self.primary / "base").write_text("hidden changes")
        with self.assertRaises(WTError):
            self.repo.ensure_clean(self.primary)

    def test_new_and_json_interface(self):
        result = self.cli("new", "one", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["branch"], "rwgk/one")
        listed = json.loads(self.cli("list", "--json").stdout)
        self.assertEqual(listed["branches"][0]["path"], value["path"])

    def remote_writer(self):
        remote = self.area / "upstream.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "upstream", str(remote))
        self.git("push", "upstream", "main")
        writer = self.area / "writer"
        self.git("clone", "--branch", "main", str(remote), str(writer))
        self.git("config", "user.name", "Writer", cwd=writer)
        self.git("config", "user.email", "writer@example.invalid", cwd=writer)
        self.git("config", "commit.gpgsign", "false", cwd=writer)
        (writer / "upstream-change").write_text("upstream\n")
        self.git("add", "upstream-change", cwd=writer)
        self.git("commit", "-m", "Upstream", cwd=writer)
        self.git("push", "origin", "main", cwd=writer)
        self.repo.config["upstream_remote"] = "upstream"
        self.repo.config["upstream_repo"] = ""  # Local-only fixture endpoint.
        self.repo.save()
        return writer

    def test_trunk_update_routes_to_primary_and_preserves_topic(self):
        topic = self.repo.create("rwgk/topic", "main")
        original = self.git("rev-parse", "HEAD", cwd=topic)
        writer = self.remote_writer()
        result = self.cli("trunk", "update", cwd=topic)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.primary), result.stdout)
        self.assertIn(self.git("rev-parse", "HEAD", cwd=writer), result.stdout)
        repeated = self.cli("trunk", "update", cwd=topic)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(repeated.stdout, result.stdout)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=topic), original)
        self.assertEqual(self.git("branch", "--show-current", cwd=topic), "rwgk/topic")

    def test_trunk_update_refuses_divergence_without_merge(self):
        self.remote_writer()
        (self.primary / "local-change").write_text("local\n")
        self.git("add", "local-change")
        self.git("commit", "-m", "Local trunk change")
        original = self.git("rev-parse", "HEAD")
        with self.assertRaises(WTError):
            self.repo.trunk_update()
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("branch", "--show-current"), "main")

    @unittest.skipUnless(shutil.which("gh"), "gh stack required")
    def test_real_stack_add_rebase_and_navigation_preserve_owners(self):
        lower = self.repo.create("rwgk/lower", "main")
        (lower / "lower").write_text("lower\n")
        self.git("add", "lower", cwd=lower)
        self.git("commit", "-m", "Lower", cwd=lower)
        lower_repo = Repo(lower)
        stacks.initialize(lower_repo)
        upper = stacks.add(lower_repo, "upper")
        (upper / "upper").write_text("upper\n")
        self.git("add", "upper", cwd=upper)
        self.git("commit", "-m", "Upper", cwd=upper)
        (lower / "lower").write_text("amended\n")
        self.git("add", "lower", cwd=lower)
        self.git("commit", "--amend", "--no-edit", cwd=lower)
        lower_repo = Repo(lower)
        stacks.rebase(lower_repo, ["--upstack", "--no-trunk"])
        self.assertEqual((upper / "lower").read_text(), "amended\n")
        self.assertEqual(stacks.navigate(lower_repo, "up"), upper)
        for path, branch in (
            (self.primary, "main"),
            (lower, "rwgk/lower"),
            (upper, "rwgk/upper"),
        ):
            self.assertEqual(self.git("branch", "--show-current", cwd=path), branch)
        self.assertTrue((self.area / "backtracking").is_dir())

    def test_local_cleanup_and_restore_via_cli(self):
        created = self.cli("new", "disposable", "--print-path")
        self.assertEqual(created.returncode, 0, created.stderr)
        path = Path(created.stdout.strip())
        result = self.cli("drop", "--local-only", "--apply", "--json", cwd=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout)["operations"][0]
        self.assertFalse(path.exists())
        restored = self.cli("restore", record["id"], "--print-path")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(Path(restored.stdout.strip()), path)
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_cleanup_path_mode_reports_recovery_without_polluting_navigation(self):
        path = self.repo.create("rwgk/disposable", "main")
        result = self.cli("drop", "--local-only", "--apply", "--print-path", cwd=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.primary.resolve()) + "\n")
        records = list((self.area / "backtracking").glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertEqual(record["status"], "complete")
        self.assertFalse(path.exists())
        lines = result.stderr.splitlines()
        remotes = dict.fromkeys(
            self.repo.config[key] for key in ("upstream_remote", "archive_remote")
        )
        self.assertEqual(len(lines), 2 + len(remotes))
        self.assertEqual(
            lines[0], "Removed local branch rwgk/disposable and its worktree."
        )
        self.assertEqual(
            lines[1], f"Backtracking: {records[0].resolve()} (1 recovery ref)"
        )
        self.assertEqual(lines[2:], [f"{remote}: no changes" for remote in remotes])


class NamingTests(unittest.TestCase):
    def test_readable_names_and_collision_safe_legacy_names(self):
        self.assertEqual(directory_name("rwgk/some_new_branch"), "rwgk-some_new_branch")
        self.assertNotEqual(directory_name("rwgk/a-b"), directory_name("rwgk/a/b"))
        for name in ("rwgk/foo/bar", "Mixed/Σ", "a" * 300):
            self.assertNotIn("/", directory_name(name))
            self.assertLess(len(directory_name(name)), 110)

    def test_command_failure_redacts_url_credentials(self):
        from subprocess import CompletedProcess

        with (
            patch(
                "rwgk_wt.core.subprocess.run",
                return_value=CompletedProcess(
                    ["git"], 1, "", "fatal: https://user:secret@example.invalid/repo"
                ),
            ),
            self.assertRaises(WTError) as caught,
        ):
            run_command(["git"])
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))

    def test_new_names_reject_traversal_and_reserved_namespace(self):
        for name in ("../escape", "rwgk/archive", "rwgk/a/b", "Mixed", "a b", "-n"):
            with self.subTest(name=name), self.assertRaises(WTError):
                branch_name(name)


if __name__ == "__main__":
    unittest.main()
