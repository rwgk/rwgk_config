#! /usr/bin/env python

"""Runs iwyu for given .h against temporarily-created .cpp file."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import subprocess
import sys


def run(args):
  """."""
  assert args, '[iwyu_opts] header_filename'
  iwuy_opts = args[:-1]
  header_filename = args[-1]
  assert os.path.isfile(header_filename), header_filename
  leading, ext = os.path.splitext(header_filename)
  assert ext in ('.h', '.hh', '.hpp')
  cpp_filename = leading + '.cpp'
  assert not os.path.exists(cpp_filename), cpp_filename
  cpp_blob = '#include "%s"\n' % os.path.basename(header_filename)
  sub_args = ['iwyu'] + iwuy_opts + [cpp_filename]
  print(' '.join(sub_args))
  print()
  sys.stdout.flush()
  open(cpp_filename, 'w').write(cpp_blob)
  try:
    subprocess.call(['iwyu'] + iwuy_opts + [cpp_filename])
  finally:
    os.remove(cpp_filename)


if __name__ == '__main__':
  run(args=sys.argv[1:])
