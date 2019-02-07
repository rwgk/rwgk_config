#! /usr/bin/env python

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys


def AllConvertibleToType(value_type, value_strings):
  for value_string in value_strings:
    try:
      value_type(value_string)
    except ValueError:
      return False
  return True


def Run(args):
  assert len(args) == 3, 'csv_filepath ix iy'
  csv_filepath, ix, iy = args
  ix = int(ix)
  iy = int(iy)

  have_title = False
  for line in open(csv_filepath).read().splitlines():
    flds = line.split(',')
    xy = [flds[i] for i in (ix, iy)]
    if not AllConvertibleToType(float, xy):
      if not have_title:
        print('@ title "' + ', '.join(xy) + '"')
        have_title = True
      else:
        print('# ' + ' '.join(xy))
    else:
      print(' '.join(xy))
  sys.stdout.flush()


if __name__ == '__main__':
  Run(args=sys.argv[1:])
