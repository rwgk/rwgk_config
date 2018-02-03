#! /usr/bin/env python

import os
import sys
import time

sys.path.insert(0, os.path.join(os.environ['HOME'], 'rwgk_config', 'py'))

import goood_datetime_stamp


def run(args):
  for filename in args:
    assert os.path.isfile(filename)
    posix_time_end = os.path.getmtime(filename)

    gdts = filename.split('_')[-1]
    dtobj = goood_datetime_stamp.GetDatetimeObjectFromGooodDatetimeStamp(gdts)
    assert dtobj is not None, filename
    posix_time_start = time.mktime(dtobj.timetuple())

    print '%s %.0f' % (filename, posix_time_end - posix_time_start)


if __name__ == '__main__':
  run(args=sys.argv[1:])
