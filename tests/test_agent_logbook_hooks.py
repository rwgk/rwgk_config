"""Capture only confirmed resource activity, without copying message bodies."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import hooks


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="logbook-hooks-")
        self.addCleanup(self.temporary.cleanup)
        self.cwd = self.temporary.name
        self.directory = str(Path(self.cwd) / "index")
        self.append = patch.object(hooks, "append_event").start()
        self.addCleanup(patch.stopall)
        patch.object(hooks, "workspace", return_value={"keys": []}).start()
        patch.object(
            hooks,
            "normalize",
            side_effect=lambda value, cwd=None: {
                "key": value,
                "kind": "test",
                "display": value,
            },
        ).start()

    def event(self, name, args, response, **overrides):
        return {
            "hook_event_name": "PostToolUse",
            "session_id": "root-session",
            "cwd": self.cwd,
            "tool_name": name,
            "tool_input": args,
            "tool_response": response,
            "tool_use_id": "tool-123",
            **overrides,
        }

    def captured(self):
        self.append.assert_called_once()
        record = self.append.call_args.args[0]
        self.assertEqual(self.append.call_args.kwargs, {"directory": self.directory})
        return record

    def handle(self, event):
        hooks.handle(event, directory=self.directory)

    def test_apply_patch_registers_new_modified_deleted_and_both_move_paths(self):
        command = """*** Begin Patch
