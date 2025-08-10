#!/usr/bin/env python3
"""
bash_history_cleanup.py FILE [FILE2 ...]
- Strict parser for Bash history with timestamps (# <epoch>).
- On any malformed input (e.g., command without timestamp, empty command), prints a terse error and exits non-zero.
- Otherwise: merges all files, sorts by timestamp, removes exact duplicates, and writes to stdout.

Duplicates are removed only when the entire entry (timestamp line + command text) is identical.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple


def err(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def is_ts_line(s: str) -> bool:
    # Timestamp line must be: "#" + spaces + digits + optional spaces + newline
    if not s.startswith("#"):
        return False
    i = 1
    n = len(s)
    # skip spaces
    while i < n and s[i].isspace():
        i += 1
    # must have at least one digit
    j = i
    while i < n and s[i].isdigit():
        i += 1
    if i == j:
        return False
    # only trailing whitespace allowed
    while i < n and s[i].isspace():
        i += 1
    return i == n


def parse_epoch(ts_line: str, file: Path, lineno: int) -> int:
    # Extract integer epoch from "#  1234567890" style line
    i = 1
    n = len(ts_line)
    while i < n and ts_line[i].isspace():
        i += 1
    j = i
    while i < n and ts_line[i].isdigit():
        i += 1
    num = ts_line[j:i]
    try:
        return int(num)
    except ValueError:
        err(f"malformed timestamp in {file} at line {lineno}")


class Entry:
    __slots__ = ("ts", "ts_line", "cmd", "order")

    def __init__(self, ts: int, ts_line: str, cmd: str, order: int):
        self.ts = ts
        self.ts_line = ts_line
        self.cmd = cmd
        self.order = order

    def key(self) -> str:
        # exact-text key for dedupe
        return self.ts_line + self.cmd


def parse_file(path: Path) -> List[Tuple[Entry, Path]]:
    try:
        data = path.read_text(errors="replace")
    except Exception as e:
        err(f"cannot read {path}: {e}")

    lines = data.splitlines(keepends=True)
    entries: List[Tuple[Entry, Path]] = []
    i = 0
    order_counter = 0

    while i < len(lines):
        line = lines[i]
        lineno = i + 1

        if not is_ts_line(line):
            # First non-timestamped thing is an error (you asked to fix manually)
            err(f"non-timestamped line in {path} at line {lineno}")

        # Timestamp OK; gather command lines until next timestamp or EOF
        ts_line = line
        ts = parse_epoch(ts_line, path, lineno)
        i += 1
        cmd_lines = []
        while i < len(lines) and not is_ts_line(lines[i]):
            cmd_lines.append(lines[i])
            i += 1

        if not cmd_lines:
            err(f"empty command for timestamp in {path} at line {lineno}")

        cmd = "".join(cmd_lines)
        # Ensure command ends with a newline in output (bash history entries do)
        if not cmd.endswith("\n"):
            cmd += "\n"

        entries.append((Entry(ts, ts_line, cmd, order_counter), path))
        order_counter += 1

    return entries


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Merge Bash history files (timestamped), sort by time, dedupe exact entries, print to stdout."
    )
    ap.add_argument("files", nargs="+", help="history files to merge")
    args = ap.parse_args(argv)

    all_entries: List[Entry] = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            err(f"no such file: {p}")
        parsed = parse_file(p)
        all_entries.extend(e for (e, _) in parsed)

    # Sort by timestamp; stable by original order
    all_entries.sort(key=lambda e: (e.ts, e.order))

    # Dedupe exact duplicates (same ts_line + cmd)
    seen = set()
    out_entries: List[Entry] = []
    for e in all_entries:
        k = e.key()
        if k in seen:
            continue
        seen.add(k)
        out_entries.append(e)

    # Write to stdout
    out = sys.stdout
    for e in out_entries:
        out.write(e.ts_line)
        out.write(e.cmd)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
