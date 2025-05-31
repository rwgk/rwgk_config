#!/usr/bin/env python3

import os
import sys
import shutil
from datetime import datetime

MY_HOSTS_ENV = "MY_HOSTS"
HOSTS_PATH = "/etc/hosts"
BEGIN_MARK = "# BEGIN MY_HOSTS"
END_MARK = "# END MY_HOSTS"


def error(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def backup_hosts_file(now):
    try:
        stat = os.stat(HOSTS_PATH)
        mtime = int(stat.st_mtime)
        backup_name = f"/tmp/etc_hosts_mtime{mtime}_@{now}"
        shutil.copy2(HOSTS_PATH, backup_name)
        print(f"Backup created: {backup_name}")
    except Exception as e:
        error(f"Failed to create backup: {e}")


def generate_new_hosts(existing_lines, my_hosts_lines):
    # Remove existing MY_HOSTS block
    in_block = False
    new_lines = []
    for line in existing_lines:
        if line.strip() == BEGIN_MARK:
            in_block = True
            continue
        if line.strip() == END_MARK:
            in_block = False
            continue
        if not in_block:
            new_lines.append(line.rstrip())

    # Append new block
    new_lines.append(BEGIN_MARK)
    new_lines.extend(line.rstrip() for line in my_hosts_lines)
    new_lines.append(END_MARK)

    return "\n".join(new_lines) + "\n"


def main():
    now = datetime.now().strftime("%Y%m%d+%H%M%S")
    tmp_path = f"/tmp/my_hosts_to_etc_@{now}"

    my_hosts_path = os.environ.get(MY_HOSTS_ENV)
    if not my_hosts_path:
        error(f"${MY_HOSTS_ENV} is not defined")

    if not os.path.isfile(my_hosts_path):
        error(f"file does not exist: {my_hosts_path}")

    with open(my_hosts_path, "r", encoding="utf-8") as f:
        my_hosts_lines = f.read().strip().splitlines()

    with open(HOSTS_PATH, "r", encoding="utf-8") as f:
        existing_lines = f.readlines()

    final_content = generate_new_hosts(existing_lines, my_hosts_lines)

    with open(HOSTS_PATH, "r", encoding="utf-8") as f:
        current_content = f.read()

    if final_content == current_content:
        print("No changes needed. /etc/hosts is already up to date.")
        return

    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(final_content)
    print(f"Prepared new hosts file at: {tmp_path}")

    backup_hosts_file(now)

    print(f"Updating {HOSTS_PATH} with sudo...")
    os.system(f"sudo cp {tmp_path} {HOSTS_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
