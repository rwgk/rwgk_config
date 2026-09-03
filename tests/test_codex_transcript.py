#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "bin" / "codex-transcript.py"
MODULE_SPEC = importlib.util.spec_from_file_location("codex_transcript", SCRIPT_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"could not load {SCRIPT_PATH}")
codex_transcript = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = codex_transcript
MODULE_SPEC.loader.exec_module(codex_transcript)


SESSION_ID = "01a03ae0-44ed-7f71-a93f-b38819732f12"
EXPORTED_AT = "2026-08-28 12:34 PDT"
SESSION_STARTED_TIMESTAMP = "2026-08-25T21:42:51.000Z"
LAST_ACTIVITY_TIMESTAMP = "2026-08-27T20:53:04.056Z"
LAST_ACTIVITY = "2026-08-27 13:53 PDT"
LAST_ACTIVITY_MOMENT = datetime(2026, 8, 27, 20, 53, 4, 56000, tzinfo=timezone.utc)
USER_MESSAGE_TIMESTAMP = "2026-08-27T20:51:04.056Z"
HOSTNAME = "host.example.com"


def transcript_metadata(
    codex_home: Path = Path("/real/codex/home"),
    session_file: Path = Path("/real/codex/home/sessions/session.jsonl"),
) -> codex_transcript.TranscriptMetadata:
    return codex_transcript.TranscriptMetadata(
        exported_at=EXPORTED_AT,
        session_last_activity=LAST_ACTIVITY,
        hostname=HOSTNAME,
        codex_home=codex_home,
        session_file=session_file,
        session_id=SESSION_ID,
    )


def session_metadata(session_id: str = SESSION_ID) -> dict[str, object]:
    return {
        "timestamp": SESSION_STARTED_TIMESTAMP,
        "type": "session_meta",
        "payload": {"id": session_id, "session_id": "unrelated-root-session-id"},
    }


def completed_message(
    item_type: str,
    text: str,
    item_id: str,
    phase: str | None = None,
    timestamp: str = LAST_ACTIVITY_TIMESTAMP,
) -> dict[str, object]:
    item: dict[str, object] = {
        "type": item_type,
        "id": item_id,
        "content": [
            {
                "type": "text" if item_type == "UserMessage" else "Text",
                "text": text,
            }
        ],
    }
    if phase is not None:
        item["phase"] = phase
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": item},
    }


def jsonl_bytes(records: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        for record in records
    )


