#!/usr/bin/env bash
# Non-destructive snapshot/backup of the running dev environment BEFORE SIP work.
# Creates: git rollback tag, docker image commit, .env backup. Deletes nothing.
set -uo pipefail
REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
STAMP=$(date +%Y%m%d-%H%M%S)
DATE=$(date +%Y%m%d)
cd "$REPO"

echo "=== 1. current state ==="
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD=$(git rev-parse --short HEAD)
echo "branch: $BRANCH"
echo "head:   $(git log --oneline -1)"
echo "container: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}} (up since {{.State.StartedAt}})' usm-backend 2>/dev/null || echo 'not found')"

echo "=== 2. git rollback tag ==="
TAG="pre-sip-$STAMP"
git tag -f "$TAG"
echo "created git tag: $TAG -> $HEAD"

echo "=== 3. docker image snapshot ==="
IMG="usm-backend:pre-sip-$DATE"
docker commit usm-backend "$IMG" >/dev/null 2>&1 && echo "committed running container to image: $IMG" || echo "WARN: docker commit failed"
docker images | grep "pre-sip-$DATE" || true

echo "=== 4. .env backup ==="
if [ -f "$REPO/.env" ]; then
  cp -n "$REPO/.env" "$REPO/.env.bak.$DATE" && echo "backed up .env -> .env.bak.$DATE" || echo ".env.bak.$DATE already exists (kept)"
else
  echo "WARN: no .env found"
fi

echo "=== 5. record current images list ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}' | grep -E 'usm-backend|docker-usm' || true

echo
echo "=== SNAPSHOT COMPLETE ==="
echo "Rollback (rebuild):     cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-backend"
echo "Rollback (image only):  docker compose -f $COMPOSE stop usm-backend && docker run -d --name usm-backend-rollback $IMG"
echo "git tag:   $TAG"
echo "img tag:   $IMG"
echo "env bkup:  $REPO/.env.bak.$DATE"
