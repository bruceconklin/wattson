#!/bin/bash
set -euo pipefail

OPTIONS=/data/options.json
opt() { jq --raw-output --arg key "$1" '.[$key] // empty' "$OPTIONS"; }

# Settings come from the app's Configuration tab in Home Assistant.
export DISCORD_TOKEN="$(opt DISCORD_TOKEN)"
export IPMI_HOST="$(opt IPMI_HOST)"
export IPMI_USER="$(opt IPMI_USER)"
export IPMI_PASS="$(opt IPMI_PASS)"
export DISCORD_GUILD_ID="$(opt DISCORD_GUILD_ID)"
export DISCORD_ALLOWED_ROLE_ID="$(opt DISCORD_ALLOWED_ROLE_ID)"
export DOCKER_SSH_HOST="$(opt DOCKER_SSH_HOST)"
export DOCKER_SSH_USER="$(opt DOCKER_SSH_USER)"

# Wattson's own SSH key for the Docker server. Created once and kept in /data,
# so it survives restarts and updates and never leaves this app.
export WATTSON_SSH_DIR=/data/ssh
mkdir -p -m 700 "$WATTSON_SSH_DIR"
if [ ! -f "$WATTSON_SSH_DIR/id_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "wattson@homeassistant" -f "$WATTSON_SSH_DIR/id_ed25519"
  echo "Created a new SSH key for Docker control."
fi
echo "Wattson SSH public key (authorize it on the Docker server, see server/SETUP.md):"
cat "$WATTSON_SSH_DIR/id_ed25519.pub"

echo "Starting Wattson..."
exec python3 /app/bot.py
