#!/usr/bin/env python3

import argparse
import re
import sys
from pathlib import Path
from urllib.request import urlopen

REDIST_URL = "https://developer.download.nvidia.com/compute/cuda/redist/"


def read_source(input_file: str | None) -> str:
    # priority: stdin → file argument → fetch live
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if input_file:
        return Path(input_file).read_text(encoding="utf-8", errors="replace")
    with urlopen(REDIST_URL) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_versions(html: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"redistrib_([0-9.]+)\.json.*?<span class='date'>([^<]+)</span>",
        re.DOTALL,
    )
    return pattern.findall(html)


def version_key(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ver.split("."))


def extract_most_recent_minors_only(
    results: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    latest: dict[str, tuple[str, str]] = {}
    for ver, date in results:
        parts = ver.split(".")
        key = ".".join(parts[:2])  # major.minor
        if key not in latest or version_key(ver) > version_key(latest[key][0]):
            latest[key] = (ver, date)
    # preserve original order of first appearance
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ver, date in results:
        key = ".".join(ver.split(".")[:2])
        if key not in seen:
            seen.add(key)
            ordered.append(latest[key])
    return ordered


def main():
    parser = argparse.ArgumentParser(
        description="List available CUDA redistrib versions from NVIDIA redist index."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="Optional path to a saved HTML file instead of fetching live",
    )
    parser.add_argument(
        "--most-recent-minors-only",
        action="store_true",
        help="Show only the most recent patch for each major.minor version",
    )

    args = parser.parse_args()

    html = read_source(args.input_file)
    results = parse_versions(html)

    if args.most_recent_minors_only:
        results = extract_most_recent_minors_only(results)

    for version, date in results:
        print(f"{version} — {date}")


if __name__ == "__main__":
    main()
