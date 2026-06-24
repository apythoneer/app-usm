#!/usr/bin/env bash
# Deploy the Capacity "Growth & Decline" summary to dev.
# Frontend-only change -> cherry-pick the single commit, then rebuild usm-frontend.
# Mirrors the proven _dev_deploy_capacity14d.sh flow (dev history has diverged,
# so we cherry-pick a bundle rather than reset/merge).
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
BUNDLE=/tmp/usm-growth-viz.bundle
cd "$REPO"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1

TAG="pre-growthviz-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG"

echo "=== fetch bundle ==="
git fetch "$BUNDLE" 'refs/heads/feature/growth-viz:refs/remotes/bundle/growthviz'
FIX=$(git rev-parse refs/remotes/bundle/growthviz)
echo "fix commit: $FIX"

echo "=== cherry-pick the single Capacity commit ==="
if git cherry-pick "$FIX"; then
  echo "cherry-pick OK"
else
  echo "!! cherry-pick conflict — aborting and rolling back"
  git cherry-pick --abort || true
  exit 1
fi
git log --oneline -2

echo "=== sanity: FleetGrowthSummary present in source ==="
grep -n 'FleetGrowthSummary' frontend/src/pages/Capacity.tsx | head

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
ASSET=$(docker exec usm-frontend sh -c "grep -o 'assets/[^\"]*\.js' /usr/share/nginx/html/index.html" | head -1)
echo "served bundle: $ASSET"
echo -n "growth markers in built JS: "
docker exec usm-frontend sh -c "grep -o -E 'Projected Full|Day-over-day|Free Headroom' /usr/share/nginx/html/$ASSET | sort -u | tr '\n' ' '" || true
echo
curl -sf -o /dev/null -w 'frontend http: %{http_code}\n' http://localhost:8080 || echo "frontend not reachable"

echo "DONE. Rollback: cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-frontend"
