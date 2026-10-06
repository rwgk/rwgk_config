#!/usr/bin/env python3
"""Test catalog display with disposable repositories and a read-only GitHub stub.

Set GH_STACK_SHOW to test an alternate helper. Linked worktree coverage uses
GH_STACK_CHECK_WORKTREE_ROOT, under $W/codex_scratch_space/worktrees/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(
    os.environ.get("GH_STACK_SHOW", REPO_ROOT / "bin/gh_stack_show_local_tracking.sh")
)
WORKTREE_ROOT = os.environ.get("GH_STACK_CHECK_WORKTREE_ROOT")


@unittest.skipUnless(shutil.which("git") and shutil.which("jq"), "Git and jq required")
class StackDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gh-stack-show-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--quiet", "--initial-branch=main", str(self.repo))
        self.git("config", "user.name", "Display fixture", cwd=self.repo)
        self.git("config", "user.email", "fixture@example.invalid", cwd=self.repo)
        self.git("commit", "--quiet", "--allow-empty", "-m", "Initial", cwd=self.repo)
        self.catalog = self.repo / ".git/gh-stack"
        self.metadata = {
            "schemaVersion": 1,
            "repository": "github.com:owner/project",
            "stacks": [
                {
                    "id": "stack-one",
                    "number": 123,
                    "trunk": {"branch": "main"},
                    "branches": [
                        {
                            "branch": branch,
                            "pullRequest": {
                                "number": number,
                                "url": f"https://github.com/owner/project/pull/{number}",
                            },
                        }
                        for branch, number in [("layer/one", 10), ("layer/two", 11)]
                    ],
                }
            ],
        }
        self.remote = {
            "open": True,
            "base": {"ref": "main"},
            "pull_requests": [
                {"head": {"ref": branch}, "number": number, "merged_at": None}
                for branch, number in [("layer/one", 10), ("layer/two", 11)]
            ],
        }
        self.calls = self.root / "gh-calls"
        self.response = self.root / "remote.json"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(
            textwrap.dedent("""\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                with open(os.environ["GH_STUB_CALLS"], "a") as log:
                    log.write(json.dumps(sys.argv[1:]) + "\\n")
                print(Path(os.environ["GH_STUB_RESPONSE"]).read_text())
                sys.exit(int(os.environ.get("GH_STUB_STATUS", "0")))
                """)
        )
        stub.chmod(0o755)
        self.env.update(
            PATH=f"{bin_dir}{os.pathsep}{self.env['PATH']}",
            GH_STUB_CALLS=str(self.calls),
            GH_STUB_RESPONSE=str(self.response),
        )

    def git(self, *arguments: str, cwd: Path | None = None) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def show(self, *paths: Path, expected: int = 0) -> str:
        self.catalog.write_text(json.dumps(self.metadata))
        self.response.write_text(json.dumps(self.remote))
        before = (self.catalog.read_bytes(), self.catalog.stat().st_mtime_ns)
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), *map(str, paths)],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(
            before, (self.catalog.read_bytes(), self.catalog.stat().st_mtime_ns)
        )
        if self.calls.exists():
            for line in self.calls.read_text().splitlines():
                self.assertEqual(
                    json.loads(line), ["api", "repos/owner/project/stacks/123"]
                )
        return result.stdout + result.stderr

    def test_matching_stack_and_repository_deduplication(self) -> None:
        output = self.show(self.repo, self.repo)
        self.assertEqual(output.count("Shared catalog:"), 1)
        self.assertIn(f"Shared catalog: {self.catalog}", output)
        self.assertIn("matches GitHub", output)
        self.assertIn("0/2 PRs merged", output)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_explicit_worktree_overrides_git_environment(self) -> None:
        unrelated = self.root / "unrelated"
        self.git("init", "--quiet", str(unrelated))
        self.env.update(
            GIT_DIR=str(unrelated / ".git"),
            GIT_COMMON_DIR=str(unrelated / ".git"),
            GIT_WORK_TREE=str(unrelated),
        )
        output = self.show(self.repo)
        self.assertIn(f"Shared catalog: {self.catalog}", output)
        self.assertIn("matches GitHub", output)
        self.assertNotIn(str(unrelated), output)

    @unittest.skipUnless(WORKTREE_ROOT, "Set GH_STACK_CHECK_WORKTREE_ROOT")
    def test_linked_worktree_uses_shared_catalog(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="gh-stack-show-", dir=WORKTREE_ROOT
        )
        self.addCleanup(temporary.cleanup)
        linked = Path(temporary.name) / "linked"
        self.git(
            "worktree", "add", "--quiet", "-b", "linked", str(linked), cwd=self.repo
        )
        output = self.show(linked, self.repo)
        self.assertEqual(output.count("Shared catalog:"), 1)
        self.assertIn(f"Shared catalog: {self.catalog}", output)
        self.assertIn("matches GitHub", output)
        self.assertNotIn("main worktree:", output)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_import_without_repository_uses_pr_urls(self) -> None:
        self.metadata["repository"] = ""
        output = self.show()
        self.assertIn("Repository: github.com:owner/project", output)
        self.assertIn("matches GitHub", output)

    def test_ambiguous_repository_does_not_guess(self) -> None:
        self.metadata["repository"] = ""
        self.metadata["stacks"][0]["branches"][1]["pullRequest"]["url"] = (
            "https://github.com/different/repository/pull/11"
        )
        output = self.show()
        self.assertIn("Repository: unknown", output)
        self.assertIn("remote state unavailable", output)
        self.assertFalse(self.calls.exists())

    def test_reordered_prs_are_stale(self) -> None:
        self.remote["pull_requests"].reverse()
        output = self.show()
        self.assertIn("stale local snapshot", output)
        self.assertNotIn("matches GitHub", output)

    def test_changed_trunk_is_stale(self) -> None:
        self.remote["base"]["ref"] = "different-trunk"
        self.assertIn("stale local snapshot", self.show())

    def test_newly_merged_pr_is_stale(self) -> None:
        self.remote["pull_requests"][0]["merged_at"] = "2026-10-06T00:00:00Z"
        output = self.show()
        self.assertIn("stale local snapshot", output)
        self.assertIn("1/2 PRs merged", output)

    def test_closed_stack_retains_local_cleanup_hint(self) -> None:
        self.remote["open"] = False
        output = self.show()
        self.assertIn("GitHub stack: 123 [closed;", output)
        self.assertIn("gh stack unstack 123 --local", output)

    def test_api_failure_body_is_not_a_stack(self) -> None:
        self.env["GH_STUB_STATUS"] = "1"
        self.remote = {"message": "Not Found", "status": "404"}
        output = self.show()
        self.assertIn("remote state unavailable", output)
        self.assertIn("layer/one  PR #10", output)
        self.assertNotIn("GitHub stack:", output)
        self.assertNotIn("Hint:", output)

    def test_local_only_stack_does_not_call_github(self) -> None:
        del self.metadata["stacks"][0]["number"]
        output = self.show()
        self.assertIn("local-only snapshot", output)
        self.assertFalse(self.calls.exists())

    def test_empty_catalog_does_not_call_github(self) -> None:
        self.metadata["stacks"] = []
        self.assertIn("No locally tracked stacks.", self.show())
        self.assertFalse(self.calls.exists())

    def test_unsupported_schema_fails(self) -> None:
        self.metadata["schemaVersion"] = 2
        self.assertIn("unsupported gh stack catalog schema", self.show(expected=5))
        self.assertFalse(self.calls.exists())

    def test_nonrepository_reports_failure_and_continues(self) -> None:
        output = self.show(self.root, self.repo, expected=1)
        self.assertIn("not a Git repository", output)
        self.assertIn("matches GitHub", output)


if __name__ == "__main__":
    unittest.main()
