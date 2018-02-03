#! /usr/bin/env python

import os
import sys

sys.path.insert(0, os.path.join(os.environ['HOME'], 'rwgk_config', 'py'))

import goood_datetime_stamp


def Convert(fobj):
  for line in fobj:
    flds = []
    for fld in line.split():
      try:
        ptm = goood_datetime_stamp.GetPosixTimeMillisFromGooodDatetimeStamp(fld)
      except Exception:
        pass
      else:
        fld = str(ptm)
      flds.append(fld)
    print ' '.join(flds)
    sys.stdout.flush()


def run(args):
  if args:
    for filename in args:
      Convert(open(filename))
  else:
    Convert(sys.stdin)


if __name__ == '__main__':
  run(args=sys.argv[1:])
