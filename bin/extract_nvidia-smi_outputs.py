#!/usr/bin/env python3

import re
import sys
from pathlib import Path

# Regex to strip GitHub Actions timestamp prefix like:
# 2025-11-13T17:17:32.4424667Z <rest of line>
TIMESTAMP_RE = re.compile(
    r"""
    ^\d{4}-\d{2}-\d{2}T      # date 'YYYY-MM-DDT'
    [0-9:.]+Z\s*             # time + 'Z' + optional spaces
    (.*)$                    # rest of the line
    """,
    re.VERBOSE,
)


def strip_timestamp(line: str) -> str:
    """Remove leading GitHub Actions timestamp, if present."""
    m = TIMESTAMP_RE.match(line.rstrip("\n"))
    if m:
        return m.group(1)
    return line.rstrip("\n")


def extract_blocks_from_lines(lines):
    """
    Return a list of nvidia-smi blocks.
    Each block is a list of strings (lines) with timestamps stripped.
    """
    blocks = []
    n = len(lines)

    i = 0
    while i < n:
        content = strip_timestamp(lines[i])

        # Detect the NVIDIA-SMI header line
        if "NVIDIA-SMI" in content and content.lstrip().startswith("| NVIDIA-SMI"):
            # Start of a block: include the previous line if it exists (top border)
            start_idx = max(i - 1, 0)
            block = []
            processes_seen = False

            j = start_idx
            while j < n:
                c = strip_timestamp(lines[j])
                block.append(c)

                if "Processes:" in c:
                    processes_seen = True

                # Border line: +----...----+
                if processes_seen and re.match(r"^\+[-+]+\+$", c):
                    # Treat this as the end of the block
                    j += 1
                    break

                j += 1

            blocks.append(block)
            i = j
        else:
            i += 1

    return blocks


def process_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"# WARNING: could not read {path}: {e}", file=sys.stderr)
        return

    lines = text.splitlines(keepends=True)
    blocks = extract_blocks_from_lines(lines)

    for idx, block in enumerate(blocks, start=1):
        print(f"# File: {path} — nvidia-smi block {idx}")
        for line in block:
            print(line)
        print()  # blank line between blocks


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print(
            "Usage: extract_nvidia-smi_outputs.py LOGFILE [LOGFILE ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    for arg in argv:
        for path in (
            sorted(Path().glob(arg)) if any(c in arg for c in "*?[]") else [Path(arg)]
        ):
            if path.is_file():
                process_file(path)
            else:
                print(f"# WARNING: {path} is not a file", file=sys.stderr)


if __name__ == "__main__":
    main()
