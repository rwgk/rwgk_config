#! /usr/bin/env python2

from __future__ import print_function

import sys


def Run(args):
  assert len(args) == 1, 'num_lines'
  num_lines = int(args[0])
  lines = sys.stdin.read().splitlines()
  for i in xrange(0, len(lines), num_lines):
    print(' '.join(lines[i:i + num_lines]))


if __name__ == '__main__':
  Run(args=sys.argv[1:])
