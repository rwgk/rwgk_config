"""Batch retirement follows live terminal state and preserves surviving stacks."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli, stacks
from rwgk_wt.core import WTError


class BatchCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.common = Path(temporary.name).resolve()
        self.names = ["rwgk/lower", "rwgk/middle", "rwgk/upper"]
        self.repo = SimpleNamespace(
            state={},
            common=self.common,
            primary=self.common,
            branches={
                name: {"pr": {"number": number, "base_repo": "owner/repo"}}
                for number, name in enumerate(self.names, 1)
            },
            resolve=lambda selector=None: selector or self.names[0],
        )
        self.before = {
            "schemaVersion": 1,
            "stacks": [
                {
                    "id": "one",
                    "trunk": {"branch": "main"},
                    # The cached flags deliberately predate GitHub's merges.
                    "branches": [
                        {"branch": name, "pullRequest": {"merged": False}}
                        for name in self.names
                    ],
                }
            ],
        }
        (self.common / "gh-stack").write_text(json.dumps(self.before))
        self.states = {1: "MERGED", 2: "CLOSED", 3: "MERGED"}
        self.calls = []

    def retire(self, repo, name, apply=False, **options):
        self.calls.append((name, apply))
        if apply:
            self.assertFalse(hasattr(repo, "_wt_retiring"))
        stacks.check_retirement(repo, name)
        if apply:
            stacks.retired(repo, name)
            repo.branches.pop(name)
        return {"id": name, "branch": name}

    def dispatch(self, *flags):
        args = cli.parser().parse_args(["clean", *flags])
        with (
            patch.object(
                cli.github,
                "get_pr",
                side_effect=lambda repo, number, base_repo: {
                    "number": number,
                    "state": self.states[number],
                },
            ),
            patch.object(cli.lifecycle, "retire", side_effect=self.retire),
        ):
            return cli._dispatch(self.repo, args)

    def test_preview_all_terminal_layers_without_mutation(self):
        original = copy.deepcopy(self.repo.branches)
        result, path = self.dispatch()
        self.assertEqual(
            [row["branch"] for row in result["operations"]], self.names[::-1]
        )
        self.assertEqual(self.calls, [(name, False) for name in self.names[::-1]])
        self.assertEqual(self.repo.branches, original)
        self.assertEqual(stacks.catalog(self.repo), self.before)
        self.assertIsNone(path)
        self.assertFalse(hasattr(self.repo, "_wt_retiring"))

    def test_apply_preflights_every_layer_then_removes_top_down(self):
        result, path = self.dispatch("--apply")
        expected = [(name, False) for name in self.names[::-1]] + [
            (name, True) for name in self.names[::-1]
        ]
        self.assertEqual(self.calls, expected)
        self.assertEqual(result["result"], "retired")
        self.assertEqual(self.repo.branches, {})
        self.assertEqual(stacks.catalog(self.repo)["stacks"], [])
        self.assertEqual(path, self.repo.primary)

    def test_open_surviving_child_blocks_all_deletions(self):
        self.states[3] = "OPEN"
        with self.assertRaisesRegex(WTError, "active descendants"):
            self.dispatch("--apply")
        self.assertTrue(self.calls)
        self.assertFalse(any(apply for name, apply in self.calls))
        self.assertEqual(list(self.repo.branches), self.names)
        self.assertFalse(hasattr(self.repo, "_wt_retiring"))

    def test_explicit_lower_target_still_requires_its_descendants(self):
        with self.assertRaisesRegex(WTError, "active descendants"):
            self.dispatch("rwgk/lower", "--apply")
        self.assertFalse(any(apply for name, apply in self.calls))


class AdoptionPreservationTests(unittest.TestCase):
    def test_apply_preserves_before_adoption_and_queues_old_published_tip(self):
        with tempfile.TemporaryDirectory() as directory:
            common = Path(directory)
            branch = "rwgk/lower"
            old, target = "a" * 40, "b" * 40
            catalog = {
                "schemaVersion": 1,
                "stacks": [
                    {
                        "id": "one",
                        "number": 123,
                        "trunk": {"branch": "main"},
                        "branches": [
                            {
                                "branch": branch,
                                "pullRequest": {"number": 1, "merged": False},
                            }
                        ],
                    }
                ],
            }
            (common / "gh-stack").write_text(json.dumps(catalog))
            events = []
            metadata = {"publication": {"oid": old}}
            repo = SimpleNamespace(
                common=common,
                root=common,
                config={
                    "trunk": "main",
                    "upstream_repo": "owner/repo",
                    "upstream_remote": "upstream",
                },
                branches={branch: metadata},
                current_branch=lambda: branch,
                exists=lambda ref: True,
                oid=lambda ref: old if not events else target,
                save=lambda: None,
                alias=lambda name, pr: None,
            )
            result = {
                "rows": [
                    {"branch": "main", "old": old, "target": target},
                    {"branch": branch, "old": old, "target": target},
                ]
            }

            def adopt(argv):
                self.assertEqual(events, ["preserved"])
                events.append("adopted")
                return SimpleNamespace(stdout=json.dumps(result))

            repo.run = adopt
            with (
                patch.object(stacks, "require_extension"),
                patch.object(stacks, "_owners"),
                patch.object(stacks, "_sync_submodules"),
                patch(
                    "rwgk_wt.lifecycle.protect_local",
                    side_effect=lambda *args, **kwargs: events.append("preserved"),
                ) as protect,
                patch("rwgk_wt.github.get_pr", return_value={"number": 1}),
            ):
                stacks.pull(repo, apply=True)
            protect.assert_called_once_with(
                repo, [{"oid": old, "label": branch}], kind="stack-pull"
            )
            self.assertEqual(
                metadata["pending_archive"],
                [{"oid": old, "label": "pre-adoption-" + branch}],
            )
            self.assertEqual(metadata["publication"]["oid"], target)


if __name__ == "__main__":
    unittest.main()
