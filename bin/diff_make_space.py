#! /usr/bin/python3

"""."""

# Example usage: git diff master | diff_make_space.py

import sys


def run(args):
  assert not args
  just_starting = True
  for line in sys.stdin.read().splitlines():
    if not just_starting and line.startswith("diff "):
      print()
      print("=#" * 39 + "=")
      print()
    print(line)
    just_starting = False


if __name__ == "__main__":
  run(args=sys.argv[1:])
