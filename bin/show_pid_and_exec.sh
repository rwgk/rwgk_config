#!/bin/bash
echo "PID: $$" >&1
echo "CWD: $(realpath .)"
set -x
exec "$@"
