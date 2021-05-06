#! /usr/bin/env python2

from __future__ import division
import sys
import time


def format_tms(tms):
  ts = time.localtime(tms // 1000)
  return "%04d-%02d-%02d+%02d:%02d" % (
      ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min)


def run(args):
  assert len(args) == 1
  time_ms_list = []
  for line in open(args[0]):
    if '"timestampMs"' not in line:
      continue
    line = line.strip()
    flds = line.split('"')
    assert len(flds) == 5
    time_ms = int(flds[3])
    time_ms_list.append(time_ms)
  time_ms_list.sort()  # Actually needed.
  gaps = []
  prev_tms = None
  for tms in time_ms_list:
    if prev_tms is not None:
      gaps.append((tms - prev_tms, prev_tms))
    prev_tms = tms
  gaps.sort(reverse=True)
  min_delta = 1000 * 60 * 60
  for delta, prev_tms in gaps:
    if delta < min_delta:
      break
    days = delta / (1000 * 60 * 60 * 24)
    prev_fmt = format_tms(prev_tms)
    next_fmt = format_tms(prev_tms + delta)
    print "%7.2f %s" % (days, prev_fmt)
    print "%7s %s" % ('', next_fmt)


if __name__ == '__main__':
  run(args=sys.argv[1:])
