"""Fast-forward an associated independent PR without configuring Git tracking."""

from __future__ import annotations

import hashlib
import re

from . import github
from .core import WTError


def _effective_url(repo, url, expected):
    """Check push URL rewrites without saving a remote or trusting an alias."""
    config = github._git_result(
        repo,
        "config",
        "--null",
        "--get-regexp",
        r"^url\..*\.(insteadof|pushinsteadof)$",
    )
    if config.returncode not in (0, 1):
        raise WTError("Cannot inspect Git URL rewrites before pushing")
    rules = {"insteadof": [], "pushinsteadof": []}
    for record in config.stdout.split("\0"):
        if not record:
            continue
        key, prefix = record.split("\n", 1)
        base, kind = key[4:].rsplit(".", 1)
        rules[kind].append((prefix, base))

    def rewritten(value, kind):
        matches = [rule for rule in rules[kind] if value.startswith(rule[0])]
        if not matches:
            return value
        prefix, base = max(matches, key=lambda rule: len(rule[0]))
        return base + value[len(prefix) :]

    def checked(value):
        try:
            identity = github._identity(value)
        except WTError as error:
            raise WTError(
                "Git URL rewrites change the PR's push destination; inspect Git configuration"
            ) from error
        if identity.lower() != expected.lower():
            raise WTError(
                "Git URL rewrites change the PR's push destination; inspect Git configuration"
            )
        return value

    # Passing an already-resolved URL to Git can otherwise trigger another URL
    # rewrite. Resolve to a stable endpoint and validate every intermediate hop.
    seen = set()
    for _ in range(8):
        if url in seen:
            raise WTError(
                "GitHub push URL rewrites form a cycle; inspect Git configuration"
            )
        seen.add(url)
        pushed = checked(rewritten(url, "pushinsteadof"))
        effective = checked(rewritten(pushed, "insteadof"))
        if effective == url:
            return url
        url = effective
    raise WTError("Too many GitHub push URL rewrites; inspect Git configuration")


def _endpoint(repo, identity):
    available = repo.git("remote").splitlines()
    preferred = (
        repo.config.get("publish_remote"),
        repo.config.get("upstream_remote"),
        repo.config.get("archive_remote"),
    )
    for remote in dict.fromkeys([*preferred, *available]):
        if remote not in available:
            continue
        try:
            actual = github.remote_identity(repo, remote)
        except WTError:
            continue
        if actual.lower() == identity.lower():
            github._checked_remote(repo, remote, identity)
            return _effective_url(
                repo, github._remote_url(repo, remote, push=True), identity
            )
    return _effective_url(repo, f"git@github.com:{identity}.git", identity)


def _local_unchanged(repo, branch, oid):
    repo.ensure_idle()
    if (
        repo.current_branch() != branch
        or repo.owner(branch) != repo.root
        or repo.oid(f"refs/heads/{branch}") != oid
    ):
        raise WTError(
            "The local branch changed during push; inspect it before retrying"
        )


def _push_error(result):
    detail = (result.stderr or result.stdout).strip()
    detail = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", detail)
    lower = detail.lower()
    if any(word in lower for word in ("stale info", "fetch first", "non-fast-forward")):
        raise WTError(
            "The PR branch changed during push. Run 'wt pull' or 'wt pull --rebase' "
            "to inspect and reconcile it before retrying. No lease was refreshed."
        )
    if any(
        word in lower
        for word in ("permission", "denied", "403", "write access", "not allowed")
    ):
        raise WTError(
            "GitHub refused the PR push. Check write access and the contributor's "
            "'Allow edits from maintainers' setting. No alternate branch was created."
            + (f"\n{detail}" if detail else "")
        )
    raise WTError(
        "The PR push failed; inspect authentication and the source branch before retrying. "
        "No lease was refreshed." + (f"\n{detail}" if detail else "")
    )


