#!/bin/bash
set -e

script_dir="$(dirname "$0")"
create_one="$script_dir/create_one_dotfile_softlink.sh"

"$create_one" .profile rwgk_config/profile
"$create_one" .bashrc rwgk_config/bashrc
"$create_one" .inputrc rwgk_config/inputrc
"$create_one" .vimrc rwgk_config/vimrc
