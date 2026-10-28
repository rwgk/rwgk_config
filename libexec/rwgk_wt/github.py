"""GitHub PR association and publication with independently fixed push leases.

Git is authoritative for worktree ownership. GitHub is authoritative for PR
identity and state; a directory name or a remote alias is never that identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from .core import WTError

SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _identity(url):
    if "://" in url:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("https", "http", "ssh", "git")
            or parsed.hostname != "github.com"
            or parsed.query
            or parsed.fragment
            or parsed.port
            not in (
                None,
                {"https": 443, "http": 80, "ssh": 22, "git": 9418}[parsed.scheme],
            )
        ):
            raise WTError("Expected a github.com repository endpoint")
        path = parsed.path.strip("/")
    else:
        match = re.fullmatch(r"(?:[^/@:]+@)?github\.com:([^\s]+)", url)
        if not match:
            raise WTError("Expected a github.com repository endpoint")
        path = match[1].strip("/")
    path = path.removesuffix(".git")
    if not REPO.fullmatch(path) or any(part in (".", "..") for part in path.split("/")):
        raise WTError("Malformed GitHub repository endpoint")
    return path


def _remote_url(repo, remote, push=False):
    args = ["remote", "get-url", "--all"]
    if push:
        args.append("--push")
    urls = repo.git(*args, remote).splitlines()
    if len(urls) != 1:
        raise WTError(
            f"Remote {remote!r} must have exactly one {'push' if push else 'fetch'} endpoint"
        )
    _identity(urls[0])
    return urls[0]


def remote_identity(repo, remote, push=False):
    """Resolve Git's effective endpoint, including pushurl and URL rewrites."""
    return _identity(_remote_url(repo, remote, push=push))


def _checked_remote(repo, remote, expected=None):
    fetch = remote_identity(repo, remote)
    push = remote_identity(repo, remote, push=True)
    if fetch.lower() != push.lower():
        raise WTError(f"Remote {remote!r} has different fetch and push repositories")
    if expected and push.lower() != expected.lower():
        raise WTError(f"Remote {remote!r} points to {push}, expected {expected}")
    return push


def _api(repo, endpoint, method="GET", payload=None):
    argv = ["gh", "api", "--hostname", "github.com", "--method", method, endpoint]
    if payload is not None:
        argv.extend(["--input", "-"])
    result = repo.run(argv, input=json.dumps(payload) if payload is not None else None)
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise WTError("GitHub returned invalid JSON") from exc


def _viewer(repo):
    data = _api(repo, "user")
    login = data.get("login") if isinstance(data, dict) else None
    if not isinstance(login, str) or not login:
        raise WTError("GitHub did not identify the authenticated user")
    return login


def validate_archive_remote(repo):
    """Require the configured archive endpoint to be the user's/admin's fork."""
    remote = repo.config["archive_remote"]
    identity = _checked_remote(repo, remote)
    data = _api(repo, f"repos/{identity}")
    viewer = _viewer(repo)
    owner = (data.get("owner") or {}).get("login", "")
    if (
        data.get("full_name", "").lower() != identity.lower()
        or not data.get("fork")
        or not (
            owner.lower() == viewer.lower()
            or (data.get("permissions") or {}).get("admin") is True
        )
    ):
        raise WTError(
            f"Archive remote {remote!r} must be a GitHub fork owned or administered by {viewer}"
        )
    upstream = repo.config["upstream_repo"].lower()
    family = {
        (data.get(key) or {}).get("full_name", "").lower()
        for key in ("parent", "source")
    }
    if upstream not in family:
        raise WTError(
            f"Archive fork {identity} does not belong to {repo.config['upstream_repo']}"
        )
    return identity


