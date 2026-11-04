#!/usr/bin/env python3
"""Watch a cuda-toolkit PyPI release and its declared component wheels.

Requires the packaging module. Checks CPython tags and OS/architecture presence,
not transitive dependency resolution or compatibility with a particular glibc.
No CUDA wheels are downloaded. Times are displayed in America/Los_Angeles.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

TARGETS = {
    "linux-x86_64": ("linux", "x86_64"),
    "linux-aarch64": ("linux", "aarch64"),
    "windows-amd64": ("win32", "AMD64"),
    "windows-arm64": ("win32", "ARM64"),
}
DEFAULT_TARGETS = list(TARGETS)[:3]
PACIFIC = ZoneInfo("America/Los_Angeles")


def fetch_json(project, version=None):
    suffix = f"/{version}" if version is not None else ""
    request = Request(
        f"https://pypi.org/pypi/{project}{suffix}/json",
        headers={"User-Agent": "cuda-toolkit-pypi-watcher/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise


def component_requirements(metadata, extras, targets, python_version):
    unknown = set(extras) - set(metadata["info"]["provides_extra"])
    if unknown:
        raise ValueError(f"Unknown extras: {', '.join(sorted(unknown))}")
    result = {}
    for target in targets:
        system, machine = TARGETS[target]
        environment = default_environment()
        environment.update(
            sys_platform=system,
            platform_machine=machine,
            platform_system="Linux" if system == "linux" else "Windows",
            os_name="posix" if system == "linux" else "nt",
            python_version=python_version,
            python_full_version=f"{python_version}.0",
            implementation_name="cpython",
            platform_python_implementation="CPython",
            implementation_version=f"{python_version}.0",
        )
        selected = {}
        for entry in metadata["info"].get("requires_dist") or []:
            requirement = Requirement(entry)
            if requirement.marker and not any(
                requirement.marker.evaluate({**environment, "extra": extra})
                for extra in extras
            ):
                continue
            name = canonicalize_name(requirement.name)
            selected[name] = selected.get(name, SpecifierSet()) & requirement.specifier
        if not selected:
            raise ValueError(f"No components apply to {target} with extras {extras}")
        result[target] = selected
    return result


def wheel_for(specifier, releases, target, python_version, python_tags):
    system, machine = TARGETS[target]
    versions = []
    for release in releases:
        try:
            version = Version(release)
        except InvalidVersion:
            continue
        if version in specifier:
            versions.append((version, release))
    for version, release in sorted(versions, reverse=True):
        for file in sorted(
            releases[release], key=lambda item: item["upload_time_iso_8601"]
        ):
            if file["packagetype"] != "bdist_wheel" or file["yanked"]:
                continue
            if python_version not in SpecifierSet(file.get("requires_python") or ""):
                continue
            _, _, _, tags = parse_wheel_filename(file["filename"])
            for tag in tags:
                platform_matches = tag.platform == "any"
                if system == "linux":
                    platform_matches |= tag.platform.startswith(
                        ("manylinux", "linux_")
                    ) and tag.platform.endswith(f"_{machine}")
                else:
                    platform_matches |= tag.platform == f"win_{machine.lower()}"
                if platform_matches and (tag.interpreter, tag.abi) in python_tags:
                    return {**file, "version": str(version)}
    return None


def display_time(timestamp):
    return (
        datetime.fromisoformat(timestamp)
        .astimezone(PACIFIC)
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def check(args, history):
    metadata = fetch_json("cuda-toolkit", args.version)
    if metadata is None:
        return False, f"Waiting for cuda-toolkit {args.version} on PyPI."
    requirements = component_requirements(
        metadata, args.extras, args.platforms, args.python_version
    )
    names = sorted({name for selected in requirements.values() for name in selected})
    with ThreadPoolExecutor(max_workers=8) as pool:
        projects = dict(zip(names, pool.map(fetch_json, names)))
    releases = {
        name: (data["releases"] if data else {}) for name, data in projects.items()
    }
    releases["cuda-toolkit"] = {args.version: metadata["urls"]}
    version_tuple = tuple(map(int, args.python_version.split(".")))
    interpreter = f"cp{version_tuple[0]}{version_tuple[1]}"
    python_tags = {
        (tag.interpreter, tag.abi)
        for tag in chain(
            cpython_tags(version_tuple, abis=[interpreter], platforms=["any"]),
            compatible_tags(version_tuple, interpreter=interpreter, platforms=["any"]),
        )
    }
    now = datetime.now(UTC).isoformat()
    rows = [["package", "required", "platform", "version", "uploaded", "first seen"]]
    ready = True
    for name in ["cuda-toolkit", *names]:
        for target in args.platforms:
            specifier = (
                SpecifierSet(f"=={args.version}")
                if name == "cuda-toolkit"
                else requirements[target].get(name)
            )
            if specifier is None:
                rows.append([name, "-", target, "N/A", "-", "-"])
                continue
            file = wheel_for(
                specifier, releases[name], target, args.python_version, python_tags
            )
            if file is None:
                ready = False
                rows.append([name, str(specifier), target, "MISSING", "-", "-"])
                continue
            observation = history.setdefault(
                f"{args.version}/{file['filename']}",
                {"first_seen": now, "uploaded": file["upload_time_iso_8601"]},
            )
            rows.append(
                [
                    name,
                    str(specifier),
                    target,
                    file["version"],
                    display_time(observation["uploaded"]),
                    display_time(observation["first_seen"]),
                ]
            )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    table = "\n".join(
        "  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
        for row in rows
    )
    status = (
        "All declared component wheels are available."
        if ready
        else "Waiting for missing wheels."
    )
    return ready, f"{table}\n{status}"


def save_history(path, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(history, stream, indent=2, sort_keys=True)
        stream.write("\n")
    Path(stream.name).replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "version", type=Version, help="Exact toolkit release, e.g. 13.5.0"
    )
    parser.add_argument(
        "--extras", default="all", help="Comma-separated toolkit extras (default: all)"
    )
    parser.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        choices=TARGETS,
        help="Repeat to select platforms (default: Linux x86_64/aarch64, Windows AMD64)",
    )
    parser.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="CPython major.minor (default: running Python)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60,
        help="Seconds between checks (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once: exit 0 ready, 1 waiting, 2 error",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="History JSON (default: $XDG_STATE_HOME/cuda-toolkit-pypi/VERSION.json)",
    )
    args = parser.parse_args(argv)
    args.version = str(args.version)
    args.extras = [
        canonicalize_name(extra.strip())
        for extra in args.extras.split(",")
        if extra.strip()
    ]
    args.platforms = args.platforms or DEFAULT_TARGETS
    try:
        major, minor = map(int, args.python_version.split("."))
        if major != 3 or minor < 0:
            raise ValueError
    except ValueError:
        parser.error("--python-version must be a CPython 3 major.minor version")
    if args.interval <= 0 or not args.extras:
        parser.error("--interval must be positive and --extras must not be empty")
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    args.state = args.state or state_home / "cuda-toolkit-pypi" / f"{args.version}.json"
    history = json.loads(args.state.read_text()) if args.state.exists() else {}
    previous = None
    while True:
        try:
            ready, output = check(args, history)
        except (URLError, TimeoutError) as error:
            print(f"PyPI request failed: {error}", file=sys.stderr, flush=True)
            if args.once:
                return 2
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
        else:
            save_history(args.state, history)
            if output != previous:
                print(
                    f"\n{display_time(datetime.now(UTC).isoformat())}\n{output}",
                    flush=True,
                )
                previous = output
            if ready:
                return 0
            if args.once:
                return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
