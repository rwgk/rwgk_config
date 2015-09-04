#! /usr/bin/env python

import sys
from fileinput import input, isfirstline, filename, isstdin
from string import expandtabs, rstrip
for line in input(inplace=1):
  if (not isstdin() and isfirstline()):
    current_stdout = sys.stdout
    sys.stdout = sys.__stdout__
    print filename() + ':'
    sys.stdout = current_stdout
  print rstrip(expandtabs(line))
