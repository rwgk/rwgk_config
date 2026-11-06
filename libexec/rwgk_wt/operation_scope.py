"""Keep independent PR synchronization outside another worktree's recovery scope."""

from __future__ import annotations

import json
from pathlib import Path

from .core import WTError


def _path(value):
    return (
        Path(value).resolve()
        if isinstance(value, str) and Path(value).is_absolute()
        else None
    )


def _stack_scope(repo, stack):
    worktrees = [{"branch": repo.config["trunk"], "path": str(repo.primary.resolve())}]
    trunk = stack["trunk"]["branch"]
    if trunk != repo.config["trunk"]:
        path = _path(repo.branches.get(trunk, {}).get("path"))
        if path is not None:
            worktrees.append({"branch": trunk, "path": str(path)})
    for row in stack["branches"]:
        path = _path(repo.branches.get(row["branch"], {}).get("path"))
        if path is None:
            if row.get("pullRequest", {}).get("merged"):
                continue
            return None
        worktrees.append({"branch": row["branch"], "path": str(path)})
    return {
        "stack_id": stack.get("id"),
        "stack_number": stack.get("number"),
        "trunk": stack["trunk"]["branch"],
        "branches": [row["branch"] for row in stack["branches"]],
        "worktrees": worktrees,
    }


def stack_scope(repo, stack):
    scope = _stack_scope(repo, stack)
    if scope is None:
        raise WTError(
            "Cannot reserve the stack's worktrees before rebasing; inspect 'wt doctor'."
        )
    return scope


def _restack_scope(repo):
    from .stacks import catalog

    operation = repo.state.get("submodule_rebase") or {}
    scope = operation.get("scope")
    stacks = catalog(repo)["stacks"]
    if scope is None:
        captured = {
            row.get("branch")
            for row in operation.get("captured", [])
            if row.get("branch") != repo.config["trunk"]
        }
        if not captured:
            return None
        matches = [
            stack
            for stack in stacks
            if captured <= {row["branch"] for row in stack["branches"]}
        ]
        scope = _stack_scope(repo, matches[0]) if len(matches) == 1 else None
        if scope is not None:
            reserved = {row["branch"]: _path(row["path"]) for row in scope["worktrees"]}
            if any(
                reserved.get(row.get("branch")) != _path(row.get("path"))
                for row in operation.get("captured", [])
            ):
                return None
        return scope
    if not isinstance(scope, dict) or not isinstance(scope.get("worktrees"), list):
        return None
    matches = [
        stack
        for stack in stacks
        if [row["branch"] for row in stack["branches"]] == scope.get("branches")
        and (scope.get("stack_id") is None or stack.get("id") == scope["stack_id"])
    ]
    if len(matches) != 1:
        return None
    expected = _stack_scope(repo, matches[0])
    if expected is None:
        return None
    actual = {}
    for row in scope["worktrees"]:
        if not isinstance(row, dict) or not isinstance(row.get("branch"), str):
            return None
        path = _path(row.get("path"))
        if path is None or row["branch"] in actual:
            return None
        actual[row["branch"]] = path
    for row in [*expected["worktrees"], *operation.get("captured", [])]:
        if actual.get(row.get("branch")) != _path(row.get("path")):
            return None
    return {**scope, "trunk": scope.get("trunk", expected["trunk"])}


def _overlaps(path, reserved):
    return (
        path == reserved
        or path.is_relative_to(reserved)
        or reserved.is_relative_to(path)
    )


def blocks(repo, kind, path):
    if not getattr(repo, "_wt_independent_sync", False):
        return True
    operation = repo.state.get(kind) or {}
    if kind == "pr_rebase":
        owner = _path(operation.get("path"))
        paths = [owner] if owner else None
    else:
        scope = _restack_scope(repo)
        paths = [_path(row["path"]) for row in scope["worktrees"]] if scope else None
    return paths is None or any(
        _overlaps(Path(path).resolve(), owner) for owner in paths
    )


