#! /usr/bin/python3

"""."""

import sys

EXAMPLE_USAGE = ("Example usage:"
                 " git diff master | diffs_matching.py 'some string' ...")


def run(args):
  search_strings = args
  assert search_strings, EXAMPLE_USAGE
  current_diff = None
  for line in sys.stdin.read().splitlines():
    if line.startswith("diff "):
      current_diff = line
      continue
    for ss in search_strings:
      if ss in line:
        print(current_diff)
        break


if __name__ == "__main__":
  run(args=sys.argv[1:])