class TemporaryCodexHome(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.home = Path(self.temp_directory.name)

    def write_session(
        self,
        records: list[dict[str, object]],
        *,
        archived: bool = False,
        compressed: bool = False,
        session_id: str = SESSION_ID,
    ) -> Path:
        root_name = "archived_sessions" if archived else "sessions"
        root = self.home / root_name / "2026" / "08" / "25"
        root.mkdir(parents=True)
        suffix = ".jsonl.zst" if compressed else ".jsonl"
        path = root / f"rollout-2026-08-25T14-42-51-{session_id}{suffix}"
        data = jsonl_bytes(records)
        if compressed:
            result = subprocess.run(
                ["zstd", "--quiet", "--compress", "--stdout"],
                input=data,
                stdout=subprocess.PIPE,
                check=True,
            )
            path.write_bytes(result.stdout)
        else:
            path.write_bytes(data)
        return path


class TranscriptTests(TemporaryCodexHome):
    def test_current_schema_exports_only_completed_visible_messages(self) -> None:
        records = [
            session_metadata(),
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": "secret developer text"}
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "injected user context"}
                    ],
                },
            },
            completed_message("UserMessage", "# Prompt\n\nUnicode: α", "user-1"),
            {
                "type": "event_msg",
                "payload": {"type": "agent_reasoning", "text": "hidden reasoning"},
            },
            completed_message(
                "AgentMessage", "Working on it.", "agent-1", phase="commentary"
            ),
            completed_message(
                "AgentMessage",
                "```python\nprint('done')\n```",
                "agent-2",
                phase="final_answer",
            ),
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "duplicate"},
            },
        ]
        path = self.write_session(records)

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual(
            transcript.messages,
            (
                codex_transcript.Message(
                    "user", "# Prompt\n\nUnicode: α", LAST_ACTIVITY_MOMENT
                ),
                codex_transcript.Message(
                    "assistant", "Working on it.", LAST_ACTIVITY_MOMENT
                ),
                codex_transcript.Message(
                    "assistant",
                    "```python\nprint('done')\n```",
                    LAST_ACTIVITY_MOMENT,
                ),
            ),
        )
        rendered = codex_transcript.render_markdown(
            transcript.messages, transcript_metadata()
        )
        self.assertNotIn("secret", rendered)
        self.assertNotIn("injected", rendered)
        self.assertNotIn("reasoning", rendered)
        self.assertNotIn("duplicate", rendered)

    def test_legacy_schema_preserves_repeated_text(self) -> None:
        records = [
            session_metadata(),
            {
                "timestamp": LAST_ACTIVITY_TIMESTAMP,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "same"},
            },
            {
                "timestamp": LAST_ACTIVITY_TIMESTAMP,
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "same",
                    "phase": "commentary",
                },
            },
            {
                "timestamp": LAST_ACTIVITY_TIMESTAMP,
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "same",
                    "phase": "final_answer",
                },
            },
        ]
        path = self.write_session(records)

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual(
            [message.text for message in transcript.messages], ["same"] * 3
        )
        self.assertEqual(
            [message.timestamp for message in transcript.messages],
            [LAST_ACTIVITY_MOMENT] * 3,
        )

    def test_completed_items_are_deduplicated_by_id_not_text(self) -> None:
        records = [
            session_metadata(),
            completed_message("UserMessage", "repeat", "one"),
            completed_message("UserMessage", "repeat", "one"),
            completed_message("UserMessage", "repeat", "two"),
        ]
        path = self.write_session(records)

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual(
            [message.text for message in transcript.messages], ["repeat", "repeat"]
        )

    def test_unknown_agent_phase_fails_closed(self) -> None:
        records = [
            session_metadata(),
            completed_message("UserMessage", "visible", "user"),
            completed_message("AgentMessage", "hidden", "agent", phase="analysis"),
        ]
        path = self.write_session(records)

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual([message.text for message in transcript.messages], ["visible"])

    def test_incomplete_final_line_is_ignored(self) -> None:
        path = self.write_session(
            [session_metadata(), completed_message("UserMessage", "visible", "user")]
        )
        with path.open("ab") as session_file:
            session_file.write(b'{"type":')

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertTrue(transcript.ignored_incomplete_final_line)
        self.assertEqual([message.text for message in transcript.messages], ["visible"])

    def test_newest_record_timestamp_is_the_last_activity(self) -> None:
        path = self.write_session(
            [
                session_metadata(),
                completed_message(
                    "UserMessage",
                    "newer",
                    "newer",
                    timestamp=LAST_ACTIVITY_TIMESTAMP,
                ),
                completed_message(
                    "AgentMessage",
                    "older record written later",
                    "older",
                    phase="final_answer",
                    timestamp="2026-08-26T20:53:04.056Z",
                ),
            ]
        )

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual(
            transcript.last_activity,
            datetime(2026, 8, 27, 20, 53, 4, 56000, tzinfo=timezone.utc),
        )

    def test_file_mtime_is_the_last_activity_fallback(self) -> None:
        metadata = session_metadata()
        metadata.pop("timestamp")
        message = completed_message("UserMessage", "visible", "user")
        message.pop("timestamp")
        path = self.write_session([metadata, message])
        expected = datetime(2026, 8, 27, 20, 53, 4, tzinfo=timezone.utc)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        transcript = codex_transcript.read_session(path, SESSION_ID)

        self.assertEqual(transcript.last_activity, expected)
        self.assertIsNone(transcript.messages[0].timestamp)

    def test_malformed_interior_line_is_an_error(self) -> None:
        path = self.write_session([session_metadata()])
        with path.open("ab") as session_file:
            session_file.write(b"not json\n")
            session_file.write(
                jsonl_bytes([completed_message("UserMessage", "visible", "user")])
            )

        with self.assertRaisesRegex(codex_transcript.SessionFileError, "line 2"):
            codex_transcript.read_session(path, SESSION_ID)

    def test_metadata_id_must_match_filename_id(self) -> None:
        path = self.write_session(
            [session_metadata("11111111-1111-1111-1111-111111111111")]
        )

        with self.assertRaisesRegex(
            codex_transcript.SessionFileError, "does not match"
        ):
            codex_transcript.read_session(path, SESSION_ID)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is not installed")
    def test_compressed_archived_session(self) -> None:
        path = self.write_session(
            [session_metadata(), completed_message("UserMessage", "archived", "user")],
            archived=True,
            compressed=True,
        )

        transcript = codex_transcript.load_session(self.home, SESSION_ID)

        self.assertEqual(transcript.path, path)
        self.assertEqual(
            [message.text for message in transcript.messages], ["archived"]
        )

    def test_duplicate_active_and_archived_sessions_are_ambiguous(self) -> None:
        records = [
            session_metadata(),
            completed_message("UserMessage", "visible", "user"),
        ]
        self.write_session(records)
        self.write_session(records, archived=True)

        with self.assertRaisesRegex(codex_transcript.TranscriptError, "multiple files"):
            codex_transcript.load_session(self.home, SESSION_ID)

    def test_pacific_timestamp_uses_pst_or_pdt(self) -> None:
        winter = datetime(2026, 1, 15, 20, 5, 59, tzinfo=timezone.utc)
        summer = datetime(2026, 8, 28, 19, 34, 59, tzinfo=timezone.utc)

        self.assertEqual(
            codex_transcript.format_pacific_datetime(winter),
            "2026-01-15 12:05 PST",
        )
        self.assertEqual(
            codex_transcript.format_pacific_datetime(summer),
            "2026-08-28 12:34 PDT",
        )

    def test_message_timestamp_includes_offset_and_abbreviation(self) -> None:
        winter = datetime(2026, 1, 15, 20, 5, 59, tzinfo=timezone.utc)
        summer = datetime(2026, 8, 28, 19, 34, 59, tzinfo=timezone.utc)

        self.assertEqual(
            codex_transcript.format_message_datetime(winter),
            "2026-01-15T12:05:59-08:00 (PST)",
        )
        self.assertEqual(
            codex_transcript.format_message_datetime(summer),
            "2026-08-28T12:34:59-07:00 (PDT)",
        )

    def test_elapsed_duration_is_compact(self) -> None:
        start = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)
        cases = (
            (timedelta(0), "<1s"),
            (timedelta(microseconds=999999), "<1s"),
            (timedelta(seconds=8), "+8s"),
            (timedelta(minutes=2, seconds=12), "+2m 12s"),
            (timedelta(hours=1, minutes=7, seconds=59), "+1h 7m"),
            (timedelta(days=3, hours=4, minutes=59), "+3d 4h"),
        )

        for elapsed, expected in cases:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(
                    codex_transcript.format_elapsed_duration(start, start + elapsed),
                    expected,
                )

        self.assertIsNone(
            codex_transcript.format_elapsed_duration(
                start, start - timedelta(seconds=1)
            )
        )

    def test_message_delta_is_correct_across_dst_fallback(self) -> None:
        messages = (
            codex_transcript.Message(
                "user",
                "Before fallback",
                datetime(2026, 11, 1, 8, 59, tzinfo=timezone.utc),
            ),
            codex_transcript.Message(
                "assistant",
                "After fallback",
                datetime(2026, 11, 1, 9, 1, tzinfo=timezone.utc),
            ),
        )

        rendered = codex_transcript.render_markdown(messages, transcript_metadata())

        self.assertIn(
            "## User — 2026-11-01T01:59:00-07:00 (PDT)\n",
            rendered,
        )
        self.assertIn(
            "## Codex — 2026-11-01T01:01:00-08:00 (PST) · +2m after User\n",
            rendered,
        )

    def test_missing_timestamp_breaks_the_delta_chain(self) -> None:
        messages = (
            codex_transcript.Message(
                "user",
                "Timestamped",
                datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc),
            ),
            codex_transcript.Message("assistant", "No timestamp"),
            codex_transcript.Message(
                "user",
                "Timestamped again",
                datetime(2026, 8, 27, 19, 5, tzinfo=timezone.utc),
            ),
            codex_transcript.Message(
                "assistant",
                "Eight seconds later",
                datetime(2026, 8, 27, 19, 5, 8, tzinfo=timezone.utc),
            ),
        )

        rendered = codex_transcript.render_markdown(messages, transcript_metadata())

        self.assertIn("## Codex\n\nNo timestamp", rendered)
        self.assertIn(
            "## User — 2026-08-27T12:05:00-07:00 (PDT)\n",
            rendered,
        )
        self.assertIn(
            "## Codex — 2026-08-27T12:05:08-07:00 (PDT) · +8s after User\n",
            rendered,
        )

    def test_negative_message_delta_is_omitted(self) -> None:
        messages = (
            codex_transcript.Message(
                "user",
                "Later timestamp",
                datetime(2026, 8, 27, 19, 5, tzinfo=timezone.utc),
            ),
            codex_transcript.Message(
                "assistant",
                "Earlier timestamp",
                datetime(2026, 8, 27, 19, 4, tzinfo=timezone.utc),
            ),
        )

        rendered = codex_transcript.render_markdown(messages, transcript_metadata())

        self.assertIn(
            "## Codex — 2026-08-27T12:04:00-07:00 (PDT)\n",
            rendered,
        )
        self.assertNotIn("after User", rendered)

    def test_hostname_falls_back_when_fqdn_is_unavailable(self) -> None:
        with (
            mock.patch.object(codex_transcript.socket, "getfqdn", side_effect=OSError),
            mock.patch.object(
                codex_transcript.socket, "gethostname", return_value="short-host"
            ),
        ):
            hostname = codex_transcript.fully_qualified_hostname()

        self.assertEqual(hostname, "short-host")