def recovery_location(repo, kind):
    operation = repo.state.get(kind) or {}
    if kind == "pr_rebase":
        return _path(operation.get("path"))
    scope = _restack_scope(repo)
    rows = scope["worktrees"] if scope else operation.get("captured", [])
    return next(
        (
            _path(row.get("path"))
            for row in rows
            if row.get("branch") in (scope["branches"] if scope else ())
            or scope is None
            and row.get("branch") != repo.config["trunk"]
        ),
        None,
    )


def require_restack_owner(repo):
    scope = _restack_scope(repo)
    branch = repo.current_branch()
    owner = (
        next(
            (
                row
                for row in scope["worktrees"]
                if row["branch"] in scope["branches"]
                and _path(row["path"]) == repo.root.resolve()
            ),
            None,
        )
        if scope
        else None
    )
    if owner:
        if branch == owner["branch"]:
            return
        if branch is None and repo.state["submodule_rebase"].get("phase") == "rebasing":
            reservations = []
            for name in ("rebase-merge/head-name", "rebase-apply/head-name"):
                marker = Path(
                    repo.git("rev-parse", "--path-format=absolute", "--git-path", name)
                )
                if marker.is_file():
                    reservations.append(marker.read_text().strip())
            if reservations == ["refs/heads/" + owner["branch"]]:
                return
    location = recovery_location(repo, "submodule_rebase")
    suffix = (
        f" Resume it from {location}."
        if location
        else " Inspect its saved recovery state."
    )
    raise WTError(
        "Another stack has pending submodule work; this worktree cannot resume or replace it."
        + suffix
    )


def native_blocks(repo, name, path):
    if name != "gh-stack-rebase-state" or not getattr(
        repo, "_wt_independent_sync", False
    ):
        return True
    try:
        native = json.loads((repo.common / name).read_text())
        if not isinstance(native, dict):
            return True
        scope = _restack_scope(repo)
        if scope is None and not repo.state.get("submodule_rebase"):
            from .stacks import catalog

            matches = [
                stack
                for stack in catalog(repo)["stacks"]
                if stack.get("id") == native.get("stackId")
                and [row["branch"] for row in stack["branches"]]
                == native.get("stackBranches")
                and stack["trunk"]["branch"]
                in (native.get("stackTrunk"), native.get("targetTrunk"))
            ]
            scope = _stack_scope(repo, matches[0]) if len(matches) == 1 else None
        if scope is None or any(
            _overlaps(Path(path).resolve(), _path(row["path"]))
            for row in scope["worktrees"]
        ):
            return True
        if (
            native.get("executionMode") not in (None, "")
            or native.get("stackBranches") != scope["branches"]
            or not scope.get("stack_id")
            or native.get("stackId") != scope["stack_id"]
            or native.get("stackTrunk") != scope["trunk"]
            or native.get("targetTrunk")
            not in (None, "", scope["trunk"], repo.config["trunk"])
        ):
            return True
        worktrees = native.get("worktrees")
        if not isinstance(worktrees, dict):
            return True
        reserved = {row["branch"]: _path(row["path"]) for row in scope["worktrees"]}
        original_stack = native.get("originalStack")
        if (
            not isinstance(original_stack, dict)
            or original_stack.get("id") != native["stackId"]
            or [row.get("branch") for row in original_stack.get("branches", [])]
            != scope["branches"]
            or original_stack.get("trunk", {}).get("branch") != native["stackTrunk"]
        ):
            return True
        allowed = set(reserved)
        touched = worktrees.get("touched") or {}
        if not isinstance(touched, dict) or not set(touched) <= allowed:
            return True
        if worktrees.get("pending") and worktrees["pending"] not in allowed:
            return True
        owners = worktrees.get("owners") or {}
        if not isinstance(owners, dict):
            return True
        for branch, owner in owners.items():
            if branch in allowed and (
                not isinstance(owner, dict)
                or _path(owner.get("path")) != reserved.get(branch)
            ):
                return True
        origin = worktrees.get("origin")
        if (
            not isinstance(origin, dict)
            or _path(origin.get("path")) not in reserved.values()
        ):
            return True
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    return False
