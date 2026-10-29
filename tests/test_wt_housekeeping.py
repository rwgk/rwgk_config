"""Housekeeping skips uncertain work while preserving recorded cleanup semantics."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
import test_wt_lifecycle as lifecycle_fixtures
from rwgk_wt import cli, housekeeping, lifecycle, stacks
from rwgk_wt.core import WTError


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="housekeeping-orchestration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.primary = self.root / "primary"
        self.primary.mkdir()
        self.worktrees = [{"branch": "main", "path": str(self.primary)}]
        self.refs = {"main": "0" * 40}
        self.entries = {}
        self.events = []
        self.counts = {}
        self.preflight_errors = {}
        self.apply_errors = {}
        self.plan_change = None
        self.assess_change = None
        self.repo = SimpleNamespace(
            root=self.primary,
            primary=self.primary,
            common=self.root,
            config={"trunk": "main", "backtracking": str(self.root / "records")},
            branches={"main": {"path": str(self.primary), "role": "trunk"}},
            state={},
            worktrees=lambda: copy.deepcopy(self.worktrees),
            git=self.git,
            oid=lambda ref: self.refs[ref.removeprefix("refs/heads/")],
            run=lambda *args, **kwargs: SimpleNamespace(returncode=0),
            check_primary=lambda: None,
            ensure_idle=lambda *args: None,
        )
        for patch in (
            mock.patch.object(housekeeping, "_assess", side_effect=self.assess),
            mock.patch.object(lifecycle, "retire", side_effect=self.plan),
            mock.patch.object(lifecycle, "_save_record", side_effect=self.save),
            mock.patch.object(lifecycle, "_apply_retirement", side_effect=self.apply),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, *args):
        self.assertEqual(args, ("for-each-ref", "--format=%(refname)", "refs/heads/"))
        return "\n".join("refs/heads/" + name for name in sorted(self.refs))

    def add(self, name, *, eligible=True, reason="PR remains open", error=False):
        path = self.root / "trees" / name.replace("/", "-")
        path.mkdir(parents=True)
        oid = f"{len(self.entries) + 1:040x}"
        pr = {
            "number": len(self.entries) + 1,
            "base_repo": "Upstream/project",
            "head_repo": "contributor/project",
            "head_ref": name,
            "head_oid": oid,
            "state": "CLOSED" if eligible else "OPEN",
            "url": f"https://github.com/Upstream/project/pull/{len(self.entries) + 1}",
        }
        self.repo.branches[name] = {
            "path": str(path),
            "pr": copy.deepcopy(pr),
            "role": "review",
        }
        self.worktrees.append({"branch": name, "path": str(path)})
        self.refs[name] = oid
        self.entries[name] = {
            "kind": "managed",
            "branch": name,
            "path": str(path),
            "pr": pr,
            "eligible": eligible,
            "reasons": [] if eligible else [{"code": "kept", "message": reason}],
            "hints": [] if eligible else [f"wt go {name}"],
            "error": error,
            "local_oid": oid,
            "submodules": [],
            "coverage": {"pr_head_oid": oid, "basis": "pr"},
        }
        return self.entries[name]

    def assess(self, repo, name, discard_builds):
        self.events.append(
            ("assess", name, discard_builds, hasattr(repo, "_wt_housekeeping_trunk"))
        )
        self.counts[name] = self.counts.get(name, 0) + 1
        result = copy.deepcopy(self.entries[name])
        if self.assess_change:
            result = self.assess_change(repo, name, self.counts[name], result)
        return result

    def plan(self, repo, name, *, apply, discard_builds):
        self.assertFalse(apply)
        self.events.append(
            ("plan", name, set(getattr(repo, "_wt_retiring", ())), discard_builds)
        )
        stacks.check_retirement(repo, name)
        if name in self.preflight_errors:
            raise self.preflight_errors[name]
        entry = self.entries[name]
        record = {
            "id": name.replace("/", "-"),
            "branch": name,
            "local_oid": entry["local_oid"],
            "path": entry["path"],
            "pr": copy.deepcopy(entry["pr"]),
            "submodules": copy.deepcopy(entry["submodules"]),
            "status": "planned",
            "snapshots": [],
            "completed": [],
        }
        if self.plan_change:
            self.plan_change(name, record)
        return record

    def save(self, repo, record):
        self.events.append(("save", record["branch"]))
        journal = lifecycle._operation_dir(repo) / (record["id"] + ".json")
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps(record))
        record["backtracking_path"] = str(
            self.root / "records" / (record["id"] + ".json")
        )

    def apply(self, repo, record):
        name = record["branch"]
        self.assertFalse(hasattr(repo, "_wt_retiring"))
        self.assertTrue(
            (lifecycle._operation_dir(repo) / (record["id"] + ".json")).exists()
        )
        stacks.check_retirement(repo, name)
        self.events.append(("apply", name))
        if name in self.apply_errors:
            raise self.apply_errors[name]
        repo.branches.pop(name)
        self.refs.pop(name)
        self.worktrees[:] = [row for row in self.worktrees if row["branch"] != name]
        stacks.retired(repo, name)
        return {**record, "status": "complete"}

    def stack(self, *names):
        value = {
            "schemaVersion": 1,
            "stacks": [
                {
                    "id": "fixture",
                    "trunk": {"branch": "main"},
                    "branches": [
                        {"branch": name, "base": self.refs["main"]} for name in names
                    ],
                }
            ],
        }
        (self.root / "gh-stack").write_text(json.dumps(value))

    def test_preview_reports_proposed_cleanup_and_all_retained_without_mutation(self):
        self.add("rwgk/finished")
        self.add("rwgk/open", eligible=False)
        self.add(
            "rwgk/extra", eligible=False, reason="2 commits outside final PR history"
        )
        before = copy.deepcopy(self.repo.branches)
        result = housekeeping.run(self.repo)
        self.assertEqual(result["result"], "preview")
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/finished"]
        )
        self.assertEqual(
            {row["branch"] for row in result["remaining"]}, {"rwgk/open", "rwgk/extra"}
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.repo.branches, before)
        self.assertFalse(any(row[0] in ("save", "apply") for row in self.events))
        self.assertFalse(hasattr(self.repo, "_wt_retiring"))

    def test_apply_cleans_only_eligible_and_keeps_all_other_pairs(self):
        self.add("rwgk/finished")
        self.add("rwgk/dirty", eligible=False, reason="2 modified files")
        result = housekeeping.run(self.repo, apply=True, discard_builds=True)
        self.assertEqual(result["result"], "housekeeping")
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/finished"]
        )
        self.assertEqual([row["branch"] for row in result["remaining"]], ["rwgk/dirty"])
        self.assertNotIn("rwgk/finished", self.repo.branches)
        self.assertTrue(all(row[2] for row in self.events if row[0] == "assess"))
        self.assertLess(
            self.events.index(("save", "rwgk/finished")),
            self.events.index(("apply", "rwgk/finished")),
        )

    def test_pair_dirtied_before_apply_is_kept_without_blocking_independent_pair(self):
        self.add("rwgk/dirty-later")
        self.add("rwgk/finished")

        def change(repo, name, count, entry):
            return (
                housekeeping._retain(entry, "dirty", "New uncommitted files")
                if name.endswith("later") and count > 1
                else entry
            )

        self.assess_change = change
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/finished"]
        )
        self.assertEqual(result["remaining"][0]["reasons"][-1]["code"], "dirty")
        self.assertFalse(result["errors"])

    def test_local_commit_created_during_planning_is_not_admitted_to_cleanup(self):
        self.add("rwgk/changed")
        self.plan_change = lambda name, plan: plan.update(local_oid="f" * 40)
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertIn("changed since", result["remaining"][0]["reasons"][-1]["message"])
        self.assertFalse(any(event[0] == "save" for event in self.events))

    def test_changed_pr_identity_state_or_head_between_assessment_and_plan_is_kept(
        self,
    ):
        self.add("rwgk/changed")
        for field, value in (
            ("number", 99),
            ("base_repo", "Other/project"),
            ("head_repo", "Elsewhere/project"),
            ("head_ref", "another"),
            ("head_oid", "f" * 40),
            ("state", "OPEN"),
        ):
            with self.subTest(field=field):
                self.plan_change = lambda name, plan, field=field, value=value: plan[
                    "pr"
                ].update({field: value})
                result = housekeeping.run(self.repo, apply=True)
                self.assertFalse(result["operations"])
                self.assertTrue(result["errors"])
        self.assertFalse(any(event[0] == "save" for event in self.events))

    def test_new_private_module_ref_during_planning_is_kept(self):
        self.add("rwgk/changed")
        self.plan_change = lambda name, plan: plan.update(
            submodules=[
                {
                    "path": "module",
                    "refs": [{"ref": "refs/heads/private", "oid": "f" * 40}],
                }
            ]
        )
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertIn(
            "Submodule history changed",
            result["remaining"][0]["reasons"][-1]["message"],
        )

    def test_fresh_assessment_cannot_replace_the_frozen_local_tip(self):
        self.add("rwgk/changed")

        def change(repo, name, count, entry):
            if count > 1:
                entry["local_oid"] = "f" * 40
            return entry

        self.assess_change = change
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertTrue(result["errors"])

    def test_lookup_error_is_reported_while_unrelated_cleanup_proceeds(self):
        self.add(
            "rwgk/unchecked", eligible=False, reason="PR lookup failed", error=True
        )
        self.add("rwgk/finished")
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/finished"]
        )
        self.assertEqual(
            [row["branch"] for row in result["errors"]], ["rwgk/unchecked"]
        )

    def test_terminal_stack_is_preflighted_and_retired_top_down(self):
        names = ["rwgk/lower", "rwgk/middle", "rwgk/upper"]
        for name in names:
            self.add(name)
        self.stack(*names)
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual([row["branch"] for row in result["operations"]], names[::-1])
        self.assertEqual(
            [event[1] for event in self.events if event[0] == "apply"], names[::-1]
        )
        self.assertFalse(result["remaining"])
        self.assertFalse(hasattr(self.repo, "_wt_retiring"))

    def test_dirty_retained_child_keeps_its_parents_but_not_an_independent_pair(self):
        self.add("rwgk/lower")
        self.add("rwgk/upper", eligible=False, reason="local edits")
        self.add("rwgk/independent")
        self.stack("rwgk/lower", "rwgk/upper")
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/independent"]
        )
        self.assertEqual(
            {row["branch"] for row in result["remaining"]}, {"rwgk/lower", "rwgk/upper"}
        )
        self.assertFalse(result["errors"])

    def test_preflight_failure_removes_dependency_exemption_for_parent(self):
        for name in ("rwgk/lower", "rwgk/upper", "rwgk/independent"):
            self.add(name)
        self.stack("rwgk/lower", "rwgk/upper")
        self.preflight_errors["rwgk/upper"] = WTError(
            "Source identity could not be verified"
        )
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/independent"]
        )
        lower = next(
            row for row in result["remaining"] if row["branch"] == "rwgk/lower"
        )
        self.assertEqual(lower["reasons"][-1]["code"], "stack-dependency")

    def test_failed_child_cleanup_retains_ancestors_and_reports_recovery_path(self):
        for name in ("rwgk/lower", "rwgk/upper", "rwgk/independent"):
            self.add(name)
        self.stack("rwgk/lower", "rwgk/upper")
        self.apply_errors["rwgk/upper"] = WTError(
            "Cleanup interrupted after recovery refs"
        )
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/independent"]
        )
        error = result["errors"][0]
        self.assertEqual(error["branch"], "rwgk/upper")
        self.assertTrue(error["recovery_path"].endswith("rwgk-upper.json"))
        self.assertTrue(Path(error["journal_path"]).is_file())
        self.assertIn("--resume rwgk-upper --apply", error["hints"][0])
        lower = next(
            row for row in result["remaining"] if row["branch"] == "rwgk/lower"
        )
        self.assertEqual(lower["reasons"][-1]["code"], "dependent-cleanup-failed")

    def test_child_reopened_before_apply_keeps_parent_and_allows_unrelated_cleanup(
        self,
    ):
        for name in ("rwgk/lower", "rwgk/upper", "rwgk/independent"):
            self.add(name)
        self.stack("rwgk/lower", "rwgk/upper")

        def change(repo, name, count, entry):
            return (
                housekeeping._retain(entry, "open-pr", "PR reopened")
                if name.endswith("upper") and count > 1
                else entry
            )

        self.assess_change = change
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/independent"]
        )
        self.assertFalse(result["errors"])

    def test_pending_operation_preview_enumerates_without_planning_or_network(self):
        self.add("rwgk/paused")
        self.repo.state["pr_rebase"] = {"phase": "rebasing"}

        def blocked(*args):
            raise WTError("Finish wt pull --continue")

        self.repo.ensure_idle = blocked
        result = housekeeping.run(self.repo)
        self.assertTrue(result["issues"])
        self.assertFalse(result["errors"])
        self.assertEqual(
            [row["branch"] for row in result["remaining"]], ["rwgk/paused"]
        )
        self.assertFalse(self.events)

    def test_global_invariant_failure_prevents_apply_and_reports_an_error(self):
        self.add("rwgk/finished")

        def blocked():
            raise WTError("Primary checkout is on a feature branch")

        self.repo.check_primary = blocked
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertTrue(result["errors"])
        self.assertFalse(self.events)

    def test_inventory_lists_legacy_detached_and_unpaired_without_alias_duplicates(
        self,
    ):
        managed = self.add("rwgk/open", eligible=False)
        for branch, path in (
            ("legacy", self.root / "legacy"),
            (None, self.root / "detached"),
        ):
            path.mkdir()
            self.worktrees.append({"branch": branch, "path": str(path)})
            if branch:
                self.refs[branch] = "e" * 40
        self.refs["unpaired"] = "d" * 40
        (Path(managed["path"]).parent / "pr1").symlink_to(managed["path"])
        result = housekeeping.run(self.repo)
        self.assertEqual(
            {row["kind"] for row in result["remaining"]},
            {"managed", "legacy", "detached", "unpaired"},
        )
        self.assertEqual(len(result["remaining"]), 4)
        self.assertFalse(
            any(row.get("branch") == "main" for row in result["remaining"])
        )
        self.assertFalse(result["operations"])

    def test_unpaired_branch_hints_preserve_literal_branch_names(self):
        name = "-legacy;echo-valuable"
        self.refs[name] = "d" * 40
        result = housekeeping.run(self.repo)
        entry = result["remaining"][0]
        self.assertEqual(entry["branch"], name)
        self.assertEqual(
            shlex.split(entry["hints"][0]), ["wt", "drop", "--local-only", "--", name]
        )

    def test_missing_managed_worktree_is_reported_once_not_as_an_unpaired_branch(self):
        entry = self.add(
            "rwgk/missing", eligible=False, reason="Managed worktree missing"
        )
        self.worktrees[:] = [
            row for row in self.worktrees if row["branch"] != entry["branch"]
        ]
        result = housekeeping.run(self.repo)
        self.assertEqual(len(result["remaining"]), 1)
        self.assertEqual(result["remaining"][0]["kind"], "managed")

    def test_trunk_coverage_cache_is_reset_at_scan_start_and_each_final_assessment(
        self,
    ):
        for name in ("rwgk/first", "rwgk/second"):
            self.add(name)
        self.repo._wt_housekeeping_trunk = "stale-from-prior-command"

        def change(repo, name, count, entry):
            repo._wt_housekeeping_trunk = "fetched-trunk"
            if name.endswith("first") and count > 1:
                return housekeeping._retain(
                    entry, "local-history", "Upstream trunk was rewound"
                )
            return entry

        self.assess_change = change
        result = housekeeping.run(self.repo, apply=True)
        assessments = [event for event in self.events if event[0] == "assess"]
        self.assertFalse(assessments[0][3])
        self.assertFalse(any(event[3] for event in assessments[2:]))
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/second"]
        )
        self.assertEqual(result["remaining"][0]["reasons"][-1]["code"], "local-history")

    def test_assessment_exception_isolated_from_independent_candidate(self):
        self.add("rwgk/broken")
        self.add("rwgk/finished")

        def change(repo, name, count, entry):
            if name.endswith("broken"):
                raise WTError("PR lookup failed")
            return entry

        self.assess_change = change
        result = housekeeping.run(self.repo, apply=True)
        self.assertEqual(
            [row["branch"] for row in result["operations"]], ["rwgk/finished"]
        )
        self.assertEqual(result["errors"][0]["branch"], "rwgk/broken")

    def test_corrupt_stack_catalog_keeps_every_pair(self):
        self.add("rwgk/finished")
        (self.root / "gh-stack").write_text("not json")
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertTrue(result["errors"])
        self.assertEqual(len(result["remaining"]), 1)


@unittest.skipUnless(
    lifecycle_fixtures.WORKTREE_ROOT,
    "Set WT_TEST_WORKTREE_ROOT under /wrk/trees/.wt-tests",
)
class HousekeepingLifecycleTests(unittest.TestCase):
    setUp = lifecycle_fixtures.LifecycleTests.setUp
    git = lifecycle_fixtures.LifecycleTests.git

    def retained_pr(self):
        self.git("push", "--quiet", "upstream", self.tip + ":refs/pull/42/head")

    def test_owned_terminal_pr_uses_archives_backtracking_and_verified_source_deletion(
        self,
    ):
        self.retained_pr()
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["errors"], result["remaining"])
        self.assertEqual(len(result["operations"]), 1)
        record = result["operations"][0]
        self.assertEqual(record["status"], "complete")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.repo.exists("refs/heads/" + self.branch))
        self.assertFalse(
            self.git(
                "ls-remote", "--refs", str(self.upstream), "refs/heads/" + self.branch
            )
        )
        self.assertTrue(
            self.git(
                "ls-remote", "--refs", str(self.origin), "refs/tags/rwgk/archive/*"
            )
        )
        saved = json.loads(Path(record["backtracking_path"]).read_text())
        self.assertEqual(saved["local_oid"], self.tip)
        self.assertEqual(self.repo.oid(saved["snapshots"][0]["recovery_ref"]), self.tip)

    def test_cli_apply_json_routes_cleanup_without_human_output(self):
        self.retained_pr()
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "Repo", return_value=self.repo),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = cli.main(["housekeeping", "--apply", "--json"])
        self.assertEqual((status, stderr.getvalue()), (0, ""))
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["result"], "housekeeping")
        self.assertEqual(len(value["operations"]), 1)
        self.assertEqual(value["operations"][0]["status"], "complete")
        self.assertFalse(value["errors"])
        self.assertFalse(self.path.exists())

    def test_review_retirement_preserves_contributor_source_without_archive_push(self):
        self.retained_pr()
        self.repo.branches[self.branch]["role"] = "review"
        self.repo.save()
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["errors"], result["remaining"])
        self.assertEqual(len(result["operations"]), 1)
        self.assertIn(
            self.tip,
            self.git(
                "ls-remote", "--refs", str(self.upstream), "refs/heads/" + self.branch
            ),
        )
        self.assertFalse(
            self.git(
                "ls-remote", "--refs", str(self.origin), "refs/tags/rwgk/archive/*"
            )
        )
        self.assertFalse(self.path.exists())

    def test_additional_local_commit_is_kept_even_though_explicit_clean_could_archive_it(
        self,
    ):
        self.retained_pr()
        (self.path / "valuable").write_text("unpublished contribution\n")
        self.git("add", "valuable", cwd=self.path)
        self.git("commit", "--quiet", "-m", "Unpublished contribution", cwd=self.path)
        unpublished = self.git("rev-parse", "HEAD", cwd=self.path)
        result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertFalse(result["errors"], result["remaining"])
        self.assertEqual(len(result["remaining"]), 1)
        self.assertEqual(self.repo.oid(self.branch), unpublished)
        self.assertEqual(
            (self.path / "valuable").read_text(), "unpublished contribution\n"
        )
        self.assertIn(
            self.tip,
            self.git(
                "ls-remote", "--refs", str(self.upstream), "refs/heads/" + self.branch
            ),
        )
        self.assertFalse(
            self.git(
                "ls-remote", "--refs", str(self.origin), "refs/tags/rwgk/archive/*"
            )
        )

    def test_apply_reassesses_new_local_commit_before_any_record_or_remote_mutation(
        self,
    ):
        self.retained_pr()
        original = housekeeping._assess
        count = 0

        def changed(repo, branch, discard_builds):
            nonlocal count
            count += 1
            if count == 2:
                (self.path / "valuable").write_text("created after planning\n")
                self.git("add", "valuable", cwd=self.path)
                self.git(
                    "commit", "--quiet", "-m", "Concurrent contribution", cwd=self.path
                )
            return original(repo, branch, discard_builds)

        with mock.patch.object(housekeeping, "_assess", side_effect=changed):
            result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertFalse(result["errors"], result["remaining"])
        self.assertTrue(self.path.is_dir())
        self.assertEqual(
            (self.path / "valuable").read_text(), "created after planning\n"
        )
        self.assertFalse(list((self.root / "backtracking").glob("*.json")))
        self.assertIn(
            self.tip,
            self.git(
                "ls-remote", "--refs", str(self.upstream), "refs/heads/" + self.branch
            ),
        )

    def test_unpaired_branch_with_ambiguous_short_name_is_reported_exactly(self):
        self.retained_pr()
        self.git("branch", "ambiguous", self.tip)
        self.git("tag", "ambiguous", self.tip)
        result = housekeeping.run(self.repo)
        self.assertFalse(result["errors"], result["remaining"])
        self.assertEqual([row["branch"] for row in result["remaining"]], ["ambiguous"])
        self.assertTrue(self.path.is_dir())

    def test_cleanup_failure_keeps_real_recovery_record_and_source_branch(self):
        self.retained_pr()
        with mock.patch.object(
            lifecycle, "_pin", side_effect=WTError("Archive unavailable")
        ):
            result = housekeeping.run(self.repo, apply=True)
        self.assertFalse(result["operations"])
        self.assertEqual(len(result["errors"]), 1)
        entry = result["errors"][0]
        self.assertTrue(Path(entry["recovery_path"]).is_file())
        record = json.loads(Path(entry["recovery_path"]).read_text())
        self.assertEqual(
            self.repo.oid(record["snapshots"][0]["recovery_ref"]), self.tip
        )
        self.assertTrue(self.path.is_dir())
        self.assertIn(
            self.tip,
            self.git(
                "ls-remote", "--refs", str(self.upstream), "refs/heads/" + self.branch
            ),
        )


if __name__ == "__main__":
    unittest.main()
