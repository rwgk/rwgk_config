"""Exercise conservative stack topology checks with disposable Git submodules."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("WT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT / "libexec"))
from rwgk_wt.core import Repo, WTError

GUARD_PATH = Path(
    os.environ.get(
        "WT_SUBMODULE_REBASE_GUARD", REPO_ROOT / "libexec/rwgk_wt/submodule_rebase.py"
    )
)
SPEC = importlib.util.spec_from_file_location(
    "rwgk_wt.rebase_guard_fixture", GUARD_PATH
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
WORKTREE_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


@unittest.skipUnless(
    WORKTREE_ROOT, "Set WT_TEST_WORKTREE_ROOT under /wrk/trees/.wt-tests"
)
class SubmoduleRebaseTests(unittest.TestCase):
    def setUp(self):
        area = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(
            area.is_relative_to(
                (Path(os.environ.get("W", "/wrk")) / "trees/.wt-tests").resolve()
            )
        )
        area.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="rebase-module-", dir=area)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.primary = self.root / "primary"
        self.source = self.root / "module"
        self.remote = self.root / "upstream.git"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update(
            GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"), GIT_CONFIG_NOSYSTEM="1"
        )
        for path in (self.primary, self.source):
            self.git(
                "init", "--quiet", "--initial-branch=main", str(path), cwd=self.root
            )
            self.git("config", "user.name", "Rebase fixture", cwd=path)
            self.git("config", "user.email", "fixture@example.invalid", cwd=path)
        (self.source / "file").write_text("old\n")
        self.git("add", ".", cwd=self.source)
        self.git("commit", "--quiet", "-m", "Old module", cwd=self.source)
        self.old_module = self.git("rev-parse", "HEAD", cwd=self.source)
        (self.source / "file").write_text("new\n")
        self.git("commit", "--quiet", "-am", "New module", cwd=self.source)
        self.new_module = self.git("rev-parse", "HEAD", cwd=self.source)
        self.git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            str(self.source),
            "deps/lib",
        )
        self.module = self.primary / "deps/lib"
        self.git("checkout", "--quiet", "--detach", self.old_module, cwd=self.module)
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Parent")
        self.old = self.git("rev-parse", "HEAD")
        self.git(
            "clone",
            "--quiet",
            "--bare",
            str(self.primary),
            str(self.remote),
            cwd=self.root,
        )
        self.git("remote", "add", "origin", str(self.remote))
        self.feature = self.root / "feature"
        self.git(
            "worktree", "add", "--quiet", "-b", "feature", str(self.feature), "main"
        )
        self.git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            cwd=self.feature,
        )
        self.repo = Repo(self.primary, initialized=False)
        self.repo.config.update(
            primary=str(self.primary),
            trunk="main",
            upstream_remote="origin",
            upstream_repo="",
        )
        self.stack = {"branches": [{"branch": "feature", "base": self.old}]}

    def git(self, *args, cwd=None, extra=None, input=None):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.primary,
            env=dict(self.env, **(extra or {})),
            input=input,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def commit_with_entry(self, parent, mode, oid):
        index = self.root / "temporary-index"
        index.unlink(missing_ok=True)
        env = {"GIT_INDEX_FILE": str(index)}
        self.git("read-tree", parent, extra=env)
        self.git("update-index", "--cacheinfo", f"{mode},{oid},deps/lib", extra=env)
        tree = self.git("write-tree", extra=env)
        return self.git("commit-tree", tree, "-p", parent, "-m", "Entry transition")

    def test_pin_only_rebase_is_allowed_and_post_guard_detects_removal(self):
        tip = self.commit_with_entry(self.old, "160000", self.new_module)
        self.git("update-ref", "refs/heads/feature", tip)
        self.git(
            "-c", "submodule.recurse=false", "reset", "--hard", tip, cwd=self.feature
        )
        self.git(
            "checkout",
            "--quiet",
            "--detach",
            self.new_module,
            cwd=self.feature / "deps/lib",
        )
        captured = guard.before(self.repo, self.stack)
        self.assertEqual({row["branch"] for row in captured}, {"main", "feature"})
        guard.after(self.repo, captured)
        self.git("update-index", "--force-remove", "deps/lib", cwd=self.feature)
        with self.assertRaisesRegex(WTError, "removes or replaces"):
            guard.after(self.repo, captured)

    def test_fresh_remote_trunk_topology_change_is_refused(self):
        blob = self.git("hash-object", "-w", "--stdin", input="ordinary file\n")
        target = self.commit_with_entry(self.old, "100644", blob)
        self.git("push", "--quiet", "origin", f"{target}:refs/heads/main")
        with self.assertRaisesRegex(WTError, "removes or replaces"):
            guard.before(self.repo, self.stack)
        self.assertTrue(self.module.is_dir())

    def test_remove_and_readd_in_replayed_history_is_refused(self):
        blob = self.git("hash-object", "-w", "--stdin", input="ordinary file\n")
        replaced = self.commit_with_entry(self.old, "100644", blob)
        restored = self.commit_with_entry(replaced, "160000", self.old_module)
        self.git("update-ref", "refs/heads/feature", restored)
        with self.assertRaisesRegex(WTError, "replay changes"):
            guard.before(self.repo, self.stack, ["--no-trunk"])
        self.assertTrue((self.feature / "deps/lib/file").is_file())

    def test_unknown_ignored_primary_module_material_is_protected(self):
        exclude = Path(
            self.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "info/exclude",
                cwd=self.module,
            )
        )
        exclude.write_text(".env\n")
        local = self.module / ".env"
        local.write_text("KEEP=1\n")
        with self.assertRaisesRegex(WTError, "Preserve ignored local material"):
            guard.before(self.repo, self.stack, ["--no-trunk"])
        self.assertEqual(local.read_text(), "KEEP=1\n")

    def test_resume_rechecks_module_material_without_requiring_clean_parent(self):
        captured = guard.before(self.repo, self.stack, ["--no-trunk"])
        (self.primary / ".git" / "MERGE_HEAD").write_text(self.old + "\n")
        guard.before_resume(self.repo, captured)
        exclude = Path(
            self.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "info/exclude",
                cwd=self.module,
            )
        )
        exclude.write_text(".env\n")
        (self.module / ".env").write_text("added while paused\n")
        with self.assertRaisesRegex(WTError, "Preserve ignored local material"):
            guard.before_resume(self.repo, captured)

    def test_alignment_refuses_an_unexpected_module_head(self):
        captured = guard.before(self.repo, self.stack, ["--no-trunk"])
        self.git("checkout", "--quiet", "--detach", self.new_module, cwd=self.module)
        with self.assertRaisesRegex(WTError, "HEAD changed during"):
            guard.after(self.repo, captured)

    def test_resume_refuses_changed_module_head(self):
        captured = guard.before(self.repo, self.stack, ["--no-trunk"])
        self.git("checkout", "--quiet", "--detach", self.new_module, cwd=self.module)
        with self.assertRaisesRegex(WTError, "HEAD changed while"):
            guard.before_resume(self.repo, captured)

    def test_resume_accepts_only_detached_checkout_reserved_for_captured_branch(self):
        captured = guard.before(self.repo, self.stack, ["--no-trunk"])
        self.git(
            "-c",
            "submodule.recurse=false",
            "checkout",
            "--quiet",
            "--detach",
            "HEAD",
            cwd=self.feature,
        )
        operation = Path(
            self.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "rebase-merge",
                cwd=self.feature,
            )
        )
        operation.mkdir()
        reservation = operation / "head-name"
        reservation.write_text("refs/heads/feature\n")
        guard.before_resume(self.repo, captured)
        reservation.write_text("refs/heads/unrelated\n")
        with self.assertRaisesRegex(WTError, "ownership changed"):
            guard.before_resume(self.repo, captured)
