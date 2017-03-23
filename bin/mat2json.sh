#! /bin/bash
# Requires JSONlab:
# https://www.mathworks.com/matlabcentral/fileexchange/33381-jsonlab--a-toolbox-to-encode-decode-json-files
# Download zip file, unzip, addpath('.../jsonlab-...');
set -e
if [ $# -ne 3 ]; then
  echo "usage: $0 matlab|octave inp.mat out.json"
  exit 1
fi
if [ "$1" == matlab ]; then
  CO='matlab -nojvm -nodisplay -nosplash -r'
  QUIT=' quit'
elif [ "$1" == octave ]; then
  CO='octave -q --eval'
  QUIT=
else
  echo 'FATAL: first argument must be "matlab" or "octave"'
  exit 1
fi
if [ ! -f "$2" ]; then
  echo "FATAL: input file not found: \"$2\""
  exit 1
fi
if [ -e "$3" ]; then
  if [ ! -w "$3" ]; then
    echo "FATAL: output file is not writable: \"$3\""
    exit 1
  fi
  if [ -d "$3" ]; then
    echo "FATAL: output file is a directory: \"$3\""
    exit 1
  fi
  /bin/rm "$3"
fi
set -x
$CO "data = load('$2'); savejson('', data, 'FileName', '$3', 'FloatFormat', '%.15g', 'SingletArray', 0, 'Compact', 0);$QUIT"
