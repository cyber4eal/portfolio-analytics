#!/usr/bin/env bash
# Push the built site to the VPS. Run from the repo root, after a build.
#
#   python3 -m fundengine build && ./deploy/deploy.sh
#
# Requires an ssh key that already reaches the box. Nothing here creates
# credentials or reads secrets - the site is static files only, and the
# Google service account never leaves this Mac.
set -euo pipefail

HOST="${FUNDLAB_HOST:-root@46.202.140.61}"
REMOTE_DIR="${FUNDLAB_DIR:-/var/www/fundlab}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$HERE/site/data.json" ]; then
  echo "site/data.json missing - run: python3 -m fundengine build" >&2
  exit 1
fi

echo "Publishing $(du -sh "$HERE/site" | cut -f1) to $HOST:$REMOTE_DIR"
ssh "$HOST" "mkdir -p $REMOTE_DIR"
rsync -avz --delete \
  --exclude '.DS_Store' \
  "$HERE/site/" "$HOST:$REMOTE_DIR/"
ssh "$HOST" "chown -R www-data:www-data $REMOTE_DIR && nginx -t && systemctl reload nginx"
echo "Done."