def _normalize_pr(data, base_repo, number=None):
    try:
        returned_number = data["number"]
        head = data["head"]
        base = data["base"]
        actual_base = base["repo"]["full_name"]
        oid = head["sha"]
        state = (
            "MERGED"
            if data.get("merged_at") or data.get("merged")
            else str(data["state"]).upper()
        )
        result = {
            "number": returned_number,
            "url": data["html_url"],
            "state": state,
            "author": data["user"]["login"],
            "head_repo": (head.get("repo") or {}).get("full_name"),
            "head_ref": head["ref"],
            "head_oid": oid,
            "base_repo": actual_base,
            "base_ref": base["ref"],
            "is_draft": bool(data.get("draft")),
            "merge_oid": data.get("merge_commit_sha"),
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise WTError("GitHub returned incomplete PR identity") from exc
    if (
        type(returned_number) is not int
        or returned_number <= 0
        or (number is not None and returned_number != number)
        or not isinstance(actual_base, str)
        or actual_base.lower() != base_repo.lower()
        or not isinstance(oid, str)
        or not SHA.fullmatch(oid)
        or not isinstance(result["author"], str)
        or not isinstance(result["head_ref"], str)
        or (
            result["head_repo"] is not None
            and (
                not isinstance(result["head_repo"], str)
                or not REPO.fullmatch(result["head_repo"])
            )
        )
        or state not in ("OPEN", "CLOSED", "MERGED")
        or not result["head_ref"]
        or result["url"] != f"https://github.com/{actual_base}/pull/{returned_number}"
    ):
        raise WTError("GitHub returned inconsistent PR identity")
    return result


def get_pr(repo, number, base_repo=None):
    base_repo = base_repo or repo.config["upstream_repo"]
    if not REPO.fullmatch(base_repo) or type(number) is not int or number <= 0:
        raise WTError("A GitHub repository and positive PR number are required")
    return _normalize_pr(
        _api(repo, f"repos/{base_repo}/pulls/{number}"), base_repo, number
    )


def ensure_source_unused(repo, pr):
    """Refuse source deletion while any open PR still uses its head or base."""
    base_repo = repo.config["upstream_repo"]
    head_repo, branch = pr.get("head_repo"), pr.get("head_ref")
    if (
        not head_repo
        or not branch
        or pr.get("base_repo", "").lower() != base_repo.lower()
    ):
        raise WTError(
            "Cannot prove the PR source is unused without its exact repository and branch"
        )
    owner = head_repo.split("/", 1)[0]
    query = quote(f"{owner}:{branch}", safe="")
    values = _api(repo, f"repos/{base_repo}/pulls?state=open&head={query}&per_page=100")
    if not isinstance(values, list) or len(values) >= 100:
        raise WTError("Open PR source lookup was incomplete; refusing branch deletion")
    for item in values:
        try:
            source = item["head"]["repo"]["full_name"]
            ref = item["head"]["ref"]
            state = item["state"]
            number = item["number"]
        except (KeyError, TypeError) as exc:
            raise WTError(
                "Open PR source identity was incomplete; refusing branch deletion"
            ) from exc
        if (
            not isinstance(source, str)
            or not isinstance(ref, str)
            or state != "open"
            or type(number) is not int
        ):
            raise WTError(
                "Open PR source lookup was inconsistent; refusing branch deletion"
            )
        if source.lower() == head_repo.lower() and ref == branch:
            raise WTError(
                f"Open PR #{number} still uses {head_repo}:{branch}; refusing branch deletion"
            )
    if head_repo.lower() != base_repo.lower():
        return
    query = quote(branch, safe="")
    values = _api(repo, f"repos/{base_repo}/pulls?state=open&base={query}&per_page=100")
    if not isinstance(values, list) or len(values) >= 100:
        raise WTError("Open PR base lookup was incomplete; refusing branch deletion")
    for item in values:
        try:
            target = item["base"]["repo"]["full_name"]
            ref = item["base"]["ref"]
            state = item["state"]
            number = item["number"]
        except (KeyError, TypeError) as exc:
            raise WTError(
                "Open PR base identity was incomplete; refusing branch deletion"
            ) from exc
        if (
            not isinstance(target, str)
            or not isinstance(ref, str)
            or state != "open"
            or type(number) is not int
        ):
            raise WTError(
                "Open PR base lookup was inconsistent; refusing branch deletion"
            )
        if target.lower() == head_repo.lower() and ref == branch:
            raise WTError(
                f"Open PR #{number} still uses {branch} as its base; refusing branch deletion"
            )


def _git_result(repo, *args):
    return repo.run(["git", "-C", str(repo.root), *args], check=False)


def _ancestor(repo, old, new):
    result = _git_result(repo, "merge-base", "--is-ancestor", old, new)
    if result.returncode not in (0, 1):
        raise WTError("Could not determine commit ancestry")
    return result.returncode == 0


def _pr_selector(repo, selector):
    value = str(selector)
    match = re.fullmatch(r"(?:pr)?([1-9][0-9]*)", value)
    if match:
        return int(match[1])
    match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)/?", value
    )
    if match and match[1].lower() == repo.config["upstream_repo"].lower():
        return int(match[2])
    raise WTError("Use a PR number or a PR URL in the configured upstream repository")


