#! /usr/bin/env python2

def run(args):
  buf_size = 30 * 1024 * 1024
  for filename in args:
    part_num = 0
    with open(filename, "rb") as f:
      while True:
        buf = f.read(buf_size)
        if len(buf) == 0:
          break
        open(filename + "_part%02d" % part_num, "wb").write(buf)
        part_num += 1

if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])
