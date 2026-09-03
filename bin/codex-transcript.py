#!/usr/bin/env python3

"""Export the human-visible messages from one local Codex session."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VISIBLE_AGENT_PHASES = {"commentary", "final_answer"}
PACIFIC_TIME_ZONE = ZoneInfo("America/Los_Angeles")


class TranscriptError(Exception):
    """An error that can be reported without a traceback."""


class SessionFileError(TranscriptError):
    """A session candidate exists but cannot be safely decoded."""


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    text: str
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionTranscript:
    path: Path
    messages: tuple[Message, ...]
    last_activity: datetime
    ignored_incomplete_final_line: bool


@dataclass(frozen=True, slots=True)
class TranscriptMetadata:
    exported_at: str
    session_last_activity: str
    hostname: str
    codex_home: Path
    session_file: Path
    session_id: str


def usage(program_name: str) -> str:
    return (
        f"Usage: {program_name} SESSION_ID\n"
        "\n"
        "Print the human-visible transcript for one local Codex session as Markdown.\n"
        "SESSION_ID must be the session's UUID.\n"
    )


def normalize_session_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise TranscriptError(
            f"invalid session ID: {value!r}; expected a UUID"
        ) from error


def codex_home() -> Path:
    configured_home = os.environ.get("CODEX_HOME")
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".codex"


def format_pacific_datetime(moment: datetime) -> str:
    return moment.astimezone(PACIFIC_TIME_ZONE).strftime("%Y-%m-%d %H:%M %Z")


def format_message_datetime(moment: datetime) -> str:
    local_moment = moment.astimezone(PACIFIC_TIME_ZONE)
    return f"{local_moment.isoformat(timespec='seconds')} ({local_moment.tzname()})"


def format_elapsed_duration(previous: datetime, current: datetime) -> str | None:
    elapsed = current.astimezone(timezone.utc) - previous.astimezone(timezone.utc)
    total_seconds = elapsed.total_seconds()
    if total_seconds < 0:
        return None
    if total_seconds < 1:
        return "<1s"

    remaining_seconds = int(total_seconds)
    parts: list[str] = []
    for unit_seconds, suffix in (
        (24 * 60 * 60, "d"),
        (60 * 60, "h"),
        (60, "m"),
        (1, "s"),
    ):
        value, remaining_seconds = divmod(remaining_seconds, unit_seconds)
        if value:
            parts.append(f"{value}{suffix}")
            if len(parts) == 2:
                break
    return f"+{' '.join(parts)}"


def current_pacific_datetime() -> str:
    return format_pacific_datetime(datetime.now(PACIFIC_TIME_ZONE))


def parse_record_timestamp(record: object) -> datetime | None:
    if not isinstance(record, dict):
        return None
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return None

    if timestamp.endswith("Z"):
        timestamp = f"{timestamp[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment


def fully_qualified_hostname() -> str:
    try:
        hostname = socket.getfqdn().strip()
    except OSError:
        hostname = ""
    if hostname:
        return hostname

    try:
        hostname = socket.gethostname().strip()
    except OSError:
        hostname = ""
    return hostname or "unavailable"


def find_session_candidates(home: Path, session_id: str) -> tuple[Path, ...]:
    patterns = (
        f"*-{session_id}.jsonl",
        f"{session_id}.jsonl",
        f"*-{session_id}.jsonl.zst",
        f"{session_id}.jsonl.zst",
    )
    candidates: dict[Path, Path] = {}

    try:
        for directory_name in ("sessions", "archived_sessions"):
            root = home / directory_name
            if not root.is_dir():
                continue
            for pattern in patterns:
                for path in root.rglob(pattern):
                    if path.is_file():
                        candidates[path.resolve()] = path
    except OSError as error:
        raise TranscriptError(
            f"could not search Codex sessions under {home}: {error}"
        ) from error

    return tuple(sorted(candidates.values(), key=lambda path: os.fspath(path)))


def iter_session_lines(path: Path) -> Iterator[bytes]:
    if not path.name.endswith(".jsonl.zst"):
        try:
            with path.open("rb") as session_file:
                yield from session_file
        except OSError as error:
            raise SessionFileError(f"could not read {path}: {error}") from error
        return

    try:
        process = subprocess.Popen(
            ["zstd", "--quiet", "--decompress", "--stdout", "--", os.fspath(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as error:
        raise SessionFileError(
            f"cannot read archived session {path}: the zstd command is not installed"
        ) from error
    except OSError as error:
        raise SessionFileError(f"could not start zstd for {path}: {error}") from error

    if process.stdout is None:
        process.kill()
        process.wait()
        raise SessionFileError(f"could not open zstd output for {path}")

    return_code: int
    try:
        yield from process.stdout
    finally:
        process.stdout.close()
        return_code = process.wait()

    if return_code != 0:
        raise SessionFileError(
            f"could not decompress {path}: zstd exited with status {return_code}"
        )


def visible_phase(phase: object) -> bool:
    return phase is None or (isinstance(phase, str) and phase in VISIBLE_AGENT_PHASES)


def text_from_completed_item(item: dict[str, object]) -> str | None:
    content = item.get("content")
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        text = part.get("text")
        if (
            isinstance(part_type, str)
            and part_type.casefold() == "text"
            and isinstance(text, str)
        ):
            parts.append(text)

    if not parts:
        return None
    return "\n\n".join(parts)


def decode_visible_message(
    record: object,
    seen_completed_item_ids: set[tuple[str, str]],
    timestamp: datetime | None = None,
) -> Message | None:
    if not isinstance(record, dict) or record.get("type") != "event_msg":
        return None

    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type in {"user_message", "agent_message"}:
        if event_type == "agent_message" and not visible_phase(payload.get("phase")):
            return None
        text = payload.get("message")
        if not isinstance(text, str):
            return None
        role = "user" if event_type == "user_message" else "assistant"
        return Message(role=role, text=text, timestamp=timestamp)

    if event_type != "item_completed":
        return None

    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if not isinstance(item_type, str):
        return None
    if item_type not in {"UserMessage", "AgentMessage"}:
        return None
    if item_type == "AgentMessage" and not visible_phase(item.get("phase")):
        return None

    text = text_from_completed_item(item)
    if text is None:
        return None

    item_id = item.get("id")
    if isinstance(item_id, str):
        item_key = (item_type, item_id)
        if item_key in seen_completed_item_ids:
            return None
        seen_completed_item_ids.add(item_key)

    role = "user" if item_type == "UserMessage" else "assistant"
    return Message(role=role, text=text, timestamp=timestamp)


def read_session(path: Path, expected_session_id: str) -> SessionTranscript:
    messages: list[Message] = []
    seen_completed_item_ids: set[tuple[str, str]] = set()
    first_record: object = None
    last_activity: datetime | None = None
    malformed_lines: list[tuple[int, bool]] = []
    total_lines = 0

    for line_number, raw_line in enumerate(iter_session_lines(path), 1):
        total_lines = line_number
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            malformed_lines.append((line_number, raw_line.endswith(b"\n")))
            continue

        if line_number == 1:
            first_record = record

        record_timestamp = parse_record_timestamp(record)
        if record_timestamp is not None and (
            last_activity is None or record_timestamp > last_activity
        ):
            last_activity = record_timestamp

        message = decode_visible_message(
            record, seen_completed_item_ids, timestamp=record_timestamp
        )
        if message is not None:
            messages.append(message)

    ignored_incomplete_final_line = malformed_lines == [(total_lines, False)]
    if malformed_lines and not ignored_incomplete_final_line:
        line_number = malformed_lines[0][0]
        raise SessionFileError(f"malformed JSON in {path} at line {line_number}")

    if not isinstance(first_record, dict) or first_record.get("type") != "session_meta":
        raise SessionFileError(f"first record in {path} is not Codex session metadata")
    metadata = first_record.get("payload")
    if not isinstance(metadata, dict) or metadata.get("id") != expected_session_id:
        raise SessionFileError(
            f"session metadata in {path} does not match ID {expected_session_id}"
        )

    if not messages:
        raise SessionFileError(
            f"no user-visible messages found in {path}; the session may be incomplete or use an unsupported schema"
        )

    if last_activity is None:
        try:
            last_activity = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError as error:
            raise SessionFileError(
                f"could not read modification time for {path}: {error}"
            ) from error

    return SessionTranscript(
        path=path,
        messages=tuple(messages),
        last_activity=last_activity,
        ignored_incomplete_final_line=ignored_incomplete_final_line,
    )


def load_session(home: Path, session_id: str) -> SessionTranscript:
    candidates = find_session_candidates(home, session_id)
    if not candidates:
        raise TranscriptError(
            f"session ID {session_id} was not found under "
            f"{home / 'sessions'} or {home / 'archived_sessions'}"
        )

    valid_sessions: list[SessionTranscript] = []
    invalid_reasons: list[str] = []
    for path in candidates:
        try:
            valid_sessions.append(read_session(path, session_id))
        except SessionFileError as error:
            invalid_reasons.append(str(error))

    if len(valid_sessions) > 1:
        paths = ", ".join(os.fspath(session.path) for session in valid_sessions)
        raise TranscriptError(f"multiple files match session ID {session_id}: {paths}")
    if not valid_sessions:
        details = "; ".join(invalid_reasons)
        raise TranscriptError(
            f"no valid file found for session ID {session_id}: {details}"
        )
    return valid_sessions[0]


def render_markdown(messages: Sequence[Message], metadata: TranscriptMetadata) -> str:
    sections = [
        (
            "# Codex transcript\n"
            "\n"
            f"- Exported: `{metadata.exported_at}`\n"
            f"- Session last activity: `{metadata.session_last_activity}`\n"
            f"- Hostname: `{metadata.hostname}`\n"
            f"- Codex home (realpath): `{metadata.codex_home}`\n"
            f"- Session JSONL (realpath): `{metadata.session_file}`\n"
            f"- Session ID: `{metadata.session_id}`"
        )
    ]
    previous_message: Message | None = None
    turn_started_at: datetime | None = None
    codex_messages_in_turn = 0
    last_codex_timestamp: datetime | None = None
    latest_known_codex_timestamp: datetime | None = None
    for message in messages:
        is_user = message.role == "user"
        heading = "User" if is_user else "Codex"
        turn_elapsed: str | None = None
        if not is_user:
            last_codex_timestamp = None
            if message.timestamp is not None and turn_started_at is not None:
                turn_elapsed = format_elapsed_duration(
                    turn_started_at, message.timestamp
                )
                timestamp_regressed = (
                    latest_known_codex_timestamp is not None
                    and format_elapsed_duration(
                        latest_known_codex_timestamp, message.timestamp
                    )
                    is None
                )
                if turn_elapsed is not None and not timestamp_regressed:
                    last_codex_timestamp = message.timestamp
                    latest_known_codex_timestamp = message.timestamp

        if message.timestamp is not None:
            heading = f"{heading} — {format_message_datetime(message.timestamp)}"
            if previous_message is not None and previous_message.timestamp is not None:
                elapsed = format_elapsed_duration(
                    previous_message.timestamp, message.timestamp
                )
                if elapsed is not None:
                    previous_heading = (
                        "User" if previous_message.role == "user" else "Codex"
                    )
                    heading = f"{heading} · {elapsed} after {previous_heading}"
            if is_user:
                if turn_started_at is not None and last_codex_timestamp is not None:
                    prior_turn_elapsed = format_elapsed_duration(
                        turn_started_at, last_codex_timestamp
                    )
                    if prior_turn_elapsed is not None:
                        heading = (
                            f"{heading} · prior Codex turn: "
                            f"{prior_turn_elapsed.removeprefix('+')}"
                        )
            elif (
                codex_messages_in_turn > 0
                and turn_elapsed is not None
                and last_codex_timestamp is not None
            ):
                heading = f"{heading} · {turn_elapsed} since User"
        body = message.text.strip("\r\n")
        sections.append(f"## {heading}\n\n{body}")

        if is_user:
            turn_started_at = message.timestamp
            codex_messages_in_turn = 0
            last_codex_timestamp = None
            latest_known_codex_timestamp = None
        else:
            codex_messages_in_turn += 1
        previous_message = message
    return "\n\n".join(sections) + "\n"


def write_stdout(text: str) -> None:
    try:
        sys.stdout.write(text)
    except BrokenPipeError:
        # Avoid a second BrokenPipeError during interpreter shutdown.
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())


def main(argv: Sequence[str] | None = None, program_name: str | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    program = Path(sys.argv[0]).name if program_name is None else program_name

    if arguments in (["-h"], ["--help"]):
        sys.stdout.write(usage(program))
        return 0
    if len(arguments) != 1:
        sys.stderr.write(usage(program))
        return 2

    try:
        session_id = normalize_session_id(arguments[0])
    except TranscriptError as error:
        sys.stderr.write(f"{program}: {error}\n")
        sys.stderr.write(usage(program))
        return 2

    home = codex_home()
    try:
        transcript = load_session(home, session_id)
    except TranscriptError as error:
        sys.stderr.write(f"{program}: {error}\n")
        return 1

    if transcript.ignored_incomplete_final_line:
        sys.stderr.write(
            f"{program}: warning: ignored an incomplete final line in {transcript.path}\n"
        )
    metadata = TranscriptMetadata(
        exported_at=current_pacific_datetime(),
        session_last_activity=format_pacific_datetime(transcript.last_activity),
        hostname=fully_qualified_hostname(),
        codex_home=Path(os.path.realpath(home)),
        session_file=Path(os.path.realpath(transcript.path)),
        session_id=session_id,
    )
    write_stdout(render_markdown(transcript.messages, metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
