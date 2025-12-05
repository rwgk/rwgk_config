#!/usr/bin/env python3

import os
import re
import sys
from collections import defaultdict

pattern = re.compile(r"^(\d+)_(.*\.txt)$")


def run(args):
    assert len(args) == 0

    all_files = os.listdir(".")

    renames = []
    collision_map = defaultdict(list)

    for filename in all_files:
        match = pattern.match(filename)
        if match:
            new_name = f"{match.group(2)}"
            renames.append((filename, new_name))
            collision_map[new_name].append(filename)

    # Find duplicates based on proposed new names
    duplicates = {new for new, olds in collision_map.items() if len(olds) > 1}

    if duplicates:
        print("Filename collision detected!")
        for new in sorted(duplicates):
            print(new)
            for old in sorted(collision_map[new]):
                print(f"    {old}")
        sys.exit(1)

    # Check for collisions with existing files
    for _, new in renames:
        if os.path.exists(new):
            print(f"Collision with existing file: {new}")
            sys.exit(1)

    # Perform the renaming
    for old, new in renames:
        print(f"Renaming: {old} -> {new}")
        os.rename(old, new)


if __name__ == "__main__":
    run(args=sys.argv[1:])
