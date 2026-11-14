"""Creation capture uses session environment and leaves manual wt use alone."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import logbook


class PublicationLogbookTests(unittest.TestCase):
    def test_outside_codex_does_not_consult_workspace_or_store(self):
        with (
            mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": "", "AGENT_LOGBOOK_SESSION_ID": ""}
            ),
            mock.patch("agent_logbook.context.workspace") as workspace,
            mock.patch("agent_logbook.store.append_event") as append,
        ):
            logbook.record_created_request(
                "https://github.com/Acme/project/pull/42", "."
            )
        workspace.assert_not_called()
        append.assert_not_called()

    def test_explicit_session_override_matches_agent_logbook_record(self):
        with (
            mock.patch.dict(
                os.environ,
                {"CODEX_THREAD_ID": "thread", "AGENT_LOGBOOK_SESSION_ID": "override"},
            ),
            mock.patch("agent_logbook.context.workspace", return_value={"keys": []}),
            mock.patch("agent_logbook.store.append_event") as append,
        ):
            logbook.record_created_request(
                "https://github.com/Acme/project/pull/42", "."
            )
        self.assertEqual(append.call_args.args[0]["session_id"], "override")


if __name__ == "__main__":
    unittest.main()
