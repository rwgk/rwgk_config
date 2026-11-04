"""Find existing managed worktrees in the bounded shared worktree layout."""

from __future__ import annotations

import os
import re
import shlex
from collections import Counter, defaultdict
from pathlib import Path

from .core import Repo, WTError, request_prefix, run_command


def trees_root():
    return (Path(os.environ.get("W", str(Path.home() / "wrk"))) / "trees").resolve()


def split_selector(selector):
    if "://" in selector or ":" not in selector:
        return None, selector
    key, _, value = selector.partition(":")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key) or not value:
        raise WTError("Use <repo-key>:<selector> to choose a repository.")
    return key, value


def _repositories(root, key):
    """Read each discovered catalog once; aliases do not create extra candidates."""
    try:
        groups = sorted(root.iterdir())
    except FileNotFoundError:
        return []
    repositories = {}
    seen = set()
    for group in groups:
        if group.name.startswith(".") or key is not None and group.name != key:
            continue
        if group.is_symlink() or not group.is_dir():
            continue
        for child in sorted(group.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            path = child.resolve()
            if path in seen or path.parent != group.resolve():
                continue
            seen.add(path)
            if not (path / ".git").exists():
                continue
            probe = run_command(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=path,
                check=False,
            )
            if probe.returncode:
                continue
            common = Path(probe.stdout.strip()).resolve()
            if not (common / "rwgk-wt.json").exists():
                continue
            if common not in repositories:
                repositories[common] = (Repo(path), set())
            repo, discovered = repositories[common]
            if (
                repo.config.get("key") == group.name
                and Path(repo.config.get("trees_root", "")).resolve() == root
            ):
                discovered.add(path)
    return [(repo, paths) for repo, paths in repositories.values() if paths]


def _matches(repo, branch, row, selector, command):
    request = re.fullmatch(r"(pr|mr)?([0-9]+)", selector)
    constrained = command if command in ("pr", "mr") else None
    if request and request[1]:
        if constrained is not None and constrained != request[1]:
            return False
        constrained = request[1]
    pr = row.get("pr") or {}
    if constrained and request_prefix(pr, repo.config) != constrained:
        return False
    if request or command in ("pr", "mr") or "://" in selector:
        if request:
            number = int(request[2])
        else:
            from .github import _pr_selector

            try:
                number = _pr_selector(repo, selector)
            except WTError:
                return False
        return (
            number > 0
            and pr.get("number") == number
            and pr.get("base_repo", "").lower()
            == repo.config.get("upstream_repo", "").lower()
        )
    return branch in (selector, "rwgk/" + selector)


def resolve(selector, command="go"):
    """Return one verified existing pair, with no forge calls or lifecycle writes."""
    key, selector = split_selector(selector)
    root = trees_root()
    matches = []
    for repo, discovered in _repositories(root, key):
        owners = defaultdict(list)
        for worktree in repo.worktrees():
            if worktree["branch"]:
                owners[worktree["branch"]].append(Path(worktree["path"]).resolve())
        for branch, row in repo.branches.items():
            path = Path(row.get("path", "")).resolve()
            if path not in discovered or not _matches(
                repo, branch, row, selector, command
            ):
                continue
            branch_owners = owners[branch]
            if len(branch_owners) > 1:
                raise WTError(
                    f"Branch {branch} has multiple owning worktrees; run 'wt doctor' in {path}."
                )
            owner = branch_owners[0] if branch_owners else None
            if owner is None and repo.state.get("pr_rebase"):
                from .pr_rebase import navigation_path

                owner = navigation_path(repo, branch)
            if owner != path or not path.is_dir():
                continue
            value = {"key": repo.config["key"], "branch": branch, "path": str(path)}
            if row.get("pr"):
                value["pr"] = row["pr"]
            matches.append((repo, value, path))
    if not matches:
        qualifier = f"{key}:" if key else ""
        raise WTError(
            f"No existing managed worktree matches {qualifier + selector!r} under {root}. "
            "Open or create its worktree from the repository first."
        )
    if len(matches) > 1:
        counts = Counter(value["key"] for _, value, _ in matches)
        lines = [
            f"Multiple existing managed worktrees match {selector!r}; choose a repository:"
        ]
        for _, value, path in matches:
            qualified = selector if counts[value["key"]] == 1 else value["branch"]
            lines.extend((f"  {value['key']}: {value['branch']}", f"    {path}"))
            if value.get("pr", {}).get("url"):
                lines.append(f"    {value['pr']['url']}")
            hint = shlex.join(["wt", "go", f"{value['key']}:{qualified}"])
            lines.append(f"    Next: {hint}")
        raise WTError("\n".join(lines))
    return matches[0]
