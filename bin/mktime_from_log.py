#! /usr/bin/env python

def run(args):
  from rwgk_utils import mktime_from_log_timestamp
  for arg in args:
    flds = arg.split()
    assert len(flds) == 2
    t = mktime_from_log_timestamp(*flds)
    if t is None: s = 'None'
    else: s = '%.2f' % t
    print "%s = %s" % (arg, s)

if (__name__ == '__main__'):
  import sys
  run(args=sys.argv[1:])
