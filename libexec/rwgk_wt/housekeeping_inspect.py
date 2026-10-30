"""Conservative, non-destructive assessment of managed housekeeping candidates."""

from __future__ import annotations

import copy
import hashlib
import shlex
from pathlib import Path

from . import github, lifecycle, stacks, submodules
from .core import WTError


def _reason(entry, code, message, hint=None, *, error=False):
    entry["reasons"].append({"code": code, "message": str(message)})
    if hint and hint not in entry["hints"]:
        entry["hints"].append(hint)
    entry["error"] |= error


def _git(repo, path, *args):
    return repo.run(["git", "-c", "submodule.recurse=false", *args], cwd=path).stdout


def _working_files(repo, path):
    raw = _git(
        repo,
        path,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ).split("\0")
    modified, untracked = [], []
    index = 0
    while index < len(raw):
        row = raw[index]
        index += 1
        if not row:
            continue
        status, name = row[:2], row[3:]
        if status == "??":
            untracked.append(name)
        else:
            modified.append(name)
        if "R" in status or "C" in status:
            index += 1
    return {"modified": modified, "untracked": untracked}


def _ignored(repo, entry, path, discard_builds, *, module=None):
    ignored = [
        value
        for value in _git(
            repo,
            path,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
        ).split("\0")
        if value
    ]
    if not ignored:
        return
    prefix = f"{module}/" if module else ""
    entry["facts"].setdefault("ignored", []).extend(prefix + value for value in ignored)
    try:
        lifecycle._check_ignored(repo, path, True)
    except WTError as error:
        _reason(
            entry,
            "ignored-material",
            error,
            "Inspect and preserve the listed ignored material before cleanup.",
        )
    else:
        if not discard_builds:
            _reason(
                entry,
                "build-outputs",
                f"Recognized disposable build outputs or caches: {', '.join(prefix + value for value in ignored)}",
                "Use 'wt housekeeping --apply --discard-builds' after inspecting these generated files.",
            )


def _module_work(repo, root, modules):
    issues = []
    for module in modules:
        path = submodules._path(root, module["path"])
        primary = submodules._path(repo.primary, module["path"])
        primary_refs = {}
        if primary != path and submodules._initialized(repo, primary):
            primary_refs = {
                row["ref"]: row["oid"] for row in submodules._refs(repo, primary)
            }
        covered = {module["gitlink"]}
        covered.update(
            row["oid"]
            for row in module["refs"]
            if row["ref"].startswith("refs/remotes/")
        )
        for row in module["refs"]:
            name, oid = row["ref"], row["oid"]
            if name.startswith("refs/remotes/"):
                continue
            if name == "refs/stash":
                issues.append(f"{module['path']}: {name}")
                continue
            if primary_refs.get(name) == oid:
                continue
            if not name.startswith(("refs/heads/", "refs/tags/")):
                issues.append(f"{module['path']}: {name}")
                continue
            peeled = repo.run(
                ["git", "rev-parse", "--verify", f"{name}^{{commit}}"],
                cwd=path,
                check=False,
            )
            if peeled.returncode:
                issues.append(f"{module['path']}: {name}")
                continue
            remaining = _git(
                repo,
                path,
                "rev-list",
                "--max-count=1",
                peeled.stdout.strip(),
                "--not",
                *sorted(covered),
            )
            if remaining.strip():
                issues.append(f"{module['path']}: {name}")
    return issues


def _fetch_pr(repo, pr):
    remote = repo.config["upstream_remote"]
    github._checked_remote(repo, remote, repo.config["upstream_repo"])
    digest = hashlib.sha256(pr["base_repo"].lower().encode()).hexdigest()[:12]
    reference = f"refs/rwgk/pr/{digest}/{pr['number']}/head"
    repo.git(
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "--recurse-submodules=no",
        remote,
        f"+{github.pr_head_ref(repo, pr['number'])}:{reference}",
    )
    if repo.oid(reference) != pr["head_oid"]:
        raise WTError(
            "PR head changed during housekeeping inspection; retry after inspecting the PR"
        )
    return pr["head_oid"]


def _trunk(repo):
    if not hasattr(repo, "_wt_housekeeping_trunk"):
        repo._wt_housekeeping_trunk = lifecycle._trunk_snapshot(repo)
    return repo._wt_housekeeping_trunk["oid"]


