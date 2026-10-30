"""Recoverable rebases of one managed PR worktree onto its frozen PR head."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from . import github, lifecycle, submodule_rebase, submodules
from .core import Repo, WTError


def _command(repo, *args, check=True):
    # A pull must not rewrite other refs, stash work, merge automatically, or
    # float module pins because of a user's unrelated Git configuration.
    return repo.run(
        [
            "git",
            "-c",
            "submodule.recurse=false",
            "-c",
            "rebase.autoStash=false",
            "-c",
            "rebase.backend=merge",
            "-c",
            "rebase.updateRefs=false",
            "-c",
            "rebase.rebaseMerges=false",
            "-c",
            "core.editor=true",
            *args,
        ],
        cwd=repo.root,
        check=check,
    )


def _markers(repo):
    result = []
    for name in ("rebase-merge", "rebase-apply"):
        directory = Path(
            repo.git("rev-parse", "--path-format=absolute", "--git-path", name)
        )
        if directory.is_dir():
            result.append(directory)
    return result


def _owner(repo, operation):
    branch = operation["branch"]
    path = Path(operation["path"]).resolve()
    metadata = repo.branches.get(branch)
    if repo.root != path or not metadata or Path(metadata["path"]).resolve() != path:
        raise WTError(
            "Resume the PR rebase in its original registered worktree: " + str(path)
        )
    github.validate_pr_source(
        operation["source"],
        github._association_source(metadata, metadata.get("pr") or {}),
    )
    current = repo.current_branch()
    markers = _markers(repo)
    if markers:
        if len(markers) != 1 or current is not None:
            raise WTError(
                "PR rebase ownership is ambiguous; inspect its recovery record"
            )
        marker = markers[0]
        try:
            values = {
                name: (marker / name).read_text().strip()
                for name in ("head-name", "orig-head", "onto")
            }
        except OSError as error:
            raise WTError(
                "PR rebase metadata is incomplete; inspect its recovery record"
            ) from error
        if values != {
            "head-name": "refs/heads/" + branch,
            "orig-head": operation["previous_oid"],
            "onto": operation["target_oid"],
        }:
            raise WTError(
                "Another Git rebase replaced the saved PR rebase; inspect before continuing"
            )
        if repo.oid("refs/heads/" + branch) != operation["previous_oid"]:
            raise WTError("The reserved PR branch changed while its rebase was paused")
    elif current != branch or repo.owner(branch) != path:
        raise WTError(
            "PR worktree ownership changed during rebase; inspect its recovery record"
        )
    return markers


def navigation_path(repo, branch):
    """Resolve a paused PR owner only through its saved rebase witness."""
    operation = repo.state.get("pr_rebase")
    if not operation or operation.get("branch") != branch:
        return None
    owner = Repo(operation["path"])
    if owner.common != repo.common:
        raise WTError("Saved PR rebase path belongs to a different repository")
    saved = owner.state.get("pr_rebase")
    if saved != operation:
        raise WTError("Saved PR rebase changed during navigation; inspect and retry")
    _owner(owner, saved)
    return owner.root


def _replay_revisions(repo, target, upstream, local):
    revisions = {target, upstream}
    revisions.update(
        repo.git("rev-list", "--no-merges", local, "^" + upstream).splitlines()
    )
    return revisions


def _capture_modules(repo, branch, target, upstream, local):
    revisions = _replay_revisions(repo, target, upstream, local)
    for revision in revisions:
        submodules._check_collisions(repo, repo.root, local, revision)
    modules = submodules.inspect(repo, repo.root)
    names = []
    for module in modules:
        relative = Path(module["path"])
        if not any(Path(name) in relative.parents for name in names):
            names.append(module["path"])
        submodule_rebase._protect_ignored(repo, repo.root / relative)
    if not modules:
        return []
    # Initial reset and every replayed tree must retain initialized gitlinks.
    # Checking each intermediate commit also catches remove-then-readd history.
    for revision in revisions:
        submodule_rebase._require_paths(repo, repo.root, names, revision)
    return [
        {
            "branch": branch,
            "path": str(repo.root),
            "modules": names,
            "heads": {module["path"]: module["oid"] for module in modules},
        }
    ]


def _check_parent_resume(repo, operation, *, abort):
    untracked = list(
        filter(
            None,
            repo.git("ls-files", "--others", "--exclude-standard", "-z").split("\0"),
        )
    )
    if untracked:
        action = "aborting" if abort else "continuing"
        raise WTError(
            f"Preserve untracked parent files before {action} PR rebase: "
            + ", ".join(repr(name) for name in untracked)
        )
    current = repo.oid("HEAD")
    revisions = (
        {operation["previous_oid"]}
        if abort
        else _replay_revisions(
            repo,
            operation["target_oid"],
            operation["upstream_oid"],
            operation["previous_oid"],
        )
    )
    for revision in revisions:
        submodules._check_collisions(repo, repo.root, current, revision)


def _check_merges(repo, local, target, upstream):
    local_merges = repo.git(
        "log",
        "--merges",
        "--format=%H %T %P",
        local,
        "^" + upstream,
        "^" + target,
    ).splitlines()
    if not local_merges:
        return
    represented = {
        tuple(line.split())
        for line in repo.git("log", "--merges", "--format=%T %P", target).splitlines()
    }
    unmatched = [
        line.split()[0]
        for line in local_merges
        if tuple(line.split()[1:]) not in represented
    ]
    if unmatched:
        raise WTError(
            "Local merge commits contain resolutions not proven represented on the PR head: "
            + ", ".join(oid[:12] for oid in unmatched)
            + "; reconcile those merges explicitly before 'wt pull --rebase'."
        )


def _result(repo, operation, result):
    return {
        "result": result,
        "branch": operation["branch"],
        "path": operation["path"],
        "pr": operation["pr"],
        "previous_oid": operation["previous_oid"],
        "oid": repo.oid("HEAD"),
        "recovery_record": operation.get("recovery_record"),
    }


def _finish(repo, operation, *, aborted=False):
    _owner(repo, operation)
    expected = operation["result_oid"]
    if repo.oid("HEAD") != expected:
        raise WTError(
            "Local HEAD changed after PR rebase; inspect recovery state before alignment"
        )
    status = repo.git(
        "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"
    )
    if status:
        raise WTError(
            "Resolve parent worktree changes before finishing PR rebase alignment:\n"
            + status
        )
    submodule_rebase.after(repo, operation["captured"])
    submodules.initialize(repo, repo.root)
    repo.ensure_clean(repo.root)
    oid = repo.oid("HEAD")
    _owner(repo, operation)
    if oid != expected:
        raise WTError(
            "Local HEAD changed during PR rebase alignment; inspect recovery state"
        )
    if aborted:
        if oid != operation["previous_oid"]:
            raise WTError("Aborted PR rebase did not restore its original branch tip")
        result = "aborted"
    else:
        if not github._ancestor(repo, operation["target_oid"], oid):
            raise WTError("PR rebase result no longer contains the frozen PR head")
        github.record_sync(
            repo, operation["branch"], operation["pr"], operation["target_oid"]
        )
        if oid == operation["previous_oid"]:
            result = "unchanged"
        elif oid == operation["target_oid"]:
            result = "refreshed"
        else:
            result = "rebased"
    repo.state.pop("pr_rebase", None)
    repo.save()
    return _result(repo, operation, result)


def _run(repo, operation):
    _owner(repo, operation)
    if repo.oid("HEAD") != operation["previous_oid"]:
        raise WTError(
            "Local HEAD changed before PR rebase began; inspect recovery state"
        )
    operation["phase"] = "rebasing"
    repo.save()
    if operation["mode"] == "fast-forward":
        result = _command(
            repo, "merge", "--ff-only", operation["target_oid"], check=False
        )
    else:
        result = _command(
            repo,
            "rebase",
            "--no-autostash",
            "--no-rebase-merges",
            "--no-fork-point",
            "--onto",
            operation["target_oid"],
            operation["upstream_oid"],
            check=False,
        )
    if result.returncode:
        raise WTError(
            "PR pull rebase paused. Resolve the reported problem, then run 'wt pull --continue', "
            "or restore the original work with 'wt pull --abort'.\n"
            + (result.stderr or result.stdout).strip()
        )
    operation["phase"] = "aligning"
    operation["result_oid"] = repo.oid("HEAD")
    repo.save()
    return _finish(repo, operation)


def start(repo):
    """Pull one PR; replay a proven local contribution suffix when necessary."""
    if repo.state.get("pr_rebase"):
        raise WTError(
            "Finish the pending PR rebase with 'wt pull --continue' or 'wt pull --abort'"
        )
    branch, metadata = github.current_pr(repo)
    submodules.recover(repo, repo.root)
    repo.ensure_clean(repo.root)
    pr = github.get_pr(repo, metadata["pr"]["number"])
    source = github._association_source(metadata, pr)
    remote = repo.config["upstream_remote"]
    github._checked_remote(repo, remote, repo.config["upstream_repo"])
    digest = hashlib.sha256(repo.config["upstream_repo"].lower().encode()).hexdigest()[
        :12
    ]
    fetched = f"refs/rwgk/pr/{digest}/{pr['number']}/head"
    repo.git(
        "fetch",
        "--recurse-submodules=no",
        "--no-tags",
        remote,
        f"+{github.pr_head_ref(repo, pr['number'])}:{fetched}",
    )
    target = repo.oid(fetched)
    if target != pr["head_oid"]:
        raise WTError("PR head changed while pulling it; retry after inspecting the PR")
    local = repo.oid("HEAD")
    operation = {
        "branch": branch,
        "path": str(repo.root),
        "pr": copy.deepcopy(pr),
        "source": source,
        "previous_oid": local,
        "target_oid": target,
    }
    boundary = github.replay_base(repo, branch, pr)
    rewritten = (
        boundary is not None
        and github._ancestor(repo, boundary, local)
        and not github._ancestor(repo, boundary, target)
    )
    if github._ancestor(repo, target, local) and not rewritten:
        github.record_sync(repo, branch, pr, target)
        return _result(repo, operation, "unchanged")
    if github._ancestor(repo, local, target):
        upstream, mode = local, "fast-forward"
    else:
        if not boundary or not github._ancestor(repo, boundary, local):
            raise WTError(
                "Cannot identify a retained replay boundary for local commits; "
                "reconcile the local history explicitly before 'wt pull --rebase'."
            )
        upstream = target if github._ancestor(repo, boundary, target) else boundary
        mode = "rebase"
        _check_merges(repo, local, target, upstream)
    captured = _capture_modules(repo, branch, target, upstream, local)
    recovery = lifecycle.protect_local(
        repo,
        [{"oid": local, "label": branch, "branch": branch, "pr": pr}],
        kind="pr-pull-rebase",
    )
    repo.ensure_clean(repo.root)
    if repo.current_branch() != branch or repo.oid("HEAD") != local:
        raise WTError(
            "Local branch changed during PR rebase preflight; no rebase was started"
        )
    operation.update(
        phase="ready",
        mode=mode,
        upstream_oid=upstream,
        captured=captured,
        recovery_record=recovery["id"],
    )
    repo.state["pr_rebase"] = operation
    repo.save()
    active = getattr(repo, "_wt_pr_rebase_active", False)
    repo._wt_pr_rebase_active = True
    try:
        return _run(repo, operation)
    finally:
        repo._wt_pr_rebase_active = active


def resume(repo, *, abort=False):
    """Continue/abort the saved target even when Git has detached HEAD."""
    operation = repo.state.get("pr_rebase")
    if not operation:
        raise WTError("There is no saved PR pull rebase to continue or abort")
    active = getattr(repo, "_wt_pr_rebase_active", False)
    repo._wt_pr_rebase_active = True
    try:
        markers = _owner(repo, operation)
        if operation["phase"] in ("aligning", "aborting"):
            if abort and operation["phase"] == "aligning":
                raise WTError(
                    "The rebase finished; submodule alignment remains. Run 'wt pull --continue' "
                    "to finish before planning a rollback."
                )
            return _finish(repo, operation, aborted=operation["phase"] == "aborting")
        _check_parent_resume(repo, operation, abort=abort)
        submodule_rebase.before_resume(repo, operation["captured"])
        if abort:
            if markers:
                _command(repo, "rebase", "--abort")
            elif repo.oid("HEAD") != operation["previous_oid"]:
                raise WTError(
                    "Git rebase already finished; continue alignment before planning a rollback"
                )
            operation["phase"] = "aborting"
            operation["result_oid"] = repo.oid("HEAD")
            repo.save()
            return _finish(repo, operation, aborted=True)
        if not markers:
            if repo.oid("HEAD") == operation["previous_oid"]:
                repo.ensure_clean(repo.root)
                return _run(repo, operation)
            if not github._ancestor(repo, operation["target_oid"], repo.oid("HEAD")):
                raise WTError(
                    "Git rebase state disappeared and HEAD changed; inspect the recovery record"
                )
        else:
            result = _command(repo, "rebase", "--continue", check=False)
            if result.returncode:
                raise WTError(
                    "PR rebase still needs resolution; use 'wt pull --continue' or 'wt pull --abort'.\n"
                    + (result.stderr or result.stdout).strip()
                )
        operation["phase"] = "aligning"
        operation["result_oid"] = repo.oid("HEAD")
        repo.save()
        return _finish(repo, operation)
    finally:
        repo._wt_pr_rebase_active = active
