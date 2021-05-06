#! /usr/bin/env python2

import sys

def run(args):
  for file_name in args:
    n_file = 0
    for i_line,line in enumerate(open(file_name).read().splitlines()):
      n_line = 0
      for c in line:
        if (ord(c) < 32 or ord(c) >= 127):
          n_line += 1
      if (n_line != 0):
        print "%s(%d): %d unprintable" % (file_name, i_line+1, n_line)
        n_file += n_line
    if (n_file != 0):
      print "%s: %d unprintable total" % (file_name, n_file)
      print

if (__name__ == "__main__"):
  run(args=sys.argv[1:])
