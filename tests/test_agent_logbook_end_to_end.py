"""Exercise installed entry points with isolated history and a stand-in Codex."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LogbookWorkflowTests(unittest.TestCase):
    def test_hooks_record_lookup_and_wt_resume_then_show_ambiguous_sessions(self):
        with tempfile.TemporaryDirectory(prefix="logbook-workflow-") as temporary:
            area = Path(temporary).resolve()
            repo = area / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/unit/fixture.git",
                ],
                check=True,
            )
            catalog = {
                "version": 1,
                "config": {"upstream_repo": "unit/fixture"},
                "branches": {
                    "main": {
                        "path": str(repo),
                        "pr": {
                            "base_repo": "unit/fixture",
                            "number": 123,
                            "url": "https://github.com/unit/fixture/pull/123",
                        },
                    }
                },
            }
            (repo / ".git" / "rwgk-wt.json").write_text(json.dumps(catalog))
            binaries = area / "bin"
            binaries.mkdir()
            marker = area / "launched.json"
            codex = binaries / "codex"
            codex.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ['LOGBOOK_TEST_LAUNCH']).write_text(json.dumps(sys.argv[1:]))\n"
            )
            codex.chmod(0o755)
            history = area / "history" / "sessions" / "2026" / "10" / "28"
            history.mkdir(parents=True)
            environment = {
                **os.environ,
                "AGENT_LOGBOOK_DIR": str(area / "logbook"),
                "CODEX_HOME": str(area / "history"),
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "LOGBOOK_TEST_LAUNCH": str(marker),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            environment.pop("AGENT_LOGBOOK_SESSION_ID", None)

            def run(name, *args, payload=None):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "bin" / name), *args],
                    cwd=repo,
                    env=environment,
                    input=json.dumps(payload) if payload else None,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result

            def register(session_id):
                transcript = history / f"rollout-fixture-{session_id}.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": session_id, "cwd": str(repo)},
                        }
                    )
                    + "\n"
                )
                run(
                    "agent-logbook",
                    "hook",
                    payload={
                        "hook_event_name": "SessionStart",
                        "session_id": session_id,
                        "cwd": str(repo),
                        "transcript_path": str(transcript),
                    },
                )
                environment["CODEX_THREAD_ID"] = session_id

            first = "00000000-0000-4000-8000-000000000001"
            register(first)
            run(
                "agent-logbook",
                "record",
                "https://example.slack.com/archives/C123/p1234567000000",
                "--relation",
                "discussed",
                "--note",
                "Investigate stream lifetime",
            )
            run(
                "agent-logbook",
                "hook",
                payload={
                    "hook_event_name": "PostToolUse",
                    "session_id": first,
                    "cwd": str(repo),
                    "tool_name": "apply_patch",
                    "tool_input": {
                        "command": "*** Begin Patch\n*** Add File: report.txt\n+results\n*** End Patch"
                    },
                    "tool_response": {},
                },
            )
            run("git-wt", "codex")
            self.assertEqual(
                json.loads(marker.read_text()), ["resume", first, "-C", str(repo)]
            )
            marker.unlink()
            run("codex-find", "report.txt")
            self.assertEqual(json.loads(marker.read_text())[1], first)
            marker.unlink()
            data = json.loads(
                run(
                    "codex-find",
                    "https://example.slack.com/archives/C123/p1234567000000",
                    "--json",
                ).stdout
            )
            self.assertEqual(data["matches"][0]["session_id"], first)
            self.assertFalse(marker.exists())
            second = "00000000-0000-4000-8000-000000000002"
            register(second)
            result = run("git-wt", "codex")
            self.assertIn(first, result.stdout)
            self.assertIn(second, result.stdout)
            self.assertIn("Investigate stream lifetime", result.stdout)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