def push(repo):
    """Publish only committed HEAD, retaining review ownership and stable paths."""
    branch, metadata = github.current_pr(repo, action="push")
    repo.ensure_idle()
    oid = repo.oid(f"refs/heads/{branch}")
    saved = metadata["pr"]
    pr = github.get_pr(repo, saved["number"], saved["base_repo"])
    github.validate_pr_source(saved, pr)
    if pr["state"] != "OPEN":
        raise WTError(
            f"PR #{pr['number']} is {pr['state'].lower()}; its branch was not pushed"
        )
    identity = pr.get("head_repo")
    if not identity:
        raise WTError(
            "The PR's source repository is unavailable; its branch cannot be pushed"
        )
    ref = "refs/heads/" + pr["head_ref"]
    if github._git_result(repo, "check-ref-format", ref).returncode:
        raise WTError("GitHub returned an invalid PR source branch")
    endpoint = _endpoint(repo, identity)
    old = github._remote_tip(repo, endpoint, ref)
    if old is None:
        raise WTError(
            "The PR's source branch is missing; 'wt push' will not recreate it"
        )
    if old != pr["head_oid"]:
        raise WTError(
            "GitHub and the PR source branch disagree; run 'wt pull' and inspect before pushing"
        )
    digest = hashlib.sha256((identity.lower() + ":" + ref).encode()).hexdigest()
    observed = f"refs/rwgk/pr-push/{digest}/observed"
    repo.git(
        "fetch",
        "--recurse-submodules=no",
        "--no-tags",
        endpoint,
        f"+{ref}:{observed}",
    )
    if repo.oid(observed) != old:
        raise WTError(
            "The PR branch changed during push preflight; run 'wt pull' before retrying"
        )
    unchanged = old == oid
    boundary = github.replay_base(repo, branch, pr)
    if (
        boundary
        and github._ancestor(repo, boundary, oid)
        and not github._ancestor(repo, boundary, old)
    ):
        raise WTError(
            "The PR author removed previously synchronized history. "
            "Run 'wt pull --rebase' before pushing so those commits are not restored."
        )
    if not unchanged and not github._ancestor(repo, old, oid):
        if github._ancestor(repo, oid, old):
            raise WTError("Local HEAD is behind the PR. Run 'wt pull', then 'wt push'.")
        raise WTError(
            "Local HEAD and the PR diverged. Run 'wt pull --rebase', then 'wt push'."
        )
    _local_unchanged(repo, branch, oid)
    if not unchanged:
        # The ancestry proof permits only fast-forward history. Its fixed lease
        # additionally rejects a concurrent remote update, including a rewind.
        result = github._git_result(
            repo,
            "-c",
            "push.autoSetupRemote=false",
            "push",
            "--atomic",
            "--no-follow-tags",
            "--recurse-submodules=no",
            f"--force-with-lease={ref}:{old}",
            endpoint,
            f"{oid}:{ref}",
            c_locale=True,
        )
        if result.returncode:
            _push_error(result)
    if github._remote_tip(repo, endpoint, ref) != oid:
        raise WTError(
            "PR push verification failed; the source branch moved. Inspect it before retrying"
        )
    actual = github.get_pr(repo, saved["number"], saved["base_repo"])
    github.validate_pr_source(saved, actual)
    if actual["state"] != "OPEN" or actual["head_oid"] != oid:
        raise WTError(
            "The source branch was verified, but GitHub's PR state differs. "
            "The push may have completed; inspect the PR before retrying."
        )
    _local_unchanged(repo, branch, oid)
    # Publication metadata belongs to owned branches. Recording a contribution
    # must never turn a review into a branch that cleanup may delete remotely.
    if metadata.get("role") == "owned" and metadata.get("publication"):
        publication = metadata["publication"]
        if (
            publication.get("repo", "").lower() == identity.lower()
            and publication.get("ref") == ref
        ):
            publication["oid"] = oid
    github.record_sync(repo, branch, actual, oid)
    return {
        "result": "unchanged" if unchanged else "pushed",
        "branch": branch,
        "path": str(repo.root),
        "repo": identity,
        "ref": ref,
        "pr": actual,
        "previous_oid": old,
        "oid": oid,
    }
