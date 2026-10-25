"""Worktree-preserving adapters around gh-stack, with no composite sync/prune."""

from __future__ import annotations

import contextlib
import fcntl
import json
import sys
from pathlib import Path

from .core import WTError, atomic_json, branch_name

BIN = Path(__file__).resolve().parents[2] / "bin"


def catalog(repo):
    path = repo.common / "gh-stack"
    if not path.exists():
        return {"schemaVersion": 1, "stacks": []}
    try:
        value = json.loads(path.read_text())
        if value.get("schemaVersion") != 1 or not isinstance(value.get("stacks"), list):
            raise ValueError("unsupported schema")
        for stack in value["stacks"]:
            if not isinstance(
                stack.get("trunk", {}).get("branch"), str
            ) or not isinstance(stack.get("branches"), list):
                raise TypeError("invalid stack")
            if not all(isinstance(row.get("branch"), str) for row in stack["branches"]):
                raise ValueError("invalid layer")
        return value
    except (ValueError, OSError, TypeError, AttributeError) as error:
        raise WTError(f"Cannot read gh-stack catalog: {error}") from error


def get_stack(repo, branch=None):
    branch = branch or repo.current_branch()
    matches = [
        stack
        for stack in catalog(repo)["stacks"]
        if any(row["branch"] == branch for row in stack["branches"])
    ]
    if len(matches) > 1:
        raise WTError(f"Branch {branch} belongs to multiple stacks.")
    return matches[0] if matches else None


