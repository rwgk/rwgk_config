"""Local-only deletion of unpaired branches with shared backtracking records."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import lifecycle
from .core import Repo, WTError


def delete(repo, branch):
    """Preserve and delete one exact local branch without registering the repo."""
    with repo.locked():
        primary = Path(repo.worktrees()[0]["path"])
        repo.config.setdefault("primary", str(primary))
        repo.config.setdefault("key", primary.name.removesuffix(".git") or "repository")
        repo.config.setdefault("trunk", None)
        ref = "refs/heads/" + branch
        valid = repo.run(["git", "check-ref-format", ref], check=False)
        if valid.returncode or not repo.exists(ref):
            raise WTError(f"No exact local branch named {branch!r}")
        if branch in repo.branches:
            raise WTError(
                "Branch is managed by wt; use 'wt clean' or 'wt drop --local-only'."
            )
        lifecycle._preflight_unpaired(repo, branch)
        oid = repo.oid(ref)
        record = lifecycle._new_record(repo, branch, {}, kind="local-branch-delete")
        record.update(
            local_oid=oid,
            path=None,
            local_only=True,
            branch_config=lifecycle._branch_config(repo, branch),
            tracking=repo.git("for-each-ref", "--format=%(upstream)", ref),
            snapshots=[{"oid": oid, "label": "local"}],
        )
        lifecycle._save_record(repo, record)
        lifecycle._protect_snapshots(repo, record)
        lifecycle._preflight_unpaired(repo, branch)
        repo.git("update-ref", "--no-deref", "-d", ref, oid)
        record["completed"].append("local_branch")
        lifecycle._save_record(repo, record)
        if record["branch_config"]:
            repo.git("config", "--local", "--remove-section", "branch." + branch)
        record["completed"].append("branch_config")
        record["status"] = "complete"
        lifecycle._save_record(repo, record)
        return record


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="git_branch_D_track_hash.py",
        description=(
            "Delete one unpaired local branch after saving its tip and configuration. "
            "Works without wt init; worktree-owned and wt-managed branches are refused."
        ),
    )
    parser.add_argument("branch", help="exact existing local branch name")
    args = parser.parse_args(argv)
    try:
        record = delete(Repo(initialized=False), args.branch)
    except (WTError, OSError, ValueError, KeyError) as error:
        print(f"git_branch_D_track_hash.py: {error}", file=sys.stderr)
        return 1
    print(f"Deleted local branch {record['branch']} ({record['local_oid']}).")
    print(f"Backtracking: {record['backtracking_path']}")
    print(f"Recovery: {record['snapshots'][0]['recovery_ref']}")
    return 0
