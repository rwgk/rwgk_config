"""Resource lookup resumes definite available sessions and reports ambiguity."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import cli, hooks, resources, store


class LogbookCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="logbook-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.logbook = self.root / "logbook"
        self.history = self.root / "codex"
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        environment = patch.dict(
            os.environ,
            {"CODEX_HOME": str(self.history), "CODEX_THREAD_ID": "thread-current"},
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = {
            "cwd": str(self.cwd),
            "root": "",
            "repo": "",
            "branch": "",
            "pr_url": "",
            "keys": [
                {
                    "key": "workspace:fixture",
                    "kind": "workspace",
                    "display": str(self.cwd),
                }
            ],
        }
        for target in (
            "agent_logbook.context.workspace",
            "agent_logbook.resources.workspace",
        ):
            mock = patch(target, return_value=self.workspace)
            mock.start()
            self.addCleanup(mock.stop)
        host = patch.object(store, "host_name", return_value="local.example")
        host.start()
        self.addCleanup(host.stop)

    def record(
        self,
        session_id="thread-one",
        *,
        available=True,
        host="local.example",
        note="Fix stream lifetime",
        resource="https://github.com/org/project/pull/123",
        relation="modified",
    ):
        transcript = self.history / "sessions" / "2026" / f"rollout-{session_id}.jsonl"
        if available:
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": session_id}})
                + "\n"
            )
        store.append_event(
            {
                "session_id": session_id,
                "host": host,
                "cwd": str(self.cwd),
                "transcript_path": str(transcript),
                "note": note,
                "links": [{**resources.normalize(resource), "relation": relation}],
            },
            directory=self.logbook,
        )
        return transcript

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli.main(args, directory=self.logbook)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_unique_exact_available_session_resumes_without_shell(self):
        self.record()
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 7
            status, stdout, stderr = self.invoke(
                ["find", "https://github.com/org/project/issues/123"]
            )
        self.assertEqual(status, 7)
        launch.assert_called_once_with(
            ["codex", "resume", "thread-one", "-C", str(self.cwd)], check=False
        )
        self.assertIn("Resuming thread-one", stdout)
        self.assertEqual(stderr, "")

    def test_multiple_matches_list_ids_hints_and_commands_without_launch(self):
        self.record()
        self.record("thread-two", note="Review stream changes")
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(
                ["find", "https://github.com/org/project/pull/123"]
            )
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("thread-one", stdout)
        self.assertIn("thread-two", stdout)
        self.assertIn("Review stream changes", stdout)
        self.assertIn("codex resume thread-two", stdout)

    def test_list_and_json_never_launch(self):
        self.record()
        with patch.object(cli.subprocess, "run") as launch:
            self.assertEqual(
                self.invoke(["find", "github:org/project#123", "--list"])[0], 0
            )
            status, stdout, stderr = self.invoke(
                ["find", "github:org/project#123", "--json"]
            )
        launch.assert_not_called()
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertTrue(result["matches"][0]["available_locally"])
        self.assertEqual(result["matches"][0]["resume_command"][0], "codex")

    def test_pr_and_bare_number_find_exact_recorded_request_and_resume(self):
        self.record()
        self.record(
            "unrelated",
            resource="https://github.com/org/project/pull/1234",
            note="Compare pr123",
        )
        for selector in ("pr123", "PR00123", "123"):
            with (
                self.subTest(selector=selector),
                patch.object(cli.subprocess, "run") as launch,
            ):
                launch.return_value.returncode = 0
                status, stdout, stderr = self.invoke(["find", selector])
                self.assertEqual(status, 0, stderr)
                self.assertIn("Resuming thread-one", stdout)
                launch.assert_called_once_with(
                    ["codex", "resume", "thread-one", "-C", str(self.cwd)], check=False
                )

    def test_mr_number_finds_gitlab_links_and_filters_github(self):
        self.record("github-thread")
        url = "https://gitlab.example/group/subgroup/project/-/merge_requests/123"
        self.record("gitlab-thread", resource=url, relation="reviewed")
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            status, stdout, stderr = self.invoke(["find", "mr123"])
            self.assertEqual(status, 0, stderr)
            self.assertIn("Resuming gitlab-thread", stdout)
            launch.assert_called_once_with(
                ["codex", "resume", "gitlab-thread", "-C", str(self.cwd)], check=False
            )
        status, stdout, stderr = self.invoke(["find", "123", "--json"])
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertTrue(result["ambiguous_request"])
        self.assertEqual(
            {row["provider"] for row in result["request_identities"]},
            {"github", "gitlab"},
        )

    def test_shorthand_ambiguity_between_repositories_never_resumes_one_spanning_session(
        self,
    ):
        self.record(relation="created")
        self.record(
            resource="https://github.com/other/project/pull/123", relation="reviewed"
        )
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "pr123"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("multiple requests", stdout)
        self.assertIn("https://github.com/org/project/issues/123", stdout)
        self.assertIn("https://github.com/other/project/issues/123", stdout)
        self.assertIn("Relations: Created, Reviewed", stdout)
        self.assertIn(
            "Request: https://github.com/org/project/issues/123 (Created)", stdout
        )
        self.assertIn(
            "Request: https://github.com/other/project/issues/123 (Reviewed)", stdout
        )
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            self.assertEqual(self.invoke(["find", "org/project#123"])[0], 0)
            launch.assert_called_once()

    def test_mr_subpage_links_share_one_request_identity_and_base_url_disambiguates(
        self,
    ):
        url = "https://gitlab.example/group/project/-/merge_requests/123"
        self.record(resource=url + "/diffs#note_4", relation="reviewed")
        status, stdout, stderr = self.invoke(["find", "mr123", "--json"])
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertFalse(result["ambiguous_request"])
        self.assertEqual(len(result["request_identities"]), 1)
        self.assertEqual(result["request_identities"][0]["display"], url)
        status, stdout, stderr = self.invoke(["find", url, "--list"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("  Relations: Reviewed", stdout)
        self.assertNotIn("possible", stdout)

    def test_request_shorthand_does_not_depend_on_surviving_worktree(self):
        self.record()
        self.cwd.rmdir()
        with patch.object(
            resources, "workspace", side_effect=AssertionError("No checkout lookup")
        ):
            status, stdout, stderr = self.invoke(
                ["find", "pr123", "--cwd", str(self.root), "--json"]
            )
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["matches"][0]["session_id"], "thread-one")
        self.assertEqual(result["matches"][0]["resume_command"][-1], str(self.root))

    def test_request_shorthand_only_uses_hints_as_possible_and_respects_number_boundary(
        self,
    ):
        self.record(
            resource="https://github.com/org/project/pull/1234", note="Review pr1234"
        )
        for selector in ("pr123", "123"):
            with self.subTest(selector=selector):
                self.assertEqual(self.invoke(["find", selector, "--json"])[0], 1)
        self.record(
            "hint-thread",
            resource="https://github.com/org/project/pull/999",
            note="Review pr123",
        )
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "pr123"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("hint-thread", stdout)
        self.assertIn("possible", stdout)
        self.assertIn("Relations: None", stdout)
        self.assertNotIn("Relations: Modified", stdout)
        self.assertEqual(stdout.splitlines()[-2], "-" * 100)

    def test_relations_only_describe_matched_resource_and_hide_workspace_when_semantic(
        self,
    ):
        self.record(relation="created")
        self.record(relation="workspace")
        self.record(
            resource="https://github.com/other/project/pull/999", relation="reviewed"
        )
        status, stdout, stderr = self.invoke(["find", "pr123", "--list"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("  Relations: Created\n", stdout)
        self.assertNotIn("Reviewed", stdout)
        self.assertNotIn("Context only", stdout)
        header = stdout.splitlines()[0]
        self.assertNotIn("created", header)
        self.assertNotIn("workspace", header)

    def test_workspace_only_relation_is_labeled_context_only(self):
        self.record(relation="workspace")
        status, stdout, stderr = self.invoke(["find", "pr123", "--list"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("Relations: Context only", stdout)

    def test_match_separators_are_exactly_100_hyphens_and_absent_from_json(self):
        self.record(relation="created")
        status, stdout, stderr = self.invoke(["find", "pr123", "--list"])
        self.assertEqual(status, 0, stderr)
        self.assertNotIn("-" * 100, stdout)
        self.record("thread-two", relation="reviewed")
        status, stdout, stderr = self.invoke(["find", "pr123", "--list"])
        self.assertEqual(status, 0, stderr)
        lines = stdout.splitlines()
        self.assertEqual(lines.count("-" * 100), 1)
        self.assertNotEqual(lines[0], "-" * 100)
        self.assertNotEqual(lines[-1], "-" * 100)
        status, stdout, stderr = self.invoke(["find", "pr123", "--json"])
        self.assertEqual(status, 0, stderr)
        self.assertNotIn("-" * 100, stdout)
        self.assertEqual(len(json.loads(stdout)["matches"]), 2)

    def test_remote_record_cannot_use_arbitrary_locally_existing_path(self):
        outside = self.root / "rollout-thread-one.jsonl"
        outside.write_text("{}\n")
        self.record(available=False, host="remote.example")
        store.append_event(
            {
                "session_id": "thread-one",
                "host": "remote.example",
                "transcript_path": str(outside),
            },
            directory=self.logbook,
        )
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "github:org/project#123"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("unavailable here", stdout)
        self.assertIn("remote.example", stdout)

    def test_shared_history_can_make_remote_session_available(self):
        self.record(host="remote.example")
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            self.assertEqual(self.invoke(["find", "github:org/project#123"])[0], 0)
        launch.assert_called_once()

    def test_missing_history_is_reported_without_launch(self):
        self.record(available=False)
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "github:org/project#123"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("unavailable here", stdout)

    def test_deleted_saved_worktree_suppresses_automatic_resume(self):
        self.record()
        self.cwd.rmdir()
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "github:org/project#123"])
            json_status, json_stdout, json_stderr = self.invoke(
                ["find", "github:org/project#123", "--json"]
            )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json_status, 0, json_stderr)
        launch.assert_not_called()
        self.assertIn("Saved history is available", stdout)
        self.assertIn("Recreate the worktree", stdout)
        self.assertIn("--cwd", stdout)
        result = json.loads(json_stdout)["matches"][0]
        self.assertTrue(result["available_locally"])
        self.assertIsNone(result["resume_command"])
        self.assertEqual(result["resume_blocked_reason"], "saved_cwd_missing")

    def test_explicit_find_cwd_resumes_into_chosen_recreated_worktree(self):
        self.record()
        self.cwd.rmdir()
        replacement = self.root / "replacement"
        replacement.mkdir()
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            status, stdout, stderr = self.invoke(
                ["find", "github:org/project#123", "--cwd", str(replacement)]
            )
        self.assertEqual(status, 0, stderr)
        self.assertIn("Resuming thread-one", stdout)
        launch.assert_called_once_with(
            ["codex", "resume", "thread-one", "-C", str(replacement)], check=False
        )

    def test_topic_only_match_is_labeled_possible_and_not_launched(self):
        self.record()
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "stream"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("possible", stdout)
        self.assertIn("Fix stream lifetime", stdout)

    def test_filename_without_extension_is_a_definite_resource_match(self):
        resource = self.root / "artifacts" / "Makefile"
        self.record(resource=str(resource))
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            status, stdout, stderr = self.invoke(["find", "Makefile"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("Resuming", stdout)
        launch.assert_called_once()

    def test_existing_local_filename_does_not_resume_another_project_by_basename(self):
        elsewhere = self.root / "another-project"
        elsewhere.mkdir()
        resource = elsewhere / "README.md"
        resource.write_text("Other project")
        self.record(resource=str(resource))
        (self.cwd / "README.md").write_text("Current project")
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(
                ["find", "README.md", "--cwd", str(self.cwd)]
            )
        self.assertEqual(status, 0, stderr)
        self.assertIn("possible", stdout)
        launch.assert_not_called()

    def test_no_matches_returns_nonzero_and_json_is_parseable(self):
        status, stdout, stderr = self.invoke(
            ["find", "github:org/project#123", "--json"]
        )
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout)["matches"], [])
        self.assertEqual(stderr, "")

    def test_corrupt_index_never_resumes_a_partial_unique_match(self):
        self.record()
        bad = self.logbook / "events" / "ff" / "broken.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{")
        with patch.object(cli.subprocess, "run") as launch:
            status, _stdout, stderr = self.invoke(["find", "github:org/project#123"])
        self.assertEqual(status, 1)
        launch.assert_not_called()
        self.assertIn("agent-logbook:", stderr)

    def test_here_prefers_canonical_pr_and_uses_receiving_worktree(self):
        self.record()
        other = self.root / "review-worktree"
        other.mkdir()
        self.workspace["pr_url"] = "https://github.com/org/project/pull/123"
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            self.assertEqual(self.invoke(["here", "--cwd", str(other)])[0], 0)
        launch.assert_called_once_with(
            ["codex", "resume", "thread-one", "-C", str(other)], check=False
        )

    def test_filename_basename_is_definite_and_multiple_sessions_are_listed(self):
        for session_id in ("thread-one", "thread-two"):
            self.record(session_id)
            store.append_event(
                {
                    "session_id": session_id,
                    "links": [
                        {
                            "key": f"file:github.com/org/project:{session_id}/report.txt",
                            "kind": "file",
                            "path": f"{session_id}/report.txt",
                            "display": str(self.cwd / session_id / "report.txt"),
                            "relation": "created",
                        }
                    ],
                },
                directory=self.logbook,
            )
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke(["find", "report.txt"])
        self.assertEqual(status, 0, stderr)
        launch.assert_not_called()
        self.assertIn("thread-one", stdout)
        self.assertIn("thread-two", stdout)
        self.assertNotIn("possible", stdout)

    def test_unique_filename_and_observed_absolute_path_resume(self):
        self.record()
        artifact = str(self.cwd / "nested" / "report.txt")
        store.append_event(
            {
                "session_id": "thread-one",
                "links": [
                    {
                        "key": "file:github.com/org/project:nested/report.txt",
                        "kind": "file",
                        "path": "nested/report.txt",
                        "display": artifact,
                        "relation": "created",
                    }
                ],
            },
            directory=self.logbook,
        )
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            self.assertEqual(self.invoke(["find", "report.txt"])[0], 0)
            self.assertEqual(self.invoke(["find", artifact])[0], 0)
        self.assertEqual(launch.call_count, 2)

    def test_session_id_prefix_cannot_resume_another_session(self):
        path = self.record()
        self.assertIsNone(cli._find_transcript("one", path))
        path.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "another-thread"}})
            + "\n"
        )
        self.assertIsNone(cli._find_transcript("thread-one", path))

    def test_record_defaults_to_current_session_and_keeps_resource_relation(self):
        status, stdout, stderr = self.invoke(
            [
                "record",
                "https://github.com/org/project/pull/123",
                "--relation",
                "reviewed",
                "--note",
                "Reviewed lifetime fix",
                "--cwd",
                str(self.cwd),
                "--json",
            ]
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["session_id"], "thread-current")
        saved = store.load_sessions(directory=self.logbook)[0]
        self.assertEqual(saved["note"], "Reviewed lifetime fix")
        self.assertEqual(
            {link["relation"] for link in saved["links"]}, {"workspace", "reviewed"}
        )

    def test_record_requires_session_outside_codex(self):
        with patch.dict(os.environ, {}, clear=True):
            status, _stdout, stderr = self.invoke(["record", "--note", "hint"])
        self.assertEqual(status, 1)
        self.assertIn("--session", stderr)
        self.assertFalse(self.logbook.exists())

    def test_hook_failure_is_advisory_and_stop_output_valid_json(self):
        event = {"hook_event_name": "Stop", "session_id": "thread-one"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(event))),
            patch.object(hooks, "handle", side_effect=OSError("unavailable")),
        ):
            status, stdout, stderr = self.invoke(["hook"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout), {})
        self.assertIn("could not record", stderr)


if __name__ == "__main__":
    unittest.main()
