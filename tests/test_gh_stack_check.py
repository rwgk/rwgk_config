#!/usr/bin/env python3
"""Exercise the read-only checker against disposable Git repositories.

Set GH_STACK_CHECK to test an alternate executable. Linked worktree coverage is
opt-in through GH_STACK_CHECK_WORKTREE_ROOT, which should name an authorized
location under $W/codex_scratch_space/worktrees/.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(
    os.environ.get("GH_STACK_CHECK", REPO_ROOT / "bin" / "gh_stack_check.py")
)
REAL_GIT = shutil.which("git")
WORKTREE_ROOT = os.environ.get("GH_STACK_CHECK_WORKTREE_ROOT")


@unittest.skipUnless(REAL_GIT, "Git is not installed")
class StackCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="gh-stack-check-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "repo"
        self.remote = self.root / "publication.git"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update(
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
        )
        self.run_git("init", "--bare", "--quiet", str(self.remote), cwd=self.root)
        self.run_git(
            "init", "--quiet", "--initial-branch=main", str(self.repo), cwd=self.root
        )
        self.git("config", "user.name", "Checker fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked.txt").write_text("original\n")
        self.git("add", "tracked.txt")
        self.git("commit", "--quiet", "-m", "Initial tree")
        self.initial = self.tip("main")
        self.main = self.commit("Local trunk")
        self.git("checkout", "--quiet", "-b", "layer/one")
        self.one = self.commit("Lower layer")
        self.git("checkout", "--quiet", "-b", "layer/two")
        self.two = self.commit("Upper layer")
        self.git("remote", "add", "upstream", str(self.remote))
        self.git(
            "push",
            "--quiet",
            "--set-upstream",
            "upstream",
            "main",
            "layer/one",
            "layer/two",
        )
        self.metadata_path = self.git_path("gh-stack")
        self.metadata = {
            "schemaVersion": 1,
            "stacks": [
                {
                    "id": "stack-one",
                    "number": 101,
                    "trunk": {"branch": "main", "head": self.main},
                    "branches": [
                        {
                            "branch": "layer/one",
                            "head": self.one,
                            "base": self.main,
                            "pullRequest": {"number": 101},
                        },
                        {
                            "branch": "layer/two",
                            "head": self.two,
                            "base": self.one,
                            "pullRequest": {"number": 102},
                        },
                    ],
                }
            ],
        }
        self.write_metadata()

    def run_git(self, *arguments: str, cwd: Path, input: str | None = None) -> str:
        result = subprocess.run(
            [str(REAL_GIT), *arguments],
            cwd=cwd,
            env=self.env,
            input=input,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def git(self, *arguments: str, input: str | None = None) -> str:
        return self.run_git(*arguments, cwd=self.repo, input=input)

    def tip(self, branch: str) -> str:
        return self.git("rev-parse", f"refs/heads/{branch}")

    def commit(self, message: str) -> str:
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def git_path(self, name: str, *, worktree: Path | None = None) -> Path:
        selected = worktree or self.repo
        path = Path(self.run_git("rev-parse", "--git-path", name, cwd=selected))
        return path if path.is_absolute() else selected / path

    def write_metadata(self, *, path: Path | None = None) -> None:
        (path or self.metadata_path).write_text(json.dumps(self.metadata) + "\n")

    def check(
        self,
        *arguments: str,
        expected: int,
        worktree: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--worktree",
                str(worktree or self.repo),
                "--remote",
                "upstream",
                "--json",
                *arguments,
            ],
            env=env or self.env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"Checker did not emit JSON: {error}\n{result.stdout}\n{result.stderr}"
            )
        self.assertEqual(report["exit_code"], expected, report)
        self.assertIs(report["github_verified"], False)
        self.assertIsInstance(report["issues"], list)
        self.assertIsInstance(report["layers"], list)
        return report

    def assert_issue(
        self, report: dict[str, object], level: str, substring: str
    ) -> None:
        issues = report["issues"]
        self.assertTrue(
            any(
                issue["level"] == level
                and substring.lower()
                in f"{issue.get('scope', '')} {issue['message']}".lower()
                for issue in issues
            ),
            f"Missing {level} diagnostic containing {substring!r}: {issues}",
        )

    def snapshot(self) -> dict[str, tuple[int, int, str]]:
        """Include refs, objects, config, index, metadata, and working files."""
        return {
            str(path.relative_to(self.repo)): (
                path.stat().st_mode,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in self.repo.rglob("*")
            if path.is_file()
        }

    def has_object(self, sha: str) -> bool:
        return (
            subprocess.run(
                [str(REAL_GIT), "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=self.repo,
                env=self.env,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )

    def external_remote_commit(self) -> str:
        other = self.root / "other"
        self.run_git(
            "clone",
            "--quiet",
            "--branch",
            "layer/two",
            str(self.remote),
            str(other),
            cwd=self.root,
        )
        self.run_git("config", "user.name", "Remote writer", cwd=other)
        self.run_git("config", "user.email", "remote@example.invalid", cwd=other)
        self.run_git(
            "commit", "--quiet", "--allow-empty", "-m", "Remote-only commit", cwd=other
        )
        sha = self.run_git("rev-parse", "HEAD", cwd=other)
        self.run_git("push", "--quiet", "origin", "layer/two", cwd=other)
        return sha

    def test_clean_two_layer_stack_passes(self) -> None:
        report = self.check(expected=0)
        self.assertEqual(len(report["layers"]), 2)
        self.check("--expect-synced", expected=0)

    def test_ordinary_local_commit_requires_allow_ahead_and_reports_refresh(
        self,
    ) -> None:
        self.commit("Unpublished upper commit")
        self.check(expected=1)
        report = self.check("--allow-local-ahead", expected=0)
        self.assert_issue(report, "info", "refresh")

    def test_unrelated_saved_head_fails_in_both_modes(self) -> None:
        unrelated = self.git(
            "commit-tree", f"{self.main}^{{tree}}", input="Unrelated root\n"
        )
        self.metadata["stacks"][0]["branches"][1]["head"] = unrelated
        self.write_metadata()
        for mode in ((), ("--allow-local-ahead",)):
            with self.subTest(mode=mode):
                report = self.check(*mode, expected=1)
                self.assert_issue(report, "violation", "head")

    def test_only_upper_layer_stale_is_detected(self) -> None:
        self.commit("Published upper change")
        self.git("push", "--quiet", "upstream", "layer/two")
        self.git("reset", "--quiet", "--hard", self.two)
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "layer/two")
        self.assertFalse(
            any(
                issue["level"] == "violation" and issue.get("scope") == "layer/one"
                for issue in report["issues"]
            )
        )

    def test_allow_ahead_rejects_behind_and_diverged_history(self) -> None:
        remote_tip = self.commit("Published remote change")
        self.git("push", "--quiet", "upstream", "layer/two")
        self.git("reset", "--quiet", "--hard", self.two)
        self.assertTrue(self.has_object(remote_tip))
        behind = self.check("--allow-local-ahead", expected=1)
        self.assert_issue(behind, "violation", "ancestor")
        self.commit("Different unpublished local change")
        diverged = self.check("--allow-local-ahead", expected=1)
        self.assert_issue(diverged, "violation", "ancestor")

    def test_equal_remote_tips_do_not_hide_stale_heads_and_bases(self) -> None:
        branches = self.metadata["stacks"][0]["branches"]
        branches[0]["head"] = self.main
        branches[1]["head"] = self.one
        branches[1]["base"] = self.main
        self.write_metadata()
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "head")
        self.assert_issue(report, "violation", "base")

    def test_merely_ancestral_base_is_rejected_even_in_allow_ahead(self) -> None:
        self.metadata["stacks"][0]["branches"][1]["base"] = self.main
        self.write_metadata()
        for mode in ((), ("--allow-local-ahead",)):
            with self.subTest(mode=mode):
                report = self.check(*mode, expected=1)
                self.assert_issue(report, "violation", "base")

    def test_child_must_contain_current_parent(self) -> None:
        self.git("checkout", "--quiet", "layer/one")
        self.one = self.commit("Lower layer changed")
        self.git("push", "--quiet", "upstream", "layer/one")
        branches = self.metadata["stacks"][0]["branches"]
        branches[0]["head"] = self.one
        branches[1]["base"] = self.one
        self.write_metadata()
        self.git("checkout", "--quiet", "layer/two")
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "parent")

    def test_merged_deleted_layer_does_not_contribute_a_parent(self) -> None:
        self.metadata["stacks"][0]["branches"].insert(
            0,
            {
                "branch": "layer/merged-and-deleted",
                "head": "f" * 40,
                "base": "e" * 40,
                "pullRequest": {"number": 100, "merged": True},
            },
        )
        self.write_metadata()
        self.check(expected=0)

    def test_unmerged_layer_remains_effective_parent(self) -> None:
        # Merge-queue placement is not persisted in schema v1: it remains unmerged.
        branches = self.metadata["stacks"][0]["branches"]
        branches[0]["pullRequest"]["merged"] = False
        branches[1]["base"] = self.main
        self.write_metadata()
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "base")

    def test_queued_unmerged_middle_layer_remains_parent_after_a_merge(self) -> None:
        self.git("checkout", "--quiet", "-b", "layer/three")
        three = self.commit("Third layer above queued middle")
        self.git("push", "--quiet", "--set-upstream", "upstream", "layer/three")
        self.git("branch", "--delete", "--force", "layer/one")
        branches = self.metadata["stacks"][0]["branches"]
        branches[0]["pullRequest"]["merged"] = True
        branches[1]["pullRequest"]["merged"] = False
        branches[1]["base"] = self.main
        branches.append(
            {
                "branch": "layer/three",
                "head": three,
                "base": self.two,
                "pullRequest": {"number": 103},
            }
        )
        # Queued is transient in v1; the middle layer is serialized as unmerged.
        self.write_metadata()
        self.check(expected=0)
        branches[2]["base"] = self.main
        self.write_metadata()
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "layer/three")

    def test_missing_tracking_is_a_violation(self) -> None:
        self.git("branch", "--unset-upstream", "layer/one")
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "tracking")

    def test_tracking_a_different_branch_is_a_violation(self) -> None:
        self.git("branch", "--set-upstream-to=upstream/main", "layer/one")
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "tracking")

    def test_multiple_publication_endpoints_are_incomplete(self) -> None:
        alternate = self.root / "alternate.git"
        self.run_git("init", "--bare", "--quiet", str(alternate), cwd=self.root)
        self.git("remote", "set-url", "--add", "--push", "upstream", str(self.remote))
        self.git("remote", "set-url", "--add", "--push", "upstream", str(alternate))
        report = self.check(expected=2)
        self.assert_issue(report, "incomplete", "push")

    def differing_fetch_url(self) -> Path:
        fetch_repo = self.root / "fetch.git"
        self.run_git("init", "--bare", "--quiet", str(fetch_repo), cwd=self.root)
        self.git("remote", "set-url", "upstream", str(fetch_repo))
        self.git("remote", "set-url", "--push", "upstream", str(self.remote))
        return fetch_repo

    def test_different_fetch_repository_is_a_tracking_violation(self) -> None:
        self.differing_fetch_url()
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "tracking")
        self.assertIn(str(self.remote), json.dumps(report))

    def test_publication_endpoint_is_used_with_separate_matching_tracking(self) -> None:
        self.differing_fetch_url()
        self.git("remote", "add", "published", str(self.remote))
        for branch in ("layer/one", "layer/two"):
            self.git("config", f"branch.{branch}.remote", "published")
            self.git("update-ref", f"refs/remotes/published/{branch}", self.tip(branch))
        report = self.check(expected=0)
        self.assertIn(str(self.remote), json.dumps(report))
        self.assert_issue(report, "info", "fetch")

    def test_missing_publication_branch_is_a_violation(self) -> None:
        self.run_git(
            "--git-dir",
            str(self.remote),
            "update-ref",
            "-d",
            "refs/heads/layer/two",
            cwd=self.root,
        )
        report = self.check(expected=1)
        self.assert_issue(report, "violation", "layer/two")

    def test_missing_selected_remote_is_incomplete(self) -> None:
        self.git("remote", "remove", "upstream")
        self.check(expected=2)

    def test_unavailable_remote_object_is_never_fetched(self) -> None:
        sha = self.external_remote_commit()
        self.assertFalse(self.has_object(sha))
        before = self.snapshot()
        strict = self.check(expected=1)
        self.assert_issue(strict, "violation", "layer/two")
        ahead = self.check("--allow-local-ahead", expected=2)
        self.assert_issue(ahead, "incomplete", "ancestry")
        self.assertFalse(self.has_object(sha))
        self.assertEqual(self.snapshot(), before)

    def test_partial_clone_without_no_lazy_fetch_support_stops_before_object_reads(
        self,
    ) -> None:
        self.git("config", "remote.upstream.promisor", "true")
        sentinel = self.root / "unsafe-object-read"
        env = self.wrapper_env(
            f"""\
            import pathlib
            import subprocess
            import sys

            arguments = sys.argv[1:]
            if "--no-lazy-fetch" in arguments and "--version" in arguments:
                sys.exit(129)
            if any(command in arguments for command in ("status", "cat-file", "merge-base")):
                pathlib.Path({str(sentinel)!r}).touch()
            result = subprocess.run([{str(REAL_GIT)!r}, *arguments], check=False)
            sys.exit(result.returncode)
            """
        )
        before = self.snapshot()
        report = self.check(expected=2, env=env)
        self.assert_issue(report, "incomplete", "partial clone")
        self.assertFalse(
            sentinel.exists(), "Partial-clone guard permitted object/status reads"
        )
        self.assertEqual(self.snapshot(), before)

    def test_https_and_ssh_repository_identity_matches_and_credentials_are_redacted(
        self,
    ) -> None:
        username = "private_fixture_user"
        password = "private_fixture_password"
        token = "private_fixture_query_token"
        fetch = f"https://{username}:{password}@example.invalid/owner/project.git?access_token={token}"
        push = "git@example.invalid:owner/project.git"
        self.git("remote", "set-url", "upstream", fetch)
        self.git("remote", "set-url", "--push", "upstream", push)
        env = self.wrapper_env(
            f"""\
            import subprocess
            import sys

            arguments = sys.argv[1:]
            if "ls-remote" in arguments:
                arguments[-1] = {str(self.remote)!r}
            result = subprocess.run([{str(REAL_GIT)!r}, *arguments], check=False)
            sys.exit(result.returncode)
            """
        )
        before = self.snapshot()
        report = self.check(expected=0, env=env)
        serialized = json.dumps(report)
        for secret in (username, password, token, "access_token"):
            self.assertNotIn(secret, serialized)
        self.assertIn("example.invalid", serialized)
        self.assertEqual(self.snapshot(), before)

    def test_missing_metadata_is_incomplete(self) -> None:
        self.metadata_path.unlink()
        self.check(expected=2)

    def test_malformed_metadata_is_incomplete(self) -> None:
        self.metadata_path.write_text("{invalid json\n")
        self.check(expected=2)

    def test_unsupported_schema_is_incomplete(self) -> None:
        self.metadata["schemaVersion"] = 99
        self.write_metadata()
        report = self.check(expected=2)
        self.assert_issue(report, "incomplete", "schema")

    def test_invalid_or_missing_required_checkpoint_is_incomplete(self) -> None:
        original = copy.deepcopy(self.metadata)
        for field, value in (("head", None), ("base", None), ("head", "not-a-sha")):
            with self.subTest(field=field, value=value):
                self.metadata = copy.deepcopy(original)
                branch = self.metadata["stacks"][0]["branches"][1]
                if value is None:
                    branch.pop(field)
                else:
                    branch[field] = value
                self.write_metadata()
                self.check(expected=2)

    def test_missing_saved_head_object_is_incomplete(self) -> None:
        self.metadata["stacks"][0]["branches"][1]["head"] = "f" * 40
        self.write_metadata()
        self.check("--allow-local-ahead", expected=2)

    def test_ambiguous_stack_requires_an_explicit_selection(self) -> None:
        self.git("checkout", "--quiet", "main")
        self.git("checkout", "--quiet", "-b", "other/layer")
        other = self.commit("Other stack layer")
        self.git("push", "--quiet", "--set-upstream", "upstream", "other/layer")
        self.metadata["stacks"].append(
            {
                "id": "stack-other",
                "number": 201,
                "trunk": {"branch": "main", "head": self.main},
                "branches": [
                    {
                        "branch": "other/layer",
                        "head": other,
                        "base": self.main,
                        "pullRequest": {"number": 201},
                    }
                ],
            }
        )
        self.write_metadata()
        self.git("checkout", "--quiet", "main")
        self.check(expected=2)
        self.check("--stack", "stack-one", expected=0)

    def test_unknown_stack_selection_is_incomplete(self) -> None:
        self.check("--stack", "does-not-exist", expected=2)

    def test_inaccessible_publication_endpoint_is_incomplete(self) -> None:
        self.git("remote", "set-url", "upstream", str(self.root / "absent.git"))
        self.check(expected=2)

    def test_repository_content_and_administrative_files_are_unchanged(self) -> None:
        (self.repo / "tracked.txt").write_text("staged change\n")
        self.git("add", "tracked.txt")
        (self.repo / "tracked.txt").write_text("unstaged change\n")
        (self.repo / "untracked.txt").write_text("unrelated pending work\n")
        before = self.snapshot()
        self.check(expected=0)
        self.assertEqual(self.snapshot(), before)

    def test_persistent_gh_stack_lock_file_does_not_block_verification(self) -> None:
        self.git_path("gh-stack.lock").write_text("persistent extension lock file\n")
        before = self.snapshot()
        self.check(expected=0)
        self.assertEqual(self.snapshot(), before)

    def test_extension_recovery_state_is_a_violation(self) -> None:
        for name in ("gh-stack-rebase-state", "gh-stack-modify-state"):
            with self.subTest(name=name):
                path = self.git_path(name)
                path.write_text("{}\n")
                try:
                    report = self.check(expected=1)
                    self.assert_issue(report, "violation", "unfinished")
                finally:
                    path.unlink()

    def test_fsmonitor_hook_is_not_invoked(self) -> None:
        sentinel = self.root / "fsmonitor-invoked"
        hook = self.root / "fsmonitor-hook"
        hook.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('hook invoked')\n"
        )
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        before = self.snapshot()
        self.check(expected=0)
        self.assertFalse(
            sentinel.exists(), "Read-only observation invoked configured hook"
        )
        self.assertEqual(self.snapshot(), before)

    def test_worktree_argument_overrides_inherited_repository_environment(self) -> None:
        unrelated = self.root / "unrelated"
        self.run_git("init", "--quiet", str(unrelated), cwd=self.root)
        env = dict(self.env)
        env.update(
            GIT_DIR=str(unrelated / ".git"),
            GIT_WORK_TREE=str(unrelated),
            GIT_COMMON_DIR=str(unrelated / ".git"),
        )
        self.check(expected=0, env=env)

    def wrapper_env(self, source: str) -> dict[str, str]:
        wrapper_bin = self.root / "wrapper-bin"
        wrapper_bin.mkdir()
        wrapper = wrapper_bin / "git"
        wrapper.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
        wrapper.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(wrapper_bin) + os.pathsep + env.get("PATH", os.defpath)
        return env

    def concurrent_change_env(self, action: str) -> tuple[dict[str, str], Path]:
        sentinel = self.root / "mutation-done"
        env = self.wrapper_env(
            f"""\
                import pathlib
                import subprocess
                import sys

                result = subprocess.run([{str(REAL_GIT)!r}, *sys.argv[1:]], capture_output=True)
                if "ls-remote" in sys.argv[1:] and not pathlib.Path({str(sentinel)!r}).exists():
                    {action}
                    pathlib.Path({str(sentinel)!r}).touch()
                sys.stdout.buffer.write(result.stdout)
                sys.stderr.buffer.write(result.stderr)
                sys.exit(result.returncode)
                """
        )
        return env, sentinel

    def test_metadata_changed_during_remote_observation_is_incomplete(self) -> None:
        env, sentinel = self.concurrent_change_env(
            f"pathlib.Path({str(self.metadata_path)!r}).write_bytes("
            f"pathlib.Path({str(self.metadata_path)!r}).read_bytes() + b'\\n')"
        )
        report = self.check(expected=2, env=env)
        self.assertTrue(
            sentinel.exists(), "Checker did not observe the remote through Git"
        )
        self.assert_issue(report, "incomplete", "concurrent")

    def test_branch_ref_changed_during_remote_observation_is_incomplete(self) -> None:
        env, sentinel = self.concurrent_change_env(
            f"subprocess.run([{str(REAL_GIT)!r}, '-C', {str(self.repo)!r}, "
            f"'update-ref', 'refs/heads/layer/two', {self.one!r}], check=True)"
        )
        report = self.check(expected=2, env=env)
        self.assertTrue(sentinel.exists())
        self.assert_issue(report, "incomplete", "concurrent")

    @unittest.skipUnless(WORKTREE_ROOT, "GH_STACK_CHECK_WORKTREE_ROOT was not supplied")
    def test_linked_worktree_uses_its_own_metadata(self) -> None:
        root = Path(WORKTREE_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="fixture-", dir=root)
        self.addCleanup(temporary.cleanup)
        selected = Path(temporary.name) / "selected"
        self.git("checkout", "--quiet", "main")
        self.git("worktree", "add", "--quiet", str(selected), "layer/two")
        self.addCleanup(self.git, "worktree", "remove", "--force", str(selected))
        selected_metadata = self.git_path("gh-stack", worktree=selected)
        self.assertNotEqual(selected_metadata, self.metadata_path)
        self.write_metadata(path=selected_metadata)
        self.metadata_path.write_text("This common-gitdir metadata must not be read.\n")
        before = self.snapshot()
        self.check(expected=0, worktree=selected)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
