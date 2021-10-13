#! /usr/bin/env python3

import sys


def run(args):
  assert args
  field_index = int(args[0])
  args.pop(0)
  for filename in args:
    for line in open(filename).readlines():
      flds = line.split()
      print(flds[field_index], flush=True)


if __name__ == '__main__':
  run(args=sys.argv[1:])
