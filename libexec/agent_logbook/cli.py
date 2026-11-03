"""Record useful session links and find saved Codex conversations."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import context, nicknames, resources, store

RELATIONS = ("created", "modified", "reviewed", "discussed", "commented", "workspace")


def parser():
    result = argparse.ArgumentParser(prog="agent-logbook", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    record = commands.add_parser(
        "record", help="associate resources and a short hint with this session"
    )
    record.add_argument("resources", nargs="*")
    record.add_argument("--relation", choices=RELATIONS, default="discussed")
    record.add_argument("--note", default="")
    record.add_argument("--title", default="")
    record.add_argument("--session", default=None)
    record.add_argument("--transcript-path", default=None)
    record.add_argument("--cwd", default=None)
    record.add_argument("--json", action="store_true")
    nickname = commands.add_parser(
        "nickname", help="assign, list, or delete session nicknames"
    )
    nickname.add_argument("name", nargs="?")
    nickname.add_argument("session_id", nargs="?", help="defaults to CODEX_THREAD_ID")
    nickname.add_argument(
        "--delete", action="store_true", help="remove only the nickname"
    )
    nickname.add_argument("--json", action="store_true")
    for name in ("find", "here", "resume"):
        command = commands.add_parser(
            name,
            help={
                "find": "find sessions by resource",
                "here": "find sessions for this workspace",
                "resume": "resume an exact global nickname",
            }[name],
        )
        if name == "find":
            command.add_argument("query")
        elif name == "resume":
            command.add_argument("nickname")
        command.add_argument("--cwd", default=None)
        command.add_argument(
            "--list", action="store_true", help="list results without resuming"
        )
        command.add_argument(
            "--json", action="store_true", help="emit results without resuming"
        )
    commands.add_parser("hook", help="receive a Codex lifecycle event on stdin")
    commands.add_parser("path", help="print the selected logbook directory")
    return result


def _line(value, limit=180):
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())[
        :limit
    ]


def _codex_home():
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def _inside_history(path):
    path = path.expanduser().resolve()
    return any(
        path.is_relative_to(_codex_home() / kind)
        for kind in ("sessions", "archived_sessions")
    )


def _transcript_matches(path, session_id):
    if (
        not path.name.endswith("-" + session_id + ".jsonl")
        and path.name != session_id + ".jsonl"
    ):
        return False
    try:
        with path.open() as stream:
            metadata = json.loads(stream.readline())
        payload = metadata.get("payload", {})
        return (
            metadata.get("type") == "session_meta"
            and payload.get("id", payload.get("session_id")) == session_id
        )
    except (OSError, ValueError, AttributeError):
        return False


def _find_transcript(session_id, preferred=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return None
    if preferred:
        path = Path(preferred)
        if (
            path.is_file()
            and _inside_history(path)
            and _transcript_matches(path, session_id)
        ):
            return path
    for kind in ("sessions", "archived_sessions"):
        root = _codex_home() / kind
        if root.is_dir():
            for path in root.glob(f"**/*{glob.escape(session_id)}*.jsonl"):
                if (
                    path.is_file()
                    and _inside_history(path)
                    and _transcript_matches(path, session_id)
                ):
                    return path
    return None


def _local_location(session):
    if session.get("agent", "codex") != "codex":
        return None
    host = store.host_name()
    locations = list(session.get("locations", []))
    if session.get("host") == host:
        locations.append(
            {key: session.get(key, "") for key in ("host", "cwd", "transcript_path")}
        )
    for location in reversed(locations):
        if location.get("host") == host:
            path = _find_transcript(
                session["session_id"], location.get("transcript_path")
            )
            if path:
                return {**location, "transcript_path": str(path)}
    # Shared home may make this session available even when it was last used elsewhere.
    path = _find_transcript(session["session_id"])
    if path:
        return {
            "host": host,
            "cwd": session.get("cwd", ""),
            "transcript_path": str(path),
        }
    return None


def _matching(sessions, query, cwd):
    canonical = resources.normalize(query, cwd=cwd)
    exact = []
    possible = []
    needle = query.casefold()
    basename = (
        "/" not in query
        and "\\" not in query
        and query not in (".", "..")
        and not (Path(cwd) / query).exists()
    )
    absolute = Path(query).expanduser()
    absolute = str(absolute.resolve()) if absolute.is_absolute() else None
    for session in sessions:
        links = []
        for link in session.get("links", []):
            matches_file = False
            if link.get("kind") == "file":
                display = link.get("display", "")
                matches_file = bool(
                    basename
                    and Path(link.get("path") or display).name == query
                    or absolute
                    and str(Path(display).expanduser().resolve()) == absolute
                )
            if link.get("key") == canonical["key"] or matches_file:
                links.append(link)
        if session["session_id"] == query or links:
            exact.append({**session, "match": "exact", "matched_links": links})
            continue
        hints = [session.get(key, "") for key in ("title", "note", "cwd")]
        hints.extend(session.get("notes", []))
        hints.extend(link.get("display", "") for link in session.get("links", []))
        hints.extend(link.get("key", "") for link in session.get("links", []))
        if any(needle in str(hint).casefold() for hint in hints):
            possible.append({**session, "match": "possible", "matched_links": []})
    return exact if exact else possible


def _here(sessions, cwd):
    workspace = context.workspace(cwd)
    if workspace.get("pr_url"):
        matches = _matching(sessions, workspace["pr_url"], cwd)
        exact = [match for match in matches if match["match"] == "exact"]
        if exact:
            return exact
    keys = {
        item["key"]
        for item in workspace.get("keys", [])
        if item.get("kind") == "workspace"
    }
    return [
        {
            **session,
            "match": "exact",
            "matched_links": [
                link for link in session.get("links", []) if link.get("key") in keys
            ],
        }
        for session in sessions
        if any(link.get("key") in keys for link in session.get("links", []))
    ]


def _resume_command(session, location, cwd=None):
    saved = cwd or location.get("cwd")
    if not saved:
        return None
    chosen = Path(saved).expanduser()
    if not chosen.is_dir():
        return None
    return ["codex", "resume", session["session_id"], "-C", str(chosen.resolve())]


def _saved_session(sessions, session_id):
    for session in sessions:
        if (
            session.get("agent", "codex") == "codex"
            and session["session_id"] == session_id
        ):
            return session
    transcript = _find_transcript(session_id)
    if transcript is None:
        raise ValueError(
            f"No recorded session or local Codex history for {session_id}."
        )
    with transcript.open() as stream:
        payload = json.loads(stream.readline())["payload"]
    cwd = payload.get("cwd", "")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        cwd = ""
    return {
        "agent": "codex",
        "session_id": session_id,
        "host": store.host_name(),
        "cwd": cwd,
        "transcript_path": str(transcript),
    }


def _nickname(args, directory=None):
    if args.name is None:
        if args.delete:
            raise ValueError("Provide a nickname to delete.")
        rows = [
            {"name": name, "session_id": target}
            for name, target in nicknames.load(directory).items()
        ]
        if args.json:
            print(json.dumps({"nicknames": rows}, indent=2))
        else:
            for row in rows:
                print(f"{row['name']}  {row['session_id']}")
            if not rows:
                print("No session nicknames assigned.")
        return 0
    nicknames.validate_name(args.name)
    if args.delete:
        if args.session_id:
            raise ValueError("Do not supply a session ID when deleting a nickname.")
        target = nicknames.delete(args.name, directory)
        value = {"result": "deleted", "name": args.name, "session_id": target}
        message = (
            f"Deleted nickname {args.name} ({target}); session history is unchanged."
        )
    else:
        target = args.session_id or os.environ.get("CODEX_THREAD_ID")
        if not target:
            raise ValueError("Supply a session UUID outside a Codex session.")
        target = nicknames.session_id(target)
        sessions = store.load_sessions(directory)
        session = _saved_session(sessions, target)
        if not any(
            row.get("agent", "codex") == "codex" and row["session_id"] == target
            for row in sessions
        ):
            # Use the target's verified metadata, never the naming command's workspace.
            store.append_event(session, directory)
        previous = nicknames.assign(args.name, target, directory)
        result = (
            "unchanged"
            if previous == target
            else "reassigned"
            if previous
            else "assigned"
        )
        value = {
            "result": result,
            "name": args.name,
            "session_id": target,
            "previous_session_id": previous,
        }
        message = (
            f"{args.name}: {previous} -> {target}"
            if previous and previous != target
            else f"{args.name}: {target}"
        )
        if result == "unchanged":
            message += " (unchanged)"
    print(json.dumps(value, indent=2) if args.json else message)
    return 0


def _find(args, directory=None):
    cwd = str(Path(args.cwd or os.getcwd()).expanduser().resolve())
    if not Path(cwd).is_dir():
        raise ValueError(f"Working directory does not exist: {cwd}")
    sessions = store.load_sessions(directory=directory)
    query = (
        args.query.strip()
        if args.command == "find"
        else args.nickname
        if args.command == "resume"
        else cwd
    )
    if not query:
        raise ValueError("Provide a resource URL, filename, session ID, or topic.")
    if args.command == "resume":
        target = nicknames.get(query, directory)
        if target is None:
            raise ValueError(
                f"Unknown session nickname: {query}. Use 'wt nickname' to list names."
            )
        matches = [
            {**_saved_session(sessions, target), "match": "exact", "matched_links": []}
        ]
    else:
        matches = (
            _matching(sessions, query, cwd)
            if args.command == "find"
            else _here(sessions, cwd)
        )
    saved_names = nicknames.load(directory)
    for session in matches:
        session["nicknames"] = [
            name
            for name, target in saved_names.items()
            if session.get("agent", "codex") == "codex"
            and target == session["session_id"]
        ]
    matches.sort(key=lambda session: session.get("updated_at", ""), reverse=True)
    for session in matches:
        location = _local_location(session)
        session["available_locally"] = location is not None
        target_cwd = cwd if args.command == "here" or args.cwd is not None else None
        session["resume_command"] = (
            _resume_command(session, location, target_cwd) if location else None
        )
        session["resume_blocked_reason"] = (
            None
            if session["resume_command"]
            else "saved_cwd_missing"
            if location
            else "history_unavailable"
        )
    if args.json:
        print(json.dumps({"query": query, "matches": matches}, indent=2))
        return 0 if matches else 1
    if not matches:
        print(
            f"No recorded sessions for {_line(query)}. Try codex resume --all or start a new session.",
            file=sys.stderr,
        )
        return 1
    if (
        len(matches) == 1
        and matches[0]["match"] == "exact"
        and not args.list
        and matches[0]["resume_command"] is not None
    ):
        command = matches[0]["resume_command"]
        label = (
            f" ({', '.join(matches[0]['nicknames'])})"
            if matches[0]["nicknames"]
            else ""
        )
        print(f"Resuming {matches[0]['session_id']}{label}", flush=True)
        return subprocess.run(command, check=False).returncode
    for session in matches:
        relations = sorted(
            {link.get("relation", "") for link in session["matched_links"]} - {""}
        )
        print(
            f"{session['session_id']}  {_line(session.get('updated_at', ''), 32)}  {session['match']}  {','.join(relations)}"
        )
        if session["nicknames"]:
            print(f"  nicknames: {', '.join(session['nicknames'])}")
        hint = session.get("note") or session.get("title") or session.get("cwd")
        if hint:
            print(f"  {_line(hint)}")
        hosts = session.get("hosts") or [session.get("host", "unknown")]
        print(f"  hosts: {_line(', '.join(hosts))}")
        if session["resume_command"]:
            print(f"  {shlex.join(session['resume_command'])}")
        elif session["available_locally"]:
            print(
                "  Saved history is available, but its working directory is missing or unrecorded."
            )
            print("  Recreate the worktree or select a directory explicitly:")
            lookup = (
                ["wt", "codex", query]
                if args.command == "resume"
                else ["codex-find", query]
            )
            print(f"  {shlex.join([*lookup, '--cwd', '/path/to/worktree'])}")
        else:
            print(
                f"  Saved history is unavailable here; resume on a recorded host with: codex resume {shlex.quote(session['session_id'])}"
            )
    if any(session["match"] == "possible" for session in matches):
        print(
            "Possible matches come from recorded hints; inspect them before resuming."
        )
    return 0


def _record(args, directory=None):
    session_id = (
        args.session
        or os.environ.get("AGENT_LOGBOOK_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
    )
    if not session_id:
        raise ValueError("Use --session SESSION_ID outside a Codex session.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise ValueError("Session ID must contain only letters, digits, '-' and '_'.")
    cwd = str(Path(args.cwd or os.getcwd()).expanduser().resolve())
    workspace = context.workspace(cwd)
    links = [{**item, "relation": "workspace"} for item in workspace.get("keys", [])]
    links.extend(
        {**resources.normalize(value, cwd=cwd), "relation": args.relation}
        for value in args.resources
    )
    transcript = args.transcript_path or _find_transcript(session_id)
    event = {
        "agent": "codex",
        "session_id": session_id,
        "cwd": cwd,
        "links": links,
        "note": args.note,
        "title": args.title,
    }
    if transcript:
        event["transcript_path"] = str(transcript)
    path = store.append_event(event, directory=directory)
    if args.json:
        print(json.dumps({"session_id": session_id, "record": str(path)}, indent=2))
    else:
        print(f"Recorded {session_id}: {len(args.resources)} resource(s).")
    return 0


def _hook(directory=None):
    event = {}
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise TypeError("Hook input must be a JSON object.")
        from . import hooks

        hooks.handle(event, directory=directory)
    except Exception as error:  # noqa: BLE001 -- hooks must never interrupt agent work
        print(
            f"agent-logbook: hook could not record activity: {_line(error)}",
            file=sys.stderr,
        )
    if isinstance(event, dict) and event.get("hook_event_name") in (
        "Stop",
        "SubagentStop",
    ):
        print("{}")
    return 0


def main(argv=None, *, directory=None):
    args = parser().parse_args(argv)
    if args.command == "hook":
        return _hook(directory=directory)
    try:
        if args.command == "path":
            print(store.default_directory() if directory is None else directory)
            return 0
        if args.command == "record":
            return _record(args, directory=directory)
        if args.command == "nickname":
            return _nickname(args, directory=directory)
        return _find(args, directory=directory)
    except (OSError, ValueError, KeyError) as error:
        print(f"agent-logbook: {_line(error)}", file=sys.stderr)
        return 1
