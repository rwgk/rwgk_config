#! /usr/bin/env python

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys


def Run(args):
  for ix_file, filename in enumerate(sorted(args)):
    print('@ s%d legend "%s"' % (ix_file, filename))
    for line in open(filename).read().splitlines():
      print(line.replace(',', ' '))
    print('&')


if __name__ == '__main__':
  Run(args=sys.argv[1:])
