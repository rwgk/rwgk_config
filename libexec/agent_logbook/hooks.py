"""Conservative metadata capture for Codex lifecycle and successful write hooks.

This adapter never stores complete prompts, tool arguments, results, or transcripts.
Unknown tools and shell programs are left to explicit ``agent-logbook record``.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .context import workspace
from .resources import normalize
from .store import append_event, default_directory

_LIFECYCLE = {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
_URL_FIELDS = {
    "url",
    "html_url",
    "web_url",
    "permalink",
    "bug_url",
    "display_url",
    "message_url",
}
_SKIP_FIELDS = {
    "body",
    "description",
    "text",
    "comment",
    "message",
    "prompt",
    "content_base64",
    "arguments",
    "tool_input",
    "links",
}


def _objects(value, depth=0):
    """Unwrap structured results and JSON MCP text, without mining prose bodies."""
    if depth > 8:
        return
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")) and len(value) <= 1_000_000:
            try:
                yield from _objects(json.loads(value), depth + 1)
            except ValueError:
                pass
    elif isinstance(value, dict):
        yield value
        for key, child in value.items():
            if key == "content" and isinstance(child, list):
                for block in child:
                    if isinstance(block, dict) and block.get("type") == "text":
                        yield from _objects(block.get("text"), depth + 1)
            elif key not in _SKIP_FIELDS:
                yield from _objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child, depth + 1)


def _failed(objects):
    for item in objects:
        if any(
            item.get(key) is True
            for key in (
                "isError",
                "is_error",
                "requires_confirmation",
                "confirmation_required",
                "requires_approval",
                "approval_required",
            )
        ):
            return True
        if any(item.get(key) is False for key in ("success", "ok")):
            return True
        if str(item.get("status", "")).lower() in {
            "error",
            "failed",
            "failure",
            "pending",
            "confirmation_required",
            "requires_confirmation",
            "awaiting_approval",
            "pending_approval",
            "approval_required",
            "requires_approval",
            "awaiting_confirmation",
            "needs_confirmation",
        }:
            return True
        for key in (
            "status",
            "status_code",
            "statusCode",
            "http_status",
            "http_status_code",
        ):
            status = item.get(key)
            if (
                isinstance(status, (int, float))
                and not isinstance(status, bool)
                and status >= 400
            ):
                return True
            if isinstance(status, str) and status.isdigit() and int(status) >= 400:
                return True
        if item.get("error"):
            return True
        for key in ("exit_code", "exitCode", "returncode", "exit_status"):
            if key in item and item[key] not in (0, "0", None):
                return True
    return False


def _link(value, relation, cwd, evidence):
    try:
        item = normalize(str(value), cwd=cwd)
    except (OSError, ValueError):
        return None
    return {**item, "relation": relation, "evidence": evidence}


def _patch_succeeded(response):
    # Some Codex versions expose only {} for a successful freeform patch.
    if isinstance(response, str):
        stripped = response.lstrip()
        if stripped.startswith("{"):
            try:
                return _patch_succeeded(json.loads(stripped))
            except ValueError:
                return False
        return stripped.startswith("Success")
    if not isinstance(response, dict) or _failed(list(_objects(response))):
        return False
    failure = re.compile(
        r"^(?:error\b|failed\b|failure\b|invalid patch\b|patch (?:failed|rejected)\b|could not\b|unable to\b)",
        re.IGNORECASE,
    )
    for item in _objects(response):
        values = [item.get("output"), item.get("stdout")]
        values += (
            [
                block.get("text")
                for block in item.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if isinstance(item.get("content"), list)
            else []
        )
        if any(
            isinstance(value, str) and failure.match(value.lstrip()) for value in values
        ):
            return False
    return True


def _paths(patch):
    if not isinstance(patch, str):
        return []
    result = []
    for line in patch.splitlines():
        match = re.fullmatch(
            r"\*\*\* (Add File|Update File|Delete File|Move to): (.+)", line
        )
        if match:
            operation, value = match.groups()
            relation = {"Add File": "created", "Delete File": "deleted"}.get(
                operation, "modified"
            )
            result.append((value, relation))
    return result


def _service_url(value, service):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
    ):
        return False
    if service == "github":
        return (
            url.hostname == "github.com"
            and bool(re.search(r"/(?:pull|issues)/\d+", url.path))
        ) or (
            url.hostname == "api.github.com"
            and bool(
                re.match(r"^/repos/[^/]+/[^/]+/(?:pulls|issues)/\d+(?:/|$)", url.path)
            )
        )
    if service == "gitlab":
        return bool(re.search(r"/(?:-/)?(?:merge_requests|issues)/\d+", url.path))
    if service == "slack":
        return url.hostname.endswith(".slack.com") and "/archives/" in url.path
    return (
        service == "nvbugs"
        and (url.hostname == "nvidia.com" or url.hostname.endswith(".nvidia.com"))
        and "bug" in value.lower()
    )


def _web_url(value):
    parsed = urlsplit(value)
    if parsed.hostname == "api.github.com":
        match = re.match(
            r"^/repos/([^/]+)/([^/]+)/(pulls|issues)/(\d+)(?:/|$)", parsed.path
        )
        if match:
            owner, repo, kind, number = match.groups()
            return f"https://github.com/{owner}/{repo}/{'pull' if kind == 'pulls' else 'issues'}/{number}"
    return value


def _operation(name, args):
    """Recognize write operations, never read tools merely returning a URL."""
    lowered = name.lower()
    if "nvbugs" in lowered:
        if re.search(r"nvbugs_create_bug(?:_v\d+)?$", lowered):
            return "nvbugs", "created"
        if re.search(r"nvbugs_update_bug(?:_v\d+)?$", lowered):
            return "nvbugs", "modified"
    if "slack" in lowered:
        if re.search(r"(?:slack_)?(?:post|send)_message$", lowered):
            return "slack", "created"
        if re.search(r"(?:slack_)?(?:edit|update)_message$", lowered):
            return "slack", "modified"
    if "github" in lowered or "gitlab" in lowered:
        service = "github" if "github" in lowered else "gitlab"
        if lowered.endswith("__issue_write") and args.get("method") in {
            "create",
            "update",
        }:
            return service, "created" if args["method"] == "create" else "modified"
        if lowered.endswith("__pull_request_review_write") and args.get("method") in {
            "create",
            "submit_pending",
        }:
            return service, "reviewed"
        if re.search(
            r"(?:create|update)_(?:pull_request|merge_request|issue)$", lowered
        ):
            return service, "created" if "create_" in lowered else "modified"
        if re.search(
            r"(?:add_comment_to_issue|add_issue_comment|create_note|create_merge_request_note|reply_to_review_comment|add_reply_to_pull_request_comment)$",
            lowered,
        ):
            return service, "commented"
        if re.search(
            r"(?:update_issue_comment|update_review_comment|update_merge_request_note)$",
            lowered,
        ):
            return service, "modified"
        if re.search(
            r"(?:submit_pull_request_review|create_pull_request_review|add_comment_to_pending_review)$",
            lowered,
        ):
            return service, "reviewed"
    return None


def _mcp_links(name, args, response, cwd, evidence):
    operation = _operation(name, args)
    if operation is None:
        return []
    service, relation = operation
    objects = list(_objects(response))
    if not objects or _failed(objects):
        return []
    if service == "nvbugs":
        # MaaS confirmation/module-choice responses intentionally save nothing.
        confirmed = "confirm_create" if relation == "created" else "confirm_update"
        if args.get(confirmed) is not True or not any(
            item.get("success") is True for item in objects
        ):
            return []
    values = []
    for item in objects:
        values.extend(
            _web_url(value)
            for key, value in item.items()
            if key in _URL_FIELDS and _service_url(value, service)
        )
    if service == "slack" and re.fullmatch(
        r"\d+\.\d{6}", str(args.get("thread_ts", ""))
    ):
        # Keep the reply identity and add the copied-URL thread identity as an alias.
        for value in list(values):
            parsed = urlsplit(value)
            query = parse_qs(parsed.query)
            query["thread_ts"] = [str(args["thread_ts"])]
            values.append(
                urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        urlencode(query, doseq=True),
                        parsed.fragment,
                    )
                )
            )
    if service == "nvbugs":
        # A template update can create a NEW bug. Prefer returned ID to input ID.
        data = next((item for item in objects if item.get("bug_id")), None)
        if data:
            bug_id = str(data["bug_id"])
            if data.get("source_template_bug_id"):
                relation = "created"
        else:
            bug_id = str(args.get("bug_id", ""))
        if bug_id.isdigit():
            values.append("nvbug " + bug_id)
    if not values and service == "github":
        repo = args.get("repository_full_name") or args.get("repo_full_name")
        if not repo and args.get("owner") and args.get("repo"):
            repo = f"{args['owner']}/{args['repo']}"
        number = (
            args.get("pr_number")
            or args.get("pullNumber")
            or args.get("pull_number")
            or args.get("issue_number")
            or args.get("issueNumber")
        )
        if (
            repo
            and number
            and re.fullmatch(r"[^/\s]+/[^/\s]+", str(repo))
            and str(number).isdigit()
        ):
            kind = "pull" if "pr_number" in args or "pull" in name.lower() else "issues"
            values.append(f"https://github.com/{repo}/{kind}/{number}")
    return [link for value in values if (link := _link(value, relation, cwd, evidence))]


def _shell_links(args, response, cwd, evidence):
    command = args.get("cmd", args.get("command", ""))
    if not isinstance(command, str):
        return []
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    if (
        len(tokens) < 3
        or tokens[0] != "gh"
        or tokens[1] not in {"pr", "issue"}
        or tokens[2] not in {"create", "edit", "comment", "review"}
    ):
        return []
    if any(token and set(token) <= set(";&|<>()") for token in tokens):
        return []
    if any(
        token in {"--dry-run", "--help", "-h"} or token.startswith("--dry-run=")
        for token in tokens[3:]
    ):
        return []
    objects = list(_objects(response))
    if _failed(objects):
        return []
    # A running async command isn't yet evidence of a completed write.
    if not any(
        any(
            item.get(key) in (0, "0")
            for key in ("exit_code", "exitCode", "returncode", "exit_status")
        )
        for item in objects
    ):
        return []
    values = []
    for item in objects:
        for key in ("output", "stdout"):
            kind = "pull" if tokens[1] == "pr" else "issues"
            values.extend(
                line.strip()
                for line in str(item.get(key, "")).splitlines()
                if re.fullmatch(
                    rf"https://github\.com/[^/\s]+/[^/\s]+/{kind}/\d+(?:#[^\s]+)?",
                    line.strip(),
                )
            )
    if not values and tokens[2] != "create":
        selector = tokens[3] if len(tokens) > 3 else ""
        if _service_url(selector, "github"):
            values.append(selector)
        elif selector.isdigit():
            repo = None
            for index, token in enumerate(tokens):
                if token in {"-R", "--repo"} and index + 1 < len(tokens):
                    repo = tokens[index + 1]
                elif token.startswith("--repo="):
                    repo = token.partition("=")[2]
            if repo and re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
                values.append(
                    f"https://github.com/{repo}/{'pull' if tokens[1] == 'pr' else 'issues'}/{selector}"
                )
    relation = {
        "create": "created",
        "edit": "modified",
        "comment": "commented",
        "review": "reviewed",
    }[tokens[2]]
    return [link for value in values if (link := _link(value, relation, cwd, evidence))]


def _session_meta(path, session_id):
    """Read only the first metadata record, never inherited conversation history."""
    try:
        with Path(path).open(encoding="utf-8") as stream:
            first = stream.readline(1_048_577)
        if len(first) > 1_048_576:
            return None
        record = json.loads(first)
        payload = record.get("payload")
        if record.get("type") != "session_meta" or not isinstance(payload, dict):
            return None
        if (payload.get("id") or payload.get("session_id")) != session_id:
            return None
        return payload
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
        return None


def _parent_transcript(session_id, current):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id):
        return None
    current = Path(current).expanduser().resolve()
    homes = [Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()]
    for ancestor in current.parents:
        if ancestor.name in {"sessions", "archived_sessions"}:
            homes.append(ancestor.parent)
            break
    pattern = "*" + glob.escape(session_id) + ".jsonl"
    candidates = list(current.parent.glob(pattern))
    for home in dict.fromkeys(homes):
        for name in ("sessions", "archived_sessions"):
            candidates.extend((home / name).rglob(pattern))
    for candidate in candidates:
        if _session_meta(candidate, session_id) is not None:
            return candidate.resolve()
    return None


def _main_session(event):
    """Attribute subagent activity to the resumable main conversation when known."""
    original = str(event["session_id"])
    session_id = original
    transcript = event.get("transcript_path")
    visited = {session_id}
    for _ in range(8):
        metadata = _session_meta(transcript, session_id) if transcript else None
        if metadata is None:
            break
        source = metadata.get("source")
        source = source if isinstance(source, dict) else {}
        subagent = source.get("subagent")
        subagent = subagent if isinstance(subagent, dict) else {}
        spawn = subagent.get("thread_spawn")
        spawn = spawn if isinstance(spawn, dict) else {}
        parent = metadata.get("parent_thread_id") or spawn.get("parent_thread_id")
        if not isinstance(parent, str) or not parent or parent in visited:
            break
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", parent):
            break
        visited.add(parent)
        parent_path = _parent_transcript(parent, transcript)
        session_id = parent
        transcript = str(parent_path) if parent_path else None
    if session_id == original:
        return event
    result = {**event, "session_id": session_id, "contributor_session_id": original}
    if transcript:
        result["transcript_path"] = transcript
    else:
        result.pop("transcript_path", None)
    return result


def handle(event, directory=None):
    """Record one supported hook event; callers own best-effort error handling."""
    if not isinstance(event, dict) or not event.get("session_id"):
        return
    event = _main_session(event)
    event_name = event.get("hook_event_name")
    if event_name not in _LIFECYCLE | {"PostToolUse"}:
        return
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return
    args = event.get("tool_input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        args = {}
    workdir = (
        args.get("workdir", args.get("cwd")) if event_name == "PostToolUse" else None
    )
    if isinstance(workdir, str) and workdir:
        cwd = str((Path(cwd) / workdir).resolve())
    links = []
    note = None
    if event_name in _LIFECYCLE:
        context = workspace(cwd)
        for item in context.get("keys", []):
            links.append({**item, "relation": "workspace"})
        if context.get("pr_url"):
            link = _link(context["pr_url"], "workspace", cwd, event_name)
            if link:
                links.append(link)
        if event_name == "UserPromptSubmit" and isinstance(event.get("prompt"), str):
            first = event["prompt"].strip().split("\n\n", 1)[0]
            note = " ".join(first.split())[:160]
    else:
        name = str(event.get("tool_name", ""))
        response = event.get("tool_response")
        objects = list(_objects(response))
        if _failed(objects):
            return
        evidence = name + (
            ":" + str(event["tool_use_id"]) if event.get("tool_use_id") else ""
        )
        if name == "apply_patch" or name.endswith("__apply_patch"):
            if not _patch_succeeded(response):
                return
            patch = args.get("command", args.get("patch", ""))
            for value, relation in _paths(patch):
                path = (Path(cwd) / value).resolve()
                index = Path(directory or default_directory()).resolve()
                if path == index or index in path.parents:
                    continue
                link = _link(value, relation, cwd, evidence)
                if link:
                    links.append(link)
        elif name in {
            "Bash",
            "exec_command",
            "shell",
            "shell_command",
        } or name.endswith("__exec_command"):
            links.extend(_shell_links(args, response, cwd, evidence))
        else:
            links.extend(_mcp_links(name, args, response, cwd, evidence))
        if not links:
            return
    # Avoid storing duplicate links from response aliases or repeated workspace keys.
    unique = {(item["key"], item["relation"]): item for item in links}
    record = {
        "agent": "codex",
        "session_id": str(event["session_id"]),
        "cwd": cwd,
        "links": list(unique.values()),
    }
    for key in ("transcript_path", "model", "contributor_session_id"):
        if event.get(key):
            record[key] = event[key]
    if note:
        record["note"] = note
    append_event(record, directory=directory)
