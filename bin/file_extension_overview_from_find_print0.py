#! /usr/bin/env python2

import collections
import os
import sys


def run(args):
  assert not args
  ext_counts = collections.defaultdict(int)
  for line in sys.stdin.read().split('\0'):
    unused_root, ext = os.path.splitext(line)
    ext_counts[ext] += 1
  for count, ext in reversed(sorted([(count, ext) for ext, count in ext_counts.items()])):
    if not ext:
      ext = None
    print '%8d %s' % (count, ext)


if __name__ == '__main__':
  run(args=sys.argv[1:])
