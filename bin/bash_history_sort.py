#! /usr/bin/env python2

"""Sorts .bash_history by timestamp."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys


def run(args):
  """Writes output to history_file_sorted. Move manually."""
  assert len(args) == 1, 'history_file'
  print('Writing: "%s"' % args[0])
  blob = open(args[0], 'r').read()
  lines = blob.splitlines()
  history_items = []
  for i in range(0, len(lines), 2):
    j = i + 1
    assert lines[i].startswith('#'), i + 1
    assert not lines[j].startswith('#'), j + 1
    t = int(lines[i][1:])
    assert t > 1000000000  # Sanity check, a bit arbitrary.
    history_items.append((lines[i], lines[j]))
  history_items.sort()
  lines_sorted = []
  for hi in history_items:
    lines_sorted.extend(hi)
  blob_sorted = '\n'.join(lines_sorted) + '\n'
  assert len(blob_sorted) == len(blob)
  print('Writing: "history_file_sorted"')
  open('history_file_sorted', 'w').write(blob)


if __name__ == '__main__':
  run(args=sys.argv[1:])
