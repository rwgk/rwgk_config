#! /usr/bin/env python

import os
op = os.path
import time

def run(args):
  t_start = time.time()
  def process(arg):
    try:
      s = eval(arg)
    except Exception:
      print arg,
    else:
      if (s < 0): s += t_start
      print "%d = %s" % (s, time.asctime(time.localtime(s)))
  if (len(args) == 0): args = ["%.0f" % t_start]
  for arg in args:
    if arg == '-':
      for line in sys.stdin:
        process(line)
    elif op.isfile(arg):
      for line in open(arg):
        process(line)
    else:
      process(arg)

if (__name__ == '__main__'):
  import sys
  run(args=sys.argv[1:])
