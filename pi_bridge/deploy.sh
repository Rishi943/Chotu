#!/usr/bin/env bash
# deploy.sh — Run from the LAPTOP after the Pi is up and SSH works.
# Usage: bash pi_bridge/deploy.sh

set -e

PI_USER="chotu"
PI_HOST="chotu.local"
REMOTE_DIR="~/chotu-bridge"

echo "==> Copying server.py to Pi..."
scp pi_bridge/server.py ${PI_USER}@${PI_HOST}:${REMOTE_DIR}/server.py

echo "==> Copying chotu package to Pi..."
ssh ${PI_USER}@${PI_HOST} "mkdir -p ${REMOTE_DIR}/chotu/faces"
scp pi_bridge/chotu/__init__.py ${PI_USER}@${PI_HOST}:${REMOTE_DIR}/chotu/__init__.py
scp pi_bridge/chotu/face.py ${PI_USER}@${PI_HOST}:${REMOTE_DIR}/chotu/face.py

echo "==> Done. To start the bridge:"
echo "    ssh ${PI_USER}@${PI_HOST}"
echo "    sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py"
