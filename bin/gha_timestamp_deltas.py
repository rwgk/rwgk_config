#! /usr/bin/python3

"""Duration of GitHub Action Jobs from log timestamps in *.txt files."""

import datetime
import sys


def run(args):
  """."""
  deltas = []
  for filename in args:
    lines = open(filename).read().splitlines()
    num_test_session_starts = 0
    for line in lines:
      if "= test session starts =" in line:
        num_test_session_starts += 1
    dts = []
    for line in [lines[0], lines[-1]]:
      flds = line.split()
      tstamp = flds[0]
      assert len(tstamp) == len("2023-04-21T01:15:30.4325268Z"), line
      datetime_fmt = "%Y-%m-%dT%H:%M:%S.%f"
      dts.append(datetime.datetime.strptime(tstamp[:-2], datetime_fmt).replace(
          tzinfo=datetime.timezone.utc))
    if num_test_session_starts:
      deltas.append((dts[1] - dts[0], num_test_session_starts, filename))
  deltas.sort()
  for item in deltas:
    print(*item)


if __name__ == "__main__":
  run(args=sys.argv[1:])
