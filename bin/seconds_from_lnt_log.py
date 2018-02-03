#! /usr/bin/env python

import datetime
import os
import sys
import time


# Copied function from py_utils.py @183770241 2018-01-29 19:29:10
def GetDatetimeObjectFromGooodDatetimeStamp(string, tzinfo=None):
  """Example: 2017-08-21+115838+123+456+789. Warning: nanos are lost!"""
  # nanos are lost, but used for rounding up.
  if not (string and 10 <= len(string) <= 29):
    return None
  flds = []
  ap = flds.append
  seps = ''
  ap(string[:4])
  seps += string[4]
  ap(string[5:7])
  seps += string[7]
  ap(string[8:10])
  if len(string) > 10:
    seps += string[10]
    if len(string) < 15:
      return None
    ap(string[11:13])
    ap(string[13:15])
    if len(string) > 15:
      if len(string) < 17:
        return None
      ap(string[15:17])
      if len(string) > 17:
        seps += string[17]
        if len(string) < 21:
          return None
        ap(string[18:21])
        if len(string) > 21:
          seps += string[21]
          if len(string) < 25:
            return None
          ap(string[22:25])
          if len(string) > 25:
            seps += string[25]
            if len(string) < 29:
              return None
            ap(string[26:])
  if not '--++++'.startswith(seps):
    return None
  vals = []
  for s in flds:
    if not s.isdigit():
      return None
    vals.append(int(s))
  round_up_micros = False
  if len(vals) > 6:
    micros = vals[6] * 1000
    if len(vals) > 7:
      micros += vals[7]
      if len(vals) > 8:
        if vals[8] >= 500:
          round_up_micros = True
    vals = vals[:6] + [micros]
  datetime_obj = datetime.datetime(*vals, tzinfo=tzinfo)  # CHANGED.
  if round_up_micros:
    datetime_obj += datetime.timedelta(microseconds=1)
  return datetime_obj


def run(args):
  for filename in args:
    assert os.path.isfile(filename)
    posix_time_end = os.path.getmtime(filename)

    goood_datetime_stamp = filename.split('_')[-1]
    dtobj = GetDatetimeObjectFromGooodDatetimeStamp(goood_datetime_stamp)
    assert dtobj is not None, filename
    posix_time_start = time.mktime(dtobj.timetuple())

    print '%s %.0f' % (filename, posix_time_end - posix_time_start)


if __name__ == '__main__':
  run(args=sys.argv[1:])
