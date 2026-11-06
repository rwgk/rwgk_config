"""Public CUDA Python publication requires an unsigned commit in each PR."""

import copy
import json
import os
import subprocess
import unittest
from unittest import mock

import test_wt_github as fixtures
from rwgk_wt import github, pr_push, signing
from rwgk_wt.core import WTError

git = fixtures.git


class UnsignedPublicationTests(unittest.TestCase):
    remove_external_trees = fixtures.GitHubTests.remove_external_trees
    response = fixtures.GitHubTests.response
    branch = fixtures.GitHubTests.branch
    publish = fixtures.GitHubTests.publish
    pushes = fixtures.GitHubTests.pushes
    native_stack = fixtures.GitHubTests.native_stack
    publish_stack = fixtures.GitHubTests.publish_stack
    assert_no_publication_writes = fixtures.GitHubTests.assert_no_publication_writes

    def setUp(self):
        # Fixture setup must never borrow Ralf's configured signing key.
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
        self.target = "NVIDIA/cuda-python"
        self.fork = "rwgk/cuda-python"
        self.configure_target(self.target)
        self.signing_key = self.root / "fixture-signing-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(self.signing_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def configure_target(self, target):
        self.target = target
        self.identities.update(
            upstream=f"https://github.com/{target}.git",
            upstream_ssh=f"git@github.com:{target}.git",
            origin=f"git@github.com:{self.fork}.git",
        )
        self.remotes["upstream_ssh"] = self.remotes["upstream"]
        self.repo.config.update(upstream_repo=target, publish_remote="upstream")
        self.repo.save()

    def translate_fixture(self, value):
        if isinstance(value, str):
            if value == "ralf":
                return "rwgk"
            return value.replace("Acme/project", self.target).replace(
                "ralf/project", self.fork
            )
        if isinstance(value, list):
            return [self.translate_fixture(item) for item in value]
        if isinstance(value, dict):
            return {key: self.translate_fixture(item) for key, item in value.items()}
        return value

    def gh(self, argv, input):
        translated = [
            value.replace(self.target, "Acme/project").replace(
                self.fork, "ralf/project"
            )
            for value in argv
        ]
        result = fixtures.GitHubTests.gh(self, translated, input)
        if argv[1:3] == ["stack", "--version"]:
            return result
        return subprocess.CompletedProcess(
            argv,
            result.returncode,
            json.dumps(self.translate_fixture(json.loads(result.stdout))),
            result.stderr,
        )

    def raw_pr(self, *args, **kwargs):
        value = self.translate_fixture(
            fixtures.GitHubTests.raw_pr(self, *args, **kwargs)
        )
        base = value["base"]["ref"]
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(self.remotes["upstream"]),
                "rev-parse",
                "--verify",
                "refs/heads/" + base,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        value["base"]["sha"] = (
            result.stdout.strip() if result.returncode == 0 else self.repo.oid(base)
        )
        return value

    def refresh_pr(self, value):
        value = copy.deepcopy(value)
        identity = (value["head"].get("repo") or {}).get("full_name")
        for remote, endpoint in self.identities.items():
            if github._identity(endpoint).lower() != (identity or "").lower():
                continue
            value["head"]["sha"] = git(
                self.remotes[remote], "rev-parse", "refs/heads/" + value["head"]["ref"]
            )
            break
        return value

    def sign(self, repo):
        git(
            repo.root,
            "-c",
            "commit.gpgsign=true",
            "-c",
            "gpg.format=ssh",
            "-c",
            f"user.signingkey={self.signing_key}",
            "commit",
            "--amend",
            "--no-edit",
        )
        self.assertIn("\ngpgsig ", repo.git("cat-file", "commit", "HEAD"))
        return repo.oid("HEAD")

    def commit(self, repo, name, *, signed):
        (repo.root / name).write_text(name + "\n")
        git(repo.root, "add", name)
        git(repo.root, "commit", "--no-gpg-sign", "-m", "Add " + name)
        return self.sign(repo) if signed else repo.oid("HEAD")

    def pair(self, *, author="rwgk", base="main"):
        repo = self.branch(base=base)
        oid = self.sign(repo)
        branch = repo.current_branch()
        git(self.primary, "push", "upstream", f"{oid}:refs/heads/{branch}")
        self.prs[42] = self.raw_pr(
            42, author=author, head_repo=self.target, branch=branch, oid=oid, base=base
        )
        pr = github.get_pr(repo, 42)
        github._associate(repo, branch, pr, "owned" if author == "rwgk" else "review")
        self.commands.clear()
        self.gh_calls.clear()
        return repo

    def test_signed_push_is_rejected_without_counting_unsigned_trunk(self):
        repo = self.pair()
        self.commit(repo, "signed-contribution", signed=True)
        before = repo.oid("HEAD")
        with self.assertRaises(WTError) as caught:
            pr_push.push(repo)
        message = str(caught.exception)
        self.assertIn("42", message)
        self.assertIn(repo.current_branch(), message)
        self.assertIn("--allow-empty", message)
        self.assertIn("--only", message)
        self.assertEqual(repo.oid("HEAD"), before)
        self.assert_no_publication_writes()

    def test_unsigned_substantive_commit_allows_push_without_dummy_marker(self):
        repo = self.pair()
        oid = self.commit(repo, "unsigned-contribution", signed=False)
        before = repo.git("rev-list", "--count", "main..HEAD")
        result = pr_push.push(repo)
        self.assertEqual(result["result"], "pushed")
        self.assertEqual(git(self.remotes["upstream"], "rev-parse", "rwgk/change"), oid)
        self.assertEqual(repo.git("rev-list", "--count", "main..HEAD"), before)

    def test_push_excludes_unsigned_commits_in_actual_pr_base_branch(self):
        parent = self.branch("rwgk/parent")
        git(self.primary, "push", "upstream", "rwgk/parent")
        repo = self.pair(base="rwgk/parent")
        self.commit(repo, "signed-contribution", signed=True)
        with self.assertRaisesRegex(WTError, "unsigned commit"):
            pr_push.push(repo)
        self.assert_no_publication_writes()
        self.assertEqual(repo.oid("rwgk/parent"), parent.oid("HEAD"))

    def test_push_fetches_missing_base_from_canonical_target(self):
        repo = self.pair()
        self.commit(repo, "unsigned-contribution", signed=False)
        previous = repo.oid("main")
        new_base = git(
            self.remotes["upstream"],
            "-c",
            "commit.gpgsign=false",
            "commit-tree",
            f"{previous}^{{tree}}",
            "-p",
            previous,
            "-m",
            "Advance remote base",
        )
        git(self.remotes["upstream"], "update-ref", "refs/heads/main", new_base)
        self.prs[42]["base"]["sha"] = new_base
        self.assertNotEqual(
            repo.run(["git", "cat-file", "-e", new_base], check=False).returncode, 0
        )
        pr_push.push(repo)
        self.assertTrue(
            any(
                "fetch" in command
                and self.identities["upstream"] in command
                and any("refs/heads/main" in part for part in command)
                for command in self.commands
            )
        )

    def test_push_excludes_upstream_history_despite_stale_api_and_local_base(self):
        repo = self.pair()
        stale_base = self.prs[42]["base"]["sha"]
        merged = self.commit(repo, "unsigned-now-upstream", signed=False)
        git(self.primary, "push", "upstream", f"{merged}:refs/heads/main")
        self.commit(repo, "signed-follow-up", signed=True)
        self.assertEqual(repo.oid("main"), stale_base)
        self.assertEqual(self.prs[42]["base"]["sha"], stale_base)
        with self.assertRaisesRegex(WTError, "unsigned commit"):
            pr_push.push(repo)
        self.assert_no_publication_writes()

    def test_other_authors_signed_pr_is_not_subject_to_ralf_policy(self):
        repo = self.pair(author="contributor")
        self.commit(repo, "signed-contribution", signed=True)
        self.assertEqual(pr_push.push(repo)["result"], "pushed")
        self.assertEqual(repo.branches[repo.current_branch()]["role"], "review")

    def test_private_signed_pr_is_not_subject_to_public_policy(self):
        self.configure_target("NVIDIA-dev/cuda-python-private")
        repo = self.pair()
        self.commit(repo, "signed-contribution", signed=True)
        self.assertEqual(pr_push.push(repo)["result"], "pushed")

    def test_policy_identity_scope_excludes_other_projects_and_gitlab(self):
        self.assertTrue(signing.required(self.repo, "nvidia/CUDA-PYTHON", "RWGK"))
        for target, author in (
            ("NVIDIA-dev/cuda-python-private", "rwgk"),
            ("pybind/pybind11", "rwgk"),
            ("someone/cuda-python", "rwgk"),
            ("NVIDIA/cuda-python", "contributor"),
        ):
            with self.subTest(target=target, author=author):
                self.assertFalse(signing.required(self.repo, target, author))
        self.repo.config["provider"] = "gitlab"
        self.assertFalse(signing.required(self.repo, "NVIDIA/cuda-python", "rwgk"))

    def test_signed_new_publication_is_rejected_before_creating_remote_refs(self):
        repo = self.branch()
        self.sign(repo)
        with (
            self.assertRaises(WTError),
            mock.patch("rwgk_wt.lifecycle.pin_snapshots") as archive,
        ):
            self.publish(repo)
        archive.assert_not_called()
        self.assert_no_publication_writes()

    def test_unsigned_new_publication_is_allowed(self):
        repo = self.branch()
        result = self.publish(repo)
        self.assertEqual(result["repo"], self.target)
        self.assertEqual(result["prs"][0]["author"], "rwgk")

    def test_publication_excludes_unsigned_work_already_in_remote_trunk(self):
        repo = self.branch()
        git(self.primary, "push", "upstream", f"{repo.oid('HEAD')}:refs/heads/main")
        self.commit(repo, "signed-follow-up", signed=True)
        with self.assertRaisesRegex(WTError, "unsigned commit"):
            self.publish(repo)
        self.assert_no_publication_writes()

    def test_bottom_layer_checks_remote_trunk_when_local_trunk_is_stale(self):
        first = self.branch("rwgk/first")
        git(self.primary, "push", "upstream", f"{first.oid('HEAD')}:refs/heads/main")
        self.commit(first, "signed-first", signed=True)
        second = self.branch("rwgk/second", "rwgk/first")
        selected = {
            "id": "9876",
            "number": 10,
            "trunk": {"branch": "main"},
            "branches": [
                {
                    "branch": "rwgk/first",
                    "head": first.oid("HEAD"),
                    "base": first.oid("main"),
                },
                {
                    "branch": "rwgk/second",
                    "head": second.oid("HEAD"),
                    "base": second.oid("rwgk/first"),
                },
            ],
        }
        with self.assertRaisesRegex(WTError, "rwgk/first.*unsigned commit"):
            self.publish_stack(second, selected)
        self.assert_no_publication_writes()

    def test_unsigned_lower_layer_does_not_cover_signed_upper_layer(self):
        repo, selected = self.native_stack()
        self.sign(repo)
        selected["branches"][1]["head"] = repo.oid("HEAD")
        lower = selected["branches"][0]["branch"]
        repo.branches[lower]["pending_archive"] = [
            {"oid": repo.oid("main"), "label": "queued", "branch": lower}
        ]
        repo.save()
        with (
            self.assertRaises(WTError) as caught,
            mock.patch("rwgk_wt.lifecycle.pin_snapshots") as archive,
        ):
            self.publish_stack(repo, selected)
        self.assertIn("rwgk/second", str(caught.exception))
        archive.assert_not_called()
        self.assert_no_publication_writes()

    def test_each_unsigned_stack_layer_can_publish_atomically(self):
        repo, selected = self.native_stack()
        result = self.publish_stack(repo, selected)
        self.assertEqual(len(result["prs"]), 2)
        self.assertEqual(len(self.pushes()), 1)
        self.assertIn("--atomic", self.pushes()[0])


if __name__ == "__main__":
    unittest.main()
