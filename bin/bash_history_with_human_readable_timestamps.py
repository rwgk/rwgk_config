#!/usr/bin/env python3

import os
import sys
import re
from datetime import datetime


TIMESTAMP_PATTERN = re.compile(r"^#(\d{10,})$")


def convert_timestamps(history_filename):
    with open(history_filename, "r") as infile:
        for line in infile:
            match = TIMESTAMP_PATTERN.match(line.rstrip())
            if match:
                epoch = int(match.group(1))
                dt = datetime.fromtimestamp(epoch).astimezone()
                formatted = dt.strftime("%Y-%m-%d %H:%M:%S %Z%z")
                line = f"# {formatted} {epoch}\n"
            sys.stdout.write(line)


def run(args):
    if not args:
        args = [os.path.expandvars("$HOME/.bash_history")]
    for history_filename in args:
        convert_timestamps(history_filename)


if __name__ == "__main__":
    run(args=sys.argv[1:])
