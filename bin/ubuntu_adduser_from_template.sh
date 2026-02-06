#!/bin/bash

set -e

if [ $# -ne 2 ]; then
    echo "Error: Expected exactly 2 arguments (template username, new username), got $#"
    echo "Usage: $0 <template_username> <new_username>"
    exit 1
fi

set -x
template_user="$1"
u="$2"
groups="$(id -Gn "$template_user" | tr ' ' ',')"
sudo adduser --gecos "" "$u"
home_dir="$(getent passwd "$u" | cut -d: -f6)"
if [ -d "$home_dir" ]; then
    sudo chmod 755 "$home_dir"
else
    echo "Warning: Home directory '$home_dir' does not exist" >&2
fi
sudo usermod -aG "$groups" "$u"
id "$u" # for visual validation
