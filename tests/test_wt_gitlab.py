"""GitLab MR workflows use real local Git and a fully isolated API fixture."""

from __future__ import annotations

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
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli, github, pr_push, pr_rebase
from rwgk_wt.core import Repo, WTError

HOST = "gitlab.example.invalid"
TARGET_PATH = "Acme/libraries/project"
TARGET = f"{HOST}/{TARGET_PATH}"
OWN = f"{HOST}/ralf/project"
CONTRIBUTOR = f"{HOST}/contributor/project"


def git(path, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(path), *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode:
        raise AssertionError(f"Git failed: {args!r}\n{result.stderr}")
    return result.stdout.strip()


class GitLabRepo(Repo):
    def __init__(self, fixture, path=None):
        self.fixture = fixture
        super().__init__(path or fixture.primary, initialized=False)

    def run(self, argv, *, cwd=None, check=True, input=None):
        argv = list(map(str, argv))
        prefix = []
        if argv[0] == "env":
            prefix.append(argv.pop(0))
            while argv and "=" in argv[0]:
                prefix.append(argv.pop(0))
        if argv[0] == "glab":
            result = self.fixture.glab(argv, input)
            if check and result.returncode:
                raise WTError(result.stderr or result.stdout)
            return result
        if argv[0] == "gh":
            self.fixture.fail("GitLab workflow invoked GitHub CLI: " + repr(argv))
        if argv[:3] == ["git", "remote", "get-url"]:
            remote = argv[-1]
            value = self.fixture.identities[remote]
            if "--push" in argv:
                value = self.fixture.push_identities.get(remote, value)
            return subprocess.CompletedProcess(argv, 0, value + "\n", "")
        self.fixture.commands.append(list(argv))
        endpoints = {
            value: self.fixture.remotes[name]
            for name, value in self.fixture.identities.items()
        }
        for identifier, project in self.fixture.projects.items():
            remote = self.fixture.project_remotes[identifier]
            endpoints[project["ssh_url_to_repo"]] = self.fixture.remotes[remote]
            endpoints[project["http_url_to_repo"]] = self.fixture.remotes[remote]
        for index, value in enumerate(argv):
            if value in endpoints:
                argv[index] = str(endpoints[value])
            elif HOST in value and ("://" in value or value.startswith("git@")):
                self.fixture.fail("Unmapped fixture Git endpoint: " + value)
        return super().run([*prefix, *argv], cwd=cwd, check=check, input=input)


class GitLabFixture(unittest.TestCase):
    def setUp(self):
        area = Path(os.environ.get("WT_TEST_WORKTREE_ROOT", "/wrk/trees/.wt-tests"))
        self.assertTrue(area.resolve().is_relative_to(Path("/wrk/trees/.wt-tests")))
        area.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="wt-gitlab-", dir=area)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.primary = self.root / "primary"
        self.primary.mkdir()
        git(self.primary, "init", "--initial-branch=main")
        for key, value in (
            ("user.name", "GitLab fixture"),
            ("user.email", "fixture@example.invalid"),
            ("commit.gpgsign", "false"),
        ):
            git(self.primary, "config", key, value)
        (self.primary / "base").write_text("base\n")
        git(self.primary, "add", "base")
        git(self.primary, "commit", "-m", "Base")
        self.base = git(self.primary, "rev-parse", "HEAD")
        self.remotes = {
            name: self.root / (name + ".git")
            for name in ("upstream", "origin", "contributor")
        }
        for name, path in self.remotes.items():
            subprocess.run(
                ["git", "init", "--bare", str(path)], check=True, capture_output=True
            )
            git(self.primary, "push", str(path), "main")
            if name != "contributor":
                git(self.primary, "remote", "add", name, str(path))
        self.identities = {
            "upstream": f"ssh://git@{HOST}:12051/{TARGET_PATH}.git",
            "origin": f"ssh://git@{HOST}:12051/ralf/project.git",
        }
        self.push_identities = {}
        self.project_remotes = {10: "upstream", 20: "origin", 30: "contributor"}
        self.projects = {
            10: self.raw_project(10, TARGET_PATH, owner="Acme", fork=False),
            20: self.raw_project(20, "ralf/project", owner="ralf"),
            30: self.raw_project(30, "contributor/project", owner="contributor"),
        }
        self.commands = []
        self.api_calls = []
        self.mrs = {}
        self.next_mr = 100
        self.repo = GitLabRepo(self)
        self.repo.config.update(
            key="gitlab-fixture",
            primary=str(self.primary),
            trunk="main",
            upstream_remote="upstream",
            upstream_repo=TARGET,
            provider="gitlab",
            host=HOST,
            publish_remote="origin",
            archive_remote="origin",
            trees_root=str(self.root / "trees"),
            backtracking=str(self.root / "backtracking"),
        )
        self.repo.branches["main"] = {"path": str(self.primary), "role": "trunk"}
        self.repo.save()
        patch = mock.patch.dict(
            os.environ,
            {
                "MY_GIT_BACKTRACKING_INFO_LOCAL": str(self.root / "backtracking"),
                "MY_GIT_BACKTRACKING_INFO_REMOTE": "",
            },
        )
        patch.start()
        self.addCleanup(patch.stop)

    def raw_project(self, identifier, path, *, owner, fork=True):
        value = {
            "id": identifier,
            "path_with_namespace": path,
            "web_url": f"https://{HOST}/{path}",
            "ssh_url_to_repo": f"ssh://git@{HOST}:12051/{path}.git",
            "http_url_to_repo": f"https://{HOST}/{path}.git",
            "default_branch": "main",
            "owner": {"id": identifier, "username": owner},
            "namespace": {
                "id": identifier,
                "full_path": path.rsplit("/", 1)[0],
                "path": owner,
                "kind": "user" if fork else "group",
            },
            "permissions": {
                "project_access": {"access_level": 40},
                "group_access": None,
            },
        }
        if fork:
            value["forked_from_project"] = {
                "id": 10,
                "path_with_namespace": TARGET_PATH,
                "web_url": f"https://{HOST}/{TARGET_PATH}",
            }
        return value

    def raw_mr(
        self,
        number=42,
        *,
        source=30,
        branch="topic",
        author="contributor",
        oid=None,
        state="opened",
        draft=False,
        title="Contributor change",
    ):
        return {
            "id": 1000 + number,
            "iid": number,
            "project_id": 10,
            "source_project_id": source,
            "target_project_id": 10,
            "source_branch": branch,
            "target_branch": "main",
            "sha": oid or self.base,
            "state": state,
            "author": {"id": 50, "username": author},
            "web_url": f"https://{HOST}/{TARGET_PATH}/-/merge_requests/{number}",
            "title": "Draft: " + title if draft else title,
            "description": "Fixture merge request",
            "draft": draft,
            "work_in_progress": draft,
            "merge_commit_sha": None,
            "merged_at": "2026-10-29T12:00:00Z" if state == "merged" else None,
            "diff_refs": {
                "base_sha": self.base,
                "start_sha": self.base,
                "head_sha": oid or self.base,
            },
        }

    def refresh_mr(self, value):
        result = copy.deepcopy(value)
        identifier = result.get("source_project_id")
        if identifier in self.project_remotes:
            remote = self.remotes[self.project_remotes[identifier]]
            oid = git(
                remote,
                "rev-parse",
                "--verify",
                "refs/heads/" + result["source_branch"],
                check=False,
            )
            if oid:
                result["sha"] = oid
                result["diff_refs"]["head_sha"] = oid
        return result

    def response(self, value, status=0):
        return subprocess.CompletedProcess(
            ["glab"],
            status,
            json.dumps(value) if status == 0 else "",
            "404 Project Not Found" if status else "",
        )

    def glab(self, argv, input):
        self.assertNotIn("--jq", argv)
        self.assertNotIn("--paginate", argv)
        self.assertEqual(
            argv[:6], ["glab", "api", "--hostname", HOST, "--method", argv[5]]
        )
        method, endpoint = argv[5:7]
        payload = json.loads(input) if input is not None else None
        self.api_calls.append((method, endpoint, payload))
        if endpoint == "user":
            return self.response({"id": 20, "username": "ralf"})
        parsed = urlsplit(endpoint)
        pieces = parsed.path.strip("/").split("/")
        self.assertEqual(pieces[0], "projects")
        project_key = unquote(pieces[1])
        project = next(
            (
                value
                for value in self.projects.values()
                if str(value["id"]) == project_key
                or value["path_with_namespace"] == project_key
            ),
            None,
        )
        if project is None:
            return self.response({}, status=1)
        project_id = project["id"]
        if len(pieces) == 2:
            return self.response(copy.deepcopy(project))
        self.assertEqual(pieces[2], "merge_requests")
        if len(pieces) == 3 and method == "GET":
            query = parse_qs(parsed.query)
            values = []
            for mr in self.mrs.values():
                if (
                    query.get("state", ["all"])[0] != "all"
                    and mr["state"] != query["state"][0]
                ):
                    continue
                if (
                    "source_branch" in query
                    and mr["source_branch"] != query["source_branch"][0]
                ):
                    continue
                if (
                    "target_branch" in query
                    and mr["target_branch"] != query["target_branch"][0]
                ):
                    continue
                if "source_project_id" in query and mr["source_project_id"] != int(
                    query["source_project_id"][0]
                ):
                    continue
                if (
                    mr["target_project_id"] != project_id
                    and mr["source_project_id"] != project_id
                ):
                    continue
                values.append(self.refresh_mr(mr))
            return self.response(values)
        if len(pieces) == 3 and method == "POST":
            self.next_mr += 1
            mr = self.raw_mr(
                self.next_mr,
                source=project_id,
                branch=payload["source_branch"],
                author="ralf",
                title=payload["title"],
            )
            mr["target_project_id"] = payload.get("target_project_id", project_id)
            mr["target_branch"] = payload["target_branch"]
            mr["description"] = payload.get("description", "")
            mr["draft"] = mr["work_in_progress"] = mr["title"].startswith(
                ("Draft:", "WIP:")
            )
            self.mrs[mr["iid"]] = mr
            return self.response(self.refresh_mr(mr))
        self.assertEqual(len(pieces), 4)
        number = int(pieces[3])
        self.assertIn(number, self.mrs)
        if method == "PUT":
            self.mrs[number].update(payload)
            self.mrs[number]["draft"] = self.mrs[number]["work_in_progress"] = self.mrs[
                number
            ]["title"].startswith(("Draft:", "WIP:"))
        return self.response(self.refresh_mr(self.mrs[number]))

    def contributor_mr(
        self, *, source=30, author="contributor", branch="topic", number=42
    ):
        self.source = self.root / "source"
        subprocess.run(
            ["git", "clone", "--quiet", str(self.primary), str(self.source)],
            check=True,
            capture_output=True,
        )
        git(self.source, "config", "user.name", "Source fixture")
        git(self.source, "config", "user.email", "source@example.invalid")
        git(self.source, "config", "commit.gpgsign", "false")
        git(self.source, "checkout", "-b", branch)
        (self.source / "contributor").write_text("original source change\n")
        self.commit(self.source, "Contributor change")
        self.mrs[number] = self.raw_mr(
            number, source=source, branch=branch, author=author
        )
        self.update_source(number)
        return self.mrs[number]

    def commit(self, path, message):
        git(path, "add", "--all")
        git(path, "commit", "-m", message)
        return git(path, "rev-parse", "HEAD")

    def update_source(self, number=42):
        mr = self.mrs[number]
        oid = git(self.source, "rev-parse", "HEAD")
        source = self.remotes[self.project_remotes[mr["source_project_id"]]]
        git(
            self.source,
            "push",
            "--force",
            str(source),
            f"HEAD:refs/heads/{mr['source_branch']}",
        )
        git(
            self.source,
            "push",
            "--force",
            str(self.remotes["upstream"]),
            f"HEAD:refs/merge-requests/{number}/head",
        )
        mr["sha"] = mr["diff_refs"]["head_sha"] = oid
        return oid

    def owned_branch(self, name="rwgk/change"):
        path = self.repo.create(name, "main")
        (path / "change").write_text("owned change\n")
        self.commit(path, "Owned change")
        return GitLabRepo(self, path)

    def open_review(self):
        path = github.open_pr(self.repo, 42)
        return GitLabRepo(self, path)

    def pushes(self):
        return [
            command
            for command in self.commands
            if command[0] == "git" and "push" in command
        ]

    def tracking(self, repo):
        return repo.git(
            "config",
            "--local",
            "--get-regexp",
            r"^branch\..*\.(remote|merge)$",
            check=False,
        )

    def assert_checkouts(self, repo=None):
        self.assertEqual(git(self.primary, "symbolic-ref", "--short", "HEAD"), "main")
        if hasattr(self, "source"):
            self.assertEqual(
                git(self.source, "symbolic-ref", "--short", "HEAD"),
                self.mrs[42]["source_branch"],
            )
        if repo:
            self.assertEqual(repo.current_branch(), "rwgk/review-pr42")

    def invoke(self, repo, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "Repo", return_value=repo),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(args)
        return status, stdout.getvalue(), stderr.getvalue()


