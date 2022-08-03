#! /usr/bin/env python3

"""."""

import sys


def extract_from(begin_tag, end_tag, line_iter):
  for line_outer in line_iter:
    if line_outer == begin_tag:
      for line_inner in line_iter:
        if line_inner == end_tag:
          break
        print(line_inner)


def run(args):
  """."""
  assert len(args) >= 2, "BEGIN_TAG END_TAG [file...]"
  begin_tag = args[0]
  end_tag = args[1]
  if len(args) == 2:
    extract_from(begin_tag, end_tag, iter(sys.stdin.read().splitlines()))
  else:
    for filename in args[2:]:
      extract_from(begin_tag, end_tag, iter(open(filename).read().splitlines()))


if __name__ == "__main__":
  run(args=sys.argv[1:])
