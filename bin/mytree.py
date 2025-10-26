#!/usr/bin/env python3
import os
import argparse
import fnmatch
from datetime import datetime

DEFAULT_PRUNE_PATTERNS = {
    ".git",
    "__pycache__",
    ".DS_Store",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
}


def format_size(num_bytes):
    formatted = f"{num_bytes:,} bytes"
    if num_bytes >= 10_000_000:
        units = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]
        size = float(num_bytes)
        for unit in units:
            if size < 1000:
                break
            size /= 1000
        formatted += f" ≈ {size:.3f} {unit}"
    return f"({formatted})"


def format_mtime(path):
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "??????????????"


def should_prune(name, prune_patterns):
    for pattern in prune_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def list_entries_sorted(root: str, prune_patterns):
    """Return entries in 'root', pruned and sorted with directories first."""
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    # prune
    entries = [e for e in entries if not should_prune(e, prune_patterns)]
    # sort: dirs first, then alphabetical (case-insensitive)
    entries.sort(key=lambda e: (not os.path.isdir(os.path.join(root, e)), e.lower()))
    return entries


def get_size_and_format(path, stats):
    try:
        size = os.path.getsize(path)
    except OSError:
        return "(os.path.getsize() → OSError)"
    stats["bytes"] += size
    return f"{format_size(size)}"


def print_tree(root, prefix="", prune_patterns=None, stats=None):
    """Recursive directory tree printer with annotations."""

    if prune_patterns is None:
        prune_patterns = set()

    entries = list_entries_sorted(root, prune_patterns)
    if entries is None:
        print(f"{prefix}[os.listdir() → OSError]")
        return

    for index, name in enumerate(entries):
        path = os.path.join(root, name)
        is_last = index == len(entries) - 1
        branch = "└── " if is_last else "├── "
        continuation = "    " if is_last else "│   "

        if os.path.isdir(path):
            print(f"{prefix}{branch}{name}/  [{format_mtime(path)}]")
            stats["dirs"] += 1
            print_tree(path, prefix + continuation, prune_patterns, stats)
        else:
            print(
                f"{prefix}{branch}{name}  [{format_mtime(path)}]  {get_size_and_format(path, stats)}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="tree-like command with mtime and size annotations"
    )
    parser.add_argument("paths", nargs="+", help="Root paths to show")
    parser.add_argument(
        "--prune",
        action="append",
        default=[],
        help="Glob pattern(s) for names to skip (e.g., '*Venv', '__pycache__')",
    )
    args = parser.parse_args()

    prune_patterns = set(args.prune) | DEFAULT_PRUNE_PATTERNS

    for path in args.paths:
        if not os.path.exists(path):
            print(path)
            print("    [Not found]")
            print()
            continue

        stats = {"dirs": 0, "bytes": 0}

        if os.path.isfile(path):
            # Print a single file "tree line"
            print(f"{path}  [{format_mtime(path)}]  {get_size_and_format(path, stats)}")
        else:
            print(path)
            print_tree(path, prune_patterns=prune_patterns, stats=stats)

        print(f"    {'_' * 60}")
        print(f"    Number of subdirs: {stats['dirs']}")
        print(f"    Sum of file sizes: {format_size(stats['bytes'])}")
        print()


if __name__ == "__main__":
    main()
