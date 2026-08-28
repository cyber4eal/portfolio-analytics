#!/usr/bin/env bash
# Push to the VPS.
#
#   ./deploy/deploy.sh              site only (the usual case)
#   ./deploy/deploy.sh --code       site + application code, restart the API
#   ./deploy/deploy.sh --pull-data  bring the VPS ledger/pension back first
#
# Who owns what, and why it matters:
#
#   The BUILD runs on the Mac, because the Google service account lives here
#   and it reads the live Holdings sheet.
#
#   The LEDGER and the PENSION are owned by the VPS, because that is where
#   the API runs and where you record a trade from your phone. If both ends
#   wrote them they would silently diverge - a trade entered on the VPS
#   would vanish at the next build from here.
#
# So the order is: pull data down, build, push site up. `refresh --deploy`
# does not do the pull, so use `--pull-data` when the API has been taking
# entries.
set -euo pipefail

HOST="${BOND_HOST:-root@46.202.140.61}"
SITE_DIR="${BOND_SITE_DIR:-/var/www/fundlab}"
APP_DIR="${BOND_APP_DIR:-/opt/portfolio-analytics}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

WITH_CODE=0
PULL_DATA=0
for arg in "$@"; do
  case "$arg" in
    --code) WITH_CODE=1 ;;
    --pull-data) PULL_DATA=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$PULL_DATA" = 1 ]; then
  echo "Pulling ledger and pension from $HOST"
  mkdir -p "$HERE/data"
  # No --delete: a file that only exists locally is not something to
  # destroy on the strength of a directory listing.
  rsync -avz "$HOST:$APP_DIR/data/" "$HERE/data/" || {
    echo "nothing to pull (first deploy?)" >&2; }
fi

if [ ! -f "$HERE/site/data.json" ]; then
  echo "site/data.json missing - run: python3 -m fundengine build" >&2
  exit 1
fi

if [ "$WITH_CODE" = 1 ]; then
  echo "Publishing application code to $HOST:$APP_DIR"
  ssh "$HOST" "mkdir -p $APP_DIR/data"
  rsync -avz --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '.cache' \
    --exclude 'logs' --exclude 'site' --exclude 'data' \
    "$HERE/" "$HOST:$APP_DIR/"
  # data/ is pushed without --delete and only if the VPS has none, so a
  # deploy can seed an empty box without ever overwriting live entries.
  ssh "$HOST" "[ -f $APP_DIR/data/transactions.jsonl ]" \
    && echo "  VPS already has a ledger; leaving data/ alone" \
    || rsync -avz "$HERE/data/" "$HOST:$APP_DIR/data/"
fi

echo "Publishing $(du -sh "$HERE/site" | cut -f1) of site to $HOST:$SITE_DIR"
ssh "$HOST" "mkdir -p $SITE_DIR"
rsync -avz --delete --exclude '.DS_Store' "$HERE/site/" "$HOST:$SITE_DIR/"

ssh "$HOST" "chown -R www-data:www-data $SITE_DIR && nginx -t && systemctl reload nginx"
if [ "$WITH_CODE" = 1 ]; then
  ssh "$HOST" "systemctl restart bond-api 2>/dev/null && sleep 1 && systemctl is-active bond-api" \
    && echo "bond-api restarted" \
    || echo "bond-api not installed yet - see deploy/README.md"
fi
echo "Done."
