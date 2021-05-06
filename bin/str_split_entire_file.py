#! /usr/bin/env python2

import sys


def read_split_print(file_like_obj):
  for line in file_like_obj.read().split():
    print line


def run(args):
  if args:
    for filename in args:
      read_split_print(open(filename))
  else:
    read_split_print(sys.stdin)


if (__name__ == '__main__'):
  run(args=sys.argv[1:])
