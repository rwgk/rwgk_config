"""Failed pushes retain useful hook output and sanitize both captured streams."""

import os
import subprocess
import unittest
from unittest import mock

import test_wt_github as fixtures
from rwgk_wt import github, pr_push
from rwgk_wt.core import WTError


class PushDiagnosticTests(unittest.TestCase):
    def failure(self, stdout="", stderr="", code=1):
        result = subprocess.CompletedProcess(["git", "push"], code, stdout, stderr)
        with self.assertRaises(WTError) as stopped:
            pr_push._push_error(result)
        return str(stopped.exception)

    def test_useful_stdout_is_retained_beside_generic_git_stderr(self):
        message = self.failure(
            "validation: generated documentation is out of date\n",
            "error: failed to push some refs to 'github.com:unit/project'\n",
        )
        self.assertIn("generated documentation is out of date", message)
        self.assertIn("failed to push some refs", message)
        self.assertNotIn("authentication", message)

    def test_permission_feedback_redacts_url_credentials_in_both_streams(self):
        message = self.failure(
            "Permission denied for https://alice:stdout-secret@github.com/unit/project\n",
            "error: https://bob:stderr-secret@github.com/unit/project rejected the push\n",
        )
        self.assertIn("Allow edits from maintainers", message)
        self.assertIn("Permission denied", message)
        self.assertIn("rejected the push", message)
        self.assertEqual(message.count("https://[redacted]@github.com"), 2)
        self.assertNotIn("stdout-secret", message)
        self.assertNotIn("stderr-secret", message)

    def test_stale_lease_in_stdout_is_classified_and_both_streams_remain_visible(self):
        message = self.failure(
            " ! [rejected] HEAD -> topic (stale info)\n",
            "error: failed to push some refs\n",
        )
        self.assertIn("changed during push", message)
        self.assertIn("wt pull --rebase", message)
        self.assertIn("HEAD -> topic (stale info)", message)
        self.assertIn("failed to push some refs", message)

    def test_empty_failure_still_reports_exit_code(self):
        message = self.failure(code=128)
        self.assertIn("128", message)
        self.assertNotIn("authentication", message)


@unittest.skipUnless(
    os.environ.get("WT_TEST_WORKTREE_ROOT"),
    "Set WT_TEST_WORKTREE_ROOT under $W/trees/.wt-tests for Git fixtures",
)
class PublicationDiagnosticTests(unittest.TestCase):
    def test_publication_failure_exposes_both_streams_without_retry_or_ref_changes(
        self,
    ):
        fixture = fixtures.GitHubTests(
            "test_new_publication_uses_fixed_empty_lease_without_setting_tracking"
        )
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        repo = fixture.branch()
        before_refs = fixtures.git(fixture.remotes["origin"], "show-ref")
        before_config = (repo.common / "config").read_bytes()
        before_catalog = repo.state_path.read_bytes()
        result = subprocess.CompletedProcess(
            ["git", "push"],
            1,
            "hook: documentation check failed https://alice:stdout-secret@github.com/unit/project\n",
            "error: failed to push https://bob:stderr-secret@github.com/unit/project\n",
        )
        with (
            mock.patch.object(github, "_push_result", return_value=result) as push,
            self.assertRaises(WTError) as stopped,
        ):
            fixture.publish(repo)
        message = str(stopped.exception)
        self.assertIn("Publication push failed", message)
        self.assertIn("documentation check failed", message)
        self.assertIn("failed to push", message)
        self.assertEqual(message.count("https://[redacted]@github.com"), 2)
        self.assertNotIn("stdout-secret", message)
        self.assertNotIn("stderr-secret", message)
        push.assert_called_once()
        self.assertEqual(
            fixtures.git(fixture.remotes["origin"], "show-ref"), before_refs
        )
        self.assertEqual((repo.common / "config").read_bytes(), before_config)
        self.assertEqual(repo.state_path.read_bytes(), before_catalog)
        self.assertFalse(fixture.prs)


if __name__ == "__main__":
    unittest.main()
