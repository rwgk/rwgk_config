"""Cleanup feedback reports journaled outcomes without corrupting machine output."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli


class RequestCommandTests(unittest.TestCase):
    def test_wrong_provider_command_stops_before_lock_or_api(self):
        for command, provider, identity, expected in (
            ("pr", "gitlab", "gitlab.example/group/project", "mr"),
            ("mr", "github", "owner/project", "pr"),
        ):
            with self.subTest(command=command):
                repo = SimpleNamespace(
                    config={"provider": provider, "upstream_repo": identity},
                    locked=Mock(),
                )
                output, errors = io.StringIO(), io.StringIO()
                with (
                    patch.object(cli, "Repo", return_value=repo),
                    patch.object(cli.github, "open_pr") as open_pr,
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(errors),
                ):
                    result = cli.main([command, "42"])
                self.assertEqual(result, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn(f"wt {expected} 42", errors.getvalue())
                open_pr.assert_not_called()
                repo.locked.assert_not_called()


class CleanupOutputTests(unittest.TestCase):
    def setUp(self):
        self.repo = SimpleNamespace(
            config={
                "upstream_remote": "upstream",
                "archive_remote": "origin",
                "backtracking": "/records",
            },
            primary=Path("/primary"),
            locked=contextlib.nullcontext,
        )
        self.record = {
            "id": "project-cleanup",
            "kind": "retire",
            "status": "complete",
            "branch": "rwgk/review-pr3189",
            "path": "/trees/rwgk-review-pr3189",
            "backtracking_path": "/actual-records/project-cleanup.json",
            "completed": ["recovery_refs", "worktree", "local_branch", "metadata"],
            "snapshots": [{"recovery_ref": "refs/rwgk/backtracking/project/0"}],
            "archives": [],
            "remote_actions": [],
        }

    def applied(self, record=None):
        return {
            "result": "retired",
            "operations": [self.record if record is None else record],
            "skipped": [],
        }

    def invoke(self, flags=(), value=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        result = self.applied() if value is None else value
        with (
            patch.object(cli, "Repo", return_value=self.repo),
            patch.object(cli, "_dispatch", return_value=(result, self.repo.primary)),
            patch("rwgk_wt.push_guard.install"),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(["clean", "--apply", *flags])
        self.assertEqual(status, 0, stderr.getvalue())
        return stdout.getvalue(), stderr.getvalue()

    def test_review_cleanup_reports_saved_path_and_no_remote_changes(self):
        stdout, stderr = self.invoke()
        self.assertEqual(stderr, "")
        self.assertEqual(
            stdout.splitlines(),
            [
                "Removed local branch rwgk/review-pr3189 and its worktree.",
                "Backtracking: /actual-records/project-cleanup.json (1 recovery ref)",
                "upstream: no changes",
                "origin: no changes",
            ],
        )

    def test_path_mode_keeps_stdout_as_one_navigation_path(self):
        expected, _ = self.invoke()
        stdout, stderr = self.invoke(["--print-path"])
        self.assertEqual(stdout, "/primary\n")
        self.assertEqual(stderr, expected)

    def test_unpaired_cleanup_does_not_claim_a_worktree_was_removed(self):
        self.record["path"] = None
        stdout, _ = self.invoke()
        self.assertEqual(
            stdout.splitlines()[0],
            "Removed local branch rwgk/review-pr3189 (no worktree).",
        )

    def test_json_has_no_human_feedback_and_preserves_result_shape(self):
        stdout, stderr = self.invoke(["--json"])
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), self.applied())

    def test_resumed_bare_record_has_same_human_feedback_and_json_shape(self):
        expected, _ = self.invoke()
        stdout, stderr = self.invoke(["--resume", self.record["id"]], self.record)
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")
        stdout, stderr = self.invoke(
            ["--resume", self.record["id"], "--json"], self.record
        )
        self.assertEqual(json.loads(stdout), self.record)
        self.assertEqual(stderr, "")

    def test_reports_exact_deleted_branch_and_created_archive(self):
        self.record["remote_actions"] = [
            {
                "remote": "upstream",
                "repo": "NVIDIA/cuda-python",
                "ref": "refs/heads/rwgk/change",
                "oid": "a" * 40,
                "action": "branch-deleted",
            },
            {
                "remote": "origin",
                "repo": "rwgk/cuda-python",
                "ref": "refs/tags/rwgk/archive/project/pr1234/" + "a" * 40,
                "oid": "a" * 40,
                "action": "archive-created",
            },
        ]
        stdout, _ = self.invoke()
        self.assertIn(
            "upstream: deleted refs/heads/rwgk/change (NVIDIA/cuda-python)", stdout
        )
        self.assertIn(
            "origin: created archive tag refs/tags/rwgk/archive/project/pr1234/"
            + "a" * 40
            + " (rwgk/cuda-python)",
            stdout,
        )

    def test_reused_archive_does_not_claim_a_remote_mutation(self):
        event = {
            "remote": "origin",
            "repo": "rwgk/cuda-python",
            "ref": "refs/tags/rwgk/archive/existing",
            "oid": "a" * 40,
            "action": "archive-reused",
        }
        self.record["remote_actions"] = [event]
        self.record["archives"] = [
            {key: value for key, value in event.items() if key != "action"}
        ]
        stdout, _ = self.invoke()
        self.assertIn(
            "origin: reused archive tag refs/tags/rwgk/archive/existing "
            "(rwgk/cuda-python)",
            stdout,
        )
        self.assertNotIn("created", stdout)
        self.assertNotIn("creation not recorded", stdout)

    def test_resume_deduplicates_created_then_reused_archive(self):
        created = {
            "remote": "origin",
            "repo": "rwgk/cuda-python",
            "ref": "refs/tags/rwgk/archive/existing",
            "oid": "a" * 40,
            "action": "archive-created",
        }
        self.record["remote_actions"] = [
            created,
            {**created, "action": "archive-reused"},
        ]
        stdout, _ = self.invoke()
        self.assertEqual(stdout.count("refs/tags/rwgk/archive/existing"), 1)
        self.assertIn("origin: created archive tag", stdout)

    def test_branch_already_absent_is_not_reported_as_deleted(self):
        self.record["remote_actions"] = [
            {
                "remote": "upstream",
                "repo": "NVIDIA/cuda-python",
                "ref": "refs/heads/rwgk/change",
                "oid": "a" * 40,
                "action": "branch-absent",
            }
        ]
        stdout, _ = self.invoke()
        self.assertIn(
            "upstream: source branch already absent: refs/heads/rwgk/change "
            "(NVIDIA/cuda-python)",
            stdout,
        )
        self.assertNotIn("deleted", stdout)

    def test_old_record_reports_final_state_without_inventing_pushes(self):
        self.record.pop("remote_actions")
        self.record["completed"].append("remote_branch")
        self.record["source"] = {
            "remote": "upstream",
            "repo": "NVIDIA/cuda-python",
            "ref": "refs/heads/rwgk/change",
            "oid": "a" * 40,
        }
        self.record["archives"] = [
            {
                "remote": "origin",
                "repo": "rwgk/cuda-python",
                "ref": "refs/tags/rwgk/archive/existing",
                "oid": "a" * 40,
            }
        ]
        stdout, _ = self.invoke(["--resume", self.record["id"]], self.record)
        self.assertIn("source branch absent:", stdout)
        self.assertIn("prior deletion not recorded", stdout)
        self.assertIn("archive verified:", stdout)
        self.assertIn("creation not recorded", stdout)
        self.assertNotIn(": deleted ", stdout)
        self.assertNotIn("created archive tag", stdout)

    def test_old_record_path_fallback_honors_environment_override(self):
        self.record.pop("backtracking_path")
        with patch.dict(
            os.environ, {"MY_GIT_BACKTRACKING_INFO_LOCAL": "/overridden-records"}
        ):
            stdout, _ = self.invoke()
        self.assertIn("Backtracking: /overridden-records/project-cleanup.json", stdout)
        self.assertNotIn("Backtracking: /records/", stdout)

    def test_same_remote_for_upstream_and_archive_is_reported_once(self):
        self.repo.config["upstream_remote"] = "origin"
        stdout, _ = self.invoke()
        self.assertEqual(stdout.splitlines()[-1], "origin: no changes")
        self.assertEqual(len(stdout.splitlines()), 3)

    def test_empty_cleanup_does_not_claim_a_backtracking_record(self):
        result = {"result": "retired", "operations": [], "skipped": []}
        stdout, stderr = self.invoke(value=result)
        self.assertEqual(stdout, "No branch/worktree pairs removed.\n")
        self.assertEqual(stderr, "")

    def test_cleanup_preview_keeps_planned_changes_and_apply_hint(self):
        args = cli.parser().parse_args(["clean", "pr3189"])
        value = copy.deepcopy(self.applied())
        value["result"] = "preview"
        value["operations"][0]["source"] = {
            "repo": "NVIDIA/cuda-python",
            "ref": "refs/heads/rwgk/change",
            "oid": "a" * 40,
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli._display(args, value, self.repo)
        text = stdout.getvalue()
        self.assertIn("preview: 1 pair(s)", text)
        self.assertIn(
            "remove live ref: NVIDIA/cuda-python refs/heads/rwgk/change", text
        )
        self.assertIn("Use --apply to execute after reviewing the plan.", text)
        self.assertNotIn("Removed local branch", text)


class HousekeepingOutputTests(unittest.TestCase):
    def setUp(self):
        self.repo = SimpleNamespace(
            config={
                "upstream_remote": "upstream",
                "archive_remote": "origin",
                "backtracking": "/records",
            },
            primary=Path("/primary"),
            locked=contextlib.nullcontext,
        )
        self.record = {
            "id": "housekeeping-clean",
            "branch": "rwgk/finished",
            "path": "/trees/rwgk-finished",
            "completed": [],
            "backtracking_path": "/records/housekeeping-clean.json",
            "snapshots": [{"recovery_ref": "refs/rwgk/backtracking/kept"}],
            "pr": {
                "number": 42,
                "state": "MERGED",
                "url": "https://github.com/Acme/project/pull/42",
            },
            "archives": [],
            "remote_actions": [],
        }
        self.value = {
            "result": "preview",
            "operations": [self.record],
            "errors": [],
            "remaining": [
                {
                    "branch": "rwgk/still-working",
                    "path": "/trees/rwgk-still-working",
                    "pr": {
                        "number": 43,
                        "state": "MERGED",
                        "url": "https://github.com/Acme/project/pull/43",
                    },
                    "reasons": [
                        {
                            "code": "local-history",
                            "message": "2 commits outside the final PR history",
                        }
                    ],
                    "hints": ["wt go pr43"],
                }
            ],
        }

    def invoke(self, flags=()):
        value = copy.deepcopy(self.value)
        if "--apply" in flags:
            value["result"] = "housekeeping"
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(cli, "Repo", return_value=self.repo),
            patch.object(cli, "_dispatch", return_value=(value, self.repo.primary)),
            patch("rwgk_wt.push_guard.install"),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(["housekeeping", *flags])
        return status, stdout.getvalue(), stderr.getvalue(), value

    def test_preview_distinguishes_candidates_from_retained_work_and_shows_hints(self):
        status, stdout, stderr, _ = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Would clean 1 pair(s)", stdout)
        self.assertIn("pr42 MERGED rwgk/finished", stdout)
        self.assertIn("/trees/rwgk-still-working", stdout)
        self.assertIn("2 commits outside the final PR history", stdout)
        self.assertIn("Next: wt go pr43", stdout)
        self.assertIn("https://github.com/Acme/project/pull/43", stdout)
        self.assertNotIn("Removed local branch", stdout)
        self.assertIn("Use --apply", stdout)

    def test_applied_feedback_includes_recovery_remote_outcomes_and_retained_work(self):
        status, stdout, stderr, _ = self.invoke(["--apply"])
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Removed local branch rwgk/finished", stdout)
        self.assertIn("Backtracking: /records/housekeeping-clean.json", stdout)
        self.assertIn("upstream: no changes", stdout)
        self.assertIn("origin: no changes", stdout)
        self.assertIn("2 commits outside the final PR history", stdout)
        self.assertIn("Cleaned: 1; retained: 1; errors: 0", stdout)

    def test_retained_pr_state_is_verified_or_explicitly_unchecked(self):
        entry = self.value["remaining"][0]
        entry["pr_verified"] = True
        _, stdout, _, _ = self.invoke()
        self.assertIn("pr43 MERGED rwgk/still-working", stdout)
        entry["pr_verified"] = False
        _, stdout, _, _ = self.invoke()
        self.assertIn("pr43 MERGED (unchecked) rwgk/still-working", stdout)

    def test_open_pr_commit_counts_are_visible_with_the_navigation_hint(self):
        entry = self.value["remaining"][0]
        entry["pr"].update(state="OPEN")
        entry.update(pr_verified=True, facts={"ahead": 2, "behind": 1})
        entry["reasons"] = [{"code": "pr-open", "message": "PR #43 is open"}]
        _, stdout, _, _ = self.invoke()
        self.assertIn("pr43 OPEN rwgk/still-working", stdout)
        self.assertIn("2 local-only, 1 PR-only", stdout)
        self.assertIn("Next: wt go pr43", stdout)

    def test_json_has_no_human_output_and_retains_diagnostics(self):
        status, stdout, stderr, value = self.invoke(["--json"])
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), value)

    def test_apply_path_mode_keeps_stdout_reserved_for_shell_navigation(self):
        status, stdout, stderr, _ = self.invoke(["--apply", "--print-path"])
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "/primary\n")
        self.assertIn("Backtracking:", stderr)
        self.assertIn("retained: 1", stderr)

    def test_failure_after_other_successes_remains_visible_and_returns_nonzero(self):
        entry = self.value["remaining"][0]
        entry["error"] = True
        entry["reasons"] = [
            {"code": "cleanup-failed", "message": "Archive verification failed"}
        ]
        entry["recovery_path"] = "/records/interrupted-clean.json"
        self.value["errors"] = [entry]
        status, stdout, stderr, _ = self.invoke(["--apply"])
        self.assertEqual((status, stderr), (1, ""))
        self.assertIn("Archive verification failed", stdout)
        self.assertIn("Recovery: /records/interrupted-clean.json", stdout)
        self.assertIn("errors: 1", stdout)

    def test_global_problem_is_reported_even_without_any_workspaces(self):
        self.value.update(
            operations=[],
            remaining=[],
            issues=[
                {
                    "code": "pending-operation",
                    "message": "A pull rebase is pending",
                    "hint": "wt pull --continue",
                }
            ],
        )
        status, stdout, stderr, _ = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("A pull rebase is pending", stdout)
        self.assertIn("Next: wt pull --continue", stdout)


if __name__ == "__main__":
    unittest.main()