@contextlib.contextmanager
def catalog_lock(repo):
    """Hold extension locks, allowing nested lifecycle helpers on the same Repo."""
    depth = getattr(repo, "_wt_stack_lock_depth", 0)
    if depth:
        repo._wt_stack_lock_depth = depth + 1
        try:
            yield
        finally:
            repo._wt_stack_lock_depth -= 1
        return
    with contextlib.ExitStack() as cleanup:
        for name in ("gh-stack-operation.lock", "gh-stack.lock"):
            stream = cleanup.enter_context((repo.common / name).open("a+"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WTError(f"Another stack operation holds {name}.") from error
        repo._wt_stack_lock_depth = 1
        try:
            yield
        finally:
            repo._wt_stack_lock_depth = 0


def _command(repo, *args, cwd=None):
    # Process-scoped configuration avoids changing global or existing branch tracking.
    result = repo.run(
        [
            "env",
            "GIT_CONFIG_COUNT=1",
            "GIT_CONFIG_KEY_0=remote.pushDefault",
            f"GIT_CONFIG_VALUE_0={repo.config['upstream_remote']}",
            f"GH_REPO={repo.config['upstream_repo']}",
            "gh",
            "stack",
            *args,
        ],
        cwd=cwd,
    )
    return result.stdout


def _owners(repo, stack):
    repo.check_primary()
    for row in stack["branches"]:
        if row.get("pullRequest", {}).get("merged"):
            continue
        name = row["branch"]
        if name not in repo.branches:
            raise WTError(
                f"Stack layer {name} is unmanaged; adopt it before this operation."
            )
        path = repo.owner(name)
        if path is None or path != Path(repo.branches[name]["path"]).resolve():
            raise WTError(f"Stack layer {name} lacks its registered owning worktree.")
        repo.ensure_clean(path)


def initialize(repo):
    repo.check_primary()
    name = repo.resolve()
    if get_stack(repo):
        raise WTError("Current branch already belongs to a stack.")
    path = repo.owner(name)
    repo.ensure_clean(path)
    _command(repo, "init", "--base", repo.config["trunk"], name, cwd=repo.primary)
    return {"stack": get_stack(repo, name), "path": str(path)}


def add(repo, slug):
    current = repo.resolve()
    stack = get_stack(repo, current)
    if not stack:
        initialize(repo)
        stack = get_stack(repo, current)
    active = [
        row for row in stack["branches"] if not row.get("pullRequest", {}).get("merged")
    ]
    if not active or active[-1]["branch"] != current:
        raise WTError("Add a layer from the top worktree; use 'wt stack go top'.")
    repo.ensure_clean(repo.owner(current))
    name = branch_name(slug)
    path = repo.create(name, "refs/heads/" + current)
    try:
        _command(repo, "add", name, cwd=repo.owner(current))
    except WTError as error:
        raise WTError(
            f"Pair created at {path}, but stack registration failed: {error}"
        ) from error
    return path


def navigate(repo, target):
    current = repo.resolve()
    stack = get_stack(repo, current)
    if not stack:
        raise WTError("Current branch does not belong to a stack.")
    names = [
        row["branch"]
        for row in stack["branches"]
        if not row.get("pullRequest", {}).get("merged")
    ]
    if not names:
        raise WTError("This stack has no active layers.")
    index = names.index(current) if current in names else 0
    selected = {
        "top": names[-1],
        "bottom": names[0],
        "up": names[min(index + 1, len(names) - 1)],
        "down": names[max(index - 1, 0)],
    }[target]
    branch = repo.resolve(selected)
    path = repo.owner(branch)
    if path is None or path != Path(repo.branches[branch]["path"]).resolve():
        raise WTError(f"Missing owning worktree: {branch}")
    return path


def rebase(repo, flags=()):
    recovery = "--continue" in flags or "--abort" in flags
    if not recovery:
        stack = get_stack(repo)
        if not stack:
            raise WTError("Current branch does not belong to a stack.")
        _owners(repo, stack)
        # Keep every local pre-rewrite tip alive, without publishing local-only work.
        from .lifecycle import protect_local

        protect_local(
            repo,
            [
                {"oid": repo.oid(row["branch"]), "label": row["branch"]}
                for row in stack["branches"]
                if repo.exists("refs/heads/" + row["branch"])
            ],
            kind="rebase",
        )
    _command(repo, "rebase", "--remote", repo.config["upstream_remote"], *flags)
    repo.check_primary()
    return {
        "result": "rebased" if "--abort" not in flags else "aborted",
        "stack": get_stack(repo),
    }


def pull(repo, number=None, apply=False):
    stack = get_stack(repo)
    if not stack:
        raise WTError("No existing local stack; create/adopt its pairs first.")
    _owners(repo, stack)
    number = number or stack.get("number")
    if not number:
        raise WTError("Supply the native GitHub stack number with --stack NUMBER.")
    argv = [
        sys.executable,
        str(BIN / "gh_stack_adopt_from_github.py"),
        str(number),
        "--worktree",
        str(repo.root),
        "--repo",
        repo.config["upstream_repo"],
        "--remote",
        repo.config["upstream_remote"],
        "--json",
    ]
    if apply:
        from .lifecycle import protect_local

        protect_local(
            repo,
            [
                {"oid": repo.oid(row["branch"]), "label": row["branch"]}
                for row in stack["branches"]
                if not row.get("pullRequest", {}).get("merged")
                and repo.exists("refs/heads/" + row["branch"])
            ],
            kind="stack-pull",
        )
        argv.append("--apply")
    result = json.loads(repo.run(argv).stdout)
    if apply:
        from .github import get_pr

        for row in result.get("rows", []):
            name = row["branch"]
            if (
                name not in repo.branches
                or name == repo.config["trunk"]
                or row["old"] == row["target"]
            ):
                continue
            pending = repo.branches[name].setdefault("pending_archive", [])
            if not any(item["oid"] == row["old"] for item in pending):
                pending.append({"oid": row["old"], "label": "pre-adoption-" + name})
        repo.save()
        for row in get_stack(repo)["branches"]:
            name = row["branch"]
            if name not in repo.branches or not row.get("pullRequest"):
                continue
            pr = get_pr(repo, row["pullRequest"]["number"])
            repo.alias(name, pr)
            publication = repo.branches[name].get("publication")
            if publication and not row["pullRequest"].get("merged"):
                publication["oid"] = repo.oid(name)
        repo.save()
    return result


def refresh(repo):
    """Refresh live PR metadata, then align replay boundaries after verified pushes."""
    _command(repo, "view", "--json")
    with catalog_lock(repo):
        data = catalog(repo)
        stack = get_stack(repo)
        if not stack:
            raise WTError("Publication lost its local stack association.")
        selected = next(
            item for item in data["stacks"] if item.get("id") == stack.get("id")
        )
        parent = repo.oid(selected["trunk"]["branch"])
        selected["trunk"]["head"] = parent
        for row in selected["branches"]:
            if row.get("pullRequest", {}).get("merged"):
                continue
            name = row["branch"]
            head = repo.oid(name)
            if repo.run(
                ["git", "merge-base", "--is-ancestor", parent, head], check=False
            ).returncode:
                raise WTError(
                    f"Restack {name} before publication handoff; it does not contain its parent."
                )
            row["head"], row["base"] = head, parent
            pr = repo.branches.get(name, {}).get("pr")
            if pr:
                row["pullRequest"] = {
                    **row.get("pullRequest", {}),
                    "number": pr["number"],
                    "url": pr["url"],
                    "merged": pr["state"] == "MERGED",
                }
            parent = head
        atomic_json(repo.common / "gh-stack", data)
    return get_stack(repo)


def check_retirement(repo, branch):
    stack = get_stack(repo, branch)
    if not stack:
        return
    names = [row["branch"] for row in stack["branches"]]
    position = names.index(branch)
    descendants = [
        row
        for row in stack["branches"][position + 1 :]
        if row["branch"] in repo.branches
        and row["branch"] not in getattr(repo, "_wt_retiring", set())
        and not row.get("pullRequest", {}).get("merged")
    ]
    if not descendants:
        return
    entry = stack["branches"][position]
    if not entry.get("pullRequest", {}).get("merged"):
        raise WTError(
            "This layer has active descendants. Adopt/restack the surviving stack before cleanup."
        )
    # A retained merged tombstone is safe only once the next active replay base
    # follows the effective surviving parent rather than the retired old head.
    parent = repo.oid(stack["trunk"]["branch"])
    for row in stack["branches"][:position]:
        if not row.get("pullRequest", {}).get("merged"):
            parent = repo.oid(row["branch"])
    child = descendants[0]
    if (
        child.get("base") != parent
        or repo.run(
            ["git", "merge-base", "--is-ancestor", parent, repo.oid(child["branch"])],
            check=False,
        ).returncode
    ):
        raise WTError(
            "Surviving replay boundaries need 'wt stack pull --apply' or a rebase before cleanup."
        )


def retired(repo, branch):
    # Called before removing this branch from wt's catalog, after its ref is gone.
    with catalog_lock(repo):
        data = catalog(repo)
        changed = False
        surviving = []
        for stack in data["stacks"]:
            rows = stack["branches"]
            if not any(row["branch"] == branch for row in rows):
                surviving.append(stack)
                continue
            changed = True
            managed = {
                row["branch"]
                for row in rows
                if row["branch"] != branch and row["branch"] in repo.branches
            }
            if not managed:
                continue
            kept = []
            for position, row in enumerate(rows):
                name = row["branch"]
                merged = row.get("pullRequest", {}).get("merged")
                retiring = name == branch or (name not in repo.branches and merged)
                if retiring:
                    # A deleted unmerged layer must not remain an active parent.
                    # Merged tombstones matter only to surviving later layers.
                    needed = merged and any(
                        later["branch"] in managed for later in rows[position + 1 :]
                    )
                    if not needed:
                        continue
                kept.append(row)
            stack["branches"] = kept
            surviving.append(stack)
        if changed:
            data["stacks"] = surviving
            atomic_json(repo.common / "gh-stack", data)


def save_native_identity(repo, number, native_id):
    """Record independently verified GitHub stack identity without rewriting refs."""
    with catalog_lock(repo):
        current = get_stack(repo)
        if not current:
            raise WTError("No local stack for the verified GitHub identity.")
        data = catalog(repo)
        selected = next(
            item for item in data["stacks"] if item.get("id") == current.get("id")
        )
        if selected.get("number") and selected["number"] != number:
            raise WTError("Local and remote stack numbers disagree.")
        selected["number"] = number
        selected["id"] = str(native_id)
        atomic_json(repo.common / "gh-stack", data)
    return get_stack(repo)
