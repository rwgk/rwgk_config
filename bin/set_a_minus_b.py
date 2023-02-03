#! /usr/bin/python3

import sys


def line_set(file_obj):
  return set(file_obj.read().splitlines())


def run(args):
  assert len(args) == 2, "file_a file_b"
  a, b = [line_set(open(filename)) for filename in args]
  d = a - b
  for line in sorted(d):
    print(line)


if __name__ == "__main__":
  run(args=sys.argv[1:])
