#! /usr/bin/env python3

import sys
from fileinput import input, isfirstline, filename, isstdin
for line in input(inplace=1):
  if (not isstdin() and isfirstline()):
    current_stdout = sys.stdout
    sys.stdout = sys.__stdout__
    print(filename() + ':')
    sys.stdout = current_stdout
  print(line.expandtabs().rstrip())
