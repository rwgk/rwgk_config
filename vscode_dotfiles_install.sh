#! /bin/bash

# Copy this to the top of settings.json (e.g. in "~/Library/Application Support/Code/User/"):
#   "dotfiles.repository": "rwgk/rwgk_config",
#   "dotfiles.targetPath": "~/rwgk_config",
#   "dotfiles.installCommand": "vscode_dotfiles_install.sh",

cd "$HOME"
mv .bashrc .bashrc_host
ln -s rwgk_config/bashrc .bashrc
mv .profile .profile_devcontainer_default
ln -s rwgk_config/profile .profile
ln -s rwgk_config/inputrc .inputrc
ln -s rwgk_config/vimrc .vimrc
mkdir -p "$HOME/.vim/backup"
mkdir -p "$HOME/.vim/swap"
mkdir -p "$HOME/.vim/undo"
