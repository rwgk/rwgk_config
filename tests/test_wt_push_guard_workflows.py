"""Installed pre-push guards allow validated wt workflows through real Git."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import github, lifecycle, pr_push, push_guard
from test_wt_gitlab import GitLabFixture, git


class PushGuardWorkflowTests(GitLabFixture):
    def test_raw_review_push_is_blocked_and_wt_push_preserves_original_hook_input(self):
        self.contributor_mr()
        repo = self.open_review()
        old = repo.oid("HEAD")
        original_calls = self.root / "original-hook.json"
        hooks = self.root / "maintained-hooks"
        hooks.mkdir()
        repo.git("config", "core.hooksPath", str(hooks))
        original = hooks / "pre-push"
        original.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"Path({str(original_calls)!r}).write_text(json.dumps({{'args': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
        )
        original.chmod(0o755)
        push_guard.install(repo)
        (repo.root / "local").write_text("guarded contribution\n")
        new = self.commit(repo.root, "Guarded contribution")
        tracking = self.tracking(repo)
        env = dict(os.environ)
        env.pop(push_guard.PERMIT_ENV, None)
        env.pop(push_guard.RECOVERY_ENV, None)
        denied = subprocess.run(
            [
                "git",
                "-C",
                str(repo.root),
                "push",
                str(self.remotes["contributor"]),
                "HEAD:refs/heads/topic",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("Raw git push is blocked", denied.stderr)
        self.assertIn("wt push", denied.stderr)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), old)
        self.assertFalse(original_calls.exists())
        result = pr_push.push(repo)
        self.assertEqual(result["result"], "pushed")
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), new)
        observed = json.loads(original_calls.read_text())
        self.assertEqual(observed["args"], [str(self.remotes["contributor"])] * 2)
        rows = [line.split() for line in observed["stdin"].splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], [new, "refs/heads/topic", old])
        self.assertEqual(self.tracking(repo), tracking)
        self.assertNotIn(push_guard.PERMIT_ENV, os.environ)
        self.assert_checkouts(repo)

    def test_owned_publication_rewrite_archives_and_cleanup_pass_installed_guard(self):
        repo = self.owned_branch()
        push_guard.install(repo)
        tracking = self.tracking(repo)
        published = github.publish(repo)
        number = published["prs"][0]["number"]
        old = repo.oid("HEAD")
        git(repo.root, "commit", "--amend", "-m", "Rewritten guarded change")
        new = repo.oid("HEAD")
        self.assertNotEqual(old, new)
        github.publish(repo)
        self.assertEqual(
            git(self.remotes["origin"], "rev-parse", repo.current_branch()), new
        )
        archived = git(
            self.remotes["origin"],
            "for-each-ref",
            "--format=%(objectname)",
            "refs/tags/",
        )
        self.assertIn(old, archived.splitlines())
        self.assertEqual(self.tracking(repo), tracking)
        self.mrs[number]["state"] = "closed"
        branch, path = repo.current_branch(), repo.root
        retired = lifecycle.retire(repo, branch, apply=True)
        self.assertEqual(retired["status"], "complete")
        self.assertFalse(path.exists())
        self.assertFalse(
            git(
                self.remotes["origin"],
                "show-ref",
                "--verify",
                "refs/heads/" + branch,
                check=False,
            )
        )
        archived = git(
            self.remotes["origin"],
            "for-each-ref",
            "--format=%(objectname)",
            "refs/tags/",
        )
        self.assertIn(new, archived.splitlines())
        self.assertNotIn(push_guard.PERMIT_ENV, os.environ)
        self.assert_checkouts()
