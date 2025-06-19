#!/usr/bin/env python3
import sys
import argparse
import hashlib
from collections import defaultdict


def normalized_hash(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # Normalize content: strip lines, ignore blank lines
            lines = [line.strip() for line in f if line.strip()]
            norm = "\n".join(lines).encode("utf-8")
            return hashlib.sha256(norm).hexdigest()
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return None


def group_files(paths, unique_only=False):
    hash_to_paths = defaultdict(list)

    for path in paths:
        h = normalized_hash(path)
        if h is not None:
            hash_to_paths[h].append(path)

    for group in hash_to_paths.values():
        if unique_only:
            print(group[0])  # Only first file in group
        else:
            print(" ".join(group))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-u",
        "--unique_only",
        action="store_true",
        help="show only the first file from each group",
    )
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()

    group_files(args.files, unique_only=args.unique_only)


if __name__ == "__main__":
    main()
