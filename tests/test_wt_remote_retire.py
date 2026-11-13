"""Remote-only retirement keeps auditable history and fixed deletion leases."""

import contextlib
import copy
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

import test_wt_github as fixtures
from rwgk_wt import cli, lifecycle, remote_retire
from rwgk_wt.core import WTError

git = fixtures.git


class RemoteRetirementTests(unittest.TestCase):
    remove_external_trees = fixtures.GitHubTests.remove_external_trees
    response = fixtures.GitHubTests.response
    pushes = fixtures.GitHubTests.pushes

    def setUp(self):
        environment = mock.patch.dict(
            os.environ,
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "commit.gpgsign",
                "GIT_CONFIG_VALUE_0": "false",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        fixtures.GitHubTests.setUp(self)
        git(self.primary, "config", "commit.gpgsign", "false")
        self.branch = "rwgk/obsolete-experiment"
        self.tip = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            self.repo.oid("main"),
            "-m",
            "Retained experiment",
        )
        self.repo_info = {
            "Acme/project": {
                "full_name": "Acme/project",
                "default_branch": "main",
                "fork": False,
                "owner": {"login": "Acme"},
                "permissions": {"push": True, "admin": False},
            },
            "ralf/project": {
                "full_name": "ralf/project",
                "default_branch": "main",
                "fork": True,
                "owner": {"login": "ralf"},
                "permissions": {"push": True, "admin": True},
                "parent": {"full_name": "Acme/project"},
                "source": {"full_name": "Acme/project"},
            },
        }
        self.protected = set()
        self.open_prs = []
        self.create_remote()
        self.commands.clear()

    def gh(self, argv, input):
        self.gh_calls.append((list(argv), json.loads(input) if input else None))
        method, endpoint = argv[5:7]
        self.assertEqual(method, "GET", "Retirement must not mutate forge metadata")
        if endpoint == "user":
            return self.response({"login": "ralf"})
        parsed = urlsplit(endpoint)
        parts = parsed.path.split("/")
        if len(parts) < 3 or parts[0] != "repos":
            self.fail(f"Unexpected forge request: {endpoint}")
        identity = "/".join(parts[1:3])
        if len(parts) == 3:
            return self.response(copy.deepcopy(self.repo_info[identity]))
        if parts[3] == "branches":
            branch = unquote("/".join(parts[4:]))
            remote = "upstream" if identity == "Acme/project" else "origin"
            tip = self.remote_tip(remote, branch)
            if tip is None:
                raise WTError("HTTP 404: branch no longer exists")
            return self.response(
                {
                    "name": branch,
                    "protected": (identity, branch) in self.protected,
                    "commit": {"sha": tip},
                }
            )
        if parts[3] == "pulls":
            query = parse_qs(parsed.query)
            matches = []
            for pr in self.open_prs:
                if pr["base"]["repo"]["full_name"] != identity:
                    continue
                if "head" in query:
                    owner = pr["head"]["repo"]["full_name"].split("/")[0]
                    if query["head"][0] != owner + ":" + pr["head"]["ref"]:
                        continue
                if "base" in query and query["base"][0] != pr["base"]["ref"]:
                    continue
                matches.append(copy.deepcopy(pr))
            return self.response(matches)
        self.fail(f"Unexpected forge request: {endpoint}")

    def create_remote(self, remote="upstream", branch=None, oid=None):
        git(
            self.primary,
            "push",
            remote,
            f"{oid or self.tip}:refs/heads/{branch or self.branch}",
        )

    def remote_tip(self, remote="upstream", branch=None):
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(self.remotes[remote]),
                "show-ref",
                "--hash",
                "--verify",
                "refs/heads/" + (branch or self.branch),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if not result.returncode else None

    def retire(self, *, branch=None, remote="upstream", apply=False, expect=None):
        return remote_retire.retire(
            self.repo,
            branch or self.branch,
            remote=remote,
            expect=expect,
            apply=apply,
        )

    def apply(self, **options):
        return self.retire(apply=True, expect=self.tip, **options)

    def records(self):
        directory = Path(self.repo.config["backtracking"])
        return [json.loads(path.read_text()) for path in directory.glob("*.json")]

    def assert_recovery(self, record):
        self.assertEqual(
            self.repo.oid(record["snapshots"][0]["recovery_ref"]), self.tip
        )
        path = Path(record["backtracking_path"])
        self.assertEqual(json.loads(path.read_text()), record)
        journal = self.repo.common / "rwgk-wt/operations" / (record["id"] + ".json")
        self.assertEqual(json.loads(journal.read_text()), record)

    def local_state(self):
        return (
            git(
                self.primary,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/heads/",
            ),
            git(self.primary, "worktree", "list", "--porcelain"),
            git(self.primary, "config", "--local", "--list"),
        )

    def open_pr(self, *, target="Acme/project", source=None, head=None, base="main"):
        return {
            "number": 81,
            "state": "open",
            "head": {
                "repo": {"full_name": source or target},
                "ref": head or self.branch,
            },
            "base": {"repo": {"full_name": target}, "ref": base},
        }

    def test_preview_reports_fixed_target_without_creating_journal_or_refs(self):
        before = self.local_state()
        plan = self.retire()
        self.assertEqual(plan["kind"], "remote-retire")
        self.assertEqual(plan["source"]["ref"], "refs/heads/" + self.branch)
        self.assertEqual(plan["source"]["oid"], self.tip)
        self.assertIn("--expect " + self.tip, plan["apply_command"])
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())
        self.assertFalse(self.repo.git("for-each-ref", "refs/rwgk/backtracking/"))
        self.assertEqual(self.remote_tip(), self.tip)
        self.assertEqual(self.local_state(), before)

    def test_apply_requires_the_exact_observed_sha_before_any_preservation(self):
        for expected in (None, self.repo.oid("main")):
            with self.subTest(expected=expected), self.assertRaises(WTError):
                self.retire(apply=True, expect=expected)
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())
        self.assertEqual(self.remote_tip(), self.tip)

    def test_apply_preserves_before_deletion_and_keeps_local_state(self):
        before = self.local_state()
        original = lifecycle._push
        observed = []

        def check_before_push(*args, **kwargs):
            record = self.records()[0]
            self.assert_recovery(record)
            observed.append(kwargs["updates"])
            return original(*args, **kwargs)

        with mock.patch.object(lifecycle, "_push", side_effect=check_before_push):
            record = self.apply()
        self.assertEqual(record["status"], "complete")
        self.assertIsNone(record["branch"])
        self.assertEqual(record["source"]["repo"], "Acme/project")
        self.assertEqual(record["source"]["remote"], "upstream")
        self.assertEqual(record["source"]["url"], self.identities["upstream"])
        self.assertIsNone(self.remote_tip())
        self.assert_recovery(record)
        self.assertEqual(self.local_state(), before)
        self.assertEqual(
            observed[-1],
            [{"ref": "refs/heads/" + self.branch, "old": self.tip, "new": None}],
        )
        self.assertEqual(record["remote_actions"][-1]["action"], "branch-deleted")
        self.assertEqual(len(record["archives"]), 1)
        archive = record["archives"][0]
        self.assertTrue(archive["ref"].startswith("refs/tags/rwgk/archive/"))
        self.assertEqual(
            git(self.remotes["origin"], "rev-parse", archive["ref"]), self.tip
        )
        self.assertEqual(
            git(
                self.remotes["origin"],
                "for-each-ref",
                "--format=%(refname)",
                "refs/heads/",
            ),
            "refs/heads/main",
        )
        self.assertIn(
            "archive-created", [event["action"] for event in record["remote_actions"]]
        )
        feedback = "\n".join(cli._cleanup_lines(self.repo, record))
        self.assertIn("created archive tag", feedback)
        self.assertNotIn("creation not recorded", feedback)

    def test_upstream_covered_tip_needs_no_archive_tag(self):
        git(
            self.remotes["upstream"],
            "update-ref",
            "refs/heads/" + self.branch,
            self.repo.oid("main"),
            self.tip,
        )
        record = self.retire(apply=True, expect=self.repo.oid("main"))
        self.assertEqual(record["archives"], [])
        self.assertFalse(git(self.remotes["origin"], "for-each-ref", "refs/tags/"))
        self.assertIsNone(self.remote_tip())

    def test_existing_immutable_archive_is_reused(self):
        archive = lifecycle._tag_for(self.repo, self.tip)
        git(self.primary, "push", "origin", f"{self.tip}:{archive}")
        self.commands.clear()
        record = self.apply()
        self.assertEqual(record["archives"][0]["ref"], archive)
        self.assertFalse(
            any(
                any(update.startswith(self.tip + ":refs/tags/") for update in command)
                for command in self.pushes()
            )
        )
        self.assertIn(
            "archive-reused", [event["action"] for event in record["remote_actions"]]
        )
        feedback = "\n".join(cli._cleanup_lines(self.repo, record))
        self.assertIn("reused archive tag", feedback)
        self.assertNotIn("creation not recorded", feedback)

    def test_own_fork_accepts_legacy_branch_name_without_rwgk_prefix(self):
        branch = "review/old-experiment"
        self.create_remote(remote="origin", branch=branch)
        record = self.apply(remote="origin", branch=branch)
        self.assertEqual(record["source"]["repo"], "ralf/project")
        self.assertEqual(record["source"]["ref"], "refs/heads/" + branch)
        self.assertIsNone(self.remote_tip("origin", branch))

    def test_canonical_remote_refuses_branches_outside_ralf_namespace(self):
        branch = "contributor/old-experiment"
        self.create_remote(branch=branch)
        with self.assertRaisesRegex(WTError, "rwgk/"):
            self.apply(branch=branch)
        self.assertEqual(self.remote_tip(branch=branch), self.tip)
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())

    def test_defaults_and_protected_branches_are_not_deleted(self):
        self.repo_info["Acme/project"]["default_branch"] = self.branch
        with self.assertRaises(WTError):
            self.apply()
        self.repo_info["Acme/project"]["default_branch"] = "main"
        self.protected.add(("Acme/project", self.branch))
        with self.assertRaises(WTError):
            self.apply()
        with self.assertRaises(WTError):
            self.apply(branch="main")
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())

    def test_other_owners_forks_and_unsupported_forges_are_rejected(self):
        self.identities["contributor"] = "https://github.com/contributor/project.git"
        self.remotes["contributor"] = self.remotes["origin"]
        git(self.primary, "remote", "add", "contributor", str(self.remotes["origin"]))
        self.repo_info["contributor/project"] = {
            **self.repo_info["ralf/project"],
            "full_name": "contributor/project",
            "owner": {"login": "contributor"},
        }
        with self.assertRaises(WTError):
            self.apply(remote="contributor")
        self.repo.config["provider"] = "gitlab"
        with self.assertRaisesRegex(WTError, "GitHub"):
            self.apply()
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())

    def test_open_pr_head_or_base_protects_source_in_both_repositories(self):
        self.create_remote(remote="origin")
        cases = (
            self.open_pr(source="ralf/project"),
            self.open_pr(target="ralf/project", source="ralf/project"),
            self.open_pr(target="ralf/project", head="unrelated", base=self.branch),
        )
        for pr in cases:
            with self.subTest(pr=pr):
                self.open_prs = [pr]
                with self.assertRaises(WTError):
                    self.apply(remote="origin")
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())

    def test_managed_publication_or_stack_layer_requires_normal_cleanup(self):
        self.repo.branches["rwgk/active-local"] = {
            "role": "owned",
            "path": str(self.primary),
            "publication": {
                "remote": "upstream",
                "repo": "Acme/project",
                "ref": "refs/heads/" + self.branch,
                "oid": self.tip,
            },
        }
        self.repo.save()
        with self.assertRaises(WTError):
            self.apply()
        self.repo.branches.pop("rwgk/active-local")
        self.repo.save()
        (self.repo.common / "gh-stack").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "stacks": [
                        {
                            "trunk": {"branch": "main"},
                            "branches": [
                                {"branch": self.branch, "base": self.repo.oid("main")}
                            ],
                        }
                    ],
                }
            )
        )
        with self.assertRaises(WTError):
            self.apply()
        self.assertFalse(self.records())
        self.assertFalse(self.pushes())

    def test_unrelated_review_with_a_deleted_fork_does_not_block_retirement(self):
        self.repo.branches["rwgk/review-pr81"] = {
            "role": "review",
            "path": str(self.primary),
            "pr": {
                "head_repo": None,
                "head_ref": "deleted-fork-topic",
                "base_repo": "Acme/project",
                "base_ref": "main",
            },
        }
        self.repo.save()
        self.assertEqual(self.retire()["source"]["oid"], self.tip)

    def test_completed_local_retirement_is_crosslinked_without_rewriting_it(self):
        git(self.primary, "branch", self.branch, self.tip)
        previous = lifecycle.retire(self.repo, self.branch, local_only=True, apply=True)
        original_bytes = Path(previous["backtracking_path"]).read_bytes()
        record = self.apply()
        links = record["local_history"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["evidence_kind"], "completed-retirement")
        self.assertEqual(links[0]["record_id"], previous["id"])
        self.assertEqual(links[0]["local_branch"], self.branch)
        self.assertEqual(links[0]["local_oid"], self.tip)
        self.assertEqual(
            Path(previous["backtracking_path"]).read_bytes(), original_bytes
        )

    def test_legacy_log_is_evidence_not_proof_of_completed_local_retirement(self):
        directory = Path(self.repo.config["backtracking"])
        directory.mkdir()
        evidence = directory / "old-local-deletion.txt"
        evidence.write_text(
            f"Current host: 'fixture'\nCurrent working directory: '{self.primary}'\n"
            f"Repository: '{self.primary.name}'\n"
            f'Archiving `git show --stat --summary` output before `git branch -D "{self.branch}"`\n\n'
            f"commit {self.tip}\nAuthor: Fixture <fixture@example.invalid>\n"
        )
        record = self.apply()
        link = record["local_history"][0]
        self.assertEqual(link["evidence_kind"], "legacy-predeletion-log")
        self.assertEqual(link["path"], str(evidence))
        self.assertEqual(link["local_branch"], self.branch)
        self.assertEqual(link["local_oid"], self.tip)
        self.assertNotIn("record_id", link)
        self.assert_recovery(record)

    def test_same_sha_from_another_branch_or_repository_does_not_crosslink(self):
        directory = Path(self.repo.config["backtracking"])
        directory.mkdir()
        for name, branch, repository in (
            ("different-name", "rwgk/unrelated", "Acme/project"),
            ("different-repository", self.branch, "Other/project"),
        ):
            record = {
                "id": name,
                "kind": "retire",
                "status": "complete",
                "repository": {
                    "upstream_repo": repository,
                    "common": "/another/repository",
                    "key": "other",
                },
                "branch": branch,
                "local_oid": self.tip,
                "snapshots": [],
            }
            (directory / (name + ".json")).write_text(json.dumps(record))
        self.assertEqual(self.retire()["local_history"], [])

    def test_fixed_lease_catches_update_after_last_preflight(self):
        newer = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            self.tip,
            "-m",
            "Concurrent remote work",
        )
        original = lifecycle._push

        def concurrent_push(*args, **kwargs):
            if any(update["new"] is None for update in kwargs["updates"]):
                self.create_remote(oid=newer)
            return original(*args, **kwargs)

        with (
            mock.patch.object(lifecycle, "_push", side_effect=concurrent_push),
            self.assertRaises(WTError),
        ):
            self.apply()
        self.assertEqual(self.remote_tip(), newer)
        record = self.records()[0]
        self.assertEqual(record["source"]["oid"], self.tip)
        with self.assertRaises(WTError):
            remote_retire.resume(self.repo, record["id"])
        self.assertEqual(self.remote_tip(), newer)

    def test_resume_reuses_archive_after_interruption(self):
        original = lifecycle._push

        def interrupt_after_archive(*args, **kwargs):
            result = original(*args, **kwargs)
            if any(
                update["ref"].startswith("refs/tags/") for update in kwargs["updates"]
            ):
                raise RuntimeError("interrupted after archive push")
            return result

        with (
            mock.patch.object(lifecycle, "_push", side_effect=interrupt_after_archive),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        record = self.records()[0]
        resumed = lifecycle.resume(self.repo, record["id"])
        self.assertEqual(resumed["status"], "complete")
        self.assertIsNone(self.remote_tip())
        self.assertEqual(
            len(
                git(
                    self.remotes["origin"],
                    "for-each-ref",
                    "--format=%(refname)",
                    "refs/tags/",
                ).splitlines()
            ),
            1,
        )

    def test_resume_reports_absent_when_deletion_preceded_journal_update(self):
        original = lifecycle._push

        def interrupt_after_delete(*args, **kwargs):
            result = original(*args, **kwargs)
            if any(update["new"] is None for update in kwargs["updates"]):
                raise RuntimeError("interrupted after source deletion")
            return result

        with (
            mock.patch.object(lifecycle, "_push", side_effect=interrupt_after_delete),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        self.assertIsNone(self.remote_tip())
        record = self.records()[0]
        resumed = remote_retire.resume(self.repo, record["id"])
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["remote_actions"][-1]["action"], "branch-absent")
        self.assertIsNone(resumed["remote_actions"][-1]["observed_oid"])
        self.assert_recovery(resumed)

    def test_resume_never_follows_a_changed_remote_endpoint(self):
        with (
            mock.patch.object(
                lifecycle,
                "_push",
                side_effect=RuntimeError("interrupted before archive"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        record = self.records()[0]
        self.push_identities["upstream"] = "git@github.com:Acme/other-project.git"
        with self.assertRaises(WTError):
            remote_retire.resume(self.repo, record["id"])
        self.assertEqual(self.remote_tip(), self.tip)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "Repo", return_value=self.repo),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = cli.main(list(args))
        return result, stdout.getvalue(), stderr.getvalue()

    def test_remote_cli_treats_numeric_and_pr_like_names_literally(self):
        for branch in ("1234", "pr42"):
            with self.subTest(branch=branch):
                self.create_remote(remote="origin", branch=branch)
                with mock.patch.object(
                    self.repo,
                    "resolve",
                    side_effect=AssertionError(
                        "must not use local selector resolution"
                    ),
                ):
                    status, output, error = self.invoke(
                        "drop", "--remote", "origin", branch, "--json"
                    )
                self.assertEqual(status, 0, error)
                self.assertEqual(
                    json.loads(output)["source"]["ref"], "refs/heads/" + branch
                )

    def test_remote_apply_rejects_print_path_before_mutation(self):
        with mock.patch.object(remote_retire, "retire") as retire:
            status, _, _ = self.invoke(
                "drop",
                "--remote",
                "upstream",
                self.branch,
                "--expect",
                self.tip,
                "--apply",
                "--print-path",
            )
        self.assertNotEqual(status, 0)
        retire.assert_not_called()
        self.assertEqual(self.remote_tip(), self.tip)

    def test_remote_resume_uses_its_record_without_local_mode_or_overrides(self):
        with (
            mock.patch.object(lifecycle, "_push", side_effect=RuntimeError("stop")),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        record = self.records()[0]
        status, _, _ = self.invoke("clean", "--resume", record["id"], "--apply")
        self.assertNotEqual(status, 0)
        self.assertEqual(self.remote_tip(), self.tip)
        status, output, error = self.invoke(
            "drop", "--resume", record["id"], "--apply", "--json"
        )
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["status"], "complete")
        self.assertIsNone(self.remote_tip())


if __name__ == "__main__":
    unittest.main()
