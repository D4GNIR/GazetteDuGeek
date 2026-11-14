#!/bin/sh
# Lance la gazette avec les réglages secrets de .env (appelé par cron ou systemd).
cd "$(dirname "$0")" || exit 1
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec python3 gazette.py "$@"
