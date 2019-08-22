#! /usr/bin/env python

"""Diff many copies of the same file.

Example usage:

find . -name code.py -print0 | diff_many_copies.py
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import difflib
import os
import sys


def run(args):
  """Main."""

  assert not args

  filepath_list = sys.stdin.read().split('\0')

  mtime_filepath_list = []
  for filepath in filepath_list:
    if not filepath:
      continue
    mtime_filepath_list.append((os.path.getmtime(filepath), filepath))
  mtime_filepath_list.sort()
  mtime_filepath_list.reverse()

  identical_files = collections.OrderedDict()
  for unused_mtime, filepath in mtime_filepath_list:
    blob = open(filepath, 'rb').read()
    identical_files.setdefault(blob, []).append(filepath)

  identical_files_iter = iter(identical_files)
  reference_blob = next(identical_files_iter)
  reference_filepaths = identical_files[reference_blob]
  print('Reference file(s): %s' % str(reference_filepaths))
  print()
  for other_blob in identical_files_iter:
    other_filepaths = identical_files[other_blob]
    print('Other file(s): %s' % str(other_filepaths))
    diff = difflib.unified_diff(
        reference_blob.splitlines(1),
        other_blob.splitlines(1),
        fromfile='reference',
        tofile='other')
    print(''.join(diff))
    print()


if __name__ == '__main__':
  run(args=sys.argv[1:])
