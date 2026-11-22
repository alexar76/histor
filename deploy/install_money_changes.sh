#!/usr/bin/env bash
# Install the money-tool changes feed on the HISTOR host (run there as root, from a checkout of this repo).
#   histor/deploy/install_money_changes.sh
# Writes /opt/histor-money-changes/money_changes.py, the unit and the timer, and the feeds directory that
# nginx already serves under /datasets/ (alias /var/lib/histor-datasets/). Removal: disable the timer and
# delete those three paths and /var/lib/histor-money-changes (the cursor and cache).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
install -d -m 0755 /opt/histor-money-changes
install -m 0644 "$here/../histor/money_changes.py" /opt/histor-money-changes/money_changes.py
install -d -m 0755 /var/lib/histor-datasets/feeds
install -m 0644 "$here/histor-money-changes.service" /etc/systemd/system/histor-money-changes.service
install -m 0644 "$here/histor-money-changes.timer" /etc/systemd/system/histor-money-changes.timer
systemctl daemon-reload
systemctl enable --now histor-money-changes.timer
echo "installed; first run: systemctl start histor-money-changes.service"
