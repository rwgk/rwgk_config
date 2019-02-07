#! /usr/bin/env python

"""Extracts Grace x,y set data from CSV file.

http://plasma-gate.weizmann.ac.il/Grace/

sudo apt install grace
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys


# Based on: https://michaelgoerz.net/notes/colors-for-xmgrace.html
# The only change is that blue and green are swapped.
CUSTOM_COLOR_MAP = """\
@map color 0 to (255, 255, 255), "white"
@map color 1 to (0, 0, 0), "black"
@map color 2 to (228, 26, 28), "red"
@map color 3 to (77, 175, 74), "green"
@map color 4 to (55, 126, 184), "blue"
@map color 5 to (152, 78, 163), "purple"
@map color 6 to (255, 127, 0), "orange"
@map color 7 to (255, 255, 51), "yellow"
@map color 8 to (166, 86, 40), "brown"
@map color 9 to (247, 129, 191), "pink"
@map color 10 to (153, 153, 153), "grey"
@map color 11 to (166, 206, 227), "lightblue"
@map color 12 to (178, 223, 138), "lightgreen"
@map color 13 to (251, 154, 153), "lightred"
@map color 14 to (253, 191, 111), "lightorange"
@map color 15 to (202, 178, 214), "lightpurple"
"""


def AllConvertibleToType(value_type, value_strings):
  for value_string in value_strings:
    try:
      value_type(value_string)
    except ValueError:
      return False
  return True


def Run(args):
  """All processing stages in this function."""

  assert len(args) == 3, 'csv_filepath ix iy1[,iy2...]'
  csv_filepath, ix, iy_list = args
  ix = int(ix)
  iy_list = [int(s) for s in iy_list.split(',')]

  set_labels = None
  set_data = [list() for _ in iy_list]
  for line in open(csv_filepath).read().splitlines():
    all_flds = line.split(',')
    use_flds = [all_flds[i] for i in [ix] + iy_list]
    if set_labels is None and not AllConvertibleToType(float, use_flds):
      set_labels = use_flds
    else:
      x = all_flds[ix]
      for iy, xy_list in zip(iy_list, set_data):
        y = all_flds[iy]
        xy_list.append((x, y))

  print(CUSTOM_COLOR_MAP)
  if set_labels is not None:
    print('@ xaxis label "%s"' % set_labels[0])
    for i_set, label in enumerate(set_labels[1:]):
      color = i_set + 2  # Start with red, green, blue.
      print('@ s%d symbol color %d' % (i_set, color))
      print('@ s%d symbol fill color %d' % (i_set, color))
      print('@ s%d line color %d' % (i_set, color))
      print('@ s%d errorbar color %d' % (i_set, color))
      print('@ s%d legend "%s"' % (i_set, label))

  for xy_list in set_data:
    for xy in xy_list:
      if not AllConvertibleToType(float, xy):
        s = '# '
      else:
        s = ''
      print(s + ' '.join(xy))
    print('&')
  sys.stdout.flush()


if __name__ == '__main__':
  Run(args=sys.argv[1:])
