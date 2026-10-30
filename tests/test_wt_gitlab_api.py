"""GitLab wire contracts with synthetic projects and no network or Git writes."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
from rwgk_wt import gitlab
from rwgk_wt.core import WTError

HOST = "forge.example.invalid"
BASE = f"{HOST}/group/sub/project"
SOURCE = f"{HOST}/ralf/project"
OID = "a" * 40


def project(number, path, **extra):
    return {
        "id": number,
        "path_with_namespace": path,
        "web_url": f"https://{HOST}/{path}",
        "ssh_url_to_repo": f"ssh://git@{HOST}:12051/{path}.git",
        "http_url_to_repo": f"https://{HOST}/{path}.git",
        **extra,
    }


def mr(**extra):
    return {
        "id": 9007,
        "iid": 7,
        "web_url": f"https://{HOST}/group/sub/project/-/merge_requests/7",
        "state": "opened",
        "author": {"username": "ralf"},
        "source_project_id": 20,
        "target_project_id": 10,
        "source_branch": "rwgk/change",
        "target_branch": "main",
        "sha": OID,
        "title": "Draft: Improve behavior",
        "draft": True,
        "merge_commit_sha": None,
        **extra,
    }


class WireRepo:
    def __init__(self):
        self.config = {
            "provider": "gitlab",
            "host": HOST,
            "upstream_repo": BASE,
            "upstream_remote": "upstream",
            "publish_remote": "origin",
            "archive_remote": "origin",
        }
        self.projects = {
            10: project(10, "group/sub/project"),
            20: project(
                20,
                "ralf/project",
                namespace={"kind": "user", "path": "ralf"},
                forked_from_project={"id": 10},
            ),
            30: project(30, "another/project"),
        }
        self.remotes = {
            "upstream": (f"ssh://git@{HOST}:12051/group/sub/project.git",) * 2,
            "origin": (f"ssh://git@{HOST}:12052/ralf/project.git",) * 2,
        }
        self.current = mr()
        self.open = []
        self.calls = []
        self.error = None
        self.invalid_json = False
        self.response_override = None

    def run(self, argv, *, input=None, check=True):
        self.calls.append((list(argv), json.loads(input) if input else None))
        if self.error:
            raise self.error
        if self.invalid_json:
            return subprocess.CompletedProcess(argv, 0, "not json", "")
        if self.response_override is not None:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps(self.response_override), ""
            )
        assert argv[:2] == ["glab", "api"], argv
        assert argv[argv.index("--hostname") + 1] == HOST
        assert "--jq" not in argv
        method = argv[argv.index("--method") + 1]
        endpoint = argv[argv.index("--method") + 2]
        path = urlsplit(endpoint).path
        if path == "user":
            value = {"username": "ralf"}
        elif path.startswith("projects/"):
            key, _, tail = path.removeprefix("projects/").partition("/")
            key = unquote(key)
            number = (
                int(key)
                if key.isdigit()
                else {
                    "group/sub/project": 10,
                    "ralf/project": 20,
                    "another/project": 30,
                }.get(key)
            )
            selected = self.projects.get(number)
            assert selected is not None, endpoint
            if not tail:
                value = selected
            elif tail.startswith("merge_requests/"):
                if method == "PUT":
                    changes = json.loads(input)
                    self.current.update(changes)
                    if "title" in changes:
                        self.current["draft"] = bool(
                            gitlab.DRAFT.match(changes["title"])
                        )
                value = self.current
            elif tail == "merge_requests":
                if method == "POST":
                    changes = json.loads(input)
                    self.current.update(changes)
                    self.current["source_project_id"] = selected["id"]
                    self.current["draft"] = bool(gitlab.DRAFT.match(changes["title"]))
                    value = self.current
                else:
                    value = self.open
            else:
                raise AssertionError(endpoint)
        else:
            raise AssertionError(endpoint)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    def git(self, *args):
        if args == ("remote",):
            return "\n".join(self.remotes)
        if args[:3] == ("remote", "get-url", "--all"):
            return self.remotes[args[-1]][1 if "--push" in args else 0]
        raise AssertionError(args)


class GitLabAPITests(unittest.TestCase):
    def setUp(self):
        self.repo = WireRepo()

    def test_identity_retains_nested_namespace_but_not_ssh_port(self):
        for url in (
            f"ssh://git@{HOST}:12051/group/sub/project.git",
            f"git@{HOST}:group/sub/project.git",
            f"https://{HOST}/group/sub/project.git",
        ):
            with self.subTest(url=url):
                self.assertEqual(gitlab.parse_identity(url), BASE)
        self.assertEqual(
            gitlab.parse_identity("https://gitlab.com/group/project"),
            "gitlab.com/group/project",
        )

    def test_identity_rejects_local_github_credential_and_ambiguous_endpoints(self):
        for url in (
            "/tmp/project",
            "file:///tmp/project",
            "https://github.com/owner/repo",
            f"https://token@{HOST}/group/project",
            f"https://{HOST}/group/../project",
            f"https://{HOST}/group/project?other=identity",
            f"ssh://git@{HOST}:bad/group/project",
            f"git://{HOST}/group/project",
            f"https://{HOST}/group//project",
        ):
            with self.subTest(url=url):
                self.assertIsNone(gitlab.parse_identity(url))

    def test_selector_uses_iid_and_exact_target(self):
        for value in (
            7,
            "7",
            "pr7",
            "mr7",
            "!7",
            f"https://{HOST}/group/sub/project/-/merge_requests/7",
        ):
            self.assertEqual(gitlab.selector(self.repo, value), 7)
        for value in (
            0,
            True,
            "9007x",
            f"https://{HOST}/another/project/-/merge_requests/7",
            "https://other.invalid/group/sub/project/-/merge_requests/7",
        ):
            with self.assertRaises(WTError):
                gitlab.selector(self.repo, value)

    def test_api_serializes_literal_json_without_jq_or_shell(self):
        body = {"description": "first line\n`literal` $(also literal)"}
        gitlab.api(
            self.repo, "projects/group%2Fsub%2Fproject/merge_requests/7", "PUT", body
        )
        argv, payload = self.repo.calls[-1]
        self.assertEqual(payload, body)
        self.assertEqual(argv[-2:], ["--input", "-"])
        self.assertEqual(
            argv[:6], ["glab", "api", "--hostname", HOST, "--method", "PUT"]
        )
        self.assertNotIn("--jq", argv)

    def test_api_failures_explain_glab_auth_and_invalid_json(self):
        for error in (OSError("missing"), WTError("authentication failed")):
            self.repo.error = error
            with self.assertRaisesRegex(
                WTError, "Install glab.*glab auth login --hostname"
            ):
                gitlab.viewer(self.repo)
        self.repo.error = None
        self.repo.invalid_json = True
        with self.assertRaisesRegex(WTError, "invalid JSON"):
            gitlab.viewer(self.repo)

    def test_get_mr_normalizes_host_project_ids_and_iid(self):
        result = gitlab.get_pr(self.repo, 7)
        self.assertEqual(result["number"], 7)
        self.assertEqual(result["provider"], "gitlab")
        self.assertEqual(result["host"], HOST)
        self.assertEqual(result["base_project_id"], 10)
        self.assertEqual(result["head_project_id"], 20)
        self.assertEqual(result["head_repo"], SOURCE)
        self.assertIn(":12051/", result["head_url"])
        self.assertEqual(result["state"], "OPEN")
        self.assertTrue(result["is_draft"])
        endpoints = [argv[6] for argv, _ in self.repo.calls]
        self.assertEqual(
            endpoints,
            [
                "projects/group%2Fsub%2Fproject",
                "projects/group%2Fsub%2Fproject/merge_requests/7",
                "projects/20",
            ],
        )

    def test_mr_lookup_refuses_another_target_before_api_requests(self):
        for identity in (
            f"{HOST}/another/project",
            "other.invalid/group/sub/project",
            "group/sub/project",
            True,
        ):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(WTError, "configured upstream"):
                    gitlab.get_pr(self.repo, 7, identity)
                self.assertEqual(self.repo.calls, [])

    def test_merged_and_legacy_wip_state_are_supported(self):
        self.repo.current = mr(
            state="merged", draft=False, title="WIP: Legacy", squash_commit_sha="b" * 40
        )
        result = gitlab.get_pr(self.repo, 7)
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["merge_oid"], "b" * 40)
        self.assertTrue(result["is_draft"])

    def test_deleted_source_stays_readable_by_receiving_mr_ref(self):
        self.repo.current["source_project_id"] = None
        result = gitlab.get_pr(self.repo, 7)
        self.assertIsNone(result["head_repo"])
        self.assertIsNone(result["head_url"])
        self.assertEqual(result["head_oid"], OID)

    def test_wrong_mr_identity_and_pending_sha_are_refused(self):
        for changes in (
            {"iid": 9007},
            {"target_project_id": 99},
            {"source_project_id": True},
            {"sha": None},
            {"sha": "not-a-sha"},
            {"author": {"username": ""}},
            {"source_branch": "invalid:branch"},
            {"target_branch": "../outside"},
            {"source_branch": "hidden\nbranch"},
            {"draft": "false"},
            {"web_url": f"https://{HOST}/another/project/-/merge_requests/7"},
        ):
            with self.subTest(changes=changes):
                self.repo.current = mr(**changes)
                with self.assertRaises(WTError):
                    gitlab.get_pr(self.repo, 7)

    def test_wrong_project_identity_or_transport_is_refused(self):
        for changes in (
            {"id": 99},
            {"path_with_namespace": "another/project"},
            {"ssh_url_to_repo": "git@other.invalid:group/sub/project.git"},
            {"web_url": "https://other.invalid/group/sub/project"},
        ):
            with self.subTest(changes=changes):
                self.repo.projects[10] = project(10, "group/sub/project", **changes)
                with self.assertRaises(WTError):
                    gitlab.get_pr(self.repo, 7)
        self.repo.projects[10] = project(10, "group/sub/project")
        self.repo.projects[20]["id"] = 99
        with self.assertRaisesRegex(WTError, "project identity"):
            gitlab.get_pr(self.repo, 7)

    def test_source_transport_preserves_configured_ssh_port(self):
        self.assertEqual(
            gitlab.source_endpoint(self.repo, SOURCE, push=True),
            f"ssh://git@{HOST}:12052/ralf/project.git",
        )
        self.repo.remotes.pop("origin")
        self.assertEqual(
            gitlab.source_endpoint(self.repo, SOURCE),
            f"ssh://git@{HOST}:12051/ralf/project.git",
        )

    def test_source_transport_rejects_recreated_project_with_same_path(self):
        self.repo.projects[20]["id"] = 99
        with self.assertRaisesRegex(WTError, "source project identity changed"):
            gitlab.source_endpoint(self.repo, SOURCE, push=True, expected_id=20)

    def test_source_transport_rejects_mismatched_push_project(self):
        self.repo.remotes["origin"] = (
            f"ssh://git@{HOST}:12052/ralf/project.git",
            f"ssh://git@{HOST}:12052/another/project.git",
        )
        with self.assertRaisesRegex(WTError, "different fetch/push projects"):
            gitlab.source_endpoint(self.repo, SOURCE, push=True)

    def test_find_open_matches_source_project_not_branch_name_alone(self):
        self.repo.open = [mr(), mr(iid=8, source_project_id=30)]
        found = gitlab.find_open(self.repo, SOURCE, "rwgk/change")
        self.assertEqual([item["number"] for item in found], [7])
        endpoint = self.repo.calls[-2][0][6]
        self.assertIn("scope=all", endpoint)
        self.assertIn("source_branch=rwgk%2Fchange", endpoint)
        self.repo.open = [mr()] * 100
        with self.assertRaisesRegex(WTError, "pagination"):
            gitlab.find_open(self.repo, SOURCE, "rwgk/change")

    def test_existing_update_uses_target_iid_and_preserves_draft(self):
        existing = gitlab.get_pr(self.repo, 7)
        entry = {"branch": "rwgk/change", "base": "main", "pr": existing}
        updated = gitlab.update_pr(
            self.repo, entry, BASE, SOURCE, "Retitled", "line1\nline2", False
        )
        self.assertTrue(updated["is_draft"])
        argv, payload = next(
            call for call in reversed(self.repo.calls) if call[0][5] == "PUT"
        )
        self.assertEqual(argv[6], "projects/group%2Fsub%2Fproject/merge_requests/7")
        self.assertEqual(
            payload, {"title": "Draft: Retitled", "description": "line1\nline2"}
        )
        updated = gitlab.update_pr(
            self.repo, {**entry, "pr": updated}, BASE, SOURCE, None, None, True
        )
        self.assertFalse(updated["is_draft"])
        self.assertEqual(updated["title"], "Retitled")

    def test_create_fork_mr_posts_to_source_with_explicit_target(self):
        entry = {
            "branch": "rwgk/change",
            "base": "main",
            "pr": None,
            "auto_text": ("Create behavior", "details"),
        }
        result = gitlab.update_pr(self.repo, entry, BASE, SOURCE, None, None, False)
        self.assertTrue(result["is_draft"])
        argv, payload = next(call for call in self.repo.calls if call[0][5] == "POST")
        self.assertEqual(argv[6], "projects/ralf%2Fproject/merge_requests")
        self.assertEqual(payload["target_project_id"], 10)
        self.assertEqual(payload["source_branch"], "rwgk/change")
        self.assertEqual(payload["title"], "Draft: Create behavior")

    def test_archive_requires_owned_fork_family(self):
        self.assertEqual(gitlab.validate_archive_remote(self.repo), SOURCE)
        self.repo.projects[20].pop("forked_from_project")
        with self.assertRaisesRegex(WTError, "does not belong"):
            gitlab.validate_archive_remote(self.repo)
        self.repo.projects[20]["forked_from_project"] = {"id": 10}
        self.repo.projects[20]["namespace"]["path"] = "someoneelse"
        with self.assertRaisesRegex(WTError, "owned or maintained"):
            gitlab.validate_archive_remote(self.repo)

    def test_retirement_checks_active_mr_source_and_same_project_base(self):
        saved = gitlab.get_pr(self.repo, 7)
        self.repo.open = [mr()]
        with self.assertRaisesRegex(WTError, "Open MR !7"):
            gitlab.ensure_source_unused(self.repo, saved)
        self.repo.open = [mr(source_project_id=30)]
        gitlab.ensure_source_unused(self.repo, saved)
        self.repo.current = mr(source_project_id=10)
        saved = gitlab.get_pr(self.repo, 7)
        self.repo.open = [mr(iid=8, source_project_id=30, target_branch="rwgk/change")]
        with self.assertRaisesRegex(WTError, "Open MR !8"):
            gitlab.ensure_source_unused(self.repo, saved)


if __name__ == "__main__":
    unittest.main()
