#!/usr/bin/env bash
# Deploy the Teams flat-payload fix (commit cherry-picked from bundle) to dev.
set -euo pipefail
REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
cd "$REPO"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1

TAG="pre-teamsfix-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG"

echo "=== fetch bundle + cherry-pick ==="
git fetch /tmp/usm-teamsfix.bundle 'refs/heads/feature/volume-growth-history:refs/remotes/bundle/teamsfix'
# The fix commit is the tip of that branch in the bundle.
FIX=$(git rev-parse refs/remotes/bundle/teamsfix)
echo "fix commit: $FIX"
git cherry-pick "$FIX"
echo "cherry-pick OK; new head: $(git log --oneline -1)"

echo "=== rebuild + recreate usm-backend ==="
docker compose -f "$COMPOSE" up -d --build usm-backend

echo "=== wait for health ==="
for i in $(seq 1 30); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-backend 2>/dev/null || echo "none")
  echo "  attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== RE-VALIDATE running container ==="
echo -n "flat payload keys in notification.py: "
docker exec usm-backend grep -c '"array_name": array_name' /app/app/services/notification.py || true
echo -n "AdaptiveCard removed (should be 0): "
docker exec usm-backend grep -c "AdaptiveCard" /app/app/services/notification.py || true
echo -n "message_id passed in dell/alerts.py: "
docker exec usm-backend grep -c "message_id=str" /app/app/collectors/dell/alerts.py || true
echo "container status: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-backend)"
echo "DONE. Rollback: cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-backend"