def _history(repo, entry, metadata, pr):
    head = _fetch_pr(repo, pr)
    local = entry["local_oid"]
    ahead, behind = map(
        int,
        repo.git("rev-list", "--left-right", "--count", f"{local}...{head}").split(),
    )
    entry["facts"].update(ahead=ahead, behind=behind)
    if pr["state"] == "OPEN":
        _reason(entry, "pr-open", f"PR #{pr['number']} is open")
        if stacks.get_stack(repo, entry["branch"]):
            entry["facts"]["stack"] = True
            if behind:
                entry["hints"].append(
                    "Use 'wt stack pull' to preview GitHub state, then '--apply' to adopt it; use 'wt stack rebase' for local history changes."
                )
            elif ahead:
                entry["hints"].append(
                    "Inspect the local stack; use authorized 'wt publish --stack' when ready to publish."
                )
            return
        if ahead and behind:
            entry["hints"].append(
                "Use 'wt pull --rebase' in this worktree to reconcile diverged commits."
            )
        elif ahead:
            boundary = github.replay_base(repo, entry["branch"], pr)
            rewound = (
                boundary
                and github._ancestor(repo, boundary, local)
                and not github._ancestor(repo, boundary, head)
            )
            entry["hints"].append(
                "Use 'wt pull --rebase' to reconcile the author's rewritten history."
                if rewound
                else "Inspect the local commits; use 'wt push' when ready to contribute them."
            )
        elif behind:
            entry["hints"].append(
                "Use 'wt pull' in this worktree to adopt the current PR head."
            )
        return
    if github._ancestor(repo, local, head):
        entry["coverage"] = {"pr_head_oid": head, "trunk_oid": None, "basis": "pr"}
        entry["facts"]["local_only_commits"] = 0
        return
    trunk = _trunk(repo)
    if github._ancestor(repo, local, trunk):
        entry["coverage"] = {"pr_head_oid": head, "trunk_oid": trunk, "basis": "trunk"}
        entry["facts"]["local_only_commits"] = 0
        return
    count = int(repo.git("rev-list", "--count", local, "^" + head, "^" + trunk))
    entry["facts"]["local_only_commits"] = count
    _reason(
        entry,
        "local-history",
        f"{count} local commit(s) outside the final PR history and upstream trunk",
        "Inspect these commits before deliberate retirement with 'wt clean'.",
    )


