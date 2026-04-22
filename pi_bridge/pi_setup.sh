#!/usr/bin/env bash
# pi_setup.sh — Run ON THE PI after reflashing, before deploy.sh from laptop.
# Assumes: SunFounder PiCrawler OS image (picrawler/vilib/robot_hat pre-installed).
# Run as: bash pi_setup.sh

set -e

BRIDGE_DIR=~/chotu-bridge

echo "==> Creating bridge directory..."
mkdir -p ${BRIDGE_DIR}

echo "==> Creating venv..."
python3 -m venv ${BRIDGE_DIR}/.venv

echo "==> Installing pip deps..."
${BRIDGE_DIR}/.venv/bin/pip install --upgrade pip
${BRIDGE_DIR}/.venv/bin/pip install fastapi "uvicorn[standard]" opencv-python-headless

echo "==> Setup complete."
echo "    Next: run deploy.sh from your laptop to copy server.py."
echo "    Then start with: sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py"
