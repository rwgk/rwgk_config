#! /usr/bin/env python

"""Simplifies filenames to avoid special characters. Non-recursive."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys


def run(args):
  """."""
  assert args, 'dirpaths...'
  for dirpath in args:
    assert os.path.isdir(dirpath), dirpath
  num_renamed = 0
  for dirpath in args:
    for name in os.listdir(dirpath):
      new_chars = []
      for c in name:
        if not (c.isalnum() or c in '_=+-@:.'):
          c = '_'
        new_chars.append(c)
      new_name = ''.join(new_chars)
      if new_name == name:
        continue
      while True:
        new_path = os.path.join(dirpath, new_name)
        if not os.path.exists(new_path):
          break
        new_path += '_'
      old_path = os.path.join(dirpath, name)
      os.rename(old_path, new_path)
      num_renamed += 1
  print('num_renamed:', num_renamed)


if __name__ == '__main__':
  run(args=sys.argv[1:])