def _source_remote(repo, head_repo):
    available = set(repo.git("remote").splitlines())
    for remote in dict.fromkeys(
        (
            repo.config["publish_remote"],
            repo.config["upstream_remote"],
            repo.config["archive_remote"],
        )
    ):
        if remote not in available:
            continue
        try:
            actual = remote_identity(repo, remote)
        except WTError:
            continue
        if actual.lower() == (head_repo or "").lower():
            _checked_remote(repo, remote, head_repo)
            return remote
    return None


def _associate(repo, branch, pr, role, refresh=False, checked_out=None):
    metadata = repo.branches[branch]
    metadata["pr"] = pr
    metadata["role"] = role
    if role == "owned" and pr.get("head_repo"):
        remote = _source_remote(repo, pr["head_repo"])
        if remote and ("publication" not in metadata or refresh):
            metadata["publication"] = {
                "remote": remote,
                "repo": pr["head_repo"],
                "ref": f"refs/heads/{pr['head_ref']}",
                "oid": pr["head_oid"],
            }
    metadata["last_seen_head"] = pr["head_oid"]
    if checked_out is not None:
        metadata["last_checked_out_head"] = checked_out
    repo.save()
    repo.alias(branch, pr)


def open_pr(repo, selector, refresh=False):
    """Open/resume a PR through its retained upstream ref, including deleted forks."""
    from . import submodules

    number = _pr_selector(repo, selector)
    base_repo = repo.config["upstream_repo"]
    remote = repo.config["upstream_remote"]
    _checked_remote(repo, remote, base_repo)
    pr = get_pr(repo, number)
    matches = [
        name
        for name, metadata in repo.branches.items()
        if metadata.get("pr", {}).get("number") == number
        and metadata.get("pr", {}).get("base_repo", "").lower() == base_repo.lower()
    ]
    if len(matches) > 1:
        raise WTError(f"Multiple managed branches claim PR #{number}")
    if matches:
        branch = matches[0]
        role = repo.branches[branch]["role"]
        path = repo.owner(branch)
        if not path:
            raise WTError(f"Managed branch {branch} has no worktree; run wt doctor")
        if not refresh:
            if repo.branches[branch].get("creating"):
                path = repo.create(branch, branch, role=role)
            _associate(repo, branch, pr, role)
            return Path(path).resolve()
    else:
        viewer = _viewer(repo)
        own = (
            pr["author"].lower() == viewer.lower()
            and _source_remote(repo, pr["head_repo"]) is not None
        )
        if own and pr["head_ref"] != repo.config["trunk"]:
            branch, role = pr["head_ref"], "owned"
        else:
            branch, role = f"rwgk/review-pr{number}", "review"
        previous = repo.branches.get(branch)
        if (
            previous
            and previous.get("pr")
            and (
                previous["pr"]["number"] != number
                or previous["pr"]["base_repo"].lower() != base_repo.lower()
            )
        ):
            raise WTError(f"Branch {branch} belongs to another PR")
        if previous and role == "review" and previous.get("role") != "review":
            raise WTError(f"Review branch {branch} already contains unrelated work")
        path = repo.owner(branch)
        if path and not previous:
            raise WTError(
                f"Branch {branch} has an unmanaged worktree at {path}; adopt it deliberately before opening its PR"
            )
        existing_review = (
            _git_result(
                repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
            ).returncode
            == 0
        )
        if role == "review" and existing_review and not previous:
            raise WTError(
                f"Review branch {branch} already exists outside the managed catalog"
            )

    digest = hashlib.sha256(base_repo.lower().encode()).hexdigest()[:12]
    fetched = f"refs/rwgk/pr/{digest}/{number}/head"
    repo.git(
        "fetch",
        "--recurse-submodules=no",
        "--no-tags",
        remote,
        f"+refs/pull/{number}/head:{fetched}",
    )
    if repo.oid(fetched) != pr["head_oid"]:
        raise WTError("PR head changed while opening it; retry after inspecting the PR")
    checked_out = None
    if not path:
        path = repo.create(branch, fetched, role=role)
        checked_out = pr["head_oid"]
    elif refresh:
        submodules.recover(repo, path)
        repo.ensure_idle(path)
        repo.ensure_clean(path)
        local = repo.oid(f"refs/heads/{branch}")
        if _ancestor(repo, local, pr["head_oid"]):
            submodules.validate_update(repo, path, fetched)
            repo.git(
                "-c",
                "submodule.recurse=false",
                "merge",
                "--ff-only",
                fetched,
                cwd=path,
            )
        elif not _ancestor(repo, pr["head_oid"], local):
            metadata = repo.branches[branch]
            previous = metadata.get(
                "last_checked_out_head", metadata.get("publication", {}).get("oid")
            )
            if local != previous:
                raise WTError(
                    "PR head and local work diverged; preserve/reconcile local commits before refreshing"
                )
            from .lifecycle import protect_local

            protect_local(
                repo,
                [{"oid": local, "label": branch, "branch": branch, "pr": pr}],
                kind="pr-refresh",
            )
            repo.ensure_clean(path)
            submodules.validate_update(repo, path, fetched)
            if (
                repo.git("symbolic-ref", "--quiet", "HEAD", cwd=path)
                != f"refs/heads/{branch}"
            ):
                raise WTError("Review worktree changed its branch during refresh")
            # Compare-and-swap the branch, then update the checkout without
            # resetting that ref a second time. Recovery refs precede both.
            repo.git("update-ref", f"refs/heads/{branch}", pr["head_oid"], local)
            repo.git(
                "-c",
                "submodule.recurse=false",
                "read-tree",
                "--reset",
                "-u",
                pr["head_oid"],
                cwd=path,
            )
            if repo.oid(f"refs/heads/{branch}") != pr["head_oid"]:
                raise WTError(
                    "Branch changed concurrently while refreshing the PR; inspect its preserved recovery record"
                )
        submodules.initialize(repo, path)
        checked_out = pr["head_oid"]
    elif repo.branches[branch].get("creating"):
        path = repo.create(branch, branch, role=role)
        checked_out = repo.oid(f"refs/heads/{branch}")
    _associate(repo, branch, pr, role, refresh=refresh, checked_out=checked_out)
    return Path(path).resolve()


