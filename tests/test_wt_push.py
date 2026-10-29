"""Independent PR pushes use exact head refs without permanent Git tracking."""

import copy
import json
import subprocess
import unittest
from unittest import mock

import test_wt_github as fixtures
from rwgk_wt import github, pr_push
from rwgk_wt.core import WTError

git = fixtures.git


class PushTests(unittest.TestCase):
    # Reuse fixture construction without inheriting and rediscovering its tests.
    setUp = fixtures.GitHubTests.setUp
    remove_external_trees = fixtures.GitHubTests.remove_external_trees
    response = fixtures.GitHubTests.response
    raw_pr = fixtures.GitHubTests.raw_pr
    gh = fixtures.GitHubTests.gh
    pushes = fixtures.GitHubTests.pushes

    def refresh_pr(self, value):
        value = copy.deepcopy(value)
        identity = (value["head"].get("repo") or {}).get("full_name")
        for remote, endpoint in self.identities.items():
            if github._identity(endpoint).lower() != (identity or "").lower():
                continue
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
                text=True,
                check=False,
            )
            if not result.returncode:
                value["head"]["sha"] = result.stdout.strip()
            break
        return value

    def pair(self, *, remote=True, own=False):
        if own:
            destination, identity, branch, author = (
                "origin",
                "ralf/project",
                "rwgk/change",
                "ralf",
            )
        else:
            destination, identity, branch, author = (
                "contributor",
                "contributor/project",
                "topic",
                "contributor",
            )
            self.remotes[destination] = self.root / "contributor.git"
            subprocess.run(
                ["git", "init", "--bare", str(self.remotes[destination])],
                check=True,
                capture_output=True,
            )
            self.identities[destination] = "git@github.com:contributor/project.git"
            if remote:
                git(
                    self.primary,
                    "remote",
                    "add",
                    destination,
                    str(self.remotes[destination]),
                )
        # Accept either ordinary GitHub transport in this wire-only fixture.
        self.identities[destination + "_https"] = f"https://github.com/{identity}.git"
        self.remotes[destination + "_https"] = self.remotes[destination]
        oid = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            self.repo.oid("main"),
            "-m",
            "Contributor change",
        )
        git(
            self.primary,
            "push",
            str(self.remotes[destination]),
            f"{oid}:refs/heads/{branch}",
        )
        self.prs[42] = self.raw_pr(
            42, author=author, head_repo=identity, branch=branch, oid=oid
        )
        self.prs[42]["maintainer_can_modify"] = True
        git(self.primary, "push", "upstream", f"{oid}:refs/pull/42/head")
        path = github.open_pr(self.repo, 42)
        return fixtures.FixtureRepo(self, path)

    def commit(self, repo, name="contribution"):
        (repo.root / name).write_text(name + "\n")
        git(repo.root, "add", name)
        git(repo.root, "commit", "-m", "Add " + name)
        return repo.oid("HEAD")

    def advance_remote(self, repo):
        pr = repo.branches[repo.current_branch()]["pr"]
        previous = git(self.remotes["contributor"], "rev-parse", "refs/heads/topic")
        oid = git(
            self.primary,
            "commit-tree",
            "main^{tree}",
            "-p",
            previous,
            "-m",
            "Remote contribution",
        )
        git(
            self.primary,
            "push",
            str(self.remotes["contributor"]),
            oid + ":refs/heads/topic",
        )
        self.prs[pr["number"]]["head"]["sha"] = oid
        return oid

    def test_push_targets_contributor_head_and_records_sync_without_tracking(self):
        repo = self.pair()
        branch = repo.current_branch()
        old = repo.oid("HEAD")
        oid = self.commit(repo)
        before_config = git(repo.root, "config", "--local", "--list")
        result = pr_push.push(repo)
        self.assertEqual(result["result"], "pushed")
        self.assertEqual(result["repo"], "contributor/project")
        self.assertEqual(result["ref"], "refs/heads/topic")
        self.assertEqual((result["previous_oid"], result["oid"]), (old, oid))
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), oid)
        self.assertEqual(git(repo.root, "config", "--local", "--list"), before_config)
        metadata = repo.branches[branch]
        self.assertEqual(metadata["role"], "review")
        self.assertNotIn("publication", metadata)
        self.assertEqual(metadata["sync_base"]["oid"], oid)
        self.assertEqual(repo.oid(metadata["sync_base"]["ref"]), oid)
        self.assertEqual(metadata["last_checked_out_head"], oid)
        command = self.pushes()[-1]
        self.assertIn(f"{oid}:refs/heads/topic", command)
        self.assertIn(f"--force-with-lease=refs/heads/topic:{old}", command)
        self.assertIn("--recurse-submodules=no", command)
        self.assertIn("--no-follow-tags", command)
        self.assertNotIn("HEAD:refs/heads/topic", command)
        self.assertEqual(len(self.pushes()), 1)

    def test_push_works_without_contributor_remote_and_does_not_add_one(self):
        repo = self.pair(remote=False)
        oid = self.commit(repo)
        before = git(repo.root, "remote")
        pr_push.push(repo)
        self.assertEqual(git(repo.root, "remote"), before)
        self.assertNotIn("contributor", before.splitlines())
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), oid)

    def test_unchanged_head_is_success_without_a_push(self):
        repo = self.pair()
        oid = repo.oid("HEAD")
        result = pr_push.push(repo)
        self.assertEqual((result["result"], result["oid"]), ("unchanged", oid))
        self.assertEqual(self.pushes(), [])
        self.assertEqual(repo.branches[repo.current_branch()]["sync_base"]["oid"], oid)

    def test_push_preserves_existing_tracking_even_to_unrelated_branch(self):
        repo = self.pair()
        branch = repo.current_branch()
        git(repo.root, "config", f"branch.{branch}.remote", "upstream")
        git(repo.root, "config", f"branch.{branch}.merge", "refs/heads/main")
        self.commit(repo)
        pr_push.push(repo)
        self.assertEqual(
            git(repo.root, "config", f"branch.{branch}.remote"), "upstream"
        )
        self.assertEqual(
            git(repo.root, "config", f"branch.{branch}.merge"), "refs/heads/main"
        )

    def test_push_does_not_inherit_auto_tracking_tag_or_submodule_push_defaults(self):
        repo = self.pair()
        oid = self.commit(repo)
        git(repo.root, "config", "push.autoSetupRemote", "true")
        git(repo.root, "config", "push.followTags", "true")
        git(repo.root, "config", "push.recurseSubmodules", "on-demand")
        git(repo.root, "tag", "-a", "local-annotation", "-m", "Keep this tag local")
        before = git(repo.root, "config", "--local", "--list")
        pr_push.push(repo)
        self.assertEqual(git(repo.root, "config", "--local", "--list"), before)
        self.assertEqual(git(self.remotes["contributor"], "tag", "--list"), "")
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), oid)

    def test_push_allows_and_preserves_uncommitted_files_and_index(self):
        repo = self.pair()
        oid = self.commit(repo)
        (repo.root / "base").write_text("staged local work\n")
        git(repo.root, "add", "base")
        (repo.root / "base").write_text("unstaged local work\n")
        (repo.root / "notes").write_text("untracked local work\n")
        before = git(repo.root, "status", "--porcelain=v1")
        index = git(repo.root, "show", ":base")
        pr_push.push(repo)
        self.assertEqual(git(repo.root, "status", "--porcelain=v1"), before)
        self.assertEqual(git(repo.root, "show", ":base"), index)
        self.assertEqual((repo.root / "base").read_text(), "unstaged local work\n")
        self.assertEqual((repo.root / "notes").read_text(), "untracked local work\n")
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), oid)

    def test_push_refuses_active_git_operation_before_network(self):
        repo = self.pair()
        gitdir = repo.git(
            "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD"
        )
        from pathlib import Path

        Path(gitdir).write_text(repo.oid("HEAD") + "\n")
        before = len(self.gh_calls)
        with self.assertRaisesRegex(WTError, "Unfinished Git operation"):
            pr_push.push(repo)
        self.assertEqual(len(self.gh_calls), before)
        self.assertFalse(self.pushes())

    def test_push_behind_directs_to_pull(self):
        repo = self.pair()
        before = copy.deepcopy(repo.branches[repo.current_branch()]["sync_base"])
        self.advance_remote(repo)
        with self.assertRaisesRegex(WTError, "behind.*wt pull"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())
        self.assertEqual(repo.branches[repo.current_branch()]["sync_base"], before)

    def test_push_diverged_directs_to_rebase(self):
        repo = self.pair()
        local = self.commit(repo)
        remote = self.advance_remote(repo)
        with self.assertRaisesRegex(WTError, "diverged.*wt pull --rebase"):
            pr_push.push(repo)
        self.assertEqual(repo.oid("HEAD"), local)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), remote)
        self.assertFalse(self.pushes())

    def test_push_rejects_changed_pr_source_before_transfer(self):
        repo = self.pair()
        for field, value in (
            ("ref", "different"),
            ("repo", {"full_name": "elsewhere/project"}),
        ):
            with self.subTest(field=field):
                previous = self.prs[42]["head"][field]
                self.prs[42]["head"][field] = value
                with self.assertRaisesRegex(
                    WTError, "source repository or branch changed"
                ):
                    pr_push.push(repo)
                self.prs[42]["head"][field] = previous
        self.assertFalse(self.pushes())

    def test_push_rejects_closed_or_merged_pr(self):
        repo = self.pair()
        for merged in (False, True):
            self.prs[42]["state"] = "closed"
            self.prs[42]["merged_at"] = "2026-10-29T12:00:00Z" if merged else None
            with (
                self.subTest(merged=merged),
                self.assertRaisesRegex(WTError, "closed|merged"),
            ):
                pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_does_not_recreate_a_missing_source_branch(self):
        repo = self.pair()
        self.commit(repo)
        git(self.remotes["contributor"], "update-ref", "-d", "refs/heads/topic")
        with self.assertRaisesRegex(WTError, "missing.*not recreate"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_refuses_mismatching_fetch_and_push_alias(self):
        repo = self.pair()
        self.push_identities["contributor"] = "git@github.com:elsewhere/project.git"
        with self.assertRaisesRegex(WTError, "different fetch and push"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_refuses_url_rewrites_to_another_repository(self):
        repo = self.pair(remote=False)
        git(
            repo.root,
            "config",
            "url.git@github.com:elsewhere/project.git.pushInsteadOf",
            "git@github.com:contributor/project.git",
        )
        with self.assertRaisesRegex(WTError, "URL rewrites change"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_refuses_api_source_tip_disagreement(self):
        repo = self.pair()
        actual = github.get_pr(repo, 42)
        actual["head_oid"] = repo.oid("main")
        with (
            mock.patch.object(github, "get_pr", return_value=actual),
            self.assertRaisesRegex(WTError, "disagree"),
        ):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_exact_lease_refuses_a_concurrent_remote_rewind(self):
        repo = self.pair()
        self.commit(repo)
        old = git(self.remotes["contributor"], "rev-parse", "topic")
        original = github._git_result

        def rewind_before_push(target, *args, **kwargs):
            if "push" in args:
                git(
                    self.remotes["contributor"],
                    "update-ref",
                    "refs/heads/topic",
                    repo.oid("main"),
                    old,
                )
            return original(target, *args, **kwargs)

        with (
            mock.patch.object(github, "_git_result", side_effect=rewind_before_push),
            self.assertRaisesRegex(WTError, "changed during push.*wt pull"),
        ):
            pr_push.push(repo)
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(
            git(self.remotes["contributor"], "rev-parse", "topic"), repo.oid("main")
        )

    def test_permission_failure_has_actionable_feedback_without_retry(self):
        repo = self.pair()
        old = repo.oid("HEAD")
        self.commit(repo)
        original = github._git_result

        def deny_push(target, *args, **kwargs):
            if "push" in args:
                return subprocess.CompletedProcess(
                    args, 128, "", "Permission denied to ralf"
                )
            return original(target, *args, **kwargs)

        with (
            mock.patch.object(github, "_git_result", side_effect=deny_push),
            self.assertRaisesRegex(WTError, "Allow edits from maintainers"),
        ):
            pr_push.push(repo)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), old)
        self.assertEqual(repo.branches[repo.current_branch()]["sync_base"]["oid"], old)

    def test_push_checks_api_after_transfer_before_advancing_checkpoint(self):
        repo = self.pair()
        old = repo.oid("HEAD")
        local = self.commit(repo)
        stale = github.get_pr(repo, 42)
        with (
            mock.patch.object(github, "get_pr", return_value=stale),
            self.assertRaisesRegex(WTError, "push may have completed"),
        ):
            pr_push.push(repo)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), local)
        self.assertEqual(repo.branches[repo.current_branch()]["sync_base"]["oid"], old)

    def test_push_supports_own_pr_and_advances_existing_publication(self):
        repo = self.pair(own=True)
        branch = repo.current_branch()
        self.assertEqual(repo.branches[branch]["role"], "owned")
        local = self.commit(repo)
        result = pr_push.push(repo)
        self.assertEqual(result["repo"], "ralf/project")
        self.assertEqual(repo.branches[branch]["publication"]["oid"], local)
        self.assertEqual(repo.branches[branch]["role"], "owned")
        self.assertEqual(git(self.remotes["origin"], "rev-parse", "rwgk/change"), local)

    def test_push_rejects_trunk_detached_and_unassociated_branches(self):
        with self.assertRaisesRegex(WTError, "trunk"):
            pr_push.push(self.repo)
        repo = self.pair()
        branch = repo.current_branch()
        saved = repo.branches[branch].pop("pr")
        with self.assertRaisesRegex(WTError, "no associated PR"):
            pr_push.push(repo)
        repo.branches[branch]["pr"] = saved
        git(repo.root, "checkout", "--detach", "HEAD")
        with self.assertRaisesRegex(WTError, "Detached HEAD"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_rejects_stack_layer_before_looking_up_github(self):
        repo = self.pair()
        (repo.common / "gh-stack").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "stacks": [
                        {
                            "trunk": {"branch": "main"},
                            "branches": [{"branch": repo.current_branch()}],
                        }
                    ],
                }
            )
        )
        before = len(self.gh_calls)
        with self.assertRaisesRegex(WTError, "stack"):
            pr_push.push(repo)
        self.assertEqual(len(self.gh_calls), before)
        self.assertFalse(self.pushes())

    def test_push_captures_head_before_network_and_refuses_a_concurrent_local_commit(
        self,
    ):
        repo = self.pair()
        self.commit(repo)
        old = git(self.remotes["contributor"], "rev-parse", "topic")
        original = github.get_pr

        def commit_during_preflight(target, *args, **kwargs):
            value = original(target, *args, **kwargs)
            self.commit(repo, "concurrent")
            return value

        with (
            mock.patch.object(github, "get_pr", side_effect=commit_during_preflight),
            self.assertRaisesRegex(WTError, "local branch changed"),
        ):
            pr_push.push(repo)
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), old)
        self.assertFalse(self.pushes())

    def test_effective_url_uses_longest_rewrite_prefix(self):
        repo = self.pair(remote=False)
        git(
            repo.root,
            "config",
            "url.git@github.com:wrong/.pushInsteadOf",
            "https://github.com/",
        )
        git(
            repo.root,
            "config",
            "url.git@github.com:contributor/.pushInsteadOf",
            "https://github.com/contributor/",
        )
        self.assertEqual(
            pr_push._effective_url(
                repo,
                "https://github.com/contributor/project.git",
                "contributor/project",
            ),
            "git@github.com:contributor/project.git",
        )

    def test_effective_url_refuses_ordinary_instead_of_redirection(self):
        repo = self.pair(remote=False)
        git(
            repo.root,
            "config",
            "url.git@github.com:elsewhere/project.git.insteadOf",
            "git@github.com:contributor/project.git",
        )
        with self.assertRaisesRegex(WTError, "URL rewrites change"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_refuses_wrong_github_owner_hidden_by_roundtrip_rewrites(self):
        repo = self.pair(remote=False)
        git(
            repo.root,
            "config",
            "url.https://github.com/elsewhere/.pushInsteadOf",
            "git@github.com:contributor/",
        )
        git(
            repo.root,
            "config",
            "url.git@github.com:contributor/.insteadOf",
            "https://github.com/elsewhere/",
        )
        with self.assertRaisesRegex(WTError, "URL rewrites change"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_refuses_non_github_endpoint_hidden_by_roundtrip_rewrites(self):
        repo = self.pair(remote=False)
        git(
            repo.root,
            "config",
            "url.https://elsewhere.invalid/contributor/.pushInsteadOf",
            "git@github.com:contributor/",
        )
        git(
            repo.root,
            "config",
            "url.git@github.com:contributor/.insteadOf",
            "https://elsewhere.invalid/contributor/",
        )
        with self.assertRaisesRegex(WTError, "URL rewrites change"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())

    def test_push_does_not_restore_history_removed_by_an_author_rewind(self):
        repo = self.pair()
        boundary = repo.oid("HEAD")
        local = self.commit(repo)
        rewound = repo.oid("main")
        git(
            self.remotes["contributor"],
            "update-ref",
            "refs/heads/topic",
            rewound,
            boundary,
        )
        with self.assertRaisesRegex(WTError, "removed.*wt pull --rebase"):
            pr_push.push(repo)
        self.assertFalse(self.pushes())
        self.assertEqual(repo.oid("HEAD"), local)
        self.assertEqual(
            git(self.remotes["contributor"], "rev-parse", "topic"), rewound
        )
        self.assertEqual(
            repo.branches[repo.current_branch()]["sync_base"]["oid"], boundary
        )

    def test_push_accepts_manual_reconciliation_after_an_author_rewind(self):
        repo = self.pair()
        boundary = repo.oid("HEAD")
        self.commit(repo)
        rewound = repo.oid("main")
        git(
            self.remotes["contributor"],
            "update-ref",
            "refs/heads/topic",
            rewound,
            boundary,
        )
        git(repo.root, "rebase", "--onto", rewound, boundary)
        local = repo.oid("HEAD")
        self.assertNotEqual(local, boundary)
        self.assertEqual(
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo.root),
                    "merge-base",
                    "--is-ancestor",
                    boundary,
                    local,
                ],
                capture_output=True,
                check=False,
            ).returncode,
            1,
        )
        result = pr_push.push(repo)
        self.assertEqual((result["result"], result["oid"]), ("pushed", local))
        self.assertEqual(git(self.remotes["contributor"], "rev-parse", "topic"), local)


if __name__ == "__main__":
    unittest.main()
