"""Conservative topology checks around the extension's cascading rebases."""

from pathlib import Path

from . import submodules
from .core import WTError


def _protect_ignored(repo, path):
    from .lifecycle import _check_ignored

    try:
        _check_ignored(repo, path, True)
    except WTError as error:
        raise WTError(
            f"Preserve ignored local material in submodule {path} before stack rebase: {error}"
        ) from error


def _require_paths(repo, path, names, revision=None):
    # Reuse the manager's validated, NUL-delimited gitlink parser.
    links = dict(submodules._links(repo, path, revision))
    missing = [name for name in names if name not in links]
    if missing:
        raise WTError(
            "Stack rebase removes or replaces an initialized submodule at "
            + ", ".join(str(path / name) for name in missing)
            + "; handle/deinitialize it explicitly before rebasing."
        )


def before(repo, stack, flags=()):
    """Return serializable old paths; caller persists them across continue/abort."""
    active = [
        row for row in stack["branches"] if not row.get("pullRequest", {}).get("merged")
    ]
    trunk = repo.config["trunk"]
    no_trunk = "--no-trunk" in flags
    owners = [(trunk, repo.primary)]
    owners.extend((row["branch"], repo.owner(row["branch"])) for row in active)
    captured = []
    for branch, path in owners:
        if path is None:
            raise WTError(f"Stack layer lacks its owning worktree: {branch}")
        path = Path(path).resolve()
        modules = submodules.inspect(repo, path)
        if not modules:
            continue
        names = []
        for module in modules:
            relative = Path(module["path"])
            if not any(Path(parent) in relative.parents for parent in names):
                names.append(module["path"])
            # A hard reset can erase even ignored module files when a gitlink
            # becomes a regular file. Protect unique material against a refetch
            # race or an extension plan different from these captured inputs.
            _protect_ignored(repo, path / relative)
        captured.append(
            {
                "branch": branch,
                "path": str(path),
                "modules": names,
                "heads": {module["path"]: module["oid"] for module in modules},
            }
        )
    if not captured:
        return []
    trunk_candidates = {repo.oid("refs/heads/" + trunk)}
    candidates = set() if no_trunk else set(trunk_candidates)
    if not no_trunk:
        repo.ensure_clean(repo.primary)
        remote = repo.config["upstream_remote"]
        if repo.config.get("upstream_repo"):
            from .github import remote_identity

            if (
                remote_identity(repo, remote).lower()
                != repo.config["upstream_repo"].lower()
            ):
                raise WTError(
                    "Upstream endpoint no longer matches the registered repository"
                )
        reference = f"refs/remotes/{remote}/{trunk}"
        repo.git(
            "fetch",
            "--no-tags",
            "--recurse-submodules=no",
            remote,
            f"+refs/heads/{trunk}:{reference}",
            cwd=repo.primary,
        )
        trunk_candidates.add(repo.oid(reference))
        candidates.update(trunk_candidates)
    replay = []
    for row in active:
        tip = repo.oid("refs/heads/" + row["branch"])
        candidates.add(tip)
        base = row.get("base")
        if base:
            base = repo.oid(base)
            candidates.add(base)
            replay.append((base, tip))
    for owner in captured:
        path = Path(owner["path"])
        names = owner["modules"]
        for revision in trunk_candidates if owner["branch"] == trunk else candidates:
            _require_paths(repo, path, names, revision)
        if owner["branch"] == trunk:
            continue
        for base, tip in replay:
            raw = repo.git(
                "log",
                "--raw",
                "--format=",
                "--no-renames",
                "-m",
                "-z",
                f"{base}..{tip}",
                "--",
                *(f":(literal){name}" for name in names),
                cwd=path,
            )
            for entry in raw.split("\0"):
                entry = entry.lstrip("\n")
                if entry.startswith(":") and entry[1:].split(" ", 2)[:2] != [
                    "160000",
                    "160000",
                ]:
                    raise WTError(
                        "Stack replay changes initialized submodule topology; "
                        "handle/deinitialize those submodules explicitly before rebasing."
                    )
    return captured


def after(repo, captured):
    """Stop before further synchronization when a captured gitlink disappeared."""
    for owner in captured:
        path = Path(owner["path"])
        if repo.owner(owner["branch"]) != path.resolve():
            raise WTError(
                "Worktree ownership changed during stack rebase; inspect recovery state"
            )
        _require_paths(repo, path, owner["modules"])
        for module in submodules._walk(repo, path, path, strict=False):
            if module["oid"] not in (
                owner.get("heads", {}).get(module["path"]),
                module["gitlink"],
            ):
                raise WTError(
                    f"Submodule HEAD changed during stack rebase: {path / module['path']}; "
                    "preserve/reconcile it before alignment"
                )


def before_resume(repo, captured):
    """Recheck module work without requiring conflicted parent checkouts to be clean."""
    for owner in captured:
        root = Path(owner["path"])
        branch = repo.git(
            "symbolic-ref", "--quiet", "--short", "HEAD", cwd=root, check=False
        )
        if repo.owner(owner["branch"]) != root.resolve() or branch != owner["branch"]:
            registered = [
                row
                for row in repo.worktrees()
                if Path(row["path"]).resolve() == root.resolve()
                and row["branch"] is None
            ]
            reservations = []
            for operation in ("rebase-merge/head-name", "rebase-apply/head-name"):
                marker = Path(
                    repo.git(
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-path",
                        operation,
                        cwd=root,
                    )
                )
                if marker.is_file():
                    reservations.append(marker.read_text().strip())
            if (
                branch
                or len(registered) != 1
                or reservations != ["refs/heads/" + owner["branch"]]
            ):
                raise WTError(
                    "Worktree ownership changed while stack rebase was paused"
                )
        heads = owner.get("heads")
        if not isinstance(heads, dict):
            raise WTError(
                "The saved rebase submodule snapshot is incomplete; inspect it first"
            )
        for name, expected in heads.items():
            module = submodules._path(root, name)
            if not submodules._initialized(repo, module):
                if module.is_dir() and any(module.iterdir()):
                    raise WTError(
                        f"Uninitialized submodule contains local files: {module}"
                    )
                continue
            submodules._clean(repo, module)
            if submodules._head(repo, module) != expected:
                raise WTError(
                    f"Submodule HEAD changed while stack rebase was paused: {module}; preserve/reconcile it first"
                )
            submodules.inspect(repo, module)
            _protect_ignored(repo, module)