def refresh_current(repo):
    """Refresh the current independent PR through the same guarded PR-ref path."""
    from . import stacks

    branch = repo.current_branch()
    if branch == repo.config["trunk"]:
        raise WTError("On trunk; use 'wt trunk update' to fast-forward it.")
    if branch is None:
        raise WTError("Detached HEAD; use 'wt go' to enter a managed PR worktree.")
    if branch not in repo.branches:
        raise WTError("Current branch is unmanaged; use 'wt pr NUMBER' to open its PR.")
    metadata = repo.branches[branch]
    if repo.owner(branch) != repo.root or Path(metadata["path"]).resolve() != repo.root:
        raise WTError(
            "Current worktree does not match its registration; run 'wt doctor'."
        )
    if stacks.get_stack(repo, branch):
        raise WTError(
            "Current branch belongs to a stack; use 'wt stack pull' to preview, "
            "then 'wt stack pull --apply' to adopt it."
        )
    pr = metadata.get("pr")
    if not pr:
        raise WTError(
            "Current branch has no associated PR; use 'wt pr NUMBER' to open or associate one."
        )
    if (pr.get("base_repo") or "").lower() != repo.config["upstream_repo"].lower():
        raise WTError(
            "Associated PR belongs to another repository; inspect 'wt doctor'."
        )
    repo.check_primary()
    previous = repo.oid(f"refs/heads/{branch}")
    path = open_pr(repo, pr["number"], refresh=True)
    oid = repo.oid(f"refs/heads/{branch}")
    return {
        "result": "unchanged" if oid == previous else "refreshed",
        "branch": branch,
        "path": str(path),
        "pr": repo.branches[branch]["pr"],
        "previous_oid": previous,
        "oid": oid,
    }


