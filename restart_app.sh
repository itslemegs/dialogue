#!/usr/bin/env bash
set -euo pipefail

SERVICE="dialogue"
HEALTH_URL="${1:-http://127.0.0.1/health}"

echo "Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"

echo "Waiting for app to become ready at $HEALTH_URL..."

for i in {1..60}; do
  if curl -fsS --max-time 2 "$HEALTH_URL" > /dev/null; then
    echo "✅ Site is up!"
    exit 0
  fi

  echo "Still starting... attempt $i/60"
  sleep 2
done

echo "❌ Site did not become ready in time."
echo ""
echo "Service status:"
sudo systemctl status "$SERVICE" --no-pager || true

echo ""
echo "Recent logs:"
sudo journalctl -u "$SERVICE" -n 80 --no-pager || true

exit 1