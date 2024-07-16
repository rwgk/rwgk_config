#! /usr/bin/env python3

r"""Replace all PTR=123456= with PTR=AAA= in order seen.

Example C++ code:
    printf("\nPTR=%ld=\n", (long) ptr);
"""

import re
import string
import sys


def run(args):
  """."""
  assert len(args) == 1, "filename|-"
  if args[0] == "-":
    blob = sys.stdin.read()
  else:
    blob = open(args[0]).read()
  mappings = {}
  for ptrnum in re.findall(r"PTR=\d+=", blob):
    ptrsym = mappings.get(ptrnum)
    if ptrsym is None:
      ptrsym = "PTR=%s=" % (string.ascii_uppercase[len(mappings)] * 3)
      mappings[ptrnum] = ptrsym
      blob = blob.replace(ptrnum, ptrsym)
  print(blob, end="")


if __name__ == "__main__":
  run(args=sys.argv[1:])
