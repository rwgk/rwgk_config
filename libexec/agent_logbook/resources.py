"""Stable resource identities across URL spellings and worktree locations."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .context import workspace


def normalize(value, cwd=None):
    value = str(value).strip()
    if not value or "\0" in value or "\n" in value:
        raise ValueError("A nonempty, single-line resource is required.")
    if value.startswith(("workspace:", "file:", "github:", "slack:", "url:")):
        return {"key": value, "kind": value.split(":", 1)[0], "display": value}
    bug = re.fullmatch(r"nvbugs?[\s:#]+(\d+)", value, re.IGNORECASE)
    if bug:
        number = str(int(bug[1]))
        return {"key": f"nvbug:{number}", "kind": "nvbug", "display": f"nvbug {number}"}
    shorthand = re.fullmatch(r"([^/#\s]+)/([^/#\s]+)#(\d+)", value)
    if shorthand:
        owner, repo, number = shorthand.groups()
        value = f"https://github.com/{owner}/{repo}/issues/{int(number)}"
    parsed = urlsplit(value)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        host = parsed.hostname.lower()
        path = parsed.path.rstrip("/")
        github = re.match(r"^/([^/]+)/([^/]+)/(?:pull|issues)/(\d+)(?:/|$)", path)
        if host == "github.com" and github:
            owner, repo, number = github.groups()
            owner, repo, number = owner.lower(), repo.lower(), str(int(number))
            return {
                "key": f"github:{owner}/{repo}#{number}",
                "kind": "github",
                "display": f"https://github.com/{owner}/{repo}/issues/{number}",
            }
        slack = re.fullmatch(r"/archives/([^/]+)/p(\d+)", path)
        if host.endswith(".slack.com") and slack:
            channel, timestamp = slack.groups()
            thread = parse_qs(parsed.query).get("thread_ts", [""])[0]
            if re.fullmatch(r"\d+\.\d{6}", thread):
                timestamp = thread.replace(".", "")
            canonical = f"https://{host}/archives/{channel}/p{timestamp}"
            return {"key": "slack:" + canonical, "kind": "slack", "display": canonical}
        if "nvbugs" in host:
            query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
            number = next(
                (query[key][0] for key in ("bugid", "id") if key in query), ""
            )
            if not number:
                match = re.search(r"(?:^|/)(\d+)$", path)
                number = match[1] if match else ""
            if number.isdigit():
                return normalize("nvbug:" + number)
        # Keep query/fragment for arbitrary URLs: they may identify a document.
        # Userinfo is always removed so a pasted authenticated URL cannot leak it.
        port = f":{parsed.port}" if parsed.port else ""
        canonical = urlunsplit(
            (parsed.scheme.lower(), host + port, path, parsed.query, parsed.fragment)
        )
        return {"key": "url:" + canonical, "kind": "url", "display": canonical}
    path = Path(unquote(value)).expanduser()
    base = Path(cwd or os.getcwd()).expanduser().resolve()
    absolute = (base / path).resolve() if not path.is_absolute() else path.resolve()
    context = workspace(base)
    if not context["root"] or not absolute.is_relative_to(context["root"]):
        probe = absolute.parent
        while not probe.is_dir() and probe != probe.parent:
            probe = probe.parent
        context = workspace(probe)
    if context["root"] and absolute.is_relative_to(context["root"]):
        relative = absolute.relative_to(context["root"]).as_posix()
        return {
            "key": f"file:{context['repo']}:{relative}",
            "kind": "file",
            "display": str(absolute),
            "path": relative,
            "repo": context["repo"],
        }
    return {"key": "file:" + str(absolute), "kind": "file", "display": str(absolute)}
