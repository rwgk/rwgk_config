#! /usr/bin/env python3

"""."""

import datetime
import os
import sys
import time
import zoneinfo


EPOCH_MAX_SECONDS = 2**31 - 1

TZNAME = "America/Los_Angeles"


def datetime_aware_fromtimestamp(s, tzname):
  return datetime.datetime.fromtimestamp(s, tz=zoneinfo.ZoneInfo(tzname))


def run(args):
  """."""
  t_start = time.time()
  def process(arg):
    try:
      s = eval(arg)  # pylint: disable=eval-used
    except Exception:  # pylint: disable=broad-exception-caught
      print(arg.rstrip())
    else:
      if (s < 0):
        s += t_start
      elif (s > EPOCH_MAX_SECONDS and arg.strip().isdigit()):
        s /= 1e9  # Assume nano-seconds.
      print("%.0f = %s" % (s, datetime_aware_fromtimestamp(s, tzname=TZNAME)))
  if not args:
    args = ["%.0f" % t_start]
  for arg in args:
    if arg == "-":
      for line in sys.stdin:
        process(line)
    elif os.path.isfile(arg):
      for line in open(arg):
        process(line)
    else:
      process(arg)

if __name__ == "__main__":
  run(args=sys.argv[1:])
