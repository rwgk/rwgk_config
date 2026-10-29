"""Retiring a layer must leave surviving stack workflows usable."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import stacks
from rwgk_wt.core import WTError


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.common = Path(temporary.name).resolve()
        self.lower = {"branch": "rwgk/lower", "pullRequest": {"merged": False}}
        self.upper = {"branch": "rwgk/upper", "pullRequest": {"merged": False}}
        self.repo = SimpleNamespace(
            common=self.common,
            branches={
                "rwgk/lower": {"path": str(self.common / "lower")},
                "rwgk/upper": {"path": str(self.common / "upper")},
            },
            current_branch=lambda: "rwgk/lower",
            resolve=lambda selector=None: selector or "rwgk/lower",
            check_primary=lambda: None,
            ensure_clean=lambda path: None,
            exists=lambda ref: False,
        )
        self.repo.owner = lambda name: Path(self.repo.branches[name]["path"])
        self.write([self.lower, self.upper])

    def write(self, rows):
        self.data = {
            "schemaVersion": 1,
            "stacks": [
                {"id": "stack-one", "trunk": {"branch": "main"}, "branches": rows}
            ],
        }
        (self.common / "gh-stack").write_text(json.dumps(self.data))

    def remove(self, name):
        stacks.retired(self.repo, name)
        self.repo.branches.pop(name, None)

    def test_unmanaged_descendant_blocks_parent_retirement_even_with_missing_ref(self):
        self.repo.branches.pop("rwgk/upper")
        for present in (False, True):
            with self.subTest(descendant_ref_exists=present):
                self.repo.exists = lambda ref, present=present: present
                with self.assertRaisesRegex(WTError, "active descendants"):
                    stacks.check_retirement(self.repo, "rwgk/lower")

    def test_top_retirement_preserves_unmanaged_lower_layer(self):
        self.repo.branches.clear()
        self.repo.exists = lambda ref: ref == "refs/heads/rwgk/lower"
        self.remove("rwgk/upper")
        stack = stacks.get_stack(self.repo, "rwgk/lower")
        self.assertEqual(stack["branches"], [self.lower])

    def test_missing_unmerged_legacy_layer_is_preserved_in_catalog(self):
        self.repo.branches.clear()
        self.remove("rwgk/upper")
        stack = stacks.get_stack(self.repo, "rwgk/lower")
        self.assertEqual(stack["branches"], [self.lower])

    def test_merged_tombstone_is_retained_for_unmanaged_child(self):
        self.repo.branches.clear()
        self.lower["pullRequest"]["merged"] = True
        self.write([self.lower, self.upper])
        self.remove("rwgk/lower")
        stack = stacks.get_stack(self.repo, "rwgk/upper")
        self.assertEqual(stack["branches"], [self.lower, self.upper])

    def test_merged_legacy_layer_with_local_ref_is_preserved(self):
        self.repo.branches.clear()
        self.lower["pullRequest"]["merged"] = True
        self.write([self.lower, self.upper])
        self.repo.exists = lambda ref: ref == "refs/heads/rwgk/lower"
        self.remove("rwgk/upper")
        stack = stacks.get_stack(self.repo, "rwgk/lower")
        self.assertEqual(stack["branches"], [self.lower])

    def test_closed_unmerged_top_can_be_replaced_by_a_new_layer(self):
        self.remove("rwgk/upper")
        stack = stacks.get_stack(self.repo)
        self.assertEqual([row["branch"] for row in stack["branches"]], ["rwgk/lower"])
        stacks._owners(self.repo, stack)
        new_path = self.common / "replacement"
        with (
            patch.object(
                self.repo, "create", create=True, return_value=new_path
            ) as create,
            patch.object(stacks, "_command") as command,
            patch.object(stacks, "require_extension"),
        ):
            self.assertEqual(stacks.add(self.repo, "replacement"), new_path)
        create.assert_called_once_with("rwgk/replacement", "refs/heads/rwgk/lower")
        self.assertEqual(command.call_args.args[1:], ("add", "rwgk/replacement"))

    def test_merged_lower_tombstone_is_retained_for_surviving_child(self):
        self.lower["pullRequest"]["merged"] = True
        self.write([self.lower, self.upper])
        self.remove("rwgk/lower")
        self.assertEqual(
            [
                row["branch"]
                for row in stacks.catalog(self.repo)["stacks"][0]["branches"]
            ],
            ["rwgk/lower", "rwgk/upper"],
        )
        stacks._owners(self.repo, stacks.get_stack(self.repo, "rwgk/upper"))

    def test_merged_top_tombstone_is_unnecessary_for_surviving_parent(self):
        self.upper["pullRequest"]["merged"] = True
        self.write([self.lower, self.upper])
        self.remove("rwgk/upper")
        self.assertEqual(stacks.get_stack(self.repo)["branches"], [self.lower])

    def test_last_managed_layer_removes_stack_and_old_tombstones(self):
        self.lower["pullRequest"]["merged"] = True
        self.write([self.lower, self.upper])
        self.remove("rwgk/lower")
        self.remove("rwgk/upper")
        self.assertEqual(stacks.catalog(self.repo)["stacks"], [])

    def test_unrelated_stack_is_preserved(self):
        other = {
            "id": "other",
            "trunk": {"branch": "master"},
            "branches": [{"branch": "legacy"}],
        }
        self.data["stacks"].append(other)
        (self.common / "gh-stack").write_text(json.dumps(self.data))
        self.remove("rwgk/upper")
        self.assertEqual(stacks.catalog(self.repo)["stacks"][1], other)

    def test_reentrant_lock_still_excludes_another_repo_instance(self):
        other = SimpleNamespace(common=self.common)
        with stacks.catalog_lock(self.repo):
            with stacks.catalog_lock(self.repo):
                self.assertEqual(self.repo._wt_stack_lock_depth, 2)
                with self.assertRaises(WTError), stacks.catalog_lock(other):
                    self.fail("a second Repo instance acquired the stack lock")
            self.assertEqual(self.repo._wt_stack_lock_depth, 1)
        self.assertEqual(self.repo._wt_stack_lock_depth, 0)
        with stacks.catalog_lock(other):
            self.assertEqual(other._wt_stack_lock_depth, 1)

    def test_lock_depth_is_reset_after_body_failure(self):
        with (
            self.assertRaisesRegex(RuntimeError, "fixture"),
            stacks.catalog_lock(self.repo),
            stacks.catalog_lock(self.repo),
        ):
            raise RuntimeError("fixture")
        self.assertEqual(self.repo._wt_stack_lock_depth, 0)
        with stacks.catalog_lock(SimpleNamespace(common=self.common)):
            pass


if __name__ == "__main__":
    unittest.main()
