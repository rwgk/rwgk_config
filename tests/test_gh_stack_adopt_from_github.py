#!/usr/bin/env python3
"""Exercise adoption with local publication repositories and a stub GitHub API.

Set GH_STACK_ADOPT to test an alternate executable. Linked worktree coverage is
opt-in through GH_STACK_CHECK_WORKTREE_ROOT under
$W/trees/.wt-tests/.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(
    os.environ.get(
        "GH_STACK_ADOPT", REPO_ROOT / "bin" / "gh_stack_adopt_from_github.py"
    )
)
REAL_GIT = shutil.which("git")
WORKTREE_ROOT = os.environ.get("GH_STACK_CHECK_WORKTREE_ROOT")
REPOSITORY = "owner/project"
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
API_URL = f"https://api.github.com/repos/{REPOSITORY}"
STACK_NUMBER = 3179


@unittest.skipUnless(REAL_GIT, "Git is not installed")
class StackAdoptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="gh-stack-adopt-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "repo"
        self.remote = self.root / "publication.git"
        self.writer = self.root / "github-writer"
        self.stub_bin = self.root / "bin"
        self.stub_bin.mkdir()
        self.config_path = self.root / "github-api.json"
        self.calls_path = self.root / "github-calls.jsonl"
        self.global_config = self.root / "gitconfig"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "GH_"))
        }
        self.env.update(
            GIT_CONFIG_GLOBAL=str(self.global_config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
            PATH=str(self.stub_bin) + os.pathsep + os.environ["PATH"],
            GH_STACK_ADOPT_FIXTURE=str(self.config_path),
            GH_STACK_ADOPT_CALLS=str(self.calls_path),
        )
        self.run_git("init", "--bare", "--quiet", str(self.remote), cwd=self.root)
        self.run_git(
            "config",
            "--global",
            f"url.{self.remote.as_uri()}.insteadOf",
            REMOTE_URL,
            cwd=self.root,
        )
        self.run_git(
            "init", "--quiet", "--initial-branch=main", str(self.repo), cwd=self.root
        )
        self.configure_identity(self.repo)
        self.initial = self.commit_file(
            self.repo, "initial.txt", "initial\n", "Initial"
        )
        self.git("checkout", "--quiet", "-b", "layer/merged")
        self.merged = self.commit_file(
            self.repo, "import.txt", "imported source\n", "Import source"
        )
        self.git("checkout", "--quiet", "-b", "layer/one")
        self.one = self.commit_file(
            self.repo, "lower.txt", "lower\n", "Integrate imported source"
        )
        self.git("checkout", "--quiet", "-b", "layer/two")
        self.two = self.commit_file(
            self.repo, "upper.txt", "upper\n", "Build integrated source"
        )
        self.git("checkout", "--quiet", "main")
        self.git("remote", "add", "upstream", REMOTE_URL)
        self.git(
            "push",
            "--quiet",
            "--set-upstream",
            "upstream",
            "main",
            "layer/merged",
            "layer/one",
            "layer/two",
        )
        self.metadata_path = self.repo / ".git" / "gh-stack"
        self.metadata = {
            "schemaVersion": 1,
            "repository": f"github.com:{REPOSITORY}",
            "stacks": [
                {
                    "id": "12345",
                    "trunk": {"branch": "main", "head": self.initial},
                    "branches": [
                        self.saved_layer(
                            "layer/merged", self.merged, self.initial, 101
                        ),
                        self.saved_layer("layer/one", self.one, self.merged, 102),
                        self.saved_layer("layer/two", self.two, self.one, 103),
                    ],
                }
            ],
        }
        self.write_metadata()
        self.rest = {
            "id": 12345,
            "number": STACK_NUMBER,
            "node_id": "PRS_fixture",
            "url": f"{API_URL}/stacks/{STACK_NUMBER}",
            "base": {"ref": "main"},
            "open": True,
            "pull_requests": [],
        }
        self.api = {"rest": self.rest, "events": {}}
        self.install_gh_stub()
        self.install_git_stub()
        self.automatically_restack()

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

    def configure_identity(self, directory: Path) -> None:
        self.run_git("config", "user.name", "Adoption fixture", cwd=directory)
        self.run_git("config", "user.email", "fixture@example.invalid", cwd=directory)

    def commit_file(
        self, directory: Path, filename: str, contents: str, message: str
    ) -> str:
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        self.run_git("add", "--", filename, cwd=directory)
        self.run_git("commit", "--quiet", "-m", message, cwd=directory)
        return self.run_git("rev-parse", "HEAD", cwd=directory)

    def tip(self, branch: str) -> str:
        return self.git("rev-parse", "--verify", f"refs/heads/{branch}")

    def saved_layer(self, branch: str, head: str, base: str, number: int) -> dict:
        return {
            "branch": branch,
            "head": head,
            "base": base,
            "pullRequest": {
                "number": number,
                "id": f"PR_fixture_{number}",
                "url": f"https://github.com/{REPOSITORY}/pull/{number}",
            },
        }

    def write_metadata(self) -> None:
        self.metadata_path.write_text(json.dumps(self.metadata) + "\n")

    def remote_pr(
        self, branch: str, head: str, base_branch: str, base: str, number: int
    ) -> dict:
        return {
            "url": f"{API_URL}/pulls/{number}",
            "html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
            "id": 10000 + number,
            "node_id": f"PR_fixture_{number}",
            "number": number,
            "title": f"Layer {number}",
            "state": "open",
            "merged_at": None,
            "draft": False,
            "head": {"ref": branch, "sha": head, "repo": {"url": API_URL}},
            "base": {"ref": base_branch, "sha": base, "repo": {"url": API_URL}},
        }

    def automatically_restack(self) -> None:
        self.run_git(
            "clone",
            "--quiet",
            "--branch",
            "main",
            REMOTE_URL,
            str(self.writer),
            cwd=self.root,
        )
        self.configure_identity(self.writer)
        self.run_git("merge", "--squash", "origin/layer/merged", cwd=self.writer)
        self.run_git(
            "commit", "--quiet", "-m", "Squash merged first PR", cwd=self.writer
        )
        self.merged_main = self.run_git("rev-parse", "HEAD", cwd=self.writer)
        self.remote_main = self.commit_file(
            self.writer, "upstream-only.txt", "new trunk contents\n", "Advance trunk"
        )
        self.run_git(
            "checkout",
            "--quiet",
            "-b",
            "layer/one",
            "origin/layer/one",
            cwd=self.writer,
        )
        self.run_git("rebase", "--onto", "main", self.merged, cwd=self.writer)
        self.remote_one = self.run_git("rev-parse", "HEAD", cwd=self.writer)
        self.run_git(
            "checkout",
            "--quiet",
            "-b",
            "layer/two",
            "origin/layer/two",
            cwd=self.writer,
        )
        self.run_git("rebase", "--onto", "layer/one", self.one, cwd=self.writer)
        self.remote_two = self.run_git("rev-parse", "HEAD", cwd=self.writer)
        self.run_git(
            "push",
            "--quiet",
            "--force",
            "origin",
            "main",
            "layer/one",
            "layer/two",
            cwd=self.writer,
        )
        self.run_git("update-ref", "-d", "refs/heads/layer/merged", cwd=self.remote)
        self.git("branch", "-D", "layer/merged")
        merged_pr = self.remote_pr(
            "layer/merged", self.merged, "main", self.initial, 101
        )
        merged_pr.update(state="closed", merged_at="2026-10-21T00:00:00Z")
        self.rest["pull_requests"] = [
            merged_pr,
            self.remote_pr("layer/one", self.remote_one, "main", self.remote_main, 102),
            self.remote_pr(
                "layer/two", self.remote_two, "layer/one", self.remote_one, 103
            ),
        ]
        self.api["events"] = {
            "102": [
                {
                    "beforeCommit": {"oid": self.one},
                    "afterCommit": {"oid": self.remote_one},
                }
            ],
            "103": [
                {
                    "beforeCommit": {"oid": self.two},
                    "afterCommit": {"oid": self.remote_two},
                }
            ],
        }
        self.write_api()

    def write_api(self) -> None:
        self.config_path.write_text(json.dumps(self.api))

    def install_gh_stub(self) -> None:
        stub = self.stub_bin / "gh"
        stub.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent(
                """\
                import json
                import os
                import re
                import subprocess
                import sys
                from pathlib import Path

                arguments = sys.argv[1:]
                with Path(os.environ["GH_STACK_ADOPT_CALLS"]).open("a") as stream:
                    stream.write(json.dumps(arguments) + "\\n")
                fixture = json.loads(Path(os.environ["GH_STACK_ADOPT_FIXTURE"]).read_text())
                if "graphql" in arguments:
                    if fixture.get("graphql_error"):
                        print(json.dumps({"errors": [{"message": fixture["graphql_error"]}]}))
                        sys.exit(1)
                    numbers = [re.search(r"(?:number|prNumber|pr|pullRequestNumber)=(\\d+)$", value)
                               for value in arguments]
                    number = next((match[1] for match in numbers if match), None)
                    events = fixture["events"].get(number, []) if number else [
                        item for values in fixture["events"].values() for item in values]
                    pages = fixture.get("event_pages", {}).get(number)
                    if pages:
                        cursor = next((value.split("=", 1)[1] for value in arguments
                                       if value.startswith("after=")), None)
                        index = int(cursor.split("-")[1]) if cursor else 0
                        more = index + 1 < len(pages)
                        page = {"nodes": pages[index], "pageInfo": {
                            "hasNextPage": more,
                            "endCursor": f"page-{index + 1}" if more else None}}
                    else:
                        page = {"nodes": events,
                                "pageInfo": {"hasNextPage": False, "endCursor": None}}
                    print(json.dumps({"data": {"repository": {"pullRequest": {"timelineItems": page}}}}))
                elif any(value == "repos/owner/project/stacks/3179" for value in arguments):
                    if fixture.get("rest_error"):
                        message, status = fixture["rest_error"]
                        print(json.dumps({"message": message}))
                        print(f"gh: {message} (HTTP {status})", file=sys.stderr)
                        sys.exit(1)
                    mutation = fixture.get("concurrent_ref_update")
                    if mutation:
                        calls = [json.loads(line) for line in Path(
                            os.environ["GH_STACK_ADOPT_CALLS"]).read_text().splitlines()]
                        rest_calls = sum("repos/owner/project/stacks/3179" in call for call in calls)
                        if rest_calls == 2:
                            subprocess.run([mutation["git"], "update-ref", mutation["ref"],
                                            mutation["new"], mutation["old"]],
                                           cwd=mutation["repo"], check=True)
                    print(json.dumps(fixture["rest"]))
                else:
                    print("Unexpected gh request: " + repr(arguments), file=sys.stderr)
                    sys.exit(91)
                """
            )
        )
        stub.chmod(0o755)

    def install_git_stub(self) -> None:
        # Fetch still reaches the isolated bare mirror through insteadOf. The
        # helper observes the endpoint a real GitHub publication remote exposes.
        handler = self.stub_bin / "git-special"
        handler.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent(
                f"""\
                import json
                import os
                import subprocess
                import sys
                from pathlib import Path

                real_git = {str(REAL_GIT)!r}
                arguments = sys.argv[1:]
                fixture = json.loads(Path(os.environ["GH_STACK_ADOPT_FIXTURE"]).read_text())
                if arguments[:2] == ["remote", "get-url"] and arguments[-1] == "upstream":
                    field = "effective_push_url" if "--push" in arguments else "effective_fetch_url"
                    print(fixture.get(field, {REMOTE_URL!r}))
                    raise SystemExit(0)
                checkout = fixture.get("concurrent_checkout")
                if "read-tree" in arguments and checkout and Path.cwd() == Path(checkout["worktree"]):
                    evidence = Path(checkout["evidence"])
                    if not evidence.exists():
                        checkout_env = dict(os.environ)
                        checkout_env.pop("GIT_INDEX_FILE", None)
                        result = subprocess.run([real_git, "checkout", "--quiet", checkout["branch"]],
                                                env=checkout_env, capture_output=True, text=True)
                        evidence.write_text(json.dumps(dict(returncode=result.returncode,
                                                            stderr=result.stderr)))
                if not (fixture.get("fail_ref_commit") and "update-ref" in arguments
                        and "--stdin" in arguments):
                    raise SystemExit(subprocess.call([real_git, *arguments]))
                process = subprocess.Popen([real_git, *arguments], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, text=True)
                for line in sys.stdin:
                    command = line.strip()
                    if command == "commit":
                        process.stdin.close()
                        process.wait()
                        print("Injected ref transaction commit failure", file=sys.stderr)
                        raise SystemExit(93)
                    process.stdin.write(line)
                    process.stdin.flush()
                    if command in ("start", "prepare", "abort"):
                        print(process.stdout.readline().strip(), flush=True)
                process.stdin.close()
                raise SystemExit(process.wait())
                """
            )
        )
        handler.chmod(0o755)
        wrapper = self.stub_bin / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            'case "$1:$2" in\n'
            "  remote:get-url|update-ref:*|-c:submodule.recurse=false) exec "
            f'{shlex.quote(sys.executable)} {shlex.quote(str(handler))} "$@" ;;\n'
            f'  *) exec {shlex.quote(str(REAL_GIT))} "$@" ;;\n'
            "esac\n"
        )
        wrapper.chmod(0o755)

    def adopt(
        self, *arguments: str, succeeds: bool = True, worktree: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                str(STACK_NUMBER),
                "--worktree",
                str(worktree or self.repo),
                "--remote",
                "upstream",
                *arguments,
            ],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def snapshot_local(self) -> tuple:
        files = {
            str(path.relative_to(self.repo)): path.read_bytes()
            for path in self.repo.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.repo).parts
        }
        return (
            self.git(
                "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/"
            ),
            self.metadata_path.read_bytes(),
            (self.repo / ".git" / "config").read_bytes(),
            self.git("symbolic-ref", "HEAD"),
            self.git("status", "--porcelain=v1", "--untracked-files=all"),
            files,
        )

    def calls(self) -> list[list[str]]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def assert_diagnostic(
        self, result: subprocess.CompletedProcess[str], pattern: str
    ) -> None:
        self.assertRegex((result.stdout + result.stderr).lower(), pattern)

    def test_preview_fetches_without_moving_local_branches_or_metadata(self) -> None:
        before = self.snapshot_local()
        result = self.adopt()
        self.assertEqual(self.snapshot_local(), before)
        self.assertEqual(
            self.git("rev-parse", "refs/remotes/upstream/layer/one"), self.remote_one
        )
        self.assert_diagnostic(result, r"preview")
        calls = self.calls()
        self.assertTrue(
            any("repos/owner/project/stacks/3179" in args for args in calls)
        )
        self.assertFalse(any(any("/pulls/" in arg for arg in args) for args in calls))

    def test_apply_adopts_changed_trees_and_is_idempotent_after_preview(self) -> None:
        self.adopt()
        self.adopt("--apply")
        self.assertEqual(self.tip("main"), self.remote_main)
        self.assertEqual(self.tip("layer/one"), self.remote_one)
        self.assertEqual(self.tip("layer/two"), self.remote_two)
        self.assertEqual(
            (self.repo / "upstream-only.txt").read_text(), "new trunk contents\n"
        )
        saved = json.loads(self.metadata_path.read_text())["stacks"][0]
        by_name = {layer["branch"]: layer for layer in saved["branches"]}
        self.assertTrue(by_name["layer/merged"]["pullRequest"]["merged"])
        self.assertEqual(by_name["layer/merged"]["head"], self.merged)
        self.assertEqual(by_name["layer/one"]["base"], self.remote_main)
        self.assertEqual(by_name["layer/two"]["base"], self.remote_one)
        self.assertEqual(by_name["layer/one"]["head"], self.remote_one)
        self.assertEqual(by_name["layer/two"]["head"], self.remote_two)
        self.assertEqual(self.git("status", "--porcelain"), "")
        before = self.snapshot_local()
        self.adopt("--apply")
        self.assertEqual(self.snapshot_local(), before)

    def test_matching_heads_and_metadata_report_already_synchronized(self) -> None:
        self.adopt("--apply")
        # Different JSON formatting does not make the saved state stale.
        self.metadata_path.write_text(
            json.dumps(json.loads(self.metadata_path.read_text()))
        )
        before = self.snapshot_local()
        catalog_inode = self.metadata_path.stat().st_ino
        for arguments in ((), ("--apply",)):
            with self.subTest(arguments=arguments):
                result = self.adopt(*arguments)
                self.assertIn("already synchronized", result.stdout)
                self.assertIn("nothing to adopt", result.stdout)
                self.assertNotIn("Use --apply", result.stdout)
                self.assertEqual(self.snapshot_local(), before)
                self.assertEqual(self.metadata_path.stat().st_ino, catalog_inode)
        result = json.loads(self.adopt("--json").stdout)
        self.assertEqual(result["result"], "already-synchronized")
        self.assertFalse(result["metadata_changed"])

    def test_matching_heads_with_stale_metadata_still_require_adoption(self) -> None:
        self.adopt("--apply")
        self.metadata = json.loads(self.metadata_path.read_text())
        self.metadata["stacks"][0]["branches"][1]["base"] = self.initial
        self.write_metadata()
        before = self.snapshot_local()
        result = json.loads(self.adopt("--json").stdout)
        self.assertTrue(all(row["old"] == row["target"] for row in result["rows"]))
        self.assertEqual(result["result"], "ready")
        self.assertTrue(result["metadata_changed"])
        self.assertFalse(result["already_synchronized"])
        self.assertIn("stack metadata needs updating", self.adopt().stdout)
        self.assertEqual(self.snapshot_local(), before)
        self.adopt("--apply")
        self.assertIn("nothing to adopt", self.adopt().stdout)

    def test_unpublished_commit_is_rejected_even_if_saved_head_matches(self) -> None:
        self.git("checkout", "--quiet", "layer/two")
        unpublished = self.commit_file(
            self.repo, "local.txt", "unpublished\n", "Local work"
        )
        self.metadata["stacks"][0]["branches"][2]["head"] = unpublished
        self.write_metadata()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"publish|local work|witness")
        self.assertEqual(self.snapshot_local(), before)

    def test_absent_publication_witness_is_rejected_without_reflog_fallback(
        self,
    ) -> None:
        self.api["events"] = {}
        self.write_api()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"publish|witness|histor")
        self.assertEqual(self.snapshot_local(), before)

    def test_publication_history_is_paginated_until_witness_is_found(self) -> None:
        self.api["event_pages"] = {
            number: [[], events] for number, events in self.api["events"].items()
        }
        self.write_api()
        self.adopt("--apply")
        graphql_calls = [call for call in self.calls() if "graphql" in call]
        self.assertGreaterEqual(len(graphql_calls), 4)
        self.assertTrue(any("after=page-1" in call for call in graphql_calls))
        self.assertEqual(self.tip("layer/two"), self.remote_two)

    def test_concurrent_local_branch_update_is_preserved(self) -> None:
        external = self.git(
            "commit-tree",
            f"{self.two}^{{tree}}",
            "-p",
            self.two,
            input="Concurrent local work\n",
        )
        self.api["concurrent_ref_update"] = {
            "git": str(REAL_GIT),
            "repo": str(self.repo),
            "ref": "refs/heads/layer/two",
            "old": self.two,
            "new": external,
        }
        self.write_api()
        before_catalog = self.metadata_path.read_bytes()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"concurrent|changed|writer")
        self.assertEqual(self.tip("layer/two"), external)
        self.assertEqual(self.tip("layer/one"), self.one)
        self.assertEqual(self.tip("main"), self.initial)
        self.assertEqual(self.metadata_path.read_bytes(), before_catalog)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_failed_ref_commit_restores_worktree_and_leaves_refs_and_metadata(
        self,
    ) -> None:
        self.api["fail_ref_commit"] = True
        self.write_api()
        before = self.snapshot_local()
        self.adopt("--apply", succeeds=False)
        self.assertEqual(self.snapshot_local(), before)
        self.assertFalse((self.repo / ".git" / "gh-stack-adopt-state").exists())

    def test_api_not_found_is_helpful_and_never_falls_back_to_pr(self) -> None:
        self.api["rest_error"] = ["Not Found", 404]
        self.write_api()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"stack")
        self.assertEqual(self.snapshot_local(), before)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("repos/owner/project/stacks/3179", self.calls()[0])

    def test_api_authentication_failure_is_distinct_from_missing_stack(self) -> None:
        self.api["rest_error"] = ["Bad credentials", 401]
        self.write_api()
        result = self.adopt(succeeds=False)
        self.assert_diagnostic(result, r"auth|credentials|401")
        self.assertEqual(len(self.calls()), 1)

    def test_rest_pr_object_is_rejected_instead_of_treated_as_stack(self) -> None:
        self.api["rest"] = self.rest["pull_requests"][1]
        self.write_api()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"stack")
        self.assertEqual(self.snapshot_local(), before)

    def test_dirty_affected_worktree_is_preserved(self) -> None:
        (self.repo / "initial.txt").write_text("local edits\n")
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"dirty|uncommitted|local changes|clean")
        self.assertEqual(self.snapshot_local(), before)

    def test_ignored_file_that_would_be_overwritten_is_preserved(self) -> None:
        (self.repo / ".git" / "info" / "exclude").write_text("upstream-only.txt\n")
        (self.repo / "upstream-only.txt").write_text("important ignored local work\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"ignored|overwrite|collision|untracked")
        self.assertEqual(self.snapshot_local(), before)

    def test_ignored_directory_that_would_be_replaced_is_preserved(self) -> None:
        (self.repo / ".git" / "info" / "exclude").write_text("upstream-only.txt/\n")
        ignored = self.repo / "upstream-only.txt"
        ignored.mkdir()
        (ignored / "scratch").write_text("important ignored contents\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"ignored|overwrite|collision|untracked")
        self.assertEqual(self.snapshot_local(), before)

    def test_unrelated_ignored_file_does_not_block_adoption(self) -> None:
        (self.repo / ".git" / "info" / "exclude").write_text("cache/\n")
        (self.repo / "cache").mkdir()
        cached = self.repo / "cache" / "scratch.txt"
        cached.write_text("cached work\n")
        self.adopt("--apply")
        self.assertEqual(cached.read_text(), "cached work\n")

    def test_preserves_unrelated_catalog_and_existing_branch_tracking(self) -> None:
        unrelated = {
            "id": "unrelated-stack",
            "trunk": {"branch": "release", "head": self.initial},
            "branches": [
                {"branch": "unrelated", "head": self.initial, "base": self.initial}
            ],
        }
        self.git("branch", "release", self.initial)
        self.git("branch", "unrelated", self.initial)
        self.metadata["stacks"].append(copy.deepcopy(unrelated))
        self.write_metadata()
        self.git("remote", "add", "origin", "https://github.com/my-fork/project.git")
        self.git("config", "branch.layer/one.remote", "origin")
        self.git("config", "branch.layer/one.merge", "refs/heads/different-name")
        before_config = (self.repo / ".git" / "config").read_bytes()
        self.adopt("--apply")
        saved = json.loads(self.metadata_path.read_text())
        self.assertEqual(saved["stacks"][1], unrelated)
        self.assertEqual((self.repo / ".git" / "config").read_bytes(), before_config)
        self.assertEqual(self.tip("unrelated"), self.initial)

    def test_main_can_advance_beyond_stack_without_corrupting_replay_base(self) -> None:
        stack_base = self.remote_main
        self.run_git("checkout", "--quiet", "main", cwd=self.writer)
        newer_main = self.commit_file(
            self.writer, "later.txt", "later trunk change\n", "Advance after restack"
        )
        self.run_git("push", "--quiet", "origin", "main", cwd=self.writer)
        self.adopt("--apply")
        self.assertEqual(self.tip("main"), newer_main)
        self.assertEqual(self.tip("layer/one"), self.remote_one)
        saved = json.loads(self.metadata_path.read_text())["stacks"][0]
        self.assertEqual(saved["trunk"]["head"], newer_main)
        by_name = {layer["branch"]: layer for layer in saved["branches"]}
        self.assertEqual(by_name["layer/one"]["base"], stack_base)
        self.assertNotEqual(stack_base, newer_main)
        result = self.adopt("--apply")
        self.assertIn("nothing to adopt", result.stdout)
        self.assertIn("a future rebase is still needed", result.stdout)

    def test_active_branch_used_as_another_stacks_trunk_is_refused(self) -> None:
        self.git("branch", "unrelated", self.two)
        self.metadata["stacks"].append(
            {
                "id": "other-stack",
                "trunk": {"branch": "layer/one", "head": self.one},
                "branches": [
                    {"branch": "unrelated", "head": self.two, "base": self.one}
                ],
            }
        )
        self.write_metadata()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"another local stack")
        self.assertEqual(self.snapshot_local(), before)

    def test_assume_unchanged_edits_are_preserved(self) -> None:
        self.git("update-index", "--assume-unchanged", "initial.txt")
        (self.repo / "initial.txt").write_text("hidden local edit\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"hidden index flags")
        self.assertEqual(self.snapshot_local(), before)

    def test_effective_remote_url_mismatch_is_refused(self) -> None:
        for field in ("effective_fetch_url", "effective_push_url"):
            with self.subTest(field=field):
                self.api[field] = "https://github.com/unexpected/project.git"
                self.write_api()
                before = self.snapshot_local()
                result = self.adopt("--apply", succeeds=False)
                self.assert_diagnostic(result, r"repo|remote|endpoint")
                self.assertEqual(self.snapshot_local(), before)
                self.api.pop(field)

    def test_json_output_is_machine_readable(self) -> None:
        before = self.snapshot_local()
        result = self.adopt("--json")
        self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertEqual(self.snapshot_local(), before)

    def test_shallow_history_is_refused_before_adoption(self) -> None:
        (self.repo / ".git" / "shallow").write_text(self.initial + "\n")
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"shallow history")
        self.assertEqual(self.snapshot_local(), before)

    def test_cleanup_preserves_a_new_writers_index_lock(self) -> None:
        index_lock = self.repo / ".git" / "index.lock"
        wrapper = textwrap.dedent("""\
                import os
                import runpy
                import sys
                from pathlib import Path

                original_replace = os.replace

                def replace(source, destination):
                    original_replace(source, destination)
                    if Path(source).resolve() == Path(os.environ["ADOPT_TEST_INDEX_LOCK"]).resolve():
                        Path(source).write_text("another Git writer owns this lock\\n")

                os.replace = replace
                sys.argv = sys.argv[1:]
                sys.path.insert(0, str(Path(sys.argv[0]).parent))
                runpy.run_path(sys.argv[0], run_name="__main__")
                """)
        self.env["ADOPT_TEST_INDEX_LOCK"] = str(index_lock)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper,
                str(SCRIPT_PATH),
                str(STACK_NUMBER),
                "--worktree",
                str(self.repo),
                "--remote",
                "upstream",
                "--apply",
            ],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(index_lock.read_text(), "another Git writer owns this lock\n")
        self.assertEqual(self.tip("main"), self.remote_main)

    def test_cross_repository_pr_is_rejected(self) -> None:
        self.rest["pull_requests"][1]["head"]["repo"]["url"] = (
            "https://api.github.com/repos/unexpected/project"
        )
        self.write_api()
        before = self.snapshot_local()
        result = self.adopt("--apply", succeeds=False)
        self.assert_diagnostic(result, r"repo|fork")
        self.assertEqual(self.snapshot_local(), before)

    @unittest.skipUnless(WORKTREE_ROOT, "GH_STACK_CHECK_WORKTREE_ROOT was not supplied")
    def test_concurrent_checkout_is_blocked_before_it_can_change_linked_worktree(
        self,
    ) -> None:
        allowed = (Path(os.environ.get("W", "/wrk")) / "trees" / ".wt-tests").resolve()
        root = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(root.is_relative_to(allowed), str(root))
        root.mkdir(parents=True, exist_ok=True)
        container = tempfile.TemporaryDirectory(prefix="gh-stack-adopt-", dir=root)
        self.addCleanup(container.cleanup)
        linked = Path(container.name) / "one"
        self.git("branch", "alternate", self.one)
        self.git("worktree", "add", "--quiet", str(linked), "layer/one")
        self.addCleanup(self.git, "worktree", "remove", "--force", str(linked))
        evidence = self.root / "checkout-result.json"
        self.api["concurrent_checkout"] = {
            "worktree": str(linked),
            "branch": "alternate",
            "evidence": str(evidence),
        }
        self.write_api()
        self.adopt("--apply")
        result = json.loads(evidence.read_text())
        self.assertNotEqual(result["returncode"], 0, result)
        self.assertIn("index.lock", result["stderr"])
        self.assertEqual(
            self.run_git("symbolic-ref", "--short", "HEAD", cwd=linked), "layer/one"
        )
        self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=linked), self.remote_one)
        self.assertEqual(self.run_git("status", "--porcelain", cwd=linked), "")
        self.assertEqual(self.tip("alternate"), self.one)

    @unittest.skipUnless(WORKTREE_ROOT, "GH_STACK_CHECK_WORKTREE_ROOT was not supplied")
    def test_unrelated_locked_worktree_does_not_block_selected_stack(self) -> None:
        allowed = (Path(os.environ.get("W", "/wrk")) / "trees" / ".wt-tests").resolve()
        root = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(root.is_relative_to(allowed), str(root))
        root.mkdir(parents=True, exist_ok=True)
        container = tempfile.TemporaryDirectory(prefix="gh-stack-adopt-", dir=root)
        self.addCleanup(container.cleanup)
        unrelated = Path(container.name) / "unrelated"
        self.git("branch", "unrelated", self.initial)
        self.git("worktree", "add", "--quiet", str(unrelated), "unrelated")
        self.addCleanup(self.git, "worktree", "remove", "--force", str(unrelated))
        self.git("worktree", "lock", str(unrelated))
        self.addCleanup(self.git, "worktree", "unlock", str(unrelated))
        self.adopt("--apply")
        self.assertEqual(self.tip("layer/two"), self.remote_two)
        self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=unrelated), self.initial)

    @unittest.skipUnless(WORKTREE_ROOT, "GH_STACK_CHECK_WORKTREE_ROOT was not supplied")
    def test_all_linked_worktrees_adopt_without_changing_selected_branches(
        self,
    ) -> None:
        allowed = (Path(os.environ.get("W", "/wrk")) / "trees" / ".wt-tests").resolve()
        root = Path(WORKTREE_ROOT).resolve()
        self.assertTrue(root.is_relative_to(allowed), str(root))
        root.mkdir(parents=True, exist_ok=True)
        container = tempfile.TemporaryDirectory(prefix="gh-stack-adopt-", dir=root)
        self.addCleanup(container.cleanup)
        one = Path(container.name) / "one"
        two = Path(container.name) / "two"
        for path, branch in ((one, "layer/one"), (two, "layer/two")):
            self.git("worktree", "add", "--quiet", str(path), branch)
            self.addCleanup(self.git, "worktree", "remove", "--force", str(path))
        self.adopt("--apply", worktree=two)
        for path, branch, tip in (
            (self.repo, "main", self.remote_main),
            (one, "layer/one", self.remote_one),
            (two, "layer/two", self.remote_two),
        ):
            self.assertEqual(
                self.run_git("symbolic-ref", "--short", "HEAD", cwd=path), branch
            )
            self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=path), tip)
            self.assertEqual(self.run_git("status", "--porcelain", cwd=path), "")
        self.assertTrue((one / "upstream-only.txt").exists())
        self.assertTrue((two / "upper.txt").exists())


if __name__ == "__main__":
    unittest.main()
