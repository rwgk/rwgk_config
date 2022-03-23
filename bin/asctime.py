#! /usr/bin/env python3

import os
op = os.path
import time


EPOCH_MAX_SECONDS = 2**31 - 1


def run(args):
  t_start = time.time()
  def process(arg):
    try:
      s = eval(arg)
    except Exception:
      print(arg, end=None)
    else:
      if (s < 0):
        s += t_start
      elif (s > EPOCH_MAX_SECONDS and arg.strip().isdigit()):
        s /= 1e9  # Assume nano-seconds.
      print("%.0f = %s" % (s, time.asctime(time.localtime(s))))
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
