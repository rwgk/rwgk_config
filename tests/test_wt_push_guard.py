"""Exercise the raw-push guard against a real local receiver and existing hooks."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import push_guard
from rwgk_wt.core import Repo, WTError


def git(path, *args, check=True, env=None):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


class PushGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wt-push-guard-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.primary = self.root / "primary"
        self.receiver = self.root / "receiver.git"
        self.primary.mkdir()
        git(self.primary, "init", "--initial-branch=main")
        git(self.primary, "config", "user.name", "Fixture")
        git(self.primary, "config", "user.email", "fixture@example.invalid")
        git(self.primary, "commit", "--allow-empty", "-m", "Initial")
        self.initial = git(self.primary, "rev-parse", "HEAD").stdout.strip()
        subprocess.run(
            ["git", "init", "--bare", str(self.receiver)],
            check=True,
            capture_output=True,
        )
        git(self.primary, "remote", "add", "origin", str(self.receiver))
        git(
            self.primary, "push", "origin", "main", "main:refs/heads/contributor/change"
        )
        git(self.primary, "config", "push.default", "current")
        tree_root = Path(
            os.environ.get("WT_TEST_WORKTREE_ROOT", "/wrk/trees/.wt-tests")
        )
        tree_root.mkdir(parents=True, exist_ok=True)
        self.tree_directory = Path(
            tempfile.mkdtemp(prefix="push-guard-", dir=tree_root)
        )
        self.addCleanup(shutil.rmtree, self.tree_directory)
        self.review = self.tree_directory / "review"
        self.branch = "rwgk/review-pr17"
        git(
            self.primary, "worktree", "add", "-b", self.branch, str(self.review), "main"
        )
        git(self.review, "commit", "--allow-empty", "-m", "Review contribution")
        self.head = git(self.review, "rev-parse", "HEAD").stdout.strip()
        self.repo = Repo(self.review, initialized=False)
        self.repo.config.update(
            primary=str(self.primary), trunk="main", key="push-guard-fixture"
        )
        self.repo.branches.update(
            {
                "main": {"path": str(self.primary), "role": "trunk"},
                self.branch: {"path": str(self.review), "role": "review"},
            }
        )
        self.repo.save()
        self.hook = self.repo.common / "hooks" / "pre-push"

    def install(self):
        return push_guard.install(self.repo)

    def tip(self, ref):
        result = subprocess.run(
            ["git", "--git-dir", str(self.receiver), "rev-parse", "--verify", ref],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def command(self, *refspecs):
        return [
            "git",
            "-C",
            str(self.review),
            "push",
            "--atomic",
            str(self.receiver),
            *refspecs,
        ]

    def permit(self, *, ref="refs/heads/contributor/change", old=None, new=None):
        return [{"ref": ref, "old": old or self.initial, "new": new or self.head}]

    def original_hook(self, *, exit_code=0, executable=True):
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        self.hook.write_text(
            f"#!{sys.executable}\n"
            "import json,sys\nfrom pathlib import Path\n"
            f"Path({str(self.root / 'hook-stdin')!r}).write_bytes(sys.stdin.buffer.read())\n"
            f"Path({str(self.root / 'hook-args')!r}).write_text(json.dumps(sys.argv[1:]))\n"
            f"raise SystemExit({exit_code})\n"
        )
        self.hook.chmod(0o755 if executable else 0o644)
        return self.hook.read_bytes()

    def test_raw_push_default_current_is_blocked_before_creating_review_branch(self):
        before = (self.repo.common / "config").read_bytes()
        self.install()
        result = git(self.review, "push", "origin", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Raw git push is blocked", result.stderr)
        self.assertIn("wt push", result.stderr)
        self.assertIsNone(self.tip(f"refs/heads/{self.branch}"))
        self.assertEqual((self.repo.common / "config").read_bytes(), before)

    def test_primary_cannot_explicitly_push_a_managed_review_source(self):
        self.install()
        result = git(
            self.primary,
            "push",
            "origin",
            f"{self.branch}:refs/heads/accidental",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Raw git push is blocked", result.stderr)
        self.assertIsNone(self.tip("refs/heads/accidental"))

    def test_primary_trunk_push_still_works(self):
        self.install()
        git(self.primary, "commit", "--allow-empty", "-m", "Trunk update")
        head = git(self.primary, "rev-parse", "HEAD").stdout.strip()
        git(self.primary, "push", "origin", "main")
        self.assertEqual(self.tip("refs/heads/main"), head)

    def test_unmanaged_branch_retains_ordinary_push_behavior(self):
        self.install()
        path = self.tree_directory / "unmanaged"
        git(self.primary, "worktree", "add", "-b", "experiment", str(path), "main")
        git(path, "push", "origin", "experiment")
        self.assertEqual(self.tip("refs/heads/experiment"), self.initial)

    def test_internal_exact_push_runs_original_hook_with_identical_args_and_stdin(self):
        original = self.original_hook()
        self.install()
        result = push_guard.run(
            self.repo,
            self.command(f"{self.head}:refs/heads/contributor/change"),
            destination=str(self.receiver),
            updates=self.permit(),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.tip("refs/heads/contributor/change"), self.head)
        self.assertEqual(
            self.hook.with_name("pre-push.rwgk-original").read_bytes(), original
        )
        self.assertEqual(
            json.loads((self.root / "hook-args").read_text()),
            [str(self.receiver), str(self.receiver)],
        )
        expected = (
            f"{self.head} {self.head} refs/heads/contributor/change {self.initial}\n"
        )
        self.assertEqual((self.root / "hook-stdin").read_bytes(), expected.encode())
        self.assertNotIn(push_guard.PERMIT_ENV, os.environ)

    def test_permit_does_not_allow_extra_ref_or_wrong_old_tip(self):
        self.install()
        for command, updates in (
            (self.command(f"{self.head}:refs/heads/unexpected"), self.permit()),
            (
                self.command(f"{self.head}:refs/heads/contributor/change"),
                self.permit(old="b" * 40),
            ),
        ):
            with self.subTest(command=command):
                result = push_guard.run(
                    self.repo,
                    command,
                    destination=str(self.receiver),
                    updates=updates,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exact-ref permit does not match", result.stderr)
        self.assertIsNone(self.tip("refs/heads/unexpected"))
        self.assertEqual(self.tip("refs/heads/contributor/change"), self.initial)

    def test_create_and_delete_permits_handle_zero_hook_oids(self):
        self.install()
        ref = "refs/tags/rwgk/archive/fixture"
        push_guard.run(
            self.repo,
            self.command(f"{self.head}:{ref}"),
            destination=str(self.receiver),
            updates=[{"ref": ref, "old": None, "new": self.head}],
        )
        self.assertEqual(self.tip(ref), self.head)
        push_guard.run(
            self.repo,
            self.command(f":{ref}"),
            destination=str(self.receiver),
            updates=[{"ref": ref, "old": self.head, "new": None}],
        )
        self.assertIsNone(self.tip(ref))

    def test_recovery_escape_preserves_rejecting_original_hook(self):
        self.original_hook(exit_code=19)
        self.install()
        env = dict(os.environ, RWGK_WT_ALLOW_RAW_PUSH="1")
        result = git(self.review, "push", "origin", check=False, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Raw git push is blocked", result.stderr)
        self.assertTrue((self.root / "hook-stdin").exists())
        self.assertIsNone(self.tip(f"refs/heads/{self.branch}"))

    def test_missing_original_hook_stops_install_and_recovery_push(self):
        self.original_hook()
        self.install()
        self.hook.with_name("pre-push.rwgk-original").unlink()
        with self.assertRaisesRegex(WTError, "original pre-push hook.*missing"):
            self.install()
        env = dict(os.environ, RWGK_WT_ALLOW_RAW_PUSH="1")
        result = git(self.review, "push", "origin", check=False, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("original pre-push hook is missing", result.stderr)
        self.assertIsNone(self.tip(f"refs/heads/{self.branch}"))

    def test_nonexecutable_original_stays_disabled(self):
        self.original_hook(executable=False)
        self.install()
        env = dict(os.environ, RWGK_WT_ALLOW_RAW_PUSH="1")
        git(self.review, "push", "origin", env=env)
        self.assertFalse((self.root / "hook-stdin").exists())

    def test_custom_hooks_path_and_existing_symlink_are_preserved(self):
        custom = self.root / "custom-hooks"
        custom.mkdir()
        git(self.primary, "config", "core.hooksPath", str(custom))
        self.hook = custom / "pre-push"
        target = custom / "real-pre-push"
        self.original_hook()
        self.hook.rename(target)
        self.hook.symlink_to(target.name)
        before = (self.repo.common / "config").read_bytes()
        self.install()
        self.assertEqual((self.repo.common / "config").read_bytes(), before)
        self.assertTrue(custom.joinpath("pre-push.rwgk-original").is_symlink())
        self.assertEqual(self.install()["result"], "already-installed")
        with mock.patch.dict(os.environ, {push_guard.RECOVERY_ENV: "1"}):
            git(self.review, "push", "origin")
        self.assertTrue((self.root / "hook-stdin").exists())

    def test_tracked_custom_hook_is_not_overwritten(self):
        custom = self.primary / ".githooks"
        custom.mkdir()
        self.hook = custom / "pre-push"
        original = self.original_hook()
        git(self.primary, "add", ".githooks/pre-push")
        git(self.primary, "config", "core.hooksPath", str(custom))
        with self.assertRaisesRegex(WTError, "tracked source"):
            self.install()
        self.assertEqual(self.hook.read_bytes(), original)
        wrapper = self.root / "untracked-wrapper-hooks"
        wrapper.mkdir()
        (wrapper / "pre-push").symlink_to(self.hook)
        git(self.primary, "config", "core.hooksPath", str(wrapper))
        self.assertEqual(self.install()["result"], "installed")
        self.assertEqual(self.hook.read_bytes(), original)
        with mock.patch.dict(os.environ, {push_guard.RECOVERY_ENV: "1"}):
            git(self.review, "push", "origin")
        self.assertTrue((self.root / "hook-stdin").exists())

    def test_context_and_permit_file_are_removed_after_failure(self):
        seen = {}

        def failing(argv, **kwargs):
            env = push_guard.subprocess_env(argv, {push_guard.PERMIT_ENV: "stale"})
            seen["path"] = Path(env[push_guard.PERMIT_ENV])
            self.assertTrue(seen["path"].is_file())
            self.assertEqual(seen["path"].stat().st_mode & 0o777, 0o600)
            self.assertNotIn(
                push_guard.PERMIT_ENV, push_guard.subprocess_env(["git", "fetch"], env)
            )
            raise WTError("fixture failure")

        with (
            mock.patch.object(self.repo, "run", side_effect=failing),
            self.assertRaisesRegex(WTError, "fixture failure"),
        ):
            push_guard.run(
                self.repo,
                self.command(f"{self.head}:refs/heads/contributor/change"),
                destination=str(self.receiver),
                updates=self.permit(),
            )
        self.assertFalse(seen["path"].exists())
        self.assertNotIn(
            push_guard.PERMIT_ENV, push_guard.subprocess_env(["git", "push"], {})
        )

    def test_helper_rejects_nonpush_and_noverify_commands(self):
        for command in (
            ["git", "fetch"],
            ["sh", "-c", "git push"],
            ["git", "push", "--no-verify"],
        ):
            with self.assertRaisesRegex(WTError, "runs its hooks"):
                push_guard.run(
                    self.repo,
                    command,
                    destination=str(self.receiver),
                    updates=self.permit(),
                )


if __name__ == "__main__":
    unittest.main()
