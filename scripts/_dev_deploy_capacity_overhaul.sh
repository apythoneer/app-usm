#!/usr/bin/env bash
# Deploy the Capacity growth-projection overhaul to dev.
# Spans backend (analytics.py) + frontend (types.ts, Capacity.tsx), so we
# rebuild BOTH usm-backend and usm-frontend.
#
# Mirrors the proven _dev_deploy_growth_viz.sh flow: dev history has diverged,
# so we cherry-pick the single commit from a bundle rather than reset/merge.
#
# Usage (from your workstation):
#   scp scripts/_dev_deploy_capacity_overhaul.sh usm-capacity-overhaul.bundle \
#       ad64490@<dev-host>:/tmp/
#   ssh ad64490@<dev-host> 'bash /tmp/_dev_deploy_capacity_overhaul.sh'
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
BUNDLE=/tmp/usm-capacity-overhaul.bundle
cd "$REPO"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1

TAG="pre-capoverhaul-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG"

echo "=== fetch bundle ==="
git fetch "$BUNDLE" \
  'refs/heads/feature/capacity-overhaul:refs/remotes/bundle/capoverhaul'
FIX=$(git rev-parse refs/remotes/bundle/capoverhaul)
echo "fix commit: $FIX"

echo "=== cherry-pick the single capacity commit ==="
if git cherry-pick "$FIX"; then
  echo "cherry-pick OK"
else
  echo "!! cherry-pick conflict — aborting and rolling back"
  git cherry-pick --abort || true
  exit 1
fi
git log --oneline -2

echo "=== sanity: server projection + freshness present in source ==="
grep -n '_linreg_slope\|projected_full_date\|last_collected' \
  backend/app/api/v1/analytics.py | head
grep -n 'projectedFullDate\|DEFAULT_TREND_DAYS = 90' \
  frontend/src/pages/Capacity.tsx | head

echo "=== rebuild + recreate usm-backend and usm-frontend ==="
docker compose -f "$COMPOSE" up -d --build usm-backend usm-frontend

echo "=== wait for backend health ==="
for i in $(seq 1 40); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-backend 2>/dev/null || echo "none")
  echo "  backend attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== wait for frontend health ==="
for i in $(seq 1 30); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-frontend 2>/dev/null || echo "none")
  echo "  frontend attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== RE-VALIDATE running containers ==="
echo "backend:  $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-backend)"
echo "frontend: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-frontend)"

echo "--- API: /daily-trend now returns projection + last_collected? ---"
curl -sf "http://localhost:8000/api/v1/analytics/daily-trend?days=90" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); p=d.get("projection") or {}; print("data_points=",d.get("data_points"),"last_collected=",d.get("last_collected")); print("trend=",p.get("trend"),"avg_rate_tb_per_day=",p.get("avg_rate_tb_per_day"),"projected_full_date=",p.get("projected_full_date"),"days_to_full=",p.get("days_to_full"))' \
  || echo "!! API check failed"

echo "--- frontend: built bundle markers ---"
ASSET=$(docker exec usm-frontend sh -c "grep -o 'assets/[^\"]*\.js' /usr/share/nginx/html/index.html" | head -1)
echo "served bundle: $ASSET"
echo -n "capacity markers in built JS: "
docker exec usm-frontend sh -c "grep -o -E 'Projected Full|Free Headroom|not filling' /usr/share/nginx/html/$ASSET | sort -u | tr '\n' ' '" || true
echo
curl -sf -o /dev/null -w 'frontend http: %{http_code}\n' http://localhost:8080 || echo "frontend not reachable"

echo "DONE."
echo "Rollback: cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-backend usm-frontend"
