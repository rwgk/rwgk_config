"""Global navigation discovers only existing, verified local managed worktrees."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from rwgk_wt.core import Repo

TEST_ROOT = os.environ.get("WT_TEST_WORKTREE_ROOT")


@unittest.skipUnless(TEST_ROOT, "Set WT_TEST_WORKTREE_ROOT under $W/trees/.wt-tests")
class GlobalNavigationTests(unittest.TestCase):
    def setUp(self):
        Path(TEST_ROOT).mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="global-nav-", dir=TEST_ROOT)
        self.addCleanup(temporary.cleanup)
        self.area = Path(temporary.name).resolve()
        self.trees = self.area / "trees"
        self.outside = self.area / "outside"
        self.outside.mkdir()
        self.binaries = self.area / "bin"
        self.binaries.mkdir()
        self.api_marker = self.area / "forge-called"
        for executable in ("gh", "glab"):
            path = self.binaries / executable
            path.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$WT_NAVIGATION_API_MARKER"\n'
                "exit 87\n"
            )
            path.chmod(0o755)
        self.environment = {
            **os.environ,
            "W": str(self.area),
            "PATH": str(self.binaries) + os.pathsep + os.environ["PATH"],
            "WT_NAVIGATION_API_MARKER": str(self.api_marker),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.addCleanup(
            lambda: self.assertFalse(
                self.api_marker.exists(), "navigation called a forge API"
            )
        )

    def git(self, path, *arguments):
        result = subprocess.run(
            ["git", "-C", str(path), *arguments],
            text=True,
            capture_output=True,
            env=self.environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(
        self, key="alpha", provider="github", number=611, *, alias=True, path=None
    ):
        primary = self.area / (key + "-primary")
        primary.mkdir()
        self.git(primary, "init", "--initial-branch=main")
        self.git(primary, "config", "user.name", "WT Navigation Fixture")
        self.git(primary, "config", "user.email", "fixture@example.invalid")
        self.git(primary, "config", "commit.gpgsign", "false")
        hooks = self.area / (key + "-hooks")
        hooks.mkdir()
        self.git(primary, "config", "core.hooksPath", str(hooks))
        self.git(primary, "commit", "--allow-empty", "-m", "Initial")
        prefix = "mr" if provider == "gitlab" else "pr"
        identity = (
            f"gitlab.example/unit/{key}" if provider == "gitlab" else f"unit/{key}"
        )
        url = (
            f"https://{identity}/-/merge_requests/{number}"
            if provider == "gitlab"
            else f"https://github.com/{identity}/pull/{number}"
        )
        remote = (
            f"git@{identity.split('/', 1)[0]}:{identity.split('/', 1)[1]}.git"
            if provider == "gitlab"
            else f"git@github.com:{identity}.git"
        )
        self.git(primary, "remote", "add", "origin", remote)
        branch = f"rwgk/review-{prefix}{number}"
        path = path or self.trees / key / branch.replace("/", "-")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git(primary, "worktree", "add", "-b", branch, str(path), "main")
        repo = Repo(primary, initialized=False)
        repo.config.update(
            key=key,
            primary=str(primary),
            trunk="main",
            trees_root=str(self.trees),
            provider=provider,
            upstream_repo=identity,
            upstream_remote="origin",
            publish_remote="origin",
            archive_remote="origin",
        )
        pr = {"provider": provider, "base_repo": identity, "number": number, "url": url}
        repo.branches.update(
            {
                "main": {"path": str(primary), "role": "trunk"},
                branch: {"path": str(path), "role": "review", "pr": pr},
            }
        )
        repo.save()
        if alias:
            (path.parent / f"{prefix}{number}").symlink_to(
                path.name, target_is_directory=True
            )
        return SimpleNamespace(
            primary=primary, path=path, repo=repo, branch=branch, url=url, hooks=hooks
        )

    def invoke(self, *arguments, cwd=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin/git-wt"), *arguments],
            cwd=cwd or self.outside,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_path(self, expected, *arguments, cwd=None):
        result = self.invoke(*arguments, "--print-path", cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(expected.resolve()))
        self.assertEqual(result.stderr, "")

    def snapshot(self, fixture):
        return {
            "catalog": fixture.repo.state_path.read_bytes(),
            "config": (fixture.repo.common / "config").read_bytes(),
            "refs": self.git(fixture.primary, "show-ref"),
            "worktrees": self.git(fixture.primary, "worktree", "list", "--porcelain"),
            "hooks": {
                str(path.relative_to(fixture.hooks)): path.read_bytes()
                for path in fixture.hooks.rglob("*")
                if path.is_file()
            },
            "lock": (fixture.repo.common / "rwgk-wt.lock").exists(),
        }

    def test_number_alias_and_branch_selectors_find_existing_tree_without_alias(self):
        fixture = self.repository(alias=False)
        # Empty .git directories are also used as discovery sentinels on hosts.
        (self.area / ".git").mkdir()
        for command, selector in (
            ("go", "611"),
            ("go", "00611"),
            ("go", "pr611"),
            ("path", "611"),
            ("pr", "611"),
            ("pr", fixture.url),
            ("go", fixture.branch),
            ("go", fixture.branch.removeprefix("rwgk/")),
        ):
            with self.subTest(command=command, selector=selector):
                self.assert_path(fixture.path, command, selector)

    def test_provider_commands_and_explicit_prefixes_filter_same_number(self):
        github = self.repository("github")
        gitlab = self.repository("gitlab", "gitlab")
        for command, selector, path in (
            ("pr", "611", github.path),
            ("mr", "611", gitlab.path),
            ("go", "pr611", github.path),
            ("go", "mr611", gitlab.path),
            ("mr", gitlab.url, gitlab.path),
        ):
            with self.subTest(command=command, selector=selector):
                self.assert_path(path, command, selector)

    def test_ambiguity_lists_projects_urls_paths_and_qualified_commands(self):
        first = self.repository("alpha")
        second = self.repository("beta")
        result = self.invoke("go", "611", "--print-path")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        for fixture in (first, second):
            self.assertIn(fixture.url, result.stderr)
            self.assertIn(str(fixture.path), result.stderr)
        self.assertIn("alpha:611", result.stderr)
        self.assertIn("beta:611", result.stderr)
        self.assert_path(second.path, "go", "beta:611")
        self.assert_path(first.path, "pr", "alpha:611")

    def test_aliases_deduplicate_and_json_returns_verified_physical_path(self):
        fixture = self.repository()
        (fixture.path.parent / "extra-alias").symlink_to(
            fixture.path.name, target_is_directory=True
        )
        result = self.invoke("path", "611", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["path"], str(fixture.path))
        self.assertEqual(value["branch"], fixture.branch)

    def test_lookup_is_read_only_and_allows_dirty_worktrees(self):
        fixture = self.repository("gitlab", "gitlab")
        (fixture.hooks / "pre-push").write_text("# preserve this hook\n")
        (fixture.path / "untracked-notes").write_text("unfinished work\n")
        before = self.snapshot(fixture)
        self.assert_path(fixture.path, "mr", "611")
        self.assertEqual(self.snapshot(fixture), before)
        self.assertEqual(
            (fixture.path / "untracked-notes").read_text(), "unfinished work\n"
        )

    def test_no_match_or_wrong_provider_never_creates_a_pair(self):
        fixture = self.repository("gitlab", "gitlab")
        before = self.snapshot(fixture)
        for command, selector in (
            ("go", "999"),
            ("mr", "999"),
            ("pr", "611"),
            ("go", "pr611"),
        ):
            with self.subTest(command=command, selector=selector):
                result = self.invoke(command, selector)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(str(fixture.path), result.stdout)
        self.assertEqual(self.snapshot(fixture), before)

    def test_missing_root_reports_no_match_without_creating_directories(self):
        result = self.invoke("go", "611")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trees.exists())

    def test_stale_alias_and_changed_worktree_owner_do_not_match(self):
        fixture = self.repository()
        self.git(fixture.primary, "worktree", "remove", str(fixture.path))
        self.git(
            fixture.primary,
            "worktree",
            "add",
            "-b",
            "rwgk/unrelated",
            str(fixture.path),
            "main",
        )
        (fixture.path.parent / "pr999").symlink_to("missing", target_is_directory=True)
        for selector in ("611", "999"):
            with self.subTest(selector=selector):
                result = self.invoke("go", selector)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_symlinks_cannot_import_worktrees_outside_the_trees_root(self):
        fixture = self.repository(path=self.area / "legacy-worktree", alias=False)
        hint = self.trees / "alpha"
        hint.mkdir(parents=True)
        (hint / "pr611").symlink_to(fixture.path, target_is_directory=True)
        result = self.invoke("go", "611")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_current_repository_scope_wins_and_qualifier_explicitly_selects_another(
        self,
    ):
        first = self.repository("alpha", number=611)
        second = self.repository("beta", number=612)
        self.assert_path(first.path, "go", "611", cwd=first.primary)
        missing = self.invoke("go", "612", cwd=first.primary)
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotIn(str(second.path), missing.stdout)
        self.assert_path(second.path, "go", "beta:612", cwd=first.primary)

    def test_unregistered_or_corrupt_current_repository_does_not_fall_back(self):
        fixture = self.repository()
        unregistered = self.area / "unregistered"
        unregistered.mkdir()
        self.git(unregistered, "init", "--initial-branch=main")
        result = self.invoke("go", "611", cwd=unregistered)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not registered", result.stderr)
        fixture.repo.state_path.write_text("{")
        result = self.invoke("go", "611", cwd=fixture.primary)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot read", result.stderr)

    def test_corrupt_discovered_catalog_does_not_make_another_match_unique(self):
        self.repository("alpha")
        broken = self.repository("beta")
        broken.repo.state_path.write_text("{")
        result = self.invoke("go", "611")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(broken.repo.state_path), result.stderr)

    def test_invalid_current_git_marker_does_not_fall_back(self):
        self.repository()
        broken = self.area / "broken-git"
        broken.mkdir()
        marker = broken / ".git"
        marker.mkdir()
        (marker / "corrupt").write_text("invalid repository\n")
        result = self.invoke("go", "611", cwd=broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("not a git repository", result.stderr)
        (marker / "corrupt").unlink()
        marker.rmdir()
        marker.write_text("gitdir: nonexistent-git-directory\n")
        result = self.invoke("go", "611", cwd=broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("not a git repository", result.stderr)

    def test_wrong_group_and_symlinked_group_do_not_hide_the_valid_tree(self):
        fixture = self.repository()
        (self.trees / "a-alias").symlink_to(
            fixture.path.parent, target_is_directory=True
        )
        misplaced = self.trees / "beta" / "misplaced"
        misplaced.parent.mkdir()
        branch = "rwgk/misplaced"
        self.git(
            fixture.primary, "worktree", "add", "-b", branch, str(misplaced), "main"
        )
        fixture.repo.branches[branch] = {
            **fixture.repo.branches[fixture.branch],
            "path": str(misplaced),
        }
        fixture.repo.save()
        self.assert_path(fixture.path, "go", "611")

    def test_shell_wrapper_navigates_from_outside_git(self):
        fixture = self.repository("gitlab", "gitlab")
        bashrc = (ROOT / "bashrc").read_text()
        start = bashrc.index("wt() {")
        end = bashrc.index("\n}\n", start) + len("\n}\n")
        environment = {
            **self.environment,
            "PATH": str(ROOT / "bin") + os.pathsep + self.environment["PATH"],
        }
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                bashrc[start:end] + "\nwt mr 611 && pwd -P\n",
            ],
            cwd=self.outside,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(fixture.path))


if __name__ == "__main__":
    unittest.main()