def _remote_tip(repo, url, ref):
    result = _git_result(repo, "ls-remote", "--exit-code", "--refs", url, ref)
    if result.returncode == 2:
        return None
    if result.returncode:
        raise WTError("Could not inspect the publication endpoint")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise WTError("Publication ref lookup was ambiguous")
    fields = lines[0].split("\t")
    if len(fields) != 2 or not SHA.fullmatch(fields[0]) or fields[1] != ref:
        raise WTError("Publication endpoint returned an unexpected ref")
    return fields[0]


def _existing_pr(repo, metadata, base_repo, head_repo, branch):
    associated = metadata.get("pr")
    if associated:
        if associated["base_repo"].lower() != base_repo.lower():
            raise WTError("Existing PR targets a different repository")
        pr = get_pr(repo, associated["number"], base_repo)
        if pr["state"] != "OPEN":
            raise WTError(
                f"PR #{pr['number']} is {pr['state'].lower()}; use a new branch for follow-up work"
            )
        candidates = [pr]
    else:
        owner = head_repo.split("/", 1)[0]
        query = quote(f"{owner}:{branch}", safe="")
        values = _api(
            repo, f"repos/{base_repo}/pulls?state=open&head={query}&per_page=100"
        )
        if not isinstance(values, list) or len(values) >= 100:
            raise WTError("GitHub PR lookup was incomplete")
        candidates = [
            _normalize_pr(item, base_repo)
            for item in values
            if (item.get("head", {}).get("repo") or {}).get("full_name", "").lower()
            == head_repo.lower()
            and item.get("head", {}).get("ref") == branch
        ]
    if len(candidates) > 1:
        raise WTError(f"Multiple open PRs use {head_repo}:{branch}")
    if not candidates:
        return None
    pr = candidates[0]
    if (pr["head_repo"] or "").lower() != head_repo.lower() or pr["head_ref"] != branch:
        raise WTError("PR source differs from the exact publication destination")
    return pr


def _auto_text(repo, branch, base):
    count = repo.git("rev-list", "--count", f"refs/heads/{base}..refs/heads/{branch}")
    if count == "0":
        raise WTError(f"Branch {branch} has no commits beyond {base}")
    if count == "1":
        return (
            repo.git("log", "-1", "--format=%s", f"refs/heads/{branch}"),
            repo.git("log", "-1", "--format=%b", f"refs/heads/{branch}"),
        )
    return branch.removeprefix("rwgk/").replace("-", " ").replace("_", " "), ""


