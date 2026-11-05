"""Stack overview uses saved relationships without refreshing or changing them."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from rwgk_wt import cli, stacks


class StackViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wt-stack-view-")
        self.addCleanup(temporary.cleanup)
        self.area = Path(temporary.name).resolve()
        self.lower = "rwgk/core"
        self.upper = "rwgk/tests"
        self.stack = {
            "id": "1099",
            "number": 77,
            "trunk": {"branch": "main", "head": "base"},
            "branches": [
                {
                    "branch": branch,
                    "base": "base",
                    "head": "head",
                    "pullRequest": {
                        "number": number,
                        "url": f"https://github.com/Unit/project/pull/{number}",
                        "merged": False,
                    },
                }
                for branch, number in ((self.lower, 42), (self.upper, 43))
            ],
        }
        self.catalog = self.area / "gh-stack"
        self.write_catalog([self.stack])
        config = {
            "provider": "github",
            "upstream_repo": "Unit/project",
            "trunk": "main",
        }
        branches = {
            branch: {"path": str(self.area / branch.replace("/", "-"))}
            for branch in (self.lower, self.upper)
        }
        forbidden = lambda action: Mock(side_effect=AssertionError(action))
        self.repo = SimpleNamespace(
            common=self.area,
            config=config,
            branches=branches,
            state={"config": config, "branches": branches},
            current_branch=Mock(return_value=self.lower),
            locked=forbidden("overview acquired a mutation lock"),
            save=forbidden("overview saved repository state"),
            ensure_idle=forbidden("overview required an idle worktree"),
            ensure_clean=forbidden("overview required a clean worktree"),
            check_primary=forbidden("overview inspected trunk ownership"),
            owner=forbidden("overview inspected worktree ownership"),
            run=forbidden("overview invoked an external command"),
            git=forbidden("overview inspected more than the current branch"),
        )

    def write_catalog(self, saved):
        self.catalog.write_text(json.dumps({"schemaVersion": 1, "stacks": saved}))

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        before = self.catalog.read_bytes(), copy.deepcopy(self.repo.state)
        with (
            patch.object(cli, "Repo", return_value=self.repo),
            patch.object(
                stacks,
                "require_extension",
                side_effect=AssertionError("extension invoked"),
            ),
            patch.object(
                stacks, "catalog_lock", side_effect=AssertionError("catalog locked")
            ),
            patch.object(
                cli.github, "_api", side_effect=AssertionError("forge queried")
            ),
            patch(
                "rwgk_wt.push_guard.install",
                side_effect=AssertionError("hook installed"),
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            status = cli.main(list(arguments))
        self.assertEqual((self.catalog.read_bytes(), self.repo.state), before)
        return status, output.getvalue(), errors.getvalue()

    def test_view_reports_saved_order_identity_current_layer_and_links(self):
        before = self.catalog.read_bytes()
        value = stacks.view(self.repo)
        self.assertEqual(value["repository"], "Unit/project")
        self.assertEqual(value["number"], 77)
        self.assertEqual(value["trunk"], "main")
        self.assertEqual(value["current_branch"], self.lower)
        self.assertEqual(
            [row["branch"] for row in value["branches"]], [self.lower, self.upper]
        )
        self.assertEqual(
            [row["is_current"] for row in value["branches"]], [True, False]
        )
        self.assertEqual(
            value["branches"][1]["pr"]["url"], "https://github.com/Unit/project/pull/43"
        )
        self.assertEqual(
            value["branches"][0]["path"], self.repo.branches[self.lower]["path"]
        )
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_default_and_explicit_view_have_identical_human_and_json_results(self):
        status, output, errors = self.invoke("stack")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(self.invoke("stack", "view"), (status, output, errors))
        self.assertIn("77", output)
        self.assertIn("main", output)
        self.assertLess(output.index(self.lower), output.index(self.upper))
        self.assertIn(
            "*", next(line for line in output.splitlines() if self.lower in line)
        )
        self.assertIn("https://github.com/Unit/project/pull/42", output)
        self.assertIn("https://github.com/Unit/project/pull/43", output)
        status, output, errors = self.invoke("stack", "--json")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            self.invoke("stack", "view", "--json"), (status, output, errors)
        )
        self.assertEqual(json.loads(output)["current_branch"], self.lower)

    def test_unpublished_stack_keeps_local_identity_and_saved_merged_fact(self):
        self.stack.pop("number")
        self.stack["id"] = "local-example"
        self.stack["branches"][0].pop("pullRequest")
        self.stack["branches"][1]["pullRequest"]["merged"] = True
        self.write_catalog([self.stack])
        status, output, errors = self.invoke("stack", "view")
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("local-example", output)
        self.assertIn("unpublished", output.lower())
        value = stacks.view(self.repo)
        self.assertIsNone(value["number"])
        self.assertIsNone(value["branches"][0]["pr"])
        self.assertTrue(value["branches"][1]["pr"]["merged"])

    def test_missing_stack_and_multiple_membership_report_errors_without_changes(self):
        self.repo.current_branch.return_value = "rwgk/unrelated"
        status, output, errors = self.invoke("stack")
        self.assertEqual((status, output), (1, ""))
        self.assertIn("rwgk/unrelated", errors)
        self.repo.current_branch.return_value = self.lower
        self.write_catalog([self.stack, {**self.stack, "id": "other"}])
        status, output, errors = self.invoke("stack", "view")
        self.assertEqual((status, output), (1, ""))
        self.assertIn("multiple stacks", errors)

    def test_pending_operation_does_not_block_local_overview(self):
        self.repo.state["pr_rebase"] = {"branch": self.lower}
        self.repo.state["submodule_rebase"] = {"branch": self.lower}
        status, output, errors = self.invoke("stack")
        self.assertEqual((status, errors), (0, ""))
        self.assertIn(self.lower, output)

    def test_print_path_reports_no_navigation_path_without_mutation(self):
        for arguments in (("stack", "--print-path"), ("stack", "view", "--print-path")):
            with self.subTest(arguments=arguments):
                status, output, errors = self.invoke(*arguments)
                self.assertEqual((status, output), (1, ""))
                self.assertIn("navigation path", errors)

    def test_installed_entry_point_leaves_dirty_fixture_and_git_metadata_untouched(
        self,
    ):
        primary = self.area / "project"
        primary.mkdir()
        hooks = self.area / "hooks"
        hooks.mkdir()

        def git(*arguments):
            return subprocess.run(
                ["git", "-C", str(primary), *arguments],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

        git("init", "-b", "main")
        git("config", "user.name", "WT View Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "core.hooksPath", str(hooks))
        git("commit", "--allow-empty", "-m", "Initial")
        git("switch", "-c", self.lower)
        git("branch", self.upper)
        common = primary / ".git"
        (common / "gh-stack").write_bytes(self.catalog.read_bytes())
        (common / "rwgk-wt.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "config": {**self.repo.config, "primary": str(primary)},
                    "branches": self.repo.branches,
                    "pr_rebase": {"branch": self.lower},
                }
            )
        )
        notes = primary / "unfinished-notes"
        notes.write_text("keep this work\n")
        binaries = self.area / "bin"
        binaries.mkdir()
        marker = self.area / "forge-called"
        gh = binaries / "gh"
        gh.write_text('#!/bin/sh\nprintf called > "$WT_VIEW_FORGE_MARKER"\nexit 87\n')
        gh.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "WT_VIEW_FORGE_MARKER": str(marker),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        def snapshot():
            return {
                str(path.relative_to(common)): (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in common.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        for arguments in (("stack",), ("stack", "view", "--json")):
            result = subprocess.run(
                [sys.executable, str(ROOT / "bin/git-wt"), *arguments],
                cwd=primary,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(), before)
        self.assertEqual(notes.read_text(), "keep this work\n")
        self.assertFalse(marker.exists())
        self.assertEqual(list(hooks.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
