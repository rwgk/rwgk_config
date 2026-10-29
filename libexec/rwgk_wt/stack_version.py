"""Minimum gh-stack release requirement shared by stack-writing helpers."""

from __future__ import annotations

import re

from .core import WTError

MINIMUM_VERSION = (0, 3, 0)
REMEDY = (
    "Install GitHub CLI if 'gh' is missing, then install the extension with "
    "'gh extension install github/gh-stack' or upgrade it with "
    "'gh extension upgrade stack'. Verify with 'gh stack --version'."
)


def require_supported(run):
    """Read the executable version without a persistent cache."""
    try:
        result = run(["gh", "stack", "--version"], check=False)
    except (OSError, WTError) as error:
        raise WTError(f"gh-stack >=0.3.0 is required. {REMEDY}") from error
    if result.returncode:
        raise WTError(
            f"Cannot read the gh-stack version; >=0.3.0 is required. {REMEDY}"
        )
    output = result.stdout.strip()
    match = re.fullmatch(
        r"gh stack version v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        output,
    )
    if not match:
        raise WTError(
            f"Unrecognized gh-stack version output; >=0.3.0 is required. {REMEDY}"
        )
    version = tuple(int(part) for part in match.groups()[:3])
    prerelease = match[4]
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part[0] == "0"
        for part in prerelease.split(".")
    ):
        raise WTError(
            f"Malformed gh-stack prerelease version; >=0.3.0 is required. {REMEDY}"
        )
    if version < MINIMUM_VERSION or version == MINIMUM_VERSION and prerelease:
        raise WTError(
            f"{output.removeprefix('gh stack version ')} is too old; gh-stack >=0.3.0 is required. {REMEDY}"
        )
    return version
