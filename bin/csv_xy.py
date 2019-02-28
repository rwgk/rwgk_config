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


def ProcessCommandlineIxRangeSpec(ix_with_optional_range_spec):
  """Converts e.g. 1,5000,10000."""
  flds = ix_with_optional_range_spec.split(',')
  assert 1 <= len(flds) <= 3, ix_with_optional_range_spec
  range_spec_float = [float(s) for s in flds[1:]]
  first = None
  last = None
  if len(range_spec_float) == 1:
    last = range_spec_float[0]
  elif len(range_spec_float) == 2:
    first, last = range_spec_float
  return int(flds[0]), first, last


def Run(args):
  """All processing stages in this function."""

  assert len(args) == 3, 'csv_filepath ix[,<range-spec>] iy1[,iy2...]'
  csv_filepath, ix_with_optional_range_spec, iy_list = args
  ix, x_first, x_last = ProcessCommandlineIxRangeSpec(
      ix_with_optional_range_spec)
  iy_list = [int(s) for s in iy_list.split(',')]

  set_labels = None
  set_data = [list() for _ in iy_list]
  x_min, x_max = x_first, x_last
  y_min, y_max = None, None
  for line in open(csv_filepath).read().splitlines():
    all_flds = line.split(',')
    use_flds = [all_flds[i] for i in [ix] + iy_list]
    if set_labels is None and not AllConvertibleToType(float, use_flds):
      set_labels = use_flds
    else:
      x = all_flds[ix]
      x_float = float(x)
      if x_first is not None and x_float < x_first:
        continue
      if x_last is not None and x_float > x_last:
        continue  # Not using break to be most general (x not sorted).
      if x_min is None or x_min > x_float:
        x_min = x_float
      if x_max is None or x_max < x_float:
        x_max = x_float
      for iy, xy_list in zip(iy_list, set_data):
        y = all_flds[iy]
        y_float = float(y)
        if y_min is None or y_min > y_float:
          y_min = y_float
        if y_max is None or y_max < y_float:
          y_max = y_float
        xy_list.append((x, y))

  if x_first is not None or x_last is not None:
    print('@version 50125')  # Indirect way to turn autoscaling off.
  print(CUSTOM_COLOR_MAP)
  if set_labels is not None:
    if x_first is not None or x_last is not None:
      print('@ world %.15g, %.15g, %.15g, %.15g' % (x_min, y_min, x_max, y_max))
      print('@ xaxis label "%s"' % set_labels[0])
      print('@ xaxis tick major 1000')  # TODO(rwgk): dynamic
      print('@ xaxis tick minor 200')
      print('@ yaxis tick major 0.2')
      print('@ yaxis tick minor 0.1')
    for i_set, label in enumerate(set_labels[1:]):
      color = i_set + 2  # Start with red, green, blue.
      print('@ s%d symbol 1' % i_set)
      print('@ s%d symbol size 0.20' % i_set)
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