*** Add File: new.py
+new body secret
*** Update File: old.py
*** Move to: moved.py
@@
-old
+new
*** Delete File: removed.py
*** End Patch"""
        self.handle(
            self.event(
                "apply_patch",
                {"command": command},
                {"output": "Success", "exit_code": 0},
            )
        )
        record = self.captured()
        self.assertEqual(
            [(item["key"], item["relation"]) for item in record["links"]],
            [
                ("new.py", "created"),
                ("old.py", "modified"),
                ("moved.py", "modified"),
                ("removed.py", "deleted"),
            ],
        )
        self.assertNotIn("secret", json.dumps(record))
        self.assertEqual(record["links"][0]["evidence"], "apply_patch:tool-123")

    def test_tool_explicit_workdir_overrides_session_start_directory(self):
        self.handle(
            self.event(
                "apply_patch",
                {"command": "*** Add File: changed.py", "workdir": "other-worktree"},
                {"output": "Success"},
            )
        )
        self.assertEqual(
            self.captured()["cwd"], str((Path(self.cwd) / "other-worktree").resolve())
        )

    def test_failed_patch_and_unknown_tool_do_not_create_evidence(self):
        for event in [
            self.event(
                "apply_patch",
                {"command": "*** Add File: nope.py"},
                {"exit_code": 1, "output": "failed"},
            ),
            self.event(
                "read_file", {"file_path": "looked-at.py"}, {"output": "file contents"}
            ),
        ]:
            self.handle(event)
        self.append.assert_not_called()

    def test_index_itself_is_excluded(self):
        self.handle(
            self.event(
                "apply_patch",
                {"command": f"*** Add File: {self.directory}/event.json"},
                {"output": "Success"},
            )
        )
        self.append.assert_not_called()

    def test_read_response_url_is_not_write_provenance(self):
        self.handle(
            self.event(
                "mcp__github__get_pull_request",
                {"owner": "org", "repo": "repo", "pull_number": 123},
                {"url": "https://github.com/org/repo/pull/123"},
            )
        )
        self.append.assert_not_called()

    def test_github_create_reads_structured_mcp_result_and_skips_body_urls(self):
        result = {
            "isError": False,
            "structuredContent": {
                "pull_request": {
                    "url": "https://github.com/org/repo/pull/123",
                    "body": "secret https://github.com/org/other/issues/999",
                }
            },
            "content": [{"type": "text", "text": "Action completed."}],
        }
        self.handle(
            self.event(
                "mcp__codex_apps__github_create_pull_request",
                {"body": "secret"},
                result,
            )
        )
        record = self.captured()
        self.assertEqual(
            [link["key"] for link in record["links"]],
            ["https://github.com/org/repo/pull/123"],
        )
        self.assertEqual(record["links"][0]["relation"], "created")
        self.assertNotIn("secret", json.dumps(record))

    def test_github_comment_input_identity_when_result_has_no_permalink(self):
        self.handle(
            self.event(
                "mcp__codex_apps__github_add_comment_to_issue",
                {
                    "repo_full_name": "org/repo",
                    "pr_number": 123,
                    "comment": "private comment",
                },
                {"content": [{"type": "text", "text": "Action completed."}]},
            )
        )
        record = self.captured()
        self.assertEqual(
            record["links"][0]["key"], "https://github.com/org/repo/pull/123"
        )
        self.assertEqual(record["links"][0]["relation"], "commented")
        self.assertNotIn("private", json.dumps(record))

    def test_nested_mcp_failure_is_rejected(self):
        result = {
            "isError": False,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": "denied",
                            "url": "https://github.com/org/repo/issues/3",
                        }
                    ),
                }
            ],
        }
        self.handle(self.event("mcp__github__create_issue", {}, result))
        self.append.assert_not_called()

    def test_nvbug_confirmation_is_not_a_write(self):
        self.handle(
            self.event(
                "mcp__MaaS_NVBugs__nvbugs_update_bug_v2",
                {"bug_id": "123", "comment": "private"},
                {
                    "structuredContent": {
                        "result": {
                            "success": True,
                            "requires_confirmation": True,
                            "confirmation_id": "challenge",
                        }
                    }
                },
            )
        )
        self.append.assert_not_called()

    def test_nvbug_success_uses_returned_new_template_id(self):
        self.handle(
            self.event(
                "mcp__MaaS_NVBugs__nvbugs_update_bug_v2",
                {"bug_id": "123", "comment": "private", "confirm_update": True},
                {
                    "structuredContent": {
                        "result": {
                            "success": True,
                            "data": {"bug_id": 456, "source_template_bug_id": 123},
                        }
                    }
                },
            )
        )
        link = self.captured()["links"][0]
        self.assertEqual(link["key"], "nvbug 456")
        self.assertEqual(link["relation"], "created")

    def test_slack_post_records_only_returned_permalink(self):
        self.handle(
            self.event(
                "mcp__slack_local_bridge__slack_post_message",
                {"channel": "C123", "text": "secret"},
                {
                    "structuredContent": {
                        "ok": True,
                        "permalink": "https://example.slack.com/archives/C123/p12345000000",
                    }
                },
            )
        )
        record = self.captured()
        self.assertEqual(
            record["links"][0]["key"],
            "https://example.slack.com/archives/C123/p12345000000",
        )
        self.assertNotIn("secret", json.dumps(record))

    def test_gh_creation_needs_completed_zero_exit_and_output_url(self):
        self.handle(
            self.event(
                "exec_command",
                {"cmd": "gh pr create --body 'private'"},
                {"exit_code": 0, "output": "https://github.com/org/repo/pull/123\n"},
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "created")

    def test_async_or_failed_gh_invocation_does_not_claim_write(self):
        for result in [
            {"session_id": 123, "output": "still running"},
            {"exit_code": 1, "output": "https://github.com/org/repo/pull/123"},
        ]:
            self.handle(self.event("exec_command", {"cmd": "gh pr create"}, result))
        self.append.assert_not_called()

    def test_shell_chains_are_not_interpreted_as_single_successful_gh_write(self):
        self.handle(
            self.event(
                "Bash",
                {"command": "gh pr create; echo https://github.com/org/repo/pull/123"},
                {"exit_code": 0, "output": "https://github.com/org/repo/pull/123"},
            )
        )
        self.append.assert_not_called()

    def test_lifecycle_records_workspace_and_short_hint_under_hook_root_id(self):
        hooks.workspace.return_value = {
            "keys": [
                {"key": "workspace-path", "kind": "workspace", "display": self.cwd}
            ],
            "pr_url": "https://github.com/org/repo/pull/123",
        }
        prompt = "Investigate stream lifetimes. " + "context " * 50
        self.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "root-session",
                "cwd": self.cwd,
                "prompt": prompt,
                "model": "actual-model",
                "transcript_path": "/local/rollout.jsonl",
            }
        )
        record = self.captured()
        self.assertEqual(record["session_id"], "root-session")
        self.assertLessEqual(len(record["note"]), 160)
        self.assertNotEqual(record["note"], prompt)
        self.assertEqual({item["relation"] for item in record["links"]}, {"workspace"})
        self.assertEqual(record["transcript_path"], "/local/rollout.jsonl")
        self.assertEqual(record["model"], "actual-model")

    def test_github_method_based_issue_and_review_writes(self):
        self.handle(
            self.event(
                "mcp__github__issue_write",
                {
                    "method": "update",
                    "owner": "org",
                    "repo": "repo",
                    "issue_number": 12,
                },
                {"ok": True},
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "modified")
        self.append.reset_mock()
        self.handle(
            self.event(
                "mcp__github__pull_request_review_write",
                {
                    "method": "submit_pending",
                    "owner": "org",
                    "repo": "repo",
                    "pullNumber": 12,
                },
                {"ok": True},
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "reviewed")

    def test_nvbug_confirmation_summary_with_success_flag_still_is_not_write(self):
        self.handle(
            self.event(
                "mcp__MaaS_NVBugs__nvbugs_update_bug_v2",
                {"bug_id": "123"},
                {"success": True, "confirmation_summary": "private"},
            )
        )
        self.append.assert_not_called()

    def test_github_api_resource_urls_are_converted_to_human_lookup_identity(self):
        self.handle(
            self.event(
                "mcp__github__create_pull_request",
                {},
                {"url": "https://api.github.com/repos/org/repo/pulls/123"},
            )
        )
        self.assertEqual(
            self.captured()["links"][0]["key"], "https://github.com/org/repo/pull/123"
        )

    def test_empty_dict_patch_result_is_success(self):
        self.handle(self.event("apply_patch", "*** Add File: changed.py", {}))
        self.assertEqual(self.captured()["links"][0]["key"], "changed.py")

    def test_absent_or_textual_failed_patch_results_are_not_success(self):
        for result in [
            None,
            "Failed to apply patch",
            "Error: Invalid patch",
            {"output": "Failed to find expected lines"},
            {"content": [{"type": "text", "text": "Error parsing patch"}]},
        ]:
            self.handle(
                self.event("apply_patch", {"command": "*** Add File: nope.py"}, result)
            )
        self.append.assert_not_called()

    def test_successful_text_patch_result_is_supported(self):
        self.handle(
            self.event(
                "apply_patch",
                {"command": "*** Add File: changed.py"},
                "Success. Updated the following files:\nA changed.py",
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "created")

    def test_live_cli_wrapped_patch_success_is_captured(self):
        self.handle(
            self.event(
                "apply_patch",
                {"command": "*** Update File: changed.py"},
                "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nSuccess. Updated the following files:\nM changed.py\n",
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "modified")

    def test_wrapped_patch_failures_and_empty_output_are_rejected(self):
        for code, output in [
            (1, "Success"),
            (-1, "Success"),
            (0, "Failed to apply patch"),
            (0, ""),
            (0, '{"isError":true}'),
        ]:
            with self.subTest(code=code, output=output):
                self.handle(
                    self.event(
                        "apply_patch",
                        {"command": "*** Add File: nope.py"},
                        f"Exit code: {code}\nWall time: 0.1 seconds\nOutput:\n{output}",
                    )
                )
        self.append.assert_not_called()

    def test_partial_or_incidental_command_headers_are_not_success(self):
        for response in [
            "Exit code: 0\nOutput:\nSuccess",
            "Log output:\nExit code: 0\nWall time: 0.1 seconds\nOutput:\nSuccess",
        ]:
            self.handle(
                self.event(
                    "apply_patch", {"command": "*** Add File: nope.py"}, response
                )
            )
        self.append.assert_not_called()

    def test_live_cli_wrapped_gh_success_is_captured(self):
        self.handle(
            self.event(
                "Bash",
                {"command": "gh pr create --title fixture --body fixture"},
                "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nhttps://github.com/org/repo/pull/123\n",
            )
        )
        self.assertEqual(self.captured()["links"][0]["relation"], "created")

    def test_wrapped_gh_failure_and_reads_are_not_write_provenance(self):
        for command, code in [("gh pr create", 1), ("gh pr view 123", 0)]:
            self.handle(
                self.event(
                    "Bash",
                    {"command": command},
                    f"Exit code: {code}\nWall time: 0.1 seconds\nOutput:\nhttps://github.com/org/repo/pull/123\n",
                )
            )
        self.append.assert_not_called()

    def test_http_failures_and_approval_states_do_not_claim_write_provenance(self):
        failures = [
            {"status": 422, "message": "Validation Failed"},
            {"status_code": 404, "message": "Not Found"},
            {"status": "500"},
            {
                "status": "awaiting_approval",
                "url": "https://github.com/org/repo/issues/1",
            },
            {"status": "pending_approval"},
            {"requires_approval": True},
        ]
        for result in failures:
            with self.subTest(result=result):
                self.handle(
                    self.event(
                        "mcp__github__update_issue",
                        {"owner": "org", "repo": "repo", "issue_number": 1},
                        result,
                    )
                )
        self.append.assert_not_called()

    def test_gh_dry_run_and_help_do_not_claim_creation_from_supplied_body_url(self):
        for flag in ["--dry-run", "--dry-run=true", "--help", "-h"]:
            self.handle(
                self.event(
                    "exec_command",
                    {
                        "cmd": f"gh pr create {flag} --body 'Fixes https://github.com/org/repo/issues/1'"
                    },
                    {
                        "exit_code": 0,
                        "output": "https://github.com/org/repo/issues/1\nhttps://github.com/org/repo/pull/123",
                    },
                )
            )
        self.append.assert_not_called()

    def test_gh_creation_uses_standalone_url_for_the_created_resource_kind(self):
        self.handle(
            self.event(
                "exec_command",
                {"cmd": "gh pr create --title Test"},
                {
                    "exit_code": 0,
                    "output": "Warning: related https://github.com/org/repo/pull/456\nhttps://github.com/org/repo/issues/1\nhttps://github.com/org/repo/pull/123\n",
                },
            )
        )
        self.assertEqual(
            [item["key"] for item in self.captured()["links"]],
            ["https://github.com/org/repo/pull/123"],
        )

    def test_slack_reply_records_reply_and_root_thread_alias(self):
        self.handle(
            self.event(
                "mcp__slack_local_bridge__slack_post_message",
                {
                    "channel": "C123",
                    "thread_ts": "1234567890.000001",
                    "text": "private",
                },
                {
                    "ok": True,
                    "permalink": "https://example.slack.com/archives/C123/p1234567890000002",
                },
            )
        )
        links = self.captured()["links"]
        self.assertEqual(
            {item["key"] for item in links},
            {
                "https://example.slack.com/archives/C123/p1234567890000002",
                "https://example.slack.com/archives/C123/p1234567890000002?thread_ts=1234567890.000001",
            },
        )
        # Verify the real canonicalization agrees with a copied Slack reply URL.
        from agent_logbook.resources import normalize

        root = normalize(
            "https://example.slack.com/archives/C123/p1234567890000002?thread_ts=1234567890.000001"
        )
        self.assertIn(root["key"], {normalize(item["key"])["key"] for item in links})

    def transcript(self, session_id, **metadata):
        path = (
            Path(self.cwd)
            / "history"
            / "sessions"
            / "2026"
            / "10"
            / "28"
            / f"rollout-{session_id}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"type": "session_meta", "payload": {"id": session_id, **metadata}}
            )
            + "\n"
        )
        return path.resolve()

    def test_subagent_metadata_flattens_to_root_without_reading_inherited_history(self):
        root = self.transcript("main-session")
        child = self.transcript(
            "child-session",
            source={"subagent": {"thread_spawn": {"parent_thread_id": "main-session"}}},
        )
        with child.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "unrelated-inherited-id",
                            "parent_thread_id": "wrong",
                        },
                    }
                )
                + "\n"
            )
        self.handle(
            {
                "hook_event_name": "SessionStart",
                "session_id": "child-session",
                "cwd": self.cwd,
                "transcript_path": str(child),
            }
        )
        record = self.captured()
        self.assertEqual(record["session_id"], "main-session")
        self.assertEqual(record["contributor_session_id"], "child-session")
        self.assertEqual(record["transcript_path"], str(root))

    def test_nested_subagent_metadata_resolves_parent_thread_chain(self):
        root = self.transcript("main-session")
        self.transcript("middle-session", parent_thread_id="main-session")
        child = self.transcript("child-session", parent_thread_id="middle-session")
        self.handle(
            {
                "hook_event_name": "SessionStart",
                "session_id": "child-session",
                "cwd": self.cwd,
                "transcript_path": str(child),
            }
        )
        record = self.captured()
        self.assertEqual(record["session_id"], "main-session")
        self.assertEqual(record["transcript_path"], str(root))

    def test_missing_parent_transcript_never_labels_child_history_as_root_history(self):
        child = self.transcript("child-session", parent_thread_id="missing-parent")
        self.handle(
            {
                "hook_event_name": "SessionStart",
                "session_id": "child-session",
                "cwd": self.cwd,
                "transcript_path": str(child),
            }
        )
        record = self.captured()
        self.assertEqual(record["session_id"], "missing-parent")
        self.assertNotIn("transcript_path", record)

    def test_direct_root_and_mismatched_first_metadata_are_not_rewritten(self):
        root = self.transcript("main-session", forked_from_id="old-main")
        event = {"session_id": "main-session", "transcript_path": str(root)}
        self.assertEqual(hooks._main_session(event), event)
        wrong = self.transcript("other-session", parent_thread_id="wrong-parent")
        event = {"session_id": "child-session", "transcript_path": str(wrong)}
        self.assertEqual(hooks._main_session(event), event)

    def test_bad_or_unknown_events_are_ignored(self):
        for event in [
            None,
            {},
            {"session_id": "id", "hook_event_name": "Unknown", "cwd": self.cwd},
        ]:
            self.handle(event)
        self.append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
