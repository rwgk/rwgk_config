"""Read repository and wt context without network access or catalog mutations."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def _git(cwd, *args):
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=3,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if not result.returncode else ""


def repository_identity(value):
    """Normalize transport/user differences without retaining credentials."""
    value = str(value or "").strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlsplit(value)
        if not parsed.hostname:
            return ""
        host = parsed.hostname.lower()
        port = f":{parsed.port}" if parsed.port and parsed.port not in (22, 443) else ""
        name = parsed.path.strip("/").removesuffix(".git")
        return (
            f"{host}{port}/{name}".lower()
            if host == "github.com"
            else f"{host}{port}/{name}"
        )
    match = re.fullmatch(r"(?:[^/@:\s]+@)?([^/:\s]+):(.+)", value)
    if match:
        host, name = match.groups()
        name = name.strip("/").removesuffix(".git")
        result = f"{host.lower()}/{name}"
        return result.lower() if host.lower() == "github.com" else result
    if re.fullmatch(r"[^/:\s]+/[^/\s]+", value):
        return "github.com/" + value.lower()
    return ""


def workspace(cwd):
    cwd = Path(cwd or os.getcwd()).expanduser().resolve()
    root_text = _git(cwd, "rev-parse", "--show-toplevel")
    result = {
        "cwd": str(cwd),
        "root": root_text,
        "repo": "",
        "branch": "",
        "pr_url": "",
        "keys": [],
    }
    if not root_text:
        result["keys"] = [
            {"key": "workspace:" + str(cwd), "kind": "workspace", "display": str(cwd)}
        ]
        return result
    root = Path(root_text).resolve()
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    catalog = {}
    if common:
        try:
            catalog = json.loads((Path(common) / "rwgk-wt.json").read_text())
            if not isinstance(catalog, dict) or catalog.get("version") != 1:
                catalog = {}
        except (OSError, ValueError):
            catalog = {}
    config = catalog.get("config", {})
    config = config if isinstance(config, dict) else {}
    branches = catalog.get("branches", {})
    entry = branches.get(branch, {}) if isinstance(branches, dict) else {}
    entry = entry if isinstance(entry, dict) else {}
    pr = entry.get("pr") or {}
    pr = pr if isinstance(pr, dict) else {}
    # Receiving repository is canonical even when the PR head comes from a fork.
    repository = repository_identity(pr.get("base_repo") or config.get("upstream_repo"))
    if not repository:
        remotes = _git(root, "remote").splitlines()
        preferred = [name for name in ("upstream", "origin") if name in remotes]
        preferred += [name for name in remotes if name not in preferred]
        for remote in preferred:
            repository = repository_identity(_git(root, "remote", "get-url", remote))
            if repository:
                break
    repository = repository or "local:" + (common or str(root))
    result.update(root=str(root), repo=repository, branch=branch)
    # Worktree roots remain distinct for detached heads and outside normal wt pairs.
    workspace_key = f"workspace:{repository}:{branch or str(root)}"
    result["keys"].append(
        {"key": workspace_key, "kind": "workspace", "display": str(root)}
    )
    # Never trust a stale catalog entry claiming ownership of another worktree.
    owner = entry.get("path")
    if owner and Path(owner).resolve() != root:
        return result
    url = pr.get("url")
    if not url and pr.get("number") and pr.get("base_repo"):
        url = f"https://github.com/{pr['base_repo']}/pull/{pr['number']}"
    if url:
        from .resources import normalize

        result["pr_url"] = url
        result["keys"].append(normalize(url, cwd=root))
    return result
