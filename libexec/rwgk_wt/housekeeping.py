"""Conservative repository inventory and recorded retirement of obsolete pairs."""

from __future__ import annotations

import contextlib
import copy
import shlex
from pathlib import Path

from . import github, lifecycle, stacks
from .core import WTError


def _assess(repo, branch, discard_builds):
    from .housekeeping_inspect import assess

    return assess(repo, branch, discard_builds=discard_builds)


def _retain(entry, code, message, hint="wt doctor", *, error=False):
    entry = copy.deepcopy(entry)
    entry["eligible"] = False
    entry.setdefault("reasons", []).append({"code": code, "message": str(message)})
    if hint and hint not in entry.setdefault("hints", []):
        entry["hints"].append(hint)
    entry["error"] = bool(entry.get("error") or error)
    return entry


def _entry(branch, path, kind, code, message, hint):
    return {
        "kind": kind,
        "branch": branch,
        "path": str(path) if path is not None else None,
        "pr": None,
        "pr_verified": False,
        "eligible": False,
        "reasons": [{"code": code, "message": message}],
        "hints": [hint],
        "error": False,
    }


def _inventory(repo, worktrees):
    """Report Git-owned objects outside the managed catalog without adopting them."""
    managed_paths = {
        Path(row["path"]).resolve() for row in repo.branches.values() if row.get("path")
    }
    registered = {row["branch"] for row in worktrees if row["branch"]}
    entries = []
    for row in worktrees:
        path = Path(row["path"]).resolve()
        if path == repo.primary.resolve() or path in managed_paths:
            continue
        branch = row["branch"]
        entries.append(
            _entry(
                branch,
                path,
                "legacy" if branch else "detached",
                "unmanaged-worktree" if branch else "detached-worktree",
                "Git-registered worktree is outside wt management"
                if branch
                else "Detached Git worktree requires deliberate inspection",
                "wt doctor",
            )
        )
    names = repo.git("for-each-ref", "--format=%(refname)", "refs/heads/")
    for reference in names.splitlines():
        branch = reference.removeprefix("refs/heads/")
        if (
            branch == repo.config["trunk"]
            or branch in repo.branches
            or branch in registered
        ):
            continue
        entries.append(
            _entry(
                branch,
                None,
                "unpaired",
                "unpaired-branch",
                "Local branch has no registered worktree or wt association",
                (
                    f"wt drop --local-only -- {shlex.quote(branch)}"
                    if branch.startswith("-")
                    else f"wt drop {shlex.quote(branch)} --local-only"
                ),
            )
        )
    return entries


@contextlib.contextmanager
def _retiring(repo, names):
    previous = getattr(repo, "_wt_retiring", None)
    repo._wt_retiring = set(names)
    try:
        yield
    finally:
        if previous is None:
            del repo._wt_retiring
        else:
            repo._wt_retiring = previous


def _ordering(repo):
    catalog = stacks.catalog(repo)
    positions = {}
    ancestors = {}
    for stack_index, stack in enumerate(catalog["stacks"]):
        names = [row["branch"] for row in stack["branches"]]
        for position, name in enumerate(names):
            if name in positions:
                raise WTError(f"Branch belongs to multiple local stacks: {name}")
            positions[name] = (stack_index, -position, name)
            ancestors[name] = set(names[:position])
    fallback = len(catalog["stacks"])
    return lambda name: positions.get(name, (fallback, 0, name)), ancestors


def _same_plan(entry, plan):
    if (
        plan.get("branch") != entry["branch"]
        or plan.get("local_oid") != entry.get("local_oid")
        or not plan.get("path")
        or not entry.get("path")
        or Path(plan["path"]).resolve() != Path(entry["path"]).resolve()
    ):
        raise WTError("Local branch or worktree changed since housekeeping assessment")
    expected, actual = entry.get("pr") or {}, plan.get("pr") or {}
    github.validate_pr_source(expected, actual)
    if (
        actual.get("head_oid") != expected.get("head_oid")
        or actual.get("state") != expected.get("state")
        or actual.get("state") not in ("MERGED", "CLOSED")
    ):
        raise WTError("PR state or head changed since housekeeping assessment")
    if plan.get("submodules", []) != entry.get("submodules", []):
        raise WTError("Submodule history changed since housekeeping assessment")


def _preview(repo, entries, discard_builds, order):
    """Remove blocked candidates until all remaining dependency checks agree."""
    candidates = {name for name, entry in entries.items() if entry["eligible"]}
    plans = {}
    while candidates:
        rejected = set()
        with _retiring(repo, candidates):
            for name in sorted(candidates, key=order):
                try:
                    stacks.check_retirement(repo, name)
                except WTError as error:
                    entries[name] = _retain(
                        entries[name], "stack-dependency", error, "wt stack pull"
                    )
                    rejected.add(name)
                    continue
                try:
                    plan = lifecycle.retire(
                        repo, name, apply=False, discard_builds=discard_builds
                    )
                    _same_plan(entries[name], plan)
                except (WTError, OSError, ValueError, KeyError) as error:
                    entries[name] = _retain(
                        entries[name],
                        "cleanup-preflight",
                        error,
                        f"wt clean pr{entries[name]['pr']['number']}",
                        error=True,
                    )
                    rejected.add(name)
                    continue
                plans[name] = plan
        if not rejected:
            break
        candidates.difference_update(rejected)
    return {name: plans[name] for name in sorted(candidates, key=order)}


