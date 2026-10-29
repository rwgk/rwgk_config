"""Exercise PR and publication policy with real Git refs and local bare remotes."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli, github
from rwgk_wt.core import Repo, WTError


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


class FixtureRepo(Repo):
    """Real local Git; substitute the wire only, retaining logical identities."""

    def __init__(self, fixture, path=None):
        self.fixture = fixture
        super().__init__(path or fixture.primary, initialized=False)

    def run(self, argv, *, cwd=None, check=True, input=None):
        argv = list(map(str, argv))
        prefix = []
        if argv[:2] == ["env", "LC_ALL=C"]:
            self.fixture.locale_commands.append(list(argv))
            prefix, argv = argv[:2], argv[2:]
        if argv[0] == "gh":
            return self.fixture.gh(argv, input)
        # Git endpoints remain actual bare repos. Only identity observation is
        # supplied by this fixture; no command can reach the network.
        if argv[:3] == ["git", "remote", "get-url"]:
            remote = argv[-1]
            value = self.fixture.identities[remote]
            if "--push" in argv:
                value = self.fixture.push_identities.get(remote, value)
            return subprocess.CompletedProcess(argv, 0, value + "\n", "")
        original = list(argv)
        for index, value in enumerate(argv):
            for remote, identity in self.fixture.identities.items():
                if value == identity:
                    argv[index] = str(self.fixture.remotes[remote])
        self.fixture.commands.append(original)
        return super().run([*prefix, *argv], cwd=cwd, check=check, input=input)


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wt-github-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        backtracking_env = mock.patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.root / "backtracking"),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        backtracking_env.start()
        self.addCleanup(backtracking_env.stop)
        self.primary = self.root / "primary"
        self.primary.mkdir()
        git(self.primary, "init", "--initial-branch=main")
        git(self.primary, "config", "user.name", "Tests")
        git(self.primary, "config", "user.email", "tests@example.invalid")
        (self.primary / "base").write_text("base\n")
        git(self.primary, "add", "base")
        git(self.primary, "commit", "-m", "Base")
        self.remotes = {
            key: self.root / (key + ".git") for key in ("upstream", "origin")
        }
        for key, path in self.remotes.items():
            subprocess.run(
                ["git", "init", "--bare", str(path)], check=True, capture_output=True
            )
            git(self.primary, "remote", "add", key, str(path))
            git(self.primary, "push", key, "main")
        self.identities = {
            "upstream": "https://github.com/Acme/project.git",
            "origin": "git@github.com:ralf/project.git",
        }
        self.push_identities = {}
        self.commands = []
        self.locale_commands = []
        self.gh_calls = []
        self.prs = {}
        self.native_base = "main"
        self.native_identity = {"id": 9876, "number": 10}
        self.next_pr = 100
        self.repo = FixtureRepo(self)
        trees_root = Path(
            os.environ.get("WT_TEST_WORKTREE_ROOT", "/wrk/trees/.wt-tests")
        )
        self.repo.config.update(
            key="project-" + self.root.name,
            primary=str(self.primary),
            trunk="main",
            upstream_remote="upstream",
            upstream_repo="Acme/project",
            publish_remote="origin",
            archive_remote="origin",
            trees_root=str(trees_root),
            backtracking=str(self.root / "backtracking"),
        )
        self.repo.branches["main"] = {"path": str(self.primary), "role": "trunk"}
        self.repo.save()
        self.addCleanup(self.remove_external_trees)

    def remove_external_trees(self):
        import shutil

        directory = Path(self.repo.config["trees_root"]) / self.repo.config["key"]
        if directory.exists():
            shutil.rmtree(directory)

    def response(self, value):
        return subprocess.CompletedProcess(["gh"], 0, json.dumps(value), "")

    def raw_pr(
        self,
        number,
        *,
        author="ralf",
        head_repo="ralf/project",
        branch="rwgk/change",
        oid=None,
        base="main",
        draft=False,
        state="open",
        merged=False,
    ):
        return {
            "number": number,
            "html_url": f"https://github.com/Acme/project/pull/{number}",
            "user": {"login": author},
            "state": state,
            "draft": draft,
            "merged_at": "2026-10-25T12:00:00Z" if merged else None,
            "head": {
                "repo": {"full_name": head_repo} if head_repo else None,
                "ref": branch,
                "sha": oid or self.repo.oid("main"),
            },
            "base": {"repo": {"full_name": "Acme/project"}, "ref": base},
            "merge_commit_sha": None,
        }

    def refresh_pr(self, value):
        value = copy.deepcopy(value)
        identity = (value["head"].get("repo") or {}).get("full_name")
        remote = {"Acme/project": "upstream", "ralf/project": "origin"}.get(identity)
        if remote:
            result = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(self.remotes[remote]),
                    "rev-parse",
                    "--verify",
                    "refs/heads/" + value["head"]["ref"],
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode == 0:
                value["head"]["sha"] = result.stdout.strip()
        return value

    def gh(self, argv, input):
        self.gh_calls.append((argv, json.loads(input) if input else None))
        if argv[1:3] == ["stack", "--version"]:
            return subprocess.CompletedProcess(argv, 0, "gh stack version 0.3.0\n", "")
        if argv[1:3] == ["stack", "link"]:
            return self.response({})
        if argv[1:3] == ["pr", "ready"]:
            number = int(argv[3].rsplit("/", 1)[1])
            self.prs[number]["draft"] = False
            return self.response({})
        self.assertEqual(
            argv[1:6], ["api", "--hostname", "github.com", "--method", argv[5]]
        )
        method, endpoint = argv[5:7]
        if endpoint == "user":
            return self.response({"login": "ralf"})
        if endpoint == "repos/ralf/project":
            return self.response(
                {
                    "full_name": "ralf/project",
                    "fork": True,
                    "owner": {"login": "ralf"},
                    "parent": {"full_name": "Acme/project"},
                }
            )
        path = urlsplit(endpoint)
        if path.path == "repos/Acme/project/pulls" and method == "GET":
            head = parse_qs(path.query)["head"][0]
            owner, branch = head.split(":", 1)
            return self.response(
                [
                    self.refresh_pr(pr)
                    for pr in self.prs.values()
                    if pr["state"] == "open"
                    and pr["head"]["ref"] == branch
                    and (pr["head"].get("repo") or {})
                    .get("full_name", "")
                    .split("/")[0]
                    == owner
                ]
            )
        if endpoint == "repos/Acme/project/pulls" and method == "POST":
            payload = json.loads(input)
            head = payload["head"]
            identity = "Acme/project"
            if ":" in head:
                owner, head = head.split(":", 1)
                identity = owner + "/project"
            self.next_pr += 1
            pr = self.raw_pr(
                self.next_pr,
                head_repo=identity,
                branch=head,
                base=payload["base"],
                draft=payload["draft"],
            )
            self.prs[pr["number"]] = pr
            return self.response(self.refresh_pr(pr))
        if endpoint.startswith("repos/Acme/project/pulls/"):
            number = int(endpoint.rsplit("/", 1)[1])
            if method == "PATCH":
                payload = json.loads(input)
                if "base" in payload:
                    self.prs[number]["base"]["ref"] = payload["base"]
            return self.response(self.refresh_pr(self.prs[number]))
        if endpoint == "repos/Acme/project/stacks/10" or endpoint.startswith(
            "repos/Acme/project/stacks?pull_request="
        ):
            native = {
                **self.native_identity,
                "open": True,
                "base": {"ref": self.native_base},
                "pull_requests": [self.refresh_pr(pr) for pr in self.prs.values()],
            }
            return self.response([native] if "?" in endpoint else native)
        self.fail(f"Unexpected GitHub operation: {argv}")

    def branch(self, name="rwgk/change", base="main"):
        path = self.repo.create(name, base)
        (path / name.rsplit("/", 1)[-1]).write_text(name + "\n")
        git(path, "add", ".")
        git(path, "commit", "-m", "Change " + name)
        return FixtureRepo(self, path)

    def publish(self, repo=None, **options):
        with mock.patch("rwgk_wt.stacks.get_stack", return_value=None):
            return github.publish(repo or self.repo, **options)

    def pushes(self):
        return [
            command
            for command in self.commands
            if command[0] == "git" and "push" in command
        ]

    def invoke_pull(self, path, flags=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        repo = FixtureRepo(self, path)
        with (
            mock.patch.object(cli, "Repo", return_value=repo),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(["pull", *flags])
        return status, stdout.getvalue(), stderr.getvalue()

    def review_pair(self):
        oid = self.repo.oid("main")
        self.prs[42] = self.raw_pr(
            42, author="contributor", head_repo=None, branch="topic", oid=oid
        )
        git(self.remotes["upstream"], "update-ref", "refs/pull/42/head", oid)
        return github.open_pr(self.repo, 42)

    def test_pull_updates_current_pr_from_subdirectory(self):
        path = self.review_pair()
        nested = path / "nested"
        nested.mkdir()
        previous = git(path, "rev-parse", "HEAD")
        oid = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            previous,
            "-m",
            "New PR commit",
        )
        self.prs[42]["head"]["sha"] = oid
        git(self.primary, "push", "upstream", oid + ":refs/pull/42/head")
        status, stdout, stderr = self.invoke_pull(nested)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Updated from PR #42: rwgk/review-pr42", stdout)
        self.assertIn(f"HEAD {oid}", stdout)
        self.assertEqual(git(path, "rev-parse", "HEAD"), oid)
        self.assertEqual(git(self.primary, "symbolic-ref", "--short", "HEAD"), "main")
        status, stdout, stderr = self.invoke_pull(path)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Already up to date with PR #42", stdout)
        self.assertFalse(self.pushes())

    def test_pull_reports_local_commits_without_discarding_them(self):
        path = self.review_pair()
        (path / "valuable").write_text("local contribution\n")
        git(path, "add", "valuable")
        git(path, "commit", "-m", "Local review work")
        local = git(path, "rev-parse", "HEAD")
        status, stdout, stderr = self.invoke_pull(path)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Kept local commits ahead of PR #42", stdout)
        self.assertIn(f"HEAD {local}", stdout)
        self.assertEqual((path / "valuable").read_text(), "local contribution\n")

    def test_pull_machine_output_retains_pr_and_commit_identity(self):
        path = self.review_pair()
        oid = git(path, "rev-parse", "HEAD")
        status, stdout, stderr = self.invoke_pull(path, flags=["--json"])
        self.assertEqual((status, stderr), (0, ""))
        value = json.loads(stdout)
        self.assertEqual(value["branch"], "rwgk/review-pr42")
        self.assertEqual(value["pr"]["number"], 42)
        self.assertEqual(value["pr"]["head_oid"], oid)
        self.assertEqual((value["previous_oid"], value["oid"]), (oid, oid))
        status, stdout, stderr = self.invoke_pull(path, flags=["--print-path"])
        self.assertEqual((status, stderr, stdout), (0, "", str(path) + "\n"))

    def test_pull_requires_pr_and_directs_trunk_to_its_update_command(self):
        status, _, stderr = self.invoke_pull(self.primary)
        self.assertEqual(status, 1)
        self.assertIn("wt trunk update", stderr)
        unpublished = self.branch()
        status, _, stderr = self.invoke_pull(unpublished.root)
        self.assertEqual(status, 1)
        self.assertIn("no associated PR", stderr)
        self.assertIn("wt pr NUMBER", stderr)
        self.assertFalse(self.gh_calls)

    def test_pull_does_not_refresh_one_stack_layer(self):
        path = self.review_pair()
        (self.repo.common / "gh-stack").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "stacks": [
                        {
                            "trunk": {"branch": "main"},
                            "branches": [{"branch": "rwgk/review-pr42"}],
                        }
                    ],
                }
            )
        )
        before = len(self.gh_calls)
        status, _, stderr = self.invoke_pull(path)
        self.assertEqual(status, 1)
        self.assertIn("wt stack pull", stderr)
        self.assertEqual(len(self.gh_calls), before)

    def test_pull_rejects_wrong_pr_repository_or_registration_before_fetch(self):
        path = self.review_pair()
        saved = copy.deepcopy(self.repo.branches["rwgk/review-pr42"])
        for problem in ("foreign-pr", "wrong-owner"):
            with self.subTest(problem=problem):
                metadata = copy.deepcopy(saved)
                if problem == "foreign-pr":
                    metadata["pr"]["base_repo"] = "Other/project"
                else:
                    metadata["path"] = str(self.primary)
                self.repo.branches["rwgk/review-pr42"] = metadata
                self.repo.save()
                before = len(self.gh_calls)
                status, _, stderr = self.invoke_pull(path)
                self.assertEqual(status, 1)
                self.assertIn("wt doctor", stderr)
                self.assertEqual(len(self.gh_calls), before)

    def test_pull_rejects_unmanaged_or_detached_worktree(self):
        path = self.branch().root
        del self.repo.branches["rwgk/change"]
        self.repo.save()
        status, _, stderr = self.invoke_pull(path)
        self.assertEqual(status, 1)
        self.assertIn("unmanaged", stderr)
        git(path, "checkout", "--detach", "HEAD")
        status, _, stderr = self.invoke_pull(path)
        self.assertEqual(status, 1)
        self.assertIn("Detached HEAD", stderr)
        self.assertFalse(self.gh_calls)

    def test_new_publication_uses_fixed_empty_lease_and_tracks_destination(self):
        repo = self.branch()
        result = self.publish(repo)
        oid = repo.oid("HEAD")
        push = self.pushes()[-1]
        self.assertIn("--atomic", push)
        self.assertIn("--force-with-lease=refs/heads/rwgk/change:", push)
        self.assertIn(oid + ":refs/heads/rwgk/change", push)
        self.assertEqual(
            git(self.remotes["origin"], "rev-parse", "refs/heads/rwgk/change"), oid
        )
        self.assertEqual(repo.git("config", "branch.rwgk/change.remote"), "origin")
        self.assertEqual(repo.branches["rwgk/change"]["publication"]["oid"], oid)
        self.assertEqual(result["repo"], "ralf/project")
        self.assertTrue(result["prs"][0]["is_draft"])
        self.assertEqual(
            repo.git("symbolic-ref", "--short", "HEAD", cwd=self.primary), "main"
        )

    def test_existing_tracking_and_ready_status_are_preserved(self):
        repo = self.branch()
        repo.git("config", "branch.rwgk/change.remote", "upstream")
        repo.git("config", "branch.rwgk/change.merge", "refs/heads/rwgk/something-else")
        self.publish(repo, ready=True)
        before = len(self.gh_calls)
        self.publish(repo)
        self.assertEqual(repo.git("config", "branch.rwgk/change.remote"), "upstream")
        self.assertEqual(
            repo.git("config", "branch.rwgk/change.merge"),
            "refs/heads/rwgk/something-else",
        )
        self.assertFalse(repo.branches["rwgk/change"]["pr"]["is_draft"])
        self.assertFalse(
            any(call[0][1:3] == ["pr", "ready"] for call in self.gh_calls[before:])
        )

    def test_distinct_push_endpoint_is_refused_before_remote_mutation(self):
        repo = self.branch()
        self.push_identities["origin"] = "https://github.com/someone-else/project.git"
        with self.assertRaisesRegex(WTError, "different fetch and push"):
            self.publish(repo)
        self.assertFalse(self.pushes())

    def test_recorded_remote_update_is_not_silently_adopted(self):
        repo = self.branch()
        self.publish(repo)
        previous_pushes = len(self.pushes())
        git(
            self.remotes["origin"],
            "update-ref",
            "refs/heads/rwgk/change",
            repo.oid("main"),
        )
        with self.assertRaisesRegex(WTError, "changed since the recorded publication"):
            self.publish(repo)
        self.assertEqual(len(self.pushes()), previous_pushes)

    def rewrite(self, repo):
        git(repo.root, "commit", "--amend", "-m", "Rewritten change")
        return repo.oid("HEAD")

    def test_rewrite_pins_old_head_before_exact_lease_push(self):
        repo = self.branch()
        self.publish(repo)
        old = repo.oid("HEAD")
        new = self.rewrite(repo)
        with mock.patch("rwgk_wt.lifecycle.pin_snapshots") as pin:

            def check_before_push(*args, **kwargs):
                self.assertEqual(
                    git(self.remotes["origin"], "rev-parse", "refs/heads/rwgk/change"),
                    old,
                )

            pin.side_effect = check_before_push
            self.publish(repo)
        self.assertEqual(pin.call_args.args[1][0]["oid"], old)
        self.assertIn(
            "--force-with-lease=refs/heads/rwgk/change:" + old, self.pushes()[-1]
        )
        self.assertNotIn("push.pushOption=pull.stacked", self.pushes()[-1])
        self.assertEqual(
            git(self.remotes["origin"], "rev-parse", "refs/heads/rwgk/change"), new
        )

    def test_rewrite_without_upstream_requires_explicit_tracking_choice(self):
        repo = self.branch()
        self.publish(repo)
        repo.git("config", "--remove-section", "branch.rwgk/change")
        self.rewrite(repo)
        before = len(self.pushes())
        with self.assertRaisesRegex(WTError, "--set-upstream"):
            self.publish(repo)
        self.assertEqual(before, len(self.pushes()))
        with mock.patch("rwgk_wt.lifecycle.pin_snapshots"):
            self.publish(repo, set_upstream=True)
        self.assertEqual(repo.git("config", "branch.rwgk/change.remote"), "origin")

    def test_unrecorded_divergence_refuses_force_push(self):
        repo = self.branch()
        self.publish(repo)
        del repo.branches["rwgk/change"]["publication"]
        self.rewrite(repo)
        before = len(self.pushes())
        with self.assertRaisesRegex(WTError, "previously recorded publication"):
            self.publish(repo)
        self.assertEqual(before, len(self.pushes()))

    def test_open_deleted_contributor_fork_via_retained_pr_ref(self):
        number = 42
        oid = self.repo.oid("main")
        self.prs[number] = self.raw_pr(
            number,
            author="contributor",
            head_repo=None,
            branch="topic",
            oid=oid,
            state="closed",
        )
        git(self.remotes["upstream"], "update-ref", f"refs/pull/{number}/head", oid)
        git(self.primary, "remote", "remove", "origin")
        path = github.open_pr(self.repo, str(number))
        metadata = self.repo.branches["rwgk/review-pr42"]
        self.assertEqual(metadata["role"], "review")
        self.assertIsNone(metadata["pr"]["head_repo"])
        self.assertEqual(git(path, "rev-parse", "HEAD"), oid)
        self.assertEqual((path.parent / "pr42").resolve(), path.resolve())
        self.assertNotIn("publication", metadata)
        (path / "local-edit").write_text("keep me\n")
        self.assertEqual(github.open_pr(self.repo, "pr42"), path)
        with self.assertRaisesRegex(WTError, "uncommitted or untracked"):
            github.open_pr(self.repo, "42", refresh=True)
        self.assertEqual((path / "local-edit").read_text(), "keep me\n")

    def test_own_pr_reuses_managed_branch_and_stable_path(self):
        repo = self.branch()
        self.publish(repo)
        expected = repo.root
        self.repo = FixtureRepo(self)
        self.assertEqual(github.open_pr(self.repo, "101"), expected)
        self.assertEqual(len(self.repo.worktrees()), 2)

    def test_review_publication_is_rejected(self):
        repo = self.branch()
        repo.branches["rwgk/change"]["role"] = "review"
        with self.assertRaisesRegex(WTError, "managed owned branch"):
            self.publish(repo)
        self.assertFalse(self.pushes())

    def test_stack_always_uses_upstream_and_links_pr_urls(self):
        first = self.branch("rwgk/first")
        second = self.branch("rwgk/second", "rwgk/first")
        stack = {
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
        with (
            mock.patch("rwgk_wt.stacks.get_stack", return_value=stack),
            mock.patch("rwgk_wt.stacks.refresh"),
            mock.patch("rwgk_wt.stacks.save_native_identity", create=True),
        ):
            result = github.publish(second, stack=True)
        self.assertEqual(result["repo"], "Acme/project")
        self.assertEqual(self.pushes()[-1][-3], self.identities["upstream"])
        for branch in ("rwgk/first", "rwgk/second"):
            self.assertEqual(
                git(self.remotes["upstream"], "rev-parse", "refs/heads/" + branch),
                second.oid(branch),
            )
            missing = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(self.remotes["origin"]),
                    "show-ref",
                    "--verify",
                    "refs/heads/" + branch,
                ],
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
        link = next(argv for argv, _ in self.gh_calls if argv[1:3] == ["stack", "link"])
        self.assertNotIn("--open", link)
        self.assertTrue(
            all(
                value.startswith("https://github.com/Acme/project/pull/")
                for value in link[7:]
            )
        )

    def native_stack(self):
        first = self.branch("rwgk/first")
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
        return second, selected

    def publish_stack(self, repo, selected):
        with (
            mock.patch("rwgk_wt.stacks.get_stack", return_value=selected),
            mock.patch("rwgk_wt.stacks.refresh"),
            mock.patch("rwgk_wt.stacks.save_native_identity"),
        ):
            return github.publish(repo, stack=True)

    def mark_first_layer_merged(self, repo, selected):
        first = selected["branches"][0]
        git(self.primary, "merge", "--ff-only", first["branch"])
        pr = next(
            pr for pr in self.prs.values() if pr["head"]["ref"] == first["branch"]
        )
        pr["state"], pr["merged_at"] = "closed", "2026-10-29T12:00:00Z"
        first["pullRequest"] = {"number": pr["number"], "merged": True}
        selected["branches"][1]["base"] = repo.oid("main")

    def assert_no_publication_writes(self):
        self.assertFalse(self.pushes())
        self.assertFalse(
            any(
                method != "GET"
                for method in (
                    argv[5] for argv, _ in self.gh_calls if argv[1:2] == ["api"]
                )
            )
        )
        self.assertFalse(
            any(argv[1:3] == ["stack", "link"] for argv, _ in self.gh_calls)
        )

    def test_retargeted_stack_refuses_before_preservation_push_or_pr_updates(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        self.native_base = "release"
        self.commands.clear()
        self.gh_calls.clear()
        with (
            mock.patch("rwgk_wt.lifecycle.pin_snapshots") as pin,
            self.assertRaisesRegex(
                WTError, "retargeted this stack to release.*wt stack rebase"
            ),
        ):
            self.publish_stack(repo, selected)
        pin.assert_not_called()
        self.assert_no_publication_writes()

    def test_retargeted_stack_with_one_active_pr_refuses_before_writes(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        self.mark_first_layer_merged(repo, selected)
        self.native_base = "release"
        self.commands.clear()
        self.gh_calls.clear()
        with self.assertRaisesRegex(WTError, "retargeted this stack to release"):
            self.publish_stack(repo, selected)
        self.assert_no_publication_writes()

    def test_partially_merged_stack_can_publish_its_remaining_layer(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        self.mark_first_layer_merged(repo, selected)
        self.commands.clear()
        result = self.publish_stack(repo, selected)
        self.assertEqual(result["branches"], ["rwgk/second"])
        self.assertEqual(len(self.pushes()), 1)
        self.assertIn(
            "repos/Acme/project/stacks/10", [argv[-1] for argv, _ in self.gh_calls]
        )

    def test_old_extension_refuses_stack_publication_before_api_or_preservation(self):
        repo, selected = self.native_stack()
        original = self.gh

        def older_extension(argv, input):
            if argv[1:3] == ["stack", "--version"]:
                self.gh_calls.append((argv, None))
                return subprocess.CompletedProcess(
                    argv, 0, "gh stack version 0.2.1\n", ""
                )
            return original(argv, input)

        with (
            mock.patch.object(self, "gh", side_effect=older_extension),
            mock.patch("rwgk_wt.lifecycle.pin_snapshots") as pin,
            self.assertRaisesRegex(WTError, "gh extension upgrade stack"),
        ):
            self.publish_stack(repo, selected)
        pin.assert_not_called()
        self.assert_no_publication_writes()
        self.assertFalse(any(argv[1:2] == ["api"] for argv, _ in self.gh_calls))

    def test_native_stack_preflight_failure_prevents_publication_writes(self):
        repo, selected = self.native_stack()
        original = self.gh

        def unavailable(argv, input):
            if argv[-1] == "repos/Acme/project/stacks/10":
                raise WTError("GitHub API unavailable")
            return original(argv, input)

        with (
            mock.patch.object(self, "gh", side_effect=unavailable),
            mock.patch("rwgk_wt.lifecycle.pin_snapshots") as pin,
            self.assertRaisesRegex(WTError, "GitHub API unavailable"),
        ):
            self.publish_stack(repo, selected)
        pin.assert_not_called()
        self.assert_no_publication_writes()

    def test_native_stack_identity_mismatch_prevents_publication(self):
        repo, selected = self.native_stack()
        self.native_identity["id"] = 8888
        with self.assertRaisesRegex(WTError, "identity|Identity"):
            self.publish_stack(repo, selected)
        self.assert_no_publication_writes()

    def test_unpublished_local_stack_can_publish_without_native_identity(self):
        repo, selected = self.native_stack()
        selected.pop("id")
        selected.pop("number")
        self.publish_stack(repo, selected)
        self.assertEqual(len(self.pushes()), 1)
        self.assertFalse(
            any(argv[-1] == "repos/Acme/project/stacks/10" for argv, _ in self.gh_calls)
        )
        self.assertNotIn("push.pushOption=pull.stacked", self.pushes()[0])

    def rewrite_stack_top(self, repo, selected):
        old = repo.oid("HEAD")
        new = self.rewrite(repo)
        selected["branches"][-1]["head"] = new
        return old, new

    def test_native_stack_rewrite_sends_review_retention_option(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        old, new = self.rewrite_stack_top(repo, selected)
        git(self.remotes["upstream"], "config", "receive.advertisePushOptions", "true")
        self.commands.clear()
        with mock.patch("rwgk_wt.lifecycle.pin_snapshots"):
            self.publish_stack(repo, selected)
        pushes = self.pushes()
        self.assertEqual(len(pushes), 1)
        self.assertIn("push.pushOption=pull.stacked", pushes[0])
        self.assertEqual(self.locale_commands[-1][:2], ["env", "LC_ALL=C"])
        self.assertIn("--atomic", pushes[0])
        self.assertIn("--force-with-lease=refs/heads/rwgk/second:" + old, pushes[0])
        self.assertIn(new + ":refs/heads/rwgk/second", pushes[0])
        self.assertEqual(git(self.remotes["upstream"], "rev-parse", "rwgk/second"), new)

    def test_native_stack_rewrite_preserves_configured_push_options(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        self.rewrite_stack_top(repo, selected)
        git(self.remotes["upstream"], "config", "receive.advertisePushOptions", "true")
        configured = ["existing.option=keep", "second.option=also-keep"]
        for option in configured:
            git(repo.root, "config", "--add", "push.pushOption", option)
        received = self.root / "received-push-options.json"
        hook = self.remotes["upstream"] / "hooks" / "pre-receive"
        hook.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "options = [os.environ[f'GIT_PUSH_OPTION_{i}'] "
            "for i in range(int(os.environ['GIT_PUSH_OPTION_COUNT']))]\n"
            f"Path({str(received)!r}).write_text(json.dumps(options))\n"
        )
        hook.chmod(0o755)
        with mock.patch("rwgk_wt.lifecycle.pin_snapshots"):
            self.publish_stack(repo, selected)
        self.assertEqual(
            json.loads(received.read_text()), [*configured, "pull.stacked"]
        )
        self.assertEqual(
            repo.git("config", "--get-all", "push.pushOption").splitlines(), configured
        )

    def test_unsupported_push_option_retries_identical_atomic_update_and_leases(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        self.rewrite_stack_top(repo, selected)
        git(self.remotes["upstream"], "config", "receive.advertisePushOptions", "false")
        self.commands.clear()
        with mock.patch("rwgk_wt.lifecycle.pin_snapshots"):
            self.publish_stack(repo, selected)
        pushes = self.pushes()
        self.assertEqual(len(pushes), 2)
        self.assertIn("push.pushOption=pull.stacked", pushes[0])
        without_signal = list(pushes[0])
        signal_index = without_signal.index("-c")
        self.assertEqual(
            without_signal[signal_index + 1], "push.pushOption=pull.stacked"
        )
        del without_signal[signal_index : signal_index + 2]
        self.assertEqual(without_signal, pushes[1])
        self.assertIn("--atomic", pushes[1])
        self.assertEqual(
            git(self.remotes["upstream"], "rev-parse", "rwgk/second"), repo.oid("HEAD")
        )

    def test_native_stack_lease_rejection_does_not_retry_without_push_option(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        old, new = self.rewrite_stack_top(repo, selected)
        git(self.remotes["upstream"], "config", "receive.advertisePushOptions", "true")
        original = repo.run

        def concurrent_writer(argv, **kwargs):
            wire = argv[2:] if argv[:2] == ["env", "LC_ALL=C"] else argv
            if wire[0] == "git" and "push" in wire:
                git(
                    self.remotes["upstream"],
                    "update-ref",
                    "refs/heads/rwgk/second",
                    repo.oid("main"),
                )
            return original(argv, **kwargs)

        self.commands.clear()
        with (
            mock.patch.object(repo, "run", side_effect=concurrent_writer),
            mock.patch("rwgk_wt.lifecycle.pin_snapshots"),
            self.assertRaisesRegex(WTError, "Fixed leases were not refreshed"),
        ):
            self.publish_stack(repo, selected)
        pushes = self.pushes()
        self.assertEqual(len(pushes), 1)
        self.assertIn("push.pushOption=pull.stacked", pushes[0])
        self.assertIn("--force-with-lease=refs/heads/rwgk/second:" + old, pushes[0])
        self.assertEqual(repo.branches["rwgk/second"]["publication"]["oid"], old)
        self.assertEqual(repo.oid("HEAD"), new)
        self.assertEqual(
            git(self.remotes["upstream"], "rev-parse", "rwgk/second"), repo.oid("main")
        )

    def test_native_stack_fast_forward_does_not_send_review_retention_option(self):
        repo, selected = self.native_stack()
        self.publish_stack(repo, selected)
        (repo.root / "next").write_text("next commit\n")
        git(repo.root, "add", "next")
        git(repo.root, "commit", "-m", "Next change")
        self.commands.clear()
        self.publish_stack(repo, selected)
        self.assertEqual(len(self.pushes()), 1)
        self.assertNotIn("push.pushOption=pull.stacked", self.pushes()[0])

    def test_empty_change_refuses_before_any_push(self):
        path = self.repo.create("rwgk/empty", "main")
        repo = FixtureRepo(self, path)
        with self.assertRaisesRegex(WTError, "no commits"):
            self.publish(repo)
        self.assertFalse(self.pushes())

    def test_failed_lease_keeps_observed_expectation_and_unexpected_remote_work(self):
        repo = self.branch()
        self.publish(repo)
        old = repo.oid("HEAD")
        new = self.rewrite(repo)
        tree = repo.oid("main")
        unexpected = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            tree,
            "-m",
            "Concurrent remote work",
        )
        git(
            self.primary,
            "push",
            str(self.remotes["origin"]),
            unexpected + ":refs/rwgk/test-concurrent",
        )
        original = repo.run

        def concurrent_writer(argv, **kwargs):
            if argv[0] == "git" and "push" in argv[:4]:
                git(
                    self.remotes["origin"],
                    "update-ref",
                    "refs/heads/rwgk/change",
                    unexpected,
                )
            return original(argv, **kwargs)

        with (
            mock.patch.object(repo, "run", side_effect=concurrent_writer),
            mock.patch("rwgk_wt.lifecycle.pin_snapshots"),
            self.assertRaisesRegex(WTError, "Fixed leases were not refreshed"),
        ):
            self.publish(repo)
        self.assertEqual(repo.branches["rwgk/change"]["publication"]["oid"], old)
        self.assertEqual(repo.oid("HEAD"), new)
        self.assertEqual(
            git(self.remotes["origin"], "rev-parse", "refs/heads/rwgk/change"),
            unexpected,
        )

    def test_unchanged_review_refresh_follows_rewrite_and_preserves_old_tip(self):
        source = self.branch("rwgk/source")
        old = source.oid("HEAD")
        number = 42
        self.prs[number] = self.raw_pr(
            number, author="contributor", head_repo=None, branch="topic", oid=old
        )
        git(self.primary, "push", "upstream", old + f":refs/pull/{number}/head")
        self.repo = FixtureRepo(self)
        path = github.open_pr(self.repo, number)
        new = self.rewrite(source)
        self.prs[number]["head"]["sha"] = new
        git(
            self.primary,
            "push",
            "--force",
            "upstream",
            new + f":refs/pull/{number}/head",
        )
        # Looking at the new API head must not destroy proof of the old checkout.
        github.open_pr(self.repo, number)
        self.assertEqual(
            self.repo.branches["rwgk/review-pr42"]["last_checked_out_head"], old
        )
        status, _, stderr = self.invoke_pull(path)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(git(path, "rev-parse", "HEAD"), new)
        self.assertEqual(git(path, "status", "--porcelain"), "")
        refs = git(
            path, "for-each-ref", "--format=%(objectname)", "refs/rwgk/backtracking"
        )
        self.assertIn(old, refs.splitlines())
        records = list((self.root / "backtracking").glob("*.json"))
        self.assertEqual(len(records), 1)

    def test_review_refresh_preserves_additional_local_commits(self):
        source = self.branch("rwgk/source")
        old = source.oid("HEAD")
        self.prs[42] = self.raw_pr(
            42, author="contributor", head_repo=None, branch="topic", oid=old
        )
        git(self.primary, "push", "upstream", old + ":refs/pull/42/head")
        self.repo = FixtureRepo(self)
        path = github.open_pr(self.repo, 42)
        (path / "valuable").write_text("local contribution\n")
        git(path, "add", "valuable")
        git(path, "commit", "-m", "Additional local commit")
        local = git(path, "rev-parse", "HEAD")
        new = self.rewrite(source)
        self.prs[42]["head"]["sha"] = new
        git(self.primary, "push", "--force", "upstream", new + ":refs/pull/42/head")
        status, _, stderr = self.invoke_pull(path)
        self.assertEqual(status, 1)
        self.assertIn("local work diverged", stderr)
        self.assertEqual(git(path, "rev-parse", "HEAD"), local)
        self.assertEqual((path / "valuable").read_text(), "local contribution\n")

    def pending_snapshot(self, repo):
        old = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            repo.oid("main"),
            "-m",
            "Old published head before server restack",
        )
        pending = [{"oid": old, "label": "published-before-server-restack"}]
        repo.branches["rwgk/change"]["pending_archive"] = pending
        repo.save()
        return pending

    def test_publication_preserves_pending_published_heads_without_a_force_push(self):
        repo = self.branch()
        pending = self.pending_snapshot(repo)

        def verify_before_push(*args, **kwargs):
            self.assertEqual(repo.branches["rwgk/change"]["pending_archive"], pending)
            self.assertFalse(self.pushes())
            return []

        with mock.patch(
            "rwgk_wt.lifecycle.pin_snapshots", side_effect=verify_before_push
        ) as pin:
            self.publish(repo)
        self.assertEqual(pin.call_args.args[1], pending)
        self.assertNotIn("pending_archive", repo.branches["rwgk/change"])
        self.assertNotIn(
            "pending_archive", FixtureRepo(self, repo.root).branches["rwgk/change"]
        )
        self.assertEqual(len(self.pushes()), 1)

    def test_failed_pending_archive_preserves_queue_and_does_not_push(self):
        repo = self.branch()
        pending = self.pending_snapshot(repo)
        with (
            mock.patch(
                "rwgk_wt.lifecycle.pin_snapshots",
                side_effect=WTError("Archive verification failed"),
            ),
            self.assertRaisesRegex(WTError, "Archive verification failed"),
        ):
            self.publish(repo)
        self.assertEqual(repo.branches["rwgk/change"]["pending_archive"], pending)
        self.assertEqual(
            FixtureRepo(self, repo.root).branches["rwgk/change"]["pending_archive"],
            pending,
        )
        self.assertFalse(self.pushes())

    def test_source_deletion_checks_open_heads_and_same_repo_bases(self):
        pr = github._normalize_pr(
            self.raw_pr(
                1, head_repo="Acme/project", branch="rwgk/source", state="closed"
            ),
            "Acme/project",
        )
        child = self.raw_pr(
            2, head_repo="Acme/project", branch="rwgk/child", base="rwgk/source"
        )
        with (
            mock.patch("rwgk_wt.github._api", side_effect=[[], [child]]) as api,
            self.assertRaisesRegex(WTError, "uses rwgk/source as its base"),
        ):
            github.ensure_source_unused(self.repo, pr)
        self.assertIn("head=Acme%3Arwgk%2Fsource", api.call_args_list[0].args[1])
        self.assertIn("base=rwgk%2Fsource", api.call_args_list[1].args[1])

    def test_source_deletion_checks_fork_head_without_querying_upstream_base(self):
        pr = github._normalize_pr(self.raw_pr(1, state="closed"), "Acme/project")
        with mock.patch("rwgk_wt.github._api", return_value=[]) as api:
            github.ensure_source_unused(self.repo, pr)
        self.assertEqual(api.call_count, 1)
        with (
            mock.patch("rwgk_wt.github._api", return_value=[self.raw_pr(2)]),
            self.assertRaisesRegex(WTError, "Open PR #2 still uses"),
        ):
            github.ensure_source_unused(self.repo, pr)

    def test_source_deletion_refuses_incomplete_or_ambiguous_lookups(self):
        pr = github._normalize_pr(self.raw_pr(1, state="closed"), "Acme/project")
        for response in ([{}], [self.raw_pr(2)] * 100, {}):
            with (
                self.subTest(response_type=type(response)),
                mock.patch("rwgk_wt.github._api", return_value=response),
                self.assertRaisesRegex(WTError, "incomplete"),
            ):
                github.ensure_source_unused(self.repo, pr)

    def test_archive_destination_must_be_owned_fork_in_correct_network(self):
        self.assertEqual(github.validate_archive_remote(self.repo), "ralf/project")
        original = self.gh

        def wrong_fork(argv, input):
            if argv[-1] == "repos/ralf/project":
                return self.response(
                    {
                        "full_name": "ralf/project",
                        "fork": True,
                        "owner": {"login": "someone"},
                        "permissions": {"push": True},
                        "parent": {"full_name": "Acme/project"},
                    }
                )
            return original(argv, input)

        with (
            mock.patch.object(self, "gh", side_effect=wrong_fork),
            self.assertRaisesRegex(WTError, "owned or administered"),
        ):
            github.validate_archive_remote(self.repo)


if __name__ == "__main__":
    unittest.main()
