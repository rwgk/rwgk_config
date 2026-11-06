"""Require manual copy-pr-bot approval for Ralf's public CUDA Python PRs."""

from __future__ import annotations

import hashlib
import shlex

from .core import WTError


def required(repo, base_repo, author):
    from . import github

    return (
        base_repo.lower() == "nvidia/cuda-python"
        and author.lower() == "rwgk"
        and github.provider(repo) == "github"
    )


def fetch_base_oid(repo, base_repo, base_ref):
    """Read the canonical target branch without moving a local worktree."""
    from . import github

    if not isinstance(base_ref, str):
        raise WTError(
            "GitHub did not identify the PR's base branch; no push was attempted"
        )
    ref = "refs/heads/" + base_ref
    if github._git_result(repo, "check-ref-format", ref).returncode:
        raise WTError(
            "GitHub returned an invalid PR base branch; no push was attempted"
        )
    digest = hashlib.sha256((base_repo.lower() + ":" + ref).encode()).hexdigest()
    observed = f"refs/rwgk/signing/{digest}/base"
    repo.git(
        "fetch",
        "--no-tags",
        "--recurse-submodules=no",
        github.source_endpoint(repo, base_repo),
        f"+{ref}:{observed}",
    )
    return repo.oid(observed)


def ensure_unsigned(repo, *, branch, base_oid, head_oid, pr=None, stack=False):
    """Inspect signature presence, without requiring verification keys."""
    for oid in repo.git("rev-list", f"{base_oid}..{head_oid}").splitlines():
        headers = repo.git("cat-file", "commit", oid).partition("\n\n")[0]
        if not any(
            line.startswith(("gpgsig ", "gpgsig-sha256 "))
            for line in headers.splitlines()
        ):
            return
    label = f"PR #{pr['number']} ({branch})" if pr else branch
    worktree = shlex.quote(str(repo.owner(branch)))
    message = (
        f"{label} needs at least one unsigned commit in its own PR commit range "
        "to require manual copy-pr-bot approval. No push was attempted.\n"
        "In its owning worktree, add an unsigned empty commit:\n"
        f"  git -C {worktree} commit --no-gpg-sign --allow-empty --only "
        '-m "ci: require manual copy-pr-bot approval"'
    )
    if stack:
        message += (
            "\nThen restack dependent layers from the same owning worktree:\n"
            f"  git -C {worktree} wt stack rebase --upstack --no-trunk"
        )
    raise WTError(message)