class CommandLineTests(TemporaryCodexHome):
    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": os.fspath(self.home)}),
            mock.patch.object(
                codex_transcript,
                "current_pacific_datetime",
                return_value=EXPORTED_AT,
            ),
            mock.patch.object(
                codex_transcript,
                "fully_qualified_hostname",
                return_value=HOSTNAME,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = codex_transcript.main(arguments, "codex-transcript.py")
        return status, stdout.getvalue(), stderr.getvalue()

    def test_exactly_one_argument_is_required(self) -> None:
        for arguments in ([], [SESSION_ID, SESSION_ID]):
            with self.subTest(arguments=arguments):
                status, stdout, stderr = self.invoke(arguments)
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertIn("Usage: codex-transcript.py SESSION_ID", stderr)

    def test_help_uses_official_session_id_term(self) -> None:
        status, stdout, stderr = self.invoke(["--help"])

        self.assertEqual(status, 0)
        self.assertIn("Usage: codex-transcript.py SESSION_ID", stdout)
        self.assertEqual(stderr, "")

    def test_invalid_uuid_is_a_usage_error(self) -> None:
        status, stdout, stderr = self.invoke(["not-an-id"])

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid session ID", stderr)

    def test_unknown_session_is_an_error(self) -> None:
        status, stdout, stderr = self.invoke([SESSION_ID])

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("was not found", stderr)

    def test_transcript_is_the_only_stdout(self) -> None:
        session_path = self.write_session(
            [
                session_metadata(),
                completed_message(
                    "UserMessage",
                    "Question",
                    "user",
                    timestamp=USER_MESSAGE_TIMESTAMP,
                ),
                completed_message(
                    "AgentMessage", "Answer", "agent", phase="final_answer"
                ),
            ]
        )

        status, stdout, stderr = self.invoke([SESSION_ID])

        self.assertEqual(status, 0)
        self.assertEqual(
            stdout,
            "# Codex transcript\n"
            "\n"
            f"- Exported: `{EXPORTED_AT}`\n"
            f"- Session last activity: `{LAST_ACTIVITY}`\n"
            f"- Hostname: `{HOSTNAME}`\n"
            f"- Codex home (realpath): `{self.home.resolve()}`\n"
            f"- Session JSONL (realpath): `{session_path.resolve()}`\n"
            f"- Session ID: `{SESSION_ID}`\n"
            "\n"
            "## User — 2026-08-27T13:51:04-07:00 (PDT)\n"
            "\n"
            "Question\n"
            "\n"
            "## Codex — 2026-08-27T13:53:04-07:00 (PDT) · +2m after User\n"
            "\n"
            "Answer\n",
        )
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
