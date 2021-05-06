#! /usr/bin/env python2

import subprocess
import sys


def run(args):
  for filename in args:
    divider = ':' * len(filename)
    if len(args) > 1:
      print divider
      print filename
      print divider
    # http://stackoverflow.com/questions/2394017/remove-comments-from-c-c-code
    cmd = ['gcc', '-fpreprocessed', '-dD', '-E', filename]
    lines = subprocess.check_output(cmd, shell=False).splitlines()
    prev_line = 'BOF'
    for line in lines:
      line = line.rstrip()
      if line.startswith('#'):
        line = ''
      if line or prev_line:
        print line
      prev_line = line


if __name__ == '__main__':
  run(args=sys.argv[1:])
