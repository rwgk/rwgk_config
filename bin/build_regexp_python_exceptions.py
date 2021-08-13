#! /usr/bin/env python3

"""Helpful regexp for searching in logs."""

import builtins
import sys


def run(args):
  assert not args
  error_types = []
  for attr_name in dir(builtins):
    if attr_name.endswith("Error"):
      error_types.append(attr_name)
  print("'^(%s): '" % "|".join(error_types))


if __name__ == "__main__":
  run(args=sys.argv[1:])
