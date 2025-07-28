#!/usr/bin/env python3
"""
Extract linear snapshots of a file from the current git branch.

This script creates snapshots of a file at each point where it changed on the
current branch, using the commit date when the change became part of the current
branch (not the original author date).

Usage:
    extract_linear_snapshots_of_file_in_git_repo.py <file_path>
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime
from pathlib import Path


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


def run_git_command(cmd, check=True):
    """Run a git command and return the output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=check
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            print(f"Git command failed: {cmd}")
            print(f"Error: {e.stderr}")
            sys.exit(1)
        return None


def get_current_branch():
    """Get the name of the current branch."""
    return run_git_command("git rev-parse --abbrev-ref HEAD")


def get_commits_touching_file(file_path, branch):
    """Get all commits that modified the file on the current branch."""
    # Use --first-parent to follow only the main branch history
    # This ignores commits that were merged in from other branches
    cmd = f"git log --first-parent --oneline --follow -- {file_path}"
    output = run_git_command(cmd)

    if not output:
        return []

    commits = []
    for line in output.split("\n"):
        if line.strip():
            commit_hash = line.split()[0]
            commits.append(commit_hash)

    # Reverse to get chronological order (oldest first)
    return list(reversed(commits))


def get_commit_info(commit_hash):
    """Get commit information including committer date."""
    # Use committer date, not author date, as it represents when the change
    # actually became part of the current branch
    cmd = f"git show --format='%H|%ci|%s' --no-patch {commit_hash}"
    output = run_git_command(cmd)

    if not output:
        return None

    parts = output.split("|", 2)
    if len(parts) >= 2:
        hash_full = parts[0]
        commit_date = parts[1]
        subject = parts[2] if len(parts) > 2 else ""
        return {
            "hash": commit_hash,
            "hash_full": hash_full,
            "date": commit_date,
            "subject": subject,
        }
    return None


def get_file_content_at_commit(file_path, commit_hash):
    """Get the content of a file at a specific commit."""
    cmd = f"git show {commit_hash}:{file_path}"
    return run_git_command(cmd, check=False)


def format_timestamp(commit_date):
    """Convert commit date to UTC snapshot filename format."""
    # Parse the commit date (format: 2022-07-02 12:30:45 +0000 or +0200)
    try:
        # Split date and timezone
        if "+" in commit_date:
            date_part, tz_part = commit_date.rsplit(" +", 1)
            tz_sign = 1
        elif commit_date.count(" -") >= 2:  # Has timezone with minus
            date_part, tz_part = commit_date.rsplit(" -", 1)
            tz_sign = -1
        else:
            # No timezone info, assume UTC
            date_part = commit_date.split(" +")[0].split(" -")[0]
            tz_part = "0000"
            tz_sign = 1

        # Parse the datetime
        dt = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")

        # Parse timezone offset (format: HHMM)
        if len(tz_part) == 4 and tz_part.isdigit():
            tz_hours = int(tz_part[:2])
            tz_minutes = int(tz_part[2:])

            # Convert to UTC by subtracting the timezone offset
            from datetime import timedelta

            tz_offset = timedelta(hours=tz_hours, minutes=tz_minutes) * tz_sign
            dt_utc = dt - tz_offset
        else:
            # Invalid timezone format, assume UTC
            dt_utc = dt

        return dt_utc.strftime("%Y-%m-%d+%H%M%S") + "_UTC"

    except (ValueError, IndexError):
        # Fallback format
        return commit_date.replace(" ", "_").replace(":", "") + "_UNKNOWN_TZ"


def create_snapshot_directory():
    """Create the ALL_SNAPSHOTS directory."""
    snapshot_dir = Path("ALL_SNAPSHOTS")
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir()
    return snapshot_dir


def has_file_changed(file_path, commit1, commit2):
    """Check if the file content changed between two commits."""
    content1 = get_file_content_at_commit(file_path, commit1)
    content2 = get_file_content_at_commit(file_path, commit2)
    return content1 != content2


def main():
    if len(sys.argv) != 2:
        print("Usage: extract_linear_snapshots_of_file_in_git_repo.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    # Check if we're in a git repository
    if not current_working_directory_is_top_level_in_git_repo():
        print("Error: Please cd to top-level directory in this repo.")
        sys.exit(1)

    # Check if file exists
    if not Path(file_path).exists():
        print(f"Error: File {file_path} does not exist")
        sys.exit(1)

    print(f"Extracting snapshots for: {file_path}")

    current_branch = get_current_branch()
    print(f"Current branch: {current_branch}")

    # Get all commits that touched the file
    commits = get_commits_touching_file(file_path, current_branch)

    if not commits:
        print("No commits found that modify this file")
        sys.exit(1)

    print(f"Found {len(commits)} commits that modified the file")

    # Create snapshot directory
    snapshot_dir = create_snapshot_directory()

    # Track actual changes (skip commits where file content didn't change)
    snapshot_count = 0
    previous_commit = None

    # Always create a snapshot for the first commit
    first_commit = commits[0]
    commit_info = get_commit_info(first_commit)
    if commit_info:
        content = get_file_content_at_commit(file_path, first_commit)
        if content is not None:
            timestamp = format_timestamp(commit_info["date"])
            filename = f"SNAPSHOT_{timestamp}_{first_commit}"
            snapshot_path = snapshot_dir / filename

            with open(snapshot_path, "w") as f:
                f.write(content)

            snapshot_count += 1
            print(f"Created snapshot {snapshot_count}: {filename}")
            print(f"  Commit: {commit_info['subject']}")
            previous_commit = first_commit

    # Process remaining commits, only creating snapshots when content actually changes
    for commit in commits[1:]:
        if previous_commit and has_file_changed(file_path, previous_commit, commit):
            commit_info = get_commit_info(commit)
            if commit_info:
                content = get_file_content_at_commit(file_path, commit)
                if content is not None:
                    timestamp = format_timestamp(commit_info["date"])
                    filename = f"SNAPSHOT_{timestamp}_{commit}"
                    snapshot_path = snapshot_dir / filename

                    with open(snapshot_path, "w") as f:
                        f.write(content)

                    snapshot_count += 1
                    print(f"Created snapshot {snapshot_count}: {filename}")
                    print(f"  Commit: {commit_info['subject']}")

        previous_commit = commit

    print(f"\nCompleted! Created {snapshot_count} snapshots in {snapshot_dir}")
    print(f"\nTo inspect changes between snapshots:")
    print(f"  git show <hash>        # For single commits")
    print(f"  git diff <hash1> <hash2> -- {file_path}  # Compare specific commits")

    # Create a summary file
    summary_path = snapshot_dir / "README.md"
    with open(summary_path, "w") as f:
        f.write(f"# Snapshots for {file_path}\n\n")
        f.write(f"Branch: {current_branch}\n")
        f.write(f"Total snapshots: {snapshot_count}\n\n")
        f.write("## How to inspect changes\n\n")
        f.write(
            "Each snapshot filename contains the commit hash. To see what changed:\n\n"
        )
        f.write("```bash\n")
        f.write("# View the commit that created a snapshot\n")
        f.write("git show <hash>\n\n")
        f.write("# Compare two specific commits\n")
        f.write(f"git diff <hash1> <hash2> -- {file_path}\n")
        f.write("```\n\n")
        f.write("## Notes\n\n")
        f.write("- Snapshots use committer date (when change landed on branch)\n")
        f.write("- Only commits that actually changed file content are included\n")
        f.write("- Uses --first-parent to follow main branch history only\n")


if __name__ == "__main__":
    main()
