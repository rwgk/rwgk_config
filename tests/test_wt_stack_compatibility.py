"""Version requirements and retarget consent stop unsafe stack mutations early."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import cli, stacks
from rwgk_wt.core import WTError
from rwgk_wt.stack_version import require_supported


def version_result(version="0.3.0", status=0):
    return subprocess.CompletedProcess(
        ["gh"], status, f"gh stack version {version}\n", ""
    )


class ExtensionVersionTests(unittest.TestCase):
    def test_supported_and_future_semver_releases(self):
        for version in (
            "0.3.0",
            "v0.3.0",
            "0.3.0+build.1",
            "0.4.0",
            "1.0.0",
            "0.4.0-rc.1",
        ):
            with self.subTest(version=version):
                run = Mock(return_value=version_result(version))
                self.assertGreaterEqual(require_supported(run), (0, 3, 0))
                run.assert_called_once_with(["gh", "stack", "--version"], check=False)

    def test_old_minimum_prerelease_and_malformed_versions_explain_upgrade(self):
        for version in (
            "0.2.1",
            "0.3.0-rc.1",
            "development",
            "0.30",
            "0.4.0-01",
            "0.03.0",
        ):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(WTError, "gh extension upgrade stack"),
            ):
                require_supported(Mock(return_value=version_result(version)))

    def test_missing_cli_and_extension_explain_install(self):
        for run in (
            Mock(side_effect=FileNotFoundError("gh")),
            Mock(side_effect=WTError("Cannot run gh")),
            Mock(return_value=version_result(status=1)),
        ):
            with (
                self.subTest(run=run),
                self.assertRaisesRegex(
                    WTError, "Install GitHub CLI.*gh extension install github/gh-stack"
                ),
            ):
                require_supported(run)

    def test_success_is_quiet_and_cached_per_repo_only(self):
        repo = SimpleNamespace(run=Mock(return_value=version_result()))
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            stacks.require_extension(repo)
            stacks.require_extension(repo)
        repo.run.assert_called_once()
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        other = SimpleNamespace(run=Mock(return_value=version_result("0.2.1")))
        with self.assertRaises(WTError):
            stacks.require_extension(other)

    def test_failed_check_is_not_cached(self):
        repo = SimpleNamespace(
            run=Mock(side_effect=[version_result("0.2.1"), version_result()])
        )
        with self.assertRaises(WTError):
            stacks.require_extension(repo)
        stacks.require_extension(repo)
        self.assertEqual(repo.run.call_count, 2)

    def test_old_extension_stops_each_write_entry_before_repo_mutations(self):
        for invoke in (
            lambda repo: stacks.initialize(repo),
            lambda repo: stacks.add(repo, "next"),
            lambda repo: stacks.rebase(repo),
            lambda repo: stacks.pull(repo, apply=True),
        ):
            with self.subTest(invoke=invoke):
                repo = SimpleNamespace(run=Mock(return_value=version_result("0.2.1")))
                with self.assertRaisesRegex(WTError, "too old"):
                    invoke(repo)
                repo.run.assert_called_once_with(
                    ["gh", "stack", "--version"], check=False
                )

    def test_local_navigation_and_help_do_not_check_extension(self):
        stack = {"branches": [{"branch": "rwgk/lower"}, {"branch": "rwgk/upper"}]}
        repo = SimpleNamespace(
            resolve=lambda selector=None: selector or "rwgk/lower",
            owner=lambda branch: Path("/trees") / branch,
            branches={"rwgk/upper": {"path": "/trees/rwgk/upper"}},
        )
        with (
            patch.object(stacks, "get_stack", return_value=stack),
            patch.object(
                stacks,
                "require_extension",
                side_effect=AssertionError("unexpected version check"),
            ),
        ):
            self.assertEqual(stacks.navigate(repo, "up"), Path("/trees/rwgk/upper"))
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                cli.parser().parse_args(["stack", "rebase", "--help"])
            self.assertEqual(stopped.exception.code, 0)


class RetargetTests(unittest.TestCase):
    def setUp(self):
        self.stack = {
            "number": 10,
            "id": "9876",
            "trunk": {"branch": "old-base"},
            "branches": [{"branch": "rwgk/lower", "base": "a" * 40}],
        }
        self.native = {"number": 10, "id": 9876, "open": True, "base": {"ref": "main"}}
        self.repo = SimpleNamespace(
            config={
                "trunk": "main",
                "upstream_repo": "owner/repo",
                "upstream_remote": "upstream",
            },
            run=Mock(return_value=subprocess.CompletedProcess([], 0, "", "")),
            state={},
            primary=Path("/primary"),
            oid=lambda ref: "a" * 40,
            exists=lambda ref: True,
            ensure_clean=Mock(),
            check_primary=Mock(),
        )

    def test_native_base_checks_identity_and_validates_branch(self):
        with patch("rwgk_wt.github._api", return_value=self.native) as api:
            self.assertEqual(stacks.native_base(self.repo, self.stack), "main")
        api.assert_called_once_with(self.repo, "repos/owner/repo/stacks/10")
        self.repo.run.assert_called_once_with(
            ["git", "check-ref-format", "refs/heads/main"], check=False
        )

    def test_number_only_supported_and_unpublished_does_not_call_api(self):
        with patch("rwgk_wt.github._api", return_value=self.native) as api:
            self.assertEqual(stacks.native_base(self.repo, {"number": 10}), "main")
            api.reset_mock()
            self.assertIsNone(stacks.native_base(self.repo, {}))
            api.assert_not_called()

    def test_unknown_identity_api_failure_and_invalid_branch_refuse(self):
        for metadata in (
            {"id": "9876"},
            {"number": "10"},
            {"number": 10, "id": "other"},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(WTError):
                stacks.native_base(self.repo, metadata)
        for native in (
            {**self.native, "id": 1},
            {**self.native, "number": 11},
            {**self.native, "base": {}},
            {**self.native, "open": False},
        ):
            with (
                self.subTest(native=native),
                patch("rwgk_wt.github._api", return_value=native),
                self.assertRaises(WTError),
            ):
                stacks.native_base(self.repo, self.stack)
        with (
            patch("rwgk_wt.github._api", side_effect=WTError("API unavailable")),
            self.assertRaisesRegex(WTError, "API unavailable"),
        ):
            stacks.native_base(self.repo, self.stack)
        self.repo.run.return_value.returncode = 1
        with (
            patch("rwgk_wt.github._api", return_value=self.native),
            self.assertRaisesRegex(WTError, "invalid.*base"),
        ):
            stacks.native_base(self.repo, self.stack)

    def test_unsupported_target_refuses_before_submodule_or_recovery_writes(self):
        with (
            patch.object(stacks, "require_extension"),
            patch.object(stacks, "get_stack", return_value=self.stack),
            patch.object(stacks, "native_base", return_value="unexpected"),
            patch.object(stacks, "_sync_submodules") as sync,
            patch.object(stacks, "_command") as command,
            patch("rwgk_wt.lifecycle.protect_local") as protect,
            self.assertRaisesRegex(WTError, "registered trunk"),
        ):
            stacks.rebase(self.repo, ["--accept-base-change"])
        sync.assert_not_called()
        command.assert_not_called()
        protect.assert_not_called()
        self.assertEqual(self.repo.state, {})

    def test_registered_target_uses_existing_owner_and_submodule_guards_and_forwards_consent(
        self,
    ):
        events = []
        with (
            patch.object(stacks, "require_extension"),
            patch.object(stacks, "get_stack", return_value=self.stack),
            patch.object(stacks, "native_base", return_value="main"),
            patch.object(stacks, "_sync_submodules"),
            patch.object(
                stacks, "_owners", side_effect=lambda *args: events.append("owners")
            ),
            patch(
                "rwgk_wt.submodule_rebase.before",
                side_effect=lambda *args: events.append("submodules") or [],
            ),
            patch(
                "rwgk_wt.lifecycle.protect_local",
                side_effect=lambda *args, **kwargs: events.append("preserved"),
            ),
            patch.object(
                stacks, "_command", side_effect=lambda *args: events.append("rebase")
            ) as command,
        ):
            stacks.rebase(self.repo, ["--accept-base-change"])
        self.assertEqual(events, ["owners", "submodules", "preserved", "rebase"])
        command.assert_called_once_with(
            self.repo, "rebase", "--remote", "upstream", "--accept-base-change"
        )
        self.repo.ensure_clean.assert_called_once_with(self.repo.primary)

    def test_continue_abort_and_no_trunk_do_not_requery_target(self):
        for flag in ("--continue", "--abort", "--no-trunk"):
            with (
                self.subTest(flag=flag),
                patch.object(stacks, "require_extension"),
                patch.object(stacks, "get_stack", return_value=self.stack),
                patch.object(
                    stacks,
                    "native_base",
                    side_effect=AssertionError("unexpected native-base lookup"),
                ),
                patch.object(stacks, "_sync_submodules"),
                patch.object(stacks, "_owners"),
                patch("rwgk_wt.submodule_rebase.before", return_value=[]),
                patch("rwgk_wt.lifecycle.protect_local"),
                patch.object(stacks, "_command"),
            ):
                stacks.rebase(self.repo, [flag])

    def test_consent_invalid_with_recovery_or_no_trunk(self):
        for flag in ("--continue", "--abort", "--no-trunk"):
            with (
                self.subTest(flag=flag),
                patch.object(stacks, "require_extension") as version,
            ):
                with self.assertRaisesRegex(WTError, "fresh rebase"):
                    stacks.rebase(self.repo, ["--accept-base-change", flag])
                version.assert_not_called()

    def test_cli_forwards_explicit_consent_and_rejects_invalid_combinations(self):
        with patch.object(stacks, "rebase", return_value={}) as rebase:
            cli._dispatch(
                self.repo,
                cli.parser().parse_args(["stack", "rebase", "--accept-base-change"]),
            )
            rebase.assert_called_once_with(self.repo, ["--accept-base-change"])
            for flag in ("--continue", "--abort", "--no-trunk"):
                with self.subTest(flag=flag), self.assertRaises(WTError):
                    cli._dispatch(
                        self.repo,
                        cli.parser().parse_args(
                            ["stack", "rebase", "--accept-base-change", flag]
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
