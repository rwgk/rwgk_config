"""Auditable retirement of an exact remote branch without a local pair."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import quote, urlencode

from . import github, lifecycle, stacks
from .core import WTError


def _branch(repo, value):
    branch = value.removeprefix("refs/heads/")
    if value.startswith("refs/") and not value.startswith("refs/heads/"):
        raise WTError("Remote retirement accepts only an exact branch, not another ref")
    if lifecycle._git_result(
        repo, "check-ref-format", "refs/heads/" + branch
    ).returncode:
        raise WTError(f"Invalid remote branch name: {value!r}")
    return branch


def _source(repo, remote):
    if github.provider(repo) != "github":
        raise WTError("Remote-only retirement currently supports GitHub repositories")
    canonical = repo.config["upstream_repo"]
    github._checked_remote(repo, repo.config["upstream_remote"], canonical)
    identity = github._checked_remote(repo, remote)
    data = github._api(repo, f"repos/{identity}")
    if (
        not isinstance(data, dict)
        or data.get("full_name", "").lower() != identity.lower()
    ):
        raise WTError("GitHub returned an inconsistent remote repository identity")
    if identity.lower() != canonical.lower():
        viewer = github._viewer(repo)
        family = {
            (data.get(key) or {}).get("full_name", "").lower()
            for key in ("parent", "source")
        }
        owner = (data.get("owner") or {}).get("login", "")
        if (
            not data.get("fork")
            or canonical.lower() not in family
            or owner.lower() != viewer.lower()
        ):
            raise WTError(
                "Remote retirement requires the canonical repository or your own related fork"
            )
    default = data.get("default_branch")
    if not isinstance(default, str) or not default:
        raise WTError("GitHub did not identify the remote's default branch")
    return {
        "remote": remote,
        "repo": identity,
        "url": lifecycle._remote_url(repo, remote, push=True),
    }, default


def _open_pulls(repo, identity, **filters):
    for page in range(1, 11):
        query = urlencode(dict(state="open", per_page=100, page=page, **filters))
        values = github._api(repo, f"repos/{identity}/pulls?{query}")
        if not isinstance(values, list):
            raise WTError("Open PR lookup was incomplete; refusing remote retirement")
        for item in values:
            if not isinstance(item, dict) or item.get("state") != "open":
                raise WTError("GitHub returned inconsistent open PR data")
            yield item
        if len(values) < 100:
            return
    raise WTError("Open PR lookup exceeded its page limit; refusing remote retirement")


def _unused(repo, source, branch):
    canonical = repo.config["upstream_repo"]
    identity = source["repo"]
    owner = identity.split("/", 1)[0]
    for target in sorted({canonical, identity}, key=str.lower):
        for item in _open_pulls(repo, target, head=f"{owner}:{branch}"):
            head = item.get("head") or {}
            name = (head.get("repo") or {}).get("full_name")
            if not isinstance(name, str) or not isinstance(head.get("ref"), str):
                raise WTError("Open PR source identity was incomplete")
            if name.lower() == identity.lower() and head["ref"] == branch:
                raise WTError(
                    f"Open PR #{item.get('number')} still uses {identity}:{branch} as its head"
                )
    for item in _open_pulls(repo, identity, base=branch):
        base = item.get("base") or {}
        name = (base.get("repo") or {}).get("full_name")
        if not isinstance(name, str) or not isinstance(base.get("ref"), str):
            raise WTError("Open PR base identity was incomplete")
        if name.lower() == identity.lower() and base["ref"] == branch:
            raise WTError(
                f"Open PR #{item.get('number')} still uses {identity}:{branch} as its base"
            )


def _managed(repo, source, branch):
    identity = source["repo"].lower()
    ref = "refs/heads/" + branch
    for name, metadata in repo.branches.items():
        publication = metadata.get("publication") or {}
        published_ref = publication.get("ref", "")
        if published_ref and not published_ref.startswith("refs/"):
            published_ref = "refs/heads/" + published_ref
        pr = metadata.get("pr") or {}
        if (
            publication.get("repo", "").lower() == identity
            and published_ref == ref
            or (pr.get("head_repo") or "").lower() == identity
            and pr.get("head_ref") == branch
            or (pr.get("base_repo") or "").lower() == identity
            and pr.get("base_ref") == branch
        ):
            raise WTError(
                f"Remote branch is still associated with managed branch {name}; use its normal lifecycle"
            )
    if identity == repo.config["upstream_repo"].lower():
        for stack in stacks.catalog(repo)["stacks"]:
            if stack.get("trunk", {}).get("branch") == branch or any(
                row.get("branch") == branch
                and not row.get("pullRequest", {}).get("merged")
                for row in stack["branches"]
            ):
                raise WTError("Remote branch is still referenced by a local stack")


def _preflight(repo, remote, branch, *, expected_source=None, expected_oid=None):
    repo.check_primary()
    repo.ensure_idle()
    source, default = _source(repo, remote)
    if expected_source and any(
        source[key] != expected_source[key] for key in ("remote", "repo", "url")
    ):
        raise WTError(
            "Remote retirement endpoint changed; the recorded destination was preserved"
        )
    if branch in (repo.config["trunk"], default):
        raise WTError("Configured trunk and remote default branches cannot be retired")
    if source["repo"].lower() == repo.config[
        "upstream_repo"
    ].lower() and not branch.startswith("rwgk/"):
        raise WTError(
            "Canonical remote retirement is limited to rwgk/ branches; preserving other contributors' work"
        )
    ref = "refs/heads/" + branch
    current = lifecycle._remote_tip(repo, source["url"], ref)
    if expected_oid is not None and current not in (None, expected_oid):
        raise WTError("Remote branch changed from the expected SHA; preserving it")
    if current:
        data = github._api(
            repo, f"repos/{source['repo']}/branches/{quote(branch, safe='')}"
        )
        if (
            not isinstance(data, dict)
            or data.get("name") != branch
            or type(data.get("protected")) is not bool
        ):
            raise WTError("GitHub did not identify remote branch protection reliably")
        if data["protected"]:
            raise WTError("Protected remote branches cannot be retired")
    source.update(ref=ref, oid=expected_oid or current, observed_oid=current)
    _managed(repo, source, branch)
    _unused(repo, source, branch)
    return source


def _same_local_repository(repo, record):
    identity = record.get("repository") or {}
    if not isinstance(identity, dict):
        return False
    canonical = identity.get("upstream_repo")
    if canonical:
        return (
            isinstance(canonical, str)
            and canonical.lower() == repo.config["upstream_repo"].lower()
        )
    return identity.get("common") == str(repo.common)


def local_history(repo, branch, oid):
    """Link exact historical names/tips without rewriting their original evidence."""
    directory = lifecycle._backtracking_dir(repo)
    matches = []
    for path in sorted(directory.glob("*.json")):
        try:
            text = path.read_text()
            if json.dumps(branch) not in text or oid not in text:
                continue
            record = json.loads(text)
        except (OSError, ValueError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("kind") not in ("retire", "local-branch-delete")
            or record.get("status") not in ("complete", "completed")
            or record.get("branch") != branch
            or record.get("local_oid") != oid
            or not _same_local_repository(repo, record)
        ):
            continue
        refs = [
            s["recovery_ref"]
            for s in record.get("snapshots", [])
            if s.get("oid") == oid and s.get("recovery_ref")
        ]
        matches.append(
            {
                "evidence_kind": "completed-retirement",
                "path": str(path.absolute()),
                "record_id": record["id"],
                "local_branch": branch,
                "local_oid": oid,
                "recovery_refs": refs,
                "recovery_refs_verified": bool(refs)
                and all(
                    lifecycle._git_result(
                        repo, "rev-parse", "--verify", ref
                    ).stdout.strip()
                    == oid
                    for ref in refs
                ),
            }
        )
    for path in sorted(directory.glob("*.txt")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        cwd = re.search(r"^Current working directory: '([^'\n]+)'$", text, re.MULTILINE)
        name = re.search(r'before `git branch -D "([^"\n]+)"`', text)
        tip = re.search(
            r"^commit ([0-9a-f]{40}|[0-9a-f]{64})(?:\s|$)", text, re.MULTILINE
        )
        if not (cwd and name and tip) or name[1] != branch or tip[1] != oid:
            continue
        if Path(cwd[1]).resolve() != repo.primary.resolve():
            continue
        matches.append(
            {
                "evidence_kind": "legacy-predeletion-log",
                "path": str(path.absolute()),
                "local_branch": branch,
                "local_oid": oid,
                "working_directory": cwd[1],
                "deletion_completion_recorded": False,
                "recovery_refs": [],
            }
        )
    return matches


def retire(repo, branch, *, remote, expect=None, apply=False):
    """Preview or retire one fixed remote tip, preserving local working state."""
    branch = _branch(repo, branch)
    if expect is not None and not github.SHA.fullmatch(expect):
        raise WTError("--expect requires a full commit SHA")
    if apply and expect is None:
        raise WTError(
            "Remote retirement requires --expect SHA with --apply; review the preview first"
        )
    source = _preflight(repo, remote, branch, expected_oid=expect)
    if not source["oid"]:
        raise WTError(f"No remote branch named {branch!r} exists on {remote}")
    lifecycle._fetch_exact(repo, source["url"], source["ref"], source["oid"])
    record = lifecycle._new_record(repo, None, {}, kind="remote-retire")
    record.update(
        remote_branch=branch,
        source=source,
        local_history=local_history(repo, branch, source["oid"]),
        local_ref_exists=repo.exists("refs/heads/" + branch),
        snapshots=[{"oid": source["oid"], "label": f"remote {remote}/{branch}"}],
        remote_actions=[],
    )
    trunk = lifecycle._trunk_snapshot(repo)
    record["trunk"] = trunk
    record["archive_snapshots"] = (
        []
        if lifecycle._ancestor(repo, source["oid"], trunk["oid"])
        else list(record["snapshots"])
    )
    if record["archive_snapshots"]:
        record["archive_destination"] = lifecycle._archive_identity(repo)
    record["apply_command"] = shlex.join(
        ["wt", "drop", "--remote", remote, branch, "--expect", source["oid"], "--apply"]
    )
    if apply:
        lifecycle._save_record(repo, record)
        return _apply(repo, record)
    return record


def _apply(repo, record):
    if record["status"] == "complete":
        return record
    source = record["source"]
    branch = record["remote_branch"]
    with stacks.catalog_lock(repo):
        _preflight(
            repo,
            source["remote"],
            branch,
            expected_source=source,
            expected_oid=source["oid"],
        )
        lifecycle._protect_snapshots(repo, record)
        if "archives" not in record["completed"]:

            def archived(archive, *, created):
                if archive not in record["archives"]:
                    record["archives"].append(archive)
                action = {key: archive[key] for key in ("remote", "repo", "ref", "oid")}
                action["action"] = "archive-created" if created else "archive-reused"
                if action not in record["remote_actions"]:
                    record["remote_actions"].append(action)
                lifecycle._save_record(repo, record)

            record["archives"] = lifecycle._pin(
                repo,
                record["archive_snapshots"],
                destination=record.get("archive_destination"),
                on_archive=archived,
            )
            record["completed"].append("archives")
            lifecycle._save_record(repo, record)
        if "remote_branch" not in record["completed"]:
            live = _preflight(
                repo,
                source["remote"],
                branch,
                expected_source=source,
                expected_oid=source["oid"],
            )
            trunk = record["trunk"]
            current_trunk = lifecycle._remote_tip(repo, trunk["url"], trunk["ref"])
            if not current_trunk:
                raise WTError("Upstream trunk disappeared; refusing remote retirement")
            lifecycle._fetch_exact(repo, trunk["url"], trunk["ref"], current_trunk)
            if not lifecycle._ancestor(repo, trunk["oid"], current_trunk):
                raise WTError(
                    "Upstream trunk history changed; refusing remote retirement"
                )
            for archive in record["archives"]:
                if (
                    lifecycle._remote_tip(repo, archive["url"], archive["ref"])
                    != archive["oid"]
                ):
                    raise WTError(
                        "Archive ref disappeared or changed; refusing remote retirement"
                    )
            if live["observed_oid"]:
                lifecycle._push(
                    repo,
                    f"--force-with-lease={source['ref']}:{source['oid']}",
                    "--",
                    source["url"],
                    f":{source['ref']}",
                    destination=source["url"],
                    updates=[{"ref": source["ref"], "old": source["oid"], "new": None}],
                )
            if lifecycle._remote_tip(repo, source["url"], source["ref"]) is not None:
                raise WTError(
                    "Remote branch still exists after retirement; inspect before retrying"
                )
            record["remote_actions"].append(
                {
                    **source,
                    "observed_oid": live["observed_oid"],
                    "action": "branch-deleted"
                    if live["observed_oid"]
                    else "branch-absent",
                }
            )
            record["completed"].append("remote_branch")
            lifecycle._save_record(repo, record)
        record["status"] = "complete"
        lifecycle._save_record(repo, record)
    return record


def resume(repo, record_id):
    """Continue the original remote retirement without refreshing its lease."""
    record = lifecycle._load_record(repo, record_id)
    if record.get("kind") != "remote-retire":
        raise WTError("This is not a remote-retirement record")
    return _apply(repo, record)
