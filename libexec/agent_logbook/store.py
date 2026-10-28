"""Immutable event files for concurrent writers on a shared filesystem."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path


def default_directory():
    """Choose one store; access errors must not silently create another store."""
    explicit = os.environ.get("AGENT_LOGBOOK_DIR")
    if explicit:
        return Path(explicit).expanduser()
    for name in ("H", "W"):
        base = os.environ.get(name)
        if base:
            if not Path(base).is_dir():
                raise ValueError(
                    "Configured $" + f"{name} is not an accessible directory: {base}"
                )
            return Path(base) / "agent-logbook"
    return Path.home() / ".local" / "share" / "agent-logbook"


def host_name():
    return socket.getfqdn() or socket.gethostname()


def _private_directory(path):
    # mkdir(mode=...) respects umask and never changes existing directory modes.
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _validate(record):
    if not isinstance(record, dict) or record.get("version") != 1:
        raise ValueError("unsupported event version or shape")
    for name in ("agent", "session_id"):
        if not isinstance(record.get(name), str) or not record[name]:
            raise ValueError(f"a nonempty {name} is required")
        if any(ord(c) < 32 for c in record[name]):
            raise ValueError(f"invalid {name}")
    for name in ("host", "cwd", "transcript_path", "model", "note", "title"):
        if (
            name in record
            and record[name] is not None
            and not isinstance(record[name], str)
        ):
            raise ValueError(f"invalid {name}")
    try:
        timestamp = datetime.fromisoformat(record["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError("timestamp needs a timezone")
        record["timestamp"] = timestamp.astimezone(UTC).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid event timestamp") from error
    if not isinstance(record.get("links", []), list):
        raise TypeError("invalid links")
    for link in record.get("links", []):
        if (
            not isinstance(link, dict)
            or not isinstance(link.get("key"), str)
            or not link["key"]
        ):
            raise ValueError("invalid resource link")
        for name in ("kind", "display", "relation"):
            if name in link and not isinstance(link[name], str):
                raise ValueError(f"invalid link {name}")
    return record


def append_event(event, directory=None):
    """Publish one immutable event atomically; no lock or shared mutable file."""
    if not isinstance(event, dict):
        raise TypeError("An event must be an object.")
    record = copy.deepcopy(event)
    record.setdefault("agent", "codex")
    record.setdefault("host", host_name())
    record.setdefault("timestamp", datetime.now(UTC).isoformat())
    record["version"] = 1
    _validate(record)
    # Validate JSON before creating any store entries.
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
    root = Path(directory) if directory is not None else default_directory()
    _private_directory(root)
    _private_directory(root / "events")
    shard = hashlib.sha256(
        (record["agent"] + "\0" + record["session_id"]).encode()
    ).hexdigest()[:2]
    parent = root / "events" / shard
    _private_directory(parent)
    destination = parent / (uuid.uuid4().hex + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".event-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def load_sessions(directory=None):
    """Fold events without losing former host locations, notes, or resource links."""
    root = Path(directory) if directory is not None else default_directory()
    try:
        with os.scandir(root) as entries:
            root_entries = list(entries)
    except FileNotFoundError:
        return []
    events_entry = next(
        (entry for entry in root_entries if entry.name == "events"), None
    )
    if events_entry is None:
        return []
    # pathlib glob silently suppresses directory permission errors. Enumerate
    # explicitly so missing shards cannot turn an incomplete index into one match.
    with os.scandir(root / "events") as entries:
        shards = sorted(entries, key=lambda entry: entry.name)
    paths = []
    for shard in shards:
        if shard.name.startswith("."):
            continue
        if not shard.is_dir():
            raise ValueError(
                f"Agent-logbook event shard is not a directory: {shard.path}"
            )
        with os.scandir(shard.path) as entries:
            for entry in entries:
                if entry.name.endswith(".json"):
                    if not entry.is_file():
                        raise ValueError(
                            f"Agent-logbook event is not a file: {entry.path}"
                        )
                    paths.append(Path(entry.path))
    events = []
    for path in sorted(paths):
        try:
            value = json.loads(path.read_text())
            if isinstance(value, dict):
                value.setdefault("agent", "codex")
            events.append(_validate(value))
        except (ValueError, TypeError, UnicodeError) as error:
            raise ValueError(
                f"Cannot read agent-logbook event {path}: {error}"
            ) from error
    sessions = {}
    for event in sorted(events, key=lambda row: row["timestamp"]):
        key = (event.get("agent", "codex"), event["session_id"])
        timestamp = event["timestamp"]
        session = sessions.setdefault(
            key,
            {
                "agent": key[0],
                "session_id": key[1],
                "created_at": timestamp,
                "updated_at": timestamp,
                "hosts": [],
                "locations": [],
                "notes": [],
                "links": [],
            },
        )
        session["updated_at"] = timestamp
        for name in ("host", "cwd", "transcript_path", "model", "title", "note"):
            if event.get(name):
                session[name] = event[name]
        host = event.get("host", "")
        if host and host not in session["hosts"]:
            session["hosts"].append(host)
        location = {
            name: event[name]
            for name in ("host", "cwd", "transcript_path")
            if event.get(name)
        }
        if (
            location.get("cwd") or location.get("transcript_path")
        ) and location not in session["locations"]:
            # Later metadata may omit a transcript path; preserve both observations.
            session["locations"].append(location)
        note = event.get("note")
        if note and note not in session["notes"]:
            session["notes"].append(note)
        for link in event.get("links", []):
            if not isinstance(link, dict) or not isinstance(link.get("key"), str):
                continue
            relation = link.get("relation", "discussed")
            existing = next(
                (
                    old
                    for old in session["links"]
                    if old["key"] == link["key"] and old["relation"] == relation
                ),
                None,
            )
            if existing is None:
                session["links"].append(
                    {
                        **link,
                        "relation": relation,
                        "first_seen": timestamp,
                        "last_seen": timestamp,
                    }
                )
            else:
                existing.update({k: v for k, v in link.items() if v})
                existing["last_seen"] = timestamp
    return sorted(sessions.values(), key=lambda row: row["updated_at"], reverse=True)
