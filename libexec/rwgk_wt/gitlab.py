"""GitLab merge-request API and transport adapters for the shared wt lifecycle.

Merge requests use project-local IIDs. Host, namespace and numeric project IDs
remain part of their identity; GitLab's global MR ID is never used as an IID.
See https://docs.gitlab.com/api/merge_requests/ and /api/projects/.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

from .core import WTError

SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
PROJECT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\Z")
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
DRAFT = re.compile(r"^(?:Draft:|WIP:|\[Draft\]|\[WIP\])\s*", re.IGNORECASE)


def _project_path(path):
    return bool(
        isinstance(path, str)
        and PROJECT.fullmatch(path)
        and all(part not in (".", "..") for part in path.split("/"))
    )


def _branch_name(value):
    return bool(
        isinstance(value, str)
        and value
        and value != "@"
        and not value.startswith(("/", "-"))
        and not value.endswith(("/", "."))
        and not any(token in value for token in ("..", "@{", "//"))
        and not any(
            ord(character) <= 32 or ord(character) == 127 or character in "~^:?*[\\"
            for character in value
        )
        and all(
            not part.startswith(".") and not part.endswith(".lock")
            for part in value.split("/")
        )
    )


def split_identity(identity):
    """Split an explicit host-qualified project without consulting a remote alias."""
    if not isinstance(identity, str):
        raise WTError("Expected a host-qualified GitLab project")
    host, separator, path = identity.partition("/")
    if not separator or not HOST.fullmatch(host) or not _project_path(path):
        raise WTError("Expected a GitLab project as HOST/NAMESPACE/PROJECT")
    return host.lower(), path


def parse_identity(url):
    """Recognize GitLab endpoints; SSH ports belong to transport, not API identity."""
    if not isinstance(url, str):
        return None
    try:
        if "://" in url:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
            if (
                parsed.scheme not in ("https", "http", "ssh")
                or not host
                or parsed.query
                or parsed.fragment
                or parsed.password is not None
                or parsed.scheme != "ssh"
                and parsed.username is not None
                or parsed.scheme != "ssh"
                and port not in (None, {"https": 443, "http": 80}.get(parsed.scheme))
            ):
                return None
            path = parsed.path.strip("/")
        else:
            match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):([^\s]+)", url)
            if not match:
                return None
            host, path = match[1], match[2].strip("/")
        host = host.lower()
        if host == "github.com":
            return None
        path = path.removesuffix(".git")
        if not HOST.fullmatch(host) or not _project_path(path):
            return None
        return f"{host}/{path}"
    except ValueError:
        return None


def api(repo, endpoint, method="GET", payload=None, *, host=None):
    host = host or split_identity(repo.config["upstream_repo"])[0]
    if not HOST.fullmatch(host):
        raise WTError("Invalid GitLab API hostname")
    argv = ["glab", "api", "--hostname", host, "--method", method, endpoint]
    if payload is not None:
        argv.extend(("--input", "-"))
    try:
        result = repo.run(
            argv, input=json.dumps(payload) if payload is not None else None
        )
    except (OSError, WTError) as error:
        raise WTError(
            f"GitLab API access failed on {host}. Install glab if missing, run "
            f"'glab auth login --hostname {host}', and verify project access and connectivity."
        ) from error
    if result.returncode:
        raise WTError(
            f"GitLab API access failed on {host}; verify 'glab auth login --hostname {host}', "
            "project permissions and connectivity."
        )
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise WTError("GitLab returned invalid JSON") from error


def _project(repo, host, key, *, expected_path=None, expected_id=None):
    data = api(repo, f"projects/{quote(str(key), safe='')}", host=host)
    if not isinstance(data, dict):
        raise WTError("GitLab returned incomplete project identity")
    number, path, web = (
        data.get("id"),
        data.get("path_with_namespace"),
        data.get("web_url"),
    )
    if (
        type(number) is not int
        or number <= 0
        or not _project_path(path)
        or expected_id is not None
        and number != expected_id
        or expected_path is not None
        and path.lower() != expected_path.lower()
        or web not in (f"https://{host}/{path}", f"http://{host}/{path}")
    ):
        raise WTError("GitLab returned inconsistent project identity")
    identity = f"{host}/{path}"
    ssh = data.get("ssh_url_to_repo")
    if (
        not isinstance(ssh, str)
        or (parse_identity(ssh) or "").lower() != identity.lower()
    ):
        raise WTError("GitLab returned an inconsistent project SSH endpoint")
    http = data.get("http_url_to_repo")
    if http is not None and (parse_identity(http) or "").lower() != identity.lower():
        raise WTError("GitLab returned an inconsistent project HTTP endpoint")
    return data


def project(repo, identity):
    host, path = split_identity(identity)
    return _project(repo, host, path, expected_path=path)


def viewer(repo):
    data = api(repo, "user")
    username = data.get("username") if isinstance(data, dict) else None
    if not isinstance(username, str) or not username:
        raise WTError("GitLab did not identify the authenticated user")
    return username


def selector(repo, value):
    value = str(value)
    match = re.fullmatch(r"(?:pr|mr|!)?([1-9][0-9]*)", value)
    if match:
        return int(match[1])
    host, path = split_identity(repo.config["upstream_repo"])
    match = re.fullmatch(
        rf"https?://{re.escape(host)}/{re.escape(path)}/-/merge_requests/([1-9][0-9]*)/?",
        value,
    )
    if match:
        return int(match[1])
    raise WTError(
        "Use an MR IID or an MR URL in the configured upstream GitLab project"
    )


def _normalize(repo, data, base, host, number=None):
    try:
        iid = data["iid"]
        source_id, target_id = data["source_project_id"], data["target_project_id"]
        source_branch, target_branch = data["source_branch"], data["target_branch"]
        oid = data["sha"]
        state = {"opened": "OPEN", "closed": "CLOSED", "merged": "MERGED"}[
            data["state"]
        ]
        author = data["author"]["username"]
        url = data["web_url"]
    except (KeyError, TypeError) as error:
        raise WTError("GitLab returned incomplete MR identity") from error
    if (
        type(iid) is not int
        or iid <= 0
        or number is not None
        and iid != number
        or type(target_id) is not int
        or target_id != base["id"]
        or source_id is not None
        and (type(source_id) is not int or source_id <= 0)
        or not isinstance(oid, str)
        or not SHA.fullmatch(oid)
        or not isinstance(author, str)
        or not author
        or not _branch_name(source_branch)
        or not _branch_name(target_branch)
        or url != f"{base['web_url']}/-/merge_requests/{iid}"
    ):
        raise WTError("GitLab returned inconsistent MR identity")
    title = data.get("title", "")
    if not isinstance(title, str) or any(
        name in data and type(data[name]) is not bool
        for name in ("draft", "work_in_progress")
    ):
        raise WTError("GitLab returned inconsistent MR draft state")
    source = None
    if source_id is not None:
        source = (
            base
            if source_id == base["id"]
            else _project(repo, host, source_id, expected_id=source_id)
        )
    merge_oid = data.get("merge_commit_sha") or data.get("squash_commit_sha")
    if merge_oid is not None and (
        not isinstance(merge_oid, str) or not SHA.fullmatch(merge_oid)
    ):
        raise WTError("GitLab returned an invalid merged commit")
    return {
        "provider": "gitlab",
        "host": host,
        "number": iid,
        "url": url,
        "state": state,
        "author": author,
        "head_repo": f"{host}/{source['path_with_namespace']}" if source else None,
        "head_ref": source_branch,
        "head_oid": oid,
        "head_project_id": source_id,
        "head_url": source["ssh_url_to_repo"] if source else None,
        "base_repo": f"{host}/{base['path_with_namespace']}",
        "base_ref": target_branch,
        "base_project_id": target_id,
        "is_draft": bool(
            data.get("draft") or data.get("work_in_progress") or DRAFT.match(title)
        ),
        "merge_oid": merge_oid,
        "title": title,
    }


def get_pr(repo, number, base_repo=None):
    configured = repo.config["upstream_repo"]
    base_repo = configured if base_repo is None else base_repo
    if not isinstance(base_repo, str) or base_repo.lower() != configured.lower():
        raise WTError("MR lookup must use the configured upstream GitLab project")
    host, path = split_identity(base_repo)
    if type(number) is not int or number <= 0:
        raise WTError("A positive GitLab MR IID is required")
    base = project(repo, base_repo)
    data = api(
        repo, f"projects/{quote(path, safe='')}/merge_requests/{number}", host=host
    )
    return _normalize(repo, data, base, host, number)


def _open(repo, identity, **filters):
    host, path = split_identity(identity)
    query = "&".join(
        f"{key}={quote(str(value), safe='')}"
        for key, value in {
            "state": "opened",
            "scope": "all",
            **filters,
            "per_page": 100,
        }.items()
    )
    values = api(
        repo, f"projects/{quote(path, safe='')}/merge_requests?{query}", host=host
    )
    if not isinstance(values, list) or len(values) >= 100:
        raise WTError(
            "GitLab MR lookup was incomplete; inspect pagination before continuing"
        )
    if any(
        not isinstance(item, dict) or item.get("state") != "opened" for item in values
    ):
        raise WTError("GitLab returned inconsistent open MR results")
    return values


def find_open(repo, head_repo, branch, base_repo=None):
    base_repo = base_repo or repo.config["upstream_repo"]
    host, _ = split_identity(base_repo)
    if split_identity(head_repo)[0] != host:
        raise WTError("Cross-instance GitLab merge requests are unsupported")
    base, head = project(repo, base_repo), project(repo, head_repo)
    return [
        _normalize(repo, item, base, host)
        for item in _open(repo, base_repo, source_branch=branch)
        if item.get("source_project_id") == head["id"]
        and item.get("source_branch") == branch
    ]


def source_endpoint(repo, identity, *, push=False, expected_id=None):
    """Prefer verified configured transports, retaining their SSH port and user."""
    data = project(repo, identity)
    if expected_id is not None and (
        type(expected_id) is not int or data["id"] != expected_id
    ):
        raise WTError("GitLab source project identity changed before transport lookup")
    remotes = repo.git("remote").splitlines()
    preferred = [
        repo.config.get(key)
        for key in ("publish_remote", "upstream_remote", "archive_remote")
    ]
    for remote in dict.fromkeys([*preferred, *remotes]):
        if remote not in remotes:
            continue
        fetch = repo.git("remote", "get-url", "--all", remote).splitlines()
        published = repo.git(
            "remote", "get-url", "--all", "--push", remote
        ).splitlines()
        matches = any(
            (parse_identity(url) or "").lower() == identity.lower()
            for url in [*fetch, *published]
        )
        if not matches:
            continue
        if (
            len(fetch) != 1
            or len(published) != 1
            or any(
                (parse_identity(url) or "").lower() != identity.lower()
                for url in [*fetch, *published]
            )
        ):
            raise WTError(
                "GitLab source remote has ambiguous or different fetch/push projects"
            )
        return published[0] if push else fetch[0]
    return data["ssh_url_to_repo"]


def _draft_title(title, draft):
    while DRAFT.match(title):
        title = DRAFT.sub("", title, count=1)
    return f"Draft: {title}" if draft else title


def update_pr(repo, entry, base_repo, head_repo, title, body, ready):
    host, path = split_identity(base_repo)
    if split_identity(head_repo)[0] != host:
        raise WTError("Cross-instance GitLab merge requests are unsupported")
    base, head = project(repo, base_repo), project(repo, head_repo)
    branch, target, pr = entry["branch"], entry["base"], entry["pr"]
    if pr:
        changes = {}
        if pr["base_ref"] != target:
            changes["target_branch"] = target
        if body is not None:
            changes["description"] = body
        if title is not None or ready and pr["is_draft"]:
            changes["title"] = _draft_title(
                title if title is not None else pr["title"],
                pr["is_draft"] and not ready,
            )
        if changes:
            data = api(
                repo,
                f"projects/{quote(path, safe='')}/merge_requests/{pr['number']}",
                "PUT",
                changes,
                host=host,
            )
            pr = _normalize(repo, data, base, host, pr["number"])
    else:
        auto_title, auto_body = entry["auto_text"]
        data = api(
            repo,
            f"projects/{quote(head['path_with_namespace'], safe='')}/merge_requests",
            "POST",
            {
                "source_branch": branch,
                "target_branch": target,
                "target_project_id": base["id"],
                "title": _draft_title(
                    title if title is not None else auto_title, not ready
                ),
                "description": body if body is not None else auto_body,
            },
            host=host,
        )
        pr = _normalize(repo, data, base, host)
    if (
        pr.get("provider") != "gitlab"
        or pr.get("host") != host
        or (pr.get("base_repo") or "").lower() != base_repo.lower()
        or (pr.get("head_repo") or "").lower() != head_repo.lower()
        or pr.get("head_project_id") != head["id"]
        or pr.get("base_project_id") != base["id"]
        or pr.get("head_ref") != branch
        or pr.get("base_ref") != target
    ):
        raise WTError("Published MR has an unexpected source or target")
    return pr


def validate_archive_remote(repo):
    remote = repo.config["archive_remote"]
    fetch = repo.git("remote", "get-url", "--all", remote).splitlines()
    push = repo.git("remote", "get-url", "--all", "--push", remote).splitlines()
    identities = [parse_identity(value) for value in [*fetch, *push]]
    if (
        len(fetch) != 1
        or len(push) != 1
        or not all(identities)
        or len({value.lower() for value in identities}) != 1
    ):
        raise WTError("Archive remote must identify one GitLab fork for fetch and push")
    identity = identities[0]
    data, upstream = (
        project(repo, identity),
        project(repo, repo.config["upstream_repo"]),
    )
    username = viewer(repo)
    namespace = data.get("namespace") or {}
    owned = (
        namespace.get("kind") == "user"
        and namespace.get("path", "").lower() == username.lower()
    )
    permissions = data.get("permissions") or {}
    maintained = any(
        type((permissions.get(key) or {}).get("access_level")) is int
        and permissions[key]["access_level"] >= 40
        for key in ("project_access", "group_access")
    )
    if not (owned or maintained):
        raise WTError(
            f"Archive remote {remote!r} must be a GitLab fork owned or maintained by {username}"
        )
    host, _ = split_identity(identity)
    if host != split_identity(repo.config["upstream_repo"])[0]:
        raise WTError("Archive fork belongs to another GitLab instance")
    seen = {data["id"]}
    for _ in range(20):
        parent = data.get("forked_from_project")
        parent_id = parent.get("id") if isinstance(parent, dict) else None
        if type(parent_id) is not int or parent_id <= 0 or parent_id in seen:
            break
        if parent_id == upstream["id"]:
            return identity
        seen.add(parent_id)
        data = _project(repo, host, parent_id, expected_id=parent_id)
    raise WTError(
        "Archive GitLab fork does not belong to the configured upstream project"
    )


def ensure_source_unused(repo, pr):
    base_repo, head_repo, branch = (
        pr.get("base_repo"),
        pr.get("head_repo"),
        pr.get("head_ref"),
    )
    if (
        not base_repo
        or not head_repo
        or not branch
        or base_repo.lower() != repo.config["upstream_repo"].lower()
    ):
        raise WTError(
            "Cannot prove the MR source is unused without its exact project and branch"
        )
    base, head = project(repo, base_repo), project(repo, head_repo)
    if (
        pr.get("base_project_id") != base["id"]
        or pr.get("head_project_id") != head["id"]
    ):
        raise WTError("MR project identity changed; refusing branch deletion")
    checks = [("source_branch", "source_project_id", head["id"])]
    if head["id"] == base["id"]:
        checks.append(("target_branch", "target_project_id", base["id"]))
    for branch_key, project_key, project_id in checks:
        for item in _open(repo, base_repo, **{branch_key: branch}):
            number = item.get("iid")
            if (
                type(number) is not int
                or number <= 0
                or type(item.get(project_key)) is not int
                or not isinstance(item.get(branch_key), str)
            ):
                raise WTError(
                    "Open MR identity was incomplete; refusing branch deletion"
                )
            if item[project_key] == project_id and item[branch_key] == branch:
                raise WTError(
                    f"Open MR !{number} still uses {head_repo}:{branch}; refusing branch deletion"
                )
