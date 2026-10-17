#!/bin/sh
set -e
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/btprintbridge" ~/.local/bin/btprintbridge
echo "Installed: ~/.local/bin/btprintbridge -> $(pwd)/bin/btprintbridge"