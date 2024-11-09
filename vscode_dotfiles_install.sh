#! /bin/bash
cd "$HOME"
ln -s dotfiles/inputrc .inputrc
ln -s dotfiles/vimrc .vimrc
mkdir -p "$HOME/.vim/backup"
mkdir -p "$HOME/.vim/swap"
mkdir -p "$HOME/.vim/undo"
