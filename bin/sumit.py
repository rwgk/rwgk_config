#! /usr/bin/env python

from __future__ import division

def run(args):
  if not args:
    args = ['-']
  assert args.count('-') < 2
  num_lines_ignored = 0
  sum = 0
  for filename in args:
    if filename == '-':
      f = sys.stdin
    else:
      f = open(filename)
    for line in f:
      flds = line.split()
      if (len(flds) > 0):
        try: val = eval(flds[0])
        except Exception: num_lines_ignored += 1
        else: sum += val
  if num_lines_ignored:
    print 'lines ignored:', num_lines_ignored
  print sum

if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])
