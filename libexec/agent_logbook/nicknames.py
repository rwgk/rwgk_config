"""Global nicknames point to exact Codex session IDs independently of titles."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from . import store


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", name
    ):
        raise ValueError(
            "Nickname must start with a letter or digit and contain only letters, "
            "digits, '.', '_' or '-'."
        )
    return name


def session_id(value):
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid Codex session UUID: {value}") from error


def _directory(directory):
    root = Path(directory) if directory is not None else store.default_directory()
    return root / "nicknames"


def get(name, directory=None):
    path = _directory(directory) / (validate_name(name) + ".json")
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        value = json.loads(text)
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("name") != name
        ):
            raise ValueError("unsupported nickname version or shape")
        return session_id(value.get("session_id"))
    except ValueError as error:
        raise ValueError(
            f"Cannot read agent-logbook nickname {path}: {error}"
        ) from error


def load(directory=None):
    try:
        with os.scandir(_directory(directory)) as entries:
            names = sorted(
                entry.name[:-5]
                for entry in entries
                if entry.name.endswith(".json") and not entry.name.startswith(".")
            )
    except FileNotFoundError:
        return {}
    result = {}
    for name in names:
        target = get(name, directory)
        if target is not None:
            result[name] = target
    return result


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def assign(name, target, directory=None):
    validate_name(name)
    target = session_id(target)
    previous = get(name, directory)
    if previous == target:
        return previous
    parent = _directory(directory)
    store._private_directory(parent.parent)
    store._private_directory(parent)
    encoded = json.dumps({"version": 1, "name": name, "session_id": target}) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".nickname-", dir=parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, parent / (name + ".json"))
        _sync_directory(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return previous


def delete(name, directory=None):
    previous = get(name, directory)
    if previous is None:
        raise ValueError(f"Unknown session nickname: {name}")
    parent = _directory(directory)
    (parent / (name + ".json")).unlink()
    _sync_directory(parent)
    return previous
