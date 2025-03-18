#!/bin/bash

# 2025-03-17+1350
# https://chatgpt.com/share/67d88b5f-4060-8008-9afa-e401cca1f8f0

set -euo pipefail

# Get the short hostname (without domain)
HOSTNAME_SHORT=$(hostname | cut -d'.' -f1)

VENV_PATH="$HOME/venvs/$HOSTNAME_SHORT/base"

if [[ -d "$VENV_PATH" ]]; then
    echo "$VENV_PATH exists already."
    echo "Please remove it first."
    exit 1
fi

echo "Creating virtual environment at $VENV_PATH"
mkdir -p "$VENV_PATH"
python3 -m venv "$VENV_PATH"

echo "Activating virtual environment for host $HOSTNAME_SHORT"
source "$VENV_PATH/bin/activate"

echo "Installing common packages..."
pip install --upgrade pip
pip install pytest requests pyyaml numpy scipy

echo "New virtual environment ready."