def assess(repo, branch, *, discard_builds=False):
    """Return facts and blockers without deleting, resetting, or changing a catalog."""
    metadata = repo.branches[branch]
    path = Path(metadata["path"]).resolve()
    entry = {
        "kind": "managed",
        "branch": branch,
        "path": str(path),
        "pr": copy.deepcopy(metadata.get("pr")),
        "pr_verified": False,
        "eligible": False,
        "reasons": [],
        "hints": [],
        "local_oid": None,
        "submodules": [],
        "coverage": None,
        "error": False,
        "facts": {},
    }
    if branch == repo.config["trunk"]:
        _reason(entry, "primary", "Primary trunk worktree")
        return entry
    if path == repo.primary.resolve():
        _reason(
            entry,
            "ownership",
            "A non-trunk branch is incorrectly registered at the primary worktree",
            "Inspect 'wt doctor' before cleanup.",
            error=True,
        )
        return entry
    selector = f"pr{entry['pr']['number']}" if entry["pr"] else branch
    entry["hints"].append(f"wt go {shlex.quote(selector)}")
    if metadata.get("creating"):
        _reason(
            entry,
            "incomplete-creation",
            "Worktree creation is incomplete",
            "Inspect 'wt doctor' before resuming creation.",
            error=True,
        )
    if not repo.exists("refs/heads/" + branch):
        _reason(
            entry,
            "missing-branch",
            "Managed branch is missing",
            "Inspect 'wt doctor'.",
            error=True,
        )
        return entry
    entry["local_oid"] = repo.oid("refs/heads/" + branch)
    if not path.is_dir():
        _reason(
            entry,
            "missing-worktree",
            "Managed worktree path is missing",
            "Inspect 'wt doctor'.",
            error=True,
        )
        return entry
    registered = [
        row for row in repo.worktrees() if Path(row["path"]).resolve() == path
    ]
    if len(registered) != 1 or registered[0]["branch"] != branch:
        pending = repo.state.get("pr_rebase")
        if (
            pending
            and pending.get("branch") == branch
            and Path(pending["path"]).resolve() == path
        ):
            _reason(
                entry,
                "unfinished-operation",
                "A PR pull rebase is paused in this worktree",
                "Use 'wt pull --continue' or 'wt pull --abort' there.",
            )
        else:
            _reason(
                entry,
                "ownership",
                "Registered worktree does not own the managed branch",
                "Inspect 'wt doctor'.",
                error=True,
            )
        return entry
    try:
        common = Path(
            repo.git(
                "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path
            )
        ).resolve()
        actual_root = Path(repo.git("rev-parse", "--show-toplevel", cwd=path)).resolve()
        actual_branch = repo.git(
            "symbolic-ref", "--quiet", "--short", "HEAD", cwd=path, check=False
        )
        if (
            common != repo.common.resolve()
            or actual_root != path
            or actual_branch != branch
        ):
            raise WTError("Worktree directory belongs to another checkout or branch")
        if repo.oid("refs/heads/" + branch) != entry["local_oid"]:
            raise WTError("Local branch changed during housekeeping inspection")
    except (WTError, OSError) as error:
        _reason(
            entry, "ownership", error, "Inspect 'wt doctor' before cleanup.", error=True
        )
        return entry
    if registered[0].get("locked"):
        _reason(
            entry,
            "locked",
            "Worktree is locked",
            "Inspect why the worktree was locked before cleanup.",
        )
    try:
        files = _working_files(repo, path)
        entry["facts"].update(files)
        if files["modified"] or files["untracked"]:
            _reason(
                entry,
                "working-files",
                f"{len(files['modified'])} modified/staged path(s), {len(files['untracked'])} untracked path(s)",
                "Inspect 'git status' and 'git diff' in this worktree.",
            )
        try:
            repo.ensure_idle(path)
        except WTError as error:
            _reason(
                entry,
                "unfinished-operation",
                error,
                "Finish or abort the pending operation before cleanup.",
            )
        _ignored(repo, entry, path, discard_builds)
        try:
            modules = submodules.inspect(repo, path)
            entry["submodules"] = modules
            for module in modules:
                _ignored(
                    repo,
                    entry,
                    path / module["path"],
                    discard_builds,
                    module=module["path"],
                )
            private = _module_work(repo, path, modules)
            if private:
                entry["facts"]["module_local_refs"] = private
                _reason(
                    entry,
                    "submodule-local-history",
                    "Submodule local work: " + ", ".join(private),
                    "Inspect and preserve these submodule refs before deliberate cleanup.",
                )
        except WTError as error:
            _reason(
                entry, "submodule-work", error, "Inspect submodule work before cleanup."
            )
        # Reuse the cleanup engine's hidden-index and full worktree checks. The
        # structured file/build/module reasons above explain ordinary blockers.
        if not entry["reasons"]:
            try:
                lifecycle._preflight_worktree(repo, path, branch, discard_builds)
            except WTError as error:
                _reason(
                    entry,
                    "worktree-check",
                    error,
                    "Inspect 'wt doctor' and the worktree state.",
                )
    except (WTError, OSError, ValueError) as error:
        _reason(
            entry,
            "inspection-failed",
            error,
            "Inspect the worktree before retrying housekeeping.",
            error=True,
        )
    saved = metadata.get("pr")
    if not saved:
        _reason(
            entry,
            "no-pr",
            "No associated PR; unpublished work stays visible",
            "Use 'wt publish', or explicitly 'wt drop --local-only' after inspecting it.",
        )
        return entry
    base_repo = saved.get("base_repo")
    if (
        not isinstance(base_repo, str)
        or base_repo.lower() != repo.config["upstream_repo"].lower()
    ):
        _reason(
            entry,
            "foreign-pr",
            "Associated PR belongs to another repository",
            "Inspect 'wt doctor' and correct the PR association before cleanup.",
            error=True,
        )
        return entry
    try:
        pr = github.get_pr(repo, saved["number"], saved["base_repo"])
        entry["pr"] = pr
        entry["pr_verified"] = True
        github._association_source(metadata, pr)
        _history(repo, entry, metadata, pr)
    except (WTError, OSError, ValueError, KeyError) as error:
        _reason(
            entry,
            "pr-check-failed",
            error,
            "Verify the PR and retry; its state was not assumed terminal.",
            error=True,
        )
    entry["eligible"] = bool(entry["coverage"]) and not entry["reasons"]
    return entry
