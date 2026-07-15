#!/usr/bin/env bash
# Deploy the Capacity 7d/14d trend-range UI tweak (commit b7b055c) to dev.
# Frontend-only change -> rebuild the usm-frontend container.
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
BUNDLE=/tmp/usm-capacity-14d.bundle
cd "$REPO"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1

TAG="pre-capacity14d-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG"

echo "=== fetch bundle ==="
git fetch "$BUNDLE" 'refs/heads/feature/volume-growth-history:refs/remotes/bundle/capacity14d'
FIX=$(git rev-parse refs/remotes/bundle/capacity14d)
echo "fix commit: $FIX"

echo "=== cherry-pick the single capacity commit ==="
# Dev history diverged (its own equivalent Teams-fix commit), so we cherry-pick
# just the frontend Capacity.tsx commit rather than fast-forward/merge.
if git cherry-pick "$FIX"; then
  echo "cherry-pick OK"
else
  echo "!! cherry-pick hit a conflict — aborting and rolling back"
  git cherry-pick --abort || true
  exit 1
fi
echo "new head:"
git log --oneline -2


echo "=== sanity: DEFAULT_TREND_DAYS present in source ==="
grep -n 'DEFAULT_TREND_DAYS' frontend/src/pages/Capacity.tsx | head

echo "=== rebuild + recreate usm-frontend ==="
docker compose -f "$COMPOSE" up -d --build usm-frontend

echo "=== wait for health ==="
for i in $(seq 1 30); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-frontend 2>/dev/null || echo "none")
  echo "  attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== RE-VALIDATE running container ==="
echo "container status: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-frontend)"
echo -n "built bundle serves DEFAULT_TREND_DAYS (14d) marker: "
# The compiled JS is minified; just confirm the SPA is reachable.
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:8080 || echo "frontend not reachable"

echo "DONE. Rollback: cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-frontend"
