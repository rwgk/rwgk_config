#! /usr/bin/env python2

from __future__ import division

import sys


def run(args):
  assert len(args) == 1
  arg = args[0]
  if arg.startswith('w'):
    width = int(arg[1:])
    assert width > 0
    scale = None
  elif arg.startswith('s'):
    width = None
    scale = float(arg[1:])
    assert scale > 0
    scale = 1 / scale
  else:
    raise RuntimeError('Expecting wINT or sFLOAT, found "%s"' % arg)
  fromtos = []
  sum_deltas = 0
  for line in sys.stdin:
    flds = line.split()
    assert len(flds) == 2
    fromto = [int(s) for s in flds]
    assert fromto[0] <= fromto[1]
    fromtos.append(fromto)
    sum_deltas += (fromto[1] - fromto[0])
  if fromtos:
    fromtos.sort()
    x_min = fromtos[0][0]
    x_max = fromtos[-1][1]
    x_delta = x_max - x_min
    if scale is None:
      if x_delta:
        scale = width / x_delta
      else:
        scale = 1
      print 's' + str(1 / scale)
    else:
      if x_delta:
        width = int(round(scale * x_delta))
      else:
        width = 78
      print 'w' + str(width)
    print 'time between first start and last end: %s' % x_delta
    print 'sum of times: %s' % sum_deltas
    def GetCol(x):
      return min(int(round((x - x_min) * scale)), width - 1)
    for ft in fromtos:
      i, j  = [GetCol(x) for x in ft]
      bar = ' ' * i + '-' * (j - i + 1) + ' ' * (width - j - 1)
      print '|' + bar + '|'
      sys.stdout.flush()


if __name__ == '__main__':
  run(args=sys.argv[1:])
