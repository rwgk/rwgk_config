#! /usr/bin/env python2

import collections
import json
import sys


def run(args):
  for filename in args:
    data = json.load(open(filename), object_pairs_hook=collections.OrderedDict)
    print json.dumps(data, indent=4)


if __name__ == '__main__':
  run(args=sys.argv[1:])
