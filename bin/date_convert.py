#! /usr/bin/env python2

import sys


def do_convert(line):
  # Thu Oct 13 17:02:49 UTC 2016
  flds = line.split(' ')
  if len(flds) != 6:
    print '#', line
    return
  hhmmss = flds[3]
  if hhmmss.count(':') != 2:
    print '#', line
    return
  hhmmss = hhmmss.replace(':', '')
  day = flds[2]
  year = flds[5]
  if not hhmmss.isdigit() or not day.isdigit() or not year.isdigit():
    print '#', line
    return
  while len(day) > 1 and day.startswith('0'):
    day = day[1:]
  j_day = int(day)
  month_names = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ')
  month_name = flds[1]
  if month_name not in month_names:
    print '#', line
    return
  j_month = month_names.index(month_name) + 1
  # %Y-%m-%d+%H%M%S
  print '%s-%02d-%02d+%s' % (year, j_month, j_day, hhmmss)


def run(args):
  if len(args) == 0:
    for line in sys.stdin.read().splitlines():
      do_convert(line)
  else:
    do_convert(' '.join(args))


if __name__ == '__main__':
  run(args=sys.argv[1:])
