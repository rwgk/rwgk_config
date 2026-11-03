"""Session nicknames preserve exact identity and the target's workspace metadata."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from agent_logbook import cli, nicknames, resources, store


class NicknameCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="logbook-nickname-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.logbook = self.root / "logbook"
        self.history = self.root / "history"
        self.saved = self.root / "original-project"
        self.saved.mkdir()
        self.caller = self.root / "unrelated-directory"
        self.caller.mkdir()
        self.original = str(uuid.uuid4())
        self.fork = str(uuid.uuid4())
        environment = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.history),
                "CODEX_THREAD_ID": self.original,
                "AGENT_LOGBOOK_SESSION_ID": self.fork,
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        current = patch.object(cli.os, "getcwd", return_value=str(self.caller))
        current.start()
        self.addCleanup(current.stop)

    def transcript(self, target=None, **metadata):
        target = target or self.original
        path = self.history / "sessions" / "2026" / f"rollout-{target}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": target,
                        "cwd": str(self.saved),
                        **metadata,
                    },
                }
            )
            + "\n"
        )
        return path

    def record(self, target=None, *, available=True, **metadata):
        target = target or self.original
        if available:
            self.transcript(target)
        store.append_event(
            {"session_id": target, "cwd": str(self.saved), **metadata}, self.logbook
        )

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli.main(list(args), directory=self.logbook)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_implicit_assignment_uses_live_id_and_bootstraps_saved_metadata(self):
        path = self.transcript()
        before = path.read_bytes()
        with patch.object(
            cli.context,
            "workspace",
            side_effect=AssertionError("must not associate caller"),
        ):
            status, stdout, stderr = self.invoke("nickname", "wtdesign")
        self.assertEqual(status, 0, stderr)
        self.assertIn(self.original, stdout)
        self.assertEqual(nicknames.get("wtdesign", self.logbook), self.original)
        (session,) = store.load_sessions(self.logbook)
        self.assertEqual(session["cwd"], str(self.saved))
        self.assertEqual(session["links"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_explicit_assignment_preserves_existing_target_metadata(self):
        self.record(
            note="Original project",
            links=[
                {
                    **resources.normalize("https://github.com/unit/repo/pull/12"),
                    "relation": "reviewed",
                }
            ],
        )
        before = store.load_sessions(self.logbook)
        status, _, stderr = self.invoke("nickname", "wtdesign", self.original)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(store.load_sessions(self.logbook), before)

    def test_reassignment_and_fork_are_explicit_and_same_target_is_harmless(self):
        self.record()
        self.assertEqual(self.invoke("nickname", "wtdesign")[0], 0)
        self.transcript(self.fork, forked_from_id=self.original)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": self.fork}):
            status, stdout, stderr = self.invoke("resume", "wtdesign", "--json")
            self.assertEqual(status, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["matches"][0]["session_id"], self.original
            )
            status, stdout, stderr = self.invoke("nickname", "wtdesign", "--json")
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["result"], "reassigned")
        self.assertEqual(result["previous_session_id"], self.original)
        self.assertEqual(result["session_id"], self.fork)
        status, stdout, stderr = self.invoke(
            "nickname", "wtdesign", self.fork, "--json"
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "unchanged")

    def test_named_resume_uses_exact_uuid_and_saved_directory(self):
        self.record()
        self.invoke("nickname", "wtdesign")
        with patch.object(cli.subprocess, "run") as launch:
            launch.return_value.returncode = 9
            status, stdout, stderr = self.invoke("resume", "wtdesign")
        self.assertEqual(status, 9, stderr)
        self.assertIn("(wtdesign)", stdout)
        launch.assert_called_once_with(
            ["codex", "resume", self.original, "-C", str(self.saved)], check=False
        )

    def test_typo_never_falls_back_to_similar_names_or_topic_hints(self):
        self.record(note="wtdesig")
        self.invoke("nickname", "wtdesign")
        with patch.object(cli.subprocess, "run") as launch:
            status, _, stderr = self.invoke("resume", "wtdesig")
        self.assertEqual(status, 1)
        self.assertIn("Unknown session nickname", stderr)
        launch.assert_not_called()

    def test_list_and_json_suppress_launch_and_resource_lookup_shows_nicknames(self):
        self.record(
            links=[
                {
                    **resources.normalize("https://github.com/unit/repo/pull/12"),
                    "relation": "reviewed",
                }
            ]
        )
        self.invoke("nickname", "wtdesign")
        with patch.object(cli.subprocess, "run") as launch:
            for options in (("--list",), ("--json",)):
                self.assertEqual(self.invoke("resume", "wtdesign", *options)[0], 0)
            status, stdout, stderr = self.invoke("find", "unit/repo#12", "--list")
            self.assertEqual(status, 0, stderr)
            self.assertIn("nicknames: wtdesign", stdout)
        launch.assert_not_called()

    def test_missing_history_is_reported_without_launch(self):
        self.record(available=False, host="other.example")
        self.invoke("nickname", "wtdesign")
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke("resume", "wtdesign", "--json")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            json.loads(stdout)["matches"][0]["resume_blocked_reason"],
            "history_unavailable",
        )
        launch.assert_not_called()

    def test_missing_saved_directory_requires_explicit_override(self):
        self.record()
        self.invoke("nickname", "wtdesign")
        self.saved.rmdir()
        with patch.object(cli.subprocess, "run") as launch:
            status, stdout, stderr = self.invoke("resume", "wtdesign")
            self.assertEqual(status, 0, stderr)
            self.assertIn("wt codex wtdesign --cwd", stdout)
            launch.assert_not_called()
            launch.return_value.returncode = 0
            status, _, stderr = self.invoke(
                "resume", "wtdesign", "--cwd", str(self.caller)
            )
        self.assertEqual(status, 0, stderr)
        launch.assert_called_once_with(
            ["codex", "resume", self.original, "-C", str(self.caller)], check=False
        )

    def test_unknown_target_cannot_replace_existing_pointer(self):
        self.record()
        self.invoke("nickname", "wtdesign")
        status, _, stderr = self.invoke("nickname", "wtdesign", self.fork)
        self.assertEqual(status, 1)
        self.assertIn("No recorded session or local Codex history", stderr)
        self.assertEqual(nicknames.get("wtdesign", self.logbook), self.original)

    def test_listing_and_delete_leave_session_and_history_unchanged(self):
        path = self.transcript()
        self.invoke("nickname", "wtdesign")
        before = store.load_sessions(self.logbook)
        status, stdout, stderr = self.invoke("nickname", "--json")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            json.loads(stdout),
            {"nicknames": [{"name": "wtdesign", "session_id": self.original}]},
        )
        self.assertEqual(self.invoke("nickname", "--delete", "wtdesign")[0], 0)
        self.assertEqual(store.load_sessions(self.logbook), before)
        self.assertTrue(path.exists())
        self.assertEqual(nicknames.load(self.logbook), {})

    def test_assignment_outside_session_requires_uuid_even_with_logbook_override(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ""}):
            status, _, stderr = self.invoke("nickname", "wtdesign")
        self.assertEqual(status, 1)
        self.assertIn("Supply a session UUID", stderr)
        self.assertFalse(self.logbook.exists())


class NicknameEntryPointTests(unittest.TestCase):
    def test_commands_work_outside_git_and_resume_only_through_uuid(self):
        with tempfile.TemporaryDirectory(prefix="nickname-entrypoint-") as temporary:
            root = Path(temporary).resolve()
            project, unrelated, binaries = (
                root / name for name in ("saved", "unrelated", "bin")
            )
            for path in (project, unrelated, binaries):
                path.mkdir()
            target = str(uuid.uuid4())
            history = root / "history" / "sessions"
            history.mkdir(parents=True)
            transcript = history / f"rollout-{target}.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": target, "cwd": str(project)},
                    }
                )
                + "\n"
            )
            marker = root / "launched.json"
            codex = binaries / "codex"
            codex.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ['NICKNAME_TEST_LAUNCH']).write_text(json.dumps(sys.argv[1:]))\n"
            )
            codex.chmod(0o755)
            environment = {
                **os.environ,
                "AGENT_LOGBOOK_DIR": str(root / "logbook"),
                "CODEX_HOME": str(history.parent),
                "CODEX_THREAD_ID": target,
                "NICKNAME_TEST_LAUNCH": str(marker),
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            entry = Path(__file__).resolve().parents[1] / "bin" / "git-wt"

            def run(*args):
                result = subprocess.run(
                    [sys.executable, str(entry), *args],
                    cwd=unrelated,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result

            run("nickname", "wtdesign")
            run("codex", "wtdesign", "--list")
            self.assertFalse(marker.exists())
            run("codex", "wtdesign")
            self.assertEqual(
                json.loads(marker.read_text()), ["resume", target, "-C", str(project)]
            )
            run("nickname", "--delete", "wtdesign")
            self.assertTrue(transcript.exists())
            self.assertFalse((unrelated / ".git").exists())


if __name__ == "__main__":
    unittest.main()