class GitLabTests(GitLabFixture):
    def test_initialization_infers_host_nested_project_and_custom_ssh_port(self):
        self.repo.state_path.unlink()
        repo = GitLabRepo(self)
        value = repo.initialize(
            key="gitlab-fixture",
            trunk="main",
            upstream="upstream",
            publish="origin",
            archive="origin",
            trees=str(self.root / "trees"),
            backtracking=str(self.root / "backtracking"),
        )
        self.assertEqual(value["upstream_repo"], TARGET)
        self.assertEqual(github.remote_identity(repo, "upstream"), TARGET)
        self.assertEqual(value["trunk"], "main")
        self.assertFalse(self.api_calls)
        self.assertFalse(self.pushes())

    def test_empty_legacy_identity_is_inferred_without_read_side_effects(self):
        self.repo.config["upstream_repo"] = ""
        self.repo.config.pop("provider")
        self.repo.config.pop("host")
        self.repo.save()
        before = self.repo.state_path.read_bytes()
        repo = GitLabRepo(self)
        self.assertEqual(repo.config["upstream_repo"], TARGET)
        self.assertEqual(repo.state_path.read_bytes(), before)

    def test_review_checkout_uses_mr_iid_retained_ref_and_idempotent_alias(self):
        mr = self.contributor_mr()
        path = github.open_pr(self.repo, mr["web_url"])
        metadata = self.repo.branches["rwgk/review-pr42"]
        self.assertEqual(metadata["role"], "review")
        self.assertEqual(metadata["pr"]["number"], 42)
        self.assertEqual(metadata["pr"]["head_repo"], CONTRIBUTOR)
        self.assertEqual(git(path, "rev-parse", "HEAD"), mr["sha"])
        self.assertEqual((path.parent / "pr42").resolve(), path)
        self.assertEqual(github.open_pr(self.repo, "pr42"), path)
        self.assertEqual(len(self.repo.worktrees()), 2)
        self.assertTrue(
            any(
                "refs/merge-requests/42/head" in " ".join(command)
                for command in self.commands
            )
        )
        self.assertFalse(
            any("refs/pull/" in " ".join(command) for command in self.commands)
        )
        self.assertFalse(self.tracking(GitLabRepo(self, path)))
        self.assert_checkouts()

    def test_clean_registered_manual_review_pair_is_adopted_without_switching(self):
        mr = self.contributor_mr()
        git(
            self.primary,
            "fetch",
            str(self.remotes["upstream"]),
            "refs/merge-requests/42/head",
        )
        path = self.repo.create("rwgk/review-pr42", mr["sha"])
        self.assertEqual(self.repo.branches["rwgk/review-pr42"]["role"], "owned")
        self.assertEqual(github.open_pr(self.repo, 42), path)
        self.assertEqual(self.repo.branches["rwgk/review-pr42"]["role"], "review")
        self.assertEqual(github.open_pr(self.repo, "pr42"), path)
        self.assertEqual(git(path, "rev-parse", "HEAD"), mr["sha"])
        self.assert_checkouts()

    def test_unmanaged_canonical_manual_review_worktree_is_adopted_safely(self):
        mr = self.contributor_mr()
        git(
            self.primary,
            "fetch",
            str(self.remotes["upstream"]),
            "refs/merge-requests/42/head",
        )
        branch = "rwgk/review-pr42"
        path = self.repo.canonical_path(branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.primary, "worktree", "add", "-b", branch, str(path), mr["sha"])
        self.assertNotIn(branch, self.repo.branches)
        self.assertEqual(github.open_pr(self.repo, 42), path)
        self.assertEqual(self.repo.branches[branch]["role"], "review")
        self.assertEqual(github.open_pr(self.repo, "pr42"), path)
        self.assertEqual(git(path, "rev-parse", "HEAD"), mr["sha"])
        self.assert_checkouts()

    def test_dirty_or_different_head_manual_pair_is_not_reclassified(self):
        mr = self.contributor_mr()
        git(
            self.primary,
            "fetch",
            str(self.remotes["upstream"]),
            "refs/merge-requests/42/head",
        )
        path = self.repo.create("rwgk/review-pr42", mr["sha"])
        (path / "valuable").write_text("keep this work\n")
        with self.assertRaises(WTError):
            github.open_pr(self.repo, 42)
        self.assertEqual(self.repo.branches["rwgk/review-pr42"]["role"], "owned")
        self.assertNotIn("pr", self.repo.branches["rwgk/review-pr42"])
        self.commit(path, "Local work before association")
        with self.assertRaises(WTError):
            github.open_pr(self.repo, 42)
        self.assertEqual((path / "valuable").read_text(), "keep this work\n")
        self.assertNotIn("pr", self.repo.branches["rwgk/review-pr42"])
        self.assert_checkouts()

    def test_own_fork_mr_keeps_owned_source_branch(self):
        mr = self.contributor_mr(source=20, author="ralf", branch="rwgk/own")
        path = github.open_pr(self.repo, 42)
        self.assertEqual(git(path, "symbolic-ref", "--short", "HEAD"), "rwgk/own")
        self.assertEqual(self.repo.branches["rwgk/own"]["role"], "owned")
        self.assertEqual(self.repo.branches["rwgk/own"]["pr"]["head_repo"], OWN)
        self.assertEqual(git(path, "rev-parse", "HEAD"), mr["sha"])
        self.assert_checkouts()

    def test_deleted_source_project_still_opens_retained_head_but_cannot_push(self):
        mr = self.contributor_mr()
        mr["source_project_id"] = None
        repo = self.open_review()
        self.assertIsNone(repo.branches[repo.current_branch()]["pr"]["head_repo"])
        with self.assertRaisesRegex(WTError, "unavailable|deleted"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())
        self.assert_checkouts(repo)

    def test_live_mr_and_retained_ref_mismatch_refuses_pair_creation(self):
        mr = self.contributor_mr()
        mr["sha"] = self.base
        mr["source_project_id"] = None
        with self.assertRaisesRegex(WTError, "head changed|head.*differ"):
            github.open_pr(self.repo, 42)
        self.assertNotIn("rwgk/review-pr42", self.repo.branches)
        self.assertEqual(len(self.repo.worktrees()), 1)
        self.assertFalse(self.pushes())

    def test_pull_fast_forwards_review_without_branch_tracking(self):
        self.contributor_mr()
        repo = self.open_review()
        (self.source / "next").write_text("next source commit\n")
        self.commit(self.source, "Source update")
        target = self.update_source()
        before = self.tracking(repo)
        status, stdout, stderr = self.invoke(repo, ["pull"])
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Updated", stdout)
        self.assertEqual(repo.oid("HEAD"), target)
        self.assertEqual(self.tracking(repo), before)
        self.assertFalse(self.pushes())
        self.assert_checkouts(repo)

    def test_pull_rebase_then_push_contribution_uses_actual_fork_without_remote_or_tracking(
        self,
    ):
        self.contributor_mr()
        repo = self.open_review()
        (repo.root / "local").write_text("local contribution\n")
        old_local = self.commit(repo.root, "Local contribution")
        (self.source / "remote").write_text("remote contribution\n")
        self.commit(self.source, "Concurrent source contribution")
        target = self.update_source()
        before = self.tracking(repo)
        result = pr_rebase.start(repo)
        self.assertEqual(result["result"], "rebased")
        self.assertEqual(repo.oid("HEAD^"), target)
        self.assertTrue((repo.root / "local").exists())
        self.assertTrue((repo.root / "remote").exists())
        refs = repo.git(
            "for-each-ref", "--format=%(objectname)", "refs/rwgk/backtracking"
        )
        self.assertIn(old_local, refs.splitlines())
        self.api_calls.clear()
        result = pr_push.push(repo)
        self.assertEqual(result["repo"], CONTRIBUTOR)
        self.assertEqual(
            git(self.remotes["contributor"], "rev-parse", "topic"), repo.oid("HEAD")
        )
        self.assertEqual(repo.branches[repo.current_branch()]["role"], "review")
        self.assertNotIn("publication", repo.branches[repo.current_branch()])
        self.assertEqual(self.tracking(repo), before)
        self.assertEqual(repo.git("remote").splitlines(), ["origin", "upstream"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.api_calls))
        self.assert_checkouts(repo)

    def test_denied_contributor_push_reports_gitlab_permissions_without_retry(self):
        self.contributor_mr()
        repo = self.open_review()
        branch = repo.current_branch()
        old = repo.oid("HEAD")
        (repo.root / "local").write_text("local contribution\n")
        local = self.commit(repo.root, "Local contribution")
        repo.git("config", f"branch.{branch}.remote", "upstream")
        repo.git("config", f"branch.{branch}.merge", "refs/heads/existing-tracking")
        tracking = self.tracking(repo)
        remotes = repo.git("remote")
        hook = self.remotes["contributor"] / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' 'permission denied: source project write access' >&2\n"
            "exit 1\n"
        )
        hook.chmod(0o755)
        self.commands.clear()
        self.api_calls.clear()
        with self.assertRaises(WTError) as failure:
            pr_push.push(repo)
        message = str(failure.exception)
        self.assertIn("GitLab refused the MR push", message)
        self.assertIn("source-project write access", message)
        self.assertIn("allow_collaboration", message)
        self.assertNotIn("GitHub", message)
        pushes = self.pushes()
        self.assertEqual(len(pushes), 1)
        self.assertIn(self.projects[30]["ssh_url_to_repo"], pushes[0])
        self.assertIn(f"--force-with-lease=refs/heads/topic:{old}", pushes[0])
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), old)
        self.assertEqual(repo.oid("HEAD"), local)
        self.assertEqual(self.tracking(repo), tracking)
        self.assertEqual(repo.git("remote"), remotes)
        self.assertEqual(repo.branches[branch]["role"], "review")
        self.assertNotIn("publication", repo.branches[branch])
        self.assertTrue(all(method == "GET" for method, _, _ in self.api_calls))
        self.assert_checkouts(repo)

    def test_changed_source_project_refuses_push_before_mutation(self):
        self.contributor_mr()
        repo = self.open_review()
        (repo.root / "local").write_text("local contribution\n")
        self.commit(repo.root, "Local contribution")
        self.mrs[42]["source_project_id"] = 20
        with self.assertRaisesRegex(WTError, "source.*changed|source.*differ"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())
        self.assert_checkouts(repo)

    def test_recreated_source_project_with_same_path_refuses_push_before_fetch(self):
        self.contributor_mr()
        repo = self.open_review()
        (repo.root / "local").write_text("local contribution\n")
        self.commit(repo.root, "Local contribution")
        self.projects[31] = {**self.projects[30], "id": 31}
        self.project_remotes[31] = "contributor"
        self.mrs[42]["source_project_id"] = 31
        self.commands.clear()
        with self.assertRaisesRegex(WTError, "source.*changed|source.*differ"):
            pr_push.push(repo)
        self.assertFalse(any("fetch" in command for command in self.commands))
        self.assertFalse(self.pushes())
        self.assert_checkouts(repo)

    def test_missing_source_branch_is_not_recreated_by_push(self):
        self.contributor_mr()
        repo = self.open_review()
        (repo.root / "local").write_text("local contribution\n")
        self.commit(repo.root, "Local contribution")
        git(self.remotes["contributor"], "update-ref", "-d", "refs/heads/topic")
        self.commands.clear()
        with self.assertRaisesRegex(
            WTError, "source branch is missing|source branch.*missing"
        ):
            pr_push.push(repo)
        self.assertFalse(self.pushes())
        self.assertFalse(
            git(
                self.remotes["contributor"],
                "show-ref",
                "--verify",
                "refs/heads/topic",
                check=False,
            )
        )
        self.assert_checkouts(repo)

    def test_foreign_mr_url_is_rejected_before_api_or_pair_creation(self):
        for url in (
            f"https://other.example.invalid/{TARGET_PATH}/-/merge_requests/42",
            f"https://{HOST}/Other/project/-/merge_requests/42",
        ):
            with self.subTest(url=url), self.assertRaises(WTError):
                github.open_pr(self.repo, url)
        self.assertFalse(self.api_calls)
        self.assertEqual(len(self.repo.worktrees()), 1)

    def test_owned_publish_creates_draft_mr_in_source_fork_and_updates_ready(self):
        repo = self.owned_branch()
        before = self.tracking(repo)
        result = github.publish(repo)
        self.assertEqual(result["repo"], OWN)
        self.assertTrue(result["prs"][0]["is_draft"])
        creates = [
            (endpoint, payload)
            for method, endpoint, payload in self.api_calls
            if method == "POST"
        ]
        self.assertEqual(len(creates), 1)
        endpoint, payload = creates[0]
        self.assertIn("ralf%2Fproject/merge_requests", endpoint)
        self.assertEqual(payload["target_project_id"], 10)
        self.assertTrue(payload["title"].startswith("Draft:"))
        self.assertEqual(self.tracking(repo), before)
        self.api_calls.clear()
        result = github.publish(repo, title="Reviewed change", ready=True)
        self.assertFalse(result["prs"][0]["is_draft"])
        self.assertTrue(
            any(
                method == "PUT" and TARGET_PATH.replace("/", "%2F") in endpoint
                for method, endpoint, _ in self.api_calls
            )
        )
        self.assertEqual(self.tracking(repo), before)
        self.assertFalse(any(method == "POST" for method, _, _ in self.api_calls))
        self.assert_checkouts()

    def test_stack_commands_fail_clearly_before_api_or_git_mutation(self):
        repo = self.owned_branch()
        self.commands.clear()
        self.api_calls.clear()
        before = repo.state_path.read_bytes()
        for args in (
            ["stack", "init"],
            ["stack", "add", "next"],
            ["stack", "rebase"],
            ["stack", "pull"],
            ["publish", "--stack"],
        ):
            with self.subTest(args=args):
                status, _, stderr = self.invoke(repo, args)
                self.assertEqual(status, 1)
                self.assertRegex(stderr, "(?i)gitlab.*stack|stack.*gitlab")
        self.assertEqual(repo.state_path.read_bytes(), before)
        self.assertFalse(self.api_calls)
        self.assertFalse(self.pushes())
        self.assertFalse((repo.common / "gh-stack").exists())
        self.assertEqual(len(repo.worktrees()), 2)


if __name__ == "__main__":
    unittest.main()
