#! /usr/bin/env python2

def run(args):
  for filename in args:
    n_all = 0
    n_over = 0
    for line in open(filename):
      n_all += 1
      if len(line.rstrip()) > 80:
        n_over += 1
    print '%5d / %5d = %5.1f %%  %s' % (
        n_over, n_all, 100. * n_over / max(n_all, 1), filename)


if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])
