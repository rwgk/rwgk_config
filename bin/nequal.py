#! /usr/bin/env python2

from __future__ import division
import fileinput
import sys

def run():
  buffer = []
  prev = None
  n = 0
  for line in fileinput.input():
    if (prev is None):
      prev = line
      n = 1
    elif (line != prev):
      buffer.append((n, prev))
      prev = line
      n = 1
    else:
      n += 1
  if (n != 0):
    buffer.append((n, prev))
  if (len(buffer) != 0):
    def cmp_buffer_entries(a, b):
      result = cmp(b[0], a[0])
      if (result == 0):
        result = cmp(a[1], b[1])
      return result
    buffer.sort(cmp_buffer_entries)
    sum_n = 0
    for n,line in buffer:
      sum_n += n
    n_fmt = "%%%dd %%6.2f %%%% %%6.2f %%%% : " % len("%d" % buffer[0][0])
    sn = 0
    for n,line in buffer:
      sn += n
      sys.stdout.write(n_fmt % (n, 100*n/sum_n, 100*sn/sum_n) + line)
    print "Number of lines shown:", len(buffer)
    print "Sum of counts:", sum_n

if (__name__ == "__main__"):
  run()
