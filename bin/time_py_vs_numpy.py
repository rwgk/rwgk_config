#! /usr/bin/env python

import sys
import time

import numpy as np


def time_py(asize, nloop):
  a = [elem * 3.14 for elem in range(asize)]
  b = [elem * 3.14 for elem in range(asize)]
  t0 = time.time()
  for i in xrange(nloop):
    c = [a[j] + b[j] for j in xrange(asize)]
  return time.time() - t0


def time_np(asize, nloop):
  a = np.arange(asize) * 3.14
  b = np.arange(asize) * 3.14
  t0 = time.time()
  for i in xrange(nloop):
    c = a + b
  return time.time() - t0


def show_time(label, elapsed):
  print '%s: %.3f s' % (label, elapsed)
  return elapsed


def run(args):
  assert len(args) <= 2, '[[asize] nloop]'
  asize = 1000000
  nloop = 10
  if args:
    nloop = int(args[-1])
    if len(args) > 1:
     asize = int(args[-2])
  print 'asize:', asize
  print 'nloop:', nloop
  ep = show_time('py', time_py(asize, nloop))
  en = show_time('np', time_np(asize, nloop))
  print 'ratio: %.3f' % (ep / en)


if __name__ == '__main__':
  run(sys.argv[1:])
