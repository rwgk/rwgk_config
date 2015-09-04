#! /usr/bin/env python

import random
import string
import sys


def run(args):
  assert len(args) == 1, 'Number of Words.'
  words = []
  for i in xrange(int(args[0])):
    l = random.randrange(2, 9)
    word = ''.join([random.choice(string.hexdigits) for unused_ in xrange(l)])
    words.append(word)
  print ' '.join(words)


if __name__ == '__main__':
  run(args=sys.argv[1:])
