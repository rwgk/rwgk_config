#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# Originally developed under https://github.com/pybind/pybind11/pull/3106
# Mostly intended for deflaking, but may also be useful for other purposes.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import subprocess
import sys


def run(args):
    num_repeats = int(args[0])
    cmd_and_args = args[1:]
    assert cmd_and_args
    if num_repeats < 0:
        # Shortcut to turn this script into a completely transparent no-op.
        return subprocess.call(cmd_and_args)
    if not num_repeats:
        # Can be used as a simple trick to skip the command entirely.
        print("REPEAT_COMMAND:SKIP", cmd_and_args)
        print()
        sys.stdout.flush()
        return
    print("REPEAT_COMMAND:CMD_AND_ARGS", cmd_and_args)
    print()
    retcode_counts = collections.defaultdict(int)
    first_non_zero_retcode = 0
    for ix in range(num_repeats):
        print("REPEAT_COMMAND:CALL", ix + 1)
        sys.stdout.flush()
        retcode = subprocess.call(cmd_and_args)
        print("REPEAT_COMMAND:RETCODE", retcode)
        print()
        retcode_counts[retcode] += 1
        if retcode and not first_non_zero_retcode:
            first_non_zero_retcode = retcode
    print("REPEAT_COMMAND:RETCODE_COUNTS", list(sorted(retcode_counts.items())))
    print("REPEAT_COMMAND:FIRST_NON_ZERO_RETCODE", first_non_zero_retcode)
    print()
    sys.stdout.flush()
    return first_non_zero_retcode


if __name__ == "__main__":
    sys.exit(run(args=sys.argv[1:]))
