#! /usr/bin/env python

import random


def GetDigits(num=8):
  while True:
    s = str(random.random()).split(".")[1]
    while s.startswith("0"):
      s = s[1:]
    s = s[:num]
    if len(s) == num:
      return s


def run(args):
  assert len(args) <= 1
  if len(args) == 0:
    num = 1
  else:
    num = eval(args[0])
  for unused in xrange(num):
    print GetDigits()

if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])
