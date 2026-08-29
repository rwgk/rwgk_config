#!/bin/bash

set -euxo pipefail

dest="$HOME/Downloads"
now="$(date "+%Y-%m-%d+%H%M%S")"

defaults export NSGlobalDomain \
    "$dest/NSGlobalDomain_${now}.plist"

defaults export com.apple.symbolichotkeys \
    "$dest/com.apple.symbolichotkeys_${now}.plist"