def _update_pr(repo, entry, base_repo, head_repo, title, body, ready):
    branch, base, pr = entry["branch"], entry["base"], entry["pr"]
    if pr:
        changes = {}
        if pr["base_ref"] != base:
            changes["base"] = base
        if title is not None:
            changes["title"] = title
        if body is not None:
            changes["body"] = body
        if changes:
            data = _api(
                repo, f"repos/{base_repo}/pulls/{pr['number']}", "PATCH", changes
            )
            pr = _normalize_pr(data, base_repo, pr["number"])
        if ready and pr["is_draft"]:
            repo.run(["gh", "pr", "ready", pr["url"]])
            pr = get_pr(repo, pr["number"], base_repo)
    else:
        auto_title, auto_body = entry["auto_text"]
        head = (
            branch
            if head_repo.lower() == base_repo.lower()
            else f"{head_repo.split('/')[0]}:{branch}"
        )
        data = _api(
            repo,
            f"repos/{base_repo}/pulls",
            "POST",
            {
                "head": head,
                "base": base,
                "title": title if title is not None else auto_title,
                "body": body if body is not None else auto_body,
                "draft": not ready,
            },
        )
        pr = _normalize_pr(data, base_repo)
    if (pr["head_repo"] or "").lower() != head_repo.lower() or pr["head_ref"] != branch:
        raise WTError("Published PR has an unexpected source")
    return pr


def _verify_native_stack(repo, selected, entries, prs):
    base_repo = repo.config["upstream_repo"]
    candidates = _api(
        repo, f"repos/{base_repo}/stacks?pull_request={prs[0]['number']}&per_page=100"
    )
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise WTError(
            "Could not uniquely verify the native GitHub stack after publication"
        )
    native = candidates[0]
    if (
        not isinstance(native, dict)
        or type(native.get("number")) is not int
        or native["number"] <= 0
        or type(native.get("id")) is not int
        or native["id"] <= 0
        or native.get("open") is False
        or native.get("base", {}).get("ref") != selected["trunk"]["branch"]
        or not isinstance(native.get("pull_requests"), list)
    ):
        raise WTError("GitHub returned inconsistent native stack identity")
    active = []
    seen_active = False
    for pr in native["pull_requests"]:
        if pr.get("merged_at"):
            if seen_active:
                raise WTError(
                    "Native GitHub stack has a merged layer above an active layer"
                )
            continue
        seen_active = True
        active.append(pr)
    if len(active) != len(entries):
        raise WTError(
            "Native GitHub stack membership differs from the requested local stack"
        )
    for entry, expected, actual in zip(entries, prs, active, strict=True):
        if (
            actual.get("number") != expected["number"]
            or actual.get("head", {}).get("ref") != entry["branch"]
            or actual.get("head", {}).get("sha") != entry["oid"]
            or actual.get("state") != "open"
            or bool(actual.get("draft")) != expected["is_draft"]
        ):
            raise WTError(
                "Native GitHub stack order or PR state differs from the publication plan"
            )
    return native


