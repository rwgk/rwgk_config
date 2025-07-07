#! /usr/bin/env python3

import os
import subprocess
import sys
from datetime import datetime, timezone


def current_working_directory_is_top_level_in_git_repo():
    """Check if we're in a git repository (handles both normal repos and worktrees)"""
    git_path = ".git"

    if os.path.isdir(git_path):
        return True  # Normal git repository

    if os.path.isfile(git_path):
        # Potentially a git worktree
        try:
            with open(git_path, "r") as f:
                first_line = f.readline().strip()
                return first_line.startswith("gitdir: ")
        except (IOError, OSError):
            pass

    return False


def get_all_revisions(repo_path, file_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    # Get commit history for the file
    log_output = subprocess.check_output(
        ["git", "log", "--pretty=format:%H %h %aD", "--", file_path],
        cwd=repo_path,
        text=True,
    )

    for line in log_output.splitlines():
        commit_hash, short_hash, *date_parts = line.split()
        date_str = " ".join(date_parts)

        # Convert date format to UTC
        dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z").astimezone(
            timezone.utc
        )
        timestamp = dt.strftime("%Y-%m-%d+%H%M%S")

        # Checkout file content from that commit
        file_output_path = os.path.join(output_dir, f"VERSION_{timestamp}_{short_hash}")

        with open(file_output_path, "wb") as f:
            file_content = subprocess.check_output(
                ["git", "show", f"{commit_hash}:{file_path}"], cwd=repo_path
            )
            f.write(file_content)

        print(f"Saved: {file_output_path}")


def run(args):
    assert len(args) == 1, "file_path"

    if not current_working_directory_is_top_level_in_git_repo():
        raise RuntimeError("Please cd to top-level directory in this repo.")

    file_path = sys.argv[1]
    if not os.path.isfile(file_path):
        raise RuntimeError(f'No such file: {file_path}"')

    repo_path = "."
    output_dir = "./ALL_VERSIONS"
    get_all_revisions(repo_path, file_path, output_dir)


if __name__ == "__main__":
    run(args=sys.argv[1:])
