"""Durable, leased retirement and recovery for managed branch/worktree pairs.

No lifecycle action infers a branch or repository from a directory basename.
Recovery records are written before references or worktrees are removed.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import shlex
import tempfile
import uuid
from pathlib import Path

from .core import WTError


def _git(repo, *args, cwd=None):
    return repo.git(*args, cwd=cwd or repo.primary)


def _git_result(repo, *args, cwd=None):
    return repo.run(["git", *map(str, args)], cwd=cwd or repo.primary, check=False)


def _exists(repo, oid):
    return _git_result(repo, "cat-file", "-e", f"{oid}^{{commit}}").returncode == 0


def _ancestor(repo, older, newer):
    result = _git_result(repo, "merge-base", "--is-ancestor", older, newer)
    if result.returncode not in (0, 1):
        raise WTError(result.stderr.strip() or "Cannot check commit ancestry")
    return result.returncode == 0


def _remote_url(repo, remote, *, push=False):
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.append(remote)
    result = _git(repo, *args)
    # Multiple push URLs are unsuitable for an exact single-destination operation.
    all_args = args[:-1] + ["--all", remote]
    if len(_git(repo, *all_args).splitlines()) != 1:
        raise WTError(f"Remote {remote!r} has multiple endpoints")
    return result


def _remote_tip(repo, url, ref):
    result = _git(repo, "ls-remote", "--refs", "--", url, ref)
    rows = [line.split() for line in result.splitlines() if line]
    matches = [row[0] for row in rows if len(row) == 2 and row[1] == ref]
    if len(matches) > 1:
        raise WTError(f"Ambiguous remote ref {ref}")
    return matches[0] if matches else None


def _fetch_exact(repo, url, ref, oid):
    if not _exists(repo, oid):
        _git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--", url, ref)
    if not _exists(repo, oid):
        raise WTError(f"Fetching {ref} did not provide expected commit {oid}")


def _safe_component(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value)).strip(".-") or "repository"


def _operation_dir(repo):
    return repo.common / "rwgk-wt" / "operations"


def _backtracking_dir(repo):
    configured = os.environ.get("MY_GIT_BACKTRACKING_INFO_LOCAL")
    if configured:
        return Path(configured).expanduser()
    configured = repo.config.get("backtracking")
    return Path(configured or "~/Sve/git_backtracking_info").expanduser()


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_record(repo, record):
    """Persist locally and, when configured, remotely before any next mutation."""
    name = f"{record['id']}.json"
    journal = _operation_dir(repo) / name
    backtracking = (_backtracking_dir(repo) / name).absolute()
    record["backtracking_path"] = str(backtracking)
    _atomic_json(journal, record)
    _atomic_json(backtracking, record)
    destination = os.environ.get("MY_GIT_BACKTRACKING_INFO_REMOTE", "").rstrip("/")
    if destination:
        if ":" not in destination:
            raise WTError("MY_GIT_BACKTRACKING_INFO_REMOTE must have HOST:PATH form")
        host, directory = destination.split(":", 1)
        if not host or not directory or host.startswith("-"):
            raise WTError("Invalid remote backtracking destination")
        remote_name = f".{name}.upload"
        repo.run(
            ["ssh", host, f"mkdir -p -- {shlex.quote(directory)}"], cwd=repo.primary
        )
        repo.run(
            ["scp", "-p", str(journal), f"{host}:{directory}/{remote_name}"],
            cwd=repo.primary,
        )
        repo.run(
            [
                "ssh",
                host,
                (
                    f"mv -- {shlex.quote(directory + '/' + remote_name)} "
                    f"{shlex.quote(directory + '/' + name)}"
                ),
            ],
            cwd=repo.primary,
        )


def _load_record(repo, record_id):
    candidate = Path(record_id).expanduser()
    if candidate.name != str(candidate):
        paths = [candidate]
    else:
        name = candidate.name.removesuffix(".json") + ".json"
        paths = [_operation_dir(repo) / name, _backtracking_dir(repo) / name]
    for path in paths:
        if path.is_file():
            record = json.loads(path.read_text())
            if record.get("repository", {}).get("key") != repo.config["key"]:
                raise WTError("Recovery record belongs to a different repository")
            expected = record["repository"].get("upstream_repo")
            if expected and expected != repo.config.get("upstream_repo"):
                raise WTError("Recovery record has a different canonical repository")
            return record
    raise WTError(f"No backtracking record found for {record_id!r}")


def _new_record(repo, branch, metadata, kind="retire"):
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return {
        "version": 1,
        "id": f"{_safe_component(repo.config['key'])}-{stamp}-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "repository": {
            "key": repo.config["key"],
            "common": str(repo.common),
            "upstream_repo": repo.config.get("upstream_repo"),
        },
        "branch": branch,
        "metadata": copy.deepcopy(metadata),
        "status": "planned",
        "completed": [],
        "snapshots": [],
        "archives": [],
    }


def _archive_identity(repo):
    from . import github

    identity = github.validate_archive_remote(repo)
    remote = repo.config.get("archive_remote", "origin")
    return {
        "remote": remote,
        "repo": identity,
        "url": _remote_url(repo, remote, push=True),
    }


def _tag_for(repo, oid, pr=None):
    identity = (pr or {}).get("base_repo") or repo.config.get("upstream_repo")
    identity = identity or repo.config["key"]
    components = [_safe_component(part) for part in identity.split("/")]
    context = f"pr{pr['number']}" if pr and pr.get("number") else "local"
    return "refs/tags/rwgk/archive/" + "/".join([*components, context, oid])


def _covering_archive(repo, destination, oid, pr):
    # Match the repository namespace across PRs; a retained descendant also
    # preserves this tip. Every returned remote ref is verified after fetching.
    prefix = _tag_for(repo, oid, pr).rsplit("/", 2)[0] + "/*"
    rows = _git(repo, "ls-remote", "--refs", "--", destination["url"], prefix)
    for line in rows.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        archived_oid, ref = fields
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", archived_oid):
            continue
        _fetch_exact(repo, destination["url"], ref, archived_oid)
        if (
            _ancestor(repo, oid, archived_oid)
            and _remote_tip(repo, destination["url"], ref) == archived_oid
        ):
            return {**destination, "ref": ref, "oid": archived_oid}
    return None


def _pin(repo, snapshots, pr=None, destination=None, *, on_archive=None):
    if not snapshots:
        return []
    destination = destination or _archive_identity(repo)
    actual_url = _remote_url(repo, destination["remote"], push=True)
    if actual_url != destination["url"]:
        raise WTError("Archive remote endpoint changed; refusing publication")
    archives = []
    for snapshot in snapshots:
        oid = snapshot["oid"]
        if not _exists(repo, oid):
            raise WTError(f"Cannot archive missing commit {oid}")
        ref = _tag_for(repo, oid, pr)
        current = _remote_tip(repo, actual_url, ref)
        if current and current != oid:
            raise WTError(f"Immutable archive ref {ref} already points to {current}")
        if not current:
            existing = _covering_archive(repo, destination, oid, pr)
            if existing:
                if existing not in archives:
                    archives.append(existing)
                if on_archive:
                    on_archive(existing, created=False)
                continue
            # Empty expected value means creation only. Never move an existing tag.
            _git(
                repo,
                "push",
                f"--force-with-lease={ref}:",
                "--",
                actual_url,
                f"{oid}:{ref}",
            )
        if _remote_tip(repo, actual_url, ref) != oid:
            raise WTError(f"Remote archive verification failed for {ref}")
        archive = {**destination, "ref": ref, "oid": oid}
        archives.append(archive)
        if on_archive:
            on_archive(archive, created=not current)
    return archives


def protect_local(repo, snapshots, kind="rebase"):
    """Keep local history reachable before rewriting, without any remote operation."""
    unique = {snapshot["oid"]: dict(snapshot) for snapshot in snapshots}
    for snapshot in unique.values():
        if not _exists(repo, snapshot["oid"]):
            raise WTError(f"Cannot preserve missing commit {snapshot['oid']}")
    record = _new_record(repo, None, {}, kind=kind)
    record["snapshots"] = list(unique.values())
    _save_record(repo, record)
    _protect_snapshots(repo, record)
    record["status"] = "complete"
    _save_record(repo, record)
    return record


def pin_snapshots(repo, snapshots, pr=None):
    """Preserve tips before publication rewrites, keeping a durable recovery index."""
    unique = {snapshot["oid"]: dict(snapshot) for snapshot in snapshots}
    if not unique:
        return []
    record = _new_record(repo, None, {}, kind="rewrite")
    record["pr"] = pr
    record["snapshots"] = list(unique.values())
    trunk = _trunk_snapshot(repo)
    record["trunk"] = trunk
    candidates = [
        snapshot
        for snapshot in record["snapshots"]
        if not _ancestor(repo, snapshot["oid"], trunk["oid"])
    ]
    record["archive_snapshots"] = [
        snapshot
        for snapshot in candidates
        if not any(
            snapshot["oid"] != other["oid"]
            and _ancestor(repo, snapshot["oid"], other["oid"])
            for other in candidates
        )
    ]
    if record["archive_snapshots"]:
        record["archive_destination"] = _archive_identity(repo)
    _save_record(repo, record)
    _protect_snapshots(repo, record)
    record["archives"] = _pin(
        repo, record["archive_snapshots"], pr, record.get("archive_destination")
    )
    record["status"] = "complete"
    _save_record(repo, record)
    return record["archives"]


def _protect_snapshots(repo, record):
    for index, snapshot in enumerate(record["snapshots"]):
        ref = f"refs/rwgk/backtracking/{record['id']}/{index}"
        result = _git_result(repo, "rev-parse", "--verify", ref)
        if result.returncode == 0:
            if result.stdout.strip() != snapshot["oid"]:
                raise WTError(f"Recovery ref unexpectedly changed: {ref}")
        else:
            _git(repo, "update-ref", ref, snapshot["oid"], "0" * len(snapshot["oid"]))
        snapshot["recovery_ref"] = ref
    if "recovery_refs" not in record["completed"]:
        record["completed"].append("recovery_refs")
    _save_record(repo, record)


def _preflight_worktree(repo, path, branch, discard_builds):
    if path.resolve() == repo.primary.resolve():
        raise WTError("The primary trunk worktree cannot be retired")
    if not path.exists():
        raise WTError(f"Managed worktree is missing: {path}")
    owner = repo.owner(branch)
    if not owner or owner.resolve() != path.resolve():
        raise WTError("Recorded worktree does not own the requested branch")
    repo.ensure_clean(path)
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", cwd=path)
    if dirty:
        raise WTError(f"Worktree contains changes or untracked files: {path}")
    ignored = _git(
        repo,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "-z",
        cwd=path,
    ).split("\0")
    ignored = [item for item in ignored if item]
    if ignored and not discard_builds:
        raise WTError(
            "Ignored files require --discard-builds after inspecting build directories"
        )
    known = set(
        repo.config.get("disposable_build_dirs", [])
        or [
            "build",
            "build_scons",
            "dist",
            "TestVenv",
            "TestVenvFT",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "CMakeFiles",
        ]
    )
    unknown = [
        item for item in ignored if not any(part in known for part in Path(item).parts)
    ]
    if unknown:
        raise WTError(
            "Ignored paths are not recognized disposable builds: " + ", ".join(unknown)
        )
    return bool(ignored)


def _trunk_snapshot(repo):
    from . import github

    remote = repo.config["upstream_remote"]
    identity = github.remote_identity(repo, remote)
    if identity.lower() != repo.config["upstream_repo"].lower():
        raise WTError("Upstream remote no longer identifies the configured repository")
    url = _remote_url(repo, remote)
    ref = f"refs/heads/{repo.config['trunk']}"
    oid = _remote_tip(repo, url, ref)
    if not oid:
        raise WTError("Configured upstream trunk is missing")
    _fetch_exact(repo, url, ref, oid)
    return {"remote": remote, "url": url, "ref": ref, "oid": oid}


def _worktree_gitdir(repo, path):
    if path.resolve() == repo.primary.resolve():
        return repo.common
    root = repo.common / "worktrees"
    candidates = []
    for admin in root.iterdir() if root.is_dir() else ():
        backlink = admin / "gitdir"
        if not backlink.is_file():
            continue
        location = admin / backlink.read_text().rstrip("\n")
        if location.parent.resolve() != path.resolve():
            continue
        common = admin / "commondir"
        if (
            not common.is_file()
            or (admin / common.read_text().rstrip("\n")).resolve()
            != repo.common.resolve()
        ):
            raise WTError(f"Invalid Git administration for {path}; inspect 'wt doctor'")
        candidates.append(admin)
    if len(candidates) != 1:
        raise WTError(
            f"Cannot identify Git administration for {path}; inspect 'wt doctor'"
        )
    return candidates[0]


def _preflight_unpaired(repo, branch):
    if branch == repo.config["trunk"]:
        raise WTError("The primary trunk branch cannot be retired")
    if branch in repo.branches:
        raise WTError("Managed branch has a missing worktree; inspect 'wt doctor'")
    owner = repo.owner(branch)
    if owner:
        raise WTError(f"Branch has an unmanaged worktree at {owner}; adopt it first")
    ref = "refs/heads/" + branch
    if _git_result(repo, "symbolic-ref", "--quiet", ref).returncode == 0:
        raise WTError("Symbolic branch refs cannot be retired")
    repo.ensure_idle(repo.primary)
    # A rebase or bisect can detach HEAD while still reserving the original branch.
    for row in repo.worktrees():
        path = Path(row["path"])
        gitdir = _worktree_gitdir(repo, path)
        for name in (
            "rebase-merge/head-name",
            "rebase-apply/head-name",
            "BISECT_START",
        ):
            state = gitdir / name
            if state.is_file() and state.read_text().strip() in (branch, ref):
                raise WTError(f"Branch is in use by a Git operation at {path}")


def _branch_config(repo, branch):
    result = _git_result(
        repo, "config", "--local", "--null", "--get-regexp", r"^branch\."
    )
    if result.returncode not in (0, 1):
        raise WTError(result.stderr.strip() or "Cannot read local branch configuration")
    section = f"branch.{branch}"
    values = []
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        key, separator, value = entry.partition("\n")
        if key.rpartition(".")[0] == section:
            values.append([key, value if separator else "true"])
    return values


def _source_snapshot(repo, metadata, pr):
    from . import github

    publication = metadata.get("publication")
    if not publication:
        raise WTError("Owned PR has no recorded publication destination")
    remote = publication["remote"]
    expected_repo = publication["repo"]
    actual = github.remote_identity(repo, remote, push=True)
    if (
        actual.lower() != expected_repo.lower()
        or actual.lower() != (pr["head_repo"] or "").lower()
    ):
        raise WTError("Publication endpoint does not match the PR head repository")
    ref = publication["ref"]
    if not ref.startswith("refs/heads/"):
        ref = "refs/heads/" + ref
    if ref != "refs/heads/" + pr["head_ref"]:
        raise WTError("Publication ref does not match the PR head branch")
    url = _remote_url(repo, remote, push=True)
    oid = _remote_tip(repo, url, ref)
    if oid and oid != pr["head_oid"]:
        raise WTError(
            "Live source branch differs from the terminal PR head; preserving it"
        )
    if oid:
        _fetch_exact(repo, url, ref, oid)
    return {"remote": remote, "repo": actual, "url": url, "ref": ref, "oid": oid}


def retire(
    repo,
    branch,
    apply=False,
    local_only=False,
    archive_local=False,
    discard_builds=False,
):
    """Retire a managed pair or an explicitly selected unpaired local branch."""
    from . import github, stacks

    repo.check_primary()
    if branch in repo.branches:
        metadata = copy.deepcopy(repo.branches[branch])
        path = Path(metadata["path"])
        ignored = _preflight_worktree(repo, path, branch, discard_builds)
    else:
        if not local_only:
            raise WTError("Unpaired branches require 'wt drop <branch> --local-only'")
        _preflight_unpaired(repo, branch)
        metadata, path, ignored = {}, None, False
    stacks.check_retirement(repo, branch)
    oid = repo.oid(f"refs/heads/{branch}")
    record = _new_record(repo, branch, metadata)
    record.update(
        local_oid=oid,
        path=str(path) if path is not None else None,
        local_only=local_only,
        archive_local=archive_local,
        discard_builds=discard_builds,
        ignored_builds=ignored,
    )
    if path is None:
        record["branch_config"] = _branch_config(repo, branch)
    tracking = _git_result(
        repo, "for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}"
    )
    record["tracking"] = tracking.stdout.strip()
    record["snapshots"] = [{"oid": oid, "label": "local"}]
    for pending in metadata.get("pending_archive", []):
        pending_oid = pending["oid"]
        if not _exists(repo, pending_oid):
            raise WTError(f"Pending published snapshot is missing: {pending_oid}")
        if not any(snapshot["oid"] == pending_oid for snapshot in record["snapshots"]):
            record["snapshots"].append(dict(pending))
    pr = metadata.get("pr")
    if pr and not local_only:
        pr = github.get_pr(repo, pr["number"], base_repo=pr.get("base_repo"))
        if pr["state"] not in ("MERGED", "CLOSED"):
            raise WTError(
                "PR is still open; use an explicit local-only retirement to keep remotes"
            )
        record["pr"] = pr
    elif not local_only:
        raise WTError("Unpublished work requires explicit local-only retirement")
    else:
        record["pr"] = pr
    owned = metadata.get("role", "owned") == "owned"
    if owned and pr and not local_only:
        source = _source_snapshot(repo, metadata, pr)
        record["source"] = source
        source_oid = source["oid"] or pr["head_oid"]
        if source_oid != oid:
            if not _exists(repo, source_oid):
                _fetch_exact(
                    repo,
                    _remote_url(repo, repo.config["upstream_remote"]),
                    f"refs/pull/{pr['number']}/head",
                    source_oid,
                )
            if not any(
                snapshot["oid"] == source_oid for snapshot in record["snapshots"]
            ):
                record["snapshots"].append({"oid": source_oid, "label": "source"})
    if (owned and not local_only) or archive_local:
        trunk = _trunk_snapshot(repo)
        record["trunk"] = trunk
        candidates = [
            s
            for s in record["snapshots"]
            if not _ancestor(repo, s["oid"], trunk["oid"])
        ]
        # Preserve distinct histories once: a descendant tip also covers its ancestors.
        record["archive_snapshots"] = [
            s
            for s in candidates
            if not any(
                s["oid"] != other["oid"] and _ancestor(repo, s["oid"], other["oid"])
                for other in candidates
            )
        ]
        if record["archive_snapshots"]:
            record["archive_destination"] = _archive_identity(repo)
    else:
        record["archive_snapshots"] = []
    if apply:
        _save_record(repo, record)
        return _apply_retirement(repo, record)
    return record


def _apply_retirement(repo, record):
    from . import stacks

    with stacks.catalog_lock(repo):
        repo.check_primary()
        return _apply_retirement_locked(repo, record)


def _assert_local_tip(repo, record):
    result = _git_result(
        repo, "rev-parse", "--verify", f"refs/heads/{record['branch']}"
    )
    if result.returncode or result.stdout.strip() != record["local_oid"]:
        raise WTError(
            "Local branch changed since the cleanup plan; preserving remaining work"
        )


def _apply_retirement_locked(repo, record):
    from . import github, stacks

    branch = record["branch"]
    path = Path(record["path"]) if record["path"] is not None else None
    completed = record["completed"]
    if record["status"] == "complete":
        return record
    if record.get("pr") and not record["local_only"]:
        expected_pr = record["pr"]
        current_pr = github.get_pr(
            repo, expected_pr["number"], base_repo=expected_pr["base_repo"]
        )
        if current_pr["state"] not in ("CLOSED", "MERGED"):
            raise WTError("PR was reopened; preserving remaining local and remote work")
        if current_pr["head_oid"] != expected_pr["head_oid"]:
            raise WTError(
                "PR head changed since cleanup planning; preserving remaining work"
            )
    if "local_branch" not in completed:
        result = _git_result(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
        if result.returncode == 0 and result.stdout.strip() != record["local_oid"]:
            raise WTError("Local branch changed since the cleanup plan; preserving it")
        if result.returncode and "worktree" not in completed:
            raise WTError("Local branch disappeared before worktree cleanup")
    if "worktree" not in completed:
        if path is None:
            _preflight_unpaired(repo, branch)
        elif path.exists():
            _preflight_worktree(repo, path, branch, record["discard_builds"])
        elif repo.owner(branch):
            raise WTError("Worktree path disappeared but registration remains")
        stacks.check_retirement(repo, branch)
    _protect_snapshots(repo, record)
    if "archives" not in completed:

        def record_archive(archive, *, created):
            action = {key: archive[key] for key in ("remote", "repo", "ref", "oid")}
            action["action"] = "archive-created" if created else "archive-reused"
            actions = record.setdefault("remote_actions", [])
            if action not in actions:
                actions.append(action)
                _save_record(repo, record)

        record["archives"] = _pin(
            repo,
            record["archive_snapshots"],
            record.get("pr"),
            record.get("archive_destination"),
            on_archive=record_archive,
        )
        completed.append("archives")
        _save_record(repo, record)
    source = record.get("source")
    if source and source["oid"] and "remote_branch" not in completed:
        _assert_local_tip(repo, record)
        github.ensure_source_unused(repo, record["pr"])
        stacks.check_retirement(repo, branch)
        if _remote_url(repo, source["remote"], push=True) != source["url"]:
            raise WTError("Source remote endpoint changed; refusing deletion")
        current = _remote_tip(repo, source["url"], source["ref"])
        if current is not None and current != source["oid"]:
            raise WTError("Source branch changed since cleanup planning; preserving it")
        # Verify snapshots are still anchored immediately before removing the source.
        trunk = record.get("trunk")
        if trunk:
            trunk_oid = _remote_tip(repo, trunk["url"], trunk["ref"])
            if not trunk_oid:
                raise WTError("Upstream trunk disappeared; refusing source deletion")
            _fetch_exact(repo, trunk["url"], trunk["ref"], trunk_oid)
            if not _ancestor(repo, trunk["oid"], trunk_oid):
                raise WTError(
                    "Upstream trunk history changed; refusing source deletion"
                )
        for archive in record["archives"]:
            if _remote_tip(repo, archive["url"], archive["ref"]) != archive["oid"]:
                raise WTError(
                    "Archive ref disappeared or changed; refusing source deletion"
                )
        if current:
            _git(
                repo,
                "push",
                f"--force-with-lease={source['ref']}:{source['oid']}",
                "--",
                source["url"],
                f":{source['ref']}",
            )
        record.setdefault("remote_actions", []).append(
            {
                **{key: source[key] for key in ("remote", "repo", "ref", "oid")},
                "action": "branch-deleted" if current else "branch-absent",
            }
        )
        completed.append("remote_branch")
        _save_record(repo, record)
    elif source and not source["oid"] and "remote_branch" not in completed:
        record.setdefault("remote_actions", []).append(
            {
                **{key: source[key] for key in ("remote", "repo", "ref", "oid")},
                "action": "branch-absent",
            }
        )
        completed.append("remote_branch")
        _save_record(repo, record)
    if "worktree" not in completed:
        if path is not None and path.exists():
            _assert_local_tip(repo, record)
            stacks.check_retirement(repo, branch)
            _preflight_worktree(repo, path, branch, record["discard_builds"])
            args = ["worktree", "remove"]
            if record["ignored_builds"]:
                args.append("--force")
            _git(repo, *args, "--", str(path), cwd=repo.primary)
        if path is not None and repo.root.resolve() == path.resolve():
            repo.root = repo.primary
        completed.append("worktree")
        _save_record(repo, record)
    if "local_branch" not in completed:
        if path is None:
            _preflight_unpaired(repo, branch)
            stacks.check_retirement(repo, branch)
        existing = _git_result(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
        if existing.returncode == 0:
            _git(
                repo,
                "update-ref",
                "--no-deref",
                "-d",
                f"refs/heads/{branch}",
                record["local_oid"],
                cwd=repo.primary,
            )
        _git_result(
            repo, "config", "--remove-section", f"branch.{branch}", cwd=repo.primary
        )
        completed.append("local_branch")
        _save_record(repo, record)
    if "metadata" not in completed:
        # Remove aliases only when they still resolve to this retiring worktree.
        trees = Path(repo.config["trees_root"]).expanduser() / repo.config["key"]
        if path is not None and trees.exists():
            for alias in trees.glob("pr[0-9]*"):
                if alias.is_symlink() and alias.resolve() == path.resolve():
                    alias.unlink()
        stacks.retired(repo, branch)
        repo.branches.pop(branch, None)
        repo.save()
        completed.append("metadata")
    record["status"] = "complete"
    _save_record(repo, record)
    return record


def resume(repo, record_id):
    """Continue a journaled cleanup without replanning against changed remote tips."""
    record = _load_record(repo, record_id)
    if record["kind"] != "retire":
        raise WTError(
            "This record describes preservation before a rewrite, not cleanup"
        )
    return _apply_retirement(repo, record)


def restore(repo, record_id):
    """Recreate the original branch/worktree pair, leaving remote branches untouched."""
    repo.check_primary()
    record = _load_record(repo, record_id)
    if record["kind"] != "retire":
        raise WTError("Only retired branch/worktree pairs can be restored")
    branch = record["branch"]
    if (
        _git_result(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode
        == 0
    ):
        raise WTError(f"Branch already exists: {branch}")
    oid = record["local_oid"]
    if not _exists(repo, oid):
        for archive in record.get("archives", []):
            _fetch_exact(repo, archive["url"], archive["ref"], archive["oid"])
            if _exists(repo, oid):
                break
    if not _exists(repo, oid):
        pr = record.get("pr")
        if pr:
            remote = repo.config["upstream_remote"]
            url = _remote_url(repo, remote)
            _git(
                repo,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--",
                url,
                f"refs/pull/{pr['number']}/head",
            )
    if not _exists(repo, oid):
        raise WTError(
            f"Commit {oid} is unavailable; recovery needs the original clone or an archive"
        )
    metadata = copy.deepcopy(record["metadata"])
    metadata.pop("path", None)
    role = metadata.pop("role", "owned")
    path = repo.create(branch, oid, role=role, extra=metadata)
    tracking = record.get("tracking")
    if "branch_config" in record:
        for key in dict(record["branch_config"]):
            _git_result(repo, "config", "--local", "--unset-all", key)
        for key, value in record["branch_config"]:
            _git(repo, "config", "--local", "--add", "--", key, value)
    elif tracking:
        remote_branch = tracking.removeprefix("refs/remotes/")
        _git_result(repo, "branch", f"--set-upstream-to={remote_branch}", branch)
    if metadata.get("pr"):
        repo.alias(branch, metadata["pr"])
    record["restored_at"] = dt.datetime.now(dt.UTC).isoformat()
    record["restored_path"] = str(path)
    _save_record(repo, record)
    return path
