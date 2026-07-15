#!/usr/bin/env bash
# Verify the running usm-frontend serves the freshly-built hashed bundle and
# that the Capacity 14d change is actually compiled into it.
set -uo pipefail

echo "=== index.html asset reference ==="
docker exec usm-frontend cat /usr/share/nginx/html/index.html | grep -o 'assets/[^"]*\.js' || true

echo "=== JS assets present in image ==="
docker exec usm-frontend ls -1 /usr/share/nginx/html/assets/ | grep -E '\.js$' || true

echo "=== image build time (when frontend was last rebuilt) ==="
docker inspect --format '{{.Created}}' docker-usm-frontend 2>/dev/null || true

echo "=== container started ==="
docker inspect --format '{{.State.StartedAt}}' usm-frontend 2>/dev/null || true

echo "=== grep for trend-day option literals (7/14) in built JS ==="
# Look for the option values our change introduces. Minified, but numeric
# literals and the 'days' option array survive.
for f in $(docker exec usm-frontend sh -c 'ls /usr/share/nginx/html/assets/*.js'); do
  hits=$(docker exec usm-frontend sh -c "grep -o -E '\"?(7d|14d|14 days|Last 14)\"?' '$f' | head" || true)
  if [ -n "$hits" ]; then
    echo "  $f -> $hits"
  fi
done

echo "=== nginx cache-control for index.html ==="
docker exec usm-frontend grep -i -A3 'location = /index.html\|location / ' /etc/nginx/conf.d/default.conf 2>/dev/null || \
  docker exec usm-frontend cat /etc/nginx/conf.d/default.conf
echo "DONE."
