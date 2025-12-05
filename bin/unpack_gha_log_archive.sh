#!/bin/bash
export PRETEND_INTERACTIVE_SHELL=1
. $HOME/.profile
set -e
set -x
unzip $1
# Remove subdirectories
find . -mindepth 1 -type d -exec rm -r {} +
simplify_filenames.py .
for txt in *.txt; do
    strip_ansi_esc -i $txt
done
strip_leading_digits_from_gha_logfile_names.py