def _reset_cache(repo):
    if hasattr(repo, "_wt_housekeeping_trunk"):
        del repo._wt_housekeeping_trunk


def _error_entry(repo, entry, error, plan=None):
    entry = _retain(entry, "cleanup-failed", error, error=True)
    if plan:
        name = plan["id"] + ".json"
        journal = lifecycle._operation_dir(repo) / name
        if journal.exists():
            entry["recovery_record"] = plan["id"]
            entry["recovery_path"] = str(
                Path(
                    plan.get("backtracking_path")
                    or lifecycle._backtracking_dir(repo) / name
                ).resolve()
            )
            entry["journal_path"] = str(journal.resolve())
            entry["hints"].insert(
                0, f"wt clean --resume {shlex.quote(plan['id'])} --apply"
            )
    return entry


def run(repo, *, apply=False, discard_builds=False):
    """Clean only proven-obsolete managed pairs, retaining a complete inventory."""
    _reset_cache(repo)
    worktrees = repo.worktrees()
    unmanaged = _inventory(repo, worktrees)
    issues = []
    try:
        repo.check_primary()
        repo.ensure_idle(repo.primary)
    except (WTError, OSError, ValueError, KeyError) as error:
        issues.append(
            {"code": "repository-blocked", "message": str(error), "hint": "wt doctor"}
        )
    entries = {}
    for name, metadata in list(repo.branches.items()):
        if name == repo.config["trunk"]:
            continue
        if issues:
            entry = _entry(
                name,
                metadata.get("path"),
                "managed",
                "repository-blocked",
                issues[0]["message"],
                "wt doctor",
            )
            entry["pr"] = copy.deepcopy(metadata.get("pr"))
        else:
            try:
                entry = _assess(repo, name, discard_builds)
            except (WTError, OSError, ValueError, KeyError) as error:
                entry = _error_entry(
                    repo,
                    _entry(
                        name,
                        metadata.get("path"),
                        "managed",
                        "assessment-failed",
                        "Could not assess this pair",
                        "wt doctor",
                    ),
                    error,
                )
                entry["pr"] = copy.deepcopy(metadata.get("pr"))
        entries[name] = entry
    errors = []
    operations = []
    if not issues:
        try:
            order, ancestors = _ordering(repo)
            plans = _preview(repo, entries, discard_builds, order)
        except (WTError, OSError, ValueError, KeyError) as error:
            issues.append(
                {"code": "stack-catalog", "message": str(error), "hint": "wt doctor"}
            )
            plans = {}
            ancestors = {}
            entries = {
                name: _retain(entry, "stack-catalog", error)
                for name, entry in entries.items()
            }
        if not apply:
            operations = list(plans.values())
        else:
            blocked = set()
            for name, plan in plans.items():
                if name in blocked:
                    entries[name] = _retain(
                        entries[name],
                        "dependent-cleanup-failed",
                        "A descendant's cleanup did not complete; retain this layer until recovery is resolved",
                        "wt doctor",
                    )
                    continue
                try:
                    _reset_cache(repo)
                    fresh = _assess(repo, name, discard_builds)
                    if not fresh["eligible"]:
                        entries[name] = fresh
                        blocked.update(ancestors.get(name, ()))
                        continue
                    _same_plan(fresh, plan)
                    # Never carry preview-only dependency exemptions into a
                    # destructive phase. Retired children must actually be gone.
                    stacks.check_retirement(repo, name)
                    lifecycle._save_record(repo, plan)
                    result = lifecycle._apply_retirement(repo, plan)
                except (WTError, OSError, ValueError, KeyError) as error:
                    entries[name] = _error_entry(repo, entries[name], error, plan)
                    blocked.update(ancestors.get(name, ()))
                    continue
                operations.append(result)
                entries.pop(name, None)
    if issues and apply:
        errors.extend(
            {"kind": "repository", "error": True, **issue} for issue in issues
        )
    errors.extend(entry for entry in entries.values() if entry.get("error"))
    remaining = [entry for entry in entries.values() if not entry.get("eligible")]
    remaining.extend(unmanaged)
    remaining.sort(key=lambda row: (row.get("branch") or "", row.get("path") or ""))
    return {
        "result": "housekeeping" if apply else "preview",
        "operations": operations,
        "remaining": remaining,
        "errors": errors,
        "issues": issues,
        "primary": str(repo.primary.resolve()),
    }