def publish(
    repo, stack=False, title=None, body_file=None, ready=False, set_upstream=False
):
    """Publish committed tips atomically, then create/link PRs without hidden pushes."""
    from . import lifecycle, stacks

    current = repo.current_branch()
    selected = stacks.get_stack(repo)
    if selected and not stack:
        raise WTError("This branch is in a stack; publish it with wt publish --stack")
    if stack and not selected:
        raise WTError("The current branch is not in a stack")
    if stack and title is not None:
        raise WTError(
            "Use per-PR titles after stack creation; --title applies to a single PR"
        )
    if stack and body_file is not None:
        raise WTError("--body-file applies to a single PR")
    try:
        body = Path(body_file).read_text() if body_file is not None else None
    except OSError as exc:
        raise WTError(f"Could not read PR body file: {body_file}") from exc

    remote = repo.config["upstream_remote"] if stack else repo.config["publish_remote"]
    base_repo = repo.config["upstream_repo"]
    head_repo = _checked_remote(repo, remote, base_repo if stack else None)
    _checked_remote(repo, repo.config["upstream_remote"], base_repo)
    push_url = _remote_url(repo, remote, push=True)
    viewer = _viewer(repo)
    layers = selected["branches"] if stack else [{"branch": current}]
    base = selected["trunk"]["branch"] if stack else repo.config["trunk"]
    entries = []
    snapshots = []
    pending_archives = {}
    for layer in layers:
        branch = layer["branch"]
        metadata = repo.branches.get(branch)
        if (
            metadata
            and metadata.get("role") == "owned"
            and metadata.get("pending_archive")
        ):
            pending_archives[branch] = list(metadata["pending_archive"])
            snapshots.extend(pending_archives[branch])
        if layer.get("pullRequest", {}).get("merged"):
            continue
        if not metadata or metadata.get("role") != "owned":
            raise WTError(
                f"Branch {branch} must be a managed owned branch before publication"
            )
        if not branch.startswith("rwgk/"):
            raise WTError(f"New publication branches must use rwgk/: {branch}")
        path = repo.owner(branch)
        if not path:
            raise WTError(f"Branch {branch} has no owning worktree")
        repo.ensure_idle(path)
        repo.ensure_clean(path)
        oid = repo.oid(f"refs/heads/{branch}")
        if not _ancestor(repo, f"refs/heads/{base}", oid):
            raise WTError(
                f"Branch {branch} does not contain its effective parent {base}; rebase first"
            )
        if stack and layer.get("base") != repo.oid(f"refs/heads/{base}"):
            raise WTError(
                f"Stack replay boundary for {branch} is stale; run wt stack rebase first"
            )
        ref = f"refs/heads/{branch}"
        old = _remote_tip(repo, push_url, ref)
        saved = metadata.get("publication")
        if saved and (
            saved["repo"].lower() != head_repo.lower()
            or saved["remote"] != remote
            or saved["ref"] != ref
        ):
            raise WTError(
                f"Publication destination changed for {branch}; adopt the intended destination explicitly"
            )
        if saved and saved["oid"] != old:
            raise WTError(
                f"Remote {head_repo}:{branch} changed since the recorded publication; inspect and adopt that work before retrying"
            )
        pr = _existing_pr(repo, metadata, base_repo, head_repo, branch)
        if pr and pr["head_oid"] != old:
            raise WTError(
                "GitHub's PR head and the publication endpoint disagree; inspect before publishing"
            )
        if pr and pr["author"].lower() != viewer.lower():
            raise WTError(
                f"PR #{pr['number']} belongs to another author; use an explicit review publication workflow"
            )
        observed = f"refs/rwgk/publication/{hashlib.sha256((head_repo.lower() + ':' + branch).encode()).hexdigest()}/observed"
        force = False
        if old:
            repo.git("fetch", "--no-tags", push_url, f"+{ref}:{observed}")
            if repo.oid(observed) != old:
                raise WTError(
                    f"Remote branch {branch} changed during publication preflight; no push was attempted"
                )
            force = not _ancestor(repo, old, oid)
            if force and not saved:
                raise WTError(
                    f"Rewriting {branch} requires a previously recorded publication tip; inspect and adopt the remote first"
                )
        upstream = repo.git("for-each-ref", "--format=%(upstream)", ref)
        if force and not upstream and not set_upstream:
            raise WTError(
                f"Rewriting existing {head_repo}:{branch} with no local upstream requires an explicit choice: rerun with --set-upstream to track {remote}/{branch}"
            )
        if force:
            snapshots.append(
                {"oid": old, "label": f"published-{branch}", "branch": branch, "pr": pr}
            )
        entries.append(
            {
                "branch": branch,
                "base": base,
                "oid": oid,
                "old": old,
                "ref": ref,
                "upstream": upstream,
                "pr": pr,
                "auto_text": _auto_text(repo, branch, base) if pr is None else None,
            }
        )
        base = branch
    if not entries:
        raise WTError("No active branches to publish")

    # Preserve the old published history before any rewrite. The lifecycle layer
    # skips snapshots already reachable from upstream trunk or an archive tag.
    if snapshots:
        lifecycle.pin_snapshots(
            repo, snapshots, pr=entries[0]["pr"] if len(entries) == 1 else None
        )
        # Server restacks can disconnect old published tips before this push,
        # even when the new push is a fast-forward. Drop only snapshots whose
        # remote preservation was just verified by the lifecycle layer.
        for branch, queued in pending_archives.items():
            metadata = repo.branches[branch]
            remaining = [
                item
                for item in metadata.get("pending_archive", [])
                if item not in queued
            ]
            if remaining:
                metadata["pending_archive"] = remaining
            else:
                metadata.pop("pending_archive", None)
        if pending_archives:
            repo.save()
    for entry in entries:
        if repo.oid(entry["ref"]) != entry["oid"]:
            raise WTError(
                "A local branch changed during publication preflight; no push was attempted"
            )
    command = ["push", "--atomic"]
    command.extend(
        f"--force-with-lease={item['ref']}:{item['old'] or ''}" for item in entries
    )
    command.append(push_url)
    command.extend(f"{item['oid']}:{item['ref']}" for item in entries)
    pushed = _git_result(repo, *command)
    if pushed.returncode:
        raise WTError(
            "Publication push failed. Fixed leases were not refreshed; inspect remote refs/authentication before retrying."
        )
    for entry in entries:
        if _remote_tip(repo, push_url, entry["ref"]) != entry["oid"]:
            raise WTError(
                "Remote publication verification failed; inspect all destination refs before retrying"
            )
        metadata = repo.branches[entry["branch"]]
        metadata["last_checked_out_head"] = entry["oid"]
        metadata["publication"] = {
            "remote": remote,
            "repo": head_repo,
            "ref": entry["ref"],
            "oid": entry["oid"],
        }
        if not entry["upstream"]:
            # Explicit config avoids git push -u changing an existing upstream.
            repo.git("config", f"branch.{entry['branch']}.remote", remote)
            repo.git("config", f"branch.{entry['branch']}.merge", entry["ref"])
        repo.save()
    prs = []
    for entry in entries:
        pr = _update_pr(repo, entry, base_repo, head_repo, title, body, ready)
        repo.branches[entry["branch"]]["pr"] = pr
        repo.save()
        repo.alias(entry["branch"], pr)
        prs.append(pr)
    if stack and len(prs) >= 2:
        repo.run(
            [
                "gh",
                "stack",
                "link",
                "--remote",
                remote,
                "--base",
                selected["trunk"]["branch"],
                *[item["url"] for item in prs],
            ]
        )
    # API proof is separate from the local catalog: re-read heads, bases, and
    # draft state after linking (which may adjust the base chain).
    for entry, expected in zip(entries, prs, strict=True):
        actual = get_pr(repo, expected["number"], base_repo)
        if (
            actual["state"] != "OPEN"
            or actual["head_oid"] != entry["oid"]
            or (actual["head_repo"] or "").lower() != head_repo.lower()
            or actual["head_ref"] != entry["branch"]
            or actual["base_ref"] != entry["base"]
            or actual["is_draft"] != expected["is_draft"]
        ):
            raise WTError(
                "GitHub PR verification failed; publication may have completed, inspect it before retrying"
            )
        repo.branches[entry["branch"]]["pr"] = actual
    repo.save()
    if stack and len(prs) >= 2:
        native = _verify_native_stack(repo, selected, entries, prs)
        stacks.save_native_identity(repo, native["number"], str(native["id"]))
    if stack:
        stacks.refresh(repo)
    for entry in entries:
        if repo.oid(entry["ref"]) != entry["oid"]:
            raise WTError(
                "A local branch changed during publication. The recorded published tip was preserved; inspect before continuing."
            )
    return {
        "remote": remote,
        "repo": head_repo,
        "branches": [item["branch"] for item in entries],
        "prs": prs,
    }
